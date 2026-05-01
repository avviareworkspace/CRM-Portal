import json
import logging
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.core.cache import cache
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (HttpResponse, HttpResponseRedirect,
                              get_object_or_404, redirect, render)
from django.templatetags.static import static
from django.conf import settings
from django.urls import reverse
from django.views.generic import UpdateView
from django.views.decorators.http import require_POST
from django.contrib.auth.hashers import make_password
from django.db.models import Count, Sum, Avg, Q, Case, When, Value, DecimalField
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .forms import *
from .lead_import_io import is_blank_import_value, iter_lead_import_rows
from .models import *
from .utils import (
    ADMIN_HOME_DASHBOARD_CACHE_KEY,
    get_counsellor_activity_snapshot,
    invalidate_admin_dashboard_cache,
    paginate_queryset,
    user_facing_exception_message,
    user_type_required,
    admin_perm_required,
)

admin_required = user_type_required('1')

logger = logging.getLogger(__name__)

IMPORT_FAILURES_SESSION_KEY = "lead_import_failures"
IMPORT_FAILURES_MAX_ROWS = 5000
IMPORT_TEMPLATE_HEADERS = [
    "name",
    "email",
    "phone",
    "alternate_phone",
    "address",
    "School Name",
    "graduation_status",
    "graduation_course",
    "graduation_year",
    "graduation_college",
    "course_interested",
]
IMPORT_TEMPLATE_SAMPLE_ROWS = [
    [
        "John Doe",
        "john.doe@example.com",
        "1234567890",
        "",
        "221B Baker Street, London",
        "MIT University",
        "YES",
        "Computer Science",
        "2020",
        "MIT",
        "MBA", 
    ],
    [
        "Jane Smith",
        "jane.smith@example.com",
        "9876543210",
        "",
        "742 Evergreen Terrace, Springfield",
        "Harvard University",
        "NO",
        "Not Applicable",
        "",
        "Not Applicable",
        "Digital Marketing",
    
    ],
]


def _import_cell_str(row, key, default=""):
    """Normalize import cell to a clean string (handles empty / NaN from Excel)."""
    if key not in row:
        return default
    val = row[key]
    if is_blank_import_value(val):
        return default
    s = str(val).strip()
    return s if s else default


def _new_import_lead_id():
    """Match Lead.save() format but longer suffix to avoid collisions on bulk import."""
    return f"L-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def _build_import_failure_row(row_num, row, reason):
    raw_row = {}
    for key, value in (row or {}).items():
        if key is None:
            continue
        norm_key = str(key).strip()
        if not norm_key:
            continue
        if is_blank_import_value(value):
            raw_row[norm_key] = ""
        else:
            raw_row[norm_key] = str(value).strip()
    return {
        "row_num": row_num,
        "name": _import_cell_str(row, "name"),
        "email": _import_cell_str(row, "email"),
        "phone": _import_cell_str(row, "phone"),
        "reason": str(reason),
        "raw_row": raw_row,
    }


def _build_lead_from_import_row(row, source, assigned_counsellor):
    """Construct an unsaved Lead from one import row dict (raises on bad data)."""
    full_name = _import_cell_str(row, "name")
    phone = _import_cell_str(row, "phone")
    course_interested = _import_cell_str(row, "course_interested")

    # Import minimum required fields (as per import guide).
    if not full_name:
        raise ValueError("Missing required column value: name")
    if not phone:
        raise ValueError("Missing required column value: phone")

    parts = full_name.split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    raw_gs = row.get("graduation_status", "NO")
    if is_blank_import_value(raw_gs):
        graduation_status = "NO"
    else:
        graduation_status = str(raw_gs).strip().upper()
        if graduation_status not in ("YES", "NO"):
            graduation_status = "NO"

    if graduation_status == "NO":
        graduation_course = "Not Applicable"
        graduation_college = "Not Applicable"
    else:
        graduation_course = row.get("graduation_course", "Not Specified")
        graduation_college = row.get("graduation_college", "Not Specified")
        if is_blank_import_value(graduation_course):
            graduation_course = "Not Specified"
        else:
            graduation_course = str(graduation_course).strip() or "Not Specified"
        if is_blank_import_value(graduation_college):
            graduation_college = "Not Specified"
        else:
            graduation_college = str(graduation_college).strip() or "Not Specified"

    graduation_year = row.get("graduation_year", None)
    if is_blank_import_value(graduation_year):
        graduation_year = None
    else:
        try:
            graduation_year = int(float(graduation_year))
        except (TypeError, ValueError):
            graduation_year = None

    alt = row.get("alternate_phone", "")
    if is_blank_import_value(alt):
        alternate_phone = ""
    else:
        alternate_phone = str(alt).strip()

    is_graduated = "YES" if graduation_status == "YES" else "NO"

    return Lead(
        lead_id=_new_import_lead_id(),
        first_name=first_name,
        last_name=last_name,
        email=_import_cell_str(row, "email"),
        phone=phone,
        alternate_phone=alternate_phone,
        school_name=_import_cell_str(row, "School Name"),
        address=_import_cell_str(row, "address"),
        graduation_status=graduation_status,
        graduation_course=graduation_course,
        graduation_year=graduation_year,
        graduation_college=graduation_college,
        course_interested=course_interested,
        industry=_import_cell_str(row, "industry"),
        source=source,
        assigned_counsellor=assigned_counsellor,
        is_graduated=is_graduated,
    )


def _admin_home_month_key(dt):
    if dt is None:
        return None
    return (dt.year, dt.month)


def _fetch_admin_home_cached_payload():
    """
    Dashboard aggregates (no ORM querysets — safe to cache).
    Recent activities are loaded separately each request.
    """
    # Basic Statistics
    total_counsellors = Counsellor.objects.filter(is_active=True).count()
    total_leads = Lead.objects.count()
    total_business = Lead.objects.filter(status='CLOSED_WON').count()

    lead_status_counts = Lead.objects.values('status').annotate(
        count=Count('id')
    ).values_list('status', 'count')
    lead_status_dict = dict(lead_status_counts)
    new_leads = lead_status_dict.get('NEW', 0)
    contacted_leads = lead_status_dict.get('CONTACTED', 0)
    qualified_leads = lead_status_dict.get('QUALIFIED', 0)
    closed_won = lead_status_dict.get('CLOSED_WON', 0)
    closed_lost = lead_status_dict.get('CLOSED_LOST', 0)

    current_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_leads = Lead.objects.filter(created_at__gte=current_month).count()
    monthly_business = Lead.objects.filter(
        status='CLOSED_WON',
        created_at__gte=current_month,
    ).count()

    lead_sources = list(
        LeadSource.objects.annotate(lead_count=Count('lead')).values('name', 'lead_count')
    )

    counsellor_performance = list(
        Counsellor.objects.filter(is_active=True)
        .select_related('admin')
        .annotate(
            total_leads=Count('lead'),
            total_business=Sum('business__value'),
            conversion_rate=Case(
                When(total_leads=0, then=Value(0.0)),
                default=Count('business') * 100.0 / Count('lead'),
                output_field=DecimalField(),
            ),
        )
        .values(
            'admin__first_name',
            'admin__last_name',
            'total_leads',
            'total_business',
            'conversion_rate',
        )
    )

    lead_status_data = {
        'NEW': new_leads,
        'CONTACTED': contacted_leads,
        'QUALIFIED': qualified_leads,
        'CLOSED_WON': closed_won,
        'CLOSED_LOST': closed_lost,
    }

    # Last 6 calendar months — two queries (leads + business) instead of 12 range filters
    anchor = current_month
    months_6 = []
    y, mo = anchor.year, anchor.month
    for _ in range(6):
        months_6.insert(0, anchor.replace(year=y, month=mo, day=1))
        mo -= 1
        if mo < 1:
            mo = 12
            y -= 1
    oldest = months_6[0]

    lead_map = {}
    for row in (
        Lead.objects.filter(created_at__gte=oldest)
        .annotate(m=TruncMonth('created_at'))
        .values('m')
        .annotate(cnt=Count('id'))
    ):
        lead_map[_admin_home_month_key(row['m'])] = row['cnt']

    biz_map = {}
    for row in (
        Business.objects.filter(created_at__gte=oldest, status='ACTIVE')
        .annotate(m=TruncMonth('created_at'))
        .values('m')
        .annotate(total=Sum('value'))
    ):
        biz_map[_admin_home_month_key(row['m'])] = float(row['total'] or 0)

    monthly_trend = []
    for ms in months_6:
        mk = (ms.year, ms.month)
        monthly_trend.append({
            'month': ms.strftime('%B %Y'),
            'leads': lead_map.get(mk, 0),
            'business': biz_map.get(mk, 0.0),
        })

    return {
        'total_counsellors': total_counsellors,
        'total_leads': total_leads,
        'total_business': total_business,
        'new_leads': new_leads,
        'contacted_leads': contacted_leads,
        'qualified_leads': qualified_leads,
        'closed_won': closed_won,
        'closed_lost': closed_lost,
        'monthly_leads': monthly_leads,
        'monthly_business': monthly_business,
        'lead_sources': lead_sources,
        'counsellor_performance': counsellor_performance,
        'lead_status_data': lead_status_data,
        'monthly_trend': monthly_trend,
    }


@admin_required
def admin_home(request):
    """Admin dashboard (aggregates cached; see ADMIN_DASHBOARD_CACHE_SECONDS)."""
    ttl = int(getattr(settings, 'ADMIN_DASHBOARD_CACHE_SECONDS', 45))
    cache_key = ADMIN_HOME_DASHBOARD_CACHE_KEY

    try:
        if ttl > 0:
            payload = cache.get(cache_key)
            if payload is None:
                payload = _fetch_admin_home_cached_payload()
                cache.set(cache_key, payload, ttl)
        else:
            payload = _fetch_admin_home_cached_payload()
    except Exception:
        logger.exception('admin_home cache/compute failed')
        payload = None

    if payload is None:
        payload = {
            'total_counsellors': 0,
            'total_leads': 0,
            'total_business': 0,
            'new_leads': 0,
            'contacted_leads': 0,
            'qualified_leads': 0,
            'closed_won': 0,
            'closed_lost': 0,
            'monthly_leads': 0,
            'monthly_business': 0,
            'lead_sources': [],
            'counsellor_performance': [],
            'lead_status_data': {},
            'monthly_trend': [],
        }

    recent_activities = list(
        LeadActivity.objects.select_related('lead', 'counsellor__admin').order_by(
            '-completed_date'
        )[:10]
    )

    context = {
        'page_title': 'CRM Admin Dashboard',
        'recent_activities': recent_activities,
        **payload,
    }
    return render(request, 'admin_template/home_content.html', context)


@admin_required
def counsellor_activity_progress_report(request):
    """Per-counsellor pipeline, targets, and activity (report page)."""
    counsellor_activity_progress = []
    for c in Counsellor.objects.filter(is_active=True).select_related('admin').order_by(
        'admin__first_name', 'admin__last_name'
    ):
        counsellor_activity_progress.append({
            'counsellor': c,
            'progress': get_counsellor_activity_snapshot(c),
        })
    return render(
        request,
        'admin_template/counsellor_activity_progress.html',
        {
            'page_title': 'Counsellor Activity Progress',
            'counsellor_activity_progress': counsellor_activity_progress,
        },
    )


@admin_required
def add_counsellor(request):
    """Add new counsellor"""
    form = CounsellorForm(request.POST or None, request.FILES or None)
    context = {'form': form, 'page_title': 'Add Counsellor'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                # Get the form data
                employee_id = form.cleaned_data['employee_id']
                department = form.cleaned_data.get('department', '')
                
                # Create the user first
                user = form.save(commit=False)
                user.user_type = '2'  # Counsellor
                user.save()
                
                # Create the counsellor profile
                Counsellor.objects.create(
                    admin=user,
                    employee_id=employee_id,
                    department=department
                )
                
                messages.success(request, "Counsellor added successfully!")
                return redirect(reverse('manage_counsellors'))
            except Exception as e:
                messages.error(request, f"Could not add counsellor: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_counsellor.html', context)


@admin_required
def manage_counsellors(request):
    """Manage all counsellors"""
    counsellors_list = Counsellor.objects.select_related('admin').all().order_by('-joining_date')
    counsellors = paginate_queryset(request, counsellors_list, 10)
    context = {
        'counsellors': counsellors,
        'page_title': 'Manage Counsellors'
    }
    return render(request, 'admin_template/manage_counsellors.html', context)


@admin_required
def edit_counsellor(request, counsellor_id):
    """Edit counsellor details"""
    counsellor = get_object_or_404(Counsellor, id=counsellor_id)
    form = CounsellorEditForm(
        request.POST or None, 
        request.FILES or None, 
        instance=counsellor.admin,
        counsellor_instance=counsellor
    )
    context = {
        'form': form,
        'counsellor': counsellor,
        'page_title': 'Edit Counsellor'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                # Save the CustomUser (admin) fields
                user = form.save()
                
                # Update the Counsellor-specific fields
                counsellor.employee_id = form.cleaned_data['employee_id']
                counsellor.department = form.cleaned_data['department']
                counsellor.is_active = form.cleaned_data['is_active']
                counsellor.save()
                
                messages.success(request, "Counsellor updated successfully!")
                return redirect(reverse('manage_counsellors'))
            except Exception as e:
                messages.error(request, f"Could not update counsellor: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_counsellor.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_counsellor(request, counsellor_id):
    """Delete counsellor"""
    counsellor = get_object_or_404(Counsellor, id=counsellor_id)
    try:
        counsellor.admin.delete()
        messages.success(request, "Counsellor deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete counsellor: {str(e)}")
    return redirect(reverse('manage_counsellors'))


@admin_required
def add_admin(request):
    """Add new admin user"""
    form = AdminForm(request.POST or None, request.FILES or None)
    context = {'form': form, 'page_title': 'Add Admin User'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                user = form.save(commit=False)
                user.user_type = '1'
                user.is_staff = True
                user.save()

                admin_profile = Admin.objects.get(admin=user)
                is_superadmin = request.POST.get('is_superadmin') == 'on'
                admin_profile.is_superadmin = is_superadmin
                if not is_superadmin:
                    admin_profile.can_delete = request.POST.get('can_delete') == 'on'
                    admin_profile.can_view_performance = request.POST.get('can_view_performance') == 'on'
                    admin_profile.can_view_counsellor_work = request.POST.get('can_view_counsellor_work') == 'on'
                    admin_profile.can_manage_settings = request.POST.get('can_manage_settings') == 'on'
                admin_profile.save()

                messages.success(request, "Admin user added successfully!")
                return redirect(reverse('manage_admins'))
            except Exception as e:
                messages.error(request, f"Could not add admin user: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_admin.html', context)


@admin_required
def manage_admins(request):
    """Manage all admin users"""
    # Get all admin users (user_type='1')
    admins_list = Admin.objects.select_related('admin').all().order_by('-admin__date_joined')
    # Don't paginate - let DataTables handle it
    context = {
        'admins': admins_list,
        'page_title': 'Manage Admin Users'
    }
    return render(request, 'admin_template/manage_admins.html', context)


@admin_required
def edit_admin(request, admin_id):
    """Edit admin user details"""
    admin_obj = get_object_or_404(Admin, id=admin_id)
    form = AdminForm(
        request.POST or None, 
        request.FILES or None, 
        instance=admin_obj
    )
    context = {
        'form': form,
        'admin_obj': admin_obj,
        'page_title': 'Edit Admin User'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                user = form.save()

                is_superadmin = request.POST.get('is_superadmin') == 'on'
                admin_obj.is_superadmin = is_superadmin
                if is_superadmin:
                    admin_obj.can_delete = True
                    admin_obj.can_view_performance = True
                    admin_obj.can_view_counsellor_work = True
                    admin_obj.can_manage_settings = True
                else:
                    admin_obj.can_delete = request.POST.get('can_delete') == 'on'
                    admin_obj.can_view_performance = request.POST.get('can_view_performance') == 'on'
                    admin_obj.can_view_counsellor_work = request.POST.get('can_view_counsellor_work') == 'on'
                    admin_obj.can_manage_settings = request.POST.get('can_manage_settings') == 'on'
                admin_obj.save()

                messages.success(request, "Admin user updated successfully!")
                return redirect(reverse('manage_admins'))
            except Exception as e:
                messages.error(request, f"Could not update admin user: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_admin.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_admin(request, admin_id):
    """Delete admin user"""
    admin_obj = get_object_or_404(Admin, id=admin_id)
    # Prevent deleting yourself
    if admin_obj.admin == request.user:
        messages.error(request, "You cannot delete your own account!")
        return redirect(reverse('manage_admins'))
    try:
        admin_obj.admin.delete()
        messages.success(request, "Admin user deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete admin user: {str(e)}")
    return redirect(reverse('manage_admins'))


@admin_required
def manage_leads(request):
    """Manage all leads with filtering options"""
    leads_list = Lead.objects.select_related('source', 'assigned_counsellor__admin').all().order_by('-created_at')
    
    # Get filter parameters from GET request
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    counsellor_filter = request.GET.get('counsellor', '')
    source_filter = request.GET.get('source', '')
    
    # Apply search filter
    if search_query:
            leads_list = leads_list.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(alternate_phone__icontains=search_query) |
                Q(lead_id__icontains=search_query)
            )
    
    # Apply status filter
    if status_filter:
        leads_list = leads_list.filter(status=status_filter)
    
    # Apply priority filter
    if priority_filter:
        leads_list = leads_list.filter(priority=priority_filter)
    
    # Apply counsellor filter
    if counsellor_filter:
        try:
            counsellor_id = int(counsellor_filter)
            leads_list = leads_list.filter(assigned_counsellor_id=counsellor_id)
        except ValueError:
            pass
    
    # Apply source filter
    if source_filter:
        try:
            source_id = int(source_filter)
            leads_list = leads_list.filter(source_id=source_id)
        except ValueError:
            pass
    
    # Get filter options for dropdowns
    all_counsellors = Counsellor.objects.filter(is_active=True).select_related('admin').order_by('admin__first_name')
    all_sources = LeadSource.objects.filter(is_active=True).order_by('name')
    
    # Get filter display names
    selected_counsellor_name = ''
    if counsellor_filter:
        try:
            counsellor = Counsellor.objects.filter(id=int(counsellor_filter)).select_related('admin').first()
            if counsellor:
                selected_counsellor_name = f"{counsellor.admin.first_name} {counsellor.admin.last_name}"
        except (ValueError, TypeError):
            pass
    
    selected_source_name = ''
    if source_filter:
        try:
            source = LeadSource.objects.filter(id=int(source_filter)).first()
            if source:
                selected_source_name = source.name
        except (ValueError, TypeError):
            pass
    
    # Get status and priority display names
    status_display = dict(LeadStatus.get_all_choices()).get(status_filter, status_filter) if status_filter else ''
    priority_display = dict(Lead.PRIORITY).get(priority_filter, priority_filter) if priority_filter else ''
    
    # Paginate server-side to keep page load fast even with many leads
    leads = paginate_queryset(request, leads_list, 50)

    # Preserve current filters in pagination links
    query_params = request.GET.copy()
    if 'page' in query_params:
        del query_params['page']
    query_string = query_params.urlencode()
    total_leads_in_system = Lead.objects.count()
    context = {
        'leads': leads,
        'total_leads_in_system': total_leads_in_system,
        'page_title': 'Manage Leads',
        'search_query': search_query,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'counsellor_filter': counsellor_filter,
        'source_filter': source_filter,
        'status_display': status_display,
        'priority_display': priority_display,
        'selected_counsellor_name': selected_counsellor_name,
        'selected_source_name': selected_source_name,
        'all_counsellors': all_counsellors,
        'all_sources': all_sources,
        'lead_statuses': LeadStatus.get_choices(),
        'lead_priorities': Lead.PRIORITY,
        'query_string': query_string,
    }
    return render(request, 'admin_template/manage_leads.html', context)


@admin_required
def add_lead(request):
    """Add new lead manually"""
    form = LeadForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Lead'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                lead = form.save()
                messages.success(request, f"Lead added successfully! Lead ID: {lead.lead_id}")
                return redirect(reverse('manage_leads'))
            except Exception as e:
                messages.error(request, f"Could not add lead: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_lead.html', context)


@admin_required
def edit_lead(request, lead_id):
    """Edit lead details"""
    lead = get_object_or_404(Lead, id=lead_id)
    form = LeadForm(request.POST or None, instance=lead)
    context = {
        'form': form,
        'lead': lead,
        'page_title': 'Edit Lead'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Lead updated successfully!")
                return redirect(reverse('manage_leads'))
            except Exception as e:
                messages.error(request, f"Could not update lead: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_lead.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_lead(request, lead_id):
    """Delete lead"""
    lead = get_object_or_404(Lead, id=lead_id)
    try:
        lead.delete()
        invalidate_admin_dashboard_cache()
        messages.success(request, "Lead deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete lead: {str(e)}")
    return redirect(reverse('manage_leads'))


@admin_required
@admin_perm_required('delete')
@require_POST
def bulk_delete_leads(request):
    """Delete multiple leads selected from the manage leads table"""
    lead_ids = request.POST.getlist('lead_ids')
    if not lead_ids:
        messages.warning(request, "No leads selected for deletion.")
        return redirect(reverse('manage_leads'))

    leads_qs = Lead.objects.filter(id__in=lead_ids)
    count = leads_qs.count()

    try:
        leads_qs.delete()
        invalidate_admin_dashboard_cache()
        messages.success(request, f"Successfully deleted {count} lead(s).")
    except Exception as e:
        messages.error(request, f"Could not delete selected leads: {str(e)}")

    return redirect(reverse('manage_leads'))


DELETE_ALL_LEADS_CONFIRM_PHRASE = 'DELETE ALL LEADS'


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_all_leads(request):
    """
    Permanently delete every Lead in the database (all pages / filters).
    Requires typing the confirmation phrase exactly.
    """
    confirm = (request.POST.get('confirm_text') or '').strip()
    if confirm != DELETE_ALL_LEADS_CONFIRM_PHRASE:
        messages.error(
            request,
            f'Confirmation failed. Type exactly: {DELETE_ALL_LEADS_CONFIRM_PHRASE}',
        )
        return redirect(reverse('manage_leads'))

    try:
        n = Lead.objects.count()
        if n == 0:
            messages.info(request, 'There are no leads to delete.')
            return redirect(reverse('manage_leads'))
        Lead.objects.all().delete()
        invalidate_admin_dashboard_cache()
        messages.success(request, f'Successfully deleted all {n} lead(s).')
    except Exception as e:
        logger.exception('delete_all_leads failed')
        messages.error(request, f'Could not delete all leads: {str(e)}')

    return redirect(reverse('manage_leads'))

@admin_required
def import_leads(request):
    """Import leads from Excel/CSV file with automatic assignment options"""
    form = LeadImportForm(request.POST or None, request.FILES or None)
    context = {
        'form': form,
        'page_title': 'Import Leads',
        'has_import_failures': bool(request.session.get(IMPORT_FAILURES_SESSION_KEY)),
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                file = form.cleaned_data['file']
                source = form.cleaned_data['source']
                assigned_counsellor = form.cleaned_data.get('assigned_counsellor')
                auto_assign = request.POST.get('auto_assign', False)
                assignment_method = request.POST.get('assignment_method', 'round_robin')

                max_size_mb = getattr(settings, 'MAX_LEAD_IMPORT_MB', 10)
                if file.size > max_size_mb * 1024 * 1024:
                    messages.error(request, f"File too large. Max size is {max_size_mb}MB.")
                    return redirect(reverse('import_leads'))
                
                success_count = 0
                error_count = 0
                imported_leads = []
                pending = []
                failed_rows = []
                batch_size = max(50, int(getattr(settings, "LEAD_IMPORT_BATCH_SIZE", 400)))

                for row_num, row in iter_lead_import_rows(file, file.name):
                    try:
                        lead = _build_lead_from_import_row(row, source, assigned_counsellor)
                        pending.append((row_num, row, lead))
                    except Exception as e:
                        error_count += 1
                        failed_rows.append(_build_import_failure_row(row_num, row, e))
                        logger.error(
                            "Error parsing import row %s: %s",
                            row_num,
                            str(e),
                            exc_info=True,
                        )

                for i in range(0, len(pending), batch_size):
                    chunk = pending[i : i + batch_size]
                    chunk_leads = [x[2] for x in chunk]
                    try:
                        with transaction.atomic():
                            Lead.objects.bulk_create(chunk_leads, batch_size=batch_size)
                        imported_leads.extend(chunk_leads)
                        success_count += len(chunk_leads)
                    except Exception as e:
                        logger.warning(
                            "Bulk insert failed for %s rows (%s); retrying one-by-one.",
                            len(chunk),
                            str(e),
                        )
                        for row_num, row, lead in chunk:
                            try:
                                with transaction.atomic():
                                    lead.save()
                                imported_leads.append(lead)
                                success_count += 1
                            except Exception as e2:
                                error_count += 1
                                failed_rows.append(_build_import_failure_row(row_num, row, e2))
                                logger.error(
                                    "Error importing row %s: %s",
                                    row_num,
                                    str(e2),
                                    exc_info=True
                                )

                # bulk_create may omit pk on some DBs; auto-assign uses bulk_update and needs ids
                if auto_assign and imported_leads:
                    if any(getattr(l, "pk", None) is None for l in imported_leads):
                        lids = [l.lead_id for l in imported_leads if l.lead_id]
                        db_map = {
                            x.lead_id: x for x in Lead.objects.filter(lead_id__in=lids)
                        }
                        imported_leads = [
                            db_map[l.lead_id]
                            for l in imported_leads
                            if l.lead_id in db_map
                        ]

                # Auto-assign leads if requested
                if auto_assign and imported_leads and not assigned_counsellor:
                    try:
                        active_counsellors = Counsellor.objects.filter(is_active=True)
                        if active_counsellors.exists():
                            unassigned_leads = [lead for lead in imported_leads if not lead.assigned_counsellor]
                            
                            if assignment_method == 'round_robin':
                                _assign_round_robin(unassigned_leads, active_counsellors)
                            elif assignment_method == 'workload_balanced':
                                _assign_workload_balanced(unassigned_leads, active_counsellors)
                            elif assignment_method == 'performance_based':
                                _assign_performance_based(unassigned_leads, active_counsellors)
                            elif assignment_method == 'specialization_based':
                                _assign_specialization_based(unassigned_leads, active_counsellors)
                            
                            messages.success(request, f"Successfully imported {success_count} leads and auto-assigned them using {assignment_method.replace('_', ' ').title()} method. {error_count} errors occurred.")
                        else:
                            messages.warning(request, f"Successfully imported {success_count} leads but no active counsellors found for auto-assignment. {error_count} errors occurred.")
                    except Exception as e:
                        messages.warning(request, f"Successfully imported {success_count} leads but auto-assignment failed: {str(e)}. {error_count} errors occurred.")
                else:
                    if error_count > 0:
                        messages.warning(request, f"Successfully imported {success_count} leads. {error_count} rows had errors and were skipped.")
                    else:
                        messages.success(request, f"Successfully imported {success_count} leads.")

                if success_count > 0:
                    invalidate_admin_dashboard_cache()

                if failed_rows:
                    request.session[IMPORT_FAILURES_SESSION_KEY] = {
                        "created_at": timezone.now().isoformat(),
                        "file_name": file.name,
                        "total_failed": len(failed_rows),
                        "rows": failed_rows[:IMPORT_FAILURES_MAX_ROWS],
                        "truncated": len(failed_rows) > IMPORT_FAILURES_MAX_ROWS,
                    }
                    request.session.modified = True
                    messages.info(
                        request,
                        "Review failed rows and reasons: /leads/import/failures/"
                    )
                else:
                    request.session.pop(IMPORT_FAILURES_SESSION_KEY, None)
                context['has_import_failures'] = bool(request.session.get(IMPORT_FAILURES_SESSION_KEY))

                return redirect(reverse('manage_leads'))
                
            except Exception as e:
                logger.exception("Lead import failed")
                messages.error(request, f"Import failed: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'admin_template/import_leads.html', context)


@admin_required
def import_lead_failures(request):
    payload = request.session.get(IMPORT_FAILURES_SESSION_KEY)
    context = {
        "page_title": "Lead Import Failures",
        "payload": payload,
    }
    if not payload:
        messages.info(request, "No failed lead import rows found in recent session.")
    return render(request, "admin_template/import_lead_failures.html", context)


@admin_required
def download_import_failures_excel(request):
    payload = request.session.get(IMPORT_FAILURES_SESSION_KEY)
    if not payload or not payload.get("rows"):
        messages.info(request, "No failed lead import rows available to download.")
        return redirect(reverse("import_lead_failures"))

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Failed Leads"
    rows = payload.get("rows") or []
    original_headers = []
    seen = set()
    for row in rows:
        raw = row.get("raw_row") or {}
        for key in raw.keys():
            if key in seen:
                continue
            seen.add(key)
            original_headers.append(key)

    ws.append(original_headers + ["failure_reason"])

    for row in rows:
        raw = row.get("raw_row") or {}
        out = [raw.get(col, "") for col in original_headers]
        out.append(row.get("reason", ""))
        ws.append(out)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="lead_import_failures.xlsx"'
    wb.save(response)
    return response


@admin_required
def assign_leads_to_counsellors(request):
    """Automatically assign unassigned leads to counsellors using multiple strategies"""
    if request.method == 'POST':
        try:
            assignment_method = request.POST.get('assignment_method', 'round_robin')
            unassigned_leads = Lead.objects.filter(assigned_counsellor__isnull=True)
            active_counsellors = Counsellor.objects.filter(is_active=True)
            selected_counsellor_ids = [x for x in request.POST.getlist('selected_counsellor_ids') if str(x).strip()]
            selected_counsellors = []
            if selected_counsellor_ids:
                try:
                    selected_ids = [int(x) for x in selected_counsellor_ids]
                except ValueError:
                    messages.error(request, "Selected counsellor list is invalid.")
                    return redirect(reverse('assign_leads_to_counsellors'))

                selected_counsellors = list(active_counsellors.filter(id__in=selected_ids).select_related('admin'))
                if len(selected_counsellors) != len(set(selected_ids)):
                    messages.error(request, "One or more selected counsellors are invalid or inactive.")
                    return redirect(reverse('assign_leads_to_counsellors'))
                active_counsellors = active_counsellors.filter(id__in=selected_ids)
            
            if not active_counsellors.exists():
                messages.error(request, "No active counsellors found!")
                return redirect(reverse('manage_leads'))
            
            if not unassigned_leads.exists():
                messages.info(request, "No unassigned leads found!")
                return redirect(reverse('manage_leads'))
            
            assigned_count = 0
            
            if assignment_method == 'round_robin':
                assigned_count = _assign_round_robin(unassigned_leads, active_counsellors)
            elif assignment_method == 'workload_balanced':
                assigned_count = _assign_workload_balanced(unassigned_leads, active_counsellors)
            elif assignment_method == 'performance_based':
                assigned_count = _assign_performance_based(unassigned_leads, active_counsellors)
            elif assignment_method == 'specialization_based':
                assigned_count = _assign_specialization_based(unassigned_leads, active_counsellors)
            else:
                assigned_count = _assign_round_robin(unassigned_leads, active_counsellors)
            
            if selected_counsellors:
                scope_label = f" for selected counsellors ({len(selected_counsellors)})"
            else:
                scope_label = " across all active counsellors"
            messages.success(
                request,
                f"Successfully assigned {assigned_count} leads using {assignment_method.replace('_', ' ').title()} method{scope_label}!"
            )
            invalidate_admin_dashboard_cache()
            return redirect(reverse('assign_leads_to_counsellors'))
            
        except Exception as e:
            messages.error(request, f"Assignment failed: {str(e)}")
    
    # GET request - show assignment page with workload summary
    try:
        from datetime import datetime, timedelta
        
        # Get unassigned leads count
        unassigned_count = Lead.objects.filter(assigned_counsellor__isnull=True).count()
        active_counsellors_count = Counsellor.objects.filter(is_active=True).count()
        
        # Calculate average leads per counsellor
        total_assigned_leads = Lead.objects.filter(assigned_counsellor__isnull=False).count()
        avg_leads_per_counsellor = round(total_assigned_leads / active_counsellors_count, 1) if active_counsellors_count > 0 else 0
        
        # Find oldest unassigned lead
        oldest_unassigned = Lead.objects.filter(assigned_counsellor__isnull=True).order_by('created_at').first()
        oldest_unassigned_days = 0
        if oldest_unassigned:
            oldest_unassigned_days = (datetime.now().replace(tzinfo=None) - oldest_unassigned.created_at.replace(tzinfo=None)).days
        
        # Get counsellor workload data (single query: annotate counts, no N+1)
        counsellor_workload = []
        for counsellor in (
            Counsellor.objects.filter(is_active=True)
            .select_related('admin')
            .annotate(lead_count=Count('lead'))
            .order_by('admin__first_name', 'admin__last_name')
        ):
            lead_count = counsellor.lead_count

            # Determine capacity and workload status
            if lead_count <= 10:
                capacity = "Low (≤10 leads)"
                workload_status = "LOW"
            elif lead_count <= 25:
                capacity = "Medium (11-25 leads)"
                workload_status = "MEDIUM"
            else:
                capacity = "High (26+ leads)"
                workload_status = "HIGH"
            
            counsellor_workload.append({
                'admin': counsellor.admin,
                'department': counsellor.department,
                'lead_count': lead_count,
                'capacity': capacity,
                'workload_status': workload_status
            })
        
        context = {
            'page_title': 'Assign Leads to Counsellors',
            'unassigned_count': unassigned_count,
            'active_counsellors_count': active_counsellors_count,
            'avg_leads_per_counsellor': avg_leads_per_counsellor,
            'oldest_unassigned_days': oldest_unassigned_days,
            'counsellor_workload': counsellor_workload,
            'active_counsellors': Counsellor.objects.filter(is_active=True).select_related('admin').order_by('admin__first_name', 'admin__last_name'),
        }
        
        return render(request, 'admin_template/assign_leads.html', context)
        
    except Exception as e:
        messages.error(request, f"Error loading assignment page: {str(e)}")
        return redirect(reverse('manage_leads'))


def _assign_round_robin(unassigned_leads, active_counsellors):
    """Round-robin assignment - distribute leads evenly"""
    counsellor_list = list(active_counsellors)
    if not counsellor_list:
        return 0

    leads = list(unassigned_leads)
    if not leads:
        return 0

    for i, lead in enumerate(leads):
        lead.assigned_counsellor = counsellor_list[i % len(counsellor_list)]

    # Single bulk update instead of per-lead saves
    Lead.objects.bulk_update(leads, ['assigned_counsellor'])
    return len(leads)


def _assign_workload_balanced(unassigned_leads, active_counsellors):
    """Workload-balanced assignment - assign to counsellors with fewer leads"""
    active_counsellors = list(active_counsellors)
    if not active_counsellors:
        return 0

    leads = list(unassigned_leads)
    if not leads:
        return 0

    # Get current workload for each counsellor in a single query
    from django.db.models import Count

    workload_qs = (
        Lead.objects
        .filter(assigned_counsellor__in=active_counsellors)
        .values('assigned_counsellor')
        .annotate(count=Count('id'))
    )
    workload_map = {row['assigned_counsellor']: row['count'] for row in workload_qs}

    counsellor_workload = []
    for counsellor in active_counsellors:
        counsellor_workload.append({
            'counsellor': counsellor,
            'lead_count': workload_map.get(counsellor.id, 0),
        })

    # Sort counsellors by workload (ascending)
    counsellor_workload.sort(key=lambda x: x['lead_count'])

    for lead in leads:
        # Assign to counsellor with least workload
        target = counsellor_workload[0]
        lead.assigned_counsellor = target['counsellor']

        # Update workload count in memory
        target['lead_count'] += 1
        counsellor_workload.sort(key=lambda x: x['lead_count'])

    Lead.objects.bulk_update(leads, ['assigned_counsellor'])
    return len(leads)


def _assign_performance_based(unassigned_leads, active_counsellors):
    """Performance-based assignment - assign to top-performing counsellors"""
    active_counsellors = list(active_counsellors)
    if not active_counsellors:
        return 0

    leads = list(unassigned_leads)
    if not leads:
        return 0

    from django.db.models import Count

    # Aggregate total leads and closed-won per counsellor in as few queries as possible
    lead_stats = (
        Lead.objects
        .filter(assigned_counsellor__in=active_counsellors)
        .values('assigned_counsellor', 'status')
        .annotate(count=Count('id'))
    )

    performance_map = {}
    for row in lead_stats:
        cid = row['assigned_counsellor']
        status = row['status']
        count = row['count']
        perf = performance_map.setdefault(cid, {'total': 0, 'won': 0})
        perf['total'] += count
        if status == 'CLOSED_WON':
            perf['won'] += count

    counsellor_performance = []
    for counsellor in active_counsellors:
        stats = performance_map.get(counsellor.id, {'total': 0, 'won': 0})
        total_leads = stats['total']
        closed_won = stats['won']
        conversion_rate = (closed_won / total_leads * 100) if total_leads > 0 else 0
        counsellor_performance.append({
            'counsellor': counsellor,
            'conversion_rate': conversion_rate,
            'total_leads': total_leads,
        })

    # Sort by conversion rate (descending) and then by total leads (ascending)
    counsellor_performance.sort(
        key=lambda x: (-x['conversion_rate'], x['total_leads'])
    )

    for lead in leads:
        # Assign to top-performing counsellor
        target = counsellor_performance[0]
        lead.assigned_counsellor = target['counsellor']

        # Update lead count in memory and re-sort
        target['total_leads'] += 1
        counsellor_performance.sort(
            key=lambda x: (-x['conversion_rate'], x['total_leads'])
        )

    Lead.objects.bulk_update(leads, ['assigned_counsellor'])
    return len(leads)


def _assign_specialization_based(unassigned_leads, active_counsellors):
    """Specialization-based assignment - assign based on counsellor expertise"""
    active_counsellors = list(active_counsellors)
    if not active_counsellors:
        return 0

    leads = list(unassigned_leads)
    if not leads:
        return 0

    # Get counsellor specializations (based on department and past performance)
    counsellor_specializations = {}

    # Prefetch all leads for the active counsellors in a single query
    counsellor_leads_qs = Lead.objects.filter(
        assigned_counsellor__in=active_counsellors
    ).select_related('source')

    leads_by_counsellor = {}
    for lead in counsellor_leads_qs:
        leads_by_counsellor.setdefault(lead.assigned_counsellor_id, []).append(lead)

    for counsellor in active_counsellors:
        industry_success = {}
        source_success = {}
        clist = leads_by_counsellor.get(counsellor.id, [])

        for l in clist:
            if l.industry:
                stats = industry_success.setdefault(
                    l.industry, {'total': 0, 'won': 0}
                )
                stats['total'] += 1
                if l.status == 'CLOSED_WON':
                    stats['won'] += 1

            if l.source:
                stats = source_success.setdefault(
                    l.source_id, {'total': 0, 'won': 0}
                )
                stats['total'] += 1
                if l.status == 'CLOSED_WON':
                    stats['won'] += 1

        counsellor_specializations[counsellor.id] = {
            'counsellor': counsellor,
            'industry_success': industry_success,
            'source_success': source_success,
            'current_workload': len(clist),
        }

    for lead in leads:
        best_counsellor = None
        best_score = -1

        for counsellor_data in counsellor_specializations.values():
            score = 0

            # Industry expertise bonus
            if lead.industry and lead.industry in counsellor_data['industry_success']:
                stats = counsellor_data['industry_success'][lead.industry]
                success_rate = stats['won'] / stats['total'] if stats['total'] else 0
                score += success_rate * 100

            # Source expertise bonus
            if lead.source and lead.source.id in counsellor_data['source_success']:
                stats = counsellor_data['source_success'][lead.source.id]
                success_rate = stats['won'] / stats['total'] if stats['total'] else 0
                score += success_rate * 50

            # Workload penalty (prefer counsellors with fewer leads)
            workload_penalty = counsellor_data['current_workload'] * 2
            score -= workload_penalty

            if score > best_score:
                best_score = score
                best_counsellor = counsellor_data['counsellor']

        if best_counsellor:
            lead.assigned_counsellor = best_counsellor
            # Update workload in memory
            data = counsellor_specializations.get(best_counsellor.id)
            if data:
                data['current_workload'] += 1

    # Persist all assignments in a single bulk update
    Lead.objects.bulk_update(leads, ['assigned_counsellor'])
    return len(leads)


@admin_required
def transfer_lead(request, lead_id):
    """Transfer lead to another counsellor"""
    lead = get_object_or_404(Lead, id=lead_id)
    form = LeadTransferForm(request.POST or None)
    context = {
        'form': form,
        'lead': lead,
        'page_title': 'Transfer Lead'
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                transfer = form.save(commit=False)
                transfer.lead = lead
                transfer.from_counsellor = lead.assigned_counsellor
                transfer.admin_approved = True
                transfer.approved_by = request.user
                transfer.approved_at = timezone.now()
                transfer.save()
                
                # Update lead assignment
                lead.previous_counsellor = lead.assigned_counsellor
                lead.assigned_counsellor = transfer.to_counsellor
                lead.status = 'TRANSFERRED'
                lead.save()
                
                messages.success(request, f"Lead transferred to {transfer.to_counsellor.admin.first_name}")
                return redirect(reverse('manage_leads'))
                
            except Exception as e:
                messages.error(request, f"Transfer failed: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'admin_template/transfer_lead.html', context)


@admin_required
@admin_perm_required('settings')
def manage_lead_sources(request):
    """Manage lead sources"""
    sources = LeadSource.objects.all().prefetch_related('lead_set')
    context = {
        'sources': sources,
        'page_title': 'Manage Lead Sources'
    }
    return render(request, 'admin_template/manage_lead_sources.html', context)


@admin_required
@admin_perm_required('settings')
def add_lead_source(request):
    """Add new lead source"""
    form = LeadSourceForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Lead Source'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Lead source added successfully!")
                return redirect(reverse('manage_lead_sources'))
            except Exception as e:
                messages.error(request, f"Could not add lead source: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_lead_source.html', context)


@admin_required
@admin_perm_required('settings')
def edit_lead_source(request, source_id):
    """Edit lead source"""
    lead_source = get_object_or_404(LeadSource, id=source_id)
    form = LeadSourceForm(request.POST or None, instance=lead_source)
    context = {'form': form, 'lead_source': lead_source, 'page_title': 'Edit Lead Source'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Lead source updated successfully!")
                return redirect(reverse('manage_lead_sources'))
            except Exception as e:
                messages.error(request, f"Could not update lead source: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_lead_source.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_lead_source(request, source_id):
    """Delete lead source"""
    lead_source = get_object_or_404(LeadSource, id=source_id)
    try:
        lead_source.delete()
        messages.success(request, "Lead source deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete lead source: {str(e)}")
    return redirect(reverse('manage_lead_sources'))


# Lead statuses (same pattern as lead sources)

@admin_required
@admin_perm_required('settings')
def manage_lead_statuses(request):
    """List all lead statuses with lead counts."""
    from django.db.models import Count, Value, IntegerField
    from django.db.models.functions import Coalesce

    statuses = LeadStatus.objects.all().order_by('sort_order', 'name')
    # Annotate each status with the number of leads using that code
    status_lead_counts = {}
    for row in Lead.objects.values('status').annotate(count=Count('id')):
        status_lead_counts[row['status']] = row['count']
    for status in statuses:
        status.lead_count = status_lead_counts.get(status.code, 0)

    context = {
        'statuses': statuses,
        'page_title': 'Manage Lead Statuses',
    }
    return render(request, 'admin_template/manage_lead_statuses.html', context)


@admin_required
@admin_perm_required('settings')
def add_lead_status(request):
    """Add a new lead status."""
    from .forms import LeadStatusForm
    form = LeadStatusForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Lead Status'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                status = form.save(commit=False)
                status.code = status.code.upper().replace(' ', '_')
                status.save()
                messages.success(request, "Lead status added successfully!")
                return redirect(reverse('manage_lead_statuses'))
            except Exception as e:
                messages.error(request, f"Could not add lead status: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_lead_status.html', context)


@admin_required
@admin_perm_required('settings')
def edit_lead_status(request, status_id):
    """Edit an existing lead status."""
    from .forms import LeadStatusForm
    lead_status = get_object_or_404(LeadStatus, id=status_id)
    form = LeadStatusForm(request.POST or None, instance=lead_status)
    context = {'form': form, 'lead_status': lead_status, 'page_title': 'Edit Lead Status'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                status = form.save(commit=False)
                status.code = status.code.upper().replace(' ', '_')
                status.save()
                messages.success(request, "Lead status updated successfully!")
                return redirect(reverse('manage_lead_statuses'))
            except Exception as e:
                messages.error(request, f"Could not update lead status: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_lead_status.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_lead_status(request, status_id):
    """Delete a lead status (system statuses are protected)."""
    lead_status = get_object_or_404(LeadStatus, id=status_id)
    if lead_status.is_system:
        messages.error(request, f"Cannot delete system status '{lead_status.name}'. You can deactivate it instead.")
        return redirect(reverse('manage_lead_statuses'))
    lead_count = Lead.objects.filter(status=lead_status.code).count()
    if lead_count > 0:
        messages.error(request, f"Cannot delete '{lead_status.name}' — {lead_count} lead(s) are using this status. Reassign them first.")
        return redirect(reverse('manage_lead_statuses'))
    try:
        lead_status.delete()
        messages.success(request, "Lead status deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete lead status: {str(e)}")
    return redirect(reverse('manage_lead_statuses'))


# Activity types

@admin_required
@admin_perm_required('settings')
def manage_activity_types(request):
    from django.db.models import Count
    types = ActivityType.objects.all().order_by('sort_order', 'name')
    type_counts = {}
    for row in LeadActivity.objects.values('activity_type').annotate(count=Count('id')):
        type_counts[row['activity_type']] = row['count']
    for t in types:
        t.activity_count = type_counts.get(t.code, 0)
    context = {'activity_types': types, 'page_title': 'Manage Activity Types'}
    return render(request, 'admin_template/manage_activity_types.html', context)


@admin_required
@admin_perm_required('settings')
def add_activity_type(request):
    from .forms import ActivityTypeForm
    form = ActivityTypeForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Activity Type'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.code = obj.code.upper().replace(' ', '_')
                obj.save()
                messages.success(request, "Activity type added successfully!")
                return redirect(reverse('manage_activity_types'))
            except Exception as e:
                messages.error(request, f"Could not add activity type: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_activity_type.html', context)


@admin_required
@admin_perm_required('settings')
def edit_activity_type(request, type_id):
    from .forms import ActivityTypeForm
    activity_type = get_object_or_404(ActivityType, id=type_id)
    form = ActivityTypeForm(request.POST or None, instance=activity_type)
    context = {'form': form, 'activity_type': activity_type, 'page_title': 'Edit Activity Type'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.code = obj.code.upper().replace(' ', '_')
                obj.save()
                messages.success(request, "Activity type updated successfully!")
                return redirect(reverse('manage_activity_types'))
            except Exception as e:
                messages.error(request, f"Could not update activity type: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_activity_type.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_activity_type(request, type_id):
    activity_type = get_object_or_404(ActivityType, id=type_id)
    if activity_type.is_system:
        messages.error(request, f"Cannot delete system activity type '{activity_type.name}'. You can deactivate it instead.")
        return redirect(reverse('manage_activity_types'))
    count = LeadActivity.objects.filter(activity_type=activity_type.code).count()
    if count > 0:
        messages.error(request, f"Cannot delete '{activity_type.name}' — {count} activities use this type.")
        return redirect(reverse('manage_activity_types'))
    try:
        activity_type.delete()
        messages.success(request, "Activity type deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete activity type: {str(e)}")
    return redirect(reverse('manage_activity_types'))


# Next actions

@admin_required
@admin_perm_required('settings')
def manage_next_actions(request):
    from django.db.models import Count
    actions = NextAction.objects.all().order_by('sort_order', 'name')
    action_counts = {}
    for row in LeadActivity.objects.exclude(next_action='').values('next_action').annotate(count=Count('id')):
        action_counts[row['next_action']] = row['count']
    for a in actions:
        a.usage_count = action_counts.get(a.code, 0)
    context = {'next_actions': actions, 'page_title': 'Manage Next Actions'}
    return render(request, 'admin_template/manage_next_actions.html', context)


@admin_required
@admin_perm_required('settings')
def add_next_action(request):
    from .forms import NextActionForm
    form = NextActionForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Next Action'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.code = obj.code.upper().replace(' ', '_')
                obj.save()
                messages.success(request, "Next action added successfully!")
                return redirect(reverse('manage_next_actions'))
            except Exception as e:
                messages.error(request, f"Could not add next action: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/add_next_action.html', context)


@admin_required
@admin_perm_required('settings')
def edit_next_action(request, action_id):
    from .forms import NextActionForm
    action = get_object_or_404(NextAction, id=action_id)
    form = NextActionForm(request.POST or None, instance=action)
    context = {'form': form, 'next_action': action, 'page_title': 'Edit Next Action'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.code = obj.code.upper().replace(' ', '_')
                obj.save()
                messages.success(request, "Next action updated successfully!")
                return redirect(reverse('manage_next_actions'))
            except Exception as e:
                messages.error(request, f"Could not update next action: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/edit_next_action.html', context)


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_next_action(request, action_id):
    action = get_object_or_404(NextAction, id=action_id)
    if action.is_system:
        messages.error(request, f"Cannot delete system action '{action.name}'. You can deactivate it instead.")
        return redirect(reverse('manage_next_actions'))
    count = LeadActivity.objects.filter(next_action=action.code).count()
    if count > 0:
        messages.error(request, f"Cannot delete '{action.name}' — {count} activities use this action.")
        return redirect(reverse('manage_next_actions'))
    try:
        action.delete()
        messages.success(request, "Next action deleted successfully!")
    except Exception as e:
        messages.error(request, f"Could not delete next action: {str(e)}")
    return redirect(reverse('manage_next_actions'))


# Daily targets

@admin_required
def manage_daily_targets(request):
    """List all daily targets."""
    targets = (
        DailyTarget.objects
        .select_related('created_by')
        .prefetch_related('assignments__counsellor__admin')
        .order_by('-target_date')
    )
    context = {'targets': targets, 'page_title': 'Daily Targets'}
    return render(request, 'admin_template/manage_daily_targets.html', context)


@admin_required
def create_daily_target(request):
    """Admin enters a number + date → assign to all / selected counsellors."""
    from .forms import DailyTargetForm
    form = DailyTargetForm(request.POST or None)
    context = {'form': form, 'page_title': 'Set Daily Target'}

    if request.method == 'POST' and form.is_valid():
        try:
            target = DailyTarget.objects.create(
                target_date=form.cleaned_data['target_date'],
                target_count=form.cleaned_data['target_count'],
                created_by=request.user,
            )
            if form.cleaned_data['assign_mode'] == 'all':
                counsellors = Counsellor.objects.filter(is_active=True)
            else:
                counsellors = form.cleaned_data.get('counsellors', Counsellor.objects.none())

            for c in counsellors:
                DailyTargetAssignment.objects.get_or_create(target=target, counsellor=c)

            messages.success(request, f"Target of {target.target_count} tasks set for {counsellors.count()} counsellor(s) on {target.target_date}!")
            return redirect(reverse('manage_daily_targets'))
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")

    return render(request, 'admin_template/create_daily_target.html', context)


@admin_required
@require_POST
def update_daily_target(request, target_id):
    """Admin updates the task count for an existing target."""
    target = get_object_or_404(DailyTarget, id=target_id)
    try:
        new_count = int(request.POST.get('target_count', target.target_count))
        if new_count < 1:
            raise ValueError("Must be at least 1")
        target.target_count = new_count
        target.save()
        messages.success(request, f"Target updated to {new_count} tasks for {target.target_date}.")
    except (ValueError, TypeError) as e:
        messages.error(request, f"Invalid value: {e}")
    return redirect(reverse('manage_daily_targets'))


@admin_required
@admin_perm_required('delete')
@require_POST
def delete_daily_target(request, target_id):
    target = get_object_or_404(DailyTarget, id=target_id)
    try:
        target.delete()
        messages.success(request, "Daily target deleted.")
    except Exception as e:
        messages.error(request, f"Could not delete: {str(e)}")
    return redirect(reverse('manage_daily_targets'))


@admin_required
def manage_businesses(request):
    """Manage all businesses"""
    businesses_list = Business.objects.select_related('lead', 'counsellor__admin').all().order_by('-created_at')
    businesses = paginate_queryset(request, businesses_list, 15)
    context = {
        'businesses': businesses,
        'page_title': 'Manage Businesses'
    }
    return render(request, 'admin_template/manage_businesses.html', context)


@admin_required
@admin_perm_required('performance')
def counsellor_performance(request):
    """View counsellor performance analytics"""
    counsellors = Counsellor.objects.filter(is_active=True)
    performance_data = []
    
    for counsellor in counsellors:
        # Get monthly performance
        current_month = timezone.now().replace(day=1)
        monthly_performance = CounsellorPerformance.objects.filter(
            counsellor=counsellor,
            month=current_month
        ).first()
        
        if not monthly_performance:
            # Calculate performance if not exists
            monthly_leads = counsellor.lead_set.filter(created_at__gte=current_month).count()
            # Monthly successfully converted leads for this counsellor
            monthly_business = counsellor.lead_set.filter(
                status='CLOSED_WON',
                created_at__gte=current_month
            ).count()
            
            try:
                conversion_rate = monthly_business / monthly_leads * 100 if monthly_leads > 0 else 0
            except ZeroDivisionError:
                conversion_rate = 0
                
            monthly_performance = CounsellorPerformance.objects.create(
                counsellor=counsellor,
                month=current_month,
                total_leads_assigned=monthly_leads,
                total_business_generated=monthly_business,
                conversion_rate=conversion_rate
            )
        
        performance_data.append({
            'counsellor': counsellor,
            'performance': monthly_performance
        })
    
    context = {
        'performance_data': performance_data,
        'page_title': 'Counsellor Performance'
    }
    return render(request, 'admin_template/counsellor_performance.html', context)


@admin_required
def send_counsellor_notification(request):
    """Send notification to counsellors"""
    form = NotificationCounsellorForm(request.POST or None)
    context = {'form': form, 'page_title': 'Send Notification'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Notification sent successfully!")
                return redirect(reverse('admin_home'))
            except Exception as e:
                messages.error(request, f"Could not send notification: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    return render(request, 'admin_template/send_counsellor_notification.html', context)


@admin_required
def admin_view_profile(request):
    """Admin profile view"""
    admin = get_object_or_404(Admin, admin=request.user)
    context = {
        'admin': admin,
        'page_title': 'Admin Profile'
    }
    return render(request, 'admin_template/admin_view_profile.html', context)


@admin_required
def admin_view_notifications(request):
    """View admin notifications"""
    notifications = NotificationAdmin.objects.filter(admin=request.user).order_by('-created_at')
    context = {
        'notifications': notifications,
        'page_title': 'Notifications'
    }
    return render(request, 'admin_template/admin_view_notifications.html', context)


@admin_required
def get_lead_analytics(request):
    """AJAX endpoint for lead analytics"""
    if request.method == 'GET':
        try:
            # Lead status distribution
            status_data = Lead.objects.values('status').annotate(
                count=Count('id')
            ).values('status', 'count')
            
            # Monthly trend
            current_month = timezone.now().replace(day=1)
            monthly_data = []
            for i in range(6):
                month_start = current_month - timedelta(days=30*i)
                month_end = month_start + timedelta(days=30)
                month_leads = Lead.objects.filter(
                    created_at__gte=month_start,
                    created_at__lt=month_end
                ).count()
                monthly_data.append({
                    'month': month_start.strftime('%B'),
                    'leads': month_leads
                })
            
            return JsonResponse({
                'status_data': list(status_data),
                'monthly_data': monthly_data
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)


@admin_required
def download_import_template(request, file_type):
    """Download sample import template files"""
    import os
    from django.http import FileResponse
    from django.conf import settings
    from openpyxl import Workbook
    
    if file_type == 'excel':
        wb = Workbook()
        ws = wb.active
        ws.title = "Lead Import Template"
        ws.append(IMPORT_TEMPLATE_HEADERS)
        for sample_row in IMPORT_TEMPLATE_SAMPLE_ROWS:
            ws.append(sample_row)

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="lead_import_template.xlsx"'
        wb.save(response)
        return response
    elif file_type == 'csv':
        file_path = os.path.join(settings.BASE_DIR, 'main_app', 'static', 'templates', 'lead_import_template.csv')
        content_type = 'text/csv'
        filename = 'lead_import_template.csv'
    else:
        return HttpResponse('Invalid file type', status=400)
    
    if os.path.exists(file_path):
        response = FileResponse(open(file_path, 'rb'), content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse('Template file not found', status=404)


@admin_required
def admin_view_lead(request, lead_id):
    """Admin view to display detailed lead information"""
    try:
        lead = get_object_or_404(Lead, id=lead_id)
        
        # Get lead activities
        activities = LeadActivity.objects.filter(lead=lead).order_by('-scheduled_date')
        
        # Get related business if exists
        business = Business.objects.filter(lead=lead).first()
        
        context = {
            'lead': lead,
            'activities': activities,
            'business': business,
            'page_title': f'Lead Details - {lead.first_name} {lead.last_name}'
        }
        
        return render(request, 'admin_template/view_lead.html', context)
        
    except Exception as e:
        messages.error(request, f'Error loading lead details: {str(e)}')
        return redirect('manage_leads')


@admin_required
def admin_run_ai_workflow(request, lead_id):
    """Admin view to run AI workflow for a lead"""
    try:
        lead = get_object_or_404(Lead, id=lead_id)
        
        # Check if lead has an assigned counsellor
        if not lead.assigned_counsellor:
            messages.error(request, 'Lead must be assigned to a counsellor to run AI workflow.')
            return redirect('admin_view_lead', lead_id=lead_id)
        
        # Import the workflow function from counsellor_views
        from .counsellor_views import run_agentic_workflow
        
        # Temporarily change the request user to the assigned counsellor
        original_user = request.user
        request.user = lead.assigned_counsellor.admin
        
        try:
            # Call the workflow function
            result = run_agentic_workflow(request, lead_id)
            
            # If we get here without exception, the workflow completed successfully
            messages.success(request, 'AI workflow completed successfully!')
                
        finally:
            # Restore the original user
            request.user = original_user
            
    except Exception as e:
        messages.error(request, f'Error running AI workflow: {str(e)}')
    
    return redirect('admin_view_lead', lead_id=lead_id)




@admin_required
def manual_route_student(request, lead_id):
    """Manual routing of student to different academic departments"""
    if request.method == 'POST':
        try:
            lead = get_object_or_404(Lead, id=lead_id)
            route_to = request.POST.get('route_to')
            custom_reason = request.POST.get('custom_reason', '')
            
            if not route_to:
                messages.error(request, 'Please select a routing option.')
                return redirect('admin_view_lead', lead_id=lead_id)
            
            # Import the routing function from counsellor_views
            from .counsellor_views import execute_academic_routing
            
            # Use custom reason if provided, otherwise use default
            if custom_reason:
                routing_reason = f"Manual routing: {custom_reason}"
            else:
                routing_reason = f"Manually routed to {route_to.replace('_', ' ')} by admin"
            
            # Execute the routing
            routing_success = execute_academic_routing(lead, route_to, routing_reason)
            
            if routing_success:
                messages.success(request, f'Student successfully routed to {route_to.replace("_", " ")}.')
            else:
                messages.error(request, 'Routing failed. Please try again.')
                
        except Exception as e:
            messages.error(request, f'Error in manual routing: {str(e)}')
    
    return redirect('admin_view_lead', lead_id=lead_id)


@admin_required
def get_admin_calendar_events(request):
    """API endpoint to get calendar events for all leads (admin view)"""
    # Get date range from request (optional)
    start_date_str = request.GET.get('start')
    end_date_str = request.GET.get('end')
    
    events = []
    
    # Parse dates if provided
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            if timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
        except (ValueError, AttributeError):
            pass
    
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            if timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)
        except (ValueError, AttributeError):
            pass
    
    # Get scheduled activities for all leads
    activities_query = LeadActivity.objects.filter(
        scheduled_date__isnull=False
    ).select_related('lead', 'counsellor__admin')
    
    if start_date and end_date:
        activities_query = activities_query.filter(
            scheduled_date__gte=start_date,
            scheduled_date__lte=end_date
        )
    
    for activity in activities_query:
        if activity.scheduled_date:
            start_iso = activity.scheduled_date.isoformat()
            end_iso = None
            if activity.duration:
                end_time = activity.scheduled_date + timedelta(minutes=activity.duration)
                end_iso = end_time.isoformat()
            else:
                end_time = activity.scheduled_date + timedelta(hours=1)
                end_iso = end_time.isoformat()
            
            activity_type_display = dict(ActivityType.get_all_choices()).get(activity.activity_type, activity.activity_type)
            counsellor_name = f"{activity.counsellor.admin.first_name} {activity.counsellor.admin.last_name}" if activity.counsellor else "Unassigned"
            
            events.append({
                'id': f'activity_{activity.id}',
                'title': f"{activity_type_display}: {activity.lead.first_name} {activity.lead.last_name}",
                'start': start_iso,
                'end': end_iso,
                'color': '#007bff',  # Blue for activities
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'activity',
                    'lead_name': f"{activity.lead.first_name} {activity.lead.last_name}",
                    'counsellor_name': counsellor_name,
                    'description': activity.description or 'No description',
                    'activity_id': activity.id,
                    'lead_id': activity.lead.id,
                }
            })
    
    # Get follow-ups from all leads
    followups_query = Lead.objects.filter(
        next_follow_up__isnull=False
    ).select_related('source', 'assigned_counsellor__admin')
    
    if start_date and end_date:
        followups_query = followups_query.filter(
            next_follow_up__gte=start_date,
            next_follow_up__lte=end_date
        )
    
    for lead in followups_query:
        if lead.next_follow_up:
            followup_date = lead.next_follow_up.date()
            counsellor_name = f"{lead.assigned_counsellor.admin.first_name} {lead.assigned_counsellor.admin.last_name}" if lead.assigned_counsellor else "Unassigned"
            
            events.append({
                'id': f'followup_{lead.id}',
                'title': f"Follow-up: {lead.first_name} {lead.last_name}",
                'start': followup_date.isoformat(),
                'allDay': True,
                'color': '#28a745',  # Green for follow-ups
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'followup',
                    'lead_name': f"{lead.first_name} {lead.last_name}",
                    'counsellor_name': counsellor_name,
                    'lead_id': lead.id,
                    'course_interested': lead.course_interested or 'N/A',
                    'school_name': lead.school_name or 'N/A',
                }
            })
    
    return JsonResponse(events, safe=False)


@admin_required
@admin_perm_required('counsellor_work')
def counsellor_work_view(request):
    """Admin view: counsellor activities and visit dates with rich filters."""
    import pytz
    from django.conf import settings as dj_settings

    counsellor_id = request.GET.get('counsellor', '').strip()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    legacy_date = request.GET.get('selected_date', '').strip()
    if not date_from_str and legacy_date:
        date_from_str = legacy_date
    if not date_to_str and legacy_date:
        date_to_str = legacy_date

    activity_type_f = request.GET.get('activity_type', '').strip()
    activity_status = request.GET.get('activity_status', '').strip()
    lead_status_f = request.GET.get('lead_status', '').strip()
    lead_source_f = request.GET.get('lead_source', '').strip()
    lead_priority_f = request.GET.get('lead_priority', '').strip()

    today = timezone.localtime(timezone.now()).date()
    if not date_from_str:
        date_from_str = today.strftime('%Y-%m-%d')
    if not date_to_str:
        date_to_str = date_from_str

    try:
        date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
    except ValueError:
        date_from = today
        date_from_str = today.strftime('%Y-%m-%d')
    try:
        date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
    except ValueError:
        date_to = date_from
        date_to_str = date_from_str
    if date_to < date_from:
        date_to = date_from
        date_to_str = date_from_str

    tz_obj = pytz.timezone(dj_settings.TIME_ZONE)
    range_start = tz_obj.localize(datetime.combine(date_from, datetime.min.time()))
    range_end = tz_obj.localize(datetime.combine(date_to, datetime.max.time().replace(microsecond=999999)))

    all_counsellors = Counsellor.objects.filter(is_active=True).select_related('admin').order_by('admin__first_name')

    activities_query = LeadActivity.objects.all().select_related(
        'lead', 'lead__source', 'counsellor__admin'
    ).order_by('-scheduled_date', '-completed_date')

    followups_query = Lead.objects.filter(next_follow_up__isnull=False).select_related(
        'assigned_counsellor__admin', 'source'
    ).order_by('next_follow_up')

    selected_counsellor = None
    if counsellor_id:
        try:
            selected_counsellor = Counsellor.objects.get(id=int(counsellor_id), is_active=True)
            activities_query = activities_query.filter(counsellor=selected_counsellor)
            followups_query = followups_query.filter(assigned_counsellor=selected_counsellor)
        except (ValueError, Counsellor.DoesNotExist):
            selected_counsellor = None

    activities_query = activities_query.filter(
        Q(scheduled_date__gte=range_start, scheduled_date__lte=range_end) |
        Q(completed_date__gte=range_start, completed_date__lte=range_end)
    )

    followups_query = followups_query.filter(
        next_follow_up__gte=range_start,
        next_follow_up__lte=range_end,
    )

    if activity_type_f:
        activities_query = activities_query.filter(activity_type=activity_type_f)
    if activity_status == 'completed':
        activities_query = activities_query.filter(is_completed=True)
    elif activity_status == 'pending':
        activities_query = activities_query.filter(is_completed=False)

    if lead_status_f:
        activities_query = activities_query.filter(lead__status=lead_status_f)
        followups_query = followups_query.filter(status=lead_status_f)

    if lead_source_f:
        try:
            sid = int(lead_source_f)
            activities_query = activities_query.filter(lead__source_id=sid)
            followups_query = followups_query.filter(source_id=sid)
        except ValueError:
            pass

    if lead_priority_f:
        activities_query = activities_query.filter(lead__priority=lead_priority_f)
        followups_query = followups_query.filter(priority=lead_priority_f)

    total_activities = activities_query.count()
    completed_activities = activities_query.filter(is_completed=True).count()
    pending_activities = activities_query.filter(is_completed=False).count()
    total_followups = followups_query.count()

    activities = paginate_queryset(request, activities_query, 50)
    followups = followups_query[:100]

    filter_params = request.GET.copy()
    filter_params.pop('page', None)
    filter_query = filter_params.urlencode()

    selected_source_name = ''
    if lead_source_f:
        try:
            src = LeadSource.objects.filter(pk=int(lead_source_f)).first()
            if src:
                selected_source_name = src.name
        except ValueError:
            pass

    lead_sources = LeadSource.objects.filter(is_active=True).order_by('name')
    try:
        activity_type_choices = ActivityType.get_choices()
    except Exception:
        activity_type_choices = []
    if not activity_type_choices:
        activity_type_choices = list(LeadActivity.ACTIVITY_TYPE)
    try:
        lead_status_choices = LeadStatus.get_choices()
    except Exception:
        lead_status_choices = []
    if not lead_status_choices:
        lead_status_choices = list(Lead.LEAD_STATUS)

    context = {
        'page_title': 'Counsellor Work View',
        'activities': activities,
        'followups': followups,
        'all_counsellors': all_counsellors,
        'selected_counsellor': selected_counsellor,
        'counsellor_id': counsellor_id,
        'date_from': date_from_str,
        'date_to': date_to_str,
        'activity_type_f': activity_type_f,
        'activity_status': activity_status,
        'lead_status_f': lead_status_f,
        'lead_source_f': lead_source_f,
        'lead_priority_f': lead_priority_f,
        'lead_sources': lead_sources,
        'activity_type_choices': activity_type_choices,
        'lead_status_choices': lead_status_choices,
        'lead_priority_choices': Lead.PRIORITY,
        'total_activities': total_activities,
        'completed_activities': completed_activities,
        'pending_activities': pending_activities,
        'total_followups': total_followups,
        'filter_query': filter_query,
        'selected_source_name': selected_source_name,
    }
    return render(request, 'admin_template/counsellor_work_view.html', context)

    

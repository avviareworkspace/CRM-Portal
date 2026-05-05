import json
from datetime import datetime, timedelta
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (HttpResponseRedirect, get_object_or_404,
                              redirect, render)
from django.urls import reverse
from django.db.models import Count, Sum, Q
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import *
from .models import *
from .utils import (
    paginate_queryset,
    user_type_required,
    get_counsellor_activity_snapshot,
    get_counsellor_daily_target_progress,
)
import os
import requests
import re
import logging


counsellor_required = user_type_required('2')


@counsellor_required
def counsellor_home(request):
    """Counsellor Dashboard"""
    counsellor = get_object_or_404(
        Counsellor.objects.select_related('admin'),
        admin=request.user,
    )
    
    my_leads = Lead.objects.filter(assigned_counsellor=counsellor)
    total_leads = my_leads.count()
    
    # Single query for all status counts
    lead_status_counts = my_leads.values('status').annotate(
        count=Count('id')
    ).values_list('status', 'count')
    lead_status_dict = dict(lead_status_counts)
    new_leads = lead_status_dict.get('NEW', 0)
    contacted_leads = lead_status_dict.get('CONTACTED', 0)
    qualified_leads = lead_status_dict.get('QUALIFIED', 0)
    closed_won = lead_status_dict.get('CLOSED_WON', 0)
    
    # Converted leads statistics for this counsellor
    converted_leads_qs = Lead.objects.filter(assigned_counsellor=counsellor, status='CLOSED_WON')
    total_converted_leads = converted_leads_qs.count()
    
    now_local = timezone.localtime(timezone.now())
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    todays_activities = LeadActivity.objects.filter(
        counsellor=counsellor,
        scheduled_date__isnull=False,
        scheduled_date__gte=today_start,
        scheduled_date__lt=today_end
    ).select_related('lead', 'lead__source').order_by('scheduled_date')

    recent_activities = LeadActivity.objects.filter(
        counsellor=counsellor
    ).select_related('lead', 'lead__source').order_by('-completed_date')[:10]

    upcoming_followups = Lead.objects.filter(
        assigned_counsellor=counsellor,
        next_follow_up__isnull=False,
        next_follow_up__gte=today_start,
        next_follow_up__lt=today_end
    ).select_related('source').order_by('next_follow_up')

    lead_status_data = {
        'NEW': new_leads,
        'CONTACTED': contacted_leads,
        'QUALIFIED': qualified_leads,
        'CLOSED_WON': closed_won,
    }

    current_month = timezone.now().replace(day=1)
    monthly_leads = my_leads.filter(created_at__gte=current_month).count()
    monthly_business = converted_leads_qs.filter(created_at__gte=current_month).count()

    incomplete_activities_count = LeadActivity.objects.filter(
        counsellor=counsellor, is_completed=False
    ).count()

    activity_progress = get_counsellor_activity_snapshot(counsellor)

    context = {
        'page_title': "Counsellor Dashboard",
        'counsellor': counsellor,
        'activity_progress': activity_progress,
        'total_leads': total_leads,
        'new_leads': new_leads,
        'contacted_leads': contacted_leads,
        'qualified_leads': qualified_leads,
        'closed_won': closed_won,
        'total_business_value': total_converted_leads,
        'pending_businesses': 0,
        'active_businesses': 0,
        'recent_activities': recent_activities,
        'todays_activities': todays_activities,
        'upcoming_followups': upcoming_followups,
        'lead_status_data': lead_status_data,
        'monthly_leads': monthly_leads,
        'monthly_business': monthly_business,
        'incomplete_activities_count': incomplete_activities_count,
    }
    return render(request, 'counsellor_template/home_content.html', context)


@counsellor_required
def my_leads(request):
    """View assigned leads"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    leads_list = Lead.objects.filter(assigned_counsellor=counsellor).select_related('source').order_by('-created_at')
    
    # Filter by status if provided
    status_filter = request.GET.get('status')
    if status_filter:
        leads_list = leads_list.filter(status=status_filter)
    
    # Use server-side pagination to keep response fast with many leads
    leads = paginate_queryset(request, leads_list, 50)

    # Preserve filters (e.g. status) in pagination links, same pattern as admin manage_leads
    query_params = request.GET.copy()
    if 'page' in query_params:
        del query_params['page']
    query_string = query_params.urlencode()

    # Audit: log that this counsellor listed their leads (once per request)
    try:
        DataAccessLog.objects.create(
            user=request.user,
            counsellor=counsellor,
            action='list_my_leads',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
    except Exception:
        logging.getLogger(__name__).warning("Failed to write DataAccessLog for my_leads", exc_info=True)
    context = {
        'leads': leads,
        'page_title': 'My Leads',
        'status_filter': status_filter,
        'query_string': query_string,
    }
    return render(request, 'counsellor_template/my_leads.html', context)


@counsellor_required
def lead_detail(request, lead_id):
    """View lead details and activities"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    activities = LeadActivity.objects.filter(lead=lead, counsellor=counsellor).order_by('-completed_date')

    # Audit: log this lead view
    try:
        DataAccessLog.objects.create(
            user=request.user,
            counsellor=counsellor,
            action='view_lead_detail',
            lead=lead,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )

        # Simple anomaly rule: if a counsellor views more than 200 distinct leads in 24 hours,
        # create admin notifications so it can be reviewed.
        from django.utils import timezone
        from .models import Admin, NotificationAdmin

        now = timezone.now()
        window_start = now - timedelta(hours=24)

        recent_views = (
            DataAccessLog.objects
            .filter(
                counsellor=counsellor,
                action='view_lead_detail',
                created_at__gte=window_start,
            )
            .values('lead_id')
            .distinct()
            .count()
        )

        THRESHOLD = 200
        if recent_views >= THRESHOLD:
            # Avoid spamming: only create one alert per counsellor per day for this threshold
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            already_flagged = NotificationAdmin.objects.filter(
                created_at__gte=today_start,
                message__icontains=f"possible data export or leak by counsellor {counsellor.employee_id}",
            ).exists()

            if not already_flagged:
                msg = (
                    f"Potential data-leak risk: counsellor {counsellor.admin.get_full_name()} "
                    f"({counsellor.employee_id}, {counsellor.admin.email}) has viewed "
                    f"{recent_views} unique leads in the last 24 hours. "
                    f"Please review their activity and access."
                )
                for admin_profile in Admin.objects.select_related('admin').all():
                    NotificationAdmin.objects.create(
                        admin=admin_profile.admin,
                        message=f"Security alert: possible data export or leak by counsellor {counsellor.employee_id}. {msg}",
                    )
    except Exception:
        logging.getLogger(__name__).warning("Failed to write DataAccessLog / security alert", exc_info=True)

    from .forms import LeadAlternatePhoneForm
    alt_phone_form = LeadAlternatePhoneForm()

    context = {
        'lead': lead,
        'activities': activities,
        'page_title': f'Lead: {lead.first_name} {lead.last_name}',
        'alt_phone_form': alt_phone_form,
    }
    return render(request, 'counsellor_template/lead_detail.html', context)


@counsellor_required
@require_POST
def add_alternate_phone(request, lead_id):
    """Allow counsellor to add an additional alternate phone number for a lead."""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)

    from .forms import LeadAlternatePhoneForm

    form = LeadAlternatePhoneForm(request.POST or None)
    if form.is_valid():
        alt_phone = form.save(commit=False)
        alt_phone.lead = lead
        alt_phone.created_by = counsellor
        try:
            alt_phone.save()
            messages.success(request, "Alternate phone added successfully.")
        except Exception as e:
            messages.error(request, f"Could not save alternate phone: {str(e)}")
    else:
        messages.error(request, "Please provide a valid alternate phone.")

    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
@require_POST
def reveal_phone(request, lead_id):
    """
    Reveal a lead's phone number on demand, log the access,
    and alert admins if a counsellor reveals too many numbers.
    """
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)

    try:
        # Audit log for phone reveal
        DataAccessLog.objects.create(
            user=request.user,
            counsellor=counsellor,
            action='reveal_phone',
            lead=lead,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )

        # Threshold check: distinct leads with any phone/alternate reveal in last 1h
        _check_phone_reveal_threshold(counsellor)

    except Exception:
        logging.getLogger(__name__).warning("Failed to log phone reveal / create alert", exc_info=True)

    return JsonResponse({'phone': lead.phone})


def _check_phone_reveal_threshold(counsellor):
    """
    If counsellor has revealed phones for too many distinct leads in the last 1 hour,
    notify admins (at most once per day per counsellor).
    End-to-end: endpoints reveal_phone + reveal_alternate_phone → DataAccessLog → this check → NotificationAdmin.
    Templates: counsellor lead_detail (main + alternate), my_leads table; JS calls endpoints on View click.
    """
    from .models import Admin, NotificationAdmin

    now = timezone.now()
    window_start = now - timedelta(hours=1)  # Check window: last 1 hour
    recent_reveals = (
        DataAccessLog.objects
        .filter(
            counsellor=counsellor,
            action__in=('reveal_phone', 'reveal_alternate_phone'),
            created_at__gte=window_start,
        )
        .values('lead_id')
        .distinct()
        .count()
    )
    REVEAL_THRESHOLD = 60
    if recent_reveals >= REVEAL_THRESHOLD:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        already_flagged = NotificationAdmin.objects.filter(
            created_at__gte=today_start,
            message__icontains=f"phone reveal threshold exceeded by counsellor {counsellor.employee_id}",
        ).exists()
        if not already_flagged:
            msg = (
                f"Data-protection alert: counsellor {counsellor.admin.get_full_name()} "
                f"({counsellor.employee_id}, {counsellor.admin.email}) has revealed phone numbers "
                f"for {recent_reveals} unique leads in the last 1 hour. "
                f"Please review their activity."
            )
            for admin_profile in Admin.objects.select_related('admin').all():
                NotificationAdmin.objects.create(
                    admin=admin_profile.admin,
                    message=f"Security alert: phone reveal threshold exceeded by counsellor {counsellor.employee_id}. {msg}",
                )


@counsellor_required
@require_POST
def reveal_alternate_phone(request, lead_id):
    """
    Reveal a lead's alternate phone (lead.alternate_phone or a LeadAlternatePhone row).
    POST with which=primary for lead.alternate_phone, or which=<id> for LeadAlternatePhone id.
    Logs access and uses same threshold alert as main phone reveal.
    """
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    which = (request.POST.get('which') or '').strip()
    phone = None
    if which == 'primary':
        phone = lead.alternate_phone or ''
    else:
        try:
            alt_id = int(which)
            alt = LeadAlternatePhone.objects.get(id=alt_id, lead=lead)
            phone = alt.phone or ''
        except (ValueError, LeadAlternatePhone.DoesNotExist):
            return JsonResponse({'error': 'Invalid which'}, status=400)
    try:
        DataAccessLog.objects.create(
            user=request.user,
            counsellor=counsellor,
            action='reveal_alternate_phone',
            lead=lead,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
        _check_phone_reveal_threshold(counsellor)
    except Exception:
        logging.getLogger(__name__).warning("Failed to log alternate phone reveal / create alert", exc_info=True)
    return JsonResponse({'phone': phone})


@counsellor_required
def edit_my_lead(request, lead_id):
    """Allow counsellor to update key lead details for their own leads"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)

    form = CounsellorLeadForm(request.POST or None, instance=lead)

    context = {
        'form': form,
        'lead': lead,
        'page_title': f'Edit Lead: {lead.first_name} {lead.last_name}',
    }

    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Lead details updated successfully.")
                return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))
            except Exception as e:
                messages.error(request, f"Could not update lead: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, 'counsellor_template/edit_my_lead.html', context)


@counsellor_required
def add_lead_activity(request, lead_id):
    """Add activity for a lead"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    form = LeadActivityForm(request.POST or None)
    
    context = {
        'form': form,
        'lead': lead,
        'page_title': 'Add Activity'
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                activity = form.save(commit=False)
                activity.lead = lead
                activity.counsellor = counsellor

                has_next = form.cleaned_data.get('has_next_action') == 'yes'
                followup_date = form.cleaned_data.get('followup_date')

                if has_next:
                    activity.is_completed = True

                activity.save()

                lead.last_contact_date = timezone.now()
                if lead.status == 'NEW':
                    lead.status = 'CONTACTED'
                lead.save()

                if has_next and followup_date:
                    LeadActivity.objects.create(
                        lead=lead,
                        counsellor=counsellor,
                        activity_type=activity.next_action or '',
                        subject='',
                        description='',
                        outcome='',
                        next_action='',
                        scheduled_date=followup_date,
                        is_completed=False,
                    )
                    messages.success(request, "Activity completed & next follow-up scheduled!")
                else:
                    messages.success(request, "Activity added successfully!")

                return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))
            except Exception as e:
                messages.error(request, f"Could not add activity: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'counsellor_template/add_lead_activity.html', context)


@counsellor_required
def edit_lead_activity(request, lead_id, activity_id):
    """Edit an existing activity for a lead"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    activity = get_object_or_404(LeadActivity, id=activity_id, lead=lead, counsellor=counsellor)
    
    form = LeadActivityForm(request.POST or None, instance=activity)
    
    context = {
        'form': form,
        'lead': lead,
        'activity': activity,
        'page_title': 'Edit Activity'
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                activity = form.save(commit=False)
                activity.lead = lead
                activity.counsellor = counsellor

                has_next = form.cleaned_data.get('has_next_action') == 'yes'
                followup_date = form.cleaned_data.get('followup_date')

                if has_next:
                    activity.is_completed = True

                activity.save()

                if activity.is_completed:
                    lead.last_contact_date = timezone.now()
                    lead.save()

                if has_next and followup_date:
                    LeadActivity.objects.create(
                        lead=lead,
                        counsellor=counsellor,
                        activity_type=activity.next_action or '',
                        subject='',
                        description='',
                        outcome='',
                        next_action='',
                        scheduled_date=followup_date,
                        is_completed=False,
                    )
                    messages.success(request, "Activity completed & next follow-up scheduled!")
                else:
                    messages.success(request, "Activity updated successfully!")

                return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))
            except Exception as e:
                messages.error(request, f"Could not update activity: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'counsellor_template/edit_lead_activity.html', context)


@counsellor_required
@require_POST
def delete_lead_activity(request, lead_id, activity_id):
    """Delete an activity."""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    activity = get_object_or_404(LeadActivity, id=activity_id, lead=lead, counsellor=counsellor)
    activity.delete()
    messages.success(request, "Activity deleted.")
    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
@require_POST
def mark_activity_complete(request, lead_id, activity_id):
    """Quick action to mark an activity as completed"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    activity = get_object_or_404(LeadActivity, id=activity_id, lead=lead, counsellor=counsellor)
    
    try:
        activity.is_completed = True
        activity.completed_date = timezone.now()
        activity.save()
        
        # Update lead last contact date
        lead.last_contact_date = timezone.now()
        lead.save()
        
        messages.success(request, "Activity marked as completed!")
    except Exception as e:
        messages.error(request, f"Could not update activity: {str(e)}")
    
    # Redirect back to the referrer or lead detail
    redirect_url = request.META.get('HTTP_REFERER', reverse('lead_detail', kwargs={'lead_id': lead_id}))
    return redirect(redirect_url)


@counsellor_required
def update_lead_status(request, lead_id):
    """Update lead status"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        valid_codes = set(code for code, _ in LeadStatus.get_choices())
        if new_status in valid_codes:
            lead.status = new_status
            lead.save()
            messages.success(request, f"Lead status updated to {new_status}")
        else:
            messages.error(request, "Invalid status")
    
    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
def create_business(request, lead_id):
    """Create business from lead"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    form = BusinessForm(request.POST or None)
    
    context = {
        'form': form,
        'lead': lead,
        'page_title': 'Create Business'
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                business = form.save(commit=False)
                business.lead = lead
                business.counsellor = counsellor
                business.save()
                
                # Update lead status to CLOSED_WON
                lead.status = 'CLOSED_WON'
                lead.actual_value = business.value
                lead.save()
                
                messages.success(request, f"Business created successfully! Business ID: {business.business_id}")
                return redirect(reverse('my_businesses'))
            except Exception as e:
                messages.error(request, f"Could not create business: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'counsellor_template/create_business.html', context)


@counsellor_required
def my_businesses(request):
    """View my businesses"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    businesses_list = Business.objects.filter(counsellor=counsellor).select_related('lead').order_by('-created_at')
    
    # Filter by status if provided
    status_filter = request.GET.get('status')
    if status_filter:
        businesses_list = businesses_list.filter(status=status_filter)
    
    businesses = paginate_queryset(request, businesses_list, 15)
    context = {
        'businesses': businesses,
        'page_title': 'My Businesses',
        'status_filter': status_filter
    }
    return render(request, 'counsellor_template/my_businesses.html', context)


@counsellor_required
def business_detail(request, business_id):
    """View business details"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    business = get_object_or_404(Business, id=business_id, counsellor=counsellor)
    
    context = {
        'business': business,
        'page_title': f'Business: {business.title}'
    }
    return render(request, 'counsellor_template/business_detail.html', context)


@counsellor_required
def update_business_status(request, business_id):
    """Update business status"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    business = get_object_or_404(Business, id=business_id, counsellor=counsellor)
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Business.BUSINESS_STATUS):
            business.status = new_status
            business.save()
            messages.success(request, f"Business status updated to {new_status}")
        else:
            messages.error(request, "Invalid status")
    
    return redirect(reverse('business_detail', kwargs={'business_id': business_id}))


@counsellor_required
def request_lead_transfer(request, lead_id):
    """Request lead transfer to another counsellor"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    form = LeadTransferForm(request.POST or None)
    
    context = {
        'form': form,
        'lead': lead,
        'page_title': 'Request Lead Transfer'
    }
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                transfer = form.save(commit=False)
                transfer.lead = lead
                transfer.from_counsellor = counsellor
                transfer.save()
                
                messages.success(request, "Transfer request submitted successfully!")
                return redirect(reverse('my_leads'))
            except Exception as e:
                messages.error(request, f"Could not submit transfer request: {str(e)}")
        else:
            messages.error(request, "Please fill the form properly!")
    
    return render(request, 'counsellor_template/request_lead_transfer.html', context)


@counsellor_required
def my_activities(request):
    """View my activities"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    activities_list = LeadActivity.objects.filter(counsellor=counsellor).select_related('lead').order_by('-completed_date')
    
    # Filter by activity type if provided
    activity_type = request.GET.get('activity_type')
    if activity_type:
        activities_list = activities_list.filter(activity_type=activity_type)
    
    activities = paginate_queryset(request, activities_list, 20)
    context = {
        'activities': activities,
        'page_title': 'My Activities',
        'activity_type': activity_type
    }
    return render(request, 'counsellor_template/my_activities.html', context)


@counsellor_required
def pending_tasks(request):
    """
    Show all pending/incomplete tasks for the counsellor:
    1. Incomplete activities (is_completed=False)
    2. Completed activities that have a next_action set (the next action is the pending task)
    """
    counsellor = get_object_or_404(Counsellor, admin=request.user)

    # Incomplete activities
    incomplete_activities = LeadActivity.objects.filter(
        counsellor=counsellor,
        is_completed=False,
    ).select_related('lead').order_by('scheduled_date')

    # Completed activities that generated a next_action (which hasn't been acted on yet)
    # We consider an action "pending" if the activity has a next_action and no newer activity
    # exists for the same lead with the next_action as activity_type.
    pending_next_actions = LeadActivity.objects.filter(
        counsellor=counsellor,
        is_completed=True,
    ).exclude(
        next_action='',
    ).select_related('lead').order_by('-completed_date')

    # Filter: only show if there's no subsequent activity for that lead matching the next_action
    truly_pending = []
    for act in pending_next_actions:
        has_followup = LeadActivity.objects.filter(
            lead=act.lead,
            counsellor=counsellor,
            activity_type=act.next_action,
            completed_date__gt=act.completed_date,
        ).exists()
        if not has_followup:
            truly_pending.append(act)

    # Also get upcoming visits (next_follow_up in the future)
    upcoming_visits = Lead.objects.filter(
        assigned_counsellor=counsellor,
        next_follow_up__isnull=False,
        next_follow_up__gte=timezone.now(),
    ).select_related('source').order_by('next_follow_up')

    # Build the next_action display map
    na_map = dict(NextAction.get_all_choices()) if NextAction.objects.exists() else {}

    context = {
        'page_title': 'Pending Tasks',
        'incomplete_activities': incomplete_activities,
        'pending_next_actions': truly_pending,
        'upcoming_visits': upcoming_visits,
        'next_action_map': na_map,
    }
    return render(request, 'counsellor_template/pending_tasks.html', context)


@counsellor_required
def my_daily_target(request):
    """Build today's task list up to the daily target (visits, then activities, then leads)."""
    from datetime import date
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    today = date.today()

    progress = get_counsellor_daily_target_progress(counsellor)
    assignment = progress['assignment']
    assignment.completed_count = progress['completed_toward_target']
    assignment.save(update_fields=['completed_count', 'updated_at'])

    limit = assignment.target.target_count
    remaining = limit

    visits = list(
        Lead.objects.filter(
            assigned_counsellor=counsellor,
            next_follow_up__date__lte=today,
        ).select_related('source').order_by('next_follow_up')[:remaining]
    )
    remaining -= len(visits)

    activities = []
    if remaining > 0:
        activities = list(
            LeadActivity.objects.filter(
                counsellor=counsellor,
                is_completed=False,
            ).select_related('lead').order_by('scheduled_date')[:remaining]
        )
        remaining -= len(activities)

    status_leads = []
    if remaining > 0:
        statuses = (
            LeadStatus.objects
            .filter(is_active=True)
            .exclude(code__in=['NEW', 'CLOSED_WON', 'CLOSED_LOST', 'TRANSFERRED'])
            .order_by('sort_order')
        )
        for st in statuses:
            if remaining <= 0:
                break
            chunk = list(
                Lead.objects.filter(
                    assigned_counsellor=counsellor, status=st.code,
                ).select_related('source').order_by('-created_at')[:remaining]
            )
            if chunk:
                status_leads.append({'status': st, 'leads': chunk})
                remaining -= len(chunk)

    new_leads = []
    if remaining > 0:
        new_leads = list(
            Lead.objects.filter(
                assigned_counsellor=counsellor, status='NEW',
            ).select_related('source').order_by('-created_at')[:remaining]
        )

    total_items = len(visits) + len(activities) + sum(len(s['leads']) for s in status_leads) + len(new_leads)

    completed_today = progress['completed_toward_target']

    context = {
        'page_title': "Today's Target",
        'assignment': assignment,
        'today': today,
        'visits': visits,
        'activities': activities,
        'status_leads': status_leads,
        'new_leads': new_leads,
        'total_items': total_items,
        'completed_today': completed_today,
        'daily_target': progress['daily_target'],
        'target_remaining': progress['target_remaining'],
        'target_progress_pct': progress['target_progress_pct'],
    }
    return render(request, 'counsellor_template/my_daily_target.html', context)




@counsellor_required
def counsellor_view_profile(request):
    """Counsellor profile view"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    
    # Performance statistics
    total_leads = counsellor.lead_set.count()
    total_business = counsellor.business_set.filter(status='ACTIVE').aggregate(
        total=Sum('value'))['total'] or 0
    try:
        conversion_rate = (counsellor.business_set.count() / total_leads * 100) if total_leads > 0 else 0
    except ZeroDivisionError:
        conversion_rate = 0
    
    context = {
        'counsellor': counsellor,
        'total_leads': total_leads,
        'total_business': total_business,
        'conversion_rate': conversion_rate,
        'page_title': 'My Profile'
    }
    return render(request, 'counsellor_template/counsellor_view_profile.html', context)


@counsellor_required
def counsellor_view_notifications(request):
    """View counsellor notifications"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    notifications = NotificationCounsellor.objects.filter(counsellor=counsellor).order_by('-created_at')
    
    # Mark notifications as read
    if request.method == 'POST':
        notifications.update(is_read=True)
        messages.success(request, "All notifications marked as read!")
    
    context = {
        'notifications': notifications,
        'page_title': 'Notifications'
    }
    return render(request, 'counsellor_template/counsellor_view_notifications.html', context)


@counsellor_required
def counsellor_fcmtoken(request):
    """Update FCM token for notifications"""
    if request.method == 'POST':
        token = request.POST.get('token')
        if token:
            request.user.fcm_token = token
            request.user.save()
            return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'})


@counsellor_required
def get_my_analytics(request):
    """AJAX endpoint for counsellor analytics"""
    if request.method == 'GET':
        try:
            counsellor = get_object_or_404(Counsellor, admin=request.user)
            
            # Lead status distribution
            status_data = counsellor.lead_set.values('status').annotate(
                count=Count('id')
            ).values('status', 'count')
            
            # Monthly activity trend
            current_month = timezone.now().replace(day=1)
            monthly_activities = []
            for i in range(6):
                month_start = current_month - timedelta(days=30*i)
                month_end = month_start + timedelta(days=30)
                month_activities = counsellor.leadactivity_set.filter(
                    completed_date__gte=month_start,
                    completed_date__lt=month_end
                ).count()
                monthly_activities.append({
                    'month': month_start.strftime('%B'),
                    'activities': month_activities
                })
            
            return JsonResponse({
                'status_data': list(status_data),
                'monthly_activities': monthly_activities
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)


@counsellor_required
def schedule_follow_up(request, lead_id):
    """Schedule follow-up for a lead"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    
    if request.method == 'POST':
        follow_up_date = request.POST.get('follow_up_date')
        if follow_up_date:
            try:
                # Parse datetime from form (datetime-local input is in local timezone)
                # Parse the datetime string (format: YYYY-MM-DDTHH:MM)
                naive_dt = datetime.fromisoformat(follow_up_date)
                
                # Make it timezone-aware in the configured timezone (Asia/Kolkata)
                from django.conf import settings
                import pytz
                
                tz_obj = pytz.timezone(settings.TIME_ZONE)
                aware_dt = tz_obj.localize(naive_dt)
                
                # Django will convert to UTC for storage automatically
                lead.next_follow_up = aware_dt
                lead.save()
                messages.success(request, "Visit scheduled successfully!")
            except Exception as e:
                messages.error(request, f"Could not schedule visit: {str(e)}")
        else:
            messages.error(request, "Please provide a valid date")
    
    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
@require_POST
def mark_followup_complete(request, lead_id):
    """Mark follow-up as completed and create an activity record"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    
    if not lead.next_follow_up:
        messages.warning(request, "No visit scheduled for this lead.")
        redirect_url = request.META.get('HTTP_REFERER', reverse('lead_detail', kwargs={'lead_id': lead_id}))
        return redirect(redirect_url)
    
    try:
        # Create an activity record for the completed follow-up
        LeadActivity.objects.create(
            lead=lead,
            counsellor=counsellor,
            activity_type='FOLLOW_UP',
            subject=f"Visit completed: {lead.first_name} {lead.last_name}",
            description=f"Completed scheduled visit with {lead.first_name} {lead.last_name}",
            scheduled_date=lead.next_follow_up,
            completed_date=timezone.now(),
            is_completed=True,
            duration=30  # Default 30 minutes for follow-up
        )
        
        # Clear the follow-up date
        lead.next_follow_up = None
        lead.last_contact_date = timezone.now()
        lead.save()
        
        messages.success(request, "Visit marked as completed!")
    except Exception as e:
        messages.error(request, f"Could not complete visit: {str(e)}")
    
    # Redirect back to the referrer or lead detail
    redirect_url = request.META.get('HTTP_REFERER', reverse('lead_detail', kwargs={'lead_id': lead_id}))
    return redirect(redirect_url)


@counsellor_required
def evaluate_conversion_score(request, lead_id):
    """Call AI API to assign an admission likelihood score (0-100) based on student profile."""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)

    prompt = (
        "You are an expert college admissions evaluator. Analyze this student's profile and predict their likelihood of successful enrollment.\n\n"
        "EVALUATION CRITERIA:\n"
        "1. Academic Background (30%): School reputation, graduation status, academic achievements\n"
        "2. Course Interest Alignment (25%): Clarity of course choice, relevance to background\n"
        "3. Engagement Level (20%): Lead status, priority, response to communications\n"
        "4. Financial Capability (15%): Payment history if any\n"
        "5. Profile Completeness (10%): Information quality, contact details, follow-up responsiveness\n\n"
        "SCORING GUIDELINES:\n"
        "- 90-100: Exceptional candidate, high-value, clear goals, strong background\n"
        "- 80-89: Very good candidate, likely to enroll, good academic profile\n"
        "- 70-79: Good candidate, moderate likelihood, some concerns\n"
        "- 60-69: Average candidate, uncertain enrollment, needs nurturing\n"
        "- 50-59: Below average, low likelihood, significant concerns\n"
        "- 0-49: Poor candidate, very unlikely to enroll\n\n"
        "STUDENT PROFILE:\n"
        f"Name: {lead.first_name} {lead.last_name}\n"
        f"12th School: {lead.school_name or 'Not provided'}\n"
        f"Graduation Status: {lead.graduation_status or 'Not provided'}\n"
        f"Graduation Course: {lead.graduation_course or 'Not provided'}\n"
        f"Graduation College: {lead.graduation_college or 'Not provided'}\n"
        f"Course Interested: {lead.course_interested or 'Not specified'}\n"
        f"Lead Status: {lead.status or 'Not set'}\n"
        f"Priority: {lead.priority or 'Not set'}\n"
        f"Financial Notes: {lead.notes or 'No financial info'}\n"
        f"Notes: {lead.notes or 'No notes'}\n\n"
        "Based on the above criteria, provide ONLY an integer score from 0-100 representing the admission likelihood:"
    )

    score = None
    error_message = None

    try:
        openai_key = os.environ.get('OPENAI_API_KEY')
        if openai_key:
            # Simple call to OpenAI's responses API (fallback to a basic prompt-completion style)
            headers = {
                'Authorization': f'Bearer {openai_key}',
                'Content-Type': 'application/json'
            }
            body = {
                'model': 'gpt-4o-mini',
                'input': f"You are a college admissions scoring function. Read the student details and output ONLY an integer 0-100 for admission likelihood.\n\n{prompt}"
            }
            resp = requests.post('https://api.openai.com/v1/responses', headers=headers, json=body, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                text = (data.get('output_text') or '').strip()
                # Extract first integer 0-100
                import re
                m = re.search(r"\b(100|\d{1,2})\b", text)
                if m:
                    score = int(m.group(1))
        
        # Fallback heuristic if no key or failed to parse (college-focused)
        if score is None:
            # College-focused heuristic: base on priority, status, and academic factors
            base = {
                'NEW': 25,
                'CONTACTED': 40,
                'QUALIFIED': 55,
                'PROPOSAL_SENT': 70,
                'NEGOTIATION': 80,
                'CLOSED_WON': 95,
                'CLOSED_LOST': 5,
                'TRANSFERRED': 35,
            }.get(lead.status, 35)
            priority_bonus = {
                'LOW': -5,
                'MEDIUM': 0,
                'HIGH': 5,
                'URGENT': 10,
            }.get(lead.priority, 0)
            
            # Additional bonuses for college context
            academic_bonus = 0
            if lead.graduation_status == 'YES':
                academic_bonus += 10  # Graduates are more likely to enroll
            if lead.course_interested:
                academic_bonus += 5   # Clear course interest is positive
            if lead.school_name:
                academic_bonus += 5   # Having school info shows engagement
                
            score = max(0, min(100, base + priority_bonus + academic_bonus))
    except Exception as e:
        error_message = str(e)

    if score is not None:
        lead.conversion_score = score
        lead.save()
        messages.success(request, f"Admission likelihood score updated: {score}")
    else:
        messages.error(request, f"Could not evaluate admission score: {error_message or 'Unknown error'}")

    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
def run_agentic_workflow(request, lead_id):
    """Agentic AI workflow for college admissions: enrich → score → route (with reasoning)."""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)

    openai_key = os.environ.get('OPENAI_API_KEY')
    headers = {'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'} if openai_key else None

    # Agent 1: Enrich student profile (academic background and interests)
    try:
        enrichment_prompt = (
            "You are an expert college admissions data enricher. Analyze the student's educational background and create a comprehensive academic profile.\n\n"
            "TASK: Create an academic profile summary and enrichment notes based on the student's educational background.\n\n"
            "STUDENT DATA:\n"
            f"Name: {lead.first_name} {lead.last_name}\n"
            f"12th School: {lead.school_name or 'Not provided'}\n"
            f"Graduation Status: {lead.graduation_status or 'Not provided'}\n"
            f"Graduation Course: {lead.graduation_course or 'Not provided'}\n"
            f"Graduation Year: {lead.graduation_year or 'Not provided'}\n"
            f"Graduation College: {lead.graduation_college or 'Not provided'}\n"
            f"Course Interested: {lead.course_interested or 'Not specified'}\n"
            f"Notes: {lead.notes or 'No additional notes'}\n\n"
            "ANALYSIS GUIDELINES:\n"
            "1. Academic Profile: Summarize educational background, achievements, and academic level\n"
            "2. Enrichment Notes: Identify strengths, potential concerns, and academic trajectory\n"
            "3. Consider school reputation, course relevance, and academic progression\n"
            "4. Note any gaps or inconsistencies in the academic journey\n\n"
            "RESPONSE FORMAT (JSON):\n"
            "{\n"
            '  "academic_profile": "Brief summary of academic background and level",\n'
            '  "enrichment_notes": "Key insights about academic strengths and considerations"\n'
            "}\n\n"
            "Provide the academic profile analysis:"
        )
        academic_profile = None
        enrichment_notes = None
        if headers:
            body = {'model': 'gpt-4o-mini', 'input': enrichment_prompt}
            r = requests.post('https://api.openai.com/v1/responses', headers=headers, json=body, timeout=20)
            if r.status_code == 200:
                txt = (r.json().get('output_text') or '').strip()
                m = re.search(r'academic_profile\s*[:\"]\s*([^\n\"]+)', txt, re.I)
                n = re.search(r'enrichment_notes\s*[:\"]\s*([^\n]+)', txt, re.I)
                if m:
                    academic_profile = m.group(1).strip()[:150]
                if n:
                    enrichment_notes = n.group(1).strip()
        if not academic_profile:
            # Heuristic fallback for academic profile
            if lead.graduation_status == 'YES':
                academic_profile = f"Graduate in {lead.graduation_course or 'General'} from {lead.graduation_college or 'College'}"
            else:
                academic_profile = f"12th Pass from {lead.school_name or 'School'}"
        if not enrichment_notes:
            enrichment_notes = 'Academic profile enriched based on educational background and interests.'
        lead.enriched_job_title = academic_profile  # Reusing this field for academic profile
        lead.enrichment_notes = enrichment_notes
        lead.save()
    except Exception as e:
        messages.warning(request, f"Academic enrichment failed; using fallback. {str(e)}")

    # Agent 2: Score admission likelihood (college-focused scoring)
    try:
        prompt = (
            "You are an expert college admissions evaluator. Analyze this student's profile and predict their likelihood of successful enrollment.\n\n"
            "EVALUATION CRITERIA:\n"
            "1. Academic Background (30%): School reputation, graduation status, academic achievements\n"
            "2. Course Interest Alignment (25%): Clarity of course choice, relevance to background\n"
            "3. Engagement Level (20%): Lead status, priority, response to communications\n"
        "4. Financial Capability (15%): Payment history if any\n"
            "5. Profile Completeness (10%): Information quality, contact details, follow-up responsiveness\n\n"
            "SCORING GUIDELINES:\n"
            "- 90-100: Exceptional candidate, high-value, clear goals, strong background\n"
            "- 80-89: Very good candidate, likely to enroll, good academic profile\n"
            "- 70-79: Good candidate, moderate likelihood, some concerns\n"
            "- 60-69: Average candidate, uncertain enrollment, needs nurturing\n"
            "- 50-59: Below average, low likelihood, significant concerns\n"
            "- 0-49: Poor candidate, very unlikely to enroll\n\n"
            "STUDENT PROFILE:\n"
            f"Name: {lead.first_name} {lead.last_name}\n"
            f"12th School: {lead.school_name or 'Not provided'}\n"
            f"Academic Profile: {lead.enriched_job_title or 'Not enriched'}\n"
            f"Graduation Status: {lead.graduation_status or 'Not provided'}\n"
            f"Graduation Course: {lead.graduation_course or 'Not provided'}\n"
            f"Graduation College: {lead.graduation_college or 'Not provided'}\n"
            f"Course Interested: {lead.course_interested or 'Not specified'}\n"
            f"Lead Status: {lead.status or 'Not set'}\n"
            f"Priority: {lead.priority or 'Not set'}\n"
            f"Financial Notes: {lead.notes or 'No financial info'}\n"
            f"Notes: {lead.notes or 'No notes'}\n\n"
            "Based on the above criteria, provide ONLY an integer score from 0-100 representing the admission likelihood:"
        )
        score = None
        if headers:
            body = {'model': 'gpt-4o-mini', 'input': f"{prompt}"}
            r = requests.post('https://api.openai.com/v1/responses', headers=headers, json=body, timeout=20)
            if r.status_code == 200:
                txt = (r.json().get('output_text') or '').strip()
                m = re.search(r"\b(100|\d{1,2})\b", txt)
                if m:
                    score = int(m.group(1))
        if score is None:
            # College-focused heuristic scoring
            base = {
                'NEW': 25, 'CONTACTED': 40, 'QUALIFIED': 55,
                'PROPOSAL_SENT': 70, 'NEGOTIATION': 80,
                'CLOSED_WON': 95, 'CLOSED_LOST': 5, 'TRANSFERRED': 35,
            }.get(lead.status, 35)
            priority_bonus = {'LOW': -5, 'MEDIUM': 0, 'HIGH': 5, 'URGENT': 10}.get(lead.priority, 0)
            
            # Additional bonuses for college context
            if lead.graduation_status == 'YES':
                base += 10  # Graduates are more likely to enroll
            if lead.course_interested:
                base += 5   # Clear course interest is positive
            if lead.school_name:
                base += 5   # Having school info shows engagement
                
            score = max(0, min(100, base + priority_bonus))
        lead.conversion_score = score
        lead.save()
    except Exception as e:
        messages.warning(request, f"Admission scoring failed; used fallback. {str(e)}")

    # Agent 3: Route to appropriate academic counselor/department
    try:
        route_prompt = (
            "You are an expert college admissions routing AI. Analyze this student's profile and route them to the most appropriate academic department/counselor.\n\n"
            "ROUTING OPTIONS:\n"
            "- undergraduate_counselor: For 12th pass students seeking bachelor's degrees, general courses, or undecided majors\n"
            "- graduate_counselor: For graduates seeking master's, MBA, PhD, or advanced degrees\n"
            "- specialized_department: For high-value students in competitive fields (Engineering, Medicine, Law, Architecture, IIT/JEE prep)\n"
            "- senior_counselor: For high-priority cases, complex requirements, or students needing specialized attention\n\n"
            "ROUTING CRITERIA:\n"
            "1. Graduation Status: YES = graduate_counselor, NO = undergraduate_counselor (unless high-value)\n"
            "2. Course Complexity: Engineering/Medicine/Law = specialized_department\n"
            "3. Admission Score: 80+ = senior_counselor, 60-79 = specialized_department\n"
            "4. High-value cases: senior_counselor or specialized_department\n"
            "5. Academic Profile: Advanced background = graduate_counselor\n\n"
            "STUDENT PROFILE:\n"
            f"Name: {lead.first_name} {lead.last_name}\n"
            f"12th School: {lead.school_name or 'Not provided'}\n"
            f"Graduation Status: {lead.graduation_status or 'Not provided'}\n"
            f"Graduation Course: {lead.graduation_course or 'Not provided'}\n"
            f"Graduation College: {lead.graduation_college or 'Not provided'}\n"
            f"Course Interested: {lead.course_interested or 'Not specified'}\n"
            f"Academic Profile: {lead.enriched_job_title or 'Not enriched'}\n"
            f"Admission Likelihood Score: {lead.conversion_score or 0}/100\n"
            f"Financial Notes: {lead.notes or 'No financial info'}\n"
            f"Priority: {lead.priority or 'Not set'}\n"
            f"Status: {lead.status or 'Not set'}\n\n"
            "RESPONSE FORMAT:\n"
            "route=<option>\n"
            "reason=<brief explanation of routing decision>\n\n"
            "Analyze the profile and provide the most appropriate routing decision:"
        )
        routed_to = None
        routing_reason = None
        if headers:
            body = {'model': 'gpt-4o-mini', 'input': route_prompt}
            r = requests.post('https://api.openai.com/v1/responses', headers=headers, json=body, timeout=20)
            if r.status_code == 200:
                txt = (r.json().get('output_text') or '').lower()
                m = re.search(r'route\s*=\s*(undergraduate_counselor|graduate_counselor|specialized_department|senior_counselor)', txt)
                n = re.search(r'reason\s*=\s*(.+)', txt)
                if m:
                    routed_to = m.group(1)
                if n:
                    routing_reason = n.group(1).strip()
        if not routed_to:
            # College-focused heuristic routing
            score = lead.conversion_score or 0
            course = (lead.course_interested or '').lower()
            graduation_status = lead.graduation_status or 'NO'
            
            # Route based on graduation status and course complexity
            if graduation_status == 'YES':
                if any(word in course for word in ['mba', 'masters', 'phd', 'postgraduate', 'pg']):
                    routed_to = 'graduate_counselor'
                elif any(word in course for word in ['engineering', 'medicine', 'law', 'architecture']):
                    routed_to = 'specialized_department'
                else:
                    routed_to = 'graduate_counselor'
            else:
                if score >= 75 or any(word in course for word in ['engineering', 'medicine', 'law']):
                    routed_to = 'specialized_department'
                elif score >= 60:
                    routed_to = 'senior_counselor'
                else:
                    routed_to = 'undergraduate_counselor'
                    
        if not routing_reason:
            routing_reason = f"Assigned to {routed_to.replace('_', ' ')} based on admission score {lead.conversion_score}, course interest '{lead.course_interested}', and graduation status '{lead.graduation_status}'."
        lead.routed_to = routed_to
        lead.routing_reason = routing_reason[:1000]
        lead.save()
        # Execute the actual routing actions
        routing_success = execute_academic_routing(lead, routed_to, routing_reason)
        
        if routing_success:
            messages.success(request, f"Academic workflow complete. Routed to {routed_to.replace('_',' ')} and status updated.")
        else:
            messages.warning(request, f"Academic workflow completed but routing actions failed. Routed to {routed_to.replace('_',' ')}.")
    except Exception as e:
        messages.error(request, f"Academic routing failed: {str(e)}")

    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


def execute_academic_routing(lead, routed_to, routing_reason):
    """
    Execute the actual routing actions based on the AI routing decision
    """
    from .models import NotificationCounsellor, NotificationAdmin
    
    try:
        # Get the current counsellor's admin for notifications
        current_admin = lead.assigned_counsellor.admin if lead.assigned_counsellor else None
        
        if routed_to == 'undergraduate_counselor':
            # Route to undergraduate counseling team
            # Update lead status and add routing note
            lead.status = 'QUALIFIED'  # Move to qualified status
            lead.priority = 'MEDIUM'   # Set appropriate priority
            if not lead.notes:
                lead.notes = f"Routed to Undergraduate Counseling: {routing_reason}"
            else:
                lead.notes += f"\n\nRouted to Undergraduate Counseling: {routing_reason}"
            lead.save()
            
            # Create notification for admin
            if current_admin:
                NotificationAdmin.objects.create(
                    admin=current_admin,
                    message=f"Student {lead.first_name} {lead.last_name} routed to Undergraduate Counseling for {lead.course_interested}"
                )
            
        elif routed_to == 'graduate_counselor':
            # Route to graduate counseling team
            lead.status = 'QUALIFIED'
            lead.priority = 'HIGH'  # Graduate students typically higher priority
            if not lead.notes:
                lead.notes = f"Routed to Graduate Counseling: {routing_reason}"
            else:
                lead.notes += f"\n\nRouted to Graduate Counseling: {routing_reason}"
            lead.save()
            
            # Create notification for admin
            if current_admin:
                NotificationAdmin.objects.create(
                    admin=current_admin,
                    message=f"Graduate student {lead.first_name} {lead.last_name} routed to Graduate Counseling for {lead.course_interested}"
                )
                
        elif routed_to == 'specialized_department':
            # Route to specialized academic department
            lead.status = 'PROPOSAL_SENT'  # Move to proposal stage
            lead.priority = 'HIGH'  # Specialized departments get high priority
            if not lead.notes:
                lead.notes = f"Routed to Specialized Department: {routing_reason}"
            else:
                lead.notes += f"\n\nRouted to Specialized Department: {routing_reason}"
            lead.save()
            
            # Create notification for admin
            if current_admin:
                NotificationAdmin.objects.create(
                    admin=current_admin,
                    message=f"Student {lead.first_name} {lead.last_name} routed to Specialized Department for {lead.course_interested} - High Priority"
                )
                
        elif routed_to == 'senior_counselor':
            # Route to senior counselor
            lead.status = 'NEGOTIATION'  # Move to negotiation stage
            lead.priority = 'URGENT'  # Senior counselor handles urgent cases
            if not lead.notes:
                lead.notes = f"Routed to Senior Counselor: {routing_reason}"
            else:
                lead.notes += f"\n\nRouted to Senior Counselor: {routing_reason}"
            lead.save()
            
            # Create notification for admin
            if current_admin:
                NotificationAdmin.objects.create(
                    admin=current_admin,
                    message=f"Student {lead.first_name} {lead.last_name} routed to Senior Counselor for {lead.course_interested} - Urgent Priority"
                )
        
        # Create a lead activity record for the routing action
        from .models import LeadActivity
        LeadActivity.objects.create(
            lead=lead,
            activity_type='ROUTED',
            description=f"AI routed student to {routed_to.replace('_', ' ').title()}: {routing_reason}",
            counsellor=lead.assigned_counsellor
        )
        
        return True
        
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error in execute_academic_routing: {e}")
        return False


@counsellor_required
def mark_lead_lost(request, lead_id):
    """Mark lead as lost"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    
    if request.method == 'POST':
        reason = request.POST.get('reason', '')
        lead.status = 'CLOSED_LOST'
        lead.notes += f"\n\nLost Reason: {reason}"
        lead.save()
        messages.success(request, "Lead marked as lost")
    
    return redirect(reverse('lead_detail', kwargs={'lead_id': lead_id}))


@counsellor_required
def counsellor_calendar(request):
    """Counsellor calendar view showing activities and follow-ups"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    
    context = {
        'page_title': 'My Calendar',
        'counsellor': counsellor,
    }
    return render(request, 'counsellor_template/counsellor_calendar.html', context)


@counsellor_required
def get_calendar_events(request):
    """API endpoint to get calendar events (activities and follow-ups)"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    
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
    
    # Get scheduled activities
    activities_query = LeadActivity.objects.filter(
        counsellor=counsellor,
        scheduled_date__isnull=False
    ).select_related('lead')
    
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
                # Default 1 hour duration if not specified
                end_time = activity.scheduled_date + timedelta(hours=1)
                end_iso = end_time.isoformat()
            
            # Get activity type display name
            activity_type_display = dict(ActivityType.get_all_choices()).get(activity.activity_type, activity.activity_type)
            
            events.append({
                'id': f'activity_{activity.id}',
                'title': f"{activity_type_display}: {activity.subject}",
                'start': start_iso,
                'end': end_iso,
                'color': '#007bff',  # Blue for activities
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'activity',
                    'lead_name': f"{activity.lead.first_name} {activity.lead.last_name}",
                    'description': activity.description or 'No description',
                    'activity_id': activity.id,
                    'lead_id': activity.lead.id,
                }
            })
    
    # Get follow-ups from leads
    followups_query = Lead.objects.filter(
        assigned_counsellor=counsellor,
        next_follow_up__isnull=False
    ).select_related('source')
    
    if start_date and end_date:
        followups_query = followups_query.filter(
            next_follow_up__gte=start_date,
            next_follow_up__lte=end_date
        )
    
    for lead in followups_query:
        if lead.next_follow_up:
            # Follow-ups are all-day events, so we only set the date part
            followup_date = lead.next_follow_up.date()
            events.append({
                'id': f'followup_{lead.id}',
                'title': f"Visit: {lead.first_name} {lead.last_name}",
                'start': followup_date.isoformat(),
                'allDay': True,
                'color': '#28a745',  # Green for visits
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'followup',
                    'lead_name': f"{lead.first_name} {lead.last_name}",
                    'lead_id': lead.id,
                    'course_interested': lead.course_interested or 'N/A',
                    'school_name': lead.school_name or 'N/A',
                }
            })
    
    return JsonResponse(events, safe=False)


@counsellor_required
def check_current_time_notifications(request):
    """API endpoint to check for activities/follow-ups at current time"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    now = timezone.now()
    
    # Time window: check within 1 minute before and after current time (exact match)
    time_window_start = now - timedelta(minutes=1)
    time_window_end = now + timedelta(minutes=1)
    
    notifications = []
    notified_keys = set()  # Track all notification keys in this response
    
    # Check for scheduled activities at current time
    activities = LeadActivity.objects.filter(
        counsellor=counsellor,
        scheduled_date__isnull=False,
        scheduled_date__gte=time_window_start,
        scheduled_date__lte=time_window_end
    ).select_related('lead').order_by('scheduled_date')
    
    for activity in activities:
        # Check if times match (same hour and minute)
        activity_time = activity.scheduled_date
        time_diff_seconds = abs((activity_time - now).total_seconds())
        
        # Only notify if within 60 seconds (1 minute) of scheduled time
        if time_diff_seconds <= 60:
            # Create unique notification key for this specific activity and time
            notification_key = f'activity_notified_{activity.id}_{activity_time.date()}_{activity_time.hour}_{activity_time.minute}'
            
            # Check session to avoid duplicates
            if not request.session.get(notification_key, False):
                activity_type_display = dict(ActivityType.get_all_choices()).get(activity.activity_type, activity.activity_type)
                notification_data = {
                    'type': 'activity',
                    'id': activity.id,
                    'title': f"{activity_type_display}: {activity.subject}",
                    'message': f"You have a scheduled {activity_type_display.lower()} with {activity.lead.first_name} {activity.lead.last_name}",
                    'lead_id': activity.lead.id,
                    'lead_name': f"{activity.lead.first_name} {activity.lead.last_name}",
                    'scheduled_time': activity.scheduled_date.isoformat(),
                    'description': activity.description or 'No description',
                    'notification_key': notification_key,
                    'unique_id': f'activity_{activity.id}_{activity_time.timestamp()}'
                }
                notifications.append(notification_data)
                notified_keys.add(notification_key)
                # Mark as notified in session (expires after 10 minutes)
                request.session[notification_key] = True
                request.session.set_expiry(600)  # 10 minutes
    
    # Check for follow-ups at current time
    followups = Lead.objects.filter(
        assigned_counsellor=counsellor,
        next_follow_up__isnull=False,
        next_follow_up__gte=time_window_start,
        next_follow_up__lte=time_window_end
    ).select_related('source')
    
    for lead in followups:
        if lead.next_follow_up:
            # Check if times match (within 1 minute)
            time_diff_seconds = abs((lead.next_follow_up - now).total_seconds())
            
            if time_diff_seconds <= 60:
                # Create unique notification key for this specific follow-up and time
                notification_key = f'followup_notified_{lead.id}_{lead.next_follow_up.date()}_{lead.next_follow_up.hour}_{lead.next_follow_up.minute}'
                
                if not request.session.get(notification_key, False):
                    notification_data = {
                        'type': 'followup',
                        'id': lead.id,
                        'title': f"Visit: {lead.first_name} {lead.last_name}",
                        'message': f"You have a visit scheduled with {lead.first_name} {lead.last_name}",
                        'lead_id': lead.id,
                        'lead_name': f"{lead.first_name} {lead.last_name}",
                        'scheduled_time': lead.next_follow_up.isoformat(),
                        'course_interested': lead.course_interested or 'N/A',
                        'notification_key': notification_key,
                        'unique_id': f'followup_{lead.id}_{lead.next_follow_up.timestamp()}'
                    }
                    notifications.append(notification_data)
                    notified_keys.add(notification_key)
                    # Mark as notified in session
                    request.session[notification_key] = True
                    request.session.set_expiry(600)  # 10 minutes
    
    # Group notifications by scheduled time for better organization
    grouped_notifications = {}
    for notification in notifications:
        scheduled_time = notification['scheduled_time']
        if scheduled_time not in grouped_notifications:
            grouped_notifications[scheduled_time] = []
        grouped_notifications[scheduled_time].append(notification)
    
    return JsonResponse({
        'notifications': notifications,
        'grouped': grouped_notifications,
        'count': len(notifications)
    }, safe=False)


@counsellor_required
def get_lead_calendar_events(request, lead_id):
    """API endpoint to get calendar events for a specific lead"""
    counsellor = get_object_or_404(Counsellor, admin=request.user)
    lead = get_object_or_404(Lead, id=lead_id, assigned_counsellor=counsellor)
    
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
    
    # Get all activities for this lead
    activities_query = LeadActivity.objects.filter(
        lead=lead,
        counsellor=counsellor,
        scheduled_date__isnull=False
    ).order_by('scheduled_date')
    
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
            status_class = 'success' if activity.is_completed else 'warning'
            activity_color = '#28a745' if activity.is_completed else '#ffc107'  # Green if completed, yellow if pending
            
            events.append({
                'id': f'activity_{activity.id}',
                'title': f"{activity_type_display}: {activity.subject}",
                'start': start_iso,
                'end': end_iso,
                'color': activity_color,
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'activity',
                    'activity_id': activity.id,
                    'description': activity.description or 'No description',
                    'outcome': activity.outcome or 'N/A',
                    'is_completed': activity.is_completed,
                    'status': 'Completed' if activity.is_completed else 'Pending',
                    'duration': activity.duration or 60,
                }
            })
    
    # Add follow-up if scheduled
    if lead.next_follow_up:
        followup_date = lead.next_follow_up.date()
        # Only include if within date range or no range specified
        include_followup = True
        if start_date and end_date:
            include_followup = (followup_date >= start_date.date() and followup_date <= end_date.date())
        
        if include_followup:
            events.append({
                'id': f'followup_{lead.id}',
                'title': f"Visit: {lead.first_name} {lead.last_name}",
                'start': followup_date.isoformat(),
                'allDay': True,
                'color': '#007bff',  # Blue for visits
                'textColor': '#ffffff',
                'extendedProps': {
                    'type': 'followup',
                    'lead_id': lead.id,
                    'course_interested': lead.course_interested or 'N/A',
                    'school_name': lead.school_name or 'N/A',
                }
            })
    
    return JsonResponse(events, safe=False)

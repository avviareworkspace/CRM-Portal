from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from functools import wraps

def paginate_queryset(request, queryset, count=10):
    """
    Utility function to paginate a queryset.
    """
    paginator = Paginator(queryset, count)
    page = request.GET.get('page')
    try:
        paginated_objects = paginator.page(page)
    except PageNotAnInteger:
        paginated_objects = paginator.page(1)
    except EmptyPage:
        paginated_objects = paginator.page(paginator.num_pages)
    return paginated_objects


def user_type_required(user_type):
    """
    Ensure the user is authenticated and matches the required user_type.
    """
    def decorator(view_func):
        @login_required(login_url='login_page')
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if getattr(request.user, 'user_type', None) != user_type:
                return HttpResponseForbidden("Access denied")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def admin_perm_required(perm_name):
    """
    Check a specific Admin permission field.
    perm_name: 'delete', 'performance', 'counsellor_work', 'settings'
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            from .models import Admin as AdminModel
            try:
                admin_obj = AdminModel.objects.get(admin=request.user)
            except AdminModel.DoesNotExist:
                return HttpResponseForbidden("Access denied")
            checker = getattr(admin_obj, f'has_perm_{perm_name}', None)
            if checker and not checker():
                from django.contrib import messages
                messages.error(request, "You don't have permission for this action.")
                from django.shortcuts import redirect
                from django.urls import reverse
                return redirect(reverse('admin_home'))
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def get_counsellor_daily_target_progress(counsellor):
    """
    Today's daily target assignment and completed count (same rules as Today's Target page).
    Completed = distinct completed activities where (completed_date is today OR scheduled_date <= today).
    """
    from django.db.models import Q, Count
    from django.utils import timezone
    from .models import DailyTarget, DailyTargetAssignment, LeadActivity

    today = timezone.localdate()

    assignment = (
        DailyTargetAssignment.objects
        .filter(counsellor=counsellor, target__target_date=today)
        .select_related('target')
        .first()
    )
    if not assignment:
        target, _ = DailyTarget.objects.get_or_create(
            target_date=today,
            defaults={'target_count': 100},
        )
        assignment, _ = DailyTargetAssignment.objects.get_or_create(
            target=target, counsellor=counsellor,
        )

    target_count = assignment.target.target_count

    completed_qs = LeadActivity.objects.filter(
        Q(completed_date__date=today) | Q(scheduled_date__date__lte=today),
        counsellor=counsellor,
        is_completed=True,
    ).distinct()

    completed_toward_target = completed_qs.count()

    toward_target_by_type = dict(
        completed_qs.values('activity_type').annotate(n=Count('id')).values_list('activity_type', 'n')
    )

    remaining = max(0, target_count - completed_toward_target)
    pct = 0
    if target_count > 0:
        pct = min(100, int(round(100 * completed_toward_target / target_count)))

    return {
        'assignment': assignment,
        'daily_target': target_count,
        'completed_toward_target': completed_toward_target,
        'target_remaining': remaining,
        'target_progress_pct': pct,
        'toward_target_by_type': toward_target_by_type,
    }


def get_counsellor_activity_snapshot(counsellor):
    """
    Numeric snapshot for dashboards: pipeline by status, visits, and monthly activity counts.
    "Today" work-on-target metrics use the same rules as the daily target (see get_counsellor_daily_target_progress).
    """
    ttl = int(getattr(settings, 'COUNSELLOR_SNAPSHOT_CACHE_SECONDS', 45))
    cache_key = f'crm:counsellor_activity_snapshot:{counsellor.pk}'
    if ttl > 0:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit

    from datetime import timedelta
    from django.db.models import Count
    from django.utils import timezone
    from .models import Lead, LeadActivity

    now_local = timezone.localtime(timezone.now())
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    target_progress = get_counsellor_daily_target_progress(counsellor)
    tt = target_progress['toward_target_by_type']

    my_leads = Lead.objects.filter(assigned_counsellor=counsellor)
    status_counts = dict(
        my_leads.values('status').annotate(c=Count('id')).values_list('status', 'c')
    )

    visits_scheduled = my_leads.filter(next_follow_up__isnull=False).count()
    visits_today = my_leads.filter(
        next_follow_up__gte=today_start,
        next_follow_up__lt=today_end,
    ).count()
    visits_overdue = my_leads.filter(
        next_follow_up__isnull=False,
        next_follow_up__lt=now_local,
    ).count()

    activities_completed_month = LeadActivity.objects.filter(
        counsellor=counsellor,
        is_completed=True,
        completed_date__gte=month_start,
    ).count()

    leads_assigned_this_month = my_leads.filter(created_at__gte=month_start).count()

    visit_activities_month = LeadActivity.objects.filter(
        counsellor=counsellor,
        is_completed=True,
        activity_type='FOLLOW_UP',
        completed_date__gte=month_start,
    ).count()

    pending_activities = LeadActivity.objects.filter(
        counsellor=counsellor, is_completed=False
    ).count()

    new_leads_today = my_leads.filter(
        created_at__gte=today_start,
        created_at__lt=today_end,
    ).count()

    contact_touchpoints_today = sum(tt.get(t, 0) for t in ('CALL', 'EMAIL', 'MEETING'))

    follow_up_activities_today = tt.get('FOLLOW_UP', 0)

    note_activities_today = tt.get('NOTE', 0)
    transfer_activities_today = tt.get('TRANSFER', 0)

    total_activities_today = target_progress['completed_toward_target']

    leads_worked_today = my_leads.filter(
        last_contact_date__gte=today_start,
        last_contact_date__lt=today_end,
    ).count()

    contacted_updates_today = my_leads.filter(
        status='CONTACTED',
        last_contact_date__gte=today_start,
        last_contact_date__lt=today_end,
    ).count()

    out = {
        'new': status_counts.get('NEW', 0),
        'contacted': status_counts.get('CONTACTED', 0),
        'qualified': status_counts.get('QUALIFIED', 0),
        'closed_won': status_counts.get('CLOSED_WON', 0),
        'closed_lost': status_counts.get('CLOSED_LOST', 0),
        'visits_scheduled': visits_scheduled,
        'visits_today': visits_today,
        'visits_overdue': visits_overdue,
        'activities_completed_month': activities_completed_month,
        'leads_assigned_this_month': leads_assigned_this_month,
        'visit_activities_month': visit_activities_month,
        'pending_activities': pending_activities,
        'status_counts': status_counts,
        # Today
        'new_leads_today': new_leads_today,
        'contact_touchpoints_today': contact_touchpoints_today,
        'follow_up_activities_today': follow_up_activities_today,
        'note_activities_today': note_activities_today,
        'transfer_activities_today': transfer_activities_today,
        'total_activities_today': total_activities_today,
        'leads_worked_today': leads_worked_today,
        'contacted_updates_today': contacted_updates_today,
        'activities_today_by_type': tt,
        'daily_target': target_progress['daily_target'],
        'completed_toward_target': target_progress['completed_toward_target'],
        'target_remaining': target_progress['target_remaining'],
        'target_progress_pct': target_progress['target_progress_pct'],
    }
    if ttl > 0:
        cache.set(cache_key, out, ttl)
    return out

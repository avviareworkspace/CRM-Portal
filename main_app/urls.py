"""URL routes for main_app (admin, counsellor, auth)."""
from django.urls import path
from django.conf import settings
from django.contrib.auth import views as auth_views

from . import admin_views, counsellor_views, views, views_meta

urlpatterns = [
    # Authentication URLs
    path("", views.login_page, name='login_page'),
    path("doLogin/", views.doLogin, name='user_login'),
    path("logout_user/", views.logout_user, name='user_logout'),
    path("firebase-messaging-sw.js", views.showFirebaseJS, name='showFirebaseJS'),

    path(
        "integrations/meta/webhook/",
        views_meta.meta_webhook,
        name="meta_webhook",
    ),

    # Password Reset URLs
    path('password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', views.custom_password_reset_confirm, name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    
    # Admin URLs
    path("admin/home/", admin_views.admin_home, name='admin_home'),
    path("admin/counsellor-activity-progress/", admin_views.counsellor_activity_progress_report, name='counsellor_activity_progress_report'),
    path("admin/profile/", admin_views.admin_view_profile, name='admin_view_profile'),
    path("admin/notifications/", admin_views.admin_view_notifications, name='admin_view_notifications'),
    
    # Counsellor Management
    path("counsellor/add/", admin_views.add_counsellor, name='add_counsellor'),
    path("counsellor/manage/", admin_views.manage_counsellors, name='manage_counsellors'),
    path("counsellor/edit/<int:counsellor_id>/", admin_views.edit_counsellor, name='edit_counsellor'),
    path("counsellor/delete/<int:counsellor_id>/", admin_views.delete_counsellor, name='delete_counsellor'),
    # Admin User Management
    path("admin/add/", admin_views.add_admin, name='add_admin'),
    path("admin/manage/", admin_views.manage_admins, name='manage_admins'),
    path("admin/edit/<int:admin_id>/", admin_views.edit_admin, name='edit_admin'),
    path("admin/delete/<int:admin_id>/", admin_views.delete_admin, name='delete_admin'),
    path("counsellor/performance/", admin_views.counsellor_performance, name='counsellor_performance'),
    path("counsellor/work/", admin_views.counsellor_work_view, name='counsellor_work_view'),
    
    # Lead Management
    path("leads/manage/", admin_views.manage_leads, name='manage_leads'),
    path("leads/add/", admin_views.add_lead, name='add_lead'),
    path("leads/view/<int:lead_id>/", admin_views.admin_view_lead, name='admin_view_lead'),
    path("leads/ai-workflow/<int:lead_id>/", admin_views.admin_run_ai_workflow, name='admin_run_ai_workflow'),
    path("leads/manual-route/<int:lead_id>/", admin_views.manual_route_student, name='manual_route_student'),
    path("leads/edit/<int:lead_id>/", admin_views.edit_lead, name='edit_lead'),
    path("leads/delete/<int:lead_id>/", admin_views.delete_lead, name='delete_lead'),
    path("leads/delete/bulk/", admin_views.bulk_delete_leads, name='bulk_delete_leads'),
    path("leads/delete/all/", admin_views.delete_all_leads, name='delete_all_leads'),
    path("leads/import/", admin_views.import_leads, name='import_leads'),
    path("leads/import/template/<str:file_type>/", admin_views.download_import_template, name='download_import_template'),
    path("leads/assign/", admin_views.assign_leads_to_counsellors, name='assign_leads_to_counsellors'),
    path("leads/transfer/<int:lead_id>/", admin_views.transfer_lead, name='transfer_lead'),
    
    # Lead Sources
    path(
        "integrations/meta/settings/",
        views_meta.manage_meta_integration,
        name="manage_meta_integration",
    ),
    path("integrations/chats/", views_meta.social_chat_inbox, name="social_chat_inbox"),
    path(
        "integrations/chats/<int:thread_id>/send/",
        views_meta.social_chat_send,
        name="social_chat_send",
    ),

    path("lead-sources/manage/", admin_views.manage_lead_sources, name='manage_lead_sources'),
    path("lead-sources/add/", admin_views.add_lead_source, name='add_lead_source'),
    path("lead-sources/edit/<int:source_id>/", admin_views.edit_lead_source, name='edit_lead_source'),
    path("lead-sources/delete/<int:source_id>/", admin_views.delete_lead_source, name='delete_lead_source'),

    path("lead-statuses/manage/", admin_views.manage_lead_statuses, name='manage_lead_statuses'),
    path("lead-statuses/add/", admin_views.add_lead_status, name='add_lead_status'),
    path("lead-statuses/edit/<int:status_id>/", admin_views.edit_lead_status, name='edit_lead_status'),
    path("lead-statuses/delete/<int:status_id>/", admin_views.delete_lead_status, name='delete_lead_status'),

    # Activity Types
    path("activity-types/manage/", admin_views.manage_activity_types, name='manage_activity_types'),
    path("activity-types/add/", admin_views.add_activity_type, name='add_activity_type'),
    path("activity-types/edit/<int:type_id>/", admin_views.edit_activity_type, name='edit_activity_type'),
    path("activity-types/delete/<int:type_id>/", admin_views.delete_activity_type, name='delete_activity_type'),

    # Daily Targets
    path("daily-targets/", admin_views.manage_daily_targets, name='manage_daily_targets'),
    path("daily-targets/create/", admin_views.create_daily_target, name='create_daily_target'),
    path("daily-targets/update/<int:target_id>/", admin_views.update_daily_target, name='update_daily_target'),
    path("daily-targets/delete/<int:target_id>/", admin_views.delete_daily_target, name='delete_daily_target'),

    # Next Actions
    path("next-actions/manage/", admin_views.manage_next_actions, name='manage_next_actions'),
    path("next-actions/add/", admin_views.add_next_action, name='add_next_action'),
    path("next-actions/edit/<int:action_id>/", admin_views.edit_next_action, name='edit_next_action'),
    path("next-actions/delete/<int:action_id>/", admin_views.delete_next_action, name='delete_next_action'),

    # Business Management
    path("businesses/manage/", admin_views.manage_businesses, name='manage_businesses'),
    
    # Notifications
    path("notifications/send/", admin_views.send_counsellor_notification, name='send_counsellor_notification'),
    
    # Analytics
    path("analytics/leads/", admin_views.get_lead_analytics, name='get_lead_analytics'),
    
    # Admin Calendar
    path("calendar/events/", admin_views.get_admin_calendar_events, name='get_admin_calendar_events'),
    
    # Counsellor URLs
    path('counsellor/home/', counsellor_views.counsellor_home, name='counsellor_home'),
    path('counsellor/profile/', counsellor_views.counsellor_view_profile, name='counsellor_view_profile'),
    path('counsellor/notifications/', counsellor_views.counsellor_view_notifications, name='counsellor_view_notifications'),
    path('counsellor/fcmtoken/', counsellor_views.counsellor_fcmtoken, name='counsellor_fcmtoken'),
    
    # Counsellor Lead Management
    path('counsellor/leads/', counsellor_views.my_leads, name='my_leads'),
    path('counsellor/leads/<int:lead_id>/', counsellor_views.lead_detail, name='lead_detail'),
    path('counsellor/leads/<int:lead_id>/edit/', counsellor_views.edit_my_lead, name='edit_my_lead'),
    path('counsellor/leads/<int:lead_id>/alternate-phone/add/', counsellor_views.add_alternate_phone, name='add_alternate_phone'),
    path('counsellor/leads/<int:lead_id>/alternate-phone/reveal/', counsellor_views.reveal_alternate_phone, name='reveal_alternate_phone'),
    path('counsellor/leads/<int:lead_id>/phone/reveal/', counsellor_views.reveal_phone, name='reveal_phone'),
    path('counsellor/leads/<int:lead_id>/calendar/events/', counsellor_views.get_lead_calendar_events, name='get_lead_calendar_events'),
    path('counsellor/leads/<int:lead_id>/activity/add/', counsellor_views.add_lead_activity, name='add_lead_activity'),
    path('counsellor/leads/<int:lead_id>/activity/<int:activity_id>/edit/', counsellor_views.edit_lead_activity, name='edit_lead_activity'),
    path('counsellor/leads/<int:lead_id>/activity/<int:activity_id>/delete/', counsellor_views.delete_lead_activity, name='delete_lead_activity'),
    path('counsellor/leads/<int:lead_id>/activity/<int:activity_id>/complete/', counsellor_views.mark_activity_complete, name='mark_activity_complete'),
    path('counsellor/leads/<int:lead_id>/status/update/', counsellor_views.update_lead_status, name='update_lead_status'),
    path('counsellor/leads/<int:lead_id>/business/create/', counsellor_views.create_business, name='create_business'),
    path('counsellor/leads/<int:lead_id>/transfer/request/', counsellor_views.request_lead_transfer, name='request_lead_transfer'),
    path('counsellor/leads/<int:lead_id>/follow-up/schedule/', counsellor_views.schedule_follow_up, name='schedule_follow_up'),
    path('counsellor/leads/<int:lead_id>/follow-up/complete/', counsellor_views.mark_followup_complete, name='mark_followup_complete'),
    path('counsellor/leads/<int:lead_id>/conversion/evaluate/', counsellor_views.evaluate_conversion_score, name='evaluate_conversion_score'),
    path('counsellor/leads/<int:lead_id>/mark-lost/', counsellor_views.mark_lead_lost, name='mark_lead_lost'),
    
    # Counsellor Business Management
    path('counsellor/businesses/', counsellor_views.my_businesses, name='my_businesses'),
    path('counsellor/businesses/<int:business_id>/', counsellor_views.business_detail, name='business_detail'),
    path('counsellor/businesses/<int:business_id>/status/update/', counsellor_views.update_business_status, name='update_business_status'),
    
    # Counsellor Activities & Tasks
    path('counsellor/activities/', counsellor_views.my_activities, name='my_activities'),
    path('counsellor/pending-tasks/', counsellor_views.pending_tasks, name='pending_tasks'),
    path('counsellor/daily-target/', counsellor_views.my_daily_target, name='my_daily_target'),
    
    # Counsellor Calendar
    path('counsellor/calendar/', counsellor_views.counsellor_calendar, name='counsellor_calendar'),
    path('counsellor/calendar/events/', counsellor_views.get_calendar_events, name='get_calendar_events'),
    path('counsellor/notifications/check/', counsellor_views.check_current_time_notifications, name='check_current_time_notifications'),
    
    # Counsellor Analytics
    path('counsellor/analytics/', counsellor_views.get_my_analytics, name='get_my_analytics'),
    
    # Notification Management
    path('counsellor/notification/delete/<int:notification_id>/', views.delete_counsellor_notification, name='delete_counsellor_notification'),
    path('admin/notification/delete/<int:notification_id>/', views.delete_admin_notification, name='delete_admin_notification'),
    
]

if settings.DEBUG:
    urlpatterns += [
        path('test-login/', views.test_login, name='test_login'),
        path('run-migrations/', views.run_migrations, name='run_migrations'),
    ]

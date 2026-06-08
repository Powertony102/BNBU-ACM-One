from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('member/dashboard/', views.member_dashboard, name='member-dashboard'),
    path('member/events/', views.member_event_list, name='member-event-list'),
    path('member/events/<int:event_id>/', views.member_event_detail, name='member-event-detail'),
    path('member/events/<int:event_id>/check-in/', views.member_event_checkin, name='member-event-checkin'),
    path('member/check-ins/', views.member_checkin_history, name='member-checkin-history'),
    path('member/star/', views.member_star_center, name='member-star-center'),
    path('member/profile/', views.member_profile, name='member-profile'),
    path('management/dashboard/', views.management_dashboard, name='management-dashboard'),
    path('management/star/', views.star_analytics, name='management-star-analytics'),
    path('management/events/', views.event_list_manage, name='event-list-manage'),
    path('management/events/create/', views.event_create, name='event-create'),
    path('management/events/<int:event_id>/edit/', views.event_edit, name='event-edit'),
    path('management/events/<int:event_id>/', views.event_detail_manage, name='event-detail-manage'),
    path('management/events/<int:event_id>/publish/', views.event_publish, name='event-publish'),
    path('management/events/<int:event_id>/close-check-in/', views.event_close_checkin, name='event-close-checkin'),
    path('management/events/<int:event_id>/generate-qr/', views.generate_qr_entry, name='event-generate-qr'),
    path('management/admins/', views.admin_list_manage, name='admin-list-manage'),
    path('management/admins/<int:admin_profile_id>/edit/', views.admin_edit, name='admin-edit'),
    path('management/admins/<int:admin_profile_id>/toggle-status/', views.admin_toggle_status, name='admin-toggle-status'),
    path('management/settings/', views.system_settings_manage, name='system-settings-manage'),
    path('management/audit-logs/', views.audit_log_list, name='audit-log-list'),
    path('qr/<str:token>/', views.qr_entry, name='qr-entry'),
    path('qr/<str:token>/check-in/', views.qr_checkin, name='qr-checkin'),
]

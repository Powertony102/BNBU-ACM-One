import base64
from datetime import timedelta
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import qrcode

from .forms import AdminCreateForm, AdminUpdateForm, EventForm, LoginForm, MemberProfileForm, SystemSettingsForm
from .models import AdminProfile, AuditLog, CheckInRecord, Event, EventQRCode, MemberProfile, SystemSetting, User


def log_action(operator, action, target_type, target_id=None, detail=''):
    AuditLog.objects.create(
        operator=operator,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )


def get_star_window_days():
    return int(SystemSetting.get_value('star_recent_window_days', '30'))


def build_qr_data_uri(content):
    qr_image = qrcode.make(content)
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def role_redirect(user):
    if user.role == User.Roles.MEMBER:
        return redirect('member-dashboard')
    return redirect('management-dashboard')


def require_member(user):
    return user.is_authenticated and user.role == User.Roles.MEMBER


def require_management(user):
    return user.is_authenticated and user.role in {User.Roles.ADMIN, User.Roles.SUPER_ADMIN}


def require_super_admin(user):
    return user.is_authenticated and user.role == User.Roles.SUPER_ADMIN


def login_view(request):
    if request.user.is_authenticated:
        return role_redirect(request.user)
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(request.GET.get('next') or request.POST.get('next') or reverse('home'))
    return render(request, 'core/login.html', {'form': form, 'next': request.GET.get('next', '')})


@login_required
def logout_view(request):
    if request.method == 'POST':
        logout(request)
        return redirect('login')
    return HttpResponseForbidden('仅支持 POST 退出。')


def home(request):
    if not request.user.is_authenticated:
        return redirect('login')
    return role_redirect(request.user)


@login_required
def member_dashboard(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    window_days = get_star_window_days()
    window_start = timezone.now() - timedelta(days=window_days)
    recent_checkins = profile.checkins.filter(
        status=CheckInRecord.Status.VALID,
        checkin_time__gte=window_start,
    ).select_related('event')
    recent_events = Event.objects.filter(status=Event.Status.PUBLISHED).order_by('start_time')[:5]
    context = {
        'profile': profile,
        'star_lit': recent_checkins.exists(),
        'window_days': window_days,
        'recent_checkins': recent_checkins[:5],
        'upcoming_events': recent_events,
        'checkin_count': profile.checkins.filter(status=CheckInRecord.Status.VALID).count(),
    }
    return render(request, 'core/member/dashboard.html', context)


@login_required
def member_event_list(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    events = Event.objects.exclude(status=Event.Status.CANCELED).order_by('start_time')
    return render(request, 'core/member/event_list.html', {'events': events})


@login_required
def member_event_detail(request, event_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    event = get_object_or_404(Event, pk=event_id)
    profile = get_object_or_404(MemberProfile, user=request.user)
    existing_checkin = CheckInRecord.objects.filter(
        member=profile,
        event=event,
        status=CheckInRecord.Status.VALID,
    ).first()
    return render(
        request,
        'core/member/event_detail.html',
        {
            'event': event,
            'existing_checkin': existing_checkin,
            'is_checkin_open': event.is_checkin_open(),
        },
    )


@login_required
def member_event_checkin(request, event_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST 签到。')
    event = get_object_or_404(Event, pk=event_id)
    profile = get_object_or_404(MemberProfile, user=request.user)
    if not event.is_checkin_open():
        messages.error(request, '当前活动未开放签到。')
        return redirect('member-event-detail', event_id=event.id)
    if CheckInRecord.objects.filter(
        member=profile,
        event=event,
        status=CheckInRecord.Status.VALID,
    ).exists():
        messages.warning(request, '你已经签到过这场活动。')
        return redirect('member-event-detail', event_id=event.id)
    checkin = CheckInRecord.objects.create(
        member=profile,
        event=event,
        checkin_method=CheckInRecord.Method.WEB,
        created_by=request.user,
    )
    log_action(request.user, 'member_checkin', 'Event', event.id, f'checkin_id={checkin.id}')
    messages.success(request, '签到成功。')
    return redirect('member-event-detail', event_id=event.id)


@login_required
def member_checkin_history(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    checkins = profile.checkins.select_related('event')
    return render(request, 'core/member/checkin_history.html', {'checkins': checkins})


@login_required
def member_profile(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    form = MemberProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_action(request.user, 'update_member_profile', 'MemberProfile', profile.id)
        messages.success(request, '个人资料已更新。')
        return redirect('member-profile')
    return render(request, 'core/member/profile.html', {'form': form, 'profile': profile})


@login_required
def management_dashboard(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_days = get_star_window_days()
    active_members = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE).count()
    lit_members = MemberProfile.objects.filter(
        checkins__status=CheckInRecord.Status.VALID,
        checkins__checkin_time__gte=now - timedelta(days=window_days),
    ).distinct().count()
    context = {
        'recent_event_count': Event.objects.filter(start_time__gte=now).count(),
        'today_checkin_count': CheckInRecord.objects.filter(
            status=CheckInRecord.Status.VALID,
            checkin_time__gte=today_start,
        ).count(),
        'lit_members': lit_members,
        'active_members': active_members,
    }
    return render(request, 'core/management/dashboard.html', context)


@login_required
def event_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    events = Event.objects.annotate(checkin_total=Count('checkins')).order_by('start_time')
    return render(request, 'core/management/event_list.html', {'events': events})


@login_required
def event_create(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    form = EventForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        if event.status == Event.Status.PUBLISHED and not event.published_at:
            event.published_at = timezone.now()
        event.save()
        log_action(request.user, 'create_event', 'Event', event.id)
        messages.success(request, '活动已创建。')
        return redirect('event-detail-manage', event_id=event.id)
    return render(request, 'core/management/event_form.html', {'form': form, 'page_title': '创建活动'})


@login_required
def event_edit(request, event_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    event = get_object_or_404(Event, pk=event_id)
    form = EventForm(request.POST or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        if event.status == Event.Status.PUBLISHED and not event.published_at:
            event.published_at = timezone.now()
        event.save()
        log_action(request.user, 'edit_event', 'Event', event.id)
        messages.success(request, '活动已更新。')
        return redirect('event-detail-manage', event_id=event.id)
    return render(request, 'core/management/event_form.html', {'form': form, 'page_title': '编辑活动'})


@login_required
def event_detail_manage(request, event_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    event = get_object_or_404(Event, pk=event_id)
    qr_code = event.qr_codes.filter(is_active=True).first()
    valid_checkins = event.checkins.filter(status=CheckInRecord.Status.VALID).select_related('member')
    qr_code_image = build_qr_data_uri(qr_code.url) if qr_code and qr_code.url else None
    return render(
        request,
        'core/management/event_detail.html',
        {
            'event': event,
            'qr_code': qr_code,
            'qr_code_image': qr_code_image,
            'valid_checkins': valid_checkins[:10],
            'checkin_total': valid_checkins.count(),
        },
    )


@login_required
def event_publish(request, event_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    event.status = Event.Status.PUBLISHED
    event.published_at = timezone.now()
    event.save(update_fields=['status', 'published_at', 'updated_at'])
    log_action(request.user, 'publish_event', 'Event', event.id)
    messages.success(request, '活动已发布。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def event_close_checkin(request, event_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    event.status = Event.Status.CHECKIN_CLOSED
    event.save(update_fields=['status', 'updated_at'])
    log_action(request.user, 'close_checkin', 'Event', event.id)
    messages.success(request, '签到已关闭。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def generate_qr_entry(request, event_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    event.qr_codes.filter(is_active=True).update(is_active=False)
    minutes = int(SystemSetting.get_value('qr_code_expire_minutes', '120'))
    qr_code = EventQRCode.objects.create(
        event=event,
        expires_at=timezone.now() + timedelta(minutes=minutes),
        created_by=request.user,
    )
    qr_code.url = request.build_absolute_uri(qr_code.get_entry_path())
    qr_code.save(update_fields=['url'])
    log_action(request.user, 'generate_qr', 'EventQRCode', qr_code.id, f'event={event.id}')
    messages.success(request, '活动签到入口已生成。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def admin_list_manage(request):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    form = AdminCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        admin_level = form.cleaned_data['admin_level']
        user = User.objects.create(
            username=form.cleaned_data['username'],
            email=form.cleaned_data['email'],
            role=User.Roles.SUPER_ADMIN if admin_level == AdminProfile.Level.SUPER_ADMIN else User.Roles.ADMIN,
            is_active=form.cleaned_data['is_active'],
            is_staff=True,
        )
        user.set_password(form.cleaned_data['password1'])
        user.save()
        profile = AdminProfile.objects.create(
            user=user,
            display_name=form.cleaned_data['display_name'],
            admin_level=admin_level,
            status=AdminProfile.Status.ACTIVE if form.cleaned_data['is_active'] else AdminProfile.Status.INACTIVE,
        )
        log_action(request.user, 'create_admin', 'AdminProfile', profile.id, f'user={user.username}')
        messages.success(request, '管理员账号已创建。')
        return redirect('admin-list-manage')

    admin_profiles = AdminProfile.objects.select_related('user').order_by('-admin_level', 'display_name')
    context = {
        'form': form,
        'admin_profiles': admin_profiles,
        'admin_total': admin_profiles.count(),
        'super_admin_total': admin_profiles.filter(admin_level=AdminProfile.Level.SUPER_ADMIN).count(),
    }
    return render(request, 'core/management/admin_list.html', context)


@login_required
def admin_edit(request, admin_profile_id):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    admin_profile = get_object_or_404(AdminProfile.objects.select_related('user'), pk=admin_profile_id)
    initial = {
        'display_name': admin_profile.display_name,
        'email': admin_profile.user.email,
        'admin_level': admin_profile.admin_level,
        'status': admin_profile.status,
        'is_active': admin_profile.user.is_active,
    }
    form = AdminUpdateForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        admin_profile.display_name = form.cleaned_data['display_name']
        admin_profile.admin_level = form.cleaned_data['admin_level']
        admin_profile.status = form.cleaned_data['status']
        admin_profile.save()

        admin_profile.user.email = form.cleaned_data['email']
        admin_profile.user.role = (
            User.Roles.SUPER_ADMIN
            if form.cleaned_data['admin_level'] == AdminProfile.Level.SUPER_ADMIN
            else User.Roles.ADMIN
        )
        admin_profile.user.is_active = form.cleaned_data['is_active']
        admin_profile.user.is_staff = True
        admin_profile.user.save()
        log_action(request.user, 'edit_admin', 'AdminProfile', admin_profile.id, f'user={admin_profile.user.username}')
        messages.success(request, '管理员资料已更新。')
        return redirect('admin-list-manage')
    return render(
        request,
        'core/management/admin_edit.html',
        {'form': form, 'admin_profile': admin_profile},
    )


@login_required
def admin_toggle_status(request, admin_profile_id):
    if not require_super_admin(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    admin_profile = get_object_or_404(AdminProfile.objects.select_related('user'), pk=admin_profile_id)
    if admin_profile.user_id == request.user.id:
        messages.error(request, '不能停用当前登录的超级管理员账号。')
        return redirect('admin-list-manage')
    admin_profile.user.is_active = not admin_profile.user.is_active
    admin_profile.user.save(update_fields=['is_active'])
    admin_profile.status = AdminProfile.Status.ACTIVE if admin_profile.user.is_active else AdminProfile.Status.INACTIVE
    admin_profile.save(update_fields=['status', 'updated_at'])
    log_action(request.user, 'toggle_admin_status', 'AdminProfile', admin_profile.id, f'active={admin_profile.user.is_active}')
    messages.success(request, '管理员状态已更新。')
    return redirect('admin-list-manage')


@login_required
def system_settings_manage(request):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    initial = {
        'star_recent_window_days': int(SystemSetting.get_value('star_recent_window_days', '30')),
        'qr_code_expire_minutes': int(SystemSetting.get_value('qr_code_expire_minutes', '120')),
    }
    form = SystemSettingsForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        for key in ('star_recent_window_days', 'qr_code_expire_minutes'):
            SystemSetting.objects.update_or_create(
                key=key,
                defaults={
                    'value': str(form.cleaned_data[key]),
                    'updated_by': request.user,
                },
            )
        log_action(
            request.user,
            'update_system_settings',
            'SystemSetting',
            detail=(
                f"star_recent_window_days={form.cleaned_data['star_recent_window_days']}, "
                f"qr_code_expire_minutes={form.cleaned_data['qr_code_expire_minutes']}"
            ),
        )
        messages.success(request, '系统参数已更新。')
        return redirect('system-settings-manage')
    return render(request, 'core/management/system_settings.html', {'form': form})


@login_required
def audit_log_list(request):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    query = request.GET.get('q', '').strip()
    logs = AuditLog.objects.select_related('operator')
    if query:
        logs = logs.filter(
            Q(action__icontains=query)
            | Q(target_type__icontains=query)
            | Q(detail__icontains=query)
            | Q(operator__username__icontains=query)
        )
    logs = logs[:100]
    return render(request, 'core/management/audit_logs.html', {'logs': logs, 'query': query})


def qr_entry(request, token):
    qr_code = get_object_or_404(EventQRCode, token=token)
    if not qr_code.is_valid():
        return render(request, 'core/qr_invalid.html', {'reason': '二维码已失效或被停用。'})
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), login_url=reverse('login'))
    if not require_member(request.user):
        return render(request, 'core/qr_invalid.html', {'reason': '只有队员账号可以签到。'})
    profile = get_object_or_404(MemberProfile, user=request.user)
    existing_checkin = CheckInRecord.objects.filter(
        member=profile,
        event=qr_code.event,
        status=CheckInRecord.Status.VALID,
    ).first()
    return render(
        request,
        'core/member/qr_entry.html',
        {
            'qr_code': qr_code,
            'event': qr_code.event,
            'existing_checkin': existing_checkin,
            'is_checkin_open': qr_code.event.is_checkin_open(),
        },
    )


@login_required
def qr_checkin(request, token):
    if request.method != 'POST' or not require_member(request.user):
        return HttpResponseForbidden('无权操作。')
    qr_code = get_object_or_404(EventQRCode, token=token)
    if not qr_code.is_valid():
        return render(request, 'core/qr_invalid.html', {'reason': '二维码已失效或被停用。'})
    event = qr_code.event
    profile = get_object_or_404(MemberProfile, user=request.user)
    if not event.is_checkin_open():
        messages.error(request, '当前活动未开放签到。')
        return redirect('qr-entry', token=token)
    if CheckInRecord.objects.filter(
        member=profile,
        event=event,
        status=CheckInRecord.Status.VALID,
    ).exists():
        messages.warning(request, '你已经签到过这场活动。')
        return redirect('qr-entry', token=token)
    checkin = CheckInRecord.objects.create(
        member=profile,
        event=event,
        checkin_method=CheckInRecord.Method.QR,
        source_qr_code=qr_code,
        created_by=request.user,
    )
    log_action(request.user, 'qr_checkin', 'Event', event.id, f'checkin_id={checkin.id}')
    messages.success(request, '扫码签到成功。')
    return redirect('qr-entry', token=token)

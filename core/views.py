import base64
import logging
import math
import queue
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import qrcode

from .forms import (
    AdminCreateForm,
    AdminUpdateForm,
    EventApplicationForm,
    EventForm,
    EventReviewForm,
    LoginForm,
    ManualCheckInForm,
    MemberProfileForm,
    MemberRegistrationForm,
    PasswordChangeConfirmForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    SystemSettingsForm,
)
from .models import AdminProfile, AuditLog, CheckInRecord, Event, EventQRCode, MemberProfile, SystemSetting, User
from .services import (
    invalidate_password_change_codes,
    invalidate_password_reset_codes,
    issue_password_change_code,
    issue_password_reset_code,
    send_password_change_code_email,
    send_password_reset_code_email,
    verify_password_change_code,
    verify_password_reset_code,
)


QR_CODE_REFRESH_SECONDS = 10
QR_LOGIN_RESUME_MAX_AGE_SECONDS = 120
QR_LOGIN_RESUME_SALT = 'event-qr-login-resume'

logger = logging.getLogger(__name__)

# QR 预生成队列：为每个 event 维护一个内存队列，保证队列中始终有 >= 2 个待选二维码。
# 使用方式：
#   1. 管理员点击"生成签到入口"时初始化队列并预生成二维码
#   2. 前端每 10 秒轮询时从队列直接取出，消除生成延迟
#   3. 停止签到 / 删除活动时销毁队列
_qr_queues = {}      # event_id -> queue.Queue[{qr_code, image, entry_url}]
_qr_queue_locks = {} # event_id -> threading.Lock()
_qr_url_prefixes = {} # event_id -> "http(s)://host"


def _generate_one_qr(event, operator, request, expires_at, url_prefix):
    """生成单个二维码并返回预构建数据（数据库记录 + 图片 data URI + URL）。"""
    qr_code = EventQRCode.objects.create(
        event=event,
        expires_at=expires_at,
        created_by=operator,
    )
    entry_path = qr_code.get_entry_path()
    qr_code.url = f'{url_prefix}{entry_path}'
    qr_code.save(update_fields=['url'])
    image = build_qr_data_uri(qr_code.url)
    return {
        'qr_code': qr_code,
        'image': image,
        'entry_url': qr_code.url,
    }


def _refill_queue(event_id, capacity=2):
    """在后台线程中将队列补充到 capacity 个。"""
    lock = _qr_queue_locks.get(event_id)
    if lock is None:
        return
    with lock:
        q = _qr_queues.get(event_id)
        if q is None:
            return
        url_prefix = _qr_url_prefixes.get(event_id, '')
        while q.qsize() < capacity:
            try:
                from .models import Event as _Event
                event_obj = _Event.objects.filter(pk=event_id).first()
                if event_obj is None or event_obj.status in {
                    _Event.Status.CANCELED,
                    _Event.Status.CHECKIN_CLOSED,
                }:
                    break
                active_qr = event_obj.qr_codes.filter(is_active=True).first()
                if active_qr is None:
                    break
                expires_at = active_qr.expires_at
                operator = active_qr.created_by
                data = _generate_one_qr(event_obj, operator, None, expires_at, url_prefix)
                q.put(data)
            except Exception:
                logger.exception('Failed to pre-generate QR code for event %s', event_id)
                break


def _refill_async(event_id, capacity=2):
    """启动后台线程补充队列。"""
    t = threading.Thread(target=_refill_queue, args=(event_id, capacity), daemon=True)
    t.start()


def init_event_qr_queue(event, request, expires_at, capacity=2):
    """初始化事件的二维码预生成队列并立即预填充。"""
    event_id = event.id
    if event_id in _qr_queues:
        return
    url_prefix = request.build_absolute_uri('/')
    _qr_url_prefixes[event_id] = url_prefix
    q = queue.Queue(maxsize=capacity + 2)
    _qr_queues[event_id] = q
    _qr_queue_locks[event_id] = threading.Lock()
    for _ in range(capacity):
        try:
            data = _generate_one_qr(event, request.user, request, expires_at, url_prefix)
            q.put(data)
        except Exception:
            logger.exception('Failed to init QR queue for event %s', event_id)
    if q.qsize() < capacity:
        _refill_async(event_id, capacity)


def clear_event_qr_queue(event_id):
    """清空并移除事件的二维码预生成队列。"""
    q = _qr_queues.pop(event_id, None)
    _qr_queue_locks.pop(event_id, None)
    _qr_url_prefixes.pop(event_id, None)
    if q is not None:
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break


def pop_event_qr_queue(event_id):
    """从队列中取出下一个预生成的二维码数据，队列不足时异步补充。"""
    q = _qr_queues.get(event_id)
    if q is None:
        return None
    try:
        data = q.get_nowait()
    except queue.Empty:
        return None
    if q.qsize() < 2:
        _refill_async(event_id)
    return data


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


def get_star_window():
    window_days = get_star_window_days()
    return window_days, timezone.now() - timedelta(days=window_days)


def get_star_level(recent_checkin_count):
    if recent_checkin_count >= 4:
        return {
            'slug': 'radiant',
            'label': 'Radiant',
            'title': '高光核心',
            'next_target': None,
            'tone': 'live',
        }
    if recent_checkin_count >= 2:
        return {
            'slug': 'pulse',
            'label': 'Pulse',
            'title': '稳定发光',
            'next_target': 4,
            'tone': 'live',
        }
    if recent_checkin_count >= 1:
        return {
            'slug': 'spark',
            'label': 'Spark',
            'title': '初燃新星',
            'next_target': 2,
            'tone': 'warn',
        }
    return {
        'slug': 'dormant',
        'label': 'Dormant',
        'title': '等待点亮',
        'next_target': 1,
        'tone': 'dim',
    }


def build_member_star_snapshot(profile, window_days=None, window_start=None):
    if window_days is None or window_start is None:
        window_days, window_start = get_star_window()
    now = timezone.now()
    valid_checkins = profile.checkins.filter(status=CheckInRecord.Status.VALID).select_related('event')
    recent_checkins = valid_checkins.filter(checkin_time__gte=window_start).order_by('-checkin_time')
    recent_checkin_count = recent_checkins.count()
    level = get_star_level(recent_checkin_count)
    last_valid_checkin = valid_checkins.order_by('-checkin_time').first()
    latest_recent_checkin = recent_checkins.first()
    expires_at = (
        latest_recent_checkin.checkin_time + timedelta(days=window_days)
        if latest_recent_checkin
        else None
    )
    days_remaining = None
    if expires_at:
        seconds_remaining = max(0, (expires_at - now).total_seconds())
        days_remaining = math.ceil(seconds_remaining / 86400)
    if recent_checkin_count == 0:
        next_milestone = f'最近 {window_days} 天内完成 1 次有效签到即可点亮 ACM Star。'
    elif level['next_target']:
        next_milestone = (
            f"再参与 {max(level['next_target'] - recent_checkin_count, 0)} 次活动，"
            f"即可升级到下一档 Star Level。"
        )
    else:
        next_milestone = '你已经处于最高等级，继续保持近期参与节奏即可。'
    return {
        'lit': recent_checkin_count > 0,
        'recent_checkin_count': recent_checkin_count,
        'recent_checkins': recent_checkins[:5],
        'last_valid_checkin': last_valid_checkin,
        'latest_recent_checkin': latest_recent_checkin,
        'expires_at': expires_at,
        'days_remaining': days_remaining,
        'level': level,
        'progress_percent': min(recent_checkin_count / 4, 1) * 100,
        'next_milestone': next_milestone,
    }


def get_star_holders_queryset(window_start):
    return (
        MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
        .annotate(
            recent_valid_checkins=Count(
                'checkins',
                filter=Q(
                    checkins__status=CheckInRecord.Status.VALID,
                    checkins__checkin_time__gte=window_start,
                ),
            ),
            last_valid_checkin=Max(
                'checkins__checkin_time',
                filter=Q(
                    checkins__status=CheckInRecord.Status.VALID,
                    checkins__checkin_time__gte=window_start,
                ),
            ),
        )
        .filter(recent_valid_checkins__gt=0)
        .select_related('user')
        .order_by('-recent_valid_checkins', '-last_valid_checkin', 'real_name')
    )


def build_qr_data_uri(content):
    qr_image = qrcode.make(content)
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def deactivate_event_qr_codes(event, deactivated_at=None, exclude_id=None):
    deactivated_at = deactivated_at or timezone.now()
    qs = event.qr_codes.filter(is_active=True)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    qs.update(is_active=False, deactivated_at=deactivated_at)


def issue_event_qr_code(event, operator, request, expires_at):
    qr_code = EventQRCode.objects.create(
        event=event,
        expires_at=expires_at,
        created_by=operator,
    )
    qr_code.url = request.build_absolute_uri(qr_code.get_entry_path())
    qr_code.save(update_fields=['url'])
    return qr_code


def get_current_event_qr_code(event, operator, request):
    qr_code = event.qr_codes.filter(is_active=True).first()
    if not qr_code:
        return None

    now = timezone.now()
    if not qr_code.is_valid_at(now):
        deactivate_event_qr_codes(event, deactivated_at=now)
        return None

    if now >= qr_code.get_refresh_deadline(QR_CODE_REFRESH_SECONDS):
        # 优先从预生成队列中取出，避免实时生成的延迟
        data = pop_event_qr_queue(event.id)
        if data is not None:
            deactivate_event_qr_codes(event, deactivated_at=now, exclude_id=data['qr_code'].id)
            return data['qr_code']
        deactivate_event_qr_codes(event, deactivated_at=now)
        return issue_event_qr_code(event, operator or qr_code.created_by, request, qr_code.expires_at)

    if not qr_code.url:
        qr_code.url = request.build_absolute_uri(qr_code.get_entry_path())
        qr_code.save(update_fields=['url'])
    return qr_code


def build_qr_resume_token(qr_code, scanned_at=None):
    scanned_at = scanned_at or timezone.now()
    return signing.dumps(
        {
            'qr_code_id': qr_code.id,
            'scanned_at': scanned_at.timestamp(),
        },
        salt=QR_LOGIN_RESUME_SALT,
    )


def resolve_qr_resume_token(resume_token):
    try:
        payload = signing.loads(
            resume_token,
            salt=QR_LOGIN_RESUME_SALT,
            max_age=QR_LOGIN_RESUME_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return None, '本次扫码确认已失效，请重新扫描最新二维码。'

    qr_code = EventQRCode.objects.filter(pk=payload.get('qr_code_id')).select_related('event').first()
    if qr_code is None:
        return None, '本次扫码确认已失效，请重新扫描最新二维码。'

    scanned_at = datetime.fromtimestamp(payload['scanned_at'], tz=dt_timezone.utc)
    if not qr_code.is_valid_at(scanned_at):
        return None, '当前二维码已经轮换，请重新扫描最新二维码。'
    return qr_code, None


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


def can_review_event_application(user, event):
    return require_management(user) and event.is_member_application()


def can_edit_event(user):
    return require_management(user)


def build_checkin_manager_choices(queryset):
    return [
        {
            'id': user.id,
            'username': user.username,
            'real_name': user.member_profile.real_name,
            'student_id': user.member_profile.student_id,
            'email': user.member_profile.email or user.email,
            'major': user.member_profile.major,
        }
        for user in queryset
    ]


def login_view(request):
    if request.user.is_authenticated:
        return role_redirect(request.user)
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(request.GET.get('next') or request.POST.get('next') or reverse('home'))
    return render(request, 'core/login.html', {'form': form, 'next': request.GET.get('next', '')})


def register_view(request):
    if request.user.is_authenticated:
        return role_redirect(request.user)
    form = MemberRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        log_action(user, 'register_member', 'MemberProfile', user.member_profile.id, f'user={user.username}')
        messages.success(request, '注册成功，欢迎加入 One BNBU-ACM。')
        return redirect('member-dashboard')
    return render(request, 'core/register.html', {'form': form})


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return role_redirect(request.user)
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            _, raw_code = issue_password_reset_code(form.user, form.cleaned_data['school_email'])
            send_password_reset_code_email(form.user, form.cleaned_data['school_email'], raw_code)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            log_action(
                form.user,
                'send_password_reset_code',
                'User',
                form.user.id,
                f'email={form.cleaned_data["school_email"]}',
            )
            messages.success(request, '验证码已发送到你的学校邮箱，请查收后完成密码重置。')
            query = urlencode(
                {
                    'username': form.cleaned_data['username'],
                    'email': form.cleaned_data['school_email'],
                }
            )
            return redirect(f'{reverse("password-reset-confirm")}?{query}')
    return render(request, 'core/password_reset_request.html', {'form': form})


def password_reset_confirm_view(request):
    if request.user.is_authenticated:
        return role_redirect(request.user)
    initial = {
        'username': request.GET.get('username', ''),
        'school_email': request.GET.get('email', ''),
    }
    form = PasswordResetConfirmForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            verification = verify_password_reset_code(
                form.user,
                form.cleaned_data['school_email'],
                form.cleaned_data['code'],
            )
        except ValidationError as exc:
            form.add_error('code', exc)
        else:
            form.user.set_password(form.cleaned_data['password1'])
            form.user.save(update_fields=['password'])
            verification.mark_used()
            invalidate_password_reset_codes(
                form.user,
                form.cleaned_data['school_email'],
                exclude_id=verification.id,
            )
            log_action(
                form.user,
                'password_reset_by_email_code',
                'User',
                form.user.id,
                f'email={form.cleaned_data["school_email"]}',
            )
            messages.success(request, '密码已重置，请使用新密码登录。')
            return redirect('login')
    return render(request, 'core/password_reset_confirm.html', {'form': form})


@login_required
def password_change_request_view(request):
    email = request.user.email.strip().lower()
    if request.method == 'POST':
        if not email:
            messages.error(request, '当前账号尚未绑定邮箱，暂时无法通过验证码修改密码。')
            return redirect('password-change-request')
        try:
            _, raw_code = issue_password_change_code(request.user, email)
            send_password_change_code_email(request.user, email, raw_code)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, 'messages') else str(exc))
        else:
            log_action(
                request.user,
                'send_password_change_code',
                'User',
                request.user.id,
                f'email={email}',
            )
            messages.success(request, '验证码已发送到当前绑定邮箱，请查收后继续修改密码。')
            return redirect('password-change-confirm')
    return render(
        request,
        'core/password_change_request.html',
        {
            'account_email': email,
            'has_email': bool(email),
        },
    )


@login_required
def password_change_confirm_view(request):
    email = request.user.email.strip().lower()
    if not email:
        messages.error(request, '当前账号尚未绑定邮箱，暂时无法通过验证码修改密码。')
        return redirect('password-change-request')
    form = PasswordChangeConfirmForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            verification = verify_password_change_code(
                request.user,
                email,
                form.cleaned_data['code'],
            )
        except ValidationError as exc:
            form.add_error('code', exc)
        else:
            request.user.set_password(form.cleaned_data['password1'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)
            verification.mark_used()
            invalidate_password_change_codes(
                request.user,
                email,
                exclude_id=verification.id,
            )
            log_action(
                request.user,
                'password_change_by_email_code',
                'User',
                request.user.id,
                f'email={email}',
            )
            messages.success(request, '密码已更新，当前登录状态已保持。')
            return redirect('home')
    return render(
        request,
        'core/password_change_confirm.html',
        {
            'form': form,
            'account_email': email,
        },
    )


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
    window_days, window_start = get_star_window()
    star_snapshot = build_member_star_snapshot(profile, window_days=window_days, window_start=window_start)
    recent_events = (
        Event.objects.filter(review_status=Event.ReviewStatus.APPROVED, status=Event.Status.PUBLISHED)
        .order_by('start_time')[:5]
    )
    context = {
        'profile': profile,
        'star_lit': star_snapshot['lit'],
        'star_snapshot': star_snapshot,
        'window_days': window_days,
        'recent_checkins': star_snapshot['recent_checkins'],
        'upcoming_events': recent_events,
        'checkin_count': profile.checkins.filter(status=CheckInRecord.Status.VALID).count(),
        'managed_event_total': Event.objects.filter(
            applicant=request.user,
            review_status=Event.ReviewStatus.APPROVED,
        ).count(),
    }
    return render(request, 'core/member/dashboard.html', context)


@login_required
def member_event_list(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    events = (
        Event.objects.filter(review_status=Event.ReviewStatus.APPROVED)
        .exclude(status=Event.Status.CANCELED)
        .order_by('start_time')
    )
    my_applications = (
        Event.objects.filter(applicant=request.user)
        .annotate(checkin_total=Count('checkins'))
        .order_by('-created_at')
    )
    return render(
        request,
        'core/member/event_list.html',
        {
            'events': events,
            'my_applications': my_applications,
        },
    )


@login_required
def member_event_apply(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    form = EventApplicationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.applicant = request.user
        event.review_status = Event.ReviewStatus.PENDING
        event.status = Event.Status.DRAFT
        event.save()
        log_action(request.user, 'submit_event_application', 'Event', event.id)
        messages.success(request, '活动申请已提交，等待管理员审核。')
        return redirect('member-event-list')
    return render(
        request,
        'core/member/event_application_form.html',
        {
            'form': form,
            'page_title': '申请活动',
        },
    )


@login_required
def member_event_detail(request, event_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    event = get_object_or_404(Event.objects.filter(review_status=Event.ReviewStatus.APPROVED), pk=event_id)
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
    event = get_object_or_404(Event.objects.filter(review_status=Event.ReviewStatus.APPROVED), pk=event_id)
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
        profile = form.save()
        if request.user.email != profile.email:
            request.user.email = profile.email
            request.user.save(update_fields=['email'])
        log_action(request.user, 'update_member_profile', 'MemberProfile', profile.id)
        messages.success(request, '个人资料已更新。')
        return redirect('member-profile')
    return render(request, 'core/member/profile.html', {'form': form, 'profile': profile})


@login_required
def member_star_center(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    window_days, window_start = get_star_window()
    star_snapshot = build_member_star_snapshot(profile, window_days=window_days, window_start=window_start)
    return render(
        request,
        'core/member/star_center.html',
        {
            'profile': profile,
            'window_days': window_days,
            'star_snapshot': star_snapshot,
        },
    )


@login_required
def management_dashboard(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_days, window_start = get_star_window()
    active_members = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE).count()
    star_holders = get_star_holders_queryset(window_start)
    lit_members = star_holders.count()
    context = {
        'recent_event_count': Event.objects.filter(start_time__gte=now).count(),
        'pending_application_count': Event.objects.filter(review_status=Event.ReviewStatus.PENDING).count(),
        'today_checkin_count': CheckInRecord.objects.filter(
            status=CheckInRecord.Status.VALID,
            checkin_time__gte=today_start,
        ).count(),
        'lit_members': lit_members,
        'active_members': active_members,
        'star_ratio': round((lit_members / active_members) * 100) if active_members else 0,
        'top_star_holders': star_holders[:3],
        'window_days': window_days,
    }
    return render(request, 'core/management/dashboard.html', context)


@login_required
def star_analytics(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    now = timezone.now()
    window_days, window_start = get_star_window()
    star_holders = list(get_star_holders_queryset(window_start))
    active_members = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
    active_members_total = active_members.count()
    for holder in star_holders:
        holder.star_level = get_star_level(holder.recent_valid_checkins)
    level_counts = {'spark': 0, 'pulse': 0, 'radiant': 0}
    major_totals = {}
    class_totals = {}
    for holder in star_holders:
        if holder.star_level['slug'] in level_counts:
            level_counts[holder.star_level['slug']] += 1
        major_key = holder.major or '未填写专业'
        class_key = holder.class_name or '未填写班级'
        major_totals[major_key] = major_totals.get(major_key, 0) + 1
        class_totals[class_key] = class_totals.get(class_key, 0) + 1
    major_breakdown = [
        {'major': major, 'total': total}
        for major, total in sorted(major_totals.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    class_breakdown = [
        {'class_name': class_name, 'total': total}
        for class_name, total in sorted(class_totals.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    context = {
        'window_days': window_days,
        'star_holders': star_holders,
        'star_holders_total': len(star_holders),
        'active_members_total': active_members_total,
        'star_ratio': round((len(star_holders) / active_members_total) * 100) if active_members_total else 0,
        'newly_active_total': sum(1 for holder in star_holders if holder.last_valid_checkin and holder.last_valid_checkin >= now - timedelta(days=7)),
        'spark_total': level_counts['spark'],
        'pulse_total': level_counts['pulse'],
        'radiant_total': level_counts['radiant'],
        'major_breakdown': major_breakdown,
        'class_breakdown': class_breakdown,
    }
    return render(request, 'core/management/star_analytics.html', context)


@login_required
def event_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    events = (
        Event.objects.select_related('applicant', 'reviewed_by').prefetch_related('checkin_managers__member_profile')
        .annotate(checkin_total=Count('checkins'))
        .order_by('review_status', 'start_time')
    )
    return render(
        request,
        'core/management/event_list.html',
        {
            'events': events,
            'pending_application_total': sum(1 for event in events if event.review_status == Event.ReviewStatus.PENDING),
        },
    )


@login_required
def event_create(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    form = EventForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.review_status = Event.ReviewStatus.APPROVED
        event.reviewed_by = request.user
        event.reviewed_at = timezone.now()
        if event.status == Event.Status.PUBLISHED and not event.published_at:
            event.published_at = timezone.now()
        event.save()
        form.save_m2m()
        log_action(request.user, 'create_event', 'Event', event.id)
        messages.success(request, '活动已创建。')
        return redirect('event-detail-manage', event_id=event.id)
    return render(
        request,
        'core/management/event_form.html',
        {
            'form': form,
            'page_title': '创建活动',
            'checkin_manager_choices': build_checkin_manager_choices(form.fields['checkin_managers'].queryset),
            'selected_checkin_manager_ids': form['checkin_managers'].value() or [],
        },
    )


@login_required
def event_edit(request, event_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    event = get_object_or_404(Event.objects.prefetch_related('checkin_managers__member_profile'), pk=event_id)
    form = EventForm(request.POST or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        if event.status == Event.Status.PUBLISHED and not event.published_at:
            event.published_at = timezone.now()
        event.save()
        form.save_m2m()
        log_action(request.user, 'edit_event', 'Event', event.id)
        messages.success(request, '活动已更新。')
        return redirect('event-detail-manage', event_id=event.id)
    return render(
        request,
        'core/management/event_form.html',
        {
            'form': form,
            'page_title': '编辑活动',
            'checkin_manager_choices': build_checkin_manager_choices(form.fields['checkin_managers'].queryset),
            'selected_checkin_manager_ids': form['checkin_managers'].value() or [],
        },
    )


@login_required
def event_detail_manage(request, event_id):
    event = get_object_or_404(
        Event.objects.select_related('applicant', 'reviewed_by', 'created_by').prefetch_related('checkin_managers__member_profile'),
        pk=event_id,
    )
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可访问。')
    qr_code = get_current_event_qr_code(event, request.user, request)
    all_checkins = (
        event.checkins.select_related('member', 'created_by')
        .order_by('-checkin_time', '-created_at')
    )
    valid_checkins = all_checkins.filter(status=CheckInRecord.Status.VALID)
    qr_code_image = build_qr_data_uri(qr_code.url) if qr_code and qr_code.url else None
    can_full_edit = can_edit_event(request.user)
    can_review = can_review_event_application(request.user, event) and event.review_status != Event.ReviewStatus.APPROVED
    return render(
        request,
        'core/management/event_detail.html',
        {
            'event': event,
            'qr_code': qr_code,
            'qr_code_image': qr_code_image,
            'qr_refresh_seconds': QR_CODE_REFRESH_SECONDS,
            'qr_refresh_at': qr_code.get_refresh_deadline(QR_CODE_REFRESH_SECONDS) if qr_code else None,
            'all_checkins': all_checkins,
            'checkin_total': valid_checkins.count(),
            'revoked_checkin_total': all_checkins.filter(status=CheckInRecord.Status.REVOKED).count(),
            'can_edit_event': can_full_edit,
            'can_publish_event': can_full_edit and event.review_status == Event.ReviewStatus.APPROVED,
            'can_delete_event': can_full_edit,
            'can_manage_checkin': event.can_manage_checkin(request.user),
            'can_review_event': can_review,
            'review_form': EventReviewForm(initial={'review_note': event.review_note}),
            'manual_checkin_form': ManualCheckInForm(event),
        },
    )


@login_required
def event_publish(request, event_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if event.review_status != Event.ReviewStatus.APPROVED:
        messages.error(request, '请先完成活动审核，再发布活动。')
        return redirect('event-detail-manage', event_id=event.id)
    event.status = Event.Status.PUBLISHED
    event.published_at = timezone.now()
    event.save(update_fields=['status', 'published_at', 'updated_at'])
    log_action(request.user, 'publish_event', 'Event', event.id)
    messages.success(request, '活动已发布。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def event_delete(request, event_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if event.status == Event.Status.CANCELED:
        messages.warning(request, '该活动已经删除过了。')
        return redirect('event-list-manage')
    event.status = Event.Status.CANCELED
    deactivate_event_qr_codes(event)
    clear_event_qr_queue(event_id)
    event.save(update_fields=['status', 'updated_at'])
    log_action(request.user, 'delete_event', 'Event', event.id)
    messages.success(request, '活动已删除，并标记为已作废。')
    return redirect('event-list-manage')


@login_required
def event_close_checkin(request, event_id):
    if request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可操作。')
    event.status = Event.Status.CHECKIN_CLOSED
    deactivate_event_qr_codes(event)
    clear_event_qr_queue(event_id)
    event.save(update_fields=['status', 'updated_at'])
    log_action(request.user, 'close_checkin', 'Event', event.id)
    messages.success(request, '签到已关闭。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def generate_qr_entry(request, event_id):
    if request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可操作。')
    deactivate_event_qr_codes(event)
    clear_event_qr_queue(event_id)
    minutes = int(SystemSetting.get_value('qr_code_expire_minutes', '120'))
    expires_at = timezone.now() + timedelta(minutes=minutes)
    qr_code = issue_event_qr_code(event, request.user, request, expires_at)
    init_event_qr_queue(event, request, expires_at, capacity=2)
    log_action(request.user, 'generate_qr', 'EventQRCode', qr_code.id, f'event={event.id}')
    messages.success(request, f'活动签到入口已生成，二维码将每 {QR_CODE_REFRESH_SECONDS} 秒自动刷新一次。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def event_qr_status(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可操作。')

    current_qr = event.qr_codes.filter(is_active=True).first()
    now = timezone.now()

    # 当前二维码仍在有效期内且未到刷新时间，直接返回，不消耗队列
    if current_qr and current_qr.is_valid_at(now) and now < current_qr.get_refresh_deadline(QR_CODE_REFRESH_SECONDS):
        return JsonResponse(
            {
                'active': True,
                'entry_url': current_qr.url,
                'image': build_qr_data_uri(current_qr.url),
                'token_preview': f'{current_qr.token[:16]}...',
                'refresh_interval_seconds': QR_CODE_REFRESH_SECONDS,
                'refresh_at': current_qr.get_refresh_deadline(QR_CODE_REFRESH_SECONDS).isoformat(),
                'expires_at': current_qr.expires_at.isoformat() if current_qr.expires_at else None,
            }
        )

    # 到达刷新时间，优先从预生成队列中取出下一个二维码，消除生成延迟
    data = pop_event_qr_queue(event_id)
    if data is not None:
        deactivate_event_qr_codes(event, exclude_id=data['qr_code'].id)
        qr_code = data['qr_code']
        return JsonResponse(
            {
                'active': True,
                'entry_url': data['entry_url'],
                'image': data['image'],
                'token_preview': f'{qr_code.token[:16]}...',
                'refresh_interval_seconds': QR_CODE_REFRESH_SECONDS,
                'refresh_at': qr_code.get_refresh_deadline(QR_CODE_REFRESH_SECONDS).isoformat(),
                'expires_at': qr_code.expires_at.isoformat() if qr_code.expires_at else None,
            }
        )

    qr_code = get_current_event_qr_code(event, request.user, request)
    if qr_code is None:
        return JsonResponse(
            {
                'active': False,
                'refresh_interval_seconds': QR_CODE_REFRESH_SECONDS,
            }
        )

    return JsonResponse(
        {
            'active': True,
            'entry_url': qr_code.url,
            'image': build_qr_data_uri(qr_code.url),
            'token_preview': f'{qr_code.token[:16]}...',
            'refresh_interval_seconds': QR_CODE_REFRESH_SECONDS,
            'refresh_at': qr_code.get_refresh_deadline(QR_CODE_REFRESH_SECONDS).isoformat(),
            'expires_at': qr_code.expires_at.isoformat() if qr_code.expires_at else None,
        }
    )


@login_required
def event_manual_checkin(request, event_id):
    if request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可操作。')
    form = ManualCheckInForm(event, request.POST)
    if not form.is_valid():
        messages.error(request, '补签失败，请检查队员信息。')
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect('event-detail-manage', event_id=event.id)
    checkin = CheckInRecord.objects.create(
        member=form.member_profile,
        event=event,
        checkin_method=CheckInRecord.Method.MANUAL,
        remark=form.cleaned_data['remark'],
        created_by=request.user,
    )
    log_action(
        request.user,
        'manual_checkin',
        'Event',
        event.id,
        f'checkin_id={checkin.id};member={form.member_profile.student_id}',
    )
    messages.success(request, '已完成手动补签。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def event_revoke_checkin(request, event_id, checkin_id):
    if request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_manage_checkin(request.user):
        return HttpResponseForbidden('仅管理员、该活动申请人或已指定的签到管理员可操作。')
    checkin = get_object_or_404(
        CheckInRecord.objects.select_related('member'),
        pk=checkin_id,
        event=event,
    )
    if checkin.status == CheckInRecord.Status.REVOKED:
        messages.warning(request, '这条签到已经撤销过了。')
        return redirect('event-detail-manage', event_id=event.id)
    checkin.status = CheckInRecord.Status.REVOKED
    if not checkin.remark:
        checkin.remark = '由签到管理员撤销。'
    checkin.save(update_fields=['status', 'remark', 'updated_at'])
    log_action(
        request.user,
        'revoke_checkin',
        'CheckInRecord',
        checkin.id,
        f'event={event.id};member={checkin.member.student_id}',
    )
    messages.success(request, '签到记录已撤销。')
    return redirect('event-detail-manage', event_id=event.id)


@login_required
def event_review(request, event_id):
    if request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    event = get_object_or_404(Event, pk=event_id)
    if not can_review_event_application(request.user, event):
        return HttpResponseForbidden('仅管理员可审核成员活动申请。')
    form = EventReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, '审核说明提交失败，请重试。')
        return redirect('event-detail-manage', event_id=event.id)

    decision = request.POST.get('decision')
    if decision not in {'approve', 'reject'}:
        messages.error(request, '无效的审核操作。')
        return redirect('event-detail-manage', event_id=event.id)

    event.review_note = form.cleaned_data['review_note']
    event.reviewed_by = request.user
    event.reviewed_at = timezone.now()
    if decision == 'approve':
        event.review_status = Event.ReviewStatus.APPROVED
        if event.status == Event.Status.DRAFT:
            event.status = Event.Status.PUBLISHED
        if not event.published_at:
            event.published_at = timezone.now()
        event.save(
            update_fields=[
                'review_note',
                'reviewed_by',
                'reviewed_at',
                'review_status',
                'status',
                'published_at',
                'updated_at',
            ]
        )
        if event.applicant_id and not event.checkin_managers.filter(id=event.applicant_id).exists():
            event.checkin_managers.add(event.applicant_id)
        log_action(request.user, 'approve_event_application', 'Event', event.id)
        messages.success(request, '活动申请已通过，申请人现在可以管理该活动的签到。')
    else:
        event.review_status = Event.ReviewStatus.REJECTED
        if event.status == Event.Status.PUBLISHED:
            event.status = Event.Status.DRAFT
        event.save(
            update_fields=[
                'review_note',
                'reviewed_by',
                'reviewed_at',
                'review_status',
                'status',
                'updated_at',
            ]
        )
        log_action(request.user, 'reject_event_application', 'Event', event.id)
        messages.success(request, '活动申请已驳回。')
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


def render_qr_entry_page(request, qr_code, post_url):
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
            'post_url': post_url,
            'qr_refresh_seconds': QR_CODE_REFRESH_SECONDS,
            'qr_resume_window_seconds': QR_LOGIN_RESUME_MAX_AGE_SECONDS,
        },
    )


def complete_qr_checkin(request, qr_code, redirect_url):
    event = qr_code.event
    profile = get_object_or_404(MemberProfile, user=request.user)
    if not event.is_checkin_open():
        messages.error(request, '当前活动未开放签到。')
        return redirect(redirect_url)
    if CheckInRecord.objects.filter(
        member=profile,
        event=event,
        status=CheckInRecord.Status.VALID,
    ).exists():
        messages.warning(request, '你已经签到过这场活动。')
        return redirect(redirect_url)
    checkin = CheckInRecord.objects.create(
        member=profile,
        event=event,
        checkin_method=CheckInRecord.Method.QR,
        source_qr_code=qr_code,
        created_by=request.user,
    )
    log_action(request.user, 'qr_checkin', 'Event', event.id, f'checkin_id={checkin.id}')
    messages.success(request, '扫码签到成功。')
    return redirect(redirect_url)


def qr_entry(request, token):
    qr_code = get_object_or_404(EventQRCode.objects.select_related('event'), token=token)
    if not qr_code.is_valid():
        return render(request, 'core/qr_invalid.html', {'reason': '二维码已失效或被停用。'})
    resume_token = build_qr_resume_token(qr_code)
    resume_url = reverse('qr-entry-resume', args=[resume_token])
    if not request.user.is_authenticated:
        return redirect_to_login(resume_url, login_url=reverse('login'))
    if not require_member(request.user):
        return render(request, 'core/qr_invalid.html', {'reason': '只有队员账号可以签到。'})
    return render_qr_entry_page(
        request,
        qr_code,
        reverse('qr-resume-checkin', args=[resume_token]),
    )


@login_required
def qr_checkin(request, token):
    if request.method != 'POST' or not require_member(request.user):
        return HttpResponseForbidden('无权操作。')
    qr_code = get_object_or_404(EventQRCode, token=token)
    if not qr_code.is_valid():
        return render(request, 'core/qr_invalid.html', {'reason': '二维码已失效或被停用。'})
    return complete_qr_checkin(request, qr_code, reverse('qr-entry', args=[token]))


def qr_entry_resume(request, resume_token):
    qr_code, reason = resolve_qr_resume_token(resume_token)
    if reason:
        return render(request, 'core/qr_invalid.html', {'reason': reason})
    if not request.user.is_authenticated:
        return redirect_to_login(reverse('qr-entry-resume', args=[resume_token]), login_url=reverse('login'))
    if not require_member(request.user):
        return render(request, 'core/qr_invalid.html', {'reason': '只有队员账号可以签到。'})
    return render_qr_entry_page(
        request,
        qr_code,
        reverse('qr-resume-checkin', args=[resume_token]),
    )


@login_required
def qr_resume_checkin(request, resume_token):
    if request.method != 'POST' or not require_member(request.user):
        return HttpResponseForbidden('无权操作。')
    qr_code, reason = resolve_qr_resume_token(resume_token)
    if reason:
        return render(request, 'core/qr_invalid.html', {'reason': reason})
    return complete_qr_checkin(
        request,
        qr_code,
        reverse('qr-entry-resume', args=[resume_token]),
    )

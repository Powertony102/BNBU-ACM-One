import base64
import json
import logging
import math
import queue
import threading
from datetime import datetime, time, timedelta, timezone as dt_timezone
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
import qrcode

from .competition import (
    AWARD_BONUS_RULES_SETTING_KEY,
    BASE_PARTICIPATION_SETTING_KEY,
    COMPETITION_LEVEL_THRESHOLDS_SETTING_KEY,
    CONTEST_LEVEL_RULES_SETTING_KEY,
    build_competition_level_ranges,
    build_integrity_sanction_snapshot,
    build_competition_ladder_queryset,
    build_member_competition_snapshot,
    get_award_bonus_rules,
    get_base_participation_score,
    get_contest_level_weight_rules,
    get_competition_display_color,
    get_competition_level,
    get_default_contest_weight,
    sync_event_series_completion,
    sync_member_competition_profile,
    sync_members_competition_profiles,
)
from .forms import (
    AdminCreateForm,
    AdminUpdateForm,
    ContestForm,
    ContestResultForm,
    ContestSubmissionForm,
    ContestSubmissionReviewForm,
    ContestTeamForm,
    EventApplicationForm,
    EventForm,
    EventReviewForm,
    EventSeriesForm,
    LoginForm,
    ManualCheckInForm,
    MemberIntegritySanctionForm,
    MemberTeamSubmissionForm,
    MemberTeamSubmissionReviewForm,
    MemberProfileForm,
    MemberRegistrationForm,
    PasswordChangeConfirmForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RatingRulesForm,
    SystemSettingsForm,
)
from .models import (
    AdminProfile,
    AuditLog,
    CheckInRecord,
    Contest,
    ContestResult,
    ContestSubmission,
    ContestTeam,
    Event,
    EventQRCode,
    EventSeries,
    EventSeriesCompletion,
    MemberTeam,
    MemberTeamSubmission,
    MemberCompetitionProfile,
    MemberIntegritySanction,
    MemberProfile,
    SystemSetting,
    User,
)
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
_qr_expires_at = {}  # event_id -> datetime (缓存过期时间，不依赖数据库活跃码)
_qr_operators = {}   # event_id -> User (缓存操作者，不依赖数据库活跃码)


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
        expires_at = _qr_expires_at.get(event_id)
        operator = _qr_operators.get(event_id)
        if expires_at is None or operator is None:
            return
        while q.qsize() < capacity:
            try:
                from .models import Event as _Event
                event_obj = _Event.objects.filter(pk=event_id).first()
                if event_obj is None or event_obj.status in {
                    _Event.Status.CANCELED,
                    _Event.Status.CHECKIN_CLOSED,
                }:
                    break
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
    _qr_expires_at[event_id] = expires_at
    _qr_operators[event_id] = request.user
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
    _qr_expires_at.pop(event_id, None)
    _qr_operators.pop(event_id, None)
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


def sync_series_completion_for_member(member, series):
    if series is None:
        return
    sync_event_series_completion(member, series)
    sync_member_competition_profile(member)


def sync_series_completions_for_event_members(event, series_ids=None):
    candidate_series_ids = set(series_ids or [])
    if event.series_id:
        candidate_series_ids.add(event.series_id)
    candidate_series_ids.discard(None)
    if not candidate_series_ids:
        return
    members = (
        MemberProfile.objects.filter(
            checkins__event=event,
        )
        .distinct()
    )
    series_map = EventSeries.objects.in_bulk(candidate_series_ids)
    for member in members:
        for series in series_map.values():
            sync_series_completion_for_member(member, series)


def sync_series_completions_for_series(series):
    member_ids = set(
        MemberProfile.objects.filter(checkins__event__series=series).values_list('id', flat=True)
    )
    member_ids.update(
        EventSeriesCompletion.objects.filter(series=series).values_list('member_id', flat=True)
    )
    if not member_ids:
        return
    for member in MemberProfile.objects.filter(id__in=member_ids):
        sync_series_completion_for_member(member, series)


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


def get_integrity_restricted_star_level():
    return {
        'slug': 'restricted',
        'label': 'Restricted',
        'title': '诚信处罚期',
        'next_target': None,
        'tone': 'dim',
    }


def build_integrity_sanction_snapshot_from_record(sanction):
    if sanction is None:
        return None
    return {
        'id': sanction.id,
        'reason_type': sanction.reason_type,
        'reason_label': sanction.get_reason_type_display(),
        'member_reason': sanction.member_reason,
        'internal_note': sanction.internal_note,
        'public_notice': sanction.public_notice,
        'starts_at': sanction.starts_at,
        'ends_at': sanction.ends_at,
    }


def build_member_integrity_context_from_snapshot(profile, sanction_snapshot, viewer=None, now=None):
    if sanction_snapshot is None:
        return None
    viewer_user_id = getattr(viewer, 'id', None)
    sanction = dict(sanction_snapshot)
    member_reason_visible = viewer_user_id == profile.user_id and bool(sanction_snapshot['member_reason'])
    sanction['member_reason_visible'] = member_reason_visible
    sanction['display_reason'] = sanction_snapshot['member_reason'] if member_reason_visible else ''
    sanction['days_remaining'] = max(
        0,
        math.ceil((sanction_snapshot['ends_at'] - (now or timezone.now())).total_seconds() / 86400),
    )
    return sanction


def build_member_integrity_context(profile, viewer=None, now=None):
    sanction = build_integrity_sanction_snapshot(profile, now=now)
    return build_member_integrity_context_from_snapshot(profile, sanction, viewer=viewer, now=now)


def choose_member_integrity_sanction(existing, candidate, now):
    if existing is None:
        return candidate
    existing_active = existing.is_active_at(now)
    candidate_active = candidate.is_active_at(now)
    if candidate_active and not existing_active:
        return candidate
    if existing_active and not candidate_active:
        return existing
    if candidate_active and existing_active:
        if (candidate.starts_at, candidate.id) > (existing.starts_at, existing.id):
            return candidate
        return existing
    if (candidate.starts_at, candidate.id) < (existing.starts_at, existing.id):
        return candidate
    return existing


def build_non_expired_integrity_sanction_map(member_ids, now=None):
    now = now or timezone.now()
    sanction_map = {}
    sanctions = (
        MemberIntegritySanction.objects.filter(
            member_id__in=member_ids,
            revoked_at__isnull=True,
            ends_at__gte=now,
        )
        .select_related('created_by', 'revoked_by')
        .order_by('member_id', 'starts_at', 'id')
    )
    for sanction in sanctions:
        sanction_map[sanction.member_id] = choose_member_integrity_sanction(
            sanction_map.get(sanction.member_id),
            sanction,
            now,
        )
    return sanction_map


def get_member_current_or_upcoming_integrity_sanction(member, now=None):
    now = now or timezone.now()
    return build_non_expired_integrity_sanction_map([member.id], now=now).get(member.id)


def get_contest_activity_datetime(contest_date):
    return timezone.make_aware(
        datetime.combine(contest_date, time(hour=12)),
        timezone.get_current_timezone(),
    )


def build_member_star_activity_records(profile, window_start=None):
    checkin_filters = {
        'status': CheckInRecord.Status.VALID,
    }
    contest_filters = {
        'verified': True,
        'contest__status': Contest.Status.PUBLISHED,
        'team__members': profile,
    }
    if window_start is not None:
        checkin_filters['checkin_time__gte'] = window_start
        contest_filters['contest__contest_date__gte'] = window_start.date()
    recent_checkins = list(
        profile.checkins.filter(**checkin_filters)
        .select_related('event')
        .order_by('-checkin_time')
    )
    recent_contest_results = list(
        ContestResult.objects.filter(**contest_filters)
        .select_related('contest', 'team')
        .order_by('-contest__contest_date', '-verified_at', '-id')
        .distinct()
    )
    activities = []
    for checkin in recent_checkins:
        activities.append(
            {
                'kind': 'checkin',
                'occurred_at': checkin.checkin_time,
                'occurred_at_text': timezone.localtime(checkin.checkin_time).strftime('%Y-%m-%d %H:%M'),
                'occurred_at_short_text': timezone.localtime(checkin.checkin_time).strftime('%m-%d'),
                'title': checkin.event.title,
                'meta': checkin.get_checkin_method_display(),
                'badge': '签到',
                'source_label': '活动签到',
                'detail': checkin.event.location,
                'status_label': checkin.get_status_display(),
                'status_tone': 'live',
            }
        )
    for result in recent_contest_results:
        occurred_at = get_contest_activity_datetime(result.contest.contest_date)
        activities.append(
            {
                'kind': 'contest',
                'occurred_at': occurred_at,
                'occurred_at_text': result.contest.contest_date.strftime('%Y-%m-%d'),
                'occurred_at_short_text': result.contest.contest_date.strftime('%m-%d'),
                'title': str(result.contest),
                'meta': result.display_award_label,
                'badge': '赛事',
                'source_label': '赛事参与',
                'detail': result.team.team_name,
                'status_label': '已认证',
                'status_tone': 'warn',
            }
        )
    activities.sort(key=lambda item: item['occurred_at'], reverse=True)
    return activities


def build_member_star_snapshot(profile, window_days=None, window_start=None):
    if window_days is None or window_start is None:
        window_days, window_start = get_star_window()
    now = timezone.now()
    integrity_sanction = build_member_integrity_context(profile, now=now)
    recent_activities = build_member_star_activity_records(profile, window_start)
    recent_activity_count = len(recent_activities)
    level = get_star_level(recent_activity_count)
    latest_recent_activity = recent_activities[0] if recent_activities else None
    expires_at = (
        latest_recent_activity['occurred_at'] + timedelta(days=window_days)
        if latest_recent_activity
        else None
    )
    days_remaining = None
    if expires_at:
        seconds_remaining = max(0, (expires_at - now).total_seconds())
        days_remaining = math.ceil(seconds_remaining / 86400)
    if recent_activity_count == 0:
        next_milestone = f'最近 {window_days} 天内完成 1 次活动签到或赛事参与即可点亮 ACM Star。'
    elif level['next_target']:
        next_milestone = (
            f"再参与 {max(level['next_target'] - recent_activity_count, 0)} 次活动，"
            f"即可升级到下一档 Star Level。"
        )
    else:
        next_milestone = '你已经处于最高等级，继续保持近期参与节奏即可。'
    lit = recent_activity_count > 0
    if integrity_sanction:
        level = get_integrity_restricted_star_level()
        lit = False
        expires_at = integrity_sanction['ends_at']
        days_remaining = integrity_sanction['days_remaining']
        next_milestone = '当前因诚信处罚处于熄灭状态，处罚到期或提前解除后将自动恢复 ACM Star 计算。'
    return {
        'lit': lit,
        'recent_activity_count': recent_activity_count,
        'recent_checkin_count': recent_activity_count,
        'recent_activities': recent_activities[:5],
        'recent_checkins': recent_activities[:5],
        'latest_activity_at': latest_recent_activity['occurred_at'] if latest_recent_activity else None,
        'latest_activity_title': latest_recent_activity['title'] if latest_recent_activity else None,
        'latest_activity_type': latest_recent_activity['source_label'] if latest_recent_activity else None,
        'latest_recent_activity': latest_recent_activity,
        'latest_recent_checkin': latest_recent_activity,
        'expires_at': expires_at,
        'days_remaining': days_remaining,
        'level': level,
        'progress_percent': min(recent_activity_count / 4, 1) * 100,
        'next_milestone': next_milestone,
        'integrity_sanction': integrity_sanction,
    }


def get_star_holders_queryset(window_start):
    window_days = get_star_window_days()
    star_holders = []
    for profile in MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE).select_related('user'):
        snapshot = build_member_star_snapshot(profile, window_days=window_days, window_start=window_start)
        if not snapshot['lit']:
            continue
        profile.recent_activity_count = snapshot['recent_activity_count']
        profile.recent_valid_checkins = snapshot['recent_activity_count']
        profile.latest_activity_at = snapshot['latest_activity_at']
        profile.last_valid_checkin = snapshot['latest_activity_at']
        profile.star_level = snapshot['level']
        star_holders.append(profile)
    star_holders.sort(
        key=lambda holder: (
            holder.recent_activity_count,
            holder.latest_activity_at or datetime.min.replace(tzinfo=dt_timezone.utc),
            holder.real_name,
        ),
        reverse=True,
    )
    return star_holders


def build_qr_data_uri(content):
    qr_image = qrcode.make(content)
    buffer = BytesIO()
    qr_image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def deactivate_event_qr_codes(event, deactivated_at=None):
    deactivated_at = deactivated_at or timezone.now()
    event.qr_codes.filter(is_active=True).update(is_active=False, deactivated_at=deactivated_at)


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
            qr_code.mark_inactive(now)
            new_qr = data['qr_code']
            new_qr.created_at = now
            new_qr.save(update_fields=['created_at'])
            return new_qr
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


def can_review_contest_submission(user, submission):
    return require_management(user)


def can_edit_member_team(user, team):
    if not require_member(user):
        return False
    profile = getattr(user, 'member_profile', None)
    return profile is not None and team.captain_id == profile.id


def can_review_member_team_submission(user, submission):
    return require_management(user)


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


def build_event_series_choices(queryset):
    return [
        {
            'id': series.id,
            'title': series.title,
            'description': series.description,
            'series_type': series.get_series_type_display(),
            'status': series.get_status_display(),
            'expected_event_count': series.expected_event_count,
            'rating_enabled': series.rating_enabled,
            'rating_points': series.rating_points,
            'required_checkins_for_rating': series.required_checkins_for_rating,
            'start_date': series.start_date.isoformat() if series.start_date else '',
            'end_date': series.end_date.isoformat() if series.end_date else '',
        }
        for series in queryset
    ]


def sync_all_competition_profiles():
    sync_members_competition_profiles(MemberProfile.objects.select_related('user').all())


def sync_default_contest_weights():
    default_weights = get_contest_level_weight_rules()
    for level, weight in default_weights.items():
        Contest.objects.filter(level=level, use_default_weight=True).update(weight=weight)


def build_rating_rule_rows(form):
    contest_level_rows = []
    current_weight_rules = get_contest_level_weight_rules()
    for level_value, level_label in Contest.Level.choices:
        contest_level_rows.append(
            {
                'key': level_value,
                'label': level_label,
                'field': form[f'weight_{level_value}'],
                'preview': str(current_weight_rules[level_value]),
            }
        )

    award_rows = []
    current_bonus_rules = get_award_bonus_rules()
    for award_value, award_label in ContestResult.AwardType.choices:
        award_rows.append(
            {
                'key': award_value,
                'label': award_label,
                'field': form[f'bonus_{award_value}'],
                'preview': current_bonus_rules[award_value],
            }
        )

    level_rows = []
    for level in build_competition_level_ranges():
        if level['slug'] == 'unrated':
            continue
        level_rows.append(
            {
                **level,
                'field': form[f'threshold_{level["slug"]}'],
            }
        )

    return {
        'contest_level_rows': contest_level_rows,
        'award_rows': award_rows,
        'level_rows': level_rows,
    }


def build_event_search_choices(queryset):
    return [
        {
            'id': event.id,
            'title': event.title,
            'location': event.location,
            'start_date': timezone.localtime(event.start_time).strftime('%Y-%m-%d'),
            'start_time': timezone.localtime(event.start_time).strftime('%H:%M'),
            'status': event.get_status_display(),
            'review_status': event.get_review_status_display(),
            'detail_url': reverse('event-detail-manage', args=[event.id]),
            'edit_url': reverse('event-edit', args=[event.id]),
        }
        for event in queryset
    ]


def build_member_search_choices(members):
    return [
        {
            'id': member.id,
            'real_name': member.real_name,
            'student_id': member.student_id,
            'username': member.user.username,
            'major': member.major or '未填写专业',
            'enrollment_year': member.enrollment_year,
            'status': '处罚期' if member.active_integrity_sanction else member.get_status_display(),
            'detail_url': reverse('member-detail-manage', args=[member.id]),
        }
        for member in members
    ]


def build_member_event_search_choices(events):
    return [
        {
            'id': event.id,
            'title': event.title,
            'event_type': event.get_event_type_display(),
            'location': event.location,
            'start_date': timezone.localtime(event.start_time).strftime('%Y-%m-%d'),
            'start_time': timezone.localtime(event.start_time).strftime('%H:%M'),
            'status': '签到中' if event.is_checkin_open() else event.get_status_display(),
            'detail_url': reverse('member-event-detail', args=[event.id]),
        }
        for event in events
    ]


def build_member_application_search_choices(events):
    return [
        {
            'id': event.id,
            'title': event.title,
            'location': event.location,
            'start_date': timezone.localtime(event.start_time).strftime('%Y-%m-%d'),
            'start_time': timezone.localtime(event.start_time).strftime('%H:%M'),
            'review_status': event.get_review_status_display(),
            'review_note': event.review_note or '等待审核结果',
            'checkin_total': event.checkin_total,
            'manage_url': reverse('event-detail-manage', args=[event.id]) if event.review_status == Event.ReviewStatus.APPROVED else '',
        }
        for event in events
    ]


def apply_contest_result_verification(result, verified, operator):
    result.verified = verified
    if verified:
        result.verified_by = operator
        result.verified_at = timezone.now()
        result.revoked_by = None
        result.revoked_at = None
    else:
        result.verified_by = None
        result.verified_at = None


def sync_competition_profiles_for_member_ids(member_ids):
    members = MemberProfile.objects.filter(id__in=set(member_ids))
    sync_members_competition_profiles(members)


def revoke_contest_result(result, operator):
    result.verified = False
    result.verified_by = None
    result.verified_at = None
    result.revoked_by = operator
    result.revoked_at = timezone.now()
    result.save(update_fields=['verified', 'verified_by', 'verified_at', 'revoked_by', 'revoked_at'])


def get_submission_member_ids(submission):
    return list(submission.team_members.values_list('id', flat=True))


def build_member_team_submission_form_context(form):
    selected_member_ids = {str(member_id) for member_id in (form['members'].value() or [])}
    selected_captain_id = str(form['captain'].value() or '')
    return {
        'member_picker_options': [
            {
                'member': member,
                'checked': str(member.id) in selected_member_ids,
            }
            for member in form.fields['members'].queryset
        ],
        'selected_captain_id': selected_captain_id,
    }


def build_member_team_prefill_map(team_queryset):
    return {
        str(team.id): {
            'name': team.name,
            'member_ids': [member.id for member in team.members.all()],
            'member_names': [member.real_name for member in team.members.all()],
            'members': [
                {
                    'id': member.id,
                    'name': member.real_name,
                    'student_id': member.student_id,
                }
                for member in sorted(team.members.all(), key=lambda member: (member.real_name, member.student_id))
            ],
        }
        for team in team_queryset.prefetch_related('members')
    }


@transaction.atomic
def approve_member_team_submission(submission, cleaned_data, operator):
    members = list(cleaned_data.get('members') or [])
    captain = cleaned_data.get('captain')
    if len(members) != 3:
        raise ValidationError('每个队伍必须恰好包含 3 名成员。')
    if captain is None or captain not in members:
        raise ValidationError('队长必须从这 3 名成员中选择。')
    if submission.action_type == MemberTeamSubmission.ActionType.EDIT and submission.target_team is None:
        raise ValidationError('待编辑的原队伍不存在，无法继续审核。')

    team = submission.resolved_team or submission.target_team
    if team is None:
        team = MemberTeam(created_by=submission.applicant)
    if not team.created_by_id:
        team.created_by = submission.applicant
    team.name = cleaned_data['team_name']
    team.captain = captain
    team.updated_by = operator
    team.save()
    team.members.set(members)

    submission.review_status = MemberTeamSubmission.ReviewStatus.APPROVED
    submission.review_note = cleaned_data.get('review_note', '')
    submission.reviewed_by = operator
    submission.reviewed_at = timezone.now()
    submission.resolved_team = team
    submission.save(
        update_fields=[
            'review_status',
            'review_note',
            'reviewed_by',
            'reviewed_at',
            'resolved_team',
            'updated_at',
        ]
    )
    return team


def ensure_submission_result_is_not_conflicting(submission, contest, team):
    existing_result = (
        ContestResult.objects.filter(contest=contest, team=team)
        .exclude(pk=submission.resolved_result_id)
        .first()
    )
    if existing_result is not None:
        raise ValidationError('该赛事队伍已经存在正式成绩，请直接编辑原成绩，不能通过申报覆盖。')


@transaction.atomic
def approve_contest_submission(submission, cleaned_data, operator):
    linked_contest = cleaned_data.get('linked_contest')
    team_members = cleaned_data.get('team_members')
    if submission.applicant.member_profile not in team_members:
        team_members = list(team_members) + [submission.applicant.member_profile]

    if linked_contest:
        contest = linked_contest
    else:
        contest = submission.resolved_contest
        if contest is None:
            contest = Contest(created_by=operator)
        contest.name = cleaned_data['contest_name']
        contest.series = cleaned_data['contest_series']
        contest.season = cleaned_data['contest_season']
        contest.stage = cleaned_data['contest_stage']
        contest.contest_date = cleaned_data['contest_date']
        contest.organizer = cleaned_data['organizer']
        contest.level = cleaned_data['contest_level']
        contest.use_default_weight = True
        contest.weight = get_default_contest_weight(contest.level)
        contest.status = Contest.Status.PUBLISHED
        contest.description = submission.submission_note or contest.description
        if not contest.created_by_id:
            contest.created_by = operator
        contest.save()

    team = submission.resolved_team
    if team is None or team.contest_id != contest.id:
        team = ContestTeam.objects.filter(contest=contest, team_name=cleaned_data['team_name']).first() or ContestTeam(contest=contest)
    team.contest = contest
    team.team_name = cleaned_data['team_name']
    team.external_member_names = cleaned_data['external_teammates']
    if submission.submission_note:
        team.note = submission.submission_note
    team.save()
    team.members.set(team_members)
    team.leader = submission.applicant.member_profile if submission.applicant.member_profile in team_members else team.members.first()
    team.save(update_fields=['leader', 'updated_at'])

    ensure_submission_result_is_not_conflicting(submission, contest, team)
    result = submission.resolved_result
    if result is None or result.contest_id != contest.id or result.team_id != team.id:
        result = ContestResult(contest=contest, team=team)
    result.contest = contest
    result.team = team
    result.award_type = cleaned_data['award_type']
    result.award_label = cleaned_data['award_label'] or dict(ContestResult.AwardType.choices).get(cleaned_data['award_type'], '')
    result.rank_label = cleaned_data['rank_label']
    result.result_tier = cleaned_data['result_tier']
    result.evidence_url = cleaned_data['evidence_url']
    result.note = cleaned_data['submission_note']
    apply_contest_result_verification(result, True, operator)
    result.save()

    submission.review_status = ContestSubmission.ReviewStatus.APPROVED
    submission.review_note = cleaned_data.get('review_note', '')
    submission.reviewed_by = operator
    submission.reviewed_at = timezone.now()
    submission.resolved_contest = contest
    submission.resolved_team = team
    submission.resolved_result = result
    submission.save(
        update_fields=[
            'review_status',
            'review_note',
            'reviewed_by',
            'reviewed_at',
            'resolved_contest',
            'resolved_team',
            'resolved_result',
            'updated_at',
        ]
    )
    sync_competition_profiles_for_member_ids(team.members.values_list('id', flat=True))
    return contest, team, result


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
    competition_snapshot = build_member_competition_snapshot(profile)
    recent_events = (
        Event.objects.filter(review_status=Event.ReviewStatus.APPROVED, status=Event.Status.PUBLISHED)
        .order_by('start_time')[:5]
    )
    context = {
        'profile': profile,
        'star_lit': star_snapshot['lit'],
        'star_snapshot': star_snapshot,
        'integrity_sanction': build_member_integrity_context(profile, viewer=request.user),
        'window_days': window_days,
        'recent_activities': star_snapshot['recent_activities'],
        'upcoming_events': recent_events,
        'checkin_count': profile.checkins.filter(status=CheckInRecord.Status.VALID).count(),
        'managed_event_total': Event.objects.filter(
            applicant=request.user,
            review_status=Event.ReviewStatus.APPROVED,
        ).count(),
        'competition_snapshot': competition_snapshot,
        'contest_submission_total': ContestSubmission.objects.filter(applicant=request.user).count(),
    }
    return render(request, 'core/member/dashboard.html', context)


@login_required
def member_event_list(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    recent_window_days = 3
    current_date = timezone.localdate()
    recent_start_date = current_date - timedelta(days=1)
    recent_end_date = current_date + timedelta(days=1)
    all_events = (
        Event.objects.filter(review_status=Event.ReviewStatus.APPROVED)
        .exclude(status=Event.Status.CANCELED)
        .order_by('-start_time', '-id')
    )
    events = all_events.filter(
        start_time__date__gte=recent_start_date,
        start_time__date__lte=recent_end_date,
    )
    all_applications = (
        Event.objects.filter(applicant=request.user)
        .annotate(checkin_total=Count('checkins'))
        .order_by('-created_at')
    )
    my_applications = all_applications.filter(
        start_time__date__gte=recent_start_date,
        start_time__date__lte=recent_end_date,
    )
    return render(
        request,
        'core/member/event_list.html',
        {
            'events': events,
            'my_applications': my_applications,
            'recent_window_days': recent_window_days,
            'recent_start_date': recent_start_date,
            'recent_end_date': recent_end_date,
            'recent_event_total': events.count(),
            'recent_application_total': my_applications.count(),
            'event_search_choices': build_member_event_search_choices(all_events),
            'application_search_choices': build_member_application_search_choices(all_applications),
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
    sync_series_completion_for_member(profile, event.series)
    log_action(request.user, 'member_checkin', 'Event', event.id, f'checkin_id={checkin.id}')
    messages.success(request, '签到成功。')
    return redirect('member-event-detail', event_id=event.id)


@login_required
def member_checkin_history(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    activity_records = build_member_star_activity_records(profile)
    return render(
        request,
        'core/member/checkin_history.html',
        {
            'activity_records': activity_records,
        },
    )


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
    return render(
        request,
        'core/member/profile.html',
        {
            'form': form,
            'profile': profile,
            'competition_snapshot': build_member_competition_snapshot(profile),
            'integrity_sanction': build_member_integrity_context(profile, viewer=request.user),
        },
    )


@login_required
def member_competition_profile_public(request, member_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, pk=member_id, status=MemberProfile.Status.ACTIVE)
    return render(
        request,
        'core/member/competition_profile_public.html',
        {
            'profile': profile,
            'competition_snapshot': build_member_competition_snapshot(profile),
            'integrity_sanction': build_member_integrity_context(profile, viewer=request.user),
            'is_self': profile.user_id == request.user.id,
        },
    )


@login_required
def member_team_list(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    teams = (
        MemberTeam.objects.filter(members=profile)
        .select_related('captain')
        .prefetch_related('members')
        .distinct()
        .order_by('name', 'id')
    )
    submissions = (
        MemberTeamSubmission.objects.filter(applicant=request.user)
        .select_related('captain', 'reviewed_by', 'target_team', 'resolved_team')
        .prefetch_related('members')
        .order_by('-created_at')
    )
    return render(
        request,
        'core/member/team_list.html',
        {
            'teams': teams,
            'submissions': submissions,
            'profile': profile,
        },
    )


@login_required
def member_team_create(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    form = MemberTeamSubmissionForm(applicant_profile=profile, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        submission = form.save(commit=False)
        submission.applicant = request.user
        submission.action_type = MemberTeamSubmission.ActionType.CREATE
        submission.review_status = MemberTeamSubmission.ReviewStatus.PENDING
        submission.review_note = ''
        submission.reviewed_by = None
        submission.reviewed_at = None
        submission.target_team = None
        submission.resolved_team = None
        submission.save()
        form.save_m2m()
        log_action(request.user, 'create_member_team_submission', 'MemberTeamSubmission', submission.id)
        messages.success(request, '新增队伍申请已提交，等待管理员审核。')
        return redirect('member-team-list')
    context = {
        'form': form,
        'page_title': '新增队伍',
    }
    context.update(build_member_team_submission_form_context(form))
    return render(request, 'core/member/team_form.html', context)


@login_required
def member_team_edit(request, team_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    team = get_object_or_404(MemberTeam.objects.select_related('captain').prefetch_related('members'), pk=team_id)
    if not can_edit_member_team(request.user, team):
        return HttpResponseForbidden('仅该队伍的队长可编辑队伍。')
    submission = team.pending_submissions.filter(review_status=MemberTeamSubmission.ReviewStatus.PENDING).first()
    if submission is None:
        submission = MemberTeamSubmission(
            applicant=request.user,
            action_type=MemberTeamSubmission.ActionType.EDIT,
            target_team=team,
            team_name=team.name,
            captain=team.captain,
        )
    form = MemberTeamSubmissionForm(applicant_profile=profile, data=request.POST or None, instance=submission)
    if request.method == 'GET' and submission.pk is None:
        form.fields['members'].initial = list(team.members.values_list('id', flat=True))
        form.fields['captain'].initial = team.captain_id
    if request.method == 'POST' and form.is_valid():
        submission = form.save(commit=False)
        submission.applicant = request.user
        submission.action_type = MemberTeamSubmission.ActionType.EDIT
        submission.target_team = team
        submission.review_status = MemberTeamSubmission.ReviewStatus.PENDING
        submission.review_note = ''
        submission.reviewed_by = None
        submission.reviewed_at = None
        submission.resolved_team = None
        submission.save()
        form.save_m2m()
        log_action(request.user, 'edit_member_team_submission', 'MemberTeamSubmission', submission.id, f'team_id={team.id}')
        messages.success(request, '队伍编辑申请已提交，等待管理员审核。')
        return redirect('member-team-list')
    context = {
        'form': form,
        'page_title': '编辑队伍',
        'team': team,
        'pending_submission': submission if submission.pk else None,
    }
    context.update(build_member_team_submission_form_context(form))
    return render(request, 'core/member/team_form.html', context)


@login_required
def member_contest_submission_list(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    submissions = (
        ContestSubmission.objects.filter(applicant=request.user)
        .select_related('reviewed_by', 'resolved_contest', 'resolved_result', 'linked_member_team')
        .prefetch_related('team_members')
        .order_by('-created_at')
    )
    return render(
        request,
        'core/member/contest_submission_list.html',
        {
            'submissions': submissions,
        },
    )


@login_required
def member_contest_submission_apply(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    profile = get_object_or_404(MemberProfile, user=request.user)
    form = ContestSubmissionForm(applicant_profile=profile, show_evidence_url=False, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        submission = form.save(commit=False)
        submission.applicant = request.user
        submission.review_status = ContestSubmission.ReviewStatus.PENDING
        submission.save()
        selected_members = list(form.cleaned_data['team_members'])
        if profile not in selected_members:
            selected_members.append(profile)
        submission.team_members.set(selected_members)
        log_action(request.user, 'submit_contest_submission', 'ContestSubmission', submission.id)
        messages.success(request, '赛事奖项申报已提交，等待管理员审核。')
        return redirect('member-contest-submission-list')
    return render(
        request,
        'core/member/contest_submission_form.html',
        {
            'form': form,
            'page_title': '申报赛事奖项',
            'member_team_prefill_map': build_member_team_prefill_map(form.fields['linked_member_team'].queryset),
        },
    )


@login_required
def member_contest_submission_detail(request, submission_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    submission = get_object_or_404(
        ContestSubmission.objects.select_related(
            'reviewed_by', 'resolved_contest', 'resolved_result', 'linked_member_team'
        ).prefetch_related('team_members'),
        pk=submission_id,
        applicant=request.user,
    )
    return render(
        request,
        'core/member/contest_submission_detail.html',
        {
            'submission': submission,
        },
    )


@login_required
def member_contest_submission_withdraw(request, submission_id):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    submission = get_object_or_404(
        ContestSubmission,
        pk=submission_id,
        applicant=request.user,
        review_status=ContestSubmission.ReviewStatus.PENDING,
    )
    if request.method == 'POST':
        submission.review_status = ContestSubmission.ReviewStatus.WITHDRAWN
        submission.save()
        log_action(request.user, 'withdraw_contest_submission', 'ContestSubmission', submission.id)
        messages.success(request, '申报已成功撤回。')
        return redirect('member-contest-submission-list')
    return render(
        request,
        'core/member/contest_submission_confirm_withdraw.html',
        {
            'submission': submission,
        },
    )


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
            'integrity_sanction': build_member_integrity_context(profile, viewer=request.user),
        },
    )


@login_required
def member_competition_ladder(request):
    if not require_member(request.user):
        return HttpResponseForbidden('仅队员可访问。')
    ladder_queryset = build_competition_ladder_queryset()
    selected_major = request.GET.get('major', '').strip()
    selected_year = request.GET.get('year', '').strip()
    selected_level = request.GET.get('level', '').strip()
    query = request.GET.get('q', '').strip()

    if selected_major:
        ladder_queryset = ladder_queryset.filter(member__major=selected_major)
    if selected_year:
        ladder_queryset = ladder_queryset.filter(member__enrollment_year=selected_year)
    if selected_level:
        ladder_queryset = ladder_queryset.filter(current_level=selected_level)
    if query:
        ladder_queryset = ladder_queryset.filter(
            Q(member__real_name__icontains=query)
            | Q(member__student_id__icontains=query)
            | Q(member__major__icontains=query)
        )

    ladder = list(ladder_queryset)
    now = timezone.now()
    sanction_map = build_non_expired_integrity_sanction_map([entry.member_id for entry in ladder], now=now)
    for entry in ladder:
        entry.level_meta = get_competition_level(entry.current_rating)
        sanction = sanction_map.get(entry.member_id)
        if sanction and not sanction.is_active_at(now):
            sanction = None
        entry.integrity_sanction = build_member_integrity_context_from_snapshot(
            entry.member,
            build_integrity_sanction_snapshot_from_record(sanction),
            viewer=request.user,
            now=now,
        )
        entry.display_color = get_competition_display_color(entry.primary_color, entry.integrity_sanction)
    available_majors = [
        value
        for value in MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
        .exclude(major='')
        .order_by('major')
        .values_list('major', flat=True)
        .distinct()
    ]
    available_years = [
        value
        for value in MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
        .exclude(enrollment_year__isnull=True)
        .order_by('enrollment_year')
        .values_list('enrollment_year', flat=True)
        .distinct()
    ]
    return render(
        request,
        'core/member/competition_ladder.html',
        {
            'ladder': ladder,
            'rated_total': sum(1 for entry in ladder if entry.current_rating > 0),
            'selected_major': selected_major,
            'selected_year': selected_year,
            'selected_level': selected_level,
            'query': query,
            'available_majors': available_majors,
            'available_years': available_years,
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
    lit_members = len(star_holders)
    top_competition_members = list(build_competition_ladder_queryset()[:3])
    sanction_map = build_non_expired_integrity_sanction_map([entry.member_id for entry in top_competition_members], now=now)
    for entry in top_competition_members:
        entry.level_meta = get_competition_level(entry.current_rating)
        sanction = sanction_map.get(entry.member_id)
        if sanction and not sanction.is_active_at(now):
            sanction = None
        entry.integrity_sanction = build_member_integrity_context_from_snapshot(
            entry.member,
            build_integrity_sanction_snapshot_from_record(sanction),
            viewer=request.user,
            now=now,
        )
        entry.display_color = get_competition_display_color(entry.primary_color, entry.integrity_sanction)
    context = {
        'recent_event_count': Event.objects.filter(start_time__gte=now).count(),
        'contest_total': Contest.objects.count(),
        'pending_contest_submission_count': ContestSubmission.objects.filter(review_status=ContestSubmission.ReviewStatus.PENDING).count(),
        'verified_result_total': ContestResult.objects.filter(verified=True).count(),
        'rated_member_total': MemberCompetitionProfile.objects.filter(current_rating__gt=0).count(),
        'pending_application_count': Event.objects.filter(review_status=Event.ReviewStatus.PENDING).count(),
        'today_checkin_count': CheckInRecord.objects.filter(
            status=CheckInRecord.Status.VALID,
            checkin_time__gte=today_start,
        ).count(),
        'lit_members': lit_members,
        'active_members': active_members,
        'star_ratio': round((lit_members / active_members) * 100) if active_members else 0,
        'top_star_holders': star_holders[:3],
        'top_competition_members': top_competition_members,
        'window_days': window_days,
    }
    return render(request, 'core/management/dashboard.html', context)


@login_required
def star_analytics(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    now = timezone.now()
    window_days, window_start = get_star_window()
    star_holders = get_star_holders_queryset(window_start)
    active_members = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
    active_members_total = active_members.count()
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
        'newly_active_total': sum(1 for holder in star_holders if holder.latest_activity_at and holder.latest_activity_at >= now - timedelta(days=7)),
        'spark_total': level_counts['spark'],
        'pulse_total': level_counts['pulse'],
        'radiant_total': level_counts['radiant'],
        'major_breakdown': major_breakdown,
        'class_breakdown': class_breakdown,
    }
    return render(request, 'core/management/star_analytics.html', context)


@login_required
def member_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    query = request.GET.get('q', '').strip()
    selected_status = request.GET.get('status', '').strip()
    selected_integrity = request.GET.get('integrity', '').strip()

    members = MemberProfile.objects.select_related('user', 'competition_profile').order_by('real_name', 'student_id')
    if query:
        members = members.filter(
            Q(real_name__icontains=query)
            | Q(student_id__icontains=query)
            | Q(major__icontains=query)
            | Q(user__username__icontains=query)
        )
    if selected_status:
        members = members.filter(status=selected_status)

    members = list(members)
    now = timezone.now()
    sanction_map = build_non_expired_integrity_sanction_map([member.id for member in members], now=now)
    filtered_members = []
    restricted_total = 0
    for member in members:
        member.active_integrity_sanction = None
        sanction = sanction_map.get(member.id)
        if sanction and sanction.is_active_at(now):
            member.active_integrity_sanction = sanction
            restricted_total += 1
        if selected_integrity == 'restricted' and member.active_integrity_sanction is None:
            continue
        if selected_integrity == 'normal' and member.active_integrity_sanction is not None:
            continue
        filtered_members.append(member)

    visible_member_limit = 5
    context = {
        'members': filtered_members[:visible_member_limit],
        'member_search_choices': build_member_search_choices(filtered_members),
        'query': query,
        'selected_status': selected_status,
        'selected_integrity': selected_integrity,
        'active_total': sum(1 for member in filtered_members if member.status == MemberProfile.Status.ACTIVE),
        'restricted_total': restricted_total,
        'member_total': len(filtered_members),
        'visible_member_total': min(len(filtered_members), visible_member_limit),
        'visible_member_limit': visible_member_limit,
    }
    return render(request, 'core/management/member_list.html', context)


@login_required
def member_detail_manage(request, member_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    member = get_object_or_404(MemberProfile.objects.select_related('user'), pk=member_id)
    now = timezone.now()
    current_sanction = get_member_current_or_upcoming_integrity_sanction(member, now=now)
    form = MemberIntegritySanctionForm(request.POST or None, instance=current_sanction)
    if request.method == 'POST' and form.is_valid():
        overlap_query = MemberIntegritySanction.objects.filter(
            member=member,
            revoked_at__isnull=True,
            starts_at__lt=form.cleaned_data['ends_at'],
            ends_at__gt=form.cleaned_data['starts_at'],
        )
        if current_sanction is not None:
            overlap_query = overlap_query.exclude(pk=current_sanction.pk)
        if overlap_query.exists():
            form.add_error(None, '该成员已有时间重叠的诚信处罚，请先调整或解除现有处罚。')
        else:
            sanction = form.save(commit=False)
            created = sanction.pk is None
            sanction.member = member
            if created:
                sanction.created_by = request.user
            sanction.save()
            log_action(
                request.user,
                'create_member_integrity_sanction' if created else 'update_member_integrity_sanction',
                'MemberIntegritySanction',
                sanction.id,
                (
                    f'member={member.student_id};reason={sanction.reason_type};'
                    f'starts_at={sanction.starts_at.isoformat()};ends_at={sanction.ends_at.isoformat()}'
                ),
            )
            messages.success(request, '诚信处罚设置已保存。')
            return redirect('member-detail-manage', member_id=member.id)

    context = {
        'member': member,
        'form': form,
        'current_sanction': current_sanction,
        'competition_snapshot': build_member_competition_snapshot(member),
        'star_snapshot': build_member_star_snapshot(member),
        'is_currently_restricted': current_sanction.is_active_at(now) if current_sanction else False,
    }
    return render(request, 'core/management/member_detail.html', context)


@login_required
def member_integrity_sanction_revoke(request, member_id):
    if not require_management(request.user) or request.method != 'POST':
        return HttpResponseForbidden('无权操作。')
    member = get_object_or_404(MemberProfile, pk=member_id)
    sanction_id = request.POST.get('sanction_id')
    sanction = get_object_or_404(
        MemberIntegritySanction.objects.filter(
            member=member,
            revoked_at__isnull=True,
            ends_at__gte=timezone.now(),
        ),
        pk=sanction_id,
    ) if sanction_id else None
    if sanction is None:
        messages.warning(request, '当前没有可解除的诚信处罚。')
        return redirect('member-detail-manage', member_id=member.id)
    sanction.revoked_by = request.user
    sanction.revoked_at = timezone.now()
    sanction.save(update_fields=['revoked_by', 'revoked_at', 'updated_at'])
    log_action(
        request.user,
        'revoke_member_integrity_sanction',
        'MemberIntegritySanction',
        sanction.id,
        f'member={member.student_id}',
    )
    messages.success(request, '诚信处罚已提前解除。')
    return redirect('member-detail-manage', member_id=member.id)


@login_required
def contest_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    contests = (
        Contest.objects.annotate(
            team_total=Count('teams', distinct=True),
            verified_result_total=Count('results', filter=Q(results__verified=True), distinct=True),
        )
    )
    query = request.GET.get('q', '').strip()
    selected_series = request.GET.get('series', '').strip()
    selected_level = request.GET.get('level', '').strip()
    selected_status = request.GET.get('status', '').strip()
    selected_season = request.GET.get('season', '').strip()

    if query:
        contests = contests.filter(
            Q(name__icontains=query)
            | Q(stage__icontains=query)
            | Q(organizer__icontains=query)
            | Q(season__icontains=query)
        )
    if selected_series:
        contests = contests.filter(series=selected_series)
    if selected_level:
        contests = contests.filter(level=selected_level)
    if selected_status:
        contests = contests.filter(status=selected_status)
    if selected_season:
        contests = contests.filter(season=selected_season)

    contests = contests.order_by('-contest_date', '-id')
    available_seasons = [
        value
        for value in Contest.objects.exclude(season='').order_by('-season').values_list('season', flat=True).distinct()
    ]
    return render(
        request,
        'core/management/contest_list.html',
        {
            'contests': contests,
            'query': query,
            'selected_series': selected_series,
            'selected_level': selected_level,
            'selected_status': selected_status,
            'selected_season': selected_season,
            'available_seasons': available_seasons,
            'series_choices': Contest.Series.choices,
            'level_choices': Contest.Level.choices,
            'status_choices': Contest.Status.choices,
        },
    )


@login_required
def member_team_submission_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    selected_status = request.GET.get('status', '').strip()
    selected_action = request.GET.get('action', '').strip()
    query = request.GET.get('q', '').strip()
    submissions = (
        MemberTeamSubmission.objects.select_related(
            'applicant',
            'applicant__member_profile',
            'captain',
            'reviewed_by',
            'target_team',
            'resolved_team',
        )
        .prefetch_related('members')
        .order_by('-created_at')
    )
    if selected_status:
        submissions = submissions.filter(review_status=selected_status)
    if selected_action:
        submissions = submissions.filter(action_type=selected_action)
    if query:
        submissions = submissions.filter(
            Q(team_name__icontains=query)
            | Q(applicant__username__icontains=query)
            | Q(applicant__member_profile__real_name__icontains=query)
            | Q(members__real_name__icontains=query)
            | Q(members__student_id__icontains=query)
        ).distinct()
    return render(
        request,
        'core/management/member_team_submission_list.html',
        {
            'submissions': submissions,
            'selected_status': selected_status,
            'selected_action': selected_action,
            'query': query,
            'status_choices': MemberTeamSubmission.ReviewStatus.choices,
            'action_choices': MemberTeamSubmission.ActionType.choices,
            'pending_total': MemberTeamSubmission.objects.filter(review_status=MemberTeamSubmission.ReviewStatus.PENDING).count(),
        },
    )


@login_required
def member_team_submission_detail_manage(request, submission_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    submission = get_object_or_404(
        MemberTeamSubmission.objects.select_related(
            'applicant',
            'applicant__member_profile',
            'captain',
            'reviewed_by',
            'target_team',
            'resolved_team',
        ).prefetch_related('members', 'resolved_team__members', 'target_team__members'),
        pk=submission_id,
    )
    applicant_profile = getattr(submission.applicant, 'member_profile', None)
    form = MemberTeamSubmissionReviewForm(applicant_profile=applicant_profile, data=request.POST or None, instance=submission)
    context = {
        'submission': submission,
        'form': form,
    }
    context.update(build_member_team_submission_form_context(form))
    return render(request, 'core/management/member_team_submission_detail.html', context)


@login_required
@transaction.atomic
def member_team_submission_review(request, submission_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    submission = get_object_or_404(
        MemberTeamSubmission.objects.select_related(
            'applicant',
            'applicant__member_profile',
            'target_team',
            'resolved_team',
        ).prefetch_related('members'),
        pk=submission_id,
    )
    if not can_review_member_team_submission(request.user, submission):
        return HttpResponseForbidden('仅管理员可审核队伍申请。')
    applicant_profile = getattr(submission.applicant, 'member_profile', None)
    form = MemberTeamSubmissionReviewForm(applicant_profile=applicant_profile, data=request.POST or None, instance=submission)
    if not form.is_valid():
        messages.error(request, '审核表单提交失败，请检查后重试。')
        context = {
            'submission': submission,
            'form': form,
        }
        context.update(build_member_team_submission_form_context(form))
        return render(request, 'core/management/member_team_submission_detail.html', context)
    decision = request.POST.get('decision')
    if decision == 'approve':
        try:
            team = approve_member_team_submission(submission, form.cleaned_data, request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
            messages.error(request, '队伍申请审核未通过，请先处理表单中的问题。')
            context = {
                'submission': submission,
                'form': form,
            }
            context.update(build_member_team_submission_form_context(form))
            return render(request, 'core/management/member_team_submission_detail.html', context)
        log_action(request.user, 'approve_member_team_submission', 'MemberTeamSubmission', submission.id, f'team_id={team.id}')
        messages.success(request, '队伍申请已审核通过，正式队伍已更新。')
    elif decision == 'reject':
        if submission.review_status == MemberTeamSubmission.ReviewStatus.APPROVED:
            messages.error(request, '已通过的队伍申请不能直接驳回，请发起新的编辑申请。')
            return redirect('member-team-submission-detail-manage', submission_id=submission.id)
        submission.review_status = MemberTeamSubmission.ReviewStatus.REJECTED
        submission.review_note = form.cleaned_data.get('review_note', '')
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save(update_fields=['review_status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
        log_action(request.user, 'reject_member_team_submission', 'MemberTeamSubmission', submission.id)
        messages.success(request, '队伍申请已驳回。')
    else:
        messages.error(request, '无效的审核操作。')
    return redirect('member-team-submission-detail-manage', submission_id=submission.id)


@login_required
def contest_submission_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    selected_status = request.GET.get('status', '').strip()
    query = request.GET.get('q', '').strip()
    submissions = ContestSubmission.objects.select_related('applicant', 'reviewed_by', 'resolved_contest', 'linked_member_team').prefetch_related('team_members')
    if selected_status:
        submissions = submissions.filter(review_status=selected_status)
    if query:
        submissions = submissions.filter(
            Q(contest_name__icontains=query)
            | Q(team_name__icontains=query)
            | Q(applicant__username__icontains=query)
            | Q(applicant__member_profile__real_name__icontains=query)
        )
    submissions = submissions.order_by('-created_at')
    return render(
        request,
        'core/management/contest_submission_list.html',
        {
            'submissions': submissions,
            'selected_status': selected_status,
            'query': query,
            'status_choices': ContestSubmission.ReviewStatus.choices,
            'pending_total': ContestSubmission.objects.filter(review_status=ContestSubmission.ReviewStatus.PENDING).count(),
        },
    )


@login_required
def contest_submission_detail_manage(request, submission_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    submission = get_object_or_404(
        ContestSubmission.objects.select_related(
            'applicant',
            'applicant__member_profile',
            'reviewed_by',
            'linked_member_team',
            'resolved_contest',
            'resolved_team',
            'resolved_result',
        ).prefetch_related('team_members'),
        pk=submission_id,
    )
    applicant_profile = getattr(submission.applicant, 'member_profile', None)
    form = ContestSubmissionReviewForm(applicant_profile=applicant_profile, data=request.POST or None, instance=submission)
    return render(
        request,
        'core/management/contest_submission_detail.html',
        {
            'submission': submission,
            'form': form,
        },
    )


@login_required
@transaction.atomic
def contest_submission_review(request, submission_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    submission = get_object_or_404(
        ContestSubmission.objects.select_related(
            'applicant',
            'applicant__member_profile',
            'linked_member_team',
            'resolved_result',
            'resolved_result__team',
        ),
        pk=submission_id,
    )
    if submission.review_status == ContestSubmission.ReviewStatus.WITHDRAWN:
        return HttpResponseForbidden('该申报已撤回，无法审核。')
    if not can_review_contest_submission(request.user, submission):
        return HttpResponseForbidden('仅管理员可审核赛事奖项申报。')
    applicant_profile = getattr(submission.applicant, 'member_profile', None)
    form = ContestSubmissionReviewForm(applicant_profile=applicant_profile, data=request.POST or None, instance=submission)
    if not form.is_valid():
        messages.error(request, '审核表单提交失败，请检查后重试。')
        return render(
            request,
            'core/management/contest_submission_detail.html',
            {
                'submission': submission,
                'form': form,
            },
        )
    decision = request.POST.get('decision')
    previous_review_status = submission.review_status
    submission = form.save(commit=False)
    submission.review_note = form.cleaned_data.get('review_note', '')
    submission.save()
    form.save_m2m()
    if decision == 'approve':
        try:
            contest, team, result = approve_contest_submission(submission, form.cleaned_data, request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
            messages.error(request, '审核未通过，请先处理正式赛事成绩冲突。')
            return render(
                request,
                'core/management/contest_submission_detail.html',
                {
                    'submission': submission,
                    'form': form,
                },
            )
        log_action(request.user, 'approve_contest_submission', 'ContestSubmission', submission.id, f'contest_id={contest.id},result_id={result.id}')
        messages.success(request, '赛事奖项申报已通过，正式赛事记录已更新。')
    elif decision == 'reject':
        if previous_review_status == ContestSubmission.ReviewStatus.APPROVED and submission.resolved_result_id:
            result = submission.resolved_result
            if result.verified:
                revoke_contest_result(result, request.user)
                sync_competition_profiles_for_member_ids(result.team.members.values_list('id', flat=True))
        submission.review_status = ContestSubmission.ReviewStatus.REJECTED
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save(update_fields=['review_status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
        log_action(request.user, 'reject_contest_submission', 'ContestSubmission', submission.id)
        messages.success(request, '赛事奖项申报已驳回。')
    else:
        messages.error(request, '无效的审核操作。')
    return redirect('contest-submission-detail-manage', submission_id=submission.id)


@login_required
def contest_create(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    form = ContestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        contest = form.save(commit=False)
        contest.created_by = request.user
        contest.save()
        log_action(request.user, 'create_contest', 'Contest', contest.id)
        messages.success(request, '赛事已创建。')
        return redirect('contest-detail-manage', contest_id=contest.id)
    return render(
        request,
        'core/management/contest_form.html',
        {
            'form': form,
            'page_title': '创建赛事',
            'contest_level_weights': [
                {
                    'key': level_value,
                    'label': dict(Contest.Level.choices).get(level_value, level_value),
                    'weight': str(weight),
                }
                for level_value, weight in get_contest_level_weight_rules().items()
            ],
            'contest_level_weights_json': json.dumps(
                {
                    level_value: str(weight)
                    for level_value, weight in get_contest_level_weight_rules().items()
                }
            ),
        },
    )


@login_required
def contest_edit(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    contest = get_object_or_404(Contest, pk=contest_id)
    form = ContestForm(request.POST or None, instance=contest)
    if request.method == 'POST' and form.is_valid():
        contest = form.save()
        if contest.results.filter(verified=True).exists():
            member_ids = list(
                MemberProfile.objects.filter(contest_teams__contest=contest)
                .values_list('id', flat=True)
                .distinct()
            )
            sync_competition_profiles_for_member_ids(member_ids)
        log_action(request.user, 'edit_contest', 'Contest', contest.id)
        messages.success(request, '赛事已更新。')
        return redirect('contest-detail-manage', contest_id=contest.id)
    return render(
        request,
        'core/management/contest_form.html',
        {
            'form': form,
            'page_title': '编辑赛事',
            'contest': contest,
            'contest_level_weights': [
                {
                    'key': level_value,
                    'label': dict(Contest.Level.choices).get(level_value, level_value),
                    'weight': str(weight),
                }
                for level_value, weight in get_contest_level_weight_rules().items()
            ],
            'contest_level_weights_json': json.dumps(
                {
                    level_value: str(weight)
                    for level_value, weight in get_contest_level_weight_rules().items()
                }
            ),
        },
    )


@login_required
def contest_detail_manage(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    contest = get_object_or_404(Contest, pk=contest_id)
    teams = contest.teams.prefetch_related('members').order_by('team_name')
    results = (
        contest.results.select_related('team', 'verified_by')
        .prefetch_related('team__members')
        .order_by('-verified', 'team__team_name')
    )
    return render(
        request,
        'core/management/contest_detail.html',
        {
            'contest': contest,
            'teams': teams,
            'results': results,
            'verified_result_total': sum(1 for result in results if result.verified),
            'revoked_result_total': sum(1 for result in results if result.is_revoked),
            'contest_weight_source_label': '跟随规则管理' if contest.use_default_weight else '手动覆写',
        },
    )


@login_required
def contest_team_create(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    contest = get_object_or_404(Contest, pk=contest_id)
    form = ContestTeamForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        team = form.save(commit=False)
        team.contest = contest
        team.save()
        form.save_m2m()
        log_action(request.user, 'create_contest_team', 'ContestTeam', team.id, f'contest_id={contest.id}')
        messages.success(request, '参赛队伍已创建。')
        return redirect('contest-detail-manage', contest_id=contest.id)
    return render(
        request,
        'core/management/contest_team_form.html',
        {
            'form': form,
            'contest': contest,
            'page_title': '新增参赛队伍',
        },
    )


@login_required
def contest_team_edit(request, team_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    team = get_object_or_404(ContestTeam.objects.prefetch_related('members'), pk=team_id)
    previous_member_ids = list(team.members.values_list('id', flat=True))
    form = ContestTeamForm(request.POST or None, instance=team)
    if request.method == 'POST' and form.is_valid():
        team = form.save()
        if team.results.filter(verified=True).exists():
            current_member_ids = list(team.members.values_list('id', flat=True))
            sync_competition_profiles_for_member_ids(previous_member_ids + current_member_ids)
        log_action(request.user, 'edit_contest_team', 'ContestTeam', team.id)
        messages.success(request, '参赛队伍已更新。')
        return redirect('contest-detail-manage', contest_id=team.contest_id)
    return render(
        request,
        'core/management/contest_team_form.html',
        {
            'form': form,
            'contest': team.contest,
            'team': team,
            'page_title': '编辑参赛队伍',
        },
    )


@login_required
def contest_result_create(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    contest = get_object_or_404(Contest, pk=contest_id)
    form = ContestResultForm(contest, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        result = form.save(commit=False)
        result.contest = contest
        apply_contest_result_verification(result, form.cleaned_data['verified'], request.user)
        if not result.award_label:
            result.award_label = result.get_award_type_display()
        result.save()
        member_ids = list(result.team.members.values_list('id', flat=True))
        sync_competition_profiles_for_member_ids(member_ids)
        log_action(request.user, 'create_contest_result', 'ContestResult', result.id, f'contest_id={contest.id}')
        messages.success(request, '赛事成绩已保存。')
        return redirect('contest-detail-manage', contest_id=contest.id)
    return render(
        request,
        'core/management/contest_result_form.html',
        {
            'form': form,
            'contest': contest,
            'page_title': '录入赛事成绩',
            'base_participation_score': get_base_participation_score(),
            'award_bonus_rules': [
                {
                    'key': award,
                    'label': dict(ContestResult.AwardType.choices).get(award, award),
                    'bonus': bonus,
                }
                for award, bonus in get_award_bonus_rules().items()
            ],
        },
    )


@login_required
def contest_result_edit(request, result_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    result = get_object_or_404(ContestResult.objects.select_related('contest', 'team'), pk=result_id)
    previous_member_ids = list(result.team.members.values_list('id', flat=True))
    previous_team_id = result.team_id
    form = ContestResultForm(result.contest, request.POST or None, instance=result)
    if request.method == 'POST' and form.is_valid():
        result = form.save(commit=False)
        apply_contest_result_verification(result, form.cleaned_data['verified'], request.user)
        if not result.award_label:
            result.award_label = result.get_award_type_display()
        result.save()
        member_ids = previous_member_ids
        if result.team_id != previous_team_id:
            member_ids += list(result.team.members.values_list('id', flat=True))
        else:
            member_ids += list(result.team.members.values_list('id', flat=True))
        sync_competition_profiles_for_member_ids(member_ids)
        log_action(request.user, 'edit_contest_result', 'ContestResult', result.id)
        messages.success(request, '赛事成绩已更新。')
        return redirect('contest-detail-manage', contest_id=result.contest_id)
    return render(
        request,
        'core/management/contest_result_form.html',
        {
            'form': form,
            'contest': result.contest,
            'result': result,
            'page_title': '编辑赛事成绩',
            'base_participation_score': get_base_participation_score(),
            'award_bonus_rules': [
                {
                    'key': award,
                    'label': dict(ContestResult.AwardType.choices).get(award, award),
                    'bonus': bonus,
                }
                for award, bonus in get_award_bonus_rules().items()
            ],
        },
    )


@login_required
def contest_archive(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    contest = get_object_or_404(Contest, pk=contest_id)
    contest.status = Contest.Status.ARCHIVED
    contest.save(update_fields=['status', 'updated_at'])
    member_ids = list(
        MemberProfile.objects.filter(contest_teams__contest=contest)
        .values_list('id', flat=True)
        .distinct()
    )
    sync_competition_profiles_for_member_ids(member_ids)
    log_action(request.user, 'archive_contest', 'Contest', contest.id)
    messages.success(request, '赛事已归档。')
    return redirect('contest-detail-manage', contest_id=contest.id)


@login_required
def contest_publish(request, contest_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    contest = get_object_or_404(Contest, pk=contest_id)
    contest.status = Contest.Status.PUBLISHED
    contest.save(update_fields=['status', 'updated_at'])
    member_ids = list(
        MemberProfile.objects.filter(contest_teams__contest=contest)
        .values_list('id', flat=True)
        .distinct()
    )
    sync_competition_profiles_for_member_ids(member_ids)
    log_action(request.user, 'publish_contest', 'Contest', contest.id)
    messages.success(request, '赛事已恢复为已发布。')
    return redirect('contest-detail-manage', contest_id=contest.id)


@login_required
def contest_result_revoke(request, result_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    result = get_object_or_404(ContestResult.objects.select_related('contest', 'team'), pk=result_id)
    revoke_contest_result(result, request.user)
    sync_competition_profiles_for_member_ids(result.team.members.values_list('id', flat=True))
    log_action(request.user, 'revoke_contest_result', 'ContestResult', result.id)
    messages.success(request, '赛事成绩已撤销生效。')
    return redirect('contest-detail-manage', contest_id=result.contest_id)


@login_required
def contest_result_restore(request, result_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    if request.method != 'POST':
        return HttpResponseForbidden('仅支持 POST。')
    result = get_object_or_404(ContestResult.objects.select_related('contest', 'team'), pk=result_id)
    apply_contest_result_verification(result, True, request.user)
    if not result.award_label:
        result.award_label = result.get_award_type_display()
    result.save()
    sync_competition_profiles_for_member_ids(result.team.members.values_list('id', flat=True))
    log_action(request.user, 'restore_contest_result', 'ContestResult', result.id)
    messages.success(request, '赛事成绩已恢复生效。')
    return redirect('contest-detail-manage', contest_id=result.contest_id)


@login_required
def event_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    recent_window_days = 3
    current_date = timezone.localdate()
    recent_start_date = current_date - timedelta(days=1)
    recent_end_date = current_date + timedelta(days=1)
    base_events = Event.objects.select_related('applicant', 'reviewed_by', 'series').prefetch_related(
        'checkin_managers__member_profile'
    )
    events = (
        base_events.filter(
            start_time__date__gte=recent_start_date,
            start_time__date__lte=recent_end_date,
        )
        .annotate(checkin_total=Count('checkins'))
        .order_by('-start_time', '-id')
    )
    searchable_events = (
        Event.objects.select_related('applicant', 'reviewed_by', 'series').prefetch_related('checkin_managers__member_profile')
        .order_by('-start_time', '-id')
    )
    return render(
        request,
        'core/management/event_list.html',
        {
            'events': events,
            'pending_application_total': Event.objects.filter(review_status=Event.ReviewStatus.PENDING).count(),
            'recent_window_days': recent_window_days,
            'recent_start_date': recent_start_date,
            'recent_end_date': recent_end_date,
            'recent_event_total': events.count(),
            'event_search_choices': build_event_search_choices(searchable_events),
        },
    )


@login_required
def event_series_list_manage(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    query = request.GET.get('q', '').strip()
    selected_status = request.GET.get('status', '').strip()
    series_list = EventSeries.objects.annotate(
        event_total=Count('events', distinct=True),
        completed_member_total=Count(
            'completions',
            filter=Q(completions__is_completed_for_rating=True),
            distinct=True,
        ),
    )
    if query:
        series_list = series_list.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
        )
    if selected_status:
        series_list = series_list.filter(status=selected_status)
    return render(
        request,
        'core/management/event_series_list.html',
        {
            'series_list': series_list.order_by('-created_at', 'title'),
            'query': query,
            'selected_status': selected_status,
            'status_choices': EventSeries.Status.choices,
        },
    )


@login_required
def event_series_create(request):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    form = EventSeriesForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        series = form.save(commit=False)
        series.created_by = request.user
        series.save()
        log_action(request.user, 'create_event_series', 'EventSeries', series.id)
        messages.success(request, '活动系列已创建。')
        return redirect('event-series-list-manage')
    return render(
        request,
        'core/management/event_series_form.html',
        {
            'form': form,
            'page_title': '创建系列',
            'submit_label': '保存系列',
        },
    )


@login_required
def event_series_edit(request, series_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    series = get_object_or_404(EventSeries.objects.annotate(event_total=Count('events', distinct=True)), pk=series_id)
    form = EventSeriesForm(request.POST or None, instance=series)
    if request.method == 'POST' and form.is_valid():
        series = form.save()
        sync_series_completions_for_series(series)
        log_action(request.user, 'edit_event_series', 'EventSeries', series.id)
        messages.success(request, '活动系列已更新。')
        return redirect('event-series-list-manage')
    return render(
        request,
        'core/management/event_series_form.html',
        {
            'form': form,
            'page_title': '编辑系列',
            'series': series,
            'submit_label': '保存修改',
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
            'event_series_choices': build_event_series_choices(form.fields['series'].queryset),
            'selected_series_id': str(form['series'].value() or ''),
            'checkin_manager_choices': build_checkin_manager_choices(form.fields['checkin_managers'].queryset),
            'selected_checkin_manager_ids': form['checkin_managers'].value() or [],
        },
    )


@login_required
def event_edit(request, event_id):
    if not require_management(request.user):
        return HttpResponseForbidden('仅管理员可访问。')
    event = get_object_or_404(
        Event.objects.select_related('series').prefetch_related('checkin_managers__member_profile'),
        pk=event_id,
    )
    previous_series_id = event.series_id
    form = EventForm(request.POST or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        if event.status == Event.Status.PUBLISHED and not event.published_at:
            event.published_at = timezone.now()
        event.save()
        form.save_m2m()
        sync_series_completions_for_event_members(event, series_ids=[previous_series_id])
        log_action(request.user, 'edit_event', 'Event', event.id)
        messages.success(request, '活动已更新。')
        return redirect('event-detail-manage', event_id=event.id)
    return render(
        request,
        'core/management/event_form.html',
        {
            'form': form,
            'page_title': '编辑活动',
            'event_series_choices': build_event_series_choices(form.fields['series'].queryset),
            'selected_series_id': str(form['series'].value() or ''),
            'checkin_manager_choices': build_checkin_manager_choices(form.fields['checkin_managers'].queryset),
            'selected_checkin_manager_ids': form['checkin_managers'].value() or [],
        },
    )


@login_required
def event_detail_manage(request, event_id):
    event = get_object_or_404(
        Event.objects.select_related('applicant', 'reviewed_by', 'created_by', 'series').prefetch_related('checkin_managers__member_profile'),
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

    now = timezone.now()
    # 取最早创建的活跃码来判断是否该轮换（它最先到达刷新截止时间）
    oldest_active_qr = event.qr_codes.filter(is_active=True).order_by('created_at').first()

    # 当前最早的活跃码仍在刷新截止时间内，直接返回最新的活跃码，不消耗队列
    if oldest_active_qr and oldest_active_qr.is_valid_at(now) and now < oldest_active_qr.get_refresh_deadline(QR_CODE_REFRESH_SECONDS):
        newest_qr = event.qr_codes.filter(is_active=True).order_by('-created_at').first()
        display_qr = newest_qr or oldest_active_qr
        return JsonResponse(
            {
                'active': True,
                'entry_url': display_qr.url,
                'image': build_qr_data_uri(display_qr.url),
                'token_preview': f'{display_qr.token[:16]}...',
                'refresh_interval_seconds': QR_CODE_REFRESH_SECONDS,
                'refresh_at': oldest_active_qr.get_refresh_deadline(QR_CODE_REFRESH_SECONDS).isoformat(),
                'expires_at': display_qr.expires_at.isoformat() if display_qr.expires_at else None,
            }
        )

    # 到达刷新时间，优先从预生成队列中取出下一个二维码，消除生成延迟
    data = pop_event_qr_queue(event_id)
    if data is not None:
        if oldest_active_qr:
            oldest_active_qr.mark_inactive(now)
        qr_code = data['qr_code']
        qr_code.created_at = now
        qr_code.save(update_fields=['created_at'])
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
    sync_series_completion_for_member(form.member_profile, event.series)
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
    sync_series_completion_for_member(checkin.member, event.series)
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
def management_rule_overview(request):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    level_ranges = build_competition_level_ranges()
    contest_level_labels = dict(Contest.Level.choices)
    award_labels = dict(ContestResult.AwardType.choices)
    return render(
        request,
        'core/management/rule_overview.html',
        {
            'star_recent_window_days': int(SystemSetting.get_value('star_recent_window_days', '30')),
            'qr_code_expire_minutes': int(SystemSetting.get_value('qr_code_expire_minutes', '120')),
            'base_participation_score': get_base_participation_score(),
            'contest_level_weights': [
                {
                    'key': level,
                    'label': contest_level_labels.get(level, level),
                    'weight': weight,
                }
                for level, weight in get_contest_level_weight_rules().items()
            ],
            'award_bonus_rules': [
                {
                    'key': award,
                    'label': award_labels.get(award, award),
                    'bonus': bonus,
                }
                for award, bonus in get_award_bonus_rules().items()
            ],
            'level_ranges': level_ranges,
        },
    )


@login_required
def star_rules_manage(request):
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
        messages.success(request, 'Star 与签到入口规则已更新。')
        return redirect('star-rules-manage')
    return render(request, 'core/management/system_settings.html', {'form': form})


@login_required
def rating_rules_manage(request):
    if not require_super_admin(request.user):
        return HttpResponseForbidden('仅超级管理员可访问。')
    form = RatingRulesForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            SystemSetting.objects.update_or_create(
                key=BASE_PARTICIPATION_SETTING_KEY,
                defaults={
                    'value': str(form.cleaned_data['base_participation_score']),
                    'updated_by': request.user,
                },
            )
            SystemSetting.objects.update_or_create(
                key=CONTEST_LEVEL_RULES_SETTING_KEY,
                defaults={
                    'value': json.dumps(form.get_contest_level_weights_payload()),
                    'updated_by': request.user,
                },
            )
            SystemSetting.objects.update_or_create(
                key=AWARD_BONUS_RULES_SETTING_KEY,
                defaults={
                    'value': json.dumps(form.get_award_bonus_payload()),
                    'updated_by': request.user,
                },
            )
            SystemSetting.objects.update_or_create(
                key=COMPETITION_LEVEL_THRESHOLDS_SETTING_KEY,
                defaults={
                    'value': json.dumps(form.get_level_threshold_payload()),
                    'updated_by': request.user,
                },
            )
            sync_default_contest_weights()
            sync_all_competition_profiles()
            log_action(
                request.user,
                'update_rating_rules',
                'SystemSetting',
                detail=(
                    f"base_participation_score={form.cleaned_data['base_participation_score']};"
                    f"contest_level_weights={json.dumps(form.get_contest_level_weights_payload(), ensure_ascii=False)};"
                    f"award_bonuses={json.dumps(form.get_award_bonus_payload(), ensure_ascii=False)};"
                    f"thresholds={json.dumps(form.get_level_threshold_payload(), ensure_ascii=False)}"
                ),
            )
            messages.success(request, 'Rating 规则已更新，相关成员档案已全量重算。')
            return redirect('rating-rules-manage')
    context = {
        'form': form,
        'base_participation_score': get_base_participation_score(),
    }
    context.update(build_rating_rule_rows(form))
    return render(request, 'core/management/rating_rules.html', context)


@login_required
def system_settings_manage(request):
    return redirect('star-rules-manage')


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
    sync_series_completion_for_member(profile, event.series)
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

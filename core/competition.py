from decimal import Decimal

from django.utils import timezone

from .models import (
    CheckInRecord,
    Contest,
    ContestResult,
    EventSeries,
    EventSeriesCompletion,
    MemberCompetitionProfile,
    MemberProfile,
)


LEVEL_WEIGHT_MAP = {
    Contest.Level.NATIONAL: Decimal('1.60'),
    Contest.Level.REGIONAL: Decimal('1.40'),
    Contest.Level.PROVINCIAL: Decimal('1.20'),
    Contest.Level.CAMPUS: Decimal('1.00'),
    Contest.Level.INTERNAL: Decimal('0.80'),
}

INTEGRITY_RESTRICTED_COLOR = '#8b5a2b'

AWARD_BONUS_MAP = {
    ContestResult.AwardType.GOLD: 120,
    ContestResult.AwardType.SILVER: 80,
    ContestResult.AwardType.BRONZE: 50,
    ContestResult.AwardType.HONORABLE: 25,
    ContestResult.AwardType.FINALIST: 15,
    ContestResult.AwardType.PARTICIPATION: 5,
    ContestResult.AwardType.CUSTOM: 20,
}

COMPETITION_LEVELS = [
    {
        'slug': 'unrated',
        'label': 'Unrated',
        'title': '未定级',
        'min_rating': 0,
        'tone': 'dim',
        'color': '#7d8b99',
    },
    {
        'slug': 'rookie',
        'label': 'Rookie',
        'title': '新秀',
        'min_rating': 1,
        'tone': 'warn',
        'color': '#3d9b50',
    },
    {
        'slug': 'solver',
        'label': 'Solver',
        'title': '进阶解题者',
        'min_rating': 200,
        'tone': 'live',
        'color': '#1c8f87',
    },
    {
        'slug': 'specialist',
        'label': 'Specialist',
        'title': '稳定解题者',
        'min_rating': 500,
        'tone': 'live',
        'color': '#2d63d7',
    },
    {
        'slug': 'expert',
        'label': 'Expert',
        'title': '校队核心',
        'min_rating': 900,
        'tone': 'live',
        'color': '#7551d7',
    },
    {
        'slug': 'master',
        'label': 'Master',
        'title': '竞赛大师',
        'min_rating': 1400,
        'tone': 'warn',
        'color': '#d8791f',
    },
    {
        'slug': 'legend',
        'label': 'Legend',
        'title': '传奇',
        'min_rating': 2000,
        'tone': 'live',
        'color': '#c83f3f',
    },
]


def get_competition_level(rating):
    selected = COMPETITION_LEVELS[0]
    for level in COMPETITION_LEVELS:
        if rating >= level['min_rating']:
            selected = level
    return selected


def build_integrity_sanction_snapshot(member, now=None):
    sanction = member.get_active_integrity_sanction(now=now)
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


def get_competition_display_color(default_color, integrity_sanction=None):
    return INTEGRITY_RESTRICTED_COLOR if integrity_sanction else default_color


def get_level_weight(level, stored_weight=None):
    if stored_weight:
        return Decimal(str(stored_weight))
    return LEVEL_WEIGHT_MAP.get(level, Decimal('1.00'))


def get_award_bonus(award_type):
    return AWARD_BONUS_MAP.get(award_type, 0)


def get_decay_factor(contest_date, today=None):
    today = today or timezone.localdate()
    age_days = max((today - contest_date).days, 0)
    if age_days <= 365:
        return Decimal('1.00')
    if age_days <= 730:
        return Decimal('0.85')
    if age_days <= 1095:
        return Decimal('0.70')
    return Decimal('0.55')


def calculate_result_rating_delta(result, today=None):
    level_weight = get_level_weight(result.contest.level, result.contest.weight)
    decay_factor = get_decay_factor(result.contest.contest_date, today=today)
    base_score = 20 + get_award_bonus(result.award_type) + result.manual_bonus
    delta = Decimal(base_score) * level_weight * decay_factor
    return int(delta.quantize(Decimal('1')))


def get_member_verified_results(member):
    return (
        ContestResult.objects.filter(
            verified=True,
            contest__status=Contest.Status.PUBLISHED,
            team__members=member,
        )
        .select_related('contest', 'team', 'verified_by')
        .prefetch_related('team__members')
        .order_by('-contest__contest_date', '-verified_at', '-id')
        .distinct()
    )


def get_member_completed_series_completions(member):
    return (
        EventSeriesCompletion.objects.filter(
            member=member,
            is_completed_for_rating=True,
            series__rating_enabled=True,
            series__status=EventSeries.Status.PUBLISHED,
        )
        .select_related('series')
        .order_by('-completed_at', '-updated_at', '-id')
    )


def sync_event_series_completion(member, series):
    if series is None:
        return None
    valid_checkins = (
        CheckInRecord.objects.filter(
            member=member,
            event__series=series,
            status=CheckInRecord.Status.VALID,
        )
        .select_related('event')
        .order_by('-checkin_time')
    )
    valid_checkin_count = valid_checkins.values('event_id').distinct().count()
    latest_checkin = valid_checkins.first()
    is_completed_for_rating = (
        series.rating_enabled
        and series.status == EventSeries.Status.PUBLISHED
        and valid_checkin_count >= series.required_checkins_for_rating
    )
    rating_delta = series.rating_points if is_completed_for_rating else 0
    completion, _ = EventSeriesCompletion.objects.get_or_create(member=member, series=series)
    completed_at = completion.completed_at
    if is_completed_for_rating and completed_at is None:
        completed_at = latest_checkin.checkin_time if latest_checkin else timezone.now()
    if not is_completed_for_rating:
        completed_at = None
    completion.valid_checkin_count = valid_checkin_count
    completion.is_completed_for_rating = is_completed_for_rating
    completion.rating_delta = rating_delta
    completion.completed_at = completed_at
    completion.last_counted_checkin_at = latest_checkin.checkin_time if latest_checkin else None
    completion.save(
        update_fields=[
            'valid_checkin_count',
            'is_completed_for_rating',
            'rating_delta',
            'completed_at',
            'last_counted_checkin_at',
            'updated_at',
        ]
    )
    return completion


def choose_highest_award(results):
    ranked = sorted(
        results,
        key=lambda result: (
            get_award_bonus(result.award_type),
            get_level_weight(result.contest.level, result.contest.weight),
            result.contest.contest_date,
        ),
        reverse=True,
    )
    return ranked[0] if ranked else None


def get_or_create_competition_profile(member):
    profile, _ = MemberCompetitionProfile.objects.get_or_create(member=member)
    return profile


def sync_member_competition_profile(member, today=None):
    today = today or timezone.localdate()
    competition_profile = get_or_create_competition_profile(member)
    results = list(get_member_verified_results(member))
    contest_rating = 0
    for result in results:
        new_delta = calculate_result_rating_delta(result, today=today)
        if result.rating_delta != new_delta:
            result.rating_delta = new_delta
            result.save(update_fields=['rating_delta'])
        contest_rating += new_delta
    series_completions = list(get_member_completed_series_completions(member))
    series_rating = sum(completion.rating_delta for completion in series_completions)
    total_rating = contest_rating + series_rating

    current_level = get_competition_level(total_rating)
    latest_result = results[0] if results else None
    highest_result = choose_highest_award(results)
    peak_rating = total_rating
    peak_level = get_competition_level(peak_rating)

    competition_profile.current_rating = total_rating
    competition_profile.current_level = current_level['slug']
    competition_profile.peak_rating = peak_rating
    competition_profile.peak_level = peak_level['slug']
    competition_profile.primary_color = current_level['color']
    competition_profile.highest_award_label = highest_result.display_award_label if highest_result else ''
    competition_profile.latest_contest_result = latest_result
    competition_profile.last_calculated_at = timezone.now()
    competition_profile.save()
    return competition_profile


def sync_members_competition_profiles(members):
    for member in members:
        sync_member_competition_profile(member)


def build_member_competition_snapshot(member):
    competition_profile = get_or_create_competition_profile(member)
    current_level = get_competition_level(competition_profile.current_rating)
    peak_level = get_competition_level(competition_profile.peak_rating)
    integrity_sanction = build_integrity_sanction_snapshot(member)
    all_results = list(get_member_verified_results(member))
    recent_results = all_results[:5]
    return {
        'profile': competition_profile,
        'level': current_level,
        'peak_level': peak_level,
        'integrity_sanction': integrity_sanction,
        'display_color': get_competition_display_color(
            competition_profile.primary_color,
            integrity_sanction=integrity_sanction,
        ),
        'recent_results': recent_results,
        'all_results': all_results,
    }


def build_competition_ladder_queryset():
    return (
        MemberCompetitionProfile.objects.select_related('member', 'member__user', 'latest_contest_result')
        .filter(member__status=MemberProfile.Status.ACTIVE)
        .order_by('-current_rating', 'member__real_name')
    )

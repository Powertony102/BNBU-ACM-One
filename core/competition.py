from decimal import Decimal

from django.utils import timezone

from .models import Contest, ContestResult, MemberCompetitionProfile, MemberProfile


LEVEL_WEIGHT_MAP = {
    Contest.Level.NATIONAL: Decimal('1.60'),
    Contest.Level.REGIONAL: Decimal('1.40'),
    Contest.Level.PROVINCIAL: Decimal('1.20'),
    Contest.Level.CAMPUS: Decimal('1.00'),
    Contest.Level.INTERNAL: Decimal('0.80'),
}

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
    total_rating = 0
    for result in results:
        new_delta = calculate_result_rating_delta(result, today=today)
        if result.rating_delta != new_delta:
            result.rating_delta = new_delta
            result.save(update_fields=['rating_delta'])
        total_rating += new_delta

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
    all_results = list(get_member_verified_results(member))
    recent_results = all_results[:5]
    return {
        'profile': competition_profile,
        'level': current_level,
        'peak_level': peak_level,
        'recent_results': recent_results,
        'all_results': all_results,
    }


def build_competition_ladder_queryset():
    return (
        MemberCompetitionProfile.objects.select_related('member', 'member__user', 'latest_contest_result')
        .filter(member__status=MemberProfile.Status.ACTIVE)
        .order_by('-current_rating', 'member__real_name')
    )

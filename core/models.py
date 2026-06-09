import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone


class User(AbstractUser):
    class Roles(models.TextChoices):
        MEMBER = 'member', 'Member'
        ADMIN = 'admin', 'Admin'
        SUPER_ADMIN = 'super_admin', 'Super Admin'

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.MEMBER,
    )

    def is_management(self):
        return self.role in {self.Roles.ADMIN, self.Roles.SUPER_ADMIN}

    def is_super_admin(self):
        return self.role == self.Roles.SUPER_ADMIN


class EmailVerificationCode(models.Model):
    class Purpose(models.TextChoices):
        PASSWORD_RESET = 'password_reset', 'Password Reset'
        PASSWORD_CHANGE = 'password_change', 'Password Change'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_verification_codes')
    email = models.EmailField()
    purpose = models.CharField(max_length=50, choices=Purpose.choices)
    code = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['purpose', 'email', 'created_at']),
            models.Index(fields=['user', 'purpose', 'created_at']),
        ]

    def __str__(self):
        return f'{self.user.username} · {self.purpose} · {self.email}'

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def is_available(self):
        return self.used_at is None and not self.is_expired()

    def mark_used(self):
        if self.used_at is None:
            self.used_at = timezone.now()
            self.save(update_fields=['used_at'])


class MemberProfile(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='member_profile')
    real_name = models.CharField(max_length=100)
    student_id = models.CharField(max_length=50, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    major = models.CharField(max_length=100, blank=True)
    enrollment_year = models.PositiveSmallIntegerField(null=True, blank=True)
    class_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.real_name} ({self.student_id})'

    def get_active_integrity_sanction(self, now=None):
        now = now or timezone.now()
        return (
            self.integrity_sanctions.filter(
                starts_at__lte=now,
                ends_at__gte=now,
                revoked_at__isnull=True,
            )
            .select_related('created_by', 'revoked_by')
            .order_by('-starts_at', '-id')
            .first()
        )

    def has_active_integrity_sanction(self, now=None):
        return self.get_active_integrity_sanction(now=now) is not None


class AdminProfile(models.Model):
    class Level(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        SUPER_ADMIN = 'super_admin', 'Super Admin'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')
    display_name = models.CharField(max_length=100)
    admin_level = models.CharField(max_length=20, choices=Level.choices, default=Level.ADMIN)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name


class EventSeries(models.Model):
    class SeriesType(models.TextChoices):
        TRAINING = 'training', '训练'
        LECTURE = 'lecture', '讲座'
        SHARING = 'sharing', '分享会'
        OTHER = 'other', '其他'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PUBLISHED = 'published', '已发布'
        ARCHIVED = 'archived', '已归档'

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    series_type = models.CharField(max_length=20, choices=SeriesType.choices, default=SeriesType.TRAINING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    expected_event_count = models.PositiveSmallIntegerField(default=10)
    required_checkins_for_rating = models.PositiveSmallIntegerField(default=1)
    rating_enabled = models.BooleanField(default=False)
    rating_points = models.IntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_event_series')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '系列活动'
        verbose_name_plural = '系列活动'

    def __str__(self):
        return self.title

    def clean(self):
        if self.required_checkins_for_rating < 1:
            raise ValidationError({'required_checkins_for_rating': '计入 rating 所需签到次数不能少于 1。'})
        if self.required_checkins_for_rating > self.expected_event_count:
            raise ValidationError({'required_checkins_for_rating': '计入 rating 所需签到次数不能超过预期活动总数。'})


class Event(models.Model):
    class EventType(models.TextChoices):
        TRAINING = 'training', '训练'
        LECTURE = 'lecture', '讲座'
        SHARING = 'sharing', '分享会'
        CONTEST = 'contest', '比赛'
        OTHER = 'other', '其他'

    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', '待审核'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PUBLISHED = 'published', '已发布'
        CHECKIN_CLOSED = 'checkin_closed', '签到关闭'
        CANCELED = 'canceled', '已作废'

    title = models.CharField(max_length=200)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.TRAINING)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200)
    series = models.ForeignKey(
        EventSeries,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='events',
    )
    series_order = models.PositiveSmallIntegerField(null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    checkin_start_time = models.DateTimeField()
    checkin_end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    applicant = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='applied_events',
    )
    checkin_managers = models.ManyToManyField(
        User,
        blank=True,
        related_name='managed_events',
    )
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.APPROVED,
    )
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_events',
    )
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_events')
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_time']
        constraints = [
            models.UniqueConstraint(
                fields=['series', 'series_order'],
                condition=models.Q(series__isnull=False),
                name='unique_series_order',
            ),
        ]

    def __str__(self):
        return self.title

    def is_checkin_open(self):
        now = timezone.now()
        return (
            self.review_status == self.ReviewStatus.APPROVED
            and self.status == self.Status.PUBLISHED
            and self.checkin_start_time <= now <= self.checkin_end_time
        )

    def is_visible_to_members(self):
        return self.review_status == self.ReviewStatus.APPROVED and self.status != self.Status.CANCELED

    def can_manage_checkin(self, user):
        if not getattr(user, 'is_authenticated', False):
            return False
        if user.is_management():
            return True
        if (
            user.role != User.Roles.MEMBER
            or self.review_status != self.ReviewStatus.APPROVED
            or self.status == self.Status.CANCELED
        ):
            return False
        if user.id == self.applicant_id:
            return True
        prefetched_managers = getattr(self, '_prefetched_objects_cache', {}).get('checkin_managers')
        if prefetched_managers is not None:
            return any(manager.id == user.id for manager in prefetched_managers)
        return self.checkin_managers.filter(id=user.id).exists()

    def get_checkin_manager_names(self):
        managers = getattr(self, '_prefetched_objects_cache', {}).get('checkin_managers')
        if managers is None:
            managers = self.checkin_managers.select_related('member_profile').all()
        return [manager.member_profile.real_name or manager.username for manager in managers]

    @property
    def checkin_manager_display(self):
        return '、'.join(self.get_checkin_manager_names()) or '未指定'

    def is_pending_review(self):
        return self.review_status == self.ReviewStatus.PENDING

    def is_member_application(self):
        return self.applicant_id is not None

    def get_absolute_url(self):
        return reverse('member-event-detail', args=[self.pk])


class Contest(models.Model):
    class Series(models.TextChoices):
        ICPC = 'icpc', 'ICPC'
        CCPC = 'ccpc', 'CCPC'
        PROVINCIAL = 'provincial', '省赛'
        INVITATIONAL = 'invitational', '邀请赛'
        CAMPUS = 'campus', '校赛'
        SELECTION = 'selection', '选拔赛'
        OTHER = 'other', '其他'

    class Level(models.TextChoices):
        NATIONAL = 'national', '国家级'
        REGIONAL = 'regional', '区域级'
        PROVINCIAL = 'provincial', '省级'
        CAMPUS = 'campus', '校级'
        INTERNAL = 'internal', '队内'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PUBLISHED = 'published', '已发布'
        ARCHIVED = 'archived', '已归档'

    name = models.CharField(max_length=200)
    series = models.CharField(max_length=20, choices=Series.choices, default=Series.OTHER)
    season = models.CharField(max_length=20, blank=True)
    stage = models.CharField(max_length=100, blank=True)
    contest_date = models.DateField()
    organizer = models.CharField(max_length=200, blank=True)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.CAMPUS)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1.00)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_contests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-contest_date', '-id']

    def __str__(self):
        if self.stage:
            return f'{self.name} · {self.stage}'
        return self.name


class ContestTeam(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='teams')
    team_name = models.CharField(max_length=200)
    members = models.ManyToManyField(MemberProfile, related_name='contest_teams', blank=True)
    external_member_names = models.CharField(max_length=255, blank=True)
    coach_name = models.CharField(max_length=100, blank=True)
    leader = models.ForeignKey(
        MemberProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='led_contest_teams',
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['contest__contest_date', 'team_name']
        constraints = [
            models.UniqueConstraint(fields=['contest', 'team_name'], name='unique_team_name_per_contest'),
        ]

    def __str__(self):
        return f'{self.contest.name} · {self.team_name}'

    @property
    def member_names(self):
        members = getattr(self, '_prefetched_objects_cache', {}).get('members')
        if members is None:
            members = self.members.all()
        names = [member.real_name for member in members]
        if self.external_member_names:
            names.extend(name.strip() for name in self.external_member_names.split('、') if name.strip())
        return '、'.join(names) or '未分配成员'


class ContestResult(models.Model):
    class AwardType(models.TextChoices):
        GOLD = 'gold', '金奖'
        SILVER = 'silver', '银奖'
        BRONZE = 'bronze', '铜奖'
        HONORABLE = 'honorable', '优胜奖'
        FINALIST = 'finalist', '入围'
        PARTICIPATION = 'participation', '参赛'
        CUSTOM = 'custom', '自定义'

    class ResultTier(models.TextChoices):
        CHAMPION = 'champion', '顶尖'
        HIGH = 'high', '高'
        MEDIUM = 'medium', '中'
        ENTRY = 'entry', '入门'

    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='results')
    team = models.ForeignKey(ContestTeam, on_delete=models.CASCADE, related_name='results')
    award_type = models.CharField(max_length=20, choices=AwardType.choices, default=AwardType.PARTICIPATION)
    award_label = models.CharField(max_length=100, blank=True)
    rank_label = models.CharField(max_length=100, blank=True)
    result_tier = models.CharField(max_length=20, choices=ResultTier.choices, default=ResultTier.ENTRY)
    manual_bonus = models.IntegerField(default=0)
    rating_delta = models.IntegerField(default=0)
    verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='verified_contest_results',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='revoked_contest_results',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    evidence_url = models.URLField(blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-contest__contest_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['contest', 'team'], name='unique_team_result_per_contest'),
        ]

    def __str__(self):
        return f'{self.contest.name} · {self.team.team_name}'

    def clean(self):
        if self.team_id and self.contest_id and self.team.contest_id != self.contest_id:
            raise ValidationError({'team': '所选队伍必须属于当前赛事。'})

    @property
    def display_award_label(self):
        return self.award_label or self.get_award_type_display()

    @property
    def is_revoked(self):
        return not self.verified and self.revoked_at is not None


class MemberCompetitionProfile(models.Model):
    member = models.OneToOneField(MemberProfile, on_delete=models.CASCADE, related_name='competition_profile')
    current_rating = models.IntegerField(default=0)
    current_level = models.CharField(max_length=30, default='unrated')
    peak_rating = models.IntegerField(default=0)
    peak_level = models.CharField(max_length=30, default='unrated')
    primary_color = models.CharField(max_length=20, default='#7d8b99')
    highest_award_label = models.CharField(max_length=100, blank=True)
    latest_contest_result = models.ForeignKey(
        ContestResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='latest_for_members',
    )
    last_calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-current_rating', 'member__real_name']

    def __str__(self):
        return f'{self.member.real_name} · {self.current_rating}'


class MemberIntegritySanction(models.Model):
    class ReasonType(models.TextChoices):
        CONTEST_NO_SHOW = 'contest_no_show', '报名后缺席比赛'
        SERIOUS_RULE_VIOLATION = 'serious_rule_violation', '活动中严重违反规则'
        OTHER = 'other', '其他'

    member = models.ForeignKey(
        MemberProfile,
        on_delete=models.CASCADE,
        related_name='integrity_sanctions',
    )
    reason_type = models.CharField(max_length=40, choices=ReasonType.choices, default=ReasonType.OTHER)
    member_reason = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_integrity_sanctions',
    )
    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='revoked_integrity_sanctions',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_at', '-id']

    def __str__(self):
        return f'{self.member.real_name} · {self.get_reason_type_display()}'

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': '处罚截止时间必须晚于生效时间。'})

    @property
    def public_notice(self):
        return '违反 ACM 准则'

    def is_active_at(self, moment=None):
        moment = moment or timezone.now()
        return self.revoked_at is None and self.starts_at <= moment <= self.ends_at


class MemberTeam(models.Model):
    name = models.CharField(max_length=200)
    members = models.ManyToManyField(MemberProfile, related_name='member_teams', blank=True)
    captain = models.ForeignKey(
        MemberProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='captained_member_teams',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_member_teams',
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='updated_member_teams',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    @property
    def member_names(self):
        members = getattr(self, '_prefetched_objects_cache', {}).get('members')
        if members is None:
            members = self.members.all()
        return '、'.join(member.real_name for member in members) or '未分配成员'

    @property
    def member_count(self):
        members = getattr(self, '_prefetched_objects_cache', {}).get('members')
        if members is None:
            return self.members.count()
        return len(members)


class MemberTeamSubmission(models.Model):
    class ActionType(models.TextChoices):
        CREATE = 'create', '新增队伍'
        EDIT = 'edit', '编辑队伍'

    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', '待审核'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'

    applicant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='member_team_submissions',
    )
    action_type = models.CharField(max_length=20, choices=ActionType.choices, default=ActionType.CREATE)
    target_team = models.ForeignKey(
        MemberTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pending_submissions',
    )
    team_name = models.CharField(max_length=200)
    members = models.ManyToManyField(MemberProfile, related_name='member_team_submissions', blank=True)
    captain = models.ForeignKey(
        MemberProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='captain_member_team_submissions',
    )
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_member_team_submissions',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_team = models.ForeignKey(
        MemberTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submission_records',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['target_team'],
                condition=models.Q(review_status='pending', target_team__isnull=False),
                name='unique_pending_member_team_submission_per_team',
            ),
        ]

    def __str__(self):
        return f'{self.get_action_type_display()} · {self.team_name}'


class ContestSubmission(models.Model):
    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', '待审核'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'
        WITHDRAWN = 'withdrawn', '已撤回'

    applicant = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contest_submissions')
    contest_name = models.CharField(max_length=200)
    contest_series = models.CharField(max_length=20, choices=Contest.Series.choices, default=Contest.Series.OTHER)
    contest_season = models.CharField(max_length=20, blank=True)
    contest_stage = models.CharField(max_length=100, blank=True)
    contest_date = models.DateField()
    organizer = models.CharField(max_length=200, blank=True)
    contest_level = models.CharField(max_length=20, choices=Contest.Level.choices, default=Contest.Level.CAMPUS)
    linked_member_team = models.ForeignKey(
        MemberTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='contest_submissions',
    )
    team_name = models.CharField(max_length=200, blank=True)
    team_members = models.ManyToManyField(MemberProfile, related_name='contest_submissions', blank=True)
    external_teammates = models.CharField(max_length=255, blank=True)
    award_type = models.CharField(max_length=20, choices=ContestResult.AwardType.choices, default=ContestResult.AwardType.PARTICIPATION)
    award_label = models.CharField(max_length=100, blank=True)
    rank_label = models.CharField(max_length=100, blank=True)
    result_tier = models.CharField(max_length=20, choices=ContestResult.ResultTier.choices, default=ContestResult.ResultTier.ENTRY)
    evidence_url = models.URLField(blank=True)
    submission_note = models.TextField(blank=True)
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_contest_submissions',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_contest = models.ForeignKey(
        Contest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submissions',
    )
    resolved_team = models.ForeignKey(
        ContestTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submissions',
    )
    resolved_result = models.ForeignKey(
        ContestResult,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submissions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.applicant.username} · {self.contest_name}'


class EventQRCode(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='qr_codes')
    token = models.CharField(max_length=64, unique=True, editable=False)
    url = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_qr_codes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def is_valid(self):
        return self.is_active and self.is_valid_at(timezone.now())

    def is_valid_at(self, moment):
        if moment < self.created_at:
            return False
        if self.expires_at and moment > self.expires_at:
            return False
        if self.deactivated_at and moment > self.deactivated_at:
            return False
        return True

    def mark_inactive(self, deactivated_at=None):
        deactivated_at = deactivated_at or timezone.now()
        update_fields = []
        if self.is_active:
            self.is_active = False
            update_fields.append('is_active')
        if self.deactivated_at is None:
            self.deactivated_at = deactivated_at
            update_fields.append('deactivated_at')
        if update_fields:
            self.save(update_fields=update_fields)

    def get_refresh_deadline(self, refresh_seconds):
        return self.created_at + timedelta(seconds=refresh_seconds)

    def get_entry_path(self):
        return reverse('qr-entry', args=[self.token])


class CheckInRecord(models.Model):
    class Method(models.TextChoices):
        WEB = 'web', 'Web'
        QR = 'qr', 'QR'
        MANUAL = 'manual', 'Manual'

    class Status(models.TextChoices):
        VALID = 'valid', 'Valid'
        REVOKED = 'revoked', 'Revoked'

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='checkins')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='checkins')
    checkin_time = models.DateTimeField(default=timezone.now)
    checkin_method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID)
    source_qr_code = models.ForeignKey(
        EventQRCode,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='checkins',
    )
    remark = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_checkins',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-checkin_time']

    def __str__(self):
        return f'{self.member} - {self.event}'


class EventSeriesCompletion(models.Model):
    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='series_completions')
    series = models.ForeignKey(EventSeries, on_delete=models.CASCADE, related_name='completions')
    valid_checkin_count = models.PositiveSmallIntegerField(default=0)
    is_completed_for_rating = models.BooleanField(default=False)
    rating_delta = models.IntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_counted_checkin_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['member', 'series'], name='unique_member_series_completion'),
        ]

    def __str__(self):
        status = '达标' if self.is_completed_for_rating else '进行中'
        return f'{self.member} · {self.series.title} ({status})'


class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

    @classmethod
    def get_value(cls, key, default=None):
        setting = cls.objects.filter(key=key).first()
        return setting.value if setting else default


class AuditLog(models.Model):
    operator = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} @ {self.created_at:%Y-%m-%d %H:%M}'

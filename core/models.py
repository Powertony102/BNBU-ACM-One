import secrets

from django.contrib.auth.models import AbstractUser
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


class Event(models.Model):
    class EventType(models.TextChoices):
        TRAINING = 'training', '训练'
        LECTURE = 'lecture', '讲座'
        SHARING = 'sharing', '分享会'
        CONTEST = 'contest', '比赛'
        OTHER = 'other', '其他'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PUBLISHED = 'published', '已发布'
        CHECKIN_CLOSED = 'checkin_closed', '签到关闭'
        CANCELED = 'canceled', '已作废'

    title = models.CharField(max_length=200)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.TRAINING)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    checkin_start_time = models.DateTimeField()
    checkin_end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_events')
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return self.title

    def is_checkin_open(self):
        now = timezone.now()
        return (
            self.status == self.Status.PUBLISHED
            and self.checkin_start_time <= now <= self.checkin_end_time
        )

    def get_absolute_url(self):
        return reverse('member-event-detail', args=[self.pk])


class EventQRCode(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='qr_codes')
    token = models.CharField(max_length=64, unique=True, editable=False)
    url = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_qr_codes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def is_valid(self):
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

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

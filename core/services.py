import math
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailVerificationCode


def normalize_email(value):
    return value.strip().lower()


def generate_numeric_code(length=6):
    return ''.join(secrets.choice(string.digits) for _ in range(length))


def password_reset_code_expires_in_minutes():
    return max(1, math.ceil(settings.PASSWORD_RESET_CODE_TTL_SECONDS / 60))


def issue_email_verification_code(user, email, purpose):
    email = normalize_email(email)
    now = timezone.now()
    recent_code = (
        EmailVerificationCode.objects.filter(
            user=user,
            email=email,
            purpose=purpose,
        )
        .order_by('-created_at')
        .first()
    )
    if recent_code:
        elapsed_seconds = (now - recent_code.created_at).total_seconds()
        cooldown_seconds = settings.PASSWORD_RESET_CODE_COOLDOWN_SECONDS
        if elapsed_seconds < cooldown_seconds:
            remaining_seconds = int(math.ceil(cooldown_seconds - elapsed_seconds))
            raise ValidationError(f'验证码发送过于频繁，请在 {remaining_seconds} 秒后重试。')

    EmailVerificationCode.objects.filter(
        user=user,
        email=email,
        purpose=purpose,
        used_at__isnull=True,
        expires_at__gt=now,
    ).update(expires_at=now)

    raw_code = generate_numeric_code()
    verification = EmailVerificationCode.objects.create(
        user=user,
        email=email,
        purpose=purpose,
        code=make_password(raw_code),
        expires_at=now + timedelta(seconds=settings.PASSWORD_RESET_CODE_TTL_SECONDS),
    )
    return verification, raw_code


def issue_password_reset_code(user, email):
    return issue_email_verification_code(user, email, EmailVerificationCode.Purpose.PASSWORD_RESET)


def issue_password_change_code(user, email):
    return issue_email_verification_code(user, email, EmailVerificationCode.Purpose.PASSWORD_CHANGE)


def send_email_verification_code_email(user, email, raw_code, subject, action_label):
    context = {
        'username': user.username,
        'code': raw_code,
        'expires_in_minutes': password_reset_code_expires_in_minutes(),
        'action_label': action_label,
    }
    text_body = render_to_string('emails/password_reset_code.txt', context)
    html_body = render_to_string('emails/password_reset_code.html', context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, 'text/html')
    message.send(fail_silently=False)


def send_password_reset_code_email(user, email, raw_code):
    send_email_verification_code_email(
        user,
        email,
        raw_code,
        'One BNBU-ACM 密码重置验证码',
        '重置密码',
    )


def send_password_change_code_email(user, email, raw_code):
    send_email_verification_code_email(
        user,
        email,
        raw_code,
        'One BNBU-ACM 修改密码验证码',
        '修改密码',
    )


def verify_email_verification_code(user, email, raw_code, purpose):
    email = normalize_email(email)
    verification = (
        EmailVerificationCode.objects.filter(
            user=user,
            email=email,
            purpose=purpose,
        )
        .order_by('-created_at')
        .first()
    )
    if not verification or verification.used_at is not None or verification.is_expired():
        raise ValidationError('验证码不存在或已过期，请重新获取。')
    if verification.attempt_count >= settings.PASSWORD_RESET_CODE_MAX_ATTEMPTS:
        raise ValidationError('验证码尝试次数过多，请重新获取。')
    if not check_password(raw_code, verification.code):
        verification.attempt_count += 1
        verification.save(update_fields=['attempt_count'])
        if verification.attempt_count >= settings.PASSWORD_RESET_CODE_MAX_ATTEMPTS:
            raise ValidationError('验证码错误次数过多，请重新获取。')
        raise ValidationError('验证码不正确。')
    return verification


def verify_password_reset_code(user, email, raw_code):
    return verify_email_verification_code(
        user,
        email,
        raw_code,
        EmailVerificationCode.Purpose.PASSWORD_RESET,
    )


def verify_password_change_code(user, email, raw_code):
    return verify_email_verification_code(
        user,
        email,
        raw_code,
        EmailVerificationCode.Purpose.PASSWORD_CHANGE,
    )


def invalidate_email_verification_codes(user, email, purpose, exclude_id=None):
    email = normalize_email(email)
    queryset = EmailVerificationCode.objects.filter(
        user=user,
        email=email,
        purpose=purpose,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    )
    if exclude_id is not None:
        queryset = queryset.exclude(id=exclude_id)
    queryset.update(expires_at=timezone.now())


def invalidate_password_reset_codes(user, email, exclude_id=None):
    invalidate_email_verification_codes(
        user,
        email,
        EmailVerificationCode.Purpose.PASSWORD_RESET,
        exclude_id=exclude_id,
    )


def invalidate_password_change_codes(user, email, exclude_id=None):
    invalidate_email_verification_codes(
        user,
        email,
        EmailVerificationCode.Purpose.PASSWORD_CHANGE,
        exclude_id=exclude_id,
    )

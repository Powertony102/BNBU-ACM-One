from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AdminProfile, AuditLog, CheckInRecord, EmailVerificationCode, Event, EventQRCode, MemberProfile, SystemSetting, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Role', {'fields': ('role',)}),
    )
    list_display = ('username', 'email', 'role', 'is_active', 'is_staff')
    list_filter = ('role', 'is_active', 'is_staff')


@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    list_display = ('real_name', 'student_id', 'enrollment_year', 'major', 'status', 'user')
    search_fields = ('real_name', 'student_id', 'user__username')


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'admin_level', 'status', 'user')
    search_fields = ('display_name', 'user__username')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_type', 'status', 'start_time', 'location')
    list_filter = ('status', 'event_type')
    search_fields = ('title', 'location')


@admin.register(EventQRCode)
class EventQRCodeAdmin(admin.ModelAdmin):
    list_display = ('event', 'token', 'is_active', 'expires_at', 'created_at')


@admin.register(CheckInRecord)
class CheckInRecordAdmin(admin.ModelAdmin):
    list_display = ('member', 'event', 'checkin_method', 'status', 'checkin_time')
    list_filter = ('checkin_method', 'status')


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'updated_at')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'target_type', 'target_id', 'operator', 'created_at')


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'email', 'purpose', 'attempt_count', 'expires_at', 'used_at', 'created_at')
    list_filter = ('purpose', 'used_at')
    search_fields = ('user__username', 'email')

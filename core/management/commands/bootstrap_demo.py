from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import AdminProfile, Event, MemberProfile, SystemSetting, User


class Command(BaseCommand):
    help = 'Bootstrap demo data for the One BNBU-ACM prototype.'

    def handle(self, *args, **options):
        self.seed_settings()
        admin_user = self.seed_admin()
        member_user = self.seed_member()
        self.seed_event(admin_user)
        self.stdout.write(self.style.SUCCESS('Prototype demo data is ready.'))
        self.stdout.write('Super Admin: superadmin / ACM123456')
        self.stdout.write('Member: member01 / ACM123456')

    def seed_settings(self):
        SystemSetting.objects.update_or_create(
            key='star_recent_window_days',
            defaults={'value': '30'},
        )
        SystemSetting.objects.update_or_create(
            key='qr_code_expire_minutes',
            defaults={'value': '120'},
        )

    def seed_admin(self):
        user, created = User.objects.update_or_create(
            username='superadmin',
            defaults={
                'role': User.Roles.SUPER_ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            },
        )
        user.set_password('ACM123456')
        user.save()
        AdminProfile.objects.update_or_create(
            user=user,
            defaults={
                'display_name': 'ACM 超级管理员',
                'admin_level': AdminProfile.Level.SUPER_ADMIN,
                'status': AdminProfile.Status.ACTIVE,
            },
        )
        if created:
            self.stdout.write('Created superadmin account.')
        return user

    def seed_member(self):
        user, created = User.objects.update_or_create(
            username='member01',
            defaults={
                'role': User.Roles.MEMBER,
                'is_active': True,
            },
        )
        user.set_password('ACM123456')
        user.save()
        MemberProfile.objects.update_or_create(
            user=user,
            defaults={
                'real_name': '示例队员',
                'student_id': '20260001',
                'email': 'member01@example.com',
                'phone': '18800000000',
                'major': 'Computer Science',
                'class_name': 'ACM Prototype Team',
                'status': MemberProfile.Status.ACTIVE,
            },
        )
        if created:
            self.stdout.write('Created member account.')
        return user

    def seed_event(self, admin_user):
        now = timezone.now()
        Event.objects.update_or_create(
            title='One BNBU-ACM 原型演示训练',
            defaults={
                'event_type': Event.EventType.TRAINING,
                'description': '用于验证活动管理、签到和 ACM Star 链路的演示活动。',
                'location': 'ACM Lab 301',
                'start_time': now + timedelta(hours=1),
                'end_time': now + timedelta(hours=3),
                'checkin_start_time': now - timedelta(minutes=30),
                'checkin_end_time': now + timedelta(hours=2),
                'status': Event.Status.PUBLISHED,
                'created_by': admin_user,
                'published_at': now,
            },
        )

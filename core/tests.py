from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AdminProfile, CheckInRecord, Event, MemberProfile, User


class ACMStarViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member_user = User.objects.create_user(
            username='member-star',
            password='ACM123456',
            role=User.Roles.MEMBER,
        )
        cls.member_profile = MemberProfile.objects.create(
            user=cls.member_user,
            real_name='星标队员',
            student_id='20261234',
            major='Computer Science',
            class_name='ACM 2026',
            status=MemberProfile.Status.ACTIVE,
        )
        cls.admin_user = User.objects.create_user(
            username='admin-star',
            password='ACM123456',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=cls.admin_user,
            display_name='统计管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        event = Event.objects.create(
            title='ACM Star 训练营',
            event_type=Event.EventType.TRAINING,
            description='用于测试 ACM Star 统计。',
            location='Lab 301',
            start_time=timezone.now() - timedelta(days=3),
            end_time=timezone.now() - timedelta(days=3) + timedelta(hours=2),
            checkin_start_time=timezone.now() - timedelta(days=3, minutes=30),
            checkin_end_time=timezone.now() - timedelta(days=3) + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            created_by=cls.admin_user,
            published_at=timezone.now() - timedelta(days=4),
        )
        second_event = Event.objects.create(
            title='ACM Star 分享会',
            event_type=Event.EventType.SHARING,
            description='用于测试等级计算。',
            location='Lab 302',
            start_time=timezone.now() - timedelta(days=1),
            end_time=timezone.now() - timedelta(days=1) + timedelta(hours=2),
            checkin_start_time=timezone.now() - timedelta(days=1, minutes=30),
            checkin_end_time=timezone.now() - timedelta(days=1) + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            created_by=cls.admin_user,
            published_at=timezone.now() - timedelta(days=2),
        )
        CheckInRecord.objects.create(
            member=cls.member_profile,
            event=event,
            checkin_time=timezone.now() - timedelta(days=3),
            checkin_method=CheckInRecord.Method.WEB,
            status=CheckInRecord.Status.VALID,
            created_by=cls.member_user,
        )
        CheckInRecord.objects.create(
            member=cls.member_profile,
            event=second_event,
            checkin_time=timezone.now() - timedelta(days=1),
            checkin_method=CheckInRecord.Method.QR,
            status=CheckInRecord.Status.VALID,
            created_by=cls.member_user,
        )

    def test_member_star_center_displays_brand_status(self):
        self.client.login(username='member-star', password='ACM123456')
        response = self.client.get(reverse('member-star-center'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ACM Star')
        self.assertContains(response, 'Pulse')
        self.assertContains(response, '近期点亮记录')

    def test_management_star_analytics_shows_holder_stats(self):
        self.client.login(username='admin-star', password='ACM123456')
        response = self.client.get(reverse('management-star-analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ACM Star 拥有者统计')
        self.assertContains(response, '星标队员')
        self.assertContains(response, 'Pulse')

    def test_member_cannot_access_management_star_analytics(self):
        self.client.login(username='member-star', password='ACM123456')
        response = self.client.get(reverse('management-star-analytics'))
        self.assertEqual(response.status_code, 403)


class MemberRegistrationTests(TestCase):
    def test_register_page_shows_required_examples(self):
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'placeholder="2330026083"', html=False)
        self.assertContains(response, 't330026083@mail.bnbu.edu.cn')

    def test_register_member_creates_hashed_password_and_profile(self):
        response = self.client.post(
            reverse('register'),
            {
                'real_name': '张三',
                'username': '2330026083',
                'enrollment_year': 2023,
                'major': 'cst',
                'school_email': 't330026083@mail.bnbu.edu.cn',
                'password1': 'AcmTeam2026!',
                'password2': 'AcmTeam2026!',
            },
            follow=True,
        )
        self.assertRedirects(response, reverse('member-dashboard'))
        user = User.objects.get(username='2330026083')
        self.assertNotEqual(user.password, 'AcmTeam2026!')
        self.assertTrue(user.check_password('AcmTeam2026!'))
        self.assertEqual(user.email, 't330026083@mail.bnbu.edu.cn')
        self.assertEqual(user.role, User.Roles.MEMBER)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

        profile = user.member_profile
        self.assertEqual(profile.real_name, '张三')
        self.assertEqual(profile.student_id, '2330026083')
        self.assertEqual(profile.enrollment_year, 2023)
        self.assertEqual(profile.major, 'CST')
        self.assertEqual(profile.email, 't330026083@mail.bnbu.edu.cn')

    def test_register_member_rejects_invalid_username_and_email_domain(self):
        response = self.client.post(
            reverse('register'),
            {
                'real_name': '李四',
                'username': 'abc123',
                'enrollment_year': 2024,
                'major': 'DS',
                'school_email': 'student@example.com',
                'password1': 'AcmTeam2026!',
                'password2': 'AcmTeam2026!',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '用户名必须是 10 位纯数字。')
        self.assertContains(response, '学校邮箱必须使用 @mail.bnbu.edu.cn 域名。')
        self.assertFalse(User.objects.filter(username='abc123').exists())

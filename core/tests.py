from datetime import timedelta
import re
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .competition import sync_member_competition_profile
from .forms import ContestTeamForm
from .models import (
    AdminProfile,
    CheckInRecord,
    Contest,
    ContestResult,
    ContestSubmission,
    ContestTeam,
    EmailVerificationCode,
    Event,
    EventQRCode,
    MemberCompetitionProfile,
    MemberProfile,
    User,
)


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

    def test_member_pages_hide_class_name(self):
        self.client.login(username='member-star', password='ACM123456')

        dashboard_response = self.client.get(reverse('member-dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertNotContains(dashboard_response, 'ACM 2026')
        self.assertNotContains(dashboard_response, '暂未填写班级')

        profile_response = self.client.get(reverse('member-profile'))
        self.assertEqual(profile_response.status_code, 200)
        self.assertNotContains(profile_response, '班级')
        self.assertNotContains(profile_response, 'class_name')

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


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='2430026137',
            password='OldPassword2026!',
            email='u430026137@mail.bnbu.edu.cn',
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=self.user,
            real_name='刘扬',
            student_id='2430026137',
            email='u430026137@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )

    def test_login_page_exposes_password_reset_entry(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '通过邮箱验证码找回')

    def test_request_password_reset_code_sends_email_and_creates_record(self):
        response = self.client.post(
            reverse('password-reset-request'),
            {
                'username': '2430026137',
                'school_email': 'u430026137@mail.bnbu.edu.cn',
            },
            follow=True,
        )
        self.assertRedirects(
            response,
            reverse('password-reset-confirm') + '?username=2430026137&email=u430026137%40mail.bnbu.edu.cn',
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['u430026137@mail.bnbu.edu.cn'])
        self.assertIn('密码重置验证码', mail.outbox[0].subject)

        verification = EmailVerificationCode.objects.get(user=self.user)
        self.assertEqual(verification.purpose, EmailVerificationCode.Purpose.PASSWORD_RESET)
        self.assertTrue(verification.is_available())

    def test_confirm_password_reset_accepts_valid_code_and_updates_password(self):
        self.client.post(
            reverse('password-reset-request'),
            {
                'username': '2430026137',
                'school_email': 'u430026137@mail.bnbu.edu.cn',
            },
        )
        message_body = mail.outbox[0].body
        code = re.search(r'验证码：(\d{6})', message_body).group(1)

        response = self.client.post(
            reverse('password-reset-confirm'),
            {
                'username': '2430026137',
                'school_email': 'u430026137@mail.bnbu.edu.cn',
                'code': code,
                'password1': 'NewPassword2026!',
                'password2': 'NewPassword2026!',
            },
            follow=True,
        )
        self.assertRedirects(response, reverse('login'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassword2026!'))
        self.assertFalse(self.user.check_password('OldPassword2026!'))
        self.assertTrue(
            self.client.login(
                username='2430026137',
                password='NewPassword2026!',
            )
        )
        verification = EmailVerificationCode.objects.get(user=self.user)
        self.assertIsNotNone(verification.used_at)

    def test_confirm_password_reset_rejects_invalid_code(self):
        self.client.post(
            reverse('password-reset-request'),
            {
                'username': '2430026137',
                'school_email': 'u430026137@mail.bnbu.edu.cn',
            },
        )
        response = self.client.post(
            reverse('password-reset-confirm'),
            {
                'username': '2430026137',
                'school_email': 'u430026137@mail.bnbu.edu.cn',
                'code': '000000',
                'password1': 'NewPassword2026!',
                'password2': 'NewPassword2026!',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '验证码不正确。')

        verification = EmailVerificationCode.objects.get(user=self.user)
        self.assertEqual(verification.attempt_count, 1)


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.member_user = User.objects.create_user(
            username='2430026137',
            password='OldPassword2026!',
            email='u430026137@mail.bnbu.edu.cn',
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=self.member_user,
            real_name='刘扬',
            student_id='2430026137',
            email='u430026137@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        self.admin_user = User.objects.create_user(
            username='admin01',
            password='AdminPassword2026!',
            email='admin01@mail.bnbu.edu.cn',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='运营管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )

    def test_member_can_change_password_while_staying_logged_in(self):
        self.client.login(username='2430026137', password='OldPassword2026!')

        send_response = self.client.post(
            reverse('password-change-request'),
            follow=True,
        )
        self.assertRedirects(send_response, reverse('password-change-confirm'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('修改密码验证码', mail.outbox[0].subject)
        code = re.search(r'验证码：(\d{6})', mail.outbox[0].body).group(1)

        confirm_response = self.client.post(
            reverse('password-change-confirm'),
            {
                'code': code,
                'password1': 'BrandNewPassword2026!',
                'password2': 'BrandNewPassword2026!',
            },
            follow=True,
        )
        self.assertRedirects(confirm_response, reverse('member-dashboard'))
        self.assertTrue(confirm_response.wsgi_request.user.is_authenticated)

        self.member_user.refresh_from_db()
        self.assertTrue(self.member_user.check_password('BrandNewPassword2026!'))
        self.client.post(reverse('logout'))
        self.assertTrue(self.client.login(username='2430026137', password='BrandNewPassword2026!'))

        verification = EmailVerificationCode.objects.get(
            user=self.member_user,
            purpose=EmailVerificationCode.Purpose.PASSWORD_CHANGE,
        )
        self.assertIsNotNone(verification.used_at)

    def test_management_pages_show_password_change_entry(self):
        self.client.login(username='admin01', password='AdminPassword2026!')
        response = self.client.get(reverse('management-dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('password-change-request'))
        self.assertContains(response, '修改密码')


class EventApplicationWorkflowTests(TestCase):
    def setUp(self):
        self.member_user = User.objects.create_user(
            username='2430026001',
            password='MemberPassword2026!',
            email='u430026001@mail.bnbu.edu.cn',
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=self.member_user,
            real_name='申请队员',
            student_id='2430026001',
            email='u430026001@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        self.other_member_user = User.objects.create_user(
            username='2430026002',
            password='MemberPassword2026!',
            email='u430026002@mail.bnbu.edu.cn',
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=self.other_member_user,
            real_name='普通队员',
            student_id='2430026002',
            email='u430026002@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        self.admin_user = User.objects.create_user(
            username='admin-review',
            password='AdminPassword2026!',
            email='admin-review@mail.bnbu.edu.cn',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='审核管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        self.application_payload = {
            'title': '队员专题分享',
            'event_type': Event.EventType.SHARING,
            'description': '成员申请举办算法专题分享。',
            'location': 'Lab 405',
            'start_time': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
            'end_time': (timezone.now() + timedelta(days=3, hours=2)).strftime('%Y-%m-%dT%H:%M'),
            'checkin_start_time': (timezone.now() + timedelta(days=3, minutes=-15)).strftime('%Y-%m-%dT%H:%M'),
            'checkin_end_time': (timezone.now() + timedelta(days=3, hours=1)).strftime('%Y-%m-%dT%H:%M'),
        }

    def create_pending_application(self):
        return Event.objects.create(
            title='成员发起活动',
            event_type=Event.EventType.LECTURE,
            description='等待审核的活动申请。',
            location='Room 101',
            start_time=timezone.now() + timedelta(days=5),
            end_time=timezone.now() + timedelta(days=5, hours=2),
            checkin_start_time=timezone.now() + timedelta(days=5, minutes=-20),
            checkin_end_time=timezone.now() + timedelta(days=5, hours=1),
            status=Event.Status.DRAFT,
            applicant=self.member_user,
            review_status=Event.ReviewStatus.PENDING,
            created_by=self.member_user,
        )

    def create_live_event(self, title='动态签到活动'):
        now = timezone.now()
        return Event.objects.create(
            title=title,
            event_type=Event.EventType.TRAINING,
            description='用于测试动态二维码签到。',
            location='Lab 305',
            start_time=now - timedelta(minutes=20),
            end_time=now + timedelta(hours=2),
            checkin_start_time=now - timedelta(minutes=15),
            checkin_end_time=now + timedelta(minutes=45),
            status=Event.Status.PUBLISHED,
            applicant=self.member_user,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now - timedelta(hours=1),
            created_by=self.admin_user,
            published_at=now - timedelta(hours=1),
        )

    def test_member_can_submit_event_application(self):
        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.post(
            reverse('member-event-apply'),
            self.application_payload,
            follow=True,
        )
        self.assertRedirects(response, reverse('member-event-list'))

        event = Event.objects.get(title='队员专题分享')
        self.assertEqual(event.applicant, self.member_user)
        self.assertEqual(event.created_by, self.member_user)
        self.assertEqual(event.review_status, Event.ReviewStatus.PENDING)
        self.assertEqual(event.status, Event.Status.DRAFT)
        self.assertContains(response, '队员专题分享')
        self.assertContains(response, '待审核')

    def test_admin_approval_grants_event_scoped_checkin_management(self):
        event = self.create_pending_application()

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-review', args=[event.id]),
            {
                'review_note': '可以开展，按计划执行。',
                'decision': 'approve',
            },
            follow=True,
        )
        self.assertRedirects(response, reverse('event-detail-manage', args=[event.id]))

        event.refresh_from_db()
        self.assertEqual(event.review_status, Event.ReviewStatus.APPROVED)
        self.assertEqual(event.status, Event.Status.PUBLISHED)
        self.assertEqual(event.reviewed_by, self.admin_user)
        self.assertQuerysetEqual(
            event.checkin_managers.order_by('id'),
            [self.member_user],
            transform=lambda user: user,
        )
        self.assertIsNotNone(event.published_at)

        self.client.logout()
        self.client.login(username='2430026001', password='MemberPassword2026!')

        detail_response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, '签到入口')

        qr_response = self.client.post(reverse('event-generate-qr', args=[event.id]), follow=True)
        self.assertRedirects(qr_response, reverse('event-detail-manage', args=[event.id]))
        event.refresh_from_db()
        self.assertEqual(event.qr_codes.count(), 1)

        edit_response = self.client.get(reverse('event-edit', args=[event.id]))
        self.assertEqual(edit_response.status_code, 403)

    def test_non_applicant_member_cannot_access_event_scoped_checkin_management(self):
        event = self.create_pending_application()
        event.review_status = Event.ReviewStatus.APPROVED
        event.status = Event.Status.PUBLISHED
        event.reviewed_by = self.admin_user
        event.reviewed_at = timezone.now()
        event.published_at = timezone.now()
        event.save(
            update_fields=[
                'review_status',
                'status',
                'reviewed_by',
                'reviewed_at',
                'published_at',
                'updated_at',
            ]
        )

        self.client.login(username='2430026002', password='MemberPassword2026!')
        response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(response.status_code, 403)
        manual_response = self.client.post(
            reverse('event-manual-checkin', args=[event.id]),
            {
                'member_keyword': '2430026002',
                'remark': '尝试越权补签',
            },
        )
        self.assertEqual(manual_response.status_code, 403)

    def test_rejected_application_does_not_grant_checkin_management(self):
        event = self.create_pending_application()

        self.client.login(username='admin-review', password='AdminPassword2026!')
        self.client.post(
            reverse('event-review', args=[event.id]),
            {
                'review_note': '时间安排不够清晰，请补充后再申请。',
                'decision': 'reject',
            },
            follow=True,
        )

        event.refresh_from_db()
        self.assertEqual(event.review_status, Event.ReviewStatus.REJECTED)

        self.client.logout()
        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(response.status_code, 403)

        list_response = self.client.get(reverse('member-event-list'))
        self.assertContains(list_response, '已驳回')
        self.assertContains(list_response, '时间安排不够清晰，请补充后再申请。')

    def test_approved_applicant_can_manual_checkin_and_revoke(self):
        event = self.create_pending_application()
        event.review_status = Event.ReviewStatus.APPROVED
        event.status = Event.Status.PUBLISHED
        event.reviewed_by = self.admin_user
        event.reviewed_at = timezone.now()
        event.published_at = timezone.now()
        event.save(
            update_fields=[
                'review_status',
                'status',
                'reviewed_by',
                'reviewed_at',
                'published_at',
                'updated_at',
            ]
        )

        self.client.login(username='2430026001', password='MemberPassword2026!')
        manual_response = self.client.post(
            reverse('event-manual-checkin', args=[event.id]),
            {
                'member_keyword': '2430026002',
                'remark': '线下已确认到场。',
            },
            follow=True,
        )
        self.assertRedirects(manual_response, reverse('event-detail-manage', args=[event.id]))

        checkin = CheckInRecord.objects.get(event=event, member__user=self.other_member_user)
        self.assertEqual(checkin.checkin_method, CheckInRecord.Method.MANUAL)
        self.assertEqual(checkin.status, CheckInRecord.Status.VALID)
        self.assertEqual(checkin.created_by, self.member_user)
        self.assertEqual(checkin.remark, '线下已确认到场。')
        self.assertContains(manual_response, '完整签到名单')
        self.assertContains(manual_response, '线下已确认到场。')

        revoke_response = self.client.post(
            reverse('event-revoke-checkin', args=[event.id, checkin.id]),
            follow=True,
        )
        self.assertRedirects(revoke_response, reverse('event-detail-manage', args=[event.id]))
        checkin.refresh_from_db()
        self.assertEqual(checkin.status, CheckInRecord.Status.REVOKED)
        self.assertContains(revoke_response, '已撤销')

    def test_admin_created_event_can_assign_member_checkin_manager(self):
        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-create'),
            {
                'title': '管理员创建活动',
                'event_type': Event.EventType.TRAINING,
                'description': '管理员创建并指定成员代管。',
                'location': 'Lab 501',
                'start_time': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_time': (timezone.now() + timedelta(days=2, hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (timezone.now() + timedelta(days=2, minutes=-10)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (timezone.now() + timedelta(days=2, hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_managers': [str(self.member_user.id), str(self.other_member_user.id)],
                'status': Event.Status.PUBLISHED,
            },
            follow=True,
        )
        event = Event.objects.get(title='管理员创建活动')
        self.assertRedirects(response, reverse('event-detail-manage', args=[event.id]))
        self.assertQuerysetEqual(
            event.checkin_managers.order_by('id'),
            [self.member_user, self.other_member_user],
            transform=lambda user: user,
        )
        self.assertEqual(event.review_status, Event.ReviewStatus.APPROVED)
        self.assertContains(response, '签到管理员')
        self.assertContains(response, '申请队员')
        self.assertContains(response, '普通队员')

        self.client.logout()
        self.client.login(username='2430026002', password='MemberPassword2026!')
        detail_response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(detail_response.status_code, 200)

        qr_response = self.client.post(reverse('event-generate-qr', args=[event.id]), follow=True)
        self.assertRedirects(qr_response, reverse('event-detail-manage', args=[event.id]))
        event.refresh_from_db()
        self.assertEqual(event.qr_codes.count(), 1)

        edit_response = self.client.get(reverse('event-edit', args=[event.id]))
        self.assertEqual(edit_response.status_code, 403)

    def test_event_form_rejects_more_than_five_checkin_managers(self):
        extra_members = []
        for index in range(3, 8):
            user = User.objects.create_user(
                username=f'24300260{index:02d}',
                password='MemberPassword2026!',
                email=f'u4300260{index:02d}@mail.bnbu.edu.cn',
                role=User.Roles.MEMBER,
            )
            MemberProfile.objects.create(
                user=user,
                real_name=f'扩展队员{index}',
                student_id=f'24300260{index:02d}',
                email=f'u4300260{index:02d}@mail.bnbu.edu.cn',
                major='CST',
                enrollment_year=2024,
                status=MemberProfile.Status.ACTIVE,
            )
            extra_members.append(user)

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-create'),
            {
                'title': '超限管理员活动',
                'event_type': Event.EventType.TRAINING,
                'description': '测试签到管理员上限。',
                'location': 'Lab 601',
                'start_time': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_time': (timezone.now() + timedelta(days=2, hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (timezone.now() + timedelta(days=2, minutes=-10)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (timezone.now() + timedelta(days=2, hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_managers': [
                    str(self.member_user.id),
                    str(self.other_member_user.id),
                    *(str(user.id) for user in extra_members),
                ],
                'status': Event.Status.PUBLISHED,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '签到管理员最多只能指定 5 名。')
        self.assertFalse(Event.objects.filter(title='超限管理员活动').exists())

    def test_admin_can_soft_delete_existing_event(self):
        event = Event.objects.create(
            title='待删除活动',
            event_type=Event.EventType.OTHER,
            description='用于测试删除。',
            location='Lab 204',
            start_time=timezone.now() + timedelta(days=4),
            end_time=timezone.now() + timedelta(days=4, hours=2),
            checkin_start_time=timezone.now() + timedelta(days=4, minutes=-15),
            checkin_end_time=timezone.now() + timedelta(days=4, hours=1),
            status=Event.Status.PUBLISHED,
            created_by=self.admin_user,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            published_at=timezone.now(),
        )
        event.checkin_managers.add(self.other_member_user)

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.post(reverse('event-delete', args=[event.id]), follow=True)
        self.assertRedirects(response, reverse('event-list-manage'))

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CANCELED)
        self.assertContains(response, '活动已删除')

        self.client.logout()
        self.client.login(username='2430026002', password='MemberPassword2026!')
        detail_response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(detail_response.status_code, 403)

    def test_event_qr_status_rotates_stale_code_and_invalidates_old_token(self):
        event = self.create_live_event()

        self.client.login(username='admin-review', password='AdminPassword2026!')
        self.client.post(reverse('event-generate-qr', args=[event.id]), follow=True)

        first_qr = EventQRCode.objects.get(event=event, is_active=True)
        EventQRCode.objects.filter(pk=first_qr.pk).update(
            created_at=timezone.now() - timedelta(seconds=11),
        )

        response = self.client.get(reverse('event-qr-status', args=[event.id]))
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertTrue(payload['active'])

        first_qr.refresh_from_db()
        self.assertFalse(first_qr.is_active)
        self.assertIsNotNone(first_qr.deactivated_at)

        current_qr = EventQRCode.objects.get(event=event, is_active=True)
        self.assertNotEqual(first_qr.token, current_qr.token)
        self.assertEqual(payload['entry_url'], current_qr.url)

        stale_response = self.client.get(reverse('qr-entry', args=[first_qr.token]))
        self.assertContains(stale_response, '二维码已失效或被停用。')

    def test_member_can_finish_checkin_via_resume_link_after_qr_rotates(self):
        event = self.create_live_event(title='扫码登录续接活动')
        qr_code = EventQRCode.objects.create(
            event=event,
            expires_at=timezone.now() + timedelta(minutes=30),
            created_by=self.admin_user,
        )
        qr_code.url = 'http://testserver' + reverse('qr-entry', args=[qr_code.token])
        qr_code.save(update_fields=['url'])

        first_response = self.client.get(reverse('qr-entry', args=[qr_code.token]))
        self.assertEqual(first_response.status_code, 302)

        login_redirect = urlparse(first_response.url)
        resume_path = parse_qs(login_redirect.query)['next'][0]
        resume_match = re.match(r'^/qr/resume/(?P<token>[^/]+)/$', resume_path)
        self.assertIsNotNone(resume_match)
        resume_token = resume_match.group('token')

        EventQRCode.objects.filter(pk=qr_code.pk).update(
            is_active=False,
            deactivated_at=timezone.now(),
        )

        self.client.login(username='2430026001', password='MemberPassword2026!')
        entry_response = self.client.get(resume_path)
        self.assertEqual(entry_response.status_code, 200)
        self.assertContains(entry_response, '确认扫码签到')

        checkin_response = self.client.post(
            reverse('qr-resume-checkin', args=[resume_token]),
            follow=True,
        )
        self.assertEqual(checkin_response.status_code, 200)
        self.assertContains(checkin_response, '扫码签到成功。')

        checkin = CheckInRecord.objects.get(event=event, member__user=self.member_user)
        self.assertEqual(checkin.checkin_method, CheckInRecord.Method.QR)
        self.assertEqual(checkin.source_qr_code, qr_code)


class BootstrapDemoCommandTests(TestCase):
    def test_bootstrap_demo_sets_user_email_for_seed_accounts(self):
        call_command('bootstrap_demo')

        superadmin = User.objects.get(username='superadmin')
        member = User.objects.get(username='member01')

        self.assertEqual(superadmin.email, 'superadmin@mail.bnbu.edu.cn')
        self.assertEqual(member.email, 'member01@example.com')
        self.assertEqual(member.member_profile.email, 'member01@example.com')


class ContestRatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(
            username='contest-admin',
            password='AdminPassword2026!',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=cls.admin_user,
            display_name='赛事管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        cls.member_user = User.objects.create_user(
            username='contest-member',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        cls.member_profile = MemberProfile.objects.create(
            user=cls.member_user,
            real_name='竞赛队员',
            student_id='2430026111',
            email='u430026111@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        cls.contest = Contest.objects.create(
            name='CCPC 校内选拔',
            series=Contest.Series.CCPC,
            season='2026',
            stage='校内选拔',
            contest_date=timezone.localdate() - timedelta(days=30),
            level=Contest.Level.CAMPUS,
            weight=1.00,
            status=Contest.Status.PUBLISHED,
            created_by=cls.admin_user,
        )
        cls.team = ContestTeam.objects.create(
            contest=cls.contest,
            team_name='BNBU Rising',
            leader=cls.member_profile,
        )
        cls.team.members.add(cls.member_profile)

    def test_sync_member_competition_profile_uses_verified_results(self):
        result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.SILVER,
            award_label='校赛银奖',
            rank_label='第 2 名',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )

        competition_profile = sync_member_competition_profile(self.member_profile)

        self.assertEqual(competition_profile.current_rating, 100)
        self.assertEqual(competition_profile.current_level, 'rookie')
        self.assertEqual(competition_profile.highest_award_label, '校赛银奖')
        result.refresh_from_db()
        self.assertEqual(result.rating_delta, 100)

    def test_management_and_member_pages_show_competition_modules(self):
        result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.BRONZE,
            award_label='校赛铜奖',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        sync_member_competition_profile(self.member_profile)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        management_dashboard_response = self.client.get(reverse('management-dashboard'))
        management_response = self.client.get(reverse('contest-detail-manage', args=[self.contest.id]))
        self.assertEqual(management_dashboard_response.status_code, 200)
        self.assertContains(management_dashboard_response, '赛事管理')
        self.assertContains(management_dashboard_response, '奖项审核')
        self.assertEqual(management_response.status_code, 200)
        self.assertContains(management_response, 'BNBU Rising')
        self.assertContains(management_response, '校赛铜奖')

        self.client.logout()
        self.client.login(username='contest-member', password='MemberPassword2026!')
        member_dashboard_response = self.client.get(reverse('member-dashboard'))
        profile_response = self.client.get(reverse('member-profile'))
        ladder_response = self.client.get(reverse('member-competition-ladder'))

        self.assertEqual(member_dashboard_response.status_code, 200)
        self.assertContains(member_dashboard_response, '竞赛天梯')
        self.assertContains(member_dashboard_response, '奖项申报')
        self.assertEqual(profile_response.status_code, 200)
        self.assertContains(profile_response, '竞赛档案')
        self.assertContains(profile_response, '校赛铜奖')

        self.assertEqual(ladder_response.status_code, 200)
        self.assertContains(ladder_response, '竞赛天梯')
        self.assertContains(ladder_response, '竞赛队员')
        self.assertTrue(MemberCompetitionProfile.objects.filter(member=self.member_profile).exists())

    def test_revoke_result_and_archive_contest_update_status_and_rating(self):
        result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.GOLD,
            award_label='校赛金奖',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        sync_member_competition_profile(self.member_profile)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        revoke_response = self.client.post(reverse('contest-result-revoke', args=[result.id]), follow=True)
        self.assertEqual(revoke_response.status_code, 200)

        result.refresh_from_db()
        self.assertTrue(result.is_revoked)
        self.assertFalse(result.verified)

        competition_profile = MemberCompetitionProfile.objects.get(member=self.member_profile)
        self.assertEqual(competition_profile.current_rating, 0)

        restored_response = self.client.post(reverse('contest-result-restore', args=[result.id]), follow=True)
        self.assertEqual(restored_response.status_code, 200)
        competition_profile.refresh_from_db()
        self.assertGreater(competition_profile.current_rating, 0)

        archive_response = self.client.post(reverse('contest-archive', args=[self.contest.id]), follow=True)
        self.assertEqual(archive_response.status_code, 200)
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, Contest.Status.ARCHIVED)
        competition_profile.refresh_from_db()
        self.assertEqual(competition_profile.current_rating, 0)

        publish_response = self.client.post(reverse('contest-publish', args=[self.contest.id]), follow=True)
        self.assertEqual(publish_response.status_code, 200)
        competition_profile.refresh_from_db()
        self.assertGreater(competition_profile.current_rating, 0)

    def test_ladder_filters_and_profile_timeline_show_expected_results(self):
        second_user = User.objects.create_user(
            username='contest-member-2',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        second_profile = MemberProfile.objects.create(
            user=second_user,
            real_name='另一位队员',
            student_id='2430026222',
            email='u430026222@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2023,
            status=MemberProfile.Status.ACTIVE,
        )
        second_team = ContestTeam.objects.create(
            contest=self.contest,
            team_name='BNBU Data',
            leader=second_profile,
        )
        second_team.members.add(second_profile)

        first_result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.SILVER,
            award_label='校赛银奖',
            rank_label='第 2 名',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        second_result = ContestResult.objects.create(
            contest=self.contest,
            team=second_team,
            award_type=ContestResult.AwardType.PARTICIPATION,
            award_label='正式参赛',
            rank_label='完成参赛',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        sync_member_competition_profile(self.member_profile)
        sync_member_competition_profile(second_profile)

        self.client.login(username='contest-member', password='MemberPassword2026!')
        filtered_ladder = self.client.get(
            reverse('member-competition-ladder'),
            {'major': 'CST', 'level': 'rookie'},
        )
        profile_response = self.client.get(reverse('member-profile'))

        self.assertContains(filtered_ladder, '竞赛队员')
        self.assertNotContains(filtered_ladder, '另一位队员')
        self.assertContains(profile_response, '完整赛事时间线')
        self.assertContains(profile_response, first_result.rank_label)
        self.assertNotContains(profile_response, second_result.award_label)

        public_profile_response = self.client.get(
            reverse('member-competition-profile-public', args=[second_profile.id])
        )
        self.assertEqual(public_profile_response.status_code, 200)
        self.assertContains(public_profile_response, '另一位队员')
        self.assertContains(public_profile_response, second_result.award_label)
        self.assertNotContains(public_profile_response, second_profile.email)

    def test_team_form_keeps_existing_inactive_members_editable(self):
        self.member_profile.status = MemberProfile.Status.INACTIVE
        self.member_profile.save(update_fields=['status'])

        form = ContestTeamForm(instance=self.team)

        self.assertIn(self.member_profile, form.fields['members'].queryset)
        self.assertIn(self.member_profile, form.fields['leader'].queryset)

    def test_peak_rating_recomputes_after_result_is_revoked(self):
        result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.GOLD,
            award_label='校赛金奖',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        competition_profile = sync_member_competition_profile(self.member_profile)
        self.assertGreater(competition_profile.peak_rating, 0)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        self.client.post(reverse('contest-result-revoke', args=[result.id]), follow=True)

        competition_profile.refresh_from_db()
        self.assertEqual(competition_profile.current_rating, 0)
        self.assertEqual(competition_profile.peak_rating, 0)

    def test_management_contest_list_filters_results(self):
        Contest.objects.create(
            name='ICPC 区域赛',
            series=Contest.Series.ICPC,
            season='2025',
            stage='区域赛',
            contest_date=timezone.localdate() - timedelta(days=400),
            level=Contest.Level.REGIONAL,
            weight=1.40,
            status=Contest.Status.ARCHIVED,
            created_by=self.admin_user,
        )

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        response = self.client.get(
            reverse('contest-list-manage'),
            {
                'series': Contest.Series.CCPC,
                'status': Contest.Status.PUBLISHED,
                'season': '2026',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CCPC 校内选拔')
        self.assertNotContains(response, 'ICPC 区域赛')

    def test_member_submission_can_be_reviewed_into_official_result(self):
        teammate_user = User.objects.create_user(
            username='contest-member-3',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        teammate_profile = MemberProfile.objects.create(
            user=teammate_user,
            real_name='队友三号',
            student_id='2430026333',
            email='u430026333@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )

        self.client.login(username='contest-member', password='MemberPassword2026!')
        submit_response = self.client.post(
            reverse('member-contest-submission-apply'),
            {
                'contest_name': '华东邀请赛',
                'contest_series': Contest.Series.INVITATIONAL,
                'contest_season': '2026',
                'contest_stage': '邀请赛',
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.PROVINCIAL,
                'team_name': 'BNBU Lights',
                'team_members': [teammate_profile.id],
                'external_teammates': '校外队友甲',
                'award_type': ContestResult.AwardType.BRONZE,
                'award_label': '邀请赛铜奖',
                'rank_label': '第 8 名',
                'result_tier': ContestResult.ResultTier.MEDIUM,
                'evidence_url': 'https://example.com/proof',
                'submission_note': '成员自助申报',
            },
            follow=True,
        )
        self.assertEqual(submit_response.status_code, 200)
        submission = ContestSubmission.objects.get(applicant=self.member_user)
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.PENDING)
        self.assertEqual(submission.team_members.count(), 2)

        self.client.logout()
        self.client.login(username='contest-admin', password='AdminPassword2026!')
        review_response = self.client.post(
            reverse('contest-submission-review', args=[submission.id]),
            {
                'contest_name': '华东邀请赛',
                'contest_series': Contest.Series.INVITATIONAL,
                'contest_season': '2026',
                'contest_stage': '正式邀请赛',
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.REGIONAL,
                'team_name': 'BNBU Lights',
                'team_members': [self.member_profile.id, teammate_profile.id],
                'external_teammates': '校外队友甲',
                'award_type': ContestResult.AwardType.SILVER,
                'award_label': '最终银奖',
                'rank_label': '第 6 名',
                'result_tier': ContestResult.ResultTier.HIGH,
                'evidence_url': 'https://example.com/final-proof',
                'submission_note': '管理员修订后通过',
                'review_note': '信息已核对',
                'decision': 'approve',
            },
            follow=True,
        )
        self.assertEqual(review_response.status_code, 200)

        submission.refresh_from_db()
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.APPROVED)
        self.assertIsNotNone(submission.resolved_result)
        self.assertEqual(submission.resolved_result.display_award_label, '最终银奖')
        self.assertEqual(submission.resolved_team.external_member_names, '校外队友甲')

        profile = MemberCompetitionProfile.objects.get(member=self.member_profile)
        self.assertGreater(profile.current_rating, 0)

    def test_reviewing_submission_cannot_overwrite_existing_official_result(self):
        official_result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.GOLD,
            award_label='原始金奖',
            rank_label='第 1 名',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )
        submission = ContestSubmission.objects.create(
            applicant=self.member_user,
            contest_name=self.contest.name,
            contest_series=self.contest.series,
            contest_season=self.contest.season,
            contest_stage=self.contest.stage,
            contest_date=self.contest.contest_date,
            organizer=self.contest.organizer,
            contest_level=self.contest.level,
            team_name=self.team.team_name,
            award_type=ContestResult.AwardType.SILVER,
            award_label='成员申报银奖',
            rank_label='第 2 名',
            result_tier=ContestResult.ResultTier.HIGH,
            evidence_url='https://example.com/submission-proof',
            submission_note='试图覆盖正式成绩',
        )
        submission.team_members.add(self.member_profile)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        response = self.client.post(
            reverse('contest-submission-review', args=[submission.id]),
            {
                'contest_name': self.contest.name,
                'contest_series': self.contest.series,
                'contest_season': self.contest.season,
                'contest_stage': self.contest.stage,
                'contest_date': self.contest.contest_date,
                'organizer': self.contest.organizer,
                'contest_level': self.contest.level,
                'linked_contest': self.contest.id,
                'team_name': self.team.team_name,
                'team_members': [self.member_profile.id],
                'external_teammates': '',
                'award_type': ContestResult.AwardType.SILVER,
                'award_label': '成员申报银奖',
                'rank_label': '第 2 名',
                'result_tier': ContestResult.ResultTier.HIGH,
                'evidence_url': 'https://example.com/submission-proof',
                'submission_note': '试图覆盖正式成绩',
                'review_note': '发现冲突',
                'decision': 'approve',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '审核未通过，请先处理正式赛事成绩冲突。')
        self.assertContains(response, '该赛事队伍已经存在正式成绩，请直接编辑原成绩，不能通过申报覆盖。')

        submission.refresh_from_db()
        official_result.refresh_from_db()
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.PENDING)
        self.assertEqual(submission.resolved_result_id, None)
        self.assertEqual(official_result.display_award_label, '原始金奖')
        self.assertEqual(ContestResult.objects.filter(contest=self.contest, team=self.team).count(), 1)

    def test_rejecting_approved_submission_revokes_official_result_and_rating(self):
        submission = ContestSubmission.objects.create(
            applicant=self.member_user,
            contest_name='华东邀请赛',
            contest_series=Contest.Series.INVITATIONAL,
            contest_season='2026',
            contest_stage='邀请赛',
            contest_date=timezone.localdate(),
            organizer='组委会',
            contest_level=Contest.Level.REGIONAL,
            team_name='BNBU Lights',
            award_type=ContestResult.AwardType.SILVER,
            award_label='成员申报银奖',
            rank_label='第 6 名',
            result_tier=ContestResult.ResultTier.HIGH,
            evidence_url='https://example.com/final-proof',
            submission_note='管理员修订后通过',
        )
        submission.team_members.add(self.member_profile)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        approve_response = self.client.post(
            reverse('contest-submission-review', args=[submission.id]),
            {
                'contest_name': '华东邀请赛',
                'contest_series': Contest.Series.INVITATIONAL,
                'contest_season': '2026',
                'contest_stage': '正式邀请赛',
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.REGIONAL,
                'team_name': 'BNBU Lights',
                'team_members': [self.member_profile.id],
                'external_teammates': '',
                'award_type': ContestResult.AwardType.SILVER,
                'award_label': '最终银奖',
                'rank_label': '第 6 名',
                'result_tier': ContestResult.ResultTier.HIGH,
                'evidence_url': 'https://example.com/final-proof',
                'submission_note': '管理员修订后通过',
                'review_note': '信息已核对',
                'decision': 'approve',
            },
            follow=True,
        )
        self.assertEqual(approve_response.status_code, 200)

        submission.refresh_from_db()
        result = submission.resolved_result
        competition_profile = MemberCompetitionProfile.objects.get(member=self.member_profile)
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.APPROVED)
        self.assertTrue(result.verified)
        self.assertGreater(competition_profile.current_rating, 0)

        reject_response = self.client.post(
            reverse('contest-submission-review', args=[submission.id]),
            {
                'contest_name': '华东邀请赛',
                'contest_series': Contest.Series.INVITATIONAL,
                'contest_season': '2026',
                'contest_stage': '正式邀请赛',
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.REGIONAL,
                'linked_contest': submission.resolved_contest_id,
                'team_name': 'BNBU Lights',
                'team_members': [self.member_profile.id],
                'external_teammates': '',
                'award_type': ContestResult.AwardType.SILVER,
                'award_label': '最终银奖',
                'rank_label': '第 6 名',
                'result_tier': ContestResult.ResultTier.HIGH,
                'evidence_url': 'https://example.com/final-proof',
                'submission_note': '管理员复核后驳回',
                'review_note': '复核驳回',
                'decision': 'reject',
            },
            follow=True,
        )

        self.assertEqual(reject_response.status_code, 200)
        submission.refresh_from_db()
        result.refresh_from_db()
        competition_profile.refresh_from_db()
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.REJECTED)
        self.assertFalse(result.verified)
        self.assertTrue(result.is_revoked)
        self.assertEqual(competition_profile.current_rating, 0)

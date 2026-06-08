from datetime import timedelta
import re

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AdminProfile, CheckInRecord, EmailVerificationCode, Event, MemberProfile, User


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


class BootstrapDemoCommandTests(TestCase):
    def test_bootstrap_demo_sets_user_email_for_seed_accounts(self):
        call_command('bootstrap_demo')

        superadmin = User.objects.get(username='superadmin')
        member = User.objects.get(username='member01')

        self.assertEqual(superadmin.email, 'superadmin@mail.bnbu.edu.cn')
        self.assertEqual(member.email, 'member01@example.com')
        self.assertEqual(member.member_profile.email, 'member01@example.com')

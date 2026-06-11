from datetime import timedelta
import re
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .competition import sync_event_series_completion, sync_member_competition_profile
from .forms import ContestTeamForm, EventForm
from .models import (
    AdminProfile,
    AuditLog,
    CheckInRecord,
    Contest,
    ContestResult,
    ContestSubmission,
    ContestTeam,
    EmailVerificationCode,
    Event,
    EventQRCode,
    EventSeries,
    EventSeriesCompletion,
    MemberIntegritySanction,
    MemberTeam,
    MemberTeamSubmission,
    MemberCompetitionProfile,
    MemberProfile,
    User,
    SystemSetting,
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

    def test_verified_contest_result_counts_as_star_activity(self):
        contest_member_user = User.objects.create_user(
            username='member-contest-star',
            password='ACM123456',
            role=User.Roles.MEMBER,
        )
        contest_member_profile = MemberProfile.objects.create(
            user=contest_member_user,
            real_name='赛事点亮队员',
            student_id='20264567',
            major='Computer Science',
            status=MemberProfile.Status.ACTIVE,
        )
        contest = Contest.objects.create(
            name='安徽省程序设计竞赛',
            series=Contest.Series.PROVINCIAL,
            season='2026',
            contest_date=timezone.localdate() - timedelta(days=2),
            level=Contest.Level.PROVINCIAL,
            status=Contest.Status.PUBLISHED,
            created_by=self.admin_user,
        )
        team = ContestTeam.objects.create(
            contest=contest,
            team_name='Star Coders',
            leader=contest_member_profile,
        )
        team.members.add(contest_member_profile)
        ContestResult.objects.create(
            contest=contest,
            team=team,
            award_type=ContestResult.AwardType.PARTICIPATION,
            award_label='正式参赛',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )

        self.client.login(username='member-contest-star', password='ACM123456')
        response = self.client.get(reverse('member-star-center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '赛事点亮队员 的 ACM Star')
        self.assertContains(response, '已点亮')
        self.assertContains(response, '近期活跃记录')
        self.assertContains(response, '安徽省程序设计竞赛')
        self.assertContains(response, '赛事参与')

    def test_member_activity_history_merges_checkins_and_contests(self):
        contest = Contest.objects.create(
            name='校赛热身赛',
            series=Contest.Series.CAMPUS,
            season='2026',
            contest_date=timezone.localdate() - timedelta(days=2),
            level=Contest.Level.CAMPUS,
            status=Contest.Status.PUBLISHED,
            created_by=self.admin_user,
        )
        team = ContestTeam.objects.create(
            contest=contest,
            team_name='History Team',
            leader=self.member_profile,
        )
        team.members.add(self.member_profile)
        ContestResult.objects.create(
            contest=contest,
            team=team,
            award_type=ContestResult.AwardType.BRONZE,
            award_label='校赛铜奖',
            verified=True,
            verified_by=self.admin_user,
            verified_at=timezone.now(),
        )

        self.client.login(username='member-star', password='ACM123456')
        response = self.client.get(reverse('member-checkin-history'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '活跃历史')
        self.assertContains(response, 'ACM Star 训练营')
        self.assertContains(response, '活动签到')
        self.assertContains(response, '校赛热身赛')
        self.assertContains(response, '赛事参与')
        self.assertContains(response, '校赛铜奖')

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

    def test_integrity_sanction_extinguishes_member_star_and_shows_reason_to_self(self):
        MemberIntegritySanction.objects.create(
            member=self.member_profile,
            reason_type=MemberIntegritySanction.ReasonType.SERIOUS_RULE_VIOLATION,
            member_reason='活动中存在严重违规行为，处罚期内暂停展示星标。',
            internal_note='管理员测试用处罚记录。',
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=5),
            created_by=self.admin_user,
        )

        self.client.login(username='member-star', password='ACM123456')
        response = self.client.get(reverse('member-star-center'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['star_snapshot']['lit'])
        self.assertContains(response, '诚信处罚期')
        self.assertContains(response, '违反 ACM 准则')
        self.assertContains(response, '活动中存在严重违规行为，处罚期内暂停展示星标。')

    def test_public_competition_page_shows_generic_notice_only_to_other_members(self):
        MemberIntegritySanction.objects.create(
            member=self.member_profile,
            reason_type=MemberIntegritySanction.ReasonType.CONTEST_NO_SHOW,
            member_reason='报名后无故缺席最近比赛。',
            internal_note='测试公开页文案边界。',
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=3),
            created_by=self.admin_user,
        )
        viewer = User.objects.create_user(
            username='viewer-member',
            password='ACM123456',
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=viewer,
            real_name='旁观队员',
            student_id='20269999',
            major='Computer Science',
            status=MemberProfile.Status.ACTIVE,
        )

        self.client.login(username='viewer-member', password='ACM123456')
        response = self.client.get(reverse('member-competition-profile-public', args=[self.member_profile.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '违反 ACM 准则')
        self.assertNotContains(response, '报名后无故缺席最近比赛。')
        self.assertFalse(response.context['integrity_sanction']['member_reason_visible'])


class MemberIntegrityManagementTests(TestCase):
    def setUp(self):
        self.member_user = User.objects.create_user(
            username='integrity-member',
            password='ACM123456',
            role=User.Roles.MEMBER,
        )
        self.member_profile = MemberProfile.objects.create(
            user=self.member_user,
            real_name='治理队员',
            student_id='20268888',
            major='CST',
            enrollment_year=2026,
            status=MemberProfile.Status.ACTIVE,
        )
        self.admin_user = User.objects.create_user(
            username='integrity-admin',
            password='AdminPassword2026!',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='治理管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )

    def test_management_can_create_and_revoke_integrity_sanction(self):
        self.client.login(username='integrity-admin', password='AdminPassword2026!')
        create_response = self.client.post(
            reverse('member-detail-manage', args=[self.member_profile.id]),
            {
                'reason_type': MemberIntegritySanction.ReasonType.CONTEST_NO_SHOW,
                'member_reason': '报名后缺席最近比赛。',
                'internal_note': '经核实未请假。',
                'starts_at': (timezone.localtime(timezone.now()) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': (timezone.localtime(timezone.now()) + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),
            },
            follow=True,
        )
        self.assertEqual(create_response.status_code, 200)

        sanction = MemberIntegritySanction.objects.get(member=self.member_profile)
        self.assertEqual(sanction.member_reason, '报名后缺席最近比赛。')
        self.assertTrue(
            AuditLog.objects.filter(
                action='create_member_integrity_sanction',
                target_id=sanction.id,
            ).exists()
        )

        list_response = self.client.get(reverse('member-list-manage'))
        self.assertContains(list_response, '治理队员')
        self.assertContains(list_response, '处罚期')

        revoke_response = self.client.post(
            reverse('member-integrity-sanction-revoke', args=[self.member_profile.id]),
            {'sanction_id': str(sanction.id)},
            follow=True,
        )
        self.assertEqual(revoke_response.status_code, 200)

        sanction.refresh_from_db()
        self.assertIsNotNone(sanction.revoked_at)
        self.assertEqual(sanction.revoked_by, self.admin_user)
        self.assertTrue(
            AuditLog.objects.filter(
                action='revoke_member_integrity_sanction',
                target_id=sanction.id,
            ).exists()
        )

    def test_management_prefers_active_sanction_over_future_one(self):
        active_sanction = MemberIntegritySanction.objects.create(
            member=self.member_profile,
            reason_type=MemberIntegritySanction.ReasonType.SERIOUS_RULE_VIOLATION,
            member_reason='当前生效处罚',
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=2),
            created_by=self.admin_user,
        )
        future_sanction = MemberIntegritySanction.objects.create(
            member=self.member_profile,
            reason_type=MemberIntegritySanction.ReasonType.CONTEST_NO_SHOW,
            member_reason='未来待生效处罚',
            starts_at=timezone.now() + timedelta(days=5),
            ends_at=timezone.now() + timedelta(days=10),
            created_by=self.admin_user,
        )

        self.client.login(username='integrity-admin', password='AdminPassword2026!')
        detail_response = self.client.get(reverse('member-detail-manage', args=[self.member_profile.id]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.context['current_sanction'].id, active_sanction.id)
        self.assertTrue(detail_response.context['is_currently_restricted'])
        self.assertContains(detail_response, '当前生效处罚')
        self.assertNotContains(detail_response, '未来待生效处罚')

        list_response = self.client.get(reverse('member-list-manage'), {'integrity': 'restricted'})
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, '治理队员')

        revoke_response = self.client.post(
            reverse('member-integrity-sanction-revoke', args=[self.member_profile.id]),
            {'sanction_id': str(active_sanction.id)},
            follow=True,
        )
        self.assertEqual(revoke_response.status_code, 200)

        active_sanction.refresh_from_db()
        future_sanction.refresh_from_db()
        self.assertIsNotNone(active_sanction.revoked_at)
        self.assertIsNone(future_sanction.revoked_at)


class MemberListManageTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='member-admin',
            password='AdminPassword2026!',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='成员管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        self.members = []
        for index in range(6):
            user = User.objects.create_user(
                username=f'member-user-{index}',
                password='MemberPassword2026!',
                role=User.Roles.MEMBER,
            )
            profile = MemberProfile.objects.create(
                user=user,
                real_name=f'成员{index}',
                student_id=f'20260{index:04d}',
                major='CST' if index % 2 == 0 else 'DS',
                enrollment_year=2023 + (index % 2),
                status=MemberProfile.Status.ACTIVE,
            )
            self.members.append(profile)

    def test_member_list_manage_shows_only_first_five_cards(self):
        self.client.login(username='member-admin', password='AdminPassword2026!')
        response = self.client.get(reverse('member-list-manage'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['members']), 5)
        self.assertEqual(response.context['member_total'], 6)
        self.assertEqual(response.context['visible_member_total'], 5)
        self.assertEqual(response.context['visible_member_limit'], 5)
        self.assertEqual(
            [member.id for member in response.context['members']],
            [member.id for member in self.members[:5]],
        )
        self.assertContains(response, '展示 5 / 6 人')
        self.assertContains(response, '搜索成员')

    def test_member_list_manage_search_choices_keep_full_filtered_results(self):
        self.client.login(username='member-admin', password='AdminPassword2026!')
        response = self.client.get(reverse('member-list-manage'), {'status': 'active'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '搜索姓名 / 学号 / 用户名 / 专业')
        self.assertContains(response, '当前筛选结果内继续按姓名、学号、用户名或专业搜索成员')
        self.assertEqual(len(response.context['member_search_choices']), 6)
        self.assertEqual(
            [item['id'] for item in response.context['member_search_choices']],
            [member.id for member in self.members],
        )
        self.assertEqual(response.context['member_search_choices'][-1]['real_name'], '成员5')


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
        self.event_series = EventSeries.objects.create(
            title='春季训练营',
            description='用于测试系列归属。',
            series_type=EventSeries.SeriesType.TRAINING,
            status=EventSeries.Status.PUBLISHED,
            expected_event_count=10,
            required_checkins_for_rating=8,
            rating_enabled=True,
            rating_points=120,
            created_by=self.admin_user,
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
        self.assertContains(response, '搜索活动')
        self.assertContains(response, '昨天 + 今天 + 明天的申请活动')

    def test_member_event_list_only_shows_yesterday_today_and_tomorrow_for_events_and_applications(self):
        now = timezone.now()
        recent_event = Event.objects.create(
            title='近期公开活动',
            event_type=Event.EventType.TRAINING,
            description='近三天内应显示。',
            location='Lab 801',
            start_time=now - timedelta(hours=2),
            end_time=now + timedelta(hours=1),
            checkin_start_time=now - timedelta(hours=3),
            checkin_end_time=now + timedelta(minutes=30),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        old_event = Event.objects.create(
            title='更早公开活动',
            event_type=Event.EventType.LECTURE,
            description='超出近三天范围。',
            location='Lab 802',
            start_time=now - timedelta(days=5),
            end_time=now - timedelta(days=5) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=5, minutes=20),
            checkin_end_time=now - timedelta(days=5) + timedelta(hours=1),
            status=Event.Status.CHECKIN_CLOSED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now - timedelta(days=5),
        )
        recent_application = Event.objects.create(
            title='近期活动申请',
            event_type=Event.EventType.SHARING,
            description='窗口内申请。',
            location='Lab 803',
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            checkin_start_time=now + timedelta(days=1, minutes=-20),
            checkin_end_time=now + timedelta(days=1, hours=1),
            status=Event.Status.DRAFT,
            applicant=self.member_user,
            review_status=Event.ReviewStatus.PENDING,
            created_by=self.member_user,
        )
        old_application = Event.objects.create(
            title='更早活动申请',
            event_type=Event.EventType.LECTURE,
            description='超出近三天范围的申请。',
            location='Lab 804',
            start_time=now - timedelta(days=6),
            end_time=now - timedelta(days=6) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=6, minutes=20),
            checkin_end_time=now - timedelta(days=6) + timedelta(hours=1),
            status=Event.Status.DRAFT,
            applicant=self.member_user,
            review_status=Event.ReviewStatus.REJECTED,
            created_by=self.member_user,
        )
        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.get(reverse('member-event-list'))

        self.assertEqual(response.status_code, 200)
        self.assertQuerysetEqual(response.context['events'], [recent_event], transform=lambda event: event)
        self.assertQuerysetEqual(response.context['my_applications'], [recent_application], transform=lambda event: event)
        self.assertEqual(response.context['recent_event_total'], 1)
        self.assertEqual(response.context['recent_application_total'], 1)
        self.assertContains(response, '昨天 + 今天 + 明天内已审核通过的活动')
        self.assertContains(response, '昨天 + 今天 + 明天的申请活动')
        self.assertNotIn(old_event.id, [event.id for event in response.context['events']])
        self.assertNotIn(old_application.id, [event.id for event in response.context['my_applications']])

    def test_member_event_search_modal_keeps_full_event_and_application_history(self):
        now = timezone.now()
        public_event = Event.objects.create(
            title='历史公开活动',
            event_type=Event.EventType.OTHER,
            description='用于搜索弹窗。',
            location='Lab 805',
            start_time=now - timedelta(days=8),
            end_time=now - timedelta(days=8) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=8, minutes=20),
            checkin_end_time=now - timedelta(days=8) + timedelta(hours=1),
            status=Event.Status.CHECKIN_CLOSED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now - timedelta(days=8),
        )
        my_application = Event.objects.create(
            title='历史活动申请',
            event_type=Event.EventType.SHARING,
            description='用于搜索弹窗。',
            location='Lab 806',
            start_time=now - timedelta(days=7),
            end_time=now - timedelta(days=7) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=7, minutes=20),
            checkin_end_time=now - timedelta(days=7) + timedelta(hours=1),
            status=Event.Status.DRAFT,
            applicant=self.member_user,
            review_status=Event.ReviewStatus.REJECTED,
            created_by=self.member_user,
        )
        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.get(reverse('member-event-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '搜索活动标题')
        self.assertContains(response, '可按标题搜索全部已审核活动，以及你提交过的全部活动申请')
        self.assertEqual(
            [item['id'] for item in response.context['event_search_choices']],
            [public_event.id],
        )
        self.assertEqual(
            [item['id'] for item in response.context['application_search_choices']],
            [my_application.id],
        )

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
        self.assertTrue(event.qr_codes.filter(is_active=True).exists())

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
        self.assertContains(list_response, '搜索活动')
        self.assertContains(list_response, '可按标题搜索全部已审核活动，以及你提交过的全部活动申请')

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
                'series': str(self.event_series.id),
                'series_order': '2',
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
        self.assertEqual(event.series, self.event_series)
        self.assertEqual(event.series_order, 2)
        self.assertEqual(event.review_status, Event.ReviewStatus.APPROVED)
        self.assertContains(response, '签到管理员')
        self.assertContains(response, '申请队员')
        self.assertContains(response, '普通队员')
        self.assertContains(response, '春季训练营')

        self.client.logout()
        self.client.login(username='2430026002', password='MemberPassword2026!')
        detail_response = self.client.get(reverse('event-detail-manage', args=[event.id]))
        self.assertEqual(detail_response.status_code, 200)

        qr_response = self.client.post(reverse('event-generate-qr', args=[event.id]), follow=True)
        self.assertRedirects(qr_response, reverse('event-detail-manage', args=[event.id]))
        event.refresh_from_db()
        self.assertTrue(event.qr_codes.filter(is_active=True).exists())

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

    def test_event_form_requires_series_before_series_order(self):
        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-create'),
            {
                'title': '缺少系列的活动',
                'event_type': Event.EventType.TRAINING,
                'description': '测试系列校验。',
                'location': 'Lab 602',
                'series_order': '3',
                'start_time': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_time': (timezone.now() + timedelta(days=2, hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (timezone.now() + timedelta(days=2, minutes=-10)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (timezone.now() + timedelta(days=2, hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'status': Event.Status.PUBLISHED,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '填写系列内序号前，请先选择所属系列。')
        self.assertFalse(Event.objects.filter(title='缺少系列的活动').exists())

    def test_event_create_page_uses_series_picker_modal(self):
        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.get(reverse('event-create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '搜索选择系列')
        self.assertContains(response, '选择所属系列')
        self.assertNotContains(response, '<select name="series"', html=False)

    def test_event_list_manage_only_shows_yesterday_today_and_tomorrow(self):
        now = timezone.now()
        visible_today = Event.objects.create(
            title='今日活动',
            event_type=Event.EventType.TRAINING,
            description='应在近三天列表中显示。',
            location='Lab 701',
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            checkin_start_time=now - timedelta(hours=2),
            checkin_end_time=now + timedelta(minutes=30),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        visible_yesterday = Event.objects.create(
            title='昨日活动',
            event_type=Event.EventType.TRAINING,
            description='应在近三天列表中显示。',
            location='Lab 702',
            start_time=now - timedelta(days=1),
            end_time=now - timedelta(days=1) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=1, minutes=20),
            checkin_end_time=now - timedelta(days=1) + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        visible_tomorrow = Event.objects.create(
            title='明天活动',
            event_type=Event.EventType.LECTURE,
            description='应在窗口内显示。',
            location='Lab 703',
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            checkin_start_time=now + timedelta(days=1, minutes=-20),
            checkin_end_time=now + timedelta(days=1, hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        hidden_old = Event.objects.create(
            title='更早活动',
            event_type=Event.EventType.OTHER,
            description='不应在近三天列表中显示。',
            location='Lab 704',
            start_time=now - timedelta(days=4),
            end_time=now - timedelta(days=4) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=4, minutes=20),
            checkin_end_time=now - timedelta(days=4) + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        hidden_past = Event.objects.create(
            title='前天活动',
            event_type=Event.EventType.SHARING,
            description='不应在当前窗口中显示。',
            location='Lab 705',
            start_time=now - timedelta(days=2),
            end_time=now - timedelta(days=2) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=2, minutes=20),
            checkin_end_time=now - timedelta(days=2) + timedelta(hours=1),
            status=Event.Status.DRAFT,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
        )

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.get(reverse('event-list-manage'))

        self.assertEqual(response.status_code, 200)
        self.assertQuerysetEqual(
            response.context['events'],
            [visible_tomorrow, visible_today, visible_yesterday],
            transform=lambda event: event,
        )
        self.assertEqual(response.context['recent_event_total'], 3)
        self.assertContains(response, '搜索活动')
        self.assertContains(response, '昨天 + 今天 + 明天的活动')
        self.assertNotIn(hidden_old.id, [event.id for event in response.context['events']])
        self.assertNotIn(hidden_past.id, [event.id for event in response.context['events']])

    def test_event_list_manage_search_modal_includes_all_event_titles(self):
        now = timezone.now()
        recent_event = Event.objects.create(
            title='近期开会',
            event_type=Event.EventType.TRAINING,
            description='近期活动。',
            location='Lab 706',
            start_time=now,
            end_time=now + timedelta(hours=2),
            checkin_start_time=now - timedelta(minutes=15),
            checkin_end_time=now + timedelta(minutes=30),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now,
        )
        archived_event = Event.objects.create(
            title='往期讲座',
            event_type=Event.EventType.LECTURE,
            description='历史活动。',
            location='Lab 707',
            start_time=now - timedelta(days=9),
            end_time=now - timedelta(days=9) + timedelta(hours=2),
            checkin_start_time=now - timedelta(days=9, minutes=20),
            checkin_end_time=now - timedelta(days=9) + timedelta(hours=1),
            status=Event.Status.CHECKIN_CLOSED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=now,
            created_by=self.admin_user,
            published_at=now - timedelta(days=9),
        )

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.get(reverse('event-list-manage'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '搜索活动名称')
        self.assertContains(response, '快速进入详情或编辑页')
        self.assertEqual(
            [item['id'] for item in response.context['event_search_choices']],
            [recent_event.id, archived_event.id],
        )
        self.assertEqual(
            [item['title'] for item in response.context['event_search_choices']],
            ['近期开会', '往期讲座'],
        )

    def test_event_edit_page_prefills_datetime_local_values(self):
        event_start = timezone.now() + timedelta(days=6)
        event = Event.objects.create(
            title='待编辑时间活动',
            event_type=Event.EventType.LECTURE,
            description='用于测试时间回填。',
            location='Lab 603',
            start_time=event_start,
            end_time=event_start + timedelta(hours=2),
            checkin_start_time=event_start - timedelta(minutes=5),
            checkin_end_time=event_start + timedelta(minutes=15),
            status=Event.Status.DRAFT,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            created_by=self.admin_user,
        )

        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.get(reverse('event-edit', args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{timezone.localtime(event.start_time).strftime("%Y-%m-%dT%H:%M")}"', html=False)
        self.assertContains(response, f'value="{timezone.localtime(event.end_time).strftime("%Y-%m-%dT%H:%M")}"', html=False)
        self.assertContains(response, f'value="{timezone.localtime(event.checkin_start_time).strftime("%Y-%m-%dT%H:%M")}"', html=False)
        self.assertContains(response, f'value="{timezone.localtime(event.checkin_end_time).strftime("%Y-%m-%dT%H:%M")}"', html=False)

    def test_event_create_page_includes_default_checkin_window_logic(self):
        self.client.login(username='admin-review', password='AdminPassword2026!')
        response = self.client.get(reverse('event-create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'syncDefaultCheckinWindow', html=False)
        self.assertContains(response, '5 * 60 * 1000', html=False)
        self.assertContains(response, '15 * 60 * 1000', html=False)

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

        first_qr = EventQRCode.objects.filter(event=event, is_active=True).order_by('created_at').first()
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

        current_qr = EventQRCode.objects.filter(event=event, is_active=True).order_by('-created_at').first()
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

    def test_member_event_detail_does_not_offer_web_checkin(self):
        event = self.create_live_event(title='仅二维码签到活动')

        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.get(reverse('member-event-detail', args=[event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '请使用活动二维码进入签到页完成签到')
        self.assertNotContains(response, '立即签到')
        self.assertNotContains(response, reverse('member-event-checkin', args=[event.id]))

    def test_member_event_checkin_endpoint_rejects_web_checkin(self):
        event = self.create_live_event(title='禁止网页签到活动')

        self.client.login(username='2430026001', password='MemberPassword2026!')
        response = self.client.post(reverse('member-event-checkin', args=[event.id]))

        self.assertEqual(response.status_code, 403)
        self.assertIn('活动仅支持通过二维码签到。', response.content.decode())
        self.assertFalse(
            CheckInRecord.objects.filter(
                event=event,
                member__user=self.member_user,
                checkin_method=CheckInRecord.Method.WEB,
            ).exists()
        )

    def test_member_can_register_from_qr_flow_and_return_to_resume_link(self):
        event = self.create_live_event(title='扫码注册回跳活动')
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

        register_page = self.client.get(reverse('register'), {'next': resume_path})
        self.assertEqual(register_page.status_code, 200)
        self.assertContains(register_page, f'value="{resume_path}"', html=False)

        register_response = self.client.post(
            reverse('register'),
            {
                'real_name': '扫码新队员',
                'username': '2430026123',
                'enrollment_year': 2024,
                'major': 'cst',
                'school_email': 'u430026123@mail.bnbu.edu.cn',
                'password1': 'MemberPassword2026!',
                'password2': 'MemberPassword2026!',
                'next': resume_path,
            },
            follow=True,
        )
        self.assertRedirects(register_response, resume_path)
        self.assertContains(register_response, '确认扫码签到')

        checkin_response = self.client.post(
            reverse('qr-resume-checkin', args=[resume_token]),
            follow=True,
        )
        self.assertEqual(checkin_response.status_code, 200)
        self.assertContains(checkin_response, '扫码签到成功。')

        checkin = CheckInRecord.objects.get(event=event, member__user__username='2430026123')
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


class EventSeriesManagementTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='series-admin',
            password='AdminPassword2026!',
            email='series-admin@mail.bnbu.edu.cn',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='系列管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        self.member_user = User.objects.create_user(
            username='2430026120',
            password='MemberPassword2026!',
            email='u430026120@mail.bnbu.edu.cn',
            role=User.Roles.MEMBER,
        )
        self.member_profile = MemberProfile.objects.create(
            user=self.member_user,
            real_name='系列队员',
            student_id='2430026120',
            email='u430026120@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        self.series = EventSeries.objects.create(
            title='春季专题训练',
            description='系列管理测试。',
            series_type=EventSeries.SeriesType.TRAINING,
            status=EventSeries.Status.PUBLISHED,
            expected_event_count=6,
            required_checkins_for_rating=2,
            rating_enabled=True,
            rating_points=50,
            created_by=self.admin_user,
        )
        event_time = timezone.now() - timedelta(days=2)
        self.event = Event.objects.create(
            title='专题训练 1',
            event_type=Event.EventType.TRAINING,
            description='用于测试系列编辑后的同步。',
            location='Lab 801',
            series=self.series,
            series_order=1,
            start_time=event_time,
            end_time=event_time + timedelta(hours=2),
            checkin_start_time=event_time - timedelta(minutes=15),
            checkin_end_time=event_time + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=event_time - timedelta(hours=1),
            created_by=self.admin_user,
            published_at=event_time - timedelta(hours=1),
        )

    def test_management_can_create_event_series(self):
        self.client.login(username='series-admin', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-series-create'),
            {
                'title': '秋季集训系列',
                'description': '用于测试创建。',
                'series_type': EventSeries.SeriesType.LECTURE,
                'status': EventSeries.Status.DRAFT,
                'start_date': '2026-09-01',
                'end_date': '2026-12-31',
                'expected_event_count': 12,
                'required_checkins_for_rating': 6,
                'rating_enabled': 'on',
                'rating_points': 180,
            },
            follow=True,
        )
        self.assertRedirects(response, reverse('event-series-list-manage'))

        created_series = EventSeries.objects.get(title='秋季集训系列')
        self.assertEqual(created_series.created_by, self.admin_user)
        self.assertEqual(created_series.rating_points, 180)
        self.assertTrue(created_series.rating_enabled)
        self.assertContains(response, '活动系列已创建。')
        self.assertContains(response, '秋季集训系列')

    def test_member_cannot_access_event_series_management(self):
        self.client.login(username='2430026120', password='MemberPassword2026!')
        list_response = self.client.get(reverse('event-series-list-manage'))
        create_response = self.client.get(reverse('event-series-create'))
        edit_response = self.client.get(reverse('event-series-edit', args=[self.series.id]))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(edit_response.status_code, 403)

    def test_editing_event_series_resyncs_completion_and_rating(self):
        CheckInRecord.objects.create(
            member=self.member_profile,
            event=self.event,
            checkin_time=timezone.now() - timedelta(days=2),
            checkin_method=CheckInRecord.Method.WEB,
            status=CheckInRecord.Status.VALID,
            created_by=self.member_user,
        )
        completion = sync_event_series_completion(self.member_profile, self.series)
        profile = sync_member_competition_profile(self.member_profile)

        self.assertFalse(completion.is_completed_for_rating)
        self.assertEqual(profile.current_rating, 0)

        self.client.login(username='series-admin', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-series-edit', args=[self.series.id]),
            {
                'title': self.series.title,
                'description': self.series.description,
                'series_type': self.series.series_type,
                'status': self.series.status,
                'start_date': '',
                'end_date': '',
                'expected_event_count': self.series.expected_event_count,
                'required_checkins_for_rating': 1,
                'rating_enabled': 'on',
                'rating_points': 80,
            },
            follow=True,
        )
        self.assertRedirects(response, reverse('event-series-list-manage'))

        completion.refresh_from_db()
        profile.refresh_from_db()
        self.assertTrue(completion.is_completed_for_rating)
        self.assertEqual(completion.valid_checkin_count, 1)
        self.assertEqual(completion.rating_delta, 80)
        self.assertEqual(profile.current_rating, 80)
        self.assertContains(response, '活动系列已更新。')


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
        cls.training_series = EventSeries.objects.create(
            title='队内十次训练',
            description='用于测试系列签到计分。',
            series_type=EventSeries.SeriesType.TRAINING,
            status=EventSeries.Status.PUBLISHED,
            expected_event_count=10,
            required_checkins_for_rating=2,
            rating_enabled=True,
            rating_points=60,
            created_by=cls.admin_user,
        )

    def create_series_event(self, title, days_offset):
        base_time = timezone.now() - timedelta(days=days_offset)
        return Event.objects.create(
            title=title,
            event_type=Event.EventType.TRAINING,
            description='系列活动测试。',
            location='Lab 701',
            series=self.training_series,
            start_time=base_time,
            end_time=base_time + timedelta(hours=2),
            checkin_start_time=base_time - timedelta(minutes=15),
            checkin_end_time=base_time + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=base_time - timedelta(hours=1),
            created_by=self.admin_user,
            published_at=base_time - timedelta(hours=1),
        )

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

    def test_series_completion_contributes_to_rating_after_threshold(self):
        first_event = self.create_series_event('训练 1', 3)
        second_event = self.create_series_event('训练 2', 1)
        CheckInRecord.objects.create(
            member=self.member_profile,
            event=first_event,
            checkin_time=timezone.now() - timedelta(days=3),
            checkin_method=CheckInRecord.Method.WEB,
            status=CheckInRecord.Status.VALID,
            created_by=self.member_user,
        )
        sync_event_series_completion(self.member_profile, self.training_series)
        profile = sync_member_competition_profile(self.member_profile)
        self.assertEqual(profile.current_rating, 0)

        CheckInRecord.objects.create(
            member=self.member_profile,
            event=second_event,
            checkin_time=timezone.now() - timedelta(days=1),
            checkin_method=CheckInRecord.Method.QR,
            status=CheckInRecord.Status.VALID,
            created_by=self.member_user,
        )
        completion = sync_event_series_completion(self.member_profile, self.training_series)
        profile = sync_member_competition_profile(self.member_profile)

        self.assertTrue(completion.is_completed_for_rating)
        self.assertEqual(completion.valid_checkin_count, 2)
        self.assertEqual(completion.rating_delta, 60)
        self.assertEqual(profile.current_rating, 60)
        self.assertTrue(
            EventSeriesCompletion.objects.filter(
                member=self.member_profile,
                series=self.training_series,
                is_completed_for_rating=True,
            ).exists()
        )

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
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.PROVINCIAL,
                'team_name': 'BNBU Lights',
                'team_members': [teammate_profile.id],
                'external_teammates': '校外队友甲',
                'award_type': ContestResult.AwardType.BRONZE,
                'award_label': '邀请赛铜奖',
                'rank_label': '第 8 名',
                'evidence_url': 'https://example.com/proof',
                'submission_note': '成员自助申报',
            },
            follow=True,
        )
        self.assertEqual(submit_response.status_code, 200)
        submission = ContestSubmission.objects.get(applicant=self.member_user)
        self.assertEqual(submission.review_status, ContestSubmission.ReviewStatus.PENDING)
        self.assertEqual(submission.contest_stage, '')
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

    def test_member_submission_form_hides_evidence_url_field(self):
        self.client.login(username='contest-member', password='MemberPassword2026!')
        response = self.client.get(reverse('member-contest-submission-apply'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '阶段')
        self.assertNotContains(response, 'name="contest_stage"', html=False)
        self.assertNotContains(response, '证据链接')
        self.assertNotContains(response, 'name="evidence_url"', html=False)

    def test_review_form_keeps_submission_contest_date_value(self):
        submission = ContestSubmission.objects.create(
            applicant=self.member_user,
            contest_name='日期回填测试',
            contest_series=Contest.Series.CCPC,
            contest_season='2026',
            contest_stage='校内',
            contest_date=timezone.datetime(2026, 6, 9).date(),
            organizer='BNBU-ACM',
            contest_level=Contest.Level.CAMPUS,
            team_name='日期测试队',
            award_type=ContestResult.AwardType.PARTICIPATION,
            award_label='测试',
            rank_label='第一',
            submission_note='BNBU-ACM',
        )
        submission.team_members.add(self.member_profile)

        self.client.login(username='contest-admin', password='AdminPassword2026!')
        response = self.client.get(reverse('contest-submission-detail-manage', args=[submission.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-06-09"', html=False)

    def test_member_team_create_submission_can_be_approved(self):
        teammate_user = User.objects.create_user(
            username='contest-member-4',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        teammate_profile = MemberProfile.objects.create(
            user=teammate_user,
            real_name='队友四号',
            student_id='2430026444',
            email='u430026444@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        third_user = User.objects.create_user(
            username='contest-member-5',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        third_profile = MemberProfile.objects.create(
            user=third_user,
            real_name='队友五号',
            student_id='2430026555',
            email='u430026555@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )

        self.client.login(username='contest-member', password='MemberPassword2026!')
        create_response = self.client.post(
            reverse('member-team-create'),
            {
                'team_name': 'BNBU Aurora',
                'members': [self.member_profile.id, teammate_profile.id, third_profile.id],
                'captain': self.member_profile.id,
            },
            follow=True,
        )
        self.assertEqual(create_response.status_code, 200)

        submission = MemberTeamSubmission.objects.get(applicant=self.member_user, team_name='BNBU Aurora')
        self.assertEqual(submission.review_status, MemberTeamSubmission.ReviewStatus.PENDING)
        self.assertEqual(submission.members.count(), 3)

        self.client.logout()
        self.client.login(username='contest-admin', password='AdminPassword2026!')
        approve_response = self.client.post(
            reverse('member-team-submission-review', args=[submission.id]),
            {
                'team_name': 'BNBU Aurora',
                'members': [self.member_profile.id, teammate_profile.id, third_profile.id],
                'captain': self.member_profile.id,
                'review_note': '成员信息核对无误。',
                'decision': 'approve',
            },
            follow=True,
        )
        self.assertEqual(approve_response.status_code, 200)

        submission.refresh_from_db()
        self.assertEqual(submission.review_status, MemberTeamSubmission.ReviewStatus.APPROVED)
        self.assertIsNotNone(submission.resolved_team)
        self.assertEqual(submission.resolved_team.name, 'BNBU Aurora')
        self.assertEqual(submission.resolved_team.captain, self.member_profile)
        self.assertEqual(submission.resolved_team.member_count, 3)

    def test_only_team_captain_can_edit_member_team(self):
        captain_user = User.objects.create_user(
            username='contest-member-6',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        captain_profile = MemberProfile.objects.create(
            user=captain_user,
            real_name='队长六号',
            student_id='2430026666',
            email='u430026666@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        teammate_user = User.objects.create_user(
            username='contest-member-7',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        teammate_profile = MemberProfile.objects.create(
            user=teammate_user,
            real_name='队友七号',
            student_id='2430026777',
            email='u430026777@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        member_team = MemberTeam.objects.create(
            name='BNBU Captain Only',
            captain=captain_profile,
            created_by=self.admin_user,
            updated_by=self.admin_user,
        )
        member_team.members.add(captain_profile, self.member_profile, teammate_profile)

        self.client.login(username='contest-member', password='MemberPassword2026!')
        response = self.client.get(reverse('member-team-edit', args=[member_team.id]))
        self.assertEqual(response.status_code, 403)

    def test_submission_team_dropdown_only_shows_current_member_teams(self):
        teammate_user = User.objects.create_user(
            username='contest-member-8',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        teammate_profile = MemberProfile.objects.create(
            user=teammate_user,
            real_name='队友八号',
            student_id='2430026888',
            email='u430026888@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        third_user = User.objects.create_user(
            username='contest-member-9',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        third_profile = MemberProfile.objects.create(
            user=third_user,
            real_name='队友九号',
            student_id='2430026999',
            email='u430026999@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        member_team = MemberTeam.objects.create(
            name='BNBU Optional Link',
            captain=self.member_profile,
            created_by=self.admin_user,
            updated_by=self.admin_user,
        )
        member_team.members.add(self.member_profile, teammate_profile, third_profile)
        outsider_user = User.objects.create_user(
            username='contest-member-10',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        outsider_profile = MemberProfile.objects.create(
            user=outsider_user,
            real_name='队外十号',
            student_id='2430027000',
            email='u430027000@mail.bnbu.edu.cn',
            major='AI',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        outsider_team = MemberTeam.objects.create(
            name='BNBU Hidden Team',
            captain=outsider_profile,
            created_by=self.admin_user,
            updated_by=self.admin_user,
        )
        outsider_team.members.add(outsider_profile, teammate_profile, third_profile)

        self.client.login(username='contest-member', password='MemberPassword2026!')
        response = self.client.get(reverse('member-contest-submission-apply'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'BNBU Optional Link')
        self.assertNotContains(response, 'BNBU Hidden Team')

    def test_linking_member_team_in_submission_directly_writes_team_info(self):
        teammate_user = User.objects.create_user(
            username='contest-member-11',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        teammate_profile = MemberProfile.objects.create(
            user=teammate_user,
            real_name='队友十一号',
            student_id='2430027111',
            email='u430027111@mail.bnbu.edu.cn',
            major='CST',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        third_user = User.objects.create_user(
            username='contest-member-12',
            password='MemberPassword2026!',
            role=User.Roles.MEMBER,
        )
        third_profile = MemberProfile.objects.create(
            user=third_user,
            real_name='队友十二号',
            student_id='2430027222',
            email='u430027222@mail.bnbu.edu.cn',
            major='DS',
            enrollment_year=2024,
            status=MemberProfile.Status.ACTIVE,
        )
        member_team = MemberTeam.objects.create(
            name='BNBU Direct Fill',
            captain=self.member_profile,
            created_by=self.admin_user,
            updated_by=self.admin_user,
        )
        member_team.members.add(self.member_profile, teammate_profile, third_profile)

        self.client.login(username='contest-member', password='MemberPassword2026!')
        response = self.client.post(
            reverse('member-contest-submission-apply'),
            {
                'contest_name': '链接队伍测试赛',
                'contest_series': Contest.Series.INVITATIONAL,
                'contest_season': '2026',
                'contest_date': timezone.localdate(),
                'organizer': '组委会',
                'contest_level': Contest.Level.CAMPUS,
                'linked_member_team': member_team.id,
                'team_name': '',
                'team_members': [],
                'external_teammates': '不会被保存',
                'award_type': ContestResult.AwardType.PARTICIPATION,
                'award_label': '',
                'rank_label': '',
                'evidence_url': '',
                'submission_note': '仅测试队伍关联',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        submission = ContestSubmission.objects.get(contest_name='链接队伍测试赛')
        self.assertEqual(submission.contest_stage, '')
        self.assertEqual(submission.linked_member_team, member_team)
        self.assertEqual(submission.team_name, 'BNBU Direct Fill')
        self.assertEqual(submission.external_teammates, '')
        self.assertQuerysetEqual(
            submission.team_members.order_by('id'),
            [self.member_profile, teammate_profile, third_profile],
            transform=lambda profile: profile,
        )

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


class RuleManagementTests(TestCase):
    def setUp(self):
        self.super_admin_user = User.objects.create_user(
            username='rules-root',
            password='AdminPassword2026!',
            role=User.Roles.SUPER_ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.super_admin_user,
            display_name='规则超级管理员',
            admin_level=AdminProfile.Level.SUPER_ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        self.member_user = User.objects.create_user(
            username='rules-member',
            password='ACM123456',
            role=User.Roles.MEMBER,
        )
        self.member_profile = MemberProfile.objects.create(
            user=self.member_user,
            real_name='规则队员',
            student_id='20267777',
            major='CST',
            enrollment_year=2026,
            status=MemberProfile.Status.ACTIVE,
        )
        self.contest = Contest.objects.create(
            name='规则校赛',
            series=Contest.Series.CAMPUS,
            season='2026',
            contest_date=timezone.localdate(),
            level=Contest.Level.CAMPUS,
            use_default_weight=True,
            weight=1.00,
            status=Contest.Status.PUBLISHED,
            created_by=self.super_admin_user,
        )
        self.team = ContestTeam.objects.create(
            contest=self.contest,
            team_name='Rule Team',
            leader=self.member_profile,
        )
        self.team.members.add(self.member_profile)
        self.result = ContestResult.objects.create(
            contest=self.contest,
            team=self.team,
            award_type=ContestResult.AwardType.SILVER,
            award_label='校赛银奖',
            verified=True,
            verified_by=self.super_admin_user,
            verified_at=timezone.now(),
        )

    def build_rating_rules_payload(self):
        return {
            'base_participation_score': 20,
            'weight_national': '1.60',
            'weight_regional': '1.40',
            'weight_provincial': '1.20',
            'weight_campus': '1.50',
            'weight_internal': '0.80',
            'bonus_gold': 120,
            'bonus_silver': 90,
            'bonus_bronze': 50,
            'bonus_honorable': 25,
            'bonus_finalist': 15,
            'bonus_participation': 5,
            'bonus_custom': 20,
            'threshold_rookie': 1,
            'threshold_solver': 150,
            'threshold_specialist': 500,
            'threshold_expert': 900,
            'threshold_master': 1400,
            'threshold_legend': 2000,
        }

    def test_super_admin_can_manage_rating_rules_and_recalculate_profiles(self):
        profile = sync_member_competition_profile(self.member_profile)
        self.assertEqual(profile.current_rating, 100)
        self.assertEqual(profile.current_level, 'rookie')

        self.client.login(username='rules-root', password='AdminPassword2026!')
        response = self.client.post(
            reverse('rating-rules-manage'),
            self.build_rating_rules_payload(),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rating 规则已更新')
        self.contest.refresh_from_db()
        profile.refresh_from_db()
        self.result.refresh_from_db()

        self.assertEqual(str(self.contest.weight), '1.50')
        self.assertTrue(self.contest.use_default_weight)
        self.assertEqual(self.result.rating_delta, 165)
        self.assertEqual(profile.current_rating, 165)
        self.assertEqual(profile.current_level, 'solver')
        self.assertEqual(SystemSetting.get_value('rating_base_participation_score'), '20')
        self.assertIsNotNone(SystemSetting.get_value('contest_level_weight_rules'))

    def test_contest_with_manual_weight_keeps_its_own_weight(self):
        manual_contest = Contest.objects.create(
            name='手动权重赛',
            series=Contest.Series.CAMPUS,
            season='2026',
            contest_date=timezone.localdate(),
            level=Contest.Level.CAMPUS,
            use_default_weight=False,
            weight=2.00,
            status=Contest.Status.PUBLISHED,
            created_by=self.super_admin_user,
        )
        manual_team = ContestTeam.objects.create(
            contest=manual_contest,
            team_name='Manual Rule Team',
            leader=self.member_profile,
        )
        manual_team.members.add(self.member_profile)
        manual_result = ContestResult.objects.create(
            contest=manual_contest,
            team=manual_team,
            award_type=ContestResult.AwardType.PARTICIPATION,
            award_label='正式参赛',
            verified=True,
            verified_by=self.super_admin_user,
            verified_at=timezone.now(),
        )

        self.client.login(username='rules-root', password='AdminPassword2026!')
        self.client.post(reverse('rating-rules-manage'), self.build_rating_rules_payload(), follow=True)

        manual_contest.refresh_from_db()
        manual_result.refresh_from_db()
        self.assertEqual(str(manual_contest.weight), '2.00')
        self.assertFalse(manual_contest.use_default_weight)
        self.assertEqual(manual_result.rating_delta, 50)

    def test_rule_navigation_and_legacy_settings_route(self):
        self.client.login(username='rules-root', password='AdminPassword2026!')
        dashboard_response = self.client.get(reverse('management-dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, '规则管理')

        overview_response = self.client.get(reverse('management-rule-overview'))
        self.assertEqual(overview_response.status_code, 200)
        self.assertContains(overview_response, '规则总览')
        self.assertContains(overview_response, 'Rating 公式摘要')

        legacy_response = self.client.get(reverse('system-settings-manage'))
        self.assertEqual(legacy_response.status_code, 302)
        self.assertEqual(legacy_response.url, reverse('star-rules-manage'))

    def test_contest_create_preloads_weight_from_current_rule(self):
        SystemSetting.objects.update_or_create(
            key='contest_level_weight_rules',
            defaults={'value': '{"campus": "1.25"}', 'updated_by': self.super_admin_user},
        )

        self.client.login(username='rules-root', password='AdminPassword2026!')
        response = self.client.get(reverse('contest-create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '跟随规则管理中的默认权重')
        self.assertContains(response, '1.25')


class EventSeriesOrderConstraintTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='order-admin',
            password='AdminPassword2026!',
            email='order-admin@mail.bnbu.edu.cn',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        AdminProfile.objects.create(
            user=self.admin_user,
            display_name='序号测试管理员',
            admin_level=AdminProfile.Level.ADMIN,
            status=AdminProfile.Status.ACTIVE,
        )
        self.series = EventSeries.objects.create(
            title='约束测试系列',
            series_type=EventSeries.SeriesType.TRAINING,
            status=EventSeries.Status.PUBLISHED,
            expected_event_count=10,
            created_by=self.admin_user,
        )
        self.base_time = timezone.now() + timedelta(days=1)

    def _make_event(self, title, series=None, series_order=None):
        return Event.objects.create(
            title=title,
            event_type=Event.EventType.TRAINING,
            location='Lab',
            series=series,
            series_order=series_order,
            start_time=self.base_time,
            end_time=self.base_time + timedelta(hours=2),
            checkin_start_time=self.base_time - timedelta(minutes=15),
            checkin_end_time=self.base_time + timedelta(hours=1),
            status=Event.Status.PUBLISHED,
            review_status=Event.ReviewStatus.APPROVED,
            reviewed_by=self.admin_user,
            reviewed_at=self.base_time - timedelta(hours=1),
            created_by=self.admin_user,
            published_at=self.base_time - timedelta(hours=1),
        )

    def test_multiple_events_same_series_without_order(self):
        e1 = self._make_event('无序号活动1', series=self.series, series_order=None)
        e2 = self._make_event('无序号活动2', series=self.series, series_order=None)
        self.assertEqual(Event.objects.filter(series=self.series, series_order__isnull=True).count(), 2)

    def test_duplicate_series_order_raises_integrity_error(self):
        self._make_event('有序号活动1', series=self.series, series_order=1)
        with self.assertRaises(IntegrityError):
            self._make_event('有序号活动2', series=self.series, series_order=1)

    def test_different_series_order_is_allowed(self):
        e1 = self._make_event('序号1', series=self.series, series_order=1)
        e2 = self._make_event('序号2', series=self.series, series_order=2)
        self.assertEqual(Event.objects.filter(series=self.series).count(), 2)

    def test_events_without_series_can_share_order(self):
        e1 = self._make_event('无系列A', series=None, series_order=1)
        e2 = self._make_event('无系列B', series=None, series_order=1)
        self.assertEqual(Event.objects.filter(series__isnull=True).count(), 2)

    def test_clear_series_order_via_edit(self):
        event = self._make_event('待编辑活动', series=self.series, series_order=1)
        event.series_order = None
        event.save()
        event.refresh_from_db()
        self.assertIsNone(event.series_order)
        self.assertEqual(event.series_id, self.series.id)

    def test_clear_series_order_via_view(self):
        event = self._make_event('视图编辑活动', series=self.series, series_order=1)
        self.client.login(username='order-admin', password='AdminPassword2026!')
        response = self.client.post(
            reverse('event-edit', args=[event.id]),
            {
                'title': event.title,
                'event_type': event.event_type,
                'description': '',
                'location': event.location,
                'series': self.series.id,
                'series_order': '',
                'start_time': (self.base_time).strftime('%Y-%m-%dT%H:%M'),
                'end_time': (self.base_time + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (self.base_time - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (self.base_time + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'status': Event.Status.PUBLISHED,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertIsNone(event.series_order)

    def test_event_form_allows_blank_series_order_when_series_is_selected(self):
        self._make_event('同系列空序号活动', series=self.series, series_order=None)
        event = self._make_event('表单校验活动', series=self.series, series_order=1)
        form = EventForm(
            data={
                'title': event.title,
                'event_type': event.event_type,
                'description': '',
                'location': event.location,
                'series': str(self.series.id),
                'series_order': '',
                'start_time': self.base_time.strftime('%Y-%m-%dT%H:%M'),
                'end_time': (self.base_time + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (self.base_time - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (self.base_time + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'status': Event.Status.PUBLISHED,
            },
            instance=event,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertIsNone(form.cleaned_data['series_order'])

    def test_clear_series_order_via_view_when_same_series_has_blank_order_event(self):
        self._make_event('同系列空序号活动2', series=self.series, series_order=None)
        event = self._make_event('视图清空并存活动', series=self.series, series_order=2)
        self.client.login(username='order-admin', password='AdminPassword2026!')

        response = self.client.post(
            reverse('event-edit', args=[event.id]),
            {
                'title': event.title,
                'event_type': event.event_type,
                'description': '',
                'location': event.location,
                'series': self.series.id,
                'series_order': '',
                'start_time': self.base_time.strftime('%Y-%m-%dT%H:%M'),
                'end_time': (self.base_time + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_start_time': (self.base_time - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M'),
                'checkin_end_time': (self.base_time + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'status': Event.Status.PUBLISHED,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertIsNone(event.series_order)

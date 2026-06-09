import re
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .competition import LEVEL_WEIGHT_MAP
from .models import (
    AdminProfile,
    CheckInRecord,
    Contest,
    ContestResult,
    ContestSubmission,
    ContestTeam,
    Event,
    EventSeries,
    MemberTeam,
    MemberTeamSubmission,
    MemberProfile,
    User,
)


USERNAME_EXAMPLE = '2330026083'
SCHOOL_EMAIL_EXAMPLE = 't330026083@mail.bnbu.edu.cn'
SCHOOL_EMAIL_DOMAIN = '@mail.bnbu.edu.cn'
USERNAME_PATTERN = re.compile(r'^\d{10}$')
MAJOR_CODE_PATTERN = re.compile(r'^[A-Z]{2,10}$')


def normalize_major_code(value):
    return value.strip().upper()


def normalize_school_email(value):
    return value.strip().lower()


def apply_widget_attrs(fields):
    text_like_widgets = (
        forms.TextInput,
        forms.EmailInput,
        forms.PasswordInput,
        forms.NumberInput,
        forms.Textarea,
        forms.DateTimeInput,
        forms.Select,
    )
    for field in fields.values():
        widget = field.widget
        attrs = widget.attrs.copy()
        if isinstance(widget, text_like_widgets):
            attrs.setdefault('placeholder', field.label)
        if isinstance(widget, forms.DateTimeInput):
            attrs.setdefault('step', 60)
        widget.attrs = attrs


def validate_event_schedule(form, cleaned_data):
    start_time = cleaned_data.get('start_time')
    end_time = cleaned_data.get('end_time')
    checkin_start_time = cleaned_data.get('checkin_start_time')
    checkin_end_time = cleaned_data.get('checkin_end_time')
    if start_time and end_time and end_time <= start_time:
        form.add_error('end_time', '结束时间必须晚于开始时间。')
    if checkin_start_time and checkin_end_time and checkin_end_time <= checkin_start_time:
        form.add_error('checkin_end_time', '签到结束时间必须晚于签到开始时间。')
    if start_time and checkin_start_time and checkin_start_time > start_time:
        form.add_error('checkin_start_time', '签到开始时间不能晚于活动开始时间。')
    if end_time and checkin_end_time and checkin_end_time > end_time + timedelta(days=1):
        form.add_error('checkin_end_time', '签到结束时间不能晚于活动结束后 24 小时。')


class LoginForm(AuthenticationForm):
    username = forms.CharField(label='用户名')
    password = forms.CharField(label='密码', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['password'].widget.attrs['autocomplete'] = 'current-password'


class MemberRegistrationForm(forms.Form):
    real_name = forms.CharField(label='姓名', max_length=100)
    username = forms.CharField(label='用户名', max_length=150)
    enrollment_year = forms.IntegerField(label='入学年份', min_value=2000, max_value=2100)
    major = forms.CharField(label='专业代码', max_length=10)
    school_email = forms.EmailField(label='学校邮箱')
    password1 = forms.CharField(label='密码', widget=forms.PasswordInput)
    password2 = forms.CharField(label='确认密码', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['username'].widget.attrs.update(
            {
                'autocomplete': 'username',
                'inputmode': 'numeric',
                'pattern': r'\d{10}',
                'maxlength': 10,
                'placeholder': USERNAME_EXAMPLE,
            }
        )
        self.fields['real_name'].widget.attrs['autocomplete'] = 'name'
        self.fields['enrollment_year'].widget.attrs['placeholder'] = '2023'
        self.fields['major'].widget.attrs['placeholder'] = 'CST'
        self.fields['school_email'].widget.attrs.update(
            {
                'autocomplete': 'email',
                'placeholder': SCHOOL_EMAIL_EXAMPLE,
            }
        )
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['username'].help_text = '必须为 10 位纯数字，例如 2330026083。'
        self.fields['major'].help_text = '请输入英文专业代码，例如 CST 或 DS。'
        self.fields['school_email'].help_text = '必须使用 @mail.bnbu.edu.cn 学校邮箱。'

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise forms.ValidationError('用户名必须是 10 位纯数字。')
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('该用户名已存在。')
        return username

    def clean_major(self):
        major = normalize_major_code(self.cleaned_data['major'])
        if not MAJOR_CODE_PATTERN.fullmatch(major):
            raise forms.ValidationError('专业代码只能包含 2-10 位大写英文字母，例如 CST 或 DS。')
        return major

    def clean_school_email(self):
        school_email = normalize_school_email(self.cleaned_data['school_email'])
        if not school_email.endswith(SCHOOL_EMAIL_DOMAIN):
            raise forms.ValidationError('学校邮箱必须使用 @mail.bnbu.edu.cn 域名。')
        return school_email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', '两次输入的密码不一致。')
        if password1 and not self.errors.get('password2'):
            try:
                validate_password(
                    password1,
                    user=User(
                        username=cleaned_data.get('username', ''),
                        email=cleaned_data.get('school_email', ''),
                    ),
                )
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned_data

    @transaction.atomic
    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['school_email'],
            password=self.cleaned_data['password1'],
            role=User.Roles.MEMBER,
        )
        MemberProfile.objects.create(
            user=user,
            real_name=self.cleaned_data['real_name'],
            student_id=self.cleaned_data['username'],
            email=self.cleaned_data['school_email'],
            major=self.cleaned_data['major'],
            enrollment_year=self.cleaned_data['enrollment_year'],
            status=MemberProfile.Status.ACTIVE,
        )
        return user


class PasswordResetRequestForm(forms.Form):
    username = forms.CharField(label='用户名', max_length=150)
    school_email = forms.EmailField(label='学校邮箱')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        apply_widget_attrs(self.fields)
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['school_email'].widget.attrs.update(
            {
                'autocomplete': 'email',
                'placeholder': SCHOOL_EMAIL_EXAMPLE,
            }
        )
        self.fields['school_email'].help_text = '请输入注册时绑定的 @mail.bnbu.edu.cn 邮箱。'

    def clean_school_email(self):
        school_email = normalize_school_email(self.cleaned_data['school_email'])
        if not school_email.endswith(SCHOOL_EMAIL_DOMAIN):
            raise forms.ValidationError('学校邮箱必须使用 @mail.bnbu.edu.cn 域名。')
        return school_email

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username', '').strip()
        school_email = cleaned_data.get('school_email')
        if not username or not school_email:
            return cleaned_data
        self.user = (
            User.objects.filter(
                username=username,
                email__iexact=school_email,
                is_active=True,
            )
            .order_by('id')
            .first()
        )
        if self.user is None:
            raise forms.ValidationError('未找到匹配的账号与邮箱，请检查后重试。')
        return cleaned_data


class PasswordResetConfirmForm(forms.Form):
    username = forms.CharField(label='用户名', max_length=150)
    school_email = forms.EmailField(label='学校邮箱')
    code = forms.CharField(label='邮箱验证码', max_length=6)
    password1 = forms.CharField(label='新密码', widget=forms.PasswordInput)
    password2 = forms.CharField(label='确认新密码', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        apply_widget_attrs(self.fields)
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['school_email'].widget.attrs.update(
            {
                'autocomplete': 'email',
                'placeholder': SCHOOL_EMAIL_EXAMPLE,
            }
        )
        self.fields['code'].widget.attrs.update(
            {
                'autocomplete': 'one-time-code',
                'inputmode': 'numeric',
                'pattern': r'\d{6}',
                'maxlength': 6,
                'placeholder': '6 位数字验证码',
            }
        )
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['school_email'].help_text = '请输入接收验证码的学校邮箱。'
        self.fields['code'].help_text = '验证码默认 10 分钟内有效。'

    def clean_school_email(self):
        school_email = normalize_school_email(self.cleaned_data['school_email'])
        if not school_email.endswith(SCHOOL_EMAIL_DOMAIN):
            raise forms.ValidationError('学校邮箱必须使用 @mail.bnbu.edu.cn 域名。')
        return school_email

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not re.fullmatch(r'\d{6}', code):
            raise forms.ValidationError('验证码必须是 6 位数字。')
        return code

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username', '').strip()
        school_email = cleaned_data.get('school_email')
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if username and school_email:
            self.user = (
                User.objects.filter(
                    username=username,
                    email__iexact=school_email,
                    is_active=True,
                )
                .order_by('id')
                .first()
            )
            if self.user is None:
                self.add_error(None, '未找到匹配的账号与邮箱，请检查后重试。')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', '两次输入的密码不一致。')
        if password1 and self.user and not self.errors.get('password2'):
            try:
                validate_password(password1, user=self.user)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned_data


class PasswordChangeConfirmForm(forms.Form):
    code = forms.CharField(label='邮箱验证码', max_length=6)
    password1 = forms.CharField(label='新密码', widget=forms.PasswordInput)
    password2 = forms.CharField(label='确认新密码', widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        apply_widget_attrs(self.fields)
        self.fields['code'].widget.attrs.update(
            {
                'autocomplete': 'one-time-code',
                'inputmode': 'numeric',
                'pattern': r'\d{6}',
                'maxlength': 6,
                'placeholder': '6 位数字验证码',
            }
        )
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['code'].help_text = '验证码默认 10 分钟内有效。'

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not re.fullmatch(r'\d{6}', code):
            raise forms.ValidationError('验证码必须是 6 位数字。')
        return code

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', '两次输入的密码不一致。')
        if password1 and not self.errors.get('password2'):
            try:
                validate_password(password1, user=self.user)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned_data


class MemberProfileForm(forms.ModelForm):
    email = forms.EmailField(label='学校邮箱')
    major = forms.CharField(label='专业代码', max_length=10)
    enrollment_year = forms.IntegerField(label='入学年份', min_value=2000, max_value=2100)

    class Meta:
        model = MemberProfile
        fields = ['email', 'major', 'enrollment_year', 'phone']
        labels = {
            'email': '学校邮箱',
            'phone': '手机号',
            'major': '专业代码',
            'enrollment_year': '入学年份',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['email'].widget.attrs['placeholder'] = SCHOOL_EMAIL_EXAMPLE
        self.fields['major'].widget.attrs['placeholder'] = 'CST'
        self.fields['enrollment_year'].widget.attrs['placeholder'] = '2023'
        self.fields['email'].help_text = '必须使用 @mail.bnbu.edu.cn 学校邮箱。'
        self.fields['major'].help_text = '请输入英文专业代码，例如 CST 或 DS。'

    def clean_email(self):
        email = normalize_school_email(self.cleaned_data['email'])
        if not email.endswith(SCHOOL_EMAIL_DOMAIN):
            raise forms.ValidationError('学校邮箱必须使用 @mail.bnbu.edu.cn 域名。')
        return email

    def clean_major(self):
        major = normalize_major_code(self.cleaned_data['major'])
        if not MAJOR_CODE_PATTERN.fullmatch(major):
            raise forms.ValidationError('专业代码只能包含 2-10 位大写英文字母，例如 CST 或 DS。')
        return major


class EventForm(forms.ModelForm):
    checkin_managers = forms.ModelMultipleChoiceField(
        label='签到管理员 Members',
        required=False,
        queryset=User.objects.none(),
        help_text='最多可指定 5 名 Member 管理该活动的签到二维码、完整签到名单、补签与撤销。',
        widget=forms.MultipleHiddenInput(),
    )
    start_time = forms.DateTimeField(label='开始时间', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    end_time = forms.DateTimeField(label='结束时间', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    checkin_start_time = forms.DateTimeField(
        label='签到开始时间',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    checkin_end_time = forms.DateTimeField(
        label='签到结束时间',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    series_order = forms.IntegerField(label='系列内序号', min_value=1, required=False)

    class Meta:
        model = Event
        fields = [
            'title',
            'event_type',
            'description',
            'location',
            'series',
            'series_order',
            'start_time',
            'end_time',
            'checkin_start_time',
            'checkin_end_time',
            'checkin_managers',
            'status',
        ]
        labels = {
            'title': '活动名称',
            'event_type': '活动类型',
            'description': '活动描述',
            'location': '活动地点',
            'series': '所属系列',
            'status': '状态',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['checkin_managers'].queryset = (
            User.objects.filter(
                role=User.Roles.MEMBER,
                is_active=True,
                member_profile__status=MemberProfile.Status.ACTIVE,
            )
            .select_related('member_profile')
            .order_by('member_profile__real_name', 'username')
        )
        self.fields['checkin_managers'].label_from_instance = (
            lambda user: f'{user.member_profile.real_name} ({user.member_profile.student_id})'
        )
        self.fields['series'].queryset = EventSeries.objects.order_by('-created_at', 'title')
        self.fields['series'].required = False
        self.fields['series'].help_text = '可选。选择后，这场活动将归入对应系列。'
        self.fields['series_order'].help_text = '可选。用于标识这是系列中的第几次活动。'

    def clean_checkin_managers(self):
        checkin_managers = self.cleaned_data['checkin_managers']
        if checkin_managers.count() > 5:
            raise forms.ValidationError('签到管理员最多只能指定 5 名。')
        return checkin_managers

    def clean(self):
        cleaned_data = super().clean()
        validate_event_schedule(self, cleaned_data)
        series = cleaned_data.get('series')
        series_order = cleaned_data.get('series_order')
        if series_order and not series:
            self.add_error('series', '填写系列内序号前，请先选择所属系列。')
        if series and series_order and series.expected_event_count and series_order > series.expected_event_count:
            self.add_error('series_order', '系列内序号不能超过该系列的预期活动总数。')
        return cleaned_data


class EventSeriesForm(forms.ModelForm):
    class Meta:
        model = EventSeries
        fields = [
            'title',
            'description',
            'series_type',
            'status',
            'start_date',
            'end_date',
            'expected_event_count',
            'required_checkins_for_rating',
            'rating_enabled',
            'rating_points',
        ]
        labels = {
            'title': '系列名称',
            'description': '系列描述',
            'series_type': '系列类型',
            'status': '状态',
            'start_date': '开始日期',
            'end_date': '结束日期',
            'expected_event_count': '预期活动总数',
            'required_checkins_for_rating': '计入 Rating 所需签到次数',
            'rating_enabled': '参与 Rating',
            'rating_points': 'Rating 分值',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['start_date'].widget = forms.DateInput(attrs={'type': 'date'})
        self.fields['end_date'].widget = forms.DateInput(attrs={'type': 'date'})

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', '结束日期不能早于开始日期。')
        return cleaned_data


class EventApplicationForm(forms.ModelForm):
    start_time = forms.DateTimeField(label='开始时间', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    end_time = forms.DateTimeField(label='结束时间', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    checkin_start_time = forms.DateTimeField(
        label='签到开始时间',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    checkin_end_time = forms.DateTimeField(
        label='签到结束时间',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )

    class Meta:
        model = Event
        fields = [
            'title',
            'event_type',
            'description',
            'location',
            'start_time',
            'end_time',
            'checkin_start_time',
            'checkin_end_time',
        ]
        labels = {
            'title': '活动名称',
            'event_type': '活动类型',
            'description': '活动说明',
            'location': '活动地点',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['description'].help_text = '可填写活动目的、分享主题或讲座安排，方便管理员审核。'

    def clean(self):
        cleaned_data = super().clean()
        validate_event_schedule(self, cleaned_data)
        return cleaned_data


class EventReviewForm(forms.Form):
    review_note = forms.CharField(
        label='审核说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '可填写通过/驳回原因，便于申请人查看。'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)


class ManualCheckInForm(forms.Form):
    member_keyword = forms.CharField(
        label='队员学号或用户名',
        max_length=150,
        help_text='输入队员学号或登录用户名，为该活动补签。',
    )
    remark = forms.CharField(
        label='补签说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '例如：线下核验已到场，补录签到。'}),
    )

    def __init__(self, event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.member_profile = None
        apply_widget_attrs(self.fields)

    def clean_member_keyword(self):
        member_keyword = self.cleaned_data['member_keyword'].strip()
        if not member_keyword:
            raise forms.ValidationError('请输入队员学号或用户名。')
        self.member_profile = (
            MemberProfile.objects.select_related('user')
            .filter(
                status=MemberProfile.Status.ACTIVE,
            )
            .filter(
                Q(student_id=member_keyword) | Q(user__username=member_keyword)
            )
            .order_by('id')
            .first()
        )
        if self.member_profile is None:
            raise forms.ValidationError('未找到匹配的队员。')
        if CheckInRecord.objects.filter(
            member=self.member_profile,
            event=self.event,
            status=CheckInRecord.Status.VALID,
        ).exists():
            raise forms.ValidationError('该队员已经存在有效签到记录。')
        return member_keyword


class AdminCreateForm(forms.Form):
    username = forms.CharField(label='用户名', max_length=150)
    display_name = forms.CharField(label='显示名称', max_length=100)
    email = forms.EmailField(label='邮箱', required=False)
    admin_level = forms.ChoiceField(label='管理员级别', choices=AdminProfile.Level.choices)
    password1 = forms.CharField(label='初始密码', widget=forms.PasswordInput)
    password2 = forms.CharField(label='确认密码', widget=forms.PasswordInput)
    is_active = forms.BooleanField(label='账号启用', required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['email'].widget.attrs['autocomplete'] = 'email'
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('该用户名已存在。')
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', '两次输入的密码不一致。')
        return cleaned_data


class AdminUpdateForm(forms.Form):
    display_name = forms.CharField(label='显示名称', max_length=100)
    email = forms.EmailField(label='邮箱', required=False)
    admin_level = forms.ChoiceField(label='管理员级别', choices=AdminProfile.Level.choices)
    status = forms.ChoiceField(label='资料状态', choices=AdminProfile.Status.choices)
    is_active = forms.BooleanField(label='账号启用', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['email'].widget.attrs['autocomplete'] = 'email'


class ContestForm(forms.ModelForm):
    contest_date = forms.DateField(label='比赛日期', widget=forms.DateInput(attrs={'type': 'date'}))

    class Meta:
        model = Contest
        fields = [
            'name',
            'series',
            'season',
            'stage',
            'contest_date',
            'organizer',
            'level',
            'weight',
            'status',
            'description',
        ]
        labels = {
            'name': '赛事名称',
            'series': '赛事系列',
            'season': '赛季/学年',
            'stage': '阶段',
            'organizer': '主办方',
            'level': '赛事级别',
            'weight': '评分权重',
            'status': '状态',
            'description': '说明',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        self.fields['season'].widget.attrs['placeholder'] = '2026'
        self.fields['stage'].widget.attrs['placeholder'] = '区域赛 / 校内选拔'
        self.fields['organizer'].widget.attrs['placeholder'] = '主办方，可选'
        self.fields['description'].help_text = '可填写赛事背景、说明或收录口径。'

    def clean(self):
        cleaned_data = super().clean()
        level = cleaned_data.get('level')
        weight = cleaned_data.get('weight')
        if level and not weight:
            cleaned_data['weight'] = LEVEL_WEIGHT_MAP.get(level)
        return cleaned_data


class ContestTeamForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        label='队员列表',
        queryset=MemberProfile.objects.none(),
        help_text='可多选该队伍参赛成员。',
    )
    leader = forms.ModelChoiceField(
        label='队长',
        queryset=MemberProfile.objects.none(),
        required=False,
    )

    class Meta:
        model = ContestTeam
        fields = ['team_name', 'members', 'external_member_names', 'leader', 'coach_name', 'note']
        labels = {
            'team_name': '队伍名称',
            'external_member_names': '未建档队员',
            'coach_name': '指导老师/带队老师',
            'note': '备注',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        member_queryset = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
        if self.instance and self.instance.pk:
            existing_member_ids = list(self.instance.members.values_list('id', flat=True))
            if existing_member_ids:
                member_queryset = member_queryset | MemberProfile.objects.filter(id__in=existing_member_ids)
            if self.instance.leader_id:
                member_queryset = member_queryset | MemberProfile.objects.filter(id=self.instance.leader_id)
        member_queryset = member_queryset.distinct().order_by('real_name')
        self.fields['members'].queryset = member_queryset
        self.fields['leader'].queryset = member_queryset
        self.fields['members'].label_from_instance = lambda member: f'{member.real_name} ({member.student_id})'
        self.fields['leader'].label_from_instance = lambda member: f'{member.real_name} ({member.student_id})'
        self.fields['external_member_names'].widget.attrs['placeholder'] = '使用 顿号 分隔，例如 张三、李四'
        self.fields['coach_name'].widget.attrs['placeholder'] = '可选'

    def clean(self):
        cleaned_data = super().clean()
        members = cleaned_data.get('members')
        leader = cleaned_data.get('leader')
        if leader and members is not None and leader not in members:
            self.add_error('leader', '队长必须包含在所选队员中。')
        return cleaned_data


class MemberTeamSubmissionForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        label='队伍成员',
        queryset=MemberProfile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text='固定 3 名成员，可搜索后勾选；申请人必须在队伍中。',
    )
    captain = forms.ModelChoiceField(
        label='队长',
        queryset=MemberProfile.objects.none(),
        help_text='只有队长在审核通过后拥有编辑队伍权限。',
    )

    class Meta:
        model = MemberTeamSubmission
        fields = ['team_name', 'members', 'captain']
        labels = {
            'team_name': '队伍名称',
        }

    def __init__(self, applicant_profile=None, *args, **kwargs):
        self.applicant_profile = applicant_profile
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        member_queryset = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE)
        current_member_ids = []
        if self.instance and self.instance.pk:
            current_member_ids = list(self.instance.members.values_list('id', flat=True))
        if current_member_ids:
            member_queryset = member_queryset | MemberProfile.objects.filter(id__in=current_member_ids)
        if self.instance and self.instance.captain_id:
            member_queryset = member_queryset | MemberProfile.objects.filter(id=self.instance.captain_id)
        member_queryset = member_queryset.distinct().order_by('real_name', 'student_id')
        self.fields['members'].queryset = member_queryset
        self.fields['captain'].queryset = member_queryset
        self.fields['members'].label_from_instance = lambda member: f'{member.real_name} ({member.student_id})'
        self.fields['captain'].label_from_instance = lambda member: f'{member.real_name} ({member.student_id})'
        self.fields['team_name'].widget.attrs['placeholder'] = '例如 BNBU Rising'

    def clean_team_name(self):
        return self.cleaned_data['team_name'].strip()

    def clean(self):
        cleaned_data = super().clean()
        members = cleaned_data.get('members')
        captain = cleaned_data.get('captain')
        if members is None:
            return cleaned_data
        if len(members) != 3:
            self.add_error('members', '每个队伍必须恰好选择 3 名成员。')
        if captain and captain not in members:
            self.add_error('captain', '队长必须包含在所选成员中。')
        if self.applicant_profile and self.applicant_profile not in members:
            self.add_error('members', '提交申请的队员必须包含在队伍成员中。')
        return cleaned_data


class MemberTeamSubmissionReviewForm(MemberTeamSubmissionForm):
    review_note = forms.CharField(
        label='审核说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '可填写通过/驳回原因，便于队员查看。'}),
    )


class ContestResultForm(forms.ModelForm):
    verified = forms.BooleanField(label='立即认证', required=False)

    class Meta:
        model = ContestResult
        fields = [
            'team',
            'award_type',
            'award_label',
            'rank_label',
            'result_tier',
            'manual_bonus',
            'verified',
            'evidence_url',
            'note',
        ]
        labels = {
            'team': '参赛队伍',
            'award_type': '奖项类型',
            'award_label': '奖项展示文案',
            'rank_label': '名次描述',
            'result_tier': '成绩层级',
            'manual_bonus': '人工加分',
            'evidence_url': '证据链接',
            'note': '备注',
        }

    def __init__(self, contest, *args, **kwargs):
        self.contest = contest
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        team_queryset = contest.teams.prefetch_related('members').order_by('team_name')
        self.fields['team'].queryset = team_queryset
        self.fields['team'].label_from_instance = lambda team: f'{team.team_name} · {team.member_names}'
        self.fields['award_label'].required = False
        self.fields['rank_label'].required = False
        self.fields['evidence_url'].required = False
        self.fields['manual_bonus'].help_text = '仅在默认口径不够表达时使用，可填正负整数。'
        if self.instance and self.instance.pk:
            self.fields['verified'].initial = self.instance.verified

    def clean_award_label(self):
        return self.cleaned_data['award_label'].strip()

    def clean_rank_label(self):
        return self.cleaned_data['rank_label'].strip()

    def clean(self):
        cleaned_data = super().clean()
        team = cleaned_data.get('team')
        if team and team.contest_id != self.contest.id:
            self.add_error('team', '所选队伍不属于当前赛事。')
        if (
            team
            and ContestResult.objects.filter(contest=self.contest, team=team)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            self.add_error('team', '该队伍已经录入过成绩，请直接编辑原记录。')
        return cleaned_data


class ContestSubmissionForm(forms.ModelForm):
    contest_date = forms.DateField(label='比赛日期', widget=forms.DateInput(attrs={'type': 'date'}))
    linked_member_team = forms.ModelChoiceField(
        label='选择队伍（可选）',
        queryset=MemberTeam.objects.none(),
        required=False,
        help_text='仅作为关联队伍展示，不会自动覆盖下面填写的申报信息。',
    )
    team_members = forms.ModelMultipleChoiceField(
        label='队内成员',
        queryset=MemberProfile.objects.none(),
        required=False,
        help_text='可选择已经在系统中的队友；你自己会自动加入。',
    )

    class Meta:
        model = ContestSubmission
        fields = [
            'contest_name',
            'contest_series',
            'contest_season',
            'contest_stage',
            'contest_date',
            'organizer',
            'contest_level',
            'linked_member_team',
            'team_name',
            'team_members',
            'external_teammates',
            'award_type',
            'award_label',
            'rank_label',
            'result_tier',
            'evidence_url',
            'submission_note',
        ]
        labels = {
            'contest_name': '赛事名称',
            'contest_series': '赛事系列',
            'contest_season': '赛季/学年',
            'contest_stage': '阶段',
            'organizer': '主办方',
            'contest_level': '赛事级别',
            'linked_member_team': '选择队伍（可选）',
            'team_name': '队伍名称',
            'external_teammates': '未建档队友',
            'award_type': '奖项类型',
            'award_label': '奖项展示文案',
            'rank_label': '名次描述',
            'result_tier': '成绩层级',
            'evidence_url': '证据链接',
            'submission_note': '补充说明',
        }

    def __init__(self, applicant_profile=None, *args, **kwargs):
        self.applicant_profile = applicant_profile
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)
        member_queryset = MemberProfile.objects.filter(status=MemberProfile.Status.ACTIVE).order_by('real_name')
        team_queryset = MemberTeam.objects.none()
        if applicant_profile is not None:
            team_queryset = (
                MemberTeam.objects.filter(members=applicant_profile)
                .select_related('captain')
                .distinct()
                .order_by('name', 'id')
            )
        if self.instance and self.instance.pk and self.instance.linked_member_team_id:
            team_queryset = (
                team_queryset | MemberTeam.objects.filter(id=self.instance.linked_member_team_id).select_related('captain')
            ).distinct().order_by('name', 'id')
        self.fields['linked_member_team'].queryset = team_queryset
        self.fields['linked_member_team'].label_from_instance = (
            lambda team: f'{team.name} · 队长 {team.captain.real_name if team.captain else "待定"}'
        )
        self.fields['team_members'].queryset = member_queryset
        self.fields['team_members'].label_from_instance = lambda member: f'{member.real_name} ({member.student_id})'
        self.fields['contest_season'].widget.attrs['placeholder'] = '2026'
        self.fields['contest_stage'].widget.attrs['placeholder'] = '区域赛 / 省赛 / 校内选拔'
        self.fields['team_name'].widget.attrs['placeholder'] = '例如 BNBU Rising'
        self.fields['external_teammates'].widget.attrs['placeholder'] = '用 顿号 分隔，例如 校外队友A、校外队友B'
        self.fields['award_label'].required = False
        self.fields['rank_label'].required = False
        self.fields['evidence_url'].required = False
        self.fields['submission_note'].required = False

    def clean_external_teammates(self):
        return self.cleaned_data['external_teammates'].strip()

    def clean_award_label(self):
        return self.cleaned_data['award_label'].strip()

    def clean_rank_label(self):
        return self.cleaned_data['rank_label'].strip()

    def clean(self):
        return super().clean()


class ContestSubmissionReviewForm(ContestSubmissionForm):
    linked_contest = forms.ModelChoiceField(
        label='合并到已有赛事',
        queryset=Contest.objects.none(),
        required=False,
        help_text='如该赛事已存在，可直接并入已有赛事；留空则按当前表单内容创建/更新正式赛事。',
    )
    review_note = forms.CharField(
        label='审核说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '可填写通过/驳回原因，便于成员查看。'}),
    )

    def __init__(self, applicant_profile=None, *args, **kwargs):
        super().__init__(applicant_profile=applicant_profile, *args, **kwargs)
        self.fields['linked_contest'].queryset = Contest.objects.order_by('-contest_date', '-id')
        self.fields['linked_contest'].label_from_instance = lambda contest: f'{contest.name} · {contest.contest_date:%Y-%m-%d}'
        if self.instance and self.instance.pk and self.instance.resolved_contest_id:
            self.fields['linked_contest'].initial = self.instance.resolved_contest_id


class SystemSettingsForm(forms.Form):
    star_recent_window_days = forms.IntegerField(label='ACM Star 近期窗口天数', min_value=1, max_value=365)
    qr_code_expire_minutes = forms.IntegerField(label='签到入口总有效分钟数', min_value=1, max_value=1440)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)

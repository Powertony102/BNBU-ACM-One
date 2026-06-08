import re
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import AdminProfile, CheckInRecord, Event, MemberProfile, User


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
            'checkin_managers',
            'status',
        ]
        labels = {
            'title': '活动名称',
            'event_type': '活动类型',
            'description': '活动描述',
            'location': '活动地点',
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

    def clean_checkin_managers(self):
        checkin_managers = self.cleaned_data['checkin_managers']
        if checkin_managers.count() > 5:
            raise forms.ValidationError('签到管理员最多只能指定 5 名。')
        return checkin_managers

    def clean(self):
        cleaned_data = super().clean()
        validate_event_schedule(self, cleaned_data)
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


class SystemSettingsForm(forms.Form):
    star_recent_window_days = forms.IntegerField(label='ACM Star 近期窗口天数', min_value=1, max_value=365)
    qr_code_expire_minutes = forms.IntegerField(label='二维码有效分钟数', min_value=1, max_value=1440)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)

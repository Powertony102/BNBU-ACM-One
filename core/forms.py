import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import AdminProfile, Event, MemberProfile, User


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

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        checkin_start_time = cleaned_data.get('checkin_start_time')
        checkin_end_time = cleaned_data.get('checkin_end_time')
        if start_time and end_time and end_time <= start_time:
            self.add_error('end_time', '结束时间必须晚于开始时间。')
        if checkin_start_time and checkin_end_time and checkin_end_time <= checkin_start_time:
            self.add_error('checkin_end_time', '签到结束时间必须晚于签到开始时间。')
        return cleaned_data


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

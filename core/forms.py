from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import AdminProfile, Event, MemberProfile, User


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


class MemberProfileForm(forms.ModelForm):
    class Meta:
        model = MemberProfile
        fields = ['email', 'phone', 'major', 'class_name']
        labels = {
            'email': '邮箱',
            'phone': '手机号',
            'major': '专业',
            'class_name': '班级',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_widget_attrs(self.fields)


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

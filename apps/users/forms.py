import re
import logging

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth import forms as auth_forms
from django.utils.translation import ugettext as _

from drumbeat.utils import CKEditorWidget

from captcha import fields as captcha_fields

from users.blacklist import passwords as blacklisted_passwords
from users.models import UserProfile
from users.fields import UsernameField
from users import drupal
from links.models import Link


log = logging.getLogger(__name__)


class AuthenticationForm(auth_forms.AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={'tabindex': '1'}))
    password = forms.CharField(
        max_length=255,
        widget=forms.PasswordInput(attrs={'tabindex': '2'},
                                   render_value=False))
    remember_me = forms.BooleanField(required=False,
                                     widget=forms.CheckboxInput(
                                         attrs={'tabindex': '3'}))


def check_password_complexity(password):
    message = _('Password must be at least 8 characters long ')
    message += _('and contain both numbers and letters.')
    if len(password) < 8 or not (
        re.search('[A-Za-z]', password) and re.search('[0-9]', password)):
        return message
    if password in blacklisted_passwords:
        return _('That password is too common. Please choose another.')
    return None


class SetPasswordForm(auth_forms.SetPasswordForm):

    def __init__(self, *args, **kwargs):
        super(SetPasswordForm, self).__init__(*args, **kwargs)

        # make sure to set the password in the user profile
        if isinstance(self.user, User):
            self.user = self.user.get_profile()

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        message = check_password_complexity(password)
        if message:
            self._errors['new_password1'] = forms.util.ErrorList([message])
        return password


class OpenIDForm(forms.Form):
    openid_identifier = forms.URLField()

    def clean_openid_identifier(self):
        msg = _('This google openid is not supported. ')
        msg += _('Please use your google profile openid.')
        openid = self.cleaned_data['openid_identifier']
        if drupal.GOOGLE_OPENID in openid:
            raise forms.ValidationError(msg)
        return openid


def validate_user_identity(form, data):
    msg = _('Your account was migrated from the old P2PU website.')
    username_provided = ('username' in data and data['username'])
    if username_provided and drupal.get_user(data['username']):
        form._errors['username'] = forms.util.ErrorList([msg])
    email_provided = ('email' in data and data['email'])
    if email_provided and drupal.get_user(data['email']):
        form._errors['email'] = forms.util.ErrorList([msg])


class CreateProfileForm(forms.ModelForm):
    recaptcha = captcha_fields.ReCaptchaField()

    class Meta:
        model = UserProfile
        fields = ('username', 'full_name', 'newsletter', 'email')
        widgets = {
            'username': forms.TextInput(attrs={'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super(CreateProfileForm, self).__init__(*args, **kwargs)

        if not settings.RECAPTCHA_PRIVATE_KEY:
            del self.fields['recaptcha']

    def clean(self):
        super(CreateProfileForm, self).clean()
        data = self.cleaned_data
        validate_user_identity(self, data)
        if 'full_name' in data:
            data['full_name'] = data['full_name'].strip()
        return data


class RegisterForm(forms.ModelForm):
    username = UsernameField()
    full_name = forms.CharField(max_length=255, required=False)
    password = forms.CharField(
        max_length=128,
        widget=forms.PasswordInput(render_value=False))
    password_confirm = forms.CharField(
        max_length=128,
        widget=forms.PasswordInput(render_value=False))
    newsletter = forms.BooleanField(required=False)
    preflang = forms.CharField(max_length=3,
            widget=forms.Select(choices=settings.SUPPORTED_LANGUAGES))
    recaptcha = captcha_fields.ReCaptchaField()

    class Meta:
        model = User
        fields = ('username', 'email', 'preflang')
        widgets = {
            'username': forms.TextInput(attrs={'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super(RegisterForm, self).__init__(*args, **kwargs)

        if not settings.RECAPTCHA_PRIVATE_KEY:
            del self.fields['recaptcha']

    def clean_password(self):
        password = self.cleaned_data['password']
        message = check_password_complexity(password)
        if message:
            self._errors['password'] = forms.util.ErrorList([message])
        return password

    def clean_username(self):
        username = self.cleaned_data['username']
        if UserProfile.objects.filter(username=username).exists():
            raise forms.ValidationError(
                _('User profile with this Username already exists.'))
        return username

    def clean_email(self):
        email = self.cleaned_data['email']
        if not email or not email.strip():
            raise forms.ValidationError(_('This field is required.'))
        if UserProfile.objects.filter(email=email).exists():
            raise forms.ValidationError(
                _('User profile with this Email already exists.'))
        return email

    def clean(self):
        super(RegisterForm, self).clean()
        data = self.cleaned_data
        validate_user_identity(self, data)
        if 'password' in data and 'password_confirm' in data:
            if data['password'] != data['password_confirm']:
                self._errors['password_confirm'] = forms.util.ErrorList([
                    _('Passwords do not match.')])
        if 'full_name' in data:
            data['full_name'] = data['full_name'].strip()
        return data


class ProfileEditForm(forms.ModelForm):

    class Meta:
        model = UserProfile
        fields = ('full_name', 'location', 'bio', 'preflang',)
        widgets = {
            'bio': CKEditorWidget(config_name='reduced'),
        }

    def clean(self):
        super(ProfileEditForm, self).clean()
        data = self.cleaned_data
        if 'full_name' in data:
            data['full_name'] = data['full_name'].strip()
        return data


class ProfileImageForm(forms.ModelForm):

    class Meta:
        model = UserProfile
        fields = ('image',)

    def clean_image(self):
        if self.cleaned_data['image'] is False:
            return self.cleaned_data['image']
        if self.cleaned_data['image'].size > settings.MAX_IMAGE_SIZE:
            max_size = settings.MAX_IMAGE_SIZE / 1024
            raise forms.ValidationError(
                _("Image exceeds max image size: %(max)dk") % dict(
                    max=max_size))
        return self.cleaned_data['image']


class ProfileLinksForm(forms.ModelForm):

    class Meta:
        model = Link
        fields = ('name', 'url', 'subscribe',)


class PasswordResetForm(auth_forms.PasswordResetForm):

    def clean_email(self):
        email = self.cleaned_data["email"]
        self.users_cache = User.objects.filter(
                                email__iexact=email,
                                is_active=True
                            )
        if len(self.users_cache) == 0 and not drupal.migrate(email):
            msg = _("That e-mail address isn't associated to a user account. ")
            msg += _("Are you sure you've registered?")
            raise forms.ValidationError(msg)
        profile = None
        for user in self.users_cache:
            try:
                profile = user.get_profile()
            except UserProfile.DoesNotExist:
                user.delete()
        if not profile:
            msg = _("You did not finish the registration proccess last time. ")
            msg += _("Please register a new account.")
            raise forms.ValidationError(msg)
        return email

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import User


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "At least 10 characters"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Repeat your password"}
        ),
    )

    class Meta:
        model = User
        fields = ("email",)
        widgets = {
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "you@example.com"}
            ),
        }

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords don't match.")
        if p1:
            from django.contrib.auth.password_validation import validate_password

            validate_password(p1, self.instance)
        return cleaned

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class InstagramEmailForm(forms.Form):
    """The one extra field a first-time Instagram sign-in needs."""

    EMAIL_TAKEN = (
        "An account already uses this email. Sign in to it and we'll connect your Instagram there."
    )

    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(self.EMAIL_TAKEN)
        return email


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "placeholder": "Your password"}
        ),
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("full_name",)


class DeleteAccountForm(forms.Form):
    """Deleting an account is irreversible, so require the email as confirmation."""

    confirm_email = forms.EmailField(label="Type your email to confirm")

    def __init__(self, *args, user: User, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_confirm_email(self) -> str:
        value = self.cleaned_data["confirm_email"].strip().lower()
        if value != self.user.email:
            raise forms.ValidationError("That doesn't match your account email.")
        return value

    def clean(self):
        from apps.billing.services.access import LIVE_STATUSES, current_subscription

        cleaned = super().clean()
        subscription = current_subscription(self.user)
        if (
            subscription is not None
            and subscription.status in LIVE_STATUSES
            and not subscription.cancel_at_period_end
        ):
            # Deleting doesn't touch billing, so a renewing plan would keep charging.
            raise forms.ValidationError(
                "Cancel your plan on Plan & billing before deleting your account, "
                "so you aren't charged again."
            )
        return cleaned

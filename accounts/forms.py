from django import forms


class TokenForm(forms.Form):
    token = forms.CharField(
        label="Verification code",
        help_text="The 6-digit code from your authenticator app, or a backup code.",
        max_length=20,
        # id "totp" and the "Verification code" label let password managers (e.g. Bitwarden) recognize
        # the field and fill a stored TOTP code; autocomplete=one-time-code covers browsers and iOS.
        widget=forms.TextInput(
            attrs={
                "id": "totp",
                "autocomplete": "one-time-code",
                "autofocus": True,
                "spellcheck": "false",
                "autocapitalize": "off",
            }
        ),
    )

    def clean_token(self):
        return "".join(self.cleaned_data["token"].split()).replace("-", "").lower()

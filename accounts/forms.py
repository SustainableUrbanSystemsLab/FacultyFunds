from django import forms


class TokenForm(forms.Form):
    token = forms.CharField(
        label="Verification code",
        help_text="The 6-digit code from your authenticator app, or one of your saved single-use codes.",
        max_length=20,
        # id "totp", the "Verification code" label and autocomplete=one-time-code let password managers
        # fill a stored TOTP code. Keep the words "backup" and "recovery" out of the label, help text and
        # nearby text on pages using this form: Bitwarden then treats the field as a recovery-code box
        # and refuses to fill it.
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

from django import forms


class TokenForm(forms.Form):
    token = forms.CharField(
        label="6-digit code or backup code",
        max_length=20,
        widget=forms.TextInput(
            attrs={"autocomplete": "one-time-code", "autofocus": True, "spellcheck": "false", "autocapitalize": "off"}
        ),
    )

    def clean_token(self):
        return "".join(self.cleaned_data["token"].split()).replace("-", "").lower()

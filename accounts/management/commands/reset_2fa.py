from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Remove all two-factor devices for a user (lost phone and backup codes). Re-enroll immediately afterwards."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user named {options['username']!r}.") from exc
        totp_count = TOTPDevice.objects.filter(user=user).delete()[0]
        static_count = StaticDevice.objects.filter(user=user).delete()[0]
        self.stdout.write(
            self.style.WARNING(
                f"Removed {totp_count} authenticator and {static_count} backup-code records. "
                "Sign in now and set up two-factor authentication again: until you do, "
                "the password alone lets someone enroll their own authenticator."
            )
        )

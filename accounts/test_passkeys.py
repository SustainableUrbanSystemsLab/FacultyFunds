"""Passkeys end to end, using a small software authenticator instead of a real device."""

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp_webauthn.models import WebAuthnCredential

PASSWORD = "correct horse battery staple"
ORIGIN = "http://localhost:8000"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftwareAuthenticator:
    """A P-256 platform authenticator with "none" attestation, like a phone or password manager."""

    def __init__(self, rp_id: str, user_verified: bool = True):
        self.rp_id = rp_id
        self.user_verified = user_verified
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.user_handle = b""
        self.sign_count = 0

    def _authenticator_data(self, attested: bool) -> bytes:
        flags = 0x01 | (0x04 if self.user_verified else 0) | (0x40 if attested else 0)  # UP, UV, AT
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.sign_count)
        if attested:
            numbers = self.key.public_key().public_numbers()
            cose_key = {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cbor2.dumps(cose_key)
        return data

    @staticmethod
    def _client_data(kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        self.user_handle = unb64url(options["user"]["id"])
        client_data = self._client_data("webauthn.create", options["challenge"])
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._authenticator_data(attested=True)})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {"credProps": {"rk": True}},
            "authenticatorAttachment": "platform",
        }

    def get(self, options: dict) -> dict:
        self.sign_count += 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        authenticator_data = self._authenticator_data(attested=False)
        signature = self.key.sign(authenticator_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle),
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }


def make_passkey(user, name=""):
    credential_id = os.urandom(16)
    return WebAuthnCredential.objects.create(
        user=user,
        name=name,
        credential_id=credential_id,
        credential_id_sha256=hashlib.sha256(credential_id).hexdigest(),
        public_key=b"\x01",
        aaguid="00000000-0000-0000-0000-000000000000",
    )


class PasskeyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password=PASSWORD)
        self.totp = TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)

    def sign_in_with_password(self):
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})

    def verify(self):
        self.sign_in_with_password()
        token = f"{totp(self.totp.bin_key, self.totp.step, self.totp.t0, self.totp.digits):06d}"
        self.assertEqual(self.client.post(reverse("accounts:verify"), {"token": token}).status_code, 302)

    def post_json(self, name, payload=None):
        return self.client.post(reverse(name), data=json.dumps(payload or {}), content_type="application/json")

    def register(self, authenticator):
        options = self.post_json("otp_webauthn:credential-registration-begin").json()
        return self.post_json("otp_webauthn:credential-registration-complete", authenticator.create(options))

    def passkey_sign_in(self, authenticator):
        options = self.post_json("otp_webauthn:credential-authentication-begin").json()
        return self.post_json("otp_webauthn:credential-authentication-complete", authenticator.get(options))

    def test_settings_are_valid(self):
        self.assertTrue(settings.OTP_WEBAUTHN_RP_ID)
        self.assertTrue(settings.OTP_WEBAUTHN_ALLOWED_ORIGINS)

    def test_login_page_offers_passkey_sign_in(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, 'id="passkey-verification-placeholder"')
        self.assertContains(response, 'id="otp_webauthn_config"')
        self.assertContains(response, "otp_webauthn_auth.js")

    def test_anyone_can_start_passkey_sign_in(self):
        response = self.post_json("otp_webauthn:credential-authentication-begin")
        self.assertEqual(response.status_code, 200)
        options = response.json()
        self.assertTrue(options["challenge"])
        self.assertEqual(options["userVerification"], "required")  # a passkey alone must check fingerprint/PIN

    def test_adding_a_passkey_needs_a_fully_verified_session(self):
        name = "otp_webauthn:credential-registration-begin"
        self.assertEqual(self.post_json(name).status_code, 302)  # signed out
        self.sign_in_with_password()
        self.assertEqual(self.post_json(name).status_code, 302)  # password only, code still pending
        self.verify()
        options = self.post_json(name).json()
        self.assertEqual(options["rp"]["id"], settings.OTP_WEBAUTHN_RP_ID)
        self.assertEqual(options["authenticatorSelection"]["residentKey"], "required")

    def test_register_then_sign_in_with_passkey_alone(self):
        self.verify()
        authenticator = SoftwareAuthenticator(settings.OTP_WEBAUTHN_RP_ID)
        response = self.register(authenticator)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(WebAuthnCredential.objects.filter(user=self.user).count(), 1)

        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 302)
        response = self.passkey_sign_in(authenticator)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["redirect_url"], reverse("ledger:dashboard"))
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 200)  # signed in and verified

    def test_passkey_replaces_the_code_after_a_password(self):
        self.verify()
        authenticator = SoftwareAuthenticator(settings.OTP_WEBAUTHN_RP_ID)
        self.register(authenticator)
        self.client.post(reverse("accounts:logout"))

        self.sign_in_with_password()
        self.assertContains(self.client.get(reverse("accounts:verify")), "passkey-verification-button")
        response = self.passkey_sign_in(authenticator)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 200)

    def test_passkey_without_user_verification_cannot_sign_in_alone(self):
        self.verify()
        authenticator = SoftwareAuthenticator(settings.OTP_WEBAUTHN_RP_ID)
        self.register(authenticator)
        self.client.post(reverse("accounts:logout"))

        authenticator.user_verified = False  # e.g. a security key touched without its PIN
        response = self.passkey_sign_in(authenticator)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 302)

    def test_forged_sign_in_is_rejected(self):
        self.verify()
        self.register(SoftwareAuthenticator(settings.OTP_WEBAUTHN_RP_ID))
        self.client.post(reverse("accounts:logout"))

        impostor = SoftwareAuthenticator(settings.OTP_WEBAUTHN_RP_ID)
        response = self.passkey_sign_in(impostor)  # unknown credential
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 302)

    def test_verify_page_offers_passkey_only_when_one_exists(self):
        self.sign_in_with_password()
        self.assertNotContains(self.client.get(reverse("accounts:verify")), "passkey-verification-button")
        make_passkey(self.user)
        self.assertContains(self.client.get(reverse("accounts:verify")), "passkey-verification-button")

    def test_security_page_lists_and_removes_own_passkeys_only(self):
        self.verify()
        mine = make_passkey(self.user, name="Laptop")
        theirs = make_passkey(get_user_model().objects.create_user("other", password=PASSWORD))
        page = self.client.get(reverse("accounts:security"))
        self.assertContains(page, "Laptop")
        self.assertContains(page, 'id="passkey-registration-placeholder"')

        self.assertEqual(self.client.post(reverse("accounts:passkey_delete", args=[theirs.pk])).status_code, 404)
        response = self.client.post(reverse("accounts:passkey_delete", args=[mine.pk]))
        self.assertRedirects(response, reverse("accounts:security") + "#passkeys", fetch_redirect_response=False)
        self.assertFalse(WebAuthnCredential.objects.filter(pk=mine.pk).exists())
        self.assertTrue(WebAuthnCredential.objects.filter(pk=theirs.pk).exists())


class PasswordManagerTests(TestCase):
    def test_code_page_avoids_words_that_stop_bitwarden_filling_it(self):
        user = get_user_model().objects.create_user("owner", password=PASSWORD)
        TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        page = self.client.get(reverse("accounts:verify")).content.decode().lower()
        self.assertIn('id="totp"', page)
        self.assertIn('autocomplete="one-time-code"', page)
        self.assertNotIn("backup", page)
        self.assertNotIn("recovery", page)

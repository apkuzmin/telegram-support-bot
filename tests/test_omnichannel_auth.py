import base64
import json
from unittest import TestCase

from support_bot.omnichannel.auth import AuthError, TokenSigner


class TokenSignerTests(TestCase):
    def setUp(self) -> None:
        self.signer = TokenSigner(
            "0123456789abcdef0123456789abcdef"
        )

    def test_round_trip_and_role_check(self) -> None:
        token = self.signer.issue(
            subject="operator-1",
            role="operator",
            conversation_id=None,
            ttl_seconds=60,
            now=100,
        )
        claims = self.signer.verify(
            token,
            allowed_roles={"operator"},
            now=101,
        )
        self.assertEqual(claims.subject, "operator-1")
        self.assertEqual(claims.role, "operator")
        encoded_header = token.split(".", 1)[0]
        header = json.loads(
            base64.urlsafe_b64decode(
                encoded_header + "=" * (-len(encoded_header) % 4)
            )
        )
        self.assertEqual(header, {"alg": "HS256", "typ": "JWT"})

        with self.assertRaisesRegex(AuthError, "role"):
            self.signer.verify(
                token,
                allowed_roles={"customer"},
                now=101,
            )

    def test_tampering_and_expiry_are_rejected(self) -> None:
        token = self.signer.issue(
            subject="customer-1",
            role="customer",
            ttl_seconds=10,
            now=100,
        )
        with self.assertRaisesRegex(AuthError, "signature"):
            self.signer.verify(token + "x", now=101)
        with self.assertRaisesRegex(AuthError, "expired"):
            self.signer.verify(token, now=110)

    def test_short_secret_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 32"):
            TokenSigner("short")

    def test_invalid_lifetime_and_empty_claims_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "TTL"):
            self.signer.issue(
                subject="customer-1",
                role="customer",
                ttl_seconds=0,
            )
        with self.assertRaisesRegex(ValueError, "subject"):
            self.signer.issue(
                subject="",
                role="customer",
                ttl_seconds=60,
            )
        with self.assertRaisesRegex(ValueError, "200"):
            self.signer.issue(
                subject="x" * 201,
                role="customer",
                ttl_seconds=60,
            )

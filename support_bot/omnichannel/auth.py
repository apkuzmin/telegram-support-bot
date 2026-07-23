from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable


class AuthError(ValueError):
    pass


MAX_SUBJECT_LENGTH = 200


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise AuthError("Malformed token") from exc


@dataclass(frozen=True)
class AuthClaims:
    subject: str
    role: str
    expires_at: int
    conversation_id: str | None = None


class TokenSigner:
    """Dependency-free JWT HS256 contract for host-site integration."""

    def __init__(self, secret: str, *, audience: str = "support-module") -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("Token secret must be at least 32 bytes")
        self._key = secret.encode("utf-8")
        self._audience = audience

    def issue(
        self,
        *,
        subject: str,
        role: str,
        ttl_seconds: int,
        conversation_id: str | None = None,
        now: int | None = None,
    ) -> str:
        if not subject or not role:
            raise ValueError("Token subject and role are required")
        if len(subject) > MAX_SUBJECT_LENGTH:
            raise ValueError(
                f"Token subject exceeds {MAX_SUBJECT_LENGTH} characters"
            )
        if ttl_seconds <= 0:
            raise ValueError("Token TTL must be positive")
        issued_at = int(time.time()) if now is None else now
        header = _b64_encode(
            json.dumps(
                {"alg": "HS256", "typ": "JWT"},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        payload: dict[str, Any] = {
            "sub": subject,
            "role": role,
            "aud": self._audience,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        encoded = _b64_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signing_input = f"{header}.{encoded}"
        signature = _b64_encode(
            hmac.new(
                self._key,
                signing_input.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"{signing_input}.{signature}"

    def verify(
        self,
        token: str,
        *,
        allowed_roles: Iterable[str] | None = None,
        now: int | None = None,
    ) -> AuthClaims:
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthError("Malformed token")
        encoded_header, encoded, signature = parts
        try:
            header = json.loads(_b64_decode(encoded_header))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("Malformed token header") from exc
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise AuthError("Invalid token algorithm")
        signing_input = f"{encoded_header}.{encoded}"
        expected = _b64_encode(
            hmac.new(
                self._key,
                signing_input.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise AuthError("Invalid token signature")
        try:
            payload = json.loads(_b64_decode(encoded))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthError("Malformed token payload") from exc
        if not isinstance(payload, dict):
            raise AuthError("Malformed token payload")
        if payload.get("aud") != self._audience:
            raise AuthError("Invalid token audience")
        current = int(time.time()) if now is None else now
        try:
            expires_at = int(payload["exp"])
            issued_at = int(payload["iat"])
            subject_value = payload["sub"]
            role_value = payload["role"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("Missing token claims") from exc
        if (
            not isinstance(subject_value, str)
            or not subject_value
            or len(subject_value) > MAX_SUBJECT_LENGTH
            or not isinstance(role_value, str)
            or not role_value
        ):
            raise AuthError("Invalid token claims")
        subject = subject_value
        role = role_value
        if issued_at > current + 60 or expires_at <= issued_at:
            raise AuthError("Invalid token lifetime")
        if expires_at <= current:
            raise AuthError("Token expired")
        roles = set(allowed_roles or ())
        if roles and role not in roles:
            raise AuthError("Token role is not allowed")
        conversation_id = payload.get("conversation_id")
        return AuthClaims(
            subject=subject,
            role=role,
            expires_at=expires_at,
            conversation_id=(
                str(conversation_id) if conversation_id is not None else None
            ),
        )

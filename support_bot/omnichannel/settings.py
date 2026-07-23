from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OmnichannelSettings:
    database_url: str
    auth_secret: str
    environment: str = "development"
    allowed_origins: tuple[str, ...] = ()
    operator_group_id: int | None = None
    upload_dir: str = "./support_uploads"
    max_upload_bytes: int = 10 * 1024 * 1024
    token_ttl_seconds: int = 24 * 60 * 60
    trusted_hosts: tuple[str, ...] = ()
    expose_docs: bool = True
    websocket_idle_seconds: int = 300
    realtime_retention_seconds: int = 7 * 24 * 60 * 60
    outbox_retention_seconds: int = 30 * 24 * 60 * 60
    unused_file_retention_seconds: int = 24 * 60 * 60
    maintenance_interval_seconds: int = 60 * 60

    @classmethod
    def from_env(cls) -> "OmnichannelSettings":
        environment = os.getenv("SUPPORT_ENV", "development").strip().lower()
        auth_secret = os.getenv("SUPPORT_AUTH_SECRET", "")
        if not auth_secret:
            if environment != "development":
                raise RuntimeError(
                    "SUPPORT_AUTH_SECRET is required outside development"
                )
            auth_secret = "development-only-change-me-32bytes"

        group_raw = os.getenv("OPERATOR_GROUP_ID")
        operator_group_id = int(group_raw) if group_raw else None
        origins = tuple(
            origin.strip()
            for origin in os.getenv("SUPPORT_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        trusted_hosts = tuple(
            host.strip()
            for host in os.getenv("SUPPORT_TRUSTED_HOSTS", "").split(",")
            if host.strip()
        )
        if environment != "development" and not trusted_hosts:
            raise RuntimeError(
                "SUPPORT_TRUSTED_HOSTS is required outside development"
            )
        expose_docs_raw = os.getenv("SUPPORT_EXPOSE_DOCS")
        expose_docs = (
            environment == "development"
            if expose_docs_raw is None
            else expose_docs_raw == "1"
        )
        return cls(
            database_url=os.getenv(
                "SUPPORT_DATABASE_URL",
                "sqlite+aiosqlite:///./support_omnichannel.sqlite3",
            ),
            auth_secret=auth_secret,
            environment=environment,
            allowed_origins=origins,
            operator_group_id=operator_group_id,
            upload_dir=os.getenv("SUPPORT_UPLOAD_DIR", "./support_uploads"),
            max_upload_bytes=int(
                os.getenv("SUPPORT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
            ),
            token_ttl_seconds=int(
                os.getenv("SUPPORT_TOKEN_TTL_SECONDS", str(24 * 60 * 60))
            ),
            trusted_hosts=trusted_hosts,
            expose_docs=expose_docs,
            websocket_idle_seconds=int(
                os.getenv("SUPPORT_WEBSOCKET_IDLE_SECONDS", "300")
            ),
            realtime_retention_seconds=int(
                os.getenv(
                    "SUPPORT_REALTIME_RETENTION_SECONDS",
                    str(7 * 24 * 60 * 60),
                )
            ),
            outbox_retention_seconds=int(
                os.getenv(
                    "SUPPORT_OUTBOX_RETENTION_SECONDS",
                    str(30 * 24 * 60 * 60),
                )
            ),
            unused_file_retention_seconds=int(
                os.getenv(
                    "SUPPORT_UNUSED_FILE_RETENTION_SECONDS",
                    str(24 * 60 * 60),
                )
            ),
            maintenance_interval_seconds=int(
                os.getenv("SUPPORT_MAINTENANCE_INTERVAL_SECONDS", "3600")
            ),
        )

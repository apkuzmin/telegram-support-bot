from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator


class FileTooLargeError(ValueError):
    pass


class UnsafeFileTypeError(ValueError):
    pass


@dataclass(frozen=True)
class SavedFile:
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str


def _safe_name(filename: str | None) -> str:
    base = os.path.basename(filename or "file")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (cleaned or "file")[:180]


class LocalFileStore:
    """Replaceable local adapter; production can provide an S3 implementation."""

    def __init__(self, root: str, *, max_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    async def save(
        self,
        *,
        filename: str | None,
        content_type: str | None,
        chunks: AsyncIterator[bytes],
    ) -> SavedFile:
        resolved_content_type = content_type or "application/octet-stream"
        if resolved_content_type.lower().split(";", 1)[0] in {
            "application/javascript",
            "application/xhtml+xml",
            "image/svg+xml",
            "text/html",
            "text/javascript",
        }:
            raise UnsafeFileTypeError("Active web content is not accepted")
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_name(filename)
        storage_key = f"{uuid.uuid4()}-{safe_name}"
        target = self.root / storage_key
        digest = hashlib.sha256()
        size = 0
        first_bytes = bytearray()
        try:
            with target.open("xb") as output:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FileTooLargeError(
                            f"File exceeds {self.max_bytes} bytes"
                        )
                    if len(first_bytes) < 1024:
                        first_bytes.extend(chunk[: 1024 - len(first_bytes)])
                        probe = bytes(first_bytes).lstrip().lower()
                        if probe.startswith(
                            (
                                b"<!doctype html",
                                b"<html",
                                b"<script",
                                b"<?xml",
                                b"<svg",
                            )
                        ):
                            raise UnsafeFileTypeError(
                                "Active web content is not accepted"
                            )
                    digest.update(chunk)
                    output.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return SavedFile(
            original_name=safe_name,
            content_type=resolved_content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
            storage_key=storage_key,
        )

    def path_for(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if path.parent != self.root:
            raise ValueError("Invalid storage key")
        return path

    def delete(self, storage_key: str) -> None:
        self.path_for(storage_key).unlink(missing_ok=True)

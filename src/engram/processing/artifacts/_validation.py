"""Small validation helpers shared by immutable processing artifacts."""

from __future__ import annotations

import re

from engram.processing.exceptions import InvalidArtifactError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_bytes(name: str, value: object) -> None:
    if not isinstance(value, bytes):
        raise InvalidArtifactError(f"{name} must be bytes")


def require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidArtifactError(f"{name} must be a non-empty string")


def require_non_negative_int(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidArtifactError(f"{name} must be a non-negative integer")


def require_positive_int(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidArtifactError(f"{name} must be a positive integer")


def require_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise InvalidArtifactError(f"{name} must be a lowercase SHA-256 hex digest")

"""Redacted-by-design identity diagnostics."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import time

from app.config import LOGS_DIR
from app.identity.models import AccountState


_SENSITIVE_PATTERN = re.compile(
    r"("
    r"\beyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){1,2}\b|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:access|refresh|id)[_-]?token\b|"
    r"\btoken\b|"
    r"secret|password|authorization[_-]?code|\bcode=|state=|nonce|verifier|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"(?<![A-Za-z0-9])(?:\+?\d[\s().-]?){8,}(?![A-Za-z0-9])|"
    r"subject|student|invoice|"
    r"[\w\u0600-\u06FF -]+\.(?:db|sqlite|pdf|xlsx|xls|zip|log|json)\b|"
    r"https?://\S*\?\S*|"
    r"\\|/"
    r")",
    re.IGNORECASE,
)
_SAFE_APP_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_SAFE_CORRELATION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MAX_ELAPSED_MS = 600_000


class DiagnosticEventCategory(StrEnum):
    STATE_TRANSITION = "state_transition"


class DiagnosticErrorCategory(StrEnum):
    CREDENTIAL_STORE_UNAVAILABLE = "credential_store_unavailable"
    CREDENTIAL_NOT_FOUND = "credential_not_found"
    CREDENTIAL_WRITE_FAILED = "credential_write_failed"
    CREDENTIAL_READ_FAILED = "credential_read_failed"
    CREDENTIAL_DELETE_FAILED = "credential_delete_failed"
    MALFORMED_CREDENTIAL = "malformed_credential"
    METADATA_UNAVAILABLE = "metadata_unavailable"
    STATE_TRANSITION_REJECTED = "state_transition_rejected"
    REDACTED_SENSITIVE_INPUT = "redacted_sensitive_input"


@dataclass(frozen=True, slots=True)
class IdentityDiagnosticEvent:
    event_category: DiagnosticEventCategory | str
    transition_from: AccountState | str | None = None
    transition_to: AccountState | str | None = None
    app_version: str | None = None
    elapsed_ms: int | None = None
    error_category: DiagnosticErrorCategory | str | None = None


class IdentityDiagnostics:
    def __init__(self, log_dir: Path = LOGS_DIR):
        self.log_path = Path(log_dir) / "identity.log"

    def record(self, event: IdentityDiagnosticEvent) -> bool:
        try:
            payload = _safe_payload(event)
            if payload is None:
                return False
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            return True
        except Exception:
            return False


def build_transition_event(
    *,
    transition_from: str | None,
    transition_to: str | None,
    app_version: str | None = None,
    elapsed_start: float | None = None,
    error_category: DiagnosticErrorCategory | str | None = None,
) -> IdentityDiagnosticEvent:
    elapsed_ms = None
    if elapsed_start is not None:
        elapsed_ms = min(MAX_ELAPSED_MS, max(0, int((time() - elapsed_start) * 1000)))
    return IdentityDiagnosticEvent(
        event_category=DiagnosticEventCategory.STATE_TRANSITION,
        transition_from=transition_from,
        transition_to=transition_to,
        app_version=app_version,
        elapsed_ms=elapsed_ms,
        error_category=error_category,
    )


def _safe_payload(event: IdentityDiagnosticEvent) -> dict | None:
    try:
        event_category = DiagnosticEventCategory(event.event_category)
    except ValueError:
        return None
    transition_from = _safe_state(event.transition_from)
    transition_to = _safe_state(event.transition_to)
    error_category = _safe_error_category(event.error_category)
    if (
        (event.transition_from is not None and transition_from is None)
        or (event.transition_to is not None and transition_to is None)
        or (event.error_category is not None and error_category is None)
    ):
        return None

    payload: dict[str, object] = {"event_category": event_category.value}
    if transition_from is not None:
        payload["transition_from"] = transition_from.value
    if transition_to is not None:
        payload["transition_to"] = transition_to.value
    if event.app_version is not None:
        if (
            not _SAFE_APP_VERSION_PATTERN.fullmatch(event.app_version)
            or _contains_sensitive_content(event.app_version)
        ):
            return None
        payload["app_version"] = event.app_version
    if event.elapsed_ms is not None:
        if not isinstance(event.elapsed_ms, int):
            return None
        payload["elapsed_ms"] = min(MAX_ELAPSED_MS, max(0, event.elapsed_ms))
    if error_category is not None:
        payload["error_category"] = error_category.value
    correlation_id = secrets.token_hex(16)
    if not _SAFE_CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        return None
    payload["correlation_id"] = correlation_id
    if any(
        isinstance(value, str) and _contains_sensitive_content(value)
        for value in payload.values()
    ):
        return None
    return payload


def _safe_state(value: AccountState | str | None) -> AccountState | None:
    if value is None:
        return None
    try:
        return AccountState(value)
    except ValueError:
        return None


def _safe_error_category(
    value: DiagnosticErrorCategory | str | None,
) -> DiagnosticErrorCategory | None:
    if value is None:
        return None
    try:
        return DiagnosticErrorCategory(value)
    except ValueError:
        return None


def _contains_sensitive_content(value: str) -> bool:
    return bool(_SENSITIVE_PATTERN.search(value))

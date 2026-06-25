"""Non-secret identity metadata stored outside the business database."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from app.config import IDENTITY_METADATA_PATH
from app.identity.errors import MetadataStoreError


METADATA_VERSION = 1
ALLOWED_METADATA_KEYS = frozenset(
    {
        "metadata_version",
        "installation_uuid",
        "created_at",
        "last_local_identity_state_at",
        "diagnostic_correlation_id",
    }
)


@dataclass(frozen=True, slots=True)
class IdentityMetadata:
    metadata_version: int
    installation_uuid: str
    created_at: str
    last_local_identity_state_at: str | None = None
    diagnostic_correlation_id: str | None = None


class IdentityMetadataStore:
    def __init__(self, path: Path = IDENTITY_METADATA_PATH):
        self.path = Path(path)

    def load_or_create(self) -> IdentityMetadata:
        try:
            return self.load()
        except MetadataStoreError:
            metadata = fresh_metadata()
            self.save(metadata)
            return metadata

    def load(self) -> IdentityMetadata:
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except FileNotFoundError as error:
            raise MetadataStoreError("Identity metadata is missing.") from error
        except (OSError, json.JSONDecodeError) as error:
            raise MetadataStoreError("Identity metadata is unreadable.") from error
        return parse_metadata(payload)

    def save(self, metadata: IdentityMetadata) -> None:
        payload = asdict(metadata)
        validate_metadata_payload(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise MetadataStoreError("Identity metadata could not be written atomically.") from error

    def touch_state_timestamp(self, when: datetime | None = None) -> IdentityMetadata:
        metadata = self.load_or_create()
        updated = replace(
            metadata,
            last_local_identity_state_at=(when or _now()).isoformat(),
        )
        self.save(updated)
        return updated


def fresh_metadata() -> IdentityMetadata:
    now = _now().isoformat()
    return IdentityMetadata(
        metadata_version=METADATA_VERSION,
        installation_uuid=str(uuid.uuid4()),
        created_at=now,
        last_local_identity_state_at=None,
        diagnostic_correlation_id=str(uuid.uuid4()),
    )


def parse_metadata(payload: object) -> IdentityMetadata:
    if not isinstance(payload, dict):
        raise MetadataStoreError("Identity metadata has an invalid shape.")
    validate_metadata_payload(payload)
    return IdentityMetadata(
        metadata_version=payload["metadata_version"],
        installation_uuid=payload["installation_uuid"],
        created_at=payload["created_at"],
        last_local_identity_state_at=payload.get("last_local_identity_state_at"),
        diagnostic_correlation_id=payload.get("diagnostic_correlation_id"),
    )


def validate_metadata_payload(payload: dict) -> None:
    extra = set(payload) - ALLOWED_METADATA_KEYS
    if extra:
        raise MetadataStoreError("Identity metadata contains forbidden fields.")
    if payload.get("metadata_version") != METADATA_VERSION:
        raise MetadataStoreError("Identity metadata version is unsupported.")
    try:
        parsed = uuid.UUID(str(payload.get("installation_uuid")), version=4)
    except (TypeError, ValueError) as error:
        raise MetadataStoreError("Installation UUID is invalid.") from error
    if str(parsed) != payload.get("installation_uuid"):
        raise MetadataStoreError("Installation UUID must use canonical UUIDv4 format.")
    for key in (
        "created_at",
        "last_local_identity_state_at",
        "diagnostic_correlation_id",
    ):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise MetadataStoreError("Identity metadata contains invalid field types.")


def _now() -> datetime:
    return datetime.now(UTC)

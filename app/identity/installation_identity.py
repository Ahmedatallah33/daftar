"""Privacy-preserving per-installation identity."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.identity.metadata_store import IdentityMetadataStore


@dataclass(frozen=True, slots=True)
class InstallationIdentity:
    installation_uuid: UUID


def load_installation_identity(
    metadata_store: IdentityMetadataStore | None = None,
) -> InstallationIdentity:
    store = metadata_store or IdentityMetadataStore()
    metadata = store.load_or_create()
    return InstallationIdentity(installation_uuid=UUID(metadata.installation_uuid))

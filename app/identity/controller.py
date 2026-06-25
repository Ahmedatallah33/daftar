"""Pure-Python identity controller with no provider, network, DB, or Qt coupling."""

from __future__ import annotations

from time import time

from app.config import APP_RELEASE_REFERENCE
from app.identity.credential_store import CredentialStore, WindowsCredentialManagerStore
from app.identity.diagnostics import IdentityDiagnostics, build_transition_event
from app.identity.installation_identity import InstallationIdentity, load_installation_identity
from app.identity.metadata_store import IdentityMetadataStore
from app.identity.models import AccountSnapshot, AccountState, AccountStateMachine


class IdentityController:
    def __init__(
        self,
        *,
        credential_store: CredentialStore | None = None,
        metadata_store: IdentityMetadataStore | None = None,
        diagnostics: IdentityDiagnostics | None = None,
        state_machine: AccountStateMachine | None = None,
    ):
        self.credential_store = credential_store or WindowsCredentialManagerStore()
        self.metadata_store = metadata_store or IdentityMetadataStore()
        self.diagnostics = diagnostics or IdentityDiagnostics()
        self.state_machine = state_machine or AccountStateMachine()

    @property
    def snapshot(self) -> AccountSnapshot:
        return self.state_machine.snapshot

    def installation_identity(self) -> InstallationIdentity:
        return load_installation_identity(self.metadata_store)

    def transition(self, target: AccountState) -> AccountSnapshot:
        start = time()
        previous = self.snapshot.state
        snapshot = self.state_machine.transition(target)
        self.metadata_store.touch_state_timestamp(snapshot.entered_at)
        self.diagnostics.record(
            build_transition_event(
                transition_from=previous.value,
                transition_to=target.value,
                app_version=APP_RELEASE_REFERENCE,
                elapsed_start=start,
            )
        )
        return snapshot

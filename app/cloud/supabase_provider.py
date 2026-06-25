"""Minimal Supabase provider boundary for the Daftar identity foundation.

This module is the smallest provider-specific seam that links the
provider-neutral identity foundation (``app.identity``) to Supabase. It is
deliberately inert:

* It performs **no** network I/O and imports no HTTP/Supabase client.
* It touches **no** business data and imports no business ORM/services.
* The only place a future Supabase refresh secret may live is the existing
  secure Windows Credential Manager boundary (``CredentialStore``); this seam
  never writes secrets to SQLite, metadata JSON, settings, logs, or repository
  files.

Offline-first guarantee: importing or constructing anything here never
requires the network or an active cloud session, so existing local SQLite
business data stays fully usable offline. Live HTTP/auth wiring is intentionally
deferred to a later sprint.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.identity.credential_store import CredentialStore, WindowsCredentialManagerStore
from app.identity.models import AccountState
from app.cloud.auth_identity import canonical_user_uuid


# The project ref is non-secret — it appears in the public project URL. Pinning
# the development ref means this boundary cannot be retargeted at production by
# a stray environment value without an explicit (and rejected) override.
DEVELOPMENT_PROJECT_REF = "thzwrbicieyilufasfoo"

# Credential-store entry name for the FUTURE Supabase refresh token. No value is
# ever written here; this only fixes WHERE such a secret may live — inside the
# secure OS credential store, under the existing identity namespace
# (resolves to "Daftar/Identity/Provider/Supabase/refresh").
SUPABASE_REFRESH_CREDENTIAL_PREFIX = "Provider/Supabase"
SUPABASE_REFRESH_CREDENTIAL_NAME = f"{SUPABASE_REFRESH_CREDENTIAL_PREFIX}/refresh"


class ProviderConfigError(RuntimeError):
    """Raised when development provider configuration is incomplete or unsafe."""


class SupabaseAuthMethod(StrEnum):
    """Development sign-in methods prepared by this sprint.

    Only Email OTP is prepared. Google OAuth is intentionally not configured.
    """

    EMAIL_OTP = "email_otp"


@dataclass(frozen=True, slots=True)
class SupabaseProjectConfig:
    """Non-secret connection descriptor for the development project."""

    project_ref: str
    api_url: str
    # anon / publishable key: client-safe by design, but still sourced from the
    # environment so it is never committed to the repository.
    publishable_key: str
    auth_method: SupabaseAuthMethod = SupabaseAuthMethod.EMAIL_OTP

    @property
    def is_development(self) -> bool:
        return self.project_ref == DEVELOPMENT_PROJECT_REF


def load_development_config(env: Mapping[str, str] | None = None) -> SupabaseProjectConfig:
    """Build the development connection descriptor from the environment.

    Never reads or returns a service_role key, database password, or PAT. Raises
    if pointed anywhere other than the development project.
    """

    source = env if env is not None else os.environ
    project_ref = source.get("SUPABASE_PROJECT_REF", DEVELOPMENT_PROJECT_REF)
    if project_ref != DEVELOPMENT_PROJECT_REF:
        raise ProviderConfigError(
            "Refusing a non-development Supabase project ref."
        )
    api_url = source.get("SUPABASE_URL", f"https://{project_ref}.supabase.co")
    publishable_key = source.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not publishable_key:
        raise ProviderConfigError(
            "SUPABASE_PUBLISHABLE_KEY is not set; provide it via the local "
            "environment, never via a repository file."
        )
    return SupabaseProjectConfig(
        project_ref=project_ref,
        api_url=api_url,
        publishable_key=publishable_key,
    )


class SupabaseCredentialBridge:
    """Brokers the Supabase refresh secret through the secure OS store only."""

    def __init__(self, credential_store: CredentialStore | None = None):
        self._store = credential_store or WindowsCredentialManagerStore()

    def _refresh_name(self, user_id: str) -> str:
        return f"{SUPABASE_REFRESH_CREDENTIAL_PREFIX}/{canonical_user_uuid(user_id)}/refresh"

    def store_refresh_secret_for_user(self, user_id: str, secret: str) -> None:
        self._store.write_credential(self._refresh_name(user_id), secret)

    def load_refresh_secret_for_user(self, user_id: str) -> str:
        return self._store.read_credential(self._refresh_name(user_id))

    def clear_refresh_secret_for_user(self, user_id: str) -> None:
        self._store.delete_credential(self._refresh_name(user_id))


# Data-only mapping of Supabase auth outcomes to provider-neutral account
# states. Applying a transition remains the IdentityController's responsibility;
# this seam only translates provider vocabulary into the neutral model.
SUPABASE_EVENT_TO_STATE: Mapping[str, AccountState] = MappingProxyType(
    {
        "otp_requested": AccountState.SIGN_IN_PENDING,
        "session_established": AccountState.SIGNED_IN_ONLINE,
        "session_refreshed": AccountState.SIGNED_IN_ONLINE,
        "offline_session_valid": AccountState.SIGNED_IN_OFFLINE_VALID,
        "session_expired": AccountState.SESSION_EXPIRED,
        "provider_unavailable": AccountState.PROVIDER_UNAVAILABLE,
        "signed_out": AccountState.SIGNED_OUT,
    }
)


def account_state_for_event(event: str) -> AccountState:
    """Translate a Supabase auth event into a provider-neutral account state."""

    try:
        return SUPABASE_EVENT_TO_STATE[event]
    except KeyError as error:
        raise ProviderConfigError(f"Unknown Supabase auth event: {event!r}") from error

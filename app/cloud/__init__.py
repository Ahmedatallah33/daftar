"""Provider-specific cloud boundary (Supabase) for the Daftar identity layer.

This package is the smallest seam connecting the provider-neutral identity
foundation (``app.identity``) to Supabase. It performs no network I/O and
touches no business data.
"""

from app.cloud.supabase_provider import (
    DEVELOPMENT_PROJECT_REF,
    SUPABASE_REFRESH_CREDENTIAL_NAME,
    ProviderConfigError,
    SupabaseAuthMethod,
    SupabaseCredentialBridge,
    SupabaseProjectConfig,
    account_state_for_event,
    load_development_config,
)

__all__ = [
    "DEVELOPMENT_PROJECT_REF",
    "SUPABASE_REFRESH_CREDENTIAL_NAME",
    "ProviderConfigError",
    "SupabaseAuthMethod",
    "SupabaseCredentialBridge",
    "SupabaseProjectConfig",
    "account_state_for_event",
    "load_development_config",
]

"""Provider-specific cloud boundary (Supabase) for the Daftar identity layer.

This package is the smallest seam connecting the provider-neutral identity
foundation (``app.identity``) to Supabase. It performs no network I/O and
touches no business data.
"""

from app.cloud.supabase_provider import (
    DEVELOPMENT_PROJECT_REF,
    SUPABASE_REFRESH_CREDENTIAL_NAME,
    SUPABASE_REFRESH_CREDENTIAL_PREFIX,
    ProviderConfigError,
    SupabaseAuthMethod,
    SupabaseCredentialBridge,
    SupabaseProjectConfig,
    account_state_for_event,
    load_development_config,
)
from app.cloud.auth_identity import AuthenticatedIdentity, AuthenticatedIdentityError
from app.cloud.supabase_auth import (
    OtpRequestResult,
    OtpVerifyResult,
    SupabaseAuthFlowError,
    SupabaseAuthInputError,
    SupabaseAuthInvalidOtpError,
    SupabaseAuthMissingConfigError,
    SupabaseAuthProviderUnavailableError,
    SupabaseEmailOtpAuth,
    normalize_email,
    normalize_otp,
)
from app.cloud.supabase_workspace_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceLookupError,
    WorkspaceMembership,
    WorkspaceSelectionError,
    WorkspaceUnavailableError,
    select_single_workspace,
)

__all__ = [
    "DEVELOPMENT_PROJECT_REF",
    "AuthenticatedIdentity",
    "AuthenticatedIdentityError",
    "OtpRequestResult",
    "OtpVerifyResult",
    "SUPABASE_REFRESH_CREDENTIAL_NAME",
    "SUPABASE_REFRESH_CREDENTIAL_PREFIX",
    "ProviderConfigError",
    "SupabaseAuthMethod",
    "SupabaseAuthFlowError",
    "SupabaseAuthInputError",
    "SupabaseAuthInvalidOtpError",
    "SupabaseAuthMissingConfigError",
    "SupabaseAuthProviderUnavailableError",
    "SupabaseCredentialBridge",
    "SupabaseEmailOtpAuth",
    "SupabaseProjectConfig",
    "SupabaseWorkspaceRepository",
    "WorkspaceLookupError",
    "WorkspaceMembership",
    "WorkspaceSelectionError",
    "WorkspaceUnavailableError",
    "account_state_for_event",
    "load_development_config",
    "normalize_email",
    "normalize_otp",
    "select_single_workspace",
]

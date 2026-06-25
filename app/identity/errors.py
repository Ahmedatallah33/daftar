"""Typed identity-layer errors.

These exceptions intentionally carry categories and operational context, not
credentials, account identifiers, URLs, or business data.
"""


class IdentityError(RuntimeError):
    """Base class for provider-neutral identity errors."""


class IdentityStateTransitionError(IdentityError):
    """Raised when an account-state transition is not permitted."""


class CredentialStoreError(IdentityError):
    """Base class for secure credential-store failures."""


class CredentialStoreUnavailableError(CredentialStoreError):
    """Raised when the configured secure credential store is unavailable."""


class CredentialNotFoundError(CredentialStoreError):
    """Raised when a requested credential entry is absent."""


class CredentialWriteError(CredentialStoreError):
    """Raised when a credential cannot be written safely."""


class CredentialReadError(CredentialStoreError):
    """Raised when a credential cannot be read safely."""


class CredentialDeleteError(CredentialStoreError):
    """Raised when a credential cannot be deleted safely."""


class MalformedCredentialError(CredentialStoreError):
    """Raised when a credential-store entry does not match the expected format."""


class MetadataStoreError(IdentityError):
    """Raised when non-secret identity metadata cannot be handled safely."""

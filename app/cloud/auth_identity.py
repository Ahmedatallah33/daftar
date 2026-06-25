from __future__ import annotations

import uuid


class AuthenticatedIdentityError(RuntimeError):
    pass


class AuthenticatedIdentity:
    """Secret-free authenticated user identity."""

    __slots__ = ("_user_id",)

    def __init__(self, user_id: str):
        self._user_id = canonical_user_uuid(user_id)

    @property
    def user_id(self) -> str:
        return self._user_id

    def __repr__(self) -> str:
        return "AuthenticatedIdentity(user_id=<redacted>)"


def canonical_user_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise AuthenticatedIdentityError("Authenticated user identity is invalid.")
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise AuthenticatedIdentityError("Authenticated user identity is invalid.") from error
    canonical = str(parsed)
    if value != canonical:
        raise AuthenticatedIdentityError("Authenticated user identity is invalid.")
    return canonical

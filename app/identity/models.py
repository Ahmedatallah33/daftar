"""Strict account-state model with immutable snapshots and no Qt dependency."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from app.identity.errors import IdentityStateTransitionError


class AccountState(StrEnum):
    SIGNED_OUT = "SIGNED_OUT"
    SIGN_IN_PENDING = "SIGN_IN_PENDING"
    SIGNED_IN_ONLINE = "SIGNED_IN_ONLINE"
    SIGNED_IN_OFFLINE_VALID = "SIGNED_IN_OFFLINE_VALID"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    DEVICE_DISABLED = "DEVICE_DISABLED"
    ONBOARDING_GRACE = "ONBOARDING_GRACE"


ALLOWED_TRANSITIONS = MappingProxyType(
    {
        AccountState.SIGNED_OUT: frozenset(
            {AccountState.SIGN_IN_PENDING, AccountState.ONBOARDING_GRACE}
        ),
        AccountState.ONBOARDING_GRACE: frozenset(
            {
                AccountState.SIGN_IN_PENDING,
                AccountState.SIGNED_OUT,
                AccountState.PROVIDER_UNAVAILABLE,
            }
        ),
        AccountState.SIGN_IN_PENDING: frozenset(
            {
                AccountState.SIGNED_IN_ONLINE,
                AccountState.PROVIDER_UNAVAILABLE,
                AccountState.SESSION_EXPIRED,
                AccountState.SIGNED_OUT,
                AccountState.DEVICE_DISABLED,
            }
        ),
        AccountState.SIGNED_IN_ONLINE: frozenset(
            {
                AccountState.SIGNED_IN_OFFLINE_VALID,
                AccountState.SESSION_EXPIRED,
                AccountState.SIGNED_OUT,
                AccountState.DEVICE_DISABLED,
            }
        ),
        AccountState.SIGNED_IN_OFFLINE_VALID: frozenset(
            {
                AccountState.SIGNED_IN_ONLINE,
                AccountState.SESSION_EXPIRED,
                AccountState.SIGNED_OUT,
                AccountState.DEVICE_DISABLED,
                AccountState.PROVIDER_UNAVAILABLE,
            }
        ),
        AccountState.PROVIDER_UNAVAILABLE: frozenset(
            {
                AccountState.SIGN_IN_PENDING,
                AccountState.SIGNED_IN_OFFLINE_VALID,
                AccountState.ONBOARDING_GRACE,
                AccountState.SIGNED_OUT,
            }
        ),
        AccountState.SESSION_EXPIRED: frozenset(
            {
                AccountState.SIGN_IN_PENDING,
                AccountState.SIGNED_OUT,
                AccountState.PROVIDER_UNAVAILABLE,
            }
        ),
        AccountState.DEVICE_DISABLED: frozenset({AccountState.SIGNED_OUT}),
    }
)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """A secret-free point-in-time identity state."""

    state: AccountState
    entered_at: datetime
    previous_state: AccountState | None = None
    correlation_id: str | None = None


class AccountStateMachine:
    """Provider-neutral account-state coordinator."""

    def __init__(self, initial_state: AccountState = AccountState.SIGNED_OUT):
        self._snapshot = AccountSnapshot(state=initial_state, entered_at=_now())

    @property
    def snapshot(self) -> AccountSnapshot:
        return self._snapshot

    def can_transition(self, target: AccountState) -> bool:
        return target in ALLOWED_TRANSITIONS[self._snapshot.state]

    def transition(
        self,
        target: AccountState,
        *,
        entered_at: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AccountSnapshot:
        if not self.can_transition(target):
            raise IdentityStateTransitionError(
                f"Illegal account-state transition: {self._snapshot.state} -> {target}"
            )
        self._snapshot = AccountSnapshot(
            state=target,
            previous_state=self._snapshot.state,
            entered_at=entered_at or _now(),
            correlation_id=correlation_id,
        )
        return self._snapshot

    def replace_snapshot_for_recovery(self, snapshot: AccountSnapshot) -> None:
        """Restore a previously persisted local state without treating it as login."""

        self._snapshot = replace(snapshot)


def _now() -> datetime:
    return datetime.now(UTC)

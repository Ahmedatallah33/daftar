"""Real Email OTP auth adapter for the Daftar desktop boundary.

The module stays offline-safe by design: importing it does not import the
Supabase client and constructing ``SupabaseEmailOtpAuth`` performs no network
I/O. The client is created lazily only when the user explicitly requests or
verifies an OTP.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.cloud.auth_identity import AuthenticatedIdentity, AuthenticatedIdentityError
from app.cloud.supabase_provider import (
    ProviderConfigError,
    SupabaseCredentialBridge,
    SupabaseProjectConfig,
    account_state_for_event,
    load_development_config,
)
from app.identity.controller import IdentityController
from app.identity.models import AccountState


_EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+$",
    re.IGNORECASE,
)
_OTP_PATTERN = re.compile(r"^\d{6}$")


class SupabaseAuthFlowError(RuntimeError):
    """Base class for recoverable desktop-auth flow failures."""


class SupabaseAuthMissingConfigError(SupabaseAuthFlowError):
    """Raised when the local publishable key is not available."""


class SupabaseAuthInputError(SupabaseAuthFlowError):
    """Raised when email or OTP input is locally invalid."""


class SupabaseAuthProviderUnavailableError(SupabaseAuthFlowError):
    """Raised when Supabase/network/provider operations fail."""


class SupabaseAuthInvalidOtpError(SupabaseAuthFlowError):
    """Raised when OTP verification does not produce a usable session."""


@dataclass(frozen=True, slots=True)
class OtpRequestResult:
    email: str


@dataclass(frozen=True, slots=True)
class OtpVerifyResult:
    account_state: AccountState
    identity: AuthenticatedIdentity


ClientFactory = Callable[[SupabaseProjectConfig], Any]
ConfigLoader = Callable[[Mapping[str, str] | None], SupabaseProjectConfig]


class SupabaseEmailOtpAuth:
    """Minimal real Supabase Email OTP flow with memory-only access token."""

    def __init__(
        self,
        *,
        identity_controller: IdentityController | None = None,
        credential_bridge: SupabaseCredentialBridge | None = None,
        config_loader: ConfigLoader = load_development_config,
        client_factory: ClientFactory | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.identity_controller = identity_controller or IdentityController()
        self.credential_bridge = credential_bridge or SupabaseCredentialBridge(
            self.identity_controller.credential_store
        )
        self._config_loader = config_loader
        self._client_factory = client_factory or _create_supabase_client
        self._env = env
        self._client: Any | None = None
        self._access_token: str | None = None
        self._identity: AuthenticatedIdentity | None = None

    @property
    def access_token_in_memory(self) -> str | None:
        return self._access_token

    @property
    def authenticated_identity(self) -> AuthenticatedIdentity | None:
        return self._identity

    @property
    def authenticated_client(self) -> Any:
        if self._client is None or self._identity is None or self._access_token is None:
            raise SupabaseAuthInvalidOtpError(
                "لم تكتمل جلسة الدخول الآمنة. أعد تسجيل الدخول وحاول مرة أخرى."
            )
        return self._client

    @property
    def current_state(self) -> AccountState:
        return self.identity_controller.snapshot.state

    def request_code(self, email: str) -> OtpRequestResult:
        normalized = normalize_email(email)
        client = self._client_for_login()
        try:
            client.auth.sign_in_with_otp(
                {
                    "email": normalized,
                    "options": {"should_create_user": True},
                }
            )
        except Exception as error:
            self._transition_if_allowed(account_state_for_event("provider_unavailable"))
            raise SupabaseAuthProviderUnavailableError(
                "تعذر إرسال رمز الدخول الآن. تأكد من اتصال الإنترنت وحاول مرة أخرى."
            ) from error

        self._transition_if_allowed(account_state_for_event("otp_requested"))
        return OtpRequestResult(email=normalized)

    def verify_code(self, email: str, otp: str) -> OtpVerifyResult:
        normalized = normalize_email(email)
        token = normalize_otp(otp)
        client = self._client_for_login()
        try:
            response = client.auth.verify_otp(
                {
                    "email": normalized,
                    "token": token,
                    "type": "email",
                }
            )
        except Exception as error:
            if _is_provider_failure(error):
                self._transition_if_allowed(account_state_for_event("provider_unavailable"))
                raise SupabaseAuthProviderUnavailableError(
                    "تعذر الاتصال بخدمة تسجيل الدخول الآن. لم تُفتح أي بيانات تشغيلية."
                ) from error
            raise SupabaseAuthInvalidOtpError(
                "رمز الدخول غير صحيح أو انتهت صلاحيته. يمكنك المحاولة مرة أخرى أو طلب رمز جديد."
            ) from error

        session = getattr(response, "session", None)
        if session is None and isinstance(response, Mapping):
            session = response.get("session")
        access_token = _session_value(session, "access_token")
        refresh_token = _session_value(session, "refresh_token")
        if not access_token or not refresh_token:
            raise SupabaseAuthInvalidOtpError(
                "لم تكتمل جلسة الدخول. اطلب رمزاً جديداً وحاول مرة أخرى."
            )

        try:
            identity = _identity_from_response(response, session)
        except AuthenticatedIdentityError as error:
            raise SupabaseAuthInvalidOtpError(
                "لم تكتمل هوية الحساب الآمنة. أعد تسجيل الدخول وحاول مرة أخرى."
            ) from error

        self.credential_bridge.store_refresh_secret_for_user(identity.user_id, refresh_token)
        self._access_token = access_token
        self._identity = identity
        self._transition_to_session_established()
        return OtpVerifyResult(account_state=self.current_state, identity=identity)

    def sign_out(self) -> None:
        identity = self._identity
        self._access_token = None
        self._identity = None
        if identity is not None:
            try:
                self.credential_bridge.clear_refresh_secret_for_user(identity.user_id)
            except Exception:
                pass
        self._transition_if_allowed(account_state_for_event("signed_out"))

    def _client_for_login(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            config = self._config_loader(self._env)
        except ProviderConfigError as error:
            raise SupabaseAuthMissingConfigError(
                "مفتاح Supabase publishable غير مضبوط على هذا الجهاز. "
                "اضبط SUPABASE_PUBLISHABLE_KEY في جلسة التشغيل ثم حاول مرة أخرى."
            ) from error
        self._client = self._client_factory(config)
        return self._client

    def _transition_if_allowed(self, target: AccountState) -> None:
        if self.identity_controller.state_machine.can_transition(target):
            self.identity_controller.transition(target)

    def _transition_to_session_established(self) -> None:
        target = account_state_for_event("session_established")
        if self.identity_controller.state_machine.can_transition(target):
            self.identity_controller.transition(target)
            return
        if self.current_state == AccountState.PROVIDER_UNAVAILABLE:
            self._transition_if_allowed(account_state_for_event("otp_requested"))
            self._transition_if_allowed(target)


def normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise SupabaseAuthInputError("أدخل بريداً إلكترونياً صالحاً.")
    normalized = email.strip().casefold()
    if not normalized or len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise SupabaseAuthInputError("أدخل بريداً إلكترونياً صالحاً.")
    return normalized


def normalize_otp(otp: str) -> str:
    if not isinstance(otp, str):
        raise SupabaseAuthInputError("أدخل رمز الدخول المكوّن من 6 أرقام.")
    token = "".join(otp.strip().split())
    if not _OTP_PATTERN.fullmatch(token):
        raise SupabaseAuthInputError("أدخل رمز الدخول المكوّن من 6 أرقام.")
    return token


def _session_value(session: Any, name: str) -> str | None:
    if isinstance(session, Mapping):
        value = session.get(name)
    else:
        value = getattr(session, name, None)
    return value if isinstance(value, str) and value else None


def _identity_from_response(response: Any, session: Any) -> AuthenticatedIdentity:
    user = _provider_value(response, "user")
    if user is None:
        user = _provider_value(session, "user")
    user_id = _provider_value(user, "id")
    if user_id is None:
        user_id = _provider_value(user, "user_id")
    return AuthenticatedIdentity(user_id)


def _provider_value(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _is_provider_failure(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    module = type(error).__module__.lower()
    name = type(error).__name__.lower()
    return "httpx" in module or "network" in name or "timeout" in name


def _create_supabase_client(config: SupabaseProjectConfig) -> Any:
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    return create_client(
        config.api_url,
        config.publishable_key,
        options=SyncClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )

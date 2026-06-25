from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread

from app.cloud.supabase_auth import (
    SupabaseAuthInputError,
    SupabaseAuthInvalidOtpError,
    SupabaseAuthMissingConfigError,
    SupabaseAuthProviderUnavailableError,
    SupabaseEmailOtpAuth,
    normalize_email,
    normalize_otp,
)
from app.cloud.supabase_provider import DEVELOPMENT_PROJECT_REF, SupabaseProjectConfig
from app.identity.controller import IdentityController
from app.identity.credential_store import InMemoryCredentialStore
from app.identity.diagnostics import IdentityDiagnostics
from app.identity.metadata_store import IdentityMetadataStore
from app.identity.models import AccountState
from app.ui.account_shell import AccountShell
from app.ui.pages.account_dialog import AccountDialog


ACCESS_CREDENTIAL = "memory-access-credential"
REFRESH_CREDENTIAL = "secure-refresh-credential"


class FakeAuthApi:
    def __init__(self):
        self.otp_requests = []
        self.verify_requests = []
        self.fail_request: Exception | None = None
        self.fail_verify: Exception | None = None
        self.response = SimpleNamespace(
            session=SimpleNamespace(
                access_token=ACCESS_CREDENTIAL,
                refresh_token=REFRESH_CREDENTIAL,
            )
        )

    def sign_in_with_otp(self, payload):
        self.otp_requests.append(payload)
        if self.fail_request is not None:
            raise self.fail_request
        return SimpleNamespace()

    def verify_otp(self, payload):
        self.verify_requests.append(payload)
        if self.fail_verify is not None:
            raise self.fail_verify
        return self.response


class FakeClient:
    def __init__(self):
        self.auth = FakeAuthApi()


class RecordingBridge:
    def __init__(self):
        self.refresh_values = []

    def store_refresh_secret(self, value: str) -> None:
        self.refresh_values.append(value)

    def load_refresh_secret(self) -> str:
        return self.refresh_values[-1]

    def clear_refresh_secret(self) -> None:
        self.refresh_values.clear()


def _config_loader(env=None):
    return SupabaseProjectConfig(
        project_ref=DEVELOPMENT_PROJECT_REF,
        api_url=f"https://{DEVELOPMENT_PROJECT_REF}.supabase.co",
        publishable_key="publishable-test-key",
    )


def _auth(tmp_path, client=None, bridge=None, config_loader=_config_loader):
    identity = IdentityController(
        credential_store=InMemoryCredentialStore(),
        metadata_store=IdentityMetadataStore(tmp_path / "identity" / "metadata.json"),
        diagnostics=IdentityDiagnostics(tmp_path / "logs"),
    )
    fake_client = client or FakeClient()
    return SupabaseEmailOtpAuth(
        identity_controller=identity,
        credential_bridge=bridge or RecordingBridge(),
        config_loader=config_loader,
        client_factory=lambda _config: fake_client,
    ), fake_client


def test_supabase_client_is_lazy_and_missing_key_is_login_only(tmp_path):
    calls = []

    def missing_config(_env=None):
        from app.cloud.supabase_provider import ProviderConfigError

        raise ProviderConfigError("missing")

    auth = SupabaseEmailOtpAuth(
        identity_controller=IdentityController(
            credential_store=InMemoryCredentialStore(),
            metadata_store=IdentityMetadataStore(tmp_path / "identity" / "metadata.json"),
            diagnostics=IdentityDiagnostics(tmp_path / "logs"),
        ),
        credential_bridge=RecordingBridge(),
        config_loader=missing_config,
        client_factory=lambda _config: calls.append("constructed"),
    )

    assert calls == []
    assert auth.current_state is AccountState.SIGNED_OUT
    with pytest.raises(SupabaseAuthMissingConfigError):
        auth.request_code("teacher@example.com")
    assert calls == []
    assert auth.current_state is AccountState.SIGNED_OUT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Teacher@Example.COM ", "teacher@example.com"),
        ("first.last+tag@example.test", "first.last+tag@example.test"),
    ],
)
def test_email_and_otp_validation(raw, expected):
    assert normalize_email(raw) == expected
    assert normalize_otp(" 123 456 ") == "123456"
    with pytest.raises(SupabaseAuthInputError):
        normalize_email("not-an-email")
    with pytest.raises(SupabaseAuthInputError):
        normalize_otp("12-456")


def test_request_code_success_uses_email_otp_and_pending_state(tmp_path):
    auth, client = _auth(tmp_path)

    result = auth.request_code("Teacher@Example.COM")

    assert result.email == "teacher@example.com"
    assert client.auth.otp_requests == [
        {
            "email": "teacher@example.com",
            "options": {"should_create_user": True},
        }
    ]
    assert auth.current_state is AccountState.SIGN_IN_PENDING


def test_request_code_provider_failure_is_recoverable_and_local_state_safe(tmp_path):
    client = FakeClient()
    client.auth.fail_request = ConnectionError("offline")
    auth, _client = _auth(tmp_path, client=client)

    with pytest.raises(SupabaseAuthProviderUnavailableError):
        auth.request_code("teacher@example.com")

    assert auth.current_state is AccountState.SIGNED_OUT


def test_verify_code_success_stores_only_refresh_bridge_and_keeps_access_memory_only(tmp_path):
    bridge = RecordingBridge()
    auth, client = _auth(tmp_path, bridge=bridge)
    auth.request_code("teacher@example.com")

    result = auth.verify_code("teacher@example.com", "123456")

    assert result.account_state is AccountState.SIGNED_IN_ONLINE
    assert auth.access_token_in_memory == ACCESS_CREDENTIAL
    assert bridge.refresh_values == [REFRESH_CREDENTIAL]
    assert client.auth.verify_requests == [
        {"email": "teacher@example.com", "token": "123456", "type": "email"}
    ]
    combined = ""
    metadata = tmp_path / "identity" / "metadata.json"
    diagnostics = tmp_path / "logs" / "identity.log"
    if metadata.exists():
        combined += metadata.read_text(encoding="utf-8")
    if diagnostics.exists():
        combined += diagnostics.read_text(encoding="utf-8")
    assert ACCESS_CREDENTIAL not in combined
    assert REFRESH_CREDENTIAL not in combined


def test_verify_code_does_not_write_credentials_to_sqlite_settings(tmp_path):
    auth, _client = _auth(tmp_path)
    auth.request_code("teacher@example.com")
    auth.verify_code("teacher@example.com", "123456")

    database = tmp_path / "test.db"
    if database.exists():
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        serialized = json.dumps(rows)
        assert ACCESS_CREDENTIAL not in serialized
        assert REFRESH_CREDENTIAL not in serialized


def test_verify_code_invalid_and_provider_failures_are_recoverable(tmp_path):
    client = FakeClient()
    client.auth.fail_verify = ValueError("invalid")
    auth, _client = _auth(tmp_path, client=client)
    auth.request_code("teacher@example.com")

    with pytest.raises(SupabaseAuthInvalidOtpError):
        auth.verify_code("teacher@example.com", "123456")
    assert auth.current_state is AccountState.SIGN_IN_PENDING

    client.auth.fail_verify = TimeoutError("timeout")
    with pytest.raises(SupabaseAuthProviderUnavailableError):
        auth.verify_code("teacher@example.com", "123456")
    assert auth.current_state is AccountState.PROVIDER_UNAVAILABLE

    client.auth.fail_verify = None
    result = auth.verify_code("teacher@example.com", "123456")
    assert result.account_state is AccountState.SIGNED_IN_ONLINE


def test_account_shell_constructs_without_supabase_client_or_publishable_key(
    qtbot, monkeypatch
):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)

    shell = AccountShell()
    qtbot.addWidget(shell)

    assert shell.auth.current_state is AccountState.SIGNED_OUT
    assert shell.auth._client is None
    assert not hasattr(shell, "stack")


def test_account_dialog_disables_controls_and_recovers_on_missing_config(qtbot, tmp_path):
    def missing_config(_env=None):
        from app.cloud.supabase_provider import ProviderConfigError

        raise ProviderConfigError("missing")

    auth, _client = _auth(tmp_path, config_loader=missing_config)
    dialog = AccountDialog(auth)
    qtbot.addWidget(dialog)
    dialog.email_edit.setText("teacher@example.com")

    dialog._request_code()
    assert not dialog.request_btn.isEnabled()

    qtbot.waitUntil(lambda: dialog.request_btn.isEnabled(), timeout=5000)
    assert "SUPABASE_PUBLISHABLE_KEY" in dialog.status_label.text()
    assert dialog.email_edit.isEnabled()


def test_account_dialog_worker_callback_updates_gui_thread(qtbot, tmp_path):
    auth, _client = _auth(tmp_path)
    dialog = AccountDialog(auth)
    qtbot.addWidget(dialog)
    dialog.email_edit.setText("teacher@example.com")

    dialog._request_code()

    qtbot.waitUntil(lambda: not dialog.otp_step.isHidden(), timeout=5000)
    assert QThread.currentThread() == dialog.thread()
    assert auth.current_state is AccountState.SIGN_IN_PENDING


def test_supabase_dependency_imports_for_packaging_probe():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import supabase; import app.cloud.supabase_auth; print('SUPABASE_IMPORT_OK')",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SUPABASE_IMPORT_OK" in result.stdout

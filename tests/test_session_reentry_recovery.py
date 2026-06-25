from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog

from app import config
from app.account_context import active_account_context, deactivate_account_context
from app.activation import ActivationCoordinator
from app.cloud.auth_identity import AuthenticatedIdentity
from app.cloud.supabase_auth import (
    SupabaseAuthCredentialMalformedError,
    SupabaseAuthCredentialRejectedError,
    SupabaseAuthMissingConfigError,
    SupabaseAuthProviderUnavailableError,
    SupabaseEmailOtpAuth,
)
from app.cloud.supabase_provider import ProviderConfigError
from app.cloud.supabase_provider import (
    DEVELOPMENT_PROJECT_REF,
    SUPABASE_REFRESH_CREDENTIAL_PREFIX,
    SupabaseCredentialBridge,
    SupabaseProjectConfig,
)
from app.db import engine as engine_mod
from app.db.engine import InvalidDatabaseError
from app.identity.controller import IdentityController
from app.identity.credential_store import InMemoryCredentialStore, credential_target
from app.identity.diagnostics import IdentityDiagnostics
from app.identity.metadata_store import IdentityMetadataStore
from app.restart import reset_restart_request, restart_requested
from app.ui.account_shell import AccountShell


USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())
WORKSPACE_A = str(uuid.uuid4())
WORKSPACE_B = str(uuid.uuid4())
ACCESS_TOKEN = "memory-access-token"
REFRESH_TOKEN = "stored-refresh-token"
REFRESH_TOKEN_REPLACEMENT = "replacement-refresh-token"


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    deactivate_account_context()
    engine_mod.unbind_engine()
    yield
    deactivate_account_context()
    engine_mod.unbind_engine()


def _row(workspace_id: str, role: str = "owner", name: str = "مساحة العمل"):
    return {
        "workspace_id": workspace_id,
        "role": role,
        "workspaces": {"id": workspace_id, "name": name},
    }


class FakeClient:
    def __init__(
        self,
        *,
        user_id: str = USER_A,
        rows=None,
        refresh_error: Exception | None = None,
    ):
        self.auth = self
        self.user_id = user_id
        self.rows = [] if rows is None else rows
        self.refresh_error = refresh_error
        self.refresh_requests = []
        self.calls = []

    def refresh_session(self, refresh_token):
        self.refresh_requests.append(refresh_token)
        if self.refresh_error is not None:
            raise self.refresh_error
        return SimpleNamespace(
            user=SimpleNamespace(id=self.user_id),
            session=SimpleNamespace(
                access_token=ACCESS_TOKEN,
                refresh_token=REFRESH_TOKEN_REPLACEMENT,
            ),
        )

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def select(self, fields):
        self.calls.append(("select", fields))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", key, value))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return SimpleNamespace(data=self.rows)


class FakeMainWindow:
    constructed = []

    def __init__(self, *, auth, activation_result, restart_callback=None):
        self.auth = auth
        self.activation_result = activation_result
        self.restart_callback = restart_callback
        FakeMainWindow.constructed.append(activation_result)

    def showNormal(self):
        return None

    def raise_(self):
        return None

    def activateWindow(self):
        return None


def _config_loader(_env=None):
    return SupabaseProjectConfig(
        project_ref=DEVELOPMENT_PROJECT_REF,
        api_url=f"https://{DEVELOPMENT_PROJECT_REF}.supabase.co",
        publishable_key="publishable-test-key",
    )


def _auth(tmp_path, store: InMemoryCredentialStore, client: FakeClient):
    controller = IdentityController(
        credential_store=store,
        metadata_store=IdentityMetadataStore(tmp_path / "identity" / "metadata.json"),
        diagnostics=IdentityDiagnostics(tmp_path / "logs"),
    )
    return SupabaseEmailOtpAuth(
        identity_controller=controller,
        credential_bridge=SupabaseCredentialBridge(store),
        config_loader=_config_loader,
        client_factory=lambda _config: client,
    )


def _prepare_runtime(tmp_path):
    deactivate_account_context()
    engine_mod.unbind_engine()
    config.apply_user_root(tmp_path / "TeacherHub")
    reset_restart_request()


def _immediate_worker(
    _owner,
    function,
    *,
    on_result,
    on_error,
    on_finished=None,
):
    try:
        result = function()
    except Exception as error:
        on_error(error)
    else:
        on_result(result)
    finally:
        if on_finished is not None:
            on_finished()
    return SimpleNamespace()


def _seed_refresh(store: InMemoryCredentialStore, user_id: str, secret: str = REFRESH_TOKEN):
    bridge = SupabaseCredentialBridge(store)
    bridge.store_refresh_secret_for_user(user_id, secret)
    return next(
        reference
        for reference in bridge.discover_refresh_credentials()
        if reference._user_id == user_id
    )


def _serialized_local_identity_state(tmp_path) -> str:
    combined = ""
    for path in (
        tmp_path / "identity" / "metadata.json",
        tmp_path / "logs" / "identity.log",
    ):
        if path.exists():
            combined += path.read_text(encoding="utf-8")
    database = tmp_path / "test.db"
    if database.exists():
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        combined += json.dumps(rows)
    return combined


def test_refresh_credential_discovery_returns_only_safe_single_reference():
    store = InMemoryCredentialStore()
    bridge = SupabaseCredentialBridge(store)

    assert bridge.discover_refresh_credentials() == ()

    bridge.store_refresh_secret_for_user(USER_A, REFRESH_TOKEN)
    references = bridge.discover_refresh_credentials()
    assert len(references) == 1
    assert repr(references[0]) == "SupabaseRefreshCredentialReference(<redacted>)"

    store.write_credential("Unrelated/example", "other-secret")
    assert bridge.discover_refresh_credentials() == references

    bridge.store_refresh_secret_for_user(USER_B, "other-refresh")
    assert len(bridge.discover_refresh_credentials()) == 2


def test_refresh_discovery_ignores_malformed_or_unrelated_targets():
    store = InMemoryCredentialStore()
    bridge = SupabaseCredentialBridge(store)
    valid_target = credential_target(
        f"{SUPABASE_REFRESH_CREDENTIAL_PREFIX}/{USER_A}/refresh"
    )
    malformed_target = credential_target(SUPABASE_REFRESH_CREDENTIAL_PREFIX) + "/NOT-A-UUID/refresh"
    wrong_suffix = credential_target(SUPABASE_REFRESH_CREDENTIAL_PREFIX) + f"/{USER_B}/other"

    store.inject_raw_target(valid_target, b'{"version":1,"secret":"refresh"}')
    store.inject_raw_target(malformed_target, b'{"version":1,"secret":"bad"}')
    store.inject_raw_target(wrong_suffix, b'{"version":1,"secret":"bad"}')

    references = bridge.discover_refresh_credentials()
    assert len(references) == 1
    assert references[0]._user_id == USER_A


def test_refresh_success_sets_memory_only_session_and_replaces_refresh(tmp_path):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(user_id=USER_A)
    auth = _auth(tmp_path, store, client)

    result = auth.refresh_remembered_session(reference)

    assert isinstance(result.identity, AuthenticatedIdentity)
    assert result.identity.user_id == USER_A
    assert auth.authenticated_identity.user_id == USER_A
    assert auth.access_token_in_memory == ACCESS_TOKEN
    assert client.refresh_requests == [REFRESH_TOKEN]
    bridge = SupabaseCredentialBridge(store)
    assert bridge.load_refresh_secret(reference) == REFRESH_TOKEN_REPLACEMENT
    combined = _serialized_local_identity_state(tmp_path)
    assert ACCESS_TOKEN not in combined
    assert REFRESH_TOKEN not in combined
    assert REFRESH_TOKEN_REPLACEMENT not in combined
    assert USER_A not in combined


def test_rejected_refresh_clears_only_selected_exact_credential(tmp_path):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    other_reference = _seed_refresh(store, USER_B, "other-refresh")
    client = FakeClient(refresh_error=ValueError("provider says no"))
    auth = _auth(tmp_path, store, client)

    with pytest.raises(SupabaseAuthCredentialRejectedError):
        auth.refresh_remembered_session(reference)

    bridge = SupabaseCredentialBridge(store)
    assert bridge.load_refresh_secret(other_reference) == "other-refresh"
    assert bridge.discover_refresh_credentials() == (other_reference,)
    assert auth.authenticated_identity is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()


def test_malformed_refresh_clears_only_identifiable_daftar_credential(tmp_path):
    store = InMemoryCredentialStore()
    bridge = SupabaseCredentialBridge(store)
    target = credential_target(f"{SUPABASE_REFRESH_CREDENTIAL_PREFIX}/{USER_A}/refresh")
    store.inject_raw_target(target, b"not-json")
    other_reference = _seed_refresh(store, USER_B, "other-refresh")
    reference = next(
        credential
        for credential in bridge.discover_refresh_credentials()
        if credential._user_id == USER_A
    )
    client = FakeClient()
    auth = _auth(tmp_path, store, client)

    with pytest.raises(SupabaseAuthCredentialMalformedError):
        auth.refresh_remembered_session(reference)

    assert bridge.discover_refresh_credentials() == (other_reference,)
    assert bridge.load_refresh_secret(other_reference) == "other-refresh"
    assert client.refresh_requests == []


def test_provider_unavailable_refresh_preserves_credential_for_retry(tmp_path):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(refresh_error=TimeoutError("offline"))
    auth = _auth(tmp_path, store, client)

    with pytest.raises(SupabaseAuthProviderUnavailableError):
        auth.refresh_remembered_session(reference)

    bridge = SupabaseCredentialBridge(store)
    assert bridge.load_refresh_secret(reference) == REFRESH_TOKEN
    assert bridge.discover_refresh_credentials() == (reference,)
    assert auth.authenticated_identity is None


def test_invalid_provider_config_preserves_refresh_and_opens_no_data(tmp_path):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient()

    def missing_config(_env=None):
        raise ProviderConfigError("missing")

    controller = IdentityController(
        credential_store=store,
        metadata_store=IdentityMetadataStore(tmp_path / "identity" / "metadata.json"),
        diagnostics=IdentityDiagnostics(tmp_path / "logs"),
    )
    auth = SupabaseEmailOtpAuth(
        identity_controller=controller,
        credential_bridge=SupabaseCredentialBridge(store),
        config_loader=missing_config,
        client_factory=lambda _config: client,
    )

    with pytest.raises(SupabaseAuthMissingConfigError):
        auth.refresh_remembered_session(reference)

    assert SupabaseCredentialBridge(store).load_refresh_secret(reference) == REFRESH_TOKEN
    assert client.refresh_requests == []
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()


def test_invalid_refreshed_identity_fails_closed_and_clears_selected_credential(tmp_path):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(user_id=USER_B)
    auth = _auth(tmp_path, store, client)

    with pytest.raises(SupabaseAuthCredentialRejectedError):
        auth.refresh_remembered_session(reference)

    assert SupabaseCredentialBridge(store).discover_refresh_credentials() == ()
    assert auth.authenticated_identity is None


@pytest.mark.parametrize("count", [0, 2])
def test_account_shell_continue_visibility_requires_exactly_one_credential(
    tmp_path,
    qtbot,
    count,
):
    store = InMemoryCredentialStore()
    if count:
        _seed_refresh(store, USER_A)
        _seed_refresh(store, USER_B, "other-refresh")
    client = FakeClient()
    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    assert not shell.continue_btn.isVisible()


def test_account_shell_continue_visible_for_one_credential_without_identity(tmp_path, qtbot):
    store = InMemoryCredentialStore()
    _seed_refresh(store, USER_A)
    shell = AccountShell(auth=_auth(tmp_path, store, FakeClient()))
    qtbot.addWidget(shell)

    assert not shell.continue_btn.isHidden()
    assert shell.continue_btn.text() == "متابعة"
    visible = shell.continue_btn.text() + shell.status_label.text()
    assert USER_A not in visible
    assert REFRESH_TOKEN not in visible


def test_account_shell_startup_does_not_refresh_automatically(tmp_path, qtbot):
    store = InMemoryCredentialStore()
    _seed_refresh(store, USER_A)
    client = FakeClient()

    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    assert client.refresh_requests == []
    assert shell.auth.authenticated_identity is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()


def test_account_shell_provider_unavailable_reenables_continue_without_deleting_refresh(
    tmp_path,
    qtbot,
    monkeypatch,
):
    import app.ui.account_shell as account_shell_mod

    monkeypatch.setattr(account_shell_mod, "run_in_background", _immediate_worker)
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(refresh_error=TimeoutError("offline"))
    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    shell.continue_remembered_session()

    assert not shell.continue_btn.isHidden()
    assert shell.continue_btn.isEnabled()
    assert SupabaseCredentialBridge(store).load_refresh_secret(reference) == REFRESH_TOKEN
    assert shell.auth.authenticated_identity is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()


def test_continue_refresh_then_one_workspace_activates_after_existing_gates(
    tmp_path,
    qtbot,
    monkeypatch,
):
    import app.ui.account_shell as account_shell_mod

    _prepare_runtime(tmp_path)
    FakeMainWindow.constructed = []
    monkeypatch.setitem(sys.modules, "app.ui.main_window", SimpleNamespace(MainWindow=FakeMainWindow))
    monkeypatch.setattr(account_shell_mod, "run_in_background", _immediate_worker)
    store = InMemoryCredentialStore()
    _seed_refresh(store, USER_A)
    client = FakeClient(user_id=USER_A, rows=[_row(WORKSPACE_A)])
    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    shell.continue_remembered_session()

    selected_db = (
        tmp_path
        / "TeacherHub"
        / "accounts"
        / USER_A
        / "workspaces"
        / WORKSPACE_A
        / "data"
        / "teacher.db"
    )
    assert selected_db.exists()
    assert FakeMainWindow.constructed
    assert FakeMainWindow.constructed[-1].workspace.workspace_id == WORKSPACE_A
    assert client.refresh_requests == [REFRESH_TOKEN]
    assert ("execute",) in client.calls


def test_continue_then_multi_workspace_cancel_keeps_engine_unbound_and_retryable(
    tmp_path,
    qtbot,
    monkeypatch,
):
    import app.ui.account_shell as account_shell_mod

    _prepare_runtime(tmp_path)
    monkeypatch.setattr(account_shell_mod, "run_in_background", _immediate_worker)
    store = InMemoryCredentialStore()
    _seed_refresh(store, USER_A)
    client = FakeClient(
        user_id=USER_A,
        rows=[
            _row(WORKSPACE_A, name="Alpha"),
            _row(WORKSPACE_B, name="Beta"),
        ],
    )
    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    class CancelingDialog:
        selected_membership = None

        def __init__(self, _memberships, _parent=None):
            with pytest.raises(InvalidDatabaseError):
                engine_mod.get_session()
            assert active_account_context() is None

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(account_shell_mod, "WorkspacePickerDialog", CancelingDialog)

    shell.continue_remembered_session()

    assert active_account_context() is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()
    assert not (tmp_path / "TeacherHub" / "accounts").exists()
    assert not restart_requested()
    assert shell.auth.authenticated_identity.user_id == USER_A
    shell.open_account_dialog()
    assert client.calls.count(("execute",)) == 2


def test_workspace_lookup_failure_after_refresh_preserves_retry_state_and_credential(
    tmp_path,
    qtbot,
    monkeypatch,
):
    import app.ui.account_shell as account_shell_mod

    _prepare_runtime(tmp_path)
    monkeypatch.setattr(account_shell_mod, "run_in_background", _immediate_worker)
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(user_id=USER_A, rows="malformed")
    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    shell.continue_remembered_session()

    assert shell.auth.authenticated_identity.user_id == USER_A
    assert SupabaseCredentialBridge(store).load_refresh_secret(reference) == REFRESH_TOKEN_REPLACEMENT
    assert active_account_context() is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()
    assert not (tmp_path / "TeacherHub" / "accounts").exists()


def test_repeated_continue_clicks_do_not_start_parallel_refresh(
    tmp_path,
    qtbot,
    monkeypatch,
):
    import app.ui.account_shell as account_shell_mod

    store = InMemoryCredentialStore()
    _seed_refresh(store, USER_A)
    shell = AccountShell(auth=_auth(tmp_path, store, FakeClient()))
    qtbot.addWidget(shell)
    calls = []

    def blocked_worker(*_args, **_kwargs):
        calls.append("worker")

    monkeypatch.setattr(account_shell_mod, "run_in_background", blocked_worker)
    shell._refresh_in_progress = True

    shell.continue_remembered_session()

    assert calls == []


def test_explicit_sign_out_removes_continue_availability_after_refresh(tmp_path, qtbot):
    store = InMemoryCredentialStore()
    reference = _seed_refresh(store, USER_A)
    client = FakeClient(user_id=USER_A)
    auth = _auth(tmp_path, store, client)
    auth.refresh_remembered_session(reference)
    ActivationCoordinator.controlled_sign_out(auth)

    shell = AccountShell(auth=_auth(tmp_path, store, client))
    qtbot.addWidget(shell)

    assert shell.continue_btn.isHidden()
    assert SupabaseCredentialBridge(store).discover_refresh_credentials() == ()

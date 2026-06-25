from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app import config
from app.account_context import active_account_context, deactivate_account_context
from app.activation import ActivationCoordinator
from app.cloud.auth_identity import AuthenticatedIdentity
from app.cloud.supabase_auth import SupabaseAuthInvalidOtpError
from app.cloud.supabase_workspace_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceLookupError,
    WorkspaceSelectionError,
    WorkspaceUnavailableError,
    select_single_workspace,
)
from app.db import engine as engine_mod


USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())
WORKSPACE_A = str(uuid.uuid4())
WORKSPACE_B = str(uuid.uuid4())
ACCESS_TOKEN = "memory-access-token"
REFRESH_TOKEN = "refresh-token-secret"


class FakeAuthApi:
    def __init__(self, user_id=USER_A):
        self.response = SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            session=SimpleNamespace(
                access_token=ACCESS_TOKEN,
                refresh_token=REFRESH_TOKEN,
            ),
        )

    def sign_in_with_otp(self, _payload):
        return SimpleNamespace()

    def verify_otp(self, _payload):
        return self.response


class FakeClient:
    def __init__(self, rows=None, *, failure: Exception | None = None):
        self.auth = FakeAuthApi()
        self.rows = rows if rows is not None else [_row(WORKSPACE_A)]
        self.failure = failure
        self.calls = []

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
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(data=self.rows)


class RecordingBridge:
    def __init__(self):
        self.stored = []
        self.cleared = []

    def store_refresh_secret_for_user(self, user_id, secret):
        self.stored.append((user_id, secret))

    def clear_refresh_secret_for_user(self, user_id):
        self.cleared.append(user_id)


def _row(workspace_id, role="owner", name="مساحة العمل"):
    return {
        "workspace_id": workspace_id,
        "role": role,
        "workspaces": {"id": workspace_id, "name": name},
    }


def _auth(tmp_path, client=None, bridge=None):
    from app.cloud.supabase_auth import SupabaseEmailOtpAuth
    from app.cloud.supabase_provider import DEVELOPMENT_PROJECT_REF, SupabaseProjectConfig
    from app.identity.controller import IdentityController
    from app.identity.credential_store import InMemoryCredentialStore
    from app.identity.diagnostics import IdentityDiagnostics
    from app.identity.metadata_store import IdentityMetadataStore

    fake_client = client or FakeClient()
    identity_controller = IdentityController(
        credential_store=InMemoryCredentialStore(),
        metadata_store=IdentityMetadataStore(tmp_path / "identity" / "metadata.json"),
        diagnostics=IdentityDiagnostics(tmp_path / "logs"),
    )
    return SupabaseEmailOtpAuth(
        identity_controller=identity_controller,
        credential_bridge=bridge or RecordingBridge(),
        config_loader=lambda _env=None: SupabaseProjectConfig(
            project_ref=DEVELOPMENT_PROJECT_REF,
            api_url=f"https://{DEVELOPMENT_PROJECT_REF}.supabase.co",
            publishable_key="publishable-test-key",
        ),
        client_factory=lambda _config: fake_client,
    )


def _activate(tmp_path, user_id=USER_A, workspace_id=WORKSPACE_A):
    try:
        deactivate_account_context()
    except Exception:
        pass
    identity = AuthenticatedIdentity(user_id)
    repo = SupabaseWorkspaceRepository(lambda: FakeClient([_row(workspace_id)]))
    coordinator = ActivationCoordinator(repo)
    legacy = tmp_path / "TeacherHub" / "data" / "teacher.db"
    engine_mod.unbind_engine()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"legacy sentinel")
    config.apply_user_root(tmp_path / "TeacherHub")
    return coordinator.activate(identity), legacy


def test_authenticated_identity_result_is_canonical_secret_free(tmp_path):
    bridge = RecordingBridge()
    auth = _auth(tmp_path, bridge=bridge)
    auth.request_code("teacher@example.com")

    result = auth.verify_code("teacher@example.com", "123456")

    assert result.identity.user_id == USER_A
    assert ACCESS_TOKEN not in repr(result)
    assert REFRESH_TOKEN not in repr(result)
    assert "teacher@example.com" not in repr(result)
    assert bridge.stored == [(USER_A, REFRESH_TOKEN)]
    with pytest.raises(TypeError):
        asdict(result.identity)


def test_malformed_authenticated_user_uuid_fails_before_refresh_storage(tmp_path):
    bridge = RecordingBridge()
    client = FakeClient()
    client.auth = FakeAuthApi("not-a-uuid")
    auth = _auth(tmp_path, client=client, bridge=bridge)
    auth.request_code("teacher@example.com")

    with pytest.raises(SupabaseAuthInvalidOtpError):
        auth.verify_code("teacher@example.com", "123456")

    assert bridge.stored == []
    assert auth.access_token_in_memory is None
    assert auth.authenticated_identity is None


def test_workspace_repository_auto_selects_exactly_one_authorized_workspace():
    client = FakeClient([_row(WORKSPACE_A, role="admin", name="الفرع الرئيسي")])
    repo = SupabaseWorkspaceRepository(lambda: client)

    memberships = repo.list_memberships(AuthenticatedIdentity(USER_A))
    selected = select_single_workspace(memberships)

    assert selected.workspace_id == WORKSPACE_A
    assert selected.role == "admin"
    assert selected.display_name == "الفرع الرئيسي"
    assert client.calls == [
        ("table", "workspace_members"),
        ("select", "workspace_id,role,workspaces(id,name)"),
        ("eq", "user_id", USER_A),
    ]


def test_activation_coordinator_orders_authorized_workspaces_deterministically():
    workspace_c = str(uuid.UUID(int=3))
    workspace_d = str(uuid.UUID(int=4))
    rows = [
        _row(WORKSPACE_B, role="member", name="Beta"),
        _row(WORKSPACE_A, role="admin", name="alpha"),
        _row(workspace_c, role="owner", name="Alpha"),
        _row(workspace_d, role="member", name="Alpha"),
    ]
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient(rows))
    )

    ordered = coordinator.list_authorized_workspaces(AuthenticatedIdentity(USER_A))

    assert [membership.workspace_id for membership in ordered] == [
        workspace_c,
        WORKSPACE_A,
        workspace_d,
        WORKSPACE_B,
    ]


def test_activate_workspace_rejects_membership_not_returned_by_authorized_lookup(tmp_path):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_A)]))
    )
    identity = AuthenticatedIdentity(USER_A)
    coordinator.list_authorized_workspaces(identity)
    unauthorized = next(
        iter(SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_B)])).list_memberships(identity))
    )

    with pytest.raises(WorkspaceLookupError):
        coordinator.activate_workspace(identity, unauthorized)

    assert active_account_context() is None
    assert not (
        clean_root / "accounts" / USER_A / "workspaces" / WORKSPACE_B / "data" / "teacher.db"
    ).exists()


def test_activate_workspace_rejects_structurally_equal_replacement_membership(tmp_path):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_A)]))
    )
    identity = AuthenticatedIdentity(USER_A)
    authorized = coordinator.list_authorized_workspaces(identity)
    replacement = type(authorized[0])(
        workspace_id=authorized[0].workspace_id,
        role=authorized[0].role,
        display_name=authorized[0].display_name,
    )

    assert replacement == authorized[0]
    assert replacement is not authorized[0]
    with pytest.raises(WorkspaceLookupError):
        coordinator.activate_workspace(identity, replacement)

    assert active_account_context() is None
    assert not (
        clean_root / "accounts" / USER_A / "workspaces" / WORKSPACE_A / "data" / "teacher.db"
    ).exists()


def test_activate_workspace_rejects_membership_from_another_identity(tmp_path):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_A)]))
    )
    user_a = AuthenticatedIdentity(USER_A)
    user_b = AuthenticatedIdentity(USER_B)
    user_a_memberships = coordinator.list_authorized_workspaces(user_a)

    with pytest.raises(WorkspaceLookupError):
        coordinator.activate_workspace(user_b, user_a_memberships[0])

    assert active_account_context() is None
    assert not (
        clean_root / "accounts" / USER_B / "workspaces" / WORKSPACE_A / "data" / "teacher.db"
    ).exists()


def test_activate_workspace_rejects_stale_selection_after_new_lookup(tmp_path):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_A)]))
    )
    identity = AuthenticatedIdentity(USER_A)
    stale = coordinator.list_authorized_workspaces(identity)[0]
    fresh = coordinator.list_authorized_workspaces(identity)[0]

    assert stale == fresh
    assert stale is not fresh
    with pytest.raises(WorkspaceLookupError):
        coordinator.activate_workspace(identity, stale)

    assert active_account_context() is None
    assert not (
        clean_root / "accounts" / USER_A / "workspaces" / WORKSPACE_A / "data" / "teacher.db"
    ).exists()


def test_activate_workspace_accepts_exact_current_authorized_selection(tmp_path):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient([_row(WORKSPACE_A)]))
    )
    identity = AuthenticatedIdentity(USER_A)
    selected = coordinator.list_authorized_workspaces(identity)[0]

    result = coordinator.activate_workspace(identity, selected)

    assert result.workspace is selected
    assert result.context.database_path.exists()
    assert result.context.database_path == (
        clean_root / "accounts" / USER_A / "workspaces" / WORKSPACE_A / "data" / "teacher.db"
    )


def test_workspace_repository_fails_closed_for_zero_multiple_malformed_and_provider_failure():
    identity = AuthenticatedIdentity(USER_A)

    with pytest.raises(WorkspaceSelectionError):
        select_single_workspace(SupabaseWorkspaceRepository(lambda: FakeClient([])).list_memberships(identity))
    with pytest.raises(WorkspaceSelectionError):
        select_single_workspace(
            SupabaseWorkspaceRepository(
                lambda: FakeClient([_row(WORKSPACE_A), _row(WORKSPACE_B)])
            ).list_memberships(identity)
        )
    with pytest.raises(WorkspaceSelectionError):
        SupabaseWorkspaceRepository(lambda: FakeClient([_row("bad")])).list_memberships(identity)
    with pytest.raises(WorkspaceUnavailableError):
        SupabaseWorkspaceRepository(
            lambda: FakeClient(failure=ConnectionError("offline"))
        ).list_memberships(identity)


def test_activation_opens_only_account_scoped_database_and_preserves_legacy(tmp_path):
    result, legacy = _activate(tmp_path)

    assert active_account_context() == result.context
    assert result.context.database_path.exists()
    assert legacy.read_bytes() == b"legacy sentinel"
    assert config.DB_PATH == result.context.database_path
    assert config.BACKUPS_DIR == result.context.backups_dir
    assert config.EXPORTS_DIR == result.context.exports_dir
    assert config.INVOICES_DIR == result.context.invoices_dir
    with sqlite3.connect(result.context.database_path) as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()


def test_activation_derives_separate_roots_for_user_b(tmp_path):
    first, _legacy = _activate(tmp_path, USER_A, WORKSPACE_A)
    deactivate_account_context()
    config.apply_user_root(tmp_path / "TeacherHub")
    second, _legacy = _activate(tmp_path, USER_B, WORKSPACE_B)

    assert first.context.database_path != second.context.database_path
    assert USER_A in first.context.database_path.as_posix()
    assert USER_B in second.context.database_path.as_posix()


def test_activation_failure_rolls_back_context_and_never_constructs_main_window(tmp_path, monkeypatch):
    clean_root = tmp_path / "ActivationRoot"
    engine_mod.unbind_engine()
    config.apply_user_root(clean_root)
    repo = SupabaseWorkspaceRepository(lambda: FakeClient([]))
    coordinator = ActivationCoordinator(repo)
    constructed = []

    class ForbiddenMainWindow:
        def __init__(self, *_args, **_kwargs):
            constructed.append(True)

    monkeypatch.setattr("app.ui.main_window.MainWindow", ForbiddenMainWindow, raising=False)

    with pytest.raises(WorkspaceSelectionError):
        coordinator.activate(AuthenticatedIdentity(USER_A))

    assert active_account_context() is None
    assert constructed == []
    assert not (clean_root / "data" / "teacher.db").exists()


def test_account_scoped_backup_export_invoice_paths_after_activation(tmp_path):
    result, _legacy = _activate(tmp_path)
    from app.services import billing_service, excel_service, pdf_service

    assert config.BACKUPS_DIR == result.context.backups_dir
    assert billing_service.config.BACKUPS_DIR == result.context.backups_dir
    assert excel_service.export_students_xlsx().parent == result.context.exports_dir
    assert pdf_service.INVOICES_DIR == result.context.invoices_dir


def test_sign_out_disposes_engine_context_and_deletes_only_active_refresh(tmp_path):
    from app.db.engine import InvalidDatabaseError

    bridge = RecordingBridge()
    auth = _auth(tmp_path, bridge=bridge)
    auth.request_code("teacher@example.com")
    auth.verify_code("teacher@example.com", "123456")
    _activate(tmp_path)

    ActivationCoordinator.controlled_sign_out(auth)

    assert bridge.cleared == [USER_A]
    assert active_account_context() is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()


def test_no_identity_workspace_or_secret_persistence_in_local_stores(tmp_path):
    bridge = RecordingBridge()
    auth = _auth(tmp_path, bridge=bridge)
    auth.request_code("teacher@example.com")
    auth.verify_code("teacher@example.com", "123456")
    _activate(tmp_path)

    combined = ""
    for path in (tmp_path / "identity").rglob("*"):
        if path.is_file():
            combined += path.read_text(encoding="utf-8")
    for path in (tmp_path / "logs").rglob("*"):
        if path.is_file():
            combined += path.read_text(encoding="utf-8")
    if config.DB_PATH.exists():
        with sqlite3.connect(config.DB_PATH) as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
            combined += json.dumps(rows)

    for forbidden in (USER_A, WORKSPACE_A, "teacher@example.com", ACCESS_TOKEN, REFRESH_TOKEN):
        assert forbidden not in combined

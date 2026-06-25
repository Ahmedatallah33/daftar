from __future__ import annotations

import sys
import uuid
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from app import config
from app.account_context import active_account_context, deactivate_account_context
from app.activation import ActivationCoordinator
from app.cloud.auth_identity import AuthenticatedIdentity
from app.cloud.supabase_workspace_repository import SupabaseWorkspaceRepository
from app.db import engine as engine_mod
from app.db.engine import InvalidDatabaseError
from app.identity.models import AccountState
from app.restart import reset_restart_request, restart_requested
from app.ui.account_shell import AccountShell
from app.ui.pages.workspace_picker_dialog import WorkspacePickerDialog


USER_ID = str(uuid.uuid4())
WORKSPACE_A = str(uuid.uuid4())
WORKSPACE_B = str(uuid.uuid4())


def _row(workspace_id: str, role: str = "owner", name: str = "مساحة العمل"):
    return {
        "workspace_id": workspace_id,
        "role": role,
        "workspaces": {"id": workspace_id, "name": name},
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return self

    def select(self, _fields):
        return self

    def eq(self, _key, _value):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeAuth:
    current_state = AccountState.SIGNED_OUT

    def __init__(self):
        self.sign_out_calls = 0

    @property
    def authenticated_client(self):
        raise AssertionError("tests provide the repository client directly")

    def sign_out(self):
        self.sign_out_calls += 1


class FakeAuthWithIdentity(FakeAuth):
    current_state = AccountState.SIGNED_IN_ONLINE

    @property
    def authenticated_identity(self):
        return AuthenticatedIdentity(USER_ID)


class FakeMainWindow:
    constructed = []

    def __init__(self, *, auth, activation_result, restart_callback=None):
        self.auth = auth
        self.activation_result = activation_result
        self.restart_callback = restart_callback
        self.shown = False
        FakeMainWindow.constructed.append(activation_result)

    def showNormal(self):
        self.shown = True

    def raise_(self):
        return None

    def activateWindow(self):
        return None


def _prepare_signed_out_runtime(tmp_path):
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


def _shell_with_memberships(qtbot, monkeypatch, rows):
    import app.ui.account_shell as account_shell_mod

    FakeMainWindow.constructed = []
    monkeypatch.setitem(sys.modules, "app.ui.main_window", SimpleNamespace(MainWindow=FakeMainWindow))
    monkeypatch.setattr(account_shell_mod, "run_in_background", _immediate_worker)
    shell = AccountShell(auth=FakeAuth())
    qtbot.addWidget(shell)
    coordinator = ActivationCoordinator(
        SupabaseWorkspaceRepository(lambda: FakeClient(rows))
    )
    shell._activation_coordinator = coordinator
    identity = AuthenticatedIdentity(USER_ID)
    memberships = coordinator.list_authorized_workspaces(identity)
    return shell, identity, memberships


def test_workspace_picker_requires_selection_and_hides_identifiers(qtbot):
    memberships = (
        SupabaseWorkspaceRepository(
            lambda: FakeClient(
                [_row(WORKSPACE_A, role="owner", name="Alpha")]
            )
        )
        .list_memberships(AuthenticatedIdentity(USER_ID))
    )
    dialog = WorkspacePickerDialog(memberships)
    qtbot.addWidget(dialog)

    assert not dialog.open_btn.isEnabled()
    visible_text = dialog.workspace_list.item(0).text()
    assert "Alpha" in visible_text
    assert "مالك مساحة العمل" in visible_text
    assert WORKSPACE_A not in visible_text
    assert USER_ID not in visible_text

    dialog.workspace_list.setCurrentRow(0)
    assert dialog.open_btn.isEnabled()
    dialog.open_btn.click()
    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_membership == memberships[0]


def test_workspace_picker_escape_cancels_without_selection(qtbot):
    memberships = (
        SupabaseWorkspaceRepository(
            lambda: FakeClient([_row(WORKSPACE_A, role="member", name="Beta")])
        )
        .list_memberships(AuthenticatedIdentity(USER_ID))
    )
    dialog = WorkspacePickerDialog(memberships)
    qtbot.addWidget(dialog)

    dialog.show()
    QTest.keyClick(dialog, Qt.Key_Escape)

    assert dialog.result() == QDialog.Rejected
    assert dialog.selected_membership is None


@pytest.mark.parametrize(
    ("selected_workspace", "other_workspace"),
    [(WORKSPACE_A, WORKSPACE_B), (WORKSPACE_B, WORKSPACE_A)],
)
def test_account_shell_selects_one_workspace_before_database_activation(
    tmp_path,
    qtbot,
    monkeypatch,
    selected_workspace,
    other_workspace,
):
    _prepare_signed_out_runtime(tmp_path)
    shell, identity, memberships = _shell_with_memberships(
        qtbot,
        monkeypatch,
        [
            _row(WORKSPACE_A, role="owner", name="Alpha"),
            _row(WORKSPACE_B, role="admin", name="Beta"),
        ],
    )

    class SelectingDialog:
        def __init__(self, received_memberships, _parent=None):
            assert received_memberships == memberships
            with pytest.raises(InvalidDatabaseError):
                engine_mod.get_session()
            assert active_account_context() is None
            self.selected_membership = next(
                membership
                for membership in received_memberships
                if membership.workspace_id == selected_workspace
            )

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(
        "app.ui.account_shell.WorkspacePickerDialog",
        SelectingDialog,
    )

    shell._on_workspaces_loaded(identity, memberships)

    selected_db = (
        tmp_path
        / "TeacherHub"
        / "accounts"
        / USER_ID
        / "workspaces"
        / selected_workspace
        / "data"
        / "teacher.db"
    )
    other_db = (
        tmp_path
        / "TeacherHub"
        / "accounts"
        / USER_ID
        / "workspaces"
        / other_workspace
        / "data"
        / "teacher.db"
    )
    assert selected_db.exists()
    assert not other_db.exists()
    assert FakeMainWindow.constructed
    assert FakeMainWindow.constructed[-1].workspace.workspace_id == selected_workspace


def test_account_shell_picker_cancel_keeps_shell_signed_out_and_unbound(
    tmp_path,
    qtbot,
    monkeypatch,
):
    _prepare_signed_out_runtime(tmp_path)
    shell, identity, memberships = _shell_with_memberships(
        qtbot,
        monkeypatch,
        [
            _row(WORKSPACE_A, role="owner", name="Alpha"),
            _row(WORKSPACE_B, role="member", name="Beta"),
        ],
    )

    class CancelingDialog:
        selected_membership = None

        def __init__(self, _memberships, _parent=None):
            return None

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(
        "app.ui.account_shell.WorkspacePickerDialog",
        CancelingDialog,
    )

    shell._on_workspaces_loaded(identity, memberships)

    assert shell.auth.sign_out_calls == 0
    assert not restart_requested()
    assert active_account_context() is None
    with pytest.raises(InvalidDatabaseError):
        engine_mod.get_session()
    assert not (tmp_path / "TeacherHub" / "accounts").exists()
    assert FakeMainWindow.constructed == []


def test_account_shell_retries_workspace_selection_without_new_otp_dialog(qtbot, monkeypatch):
    import app.ui.account_shell as account_shell_mod

    shell = AccountShell(auth=FakeAuthWithIdentity())
    qtbot.addWidget(shell)
    started = []
    monkeypatch.setattr(
        shell,
        "_start_workspace_lookup",
        lambda identity: started.append(identity.user_id),
    )

    class ForbiddenDialog:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("OTP dialog should not reopen for an active identity")

    monkeypatch.setattr(account_shell_mod, "AccountDialog", ForbiddenDialog)

    shell.open_account_dialog()

    assert started == [USER_ID]


def test_account_shell_rejects_duplicate_activation_requests(
    tmp_path,
    qtbot,
    monkeypatch,
):
    _prepare_signed_out_runtime(tmp_path)
    shell, identity, memberships = _shell_with_memberships(
        qtbot,
        monkeypatch,
        [_row(WORKSPACE_A, role="owner", name="Alpha")],
    )
    calls = []

    def forbidden_worker(*_args, **_kwargs):
        calls.append("called")

    monkeypatch.setattr("app.ui.account_shell.run_in_background", forbidden_worker)
    shell._activation_in_progress = True

    shell._start_activation(identity, memberships[0])

    assert calls == []
    assert active_account_context() is None

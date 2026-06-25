from __future__ import annotations

import sqlite3
import uuid

import pytest

from app import config
from app.account_context import (
    AccountContextError,
    activate_account_context,
    active_account_context,
    deactivate_account_context,
    resolve_account_context,
)


def _restore_root(root):
    deactivate_account_context()
    config.apply_user_root(root)


def test_account_context_resolves_isolated_paths_without_creating_files(tmp_path):
    user_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    context = resolve_account_context(user_id, workspace_id, base_root=tmp_path)

    expected_root = tmp_path / "accounts" / user_id / "workspaces" / workspace_id
    assert context.user_root == expected_root.resolve()
    assert context.data_dir == context.user_root / "data"
    assert context.database_path == context.user_root / "data" / "teacher.db"
    assert context.backups_dir == context.user_root / "backups"
    assert context.exports_dir == context.user_root / "exports"
    assert context.invoices_dir == context.user_root / "exports" / "invoices"
    assert context.restore_dir == context.user_root / "restore"
    assert context.logs_dir == context.user_root / "logs"
    assert not context.user_root.exists()


@pytest.mark.parametrize(
    "bad_value",
    [
        "",
        "../escape",
        "not-a-uuid",
        str(uuid.uuid4()).upper(),
        uuid.uuid4().hex,
        f"{uuid.uuid4()}/extra",
    ],
)
def test_account_context_rejects_invalid_identifiers(tmp_path, bad_value):
    valid = str(uuid.uuid4())

    with pytest.raises(AccountContextError):
        resolve_account_context(bad_value, valid, base_root=tmp_path)
    with pytest.raises(AccountContextError):
        resolve_account_context(valid, bad_value, base_root=tmp_path)


def test_account_contexts_for_different_accounts_are_separate(tmp_path):
    workspace_id = str(uuid.uuid4())

    first = resolve_account_context(str(uuid.uuid4()), workspace_id, base_root=tmp_path)
    second = resolve_account_context(str(uuid.uuid4()), workspace_id, base_root=tmp_path)

    assert first.user_root != second.user_root
    assert first.database_path != second.database_path
    assert first.exports_dir != second.exports_dir


def test_activation_initializes_only_selected_context_and_preserves_legacy_root(tmp_path):
    original_root = config.USER_ROOT
    legacy_root = tmp_path / "TeacherHub"
    config.apply_user_root(legacy_root)
    legacy_db = legacy_root / "data" / "teacher.db"
    legacy_db.parent.mkdir(parents=True)
    legacy_db.write_bytes(b"legacy sentinel")
    context = resolve_account_context(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        base_root=legacy_root,
    )

    try:
        activated = activate_account_context(context)

        assert activated == context
        assert active_account_context() == context
        assert context.database_path.exists()
        with sqlite3.connect(context.database_path) as connection:
            assert connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        assert legacy_db.read_bytes() == b"legacy sentinel"
    finally:
        _restore_root(original_root)


def test_deactivation_clears_active_context_and_allows_next_context(tmp_path):
    original_root = config.USER_ROOT
    base = tmp_path / "TeacherHub"
    first = resolve_account_context(str(uuid.uuid4()), str(uuid.uuid4()), base_root=base)
    second = resolve_account_context(str(uuid.uuid4()), str(uuid.uuid4()), base_root=base)

    try:
        activate_account_context(first, initialize=False)
        with pytest.raises(AccountContextError):
            activate_account_context(second, initialize=False)

        deactivate_account_context()
        assert active_account_context() is None

        activate_account_context(second, initialize=False)
        assert active_account_context() == second
    finally:
        _restore_root(original_root)

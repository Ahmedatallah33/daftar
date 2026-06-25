"""Account/workspace local-storage boundary for future mandatory sign-in."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app import config


class AccountContextError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class AccountWorkspaceContext:
    user_id: str
    workspace_id: str
    user_root: Path
    data_dir: Path
    database_path: Path
    backups_dir: Path
    exports_dir: Path
    invoices_dir: Path
    restore_dir: Path
    logs_dir: Path

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    def __repr__(self) -> str:
        return "AccountWorkspaceContext(user_id=<redacted>, workspace_id=<redacted>)"


_active_context: AccountWorkspaceContext | None = None


def resolve_account_context(
    user_id: str,
    workspace_id: str,
    *,
    base_root: Path | None = None,
) -> AccountWorkspaceContext:
    """Resolve future per-account/workspace writable paths without creating them."""

    safe_user_id = _canonical_uuid(user_id, "user_id")
    safe_workspace_id = _canonical_uuid(workspace_id, "workspace_id")
    root = Path(base_root or config.USER_ROOT).expanduser().resolve()
    accounts_root = root / "accounts"
    context_root = accounts_root / safe_user_id / "workspaces" / safe_workspace_id
    resolved = context_root.resolve()
    try:
        resolved.relative_to(accounts_root.resolve())
    except ValueError as error:
        raise AccountContextError("Resolved account context escaped the accounts root.") from error
    exports_dir = resolved / "exports"
    return AccountWorkspaceContext(
        user_id=safe_user_id,
        workspace_id=safe_workspace_id,
        user_root=resolved,
        data_dir=resolved / "data",
        database_path=resolved / "data" / "teacher.db",
        backups_dir=resolved / "backups",
        exports_dir=exports_dir,
        invoices_dir=exports_dir / "invoices",
        restore_dir=resolved / "restore",
        logs_dir=resolved / "logs",
    )


def activate_account_context(
    context: AccountWorkspaceContext,
    *,
    initialize: bool = True,
) -> AccountWorkspaceContext:
    """Activate a selected account/workspace database boundary."""

    global _active_context
    if _active_context is not None:
        raise AccountContextError("An account context is already active.")
    from app.db import engine as engine_mod

    try:
        config.apply_user_root(context.user_root)
        engine_mod.configure_engine(context.db_url)
        if initialize:
            from app.startup import initialize_account_context_data

            initialize_account_context_data()
        _active_context = context
        return context
    except Exception:
        engine_mod.unbind_engine()
        _active_context = None
        raise


def deactivate_account_context() -> None:
    """Release SQLAlchemy resources so another account can be selected later."""

    global _active_context
    from app.db import engine as engine_mod

    try:
        engine_mod.SessionLocal.remove()
    finally:
        engine_mod.unbind_engine()
        _active_context = None


def active_account_context() -> AccountWorkspaceContext | None:
    return _active_context


def _canonical_uuid(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise AccountContextError(f"{field_name} must be a UUID string.")
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise AccountContextError(f"{field_name} must be a valid UUID.") from error
    canonical = str(parsed)
    if value != canonical:
        raise AccountContextError(f"{field_name} must use canonical lowercase UUID form.")
    return canonical

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.cloud.auth_identity import AuthenticatedIdentity


class WorkspaceLookupError(RuntimeError):
    """Recoverable workspace lookup failure with a safe UI message."""


class WorkspaceUnavailableError(WorkspaceLookupError):
    pass


class WorkspaceSelectionError(WorkspaceLookupError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    workspace_id: str
    role: str
    display_name: str

    def __repr__(self) -> str:
        return (
            "WorkspaceMembership("
            "workspace_id=<redacted>, role=<redacted>, display_name=<redacted>)"
        )


ClientProvider = Callable[[], Any]


class SupabaseWorkspaceRepository:
    """Read authorized workspace membership through the authenticated client."""

    def __init__(self, client_provider: ClientProvider):
        self._client_provider = client_provider

    def list_memberships(self, identity: AuthenticatedIdentity) -> list[WorkspaceMembership]:
        try:
            response = (
                self._client_provider()
                .table("workspace_members")
                .select("workspace_id,role,workspaces(id,name)")
                .eq("user_id", identity.user_id)
                .execute()
            )
        except Exception as error:
            raise WorkspaceUnavailableError(
                "تعذر التحقق من مساحة العمل الآن. تأكد من الاتصال وحاول مرة أخرى."
            ) from error

        rows = _response_data(response)
        if not isinstance(rows, list):
            raise WorkspaceSelectionError(
                "تعذر قراءة مساحات العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
            )
        return [_membership_from_row(row) for row in rows]


def select_single_workspace(
    memberships: list[WorkspaceMembership],
) -> WorkspaceMembership:
    if len(memberships) == 1:
        return memberships[0]
    if not memberships:
        raise WorkspaceSelectionError(
            "لا توجد مساحة عمل مفعلة لهذا الحساب. لم يتم فتح أي بيانات محلية."
        )
    raise WorkspaceSelectionError(
        "هذا الحساب مرتبط بأكثر من مساحة عمل. اختيار مساحة العمل سيضاف في إصدار لاحق."
    )


def _response_data(response: Any) -> Any:
    if isinstance(response, Mapping):
        return response.get("data")
    return getattr(response, "data", None)


def _membership_from_row(row: Any) -> WorkspaceMembership:
    if not isinstance(row, Mapping):
        raise WorkspaceSelectionError(
            "تعذر قراءة مساحات العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    workspace_id = _canonical_uuid(row.get("workspace_id"))
    role = row.get("role")
    if role not in {"owner", "admin", "member"}:
        raise WorkspaceSelectionError(
            "تعذر قراءة صلاحية مساحة العمل. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    workspace = row.get("workspaces")
    if isinstance(workspace, list):
        workspace = workspace[0] if workspace else None
    if not isinstance(workspace, Mapping):
        raise WorkspaceSelectionError(
            "تعذر قراءة مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    linked_workspace_id = _canonical_uuid(workspace.get("id"))
    if linked_workspace_id != workspace_id:
        raise WorkspaceSelectionError(
            "تعذر تأكيد مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    name = workspace.get("name")
    display_name = name.strip() if isinstance(name, str) and name.strip() else "مساحة العمل"
    return WorkspaceMembership(
        workspace_id=workspace_id,
        role=role,
        display_name=display_name,
    )


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkspaceSelectionError(
            "تعذر قراءة مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise WorkspaceSelectionError(
            "تعذر قراءة مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        ) from error
    canonical = str(parsed)
    if value != canonical:
        raise WorkspaceSelectionError(
            "تعذر قراءة مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
        )
    return canonical

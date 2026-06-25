from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app import config
from app.account_context import (
    AccountWorkspaceContext,
    activate_account_context,
    deactivate_account_context,
    resolve_account_context,
)
from app.cloud.auth_identity import AuthenticatedIdentity
from app.cloud.supabase_workspace_repository import (
    SupabaseWorkspaceRepository,
    WorkspaceMembership,
    WorkspaceLookupError,
    select_single_workspace,
)
from app.ui.helpers.worker import set_background_operations_enabled


class ActivationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class ActivationResult:
    identity: AuthenticatedIdentity
    workspace: WorkspaceMembership
    context: AccountWorkspaceContext

    def __repr__(self) -> str:
        return "ActivationResult(identity=<redacted>, workspace=<redacted>)"


_ROLE_ORDER = {"owner": 0, "admin": 1, "member": 2}
_UNAUTHORIZED_WORKSPACE_MESSAGE = (
    "تعذر تأكيد مساحة العمل المصرح بها. أعد تسجيل الدخول وحاول مرة أخرى."
)


class ActivationCoordinator:
    def __init__(
        self,
        workspace_repository: SupabaseWorkspaceRepository,
        *,
        context_resolver: Callable[..., AccountWorkspaceContext] = resolve_account_context,
        context_activator: Callable[[AccountWorkspaceContext], AccountWorkspaceContext] | None = None,
    ):
        self.workspace_repository = workspace_repository
        self.context_resolver = context_resolver
        self.context_activator = context_activator or activate_account_context
        self._authorized_memberships: dict[str, tuple[WorkspaceMembership, ...]] = {}

    def activate(self, identity: AuthenticatedIdentity) -> ActivationResult:
        memberships = self.list_authorized_workspaces(identity)
        workspace = select_single_workspace(list(memberships))
        return self.activate_workspace(identity, workspace)

    def list_authorized_workspaces(
        self,
        identity: AuthenticatedIdentity,
    ) -> tuple[WorkspaceMembership, ...]:
        memberships = self.workspace_repository.list_memberships(identity)
        ordered = tuple(sorted(memberships, key=_membership_sort_key))
        self._authorized_memberships[identity.user_id] = ordered
        return ordered

    def activate_workspace(
        self,
        identity: AuthenticatedIdentity,
        selected_membership: WorkspaceMembership,
    ) -> ActivationResult:
        try:
            workspace = self._authorized_workspace(identity, selected_membership)
            context = self.context_resolver(identity.user_id, workspace.workspace_id)
            activated = self.context_activator(context)
            return ActivationResult(identity=identity, workspace=workspace, context=activated)
        except WorkspaceLookupError:
            self.rollback_partial_activation()
            raise
        except Exception as error:
            self.rollback_partial_activation()
            raise ActivationError(
                "تعذر فتح مساحة العمل بأمان. لم يتم فتح أي بيانات تشغيلية."
            ) from error

    def _authorized_workspace(
        self,
        identity: AuthenticatedIdentity,
        selected_membership: WorkspaceMembership,
    ) -> WorkspaceMembership:
        if not isinstance(selected_membership, WorkspaceMembership):
            raise WorkspaceLookupError(_UNAUTHORIZED_WORKSPACE_MESSAGE)
        authorized = self._authorized_memberships.get(identity.user_id, ())
        for membership in authorized:
            if selected_membership is membership:
                return membership
        raise WorkspaceLookupError(_UNAUTHORIZED_WORKSPACE_MESSAGE)

    @staticmethod
    def rollback_partial_activation() -> None:
        try:
            deactivate_account_context()
        except Exception:
            pass
        try:
            config.reset_user_root()
        except Exception:
            pass

    @staticmethod
    def controlled_sign_out(auth) -> None:
        set_background_operations_enabled(False)
        try:
            auth.sign_out()
        finally:
            deactivate_account_context()


def _membership_sort_key(membership: WorkspaceMembership) -> tuple[str, int, str]:
    normalized_name = " ".join(membership.display_name.split()).casefold()
    return (
        normalized_name,
        _ROLE_ORDER.get(membership.role, len(_ROLE_ORDER)),
        membership.workspace_id,
    )

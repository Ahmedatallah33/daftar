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

    def activate(self, identity: AuthenticatedIdentity) -> ActivationResult:
        try:
            memberships = self.workspace_repository.list_memberships(identity)
            workspace = select_single_workspace(memberships)
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

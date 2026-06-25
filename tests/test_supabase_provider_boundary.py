"""Tests for the minimal Supabase provider boundary (app.cloud).

These tests prove the seam is offline, secret-safe, business-data-free, and
correctly bridges to the provider-neutral identity foundation.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from app.cloud.supabase_provider import (
    DEVELOPMENT_PROJECT_REF,
    SUPABASE_REFRESH_CREDENTIAL_NAME,
    ProviderConfigError,
    SupabaseAuthMethod,
    SupabaseCredentialBridge,
    load_development_config,
    account_state_for_event,
)
from app.identity.credential_store import (
    TARGET_NAMESPACE,
    InMemoryCredentialStore,
    credential_target,
)
from app.identity.models import AccountState


ROOT = Path(__file__).resolve().parent.parent
CLOUD_ROOT = ROOT / "app" / "cloud"


def test_development_config_defaults_to_dev_project_and_builds_url():
    config = load_development_config(
        {"SUPABASE_PUBLISHABLE_KEY": "sb_publishable_dev_dummy"}
    )

    assert config.project_ref == DEVELOPMENT_PROJECT_REF
    assert config.api_url == f"https://{DEVELOPMENT_PROJECT_REF}.supabase.co"
    assert config.is_development is True
    assert config.auth_method is SupabaseAuthMethod.EMAIL_OTP


def test_development_config_rejects_non_development_project():
    with pytest.raises(ProviderConfigError):
        load_development_config(
            {
                "SUPABASE_PROJECT_REF": "some-other-project",
                "SUPABASE_PUBLISHABLE_KEY": "x",
            }
        )


def test_development_config_requires_publishable_key_from_environment():
    with pytest.raises(ProviderConfigError):
        load_development_config({})


def test_only_email_otp_is_prepared():
    assert [method.value for method in SupabaseAuthMethod] == ["email_otp"]
    assert not any("google" in method.value.lower() for method in SupabaseAuthMethod)


def test_refresh_secret_is_namespaced_under_identity():
    target = credential_target(SUPABASE_REFRESH_CREDENTIAL_NAME)
    assert target == f"{TARGET_NAMESPACE}Provider/Supabase/refresh"
    assert target.startswith("Daftar/Identity/")


def test_credential_bridge_round_trips_only_through_the_secure_store():
    store = InMemoryCredentialStore()
    bridge = SupabaseCredentialBridge(credential_store=store)

    bridge.store_refresh_secret("future-supabase-refresh-token")
    assert bridge.load_refresh_secret() == "future-supabase-refresh-token"
    # The secret lives only under the namespaced credential target.
    assert store.read_credential(SUPABASE_REFRESH_CREDENTIAL_NAME) == (
        "future-supabase-refresh-token"
    )

    bridge.clear_refresh_secret()
    from app.identity.errors import CredentialNotFoundError

    with pytest.raises(CredentialNotFoundError):
        bridge.load_refresh_secret()


def test_event_to_state_mapping_is_complete_and_neutral():
    assert account_state_for_event("otp_requested") is AccountState.SIGN_IN_PENDING
    assert account_state_for_event("session_established") is AccountState.SIGNED_IN_ONLINE
    assert (
        account_state_for_event("offline_session_valid")
        is AccountState.SIGNED_IN_OFFLINE_VALID
    )
    assert account_state_for_event("signed_out") is AccountState.SIGNED_OUT
    with pytest.raises(ProviderConfigError):
        account_state_for_event("not_a_real_event")


def test_boundary_imports_no_network_business_orm_or_qt():
    forbidden_roots = {
        "app.db",
        "app.services",
        "app.ui",
        "PySide6",
        "requests",
        "http",
        "urllib",
        "socket",
        "sqlite3",
        "sqlalchemy",
    }
    for path in CLOUD_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for imported in imports
            for forbidden in forbidden_roots
        ), path


def _code_identifiers(path: Path) -> set[str]:
    """Return the set of identifier tokens in a file (no comments/strings).

    Dropping string/docstring literals keeps reassuring prose (which mentions
    things like "SQLite" or "service_role" only to say they are NOT used) from
    causing false positives, while real code identifiers are still inspected.
    """

    names: set[str] = set()
    readline = io.StringIO(path.read_text(encoding="utf-8")).readline
    for token in tokenize.generate_tokens(readline):
        if token.type == tokenize.NAME:
            names.add(token.string.lower())
    return names


def test_boundary_has_no_secret_persistence_or_business_terms():
    identifiers: set[str] = set()
    for path in CLOUD_ROOT.glob("*.py"):
        identifiers |= _code_identifiers(path)

    # No alternate secret-persistence backends are referenced in code.
    forbidden_persistence = {
        "sqlite3",
        "settings_service",
        "metadata_store",
        "write_text",
        "open",
        "service_role",
        "db_password",
    }
    assert not (identifiers & forbidden_persistence), identifiers & forbidden_persistence

    # No business entities may be referenced by the account/auth boundary.
    forbidden_business = {
        "student",
        "students",
        "guardian",
        "session_service",
        "attendance",
        "invoice",
        "payment",
        "billing",
        "excel",
        "pdf",
    }
    assert not (identifiers & forbidden_business), identifiers & forbidden_business


def test_constructing_boundary_objects_does_no_io(tmp_path, monkeypatch):
    # Importing and constructing must not require network or business files.
    # Use a dedicated empty dir (the autouse temp_db fixture writes to tmp_path).
    work = tmp_path / "boundary_work"
    work.mkdir()
    monkeypatch.chdir(work)
    bridge = SupabaseCredentialBridge(credential_store=InMemoryCredentialStore())
    config = load_development_config({"SUPABASE_PUBLISHABLE_KEY": "x"})

    assert config.is_development
    assert callable(bridge.store_refresh_secret)
    # No files were created in the working directory by construction.
    assert list(work.iterdir()) == []

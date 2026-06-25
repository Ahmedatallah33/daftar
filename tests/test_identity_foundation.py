from __future__ import annotations

import ast
import json
import re
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from uuid import UUID

import pytest

from app.identity.controller import IdentityController
from app.identity.credential_store import (
    TARGET_NAMESPACE,
    InMemoryCredentialStore,
    WindowsCredentialManagerStore,
    assert_no_plaintext_fallback,
    credential_target,
)
from app.identity.diagnostics import IdentityDiagnosticEvent, IdentityDiagnostics
from app.identity.errors import (
    CredentialDeleteError,
    CredentialNotFoundError,
    CredentialReadError,
    CredentialStoreError,
    CredentialWriteError,
    IdentityStateTransitionError,
    MalformedCredentialError,
    MetadataStoreError,
)
from app.identity.diagnostics import (
    DiagnosticErrorCategory,
    DiagnosticEventCategory,
)
from app.identity.installation_identity import load_installation_identity
from app.identity.metadata_store import (
    ALLOWED_METADATA_KEYS,
    IdentityMetadataStore,
    parse_metadata,
)
from app.identity.models import (
    ALLOWED_TRANSITIONS,
    AccountSnapshot,
    AccountState,
    AccountStateMachine,
)


ROOT = Path(__file__).resolve().parent.parent
IDENTITY_ROOT = ROOT / "app" / "identity"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in ALLOWED_TRANSITIONS.items()
        for target in targets
    ],
)
def test_every_allowed_account_transition(source, target):
    machine = AccountStateMachine(source)

    snapshot = machine.transition(target)

    assert snapshot.state is target
    assert snapshot.previous_state is source
    assert machine.snapshot is snapshot


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source in AccountState
        for target in AccountState
        if target not in ALLOWED_TRANSITIONS[source]
    ],
)
def test_every_prohibited_account_transition_fails_safely(source, target):
    machine = AccountStateMachine(source)
    before = machine.snapshot

    with pytest.raises(IdentityStateTransitionError):
        machine.transition(target)

    assert machine.snapshot == before


def test_snapshot_is_immutable_and_has_no_secret_fields():
    snapshot = AccountStateMachine().snapshot

    with pytest.raises(FrozenInstanceError):
        snapshot.state = AccountState.SIGNED_IN_ONLINE  # type: ignore[misc]

    names = {field.name.lower() for field in fields(AccountSnapshot)}
    forbidden = {"secret", "token", "password", "credential", "email", "subject"}
    assert names.isdisjoint(forbidden)


def test_fake_credential_store_round_trip_missing_malformed_and_failures():
    store = InMemoryCredentialStore()
    store.write_credential("refresh", "future-refresh-secret")

    assert store.read_credential("refresh") == "future-refresh-secret"

    store.delete_credential("refresh")
    with pytest.raises(CredentialNotFoundError):
        store.read_credential("refresh")

    store.inject_raw_entry("refresh", b"not-json")
    with pytest.raises(MalformedCredentialError):
        store.read_credential("refresh")

    store.fail_writes = True
    with pytest.raises(CredentialWriteError):
        store.write_credential("refresh", "x")
    store.fail_writes = False
    store.fail_reads = True
    with pytest.raises(CredentialReadError):
        store.read_credential("refresh")
    store.fail_reads = False
    store.fail_deletes = True
    with pytest.raises(CredentialDeleteError):
        store.delete_credential("refresh")


def test_credential_targets_are_namespaced_and_plaintext_fallback_is_absent():
    assert credential_target("device_secret") == f"{TARGET_NAMESPACE}device_secret"
    audit_target = f"{TARGET_NAMESPACE}Audit/example"
    assert credential_target(audit_target) == audit_target
    assert credential_target("Audit/example") == audit_target
    with pytest.raises(MalformedCredentialError):
        credential_target("../escape")
    with pytest.raises(MalformedCredentialError):
        credential_target("Daftar/Other/example")
    with pytest.raises(CredentialStoreError):
        assert_no_plaintext_fallback()

    source = (IDENTITY_ROOT / "credential_store.py").read_text(encoding="utf-8")
    forbidden_fallbacks = ["sqlite", "settings_service", "os.environ", ".open(", "write_text"]
    assert all(item not in source for item in forbidden_fallbacks)


def test_windows_credential_adapter_imports_without_real_credential_use():
    adapter = WindowsCredentialManagerStore()

    assert callable(adapter.write_credential)
    assert callable(adapter.read_credential)
    assert callable(adapter.delete_credential)
    assert credential_target("refresh").startswith("Daftar/Identity/")


def test_windows_credential_manager_real_audit_target_round_trip():
    import sys
    import uuid

    if not sys.platform.startswith("win"):
        pytest.skip("Windows Credential Manager is only available on Windows")

    store = WindowsCredentialManagerStore()
    target = f"{TARGET_NAMESPACE}Audit/{uuid.uuid4().hex}"
    secret_one = f"synthetic-audit-secret-{uuid.uuid4().hex}"
    secret_two = f"synthetic-audit-secret-overwrite-{uuid.uuid4().hex}"
    try:
        store.write_credential(target, secret_one)
        assert store.read_credential(target) == secret_one
        store.write_credential(target, secret_two)
        assert store.read_credential(target) == secret_two
    finally:
        try:
            store.delete_credential(target)
        except CredentialNotFoundError:
            pass
    with pytest.raises(CredentialNotFoundError):
        store.read_credential(target)


def test_installation_identity_is_stable_uuidv4(tmp_path):
    store = IdentityMetadataStore(tmp_path / "identity" / "metadata.json")

    first = load_installation_identity(store)
    second = load_installation_identity(store)

    assert first == second
    assert first.installation_uuid.version == 4


def test_corrupt_or_missing_metadata_recovers_without_touching_business_files(tmp_path):
    store = IdentityMetadataStore(tmp_path / "identity" / "metadata.json")
    sqlite_file = tmp_path / "data" / "teacher.db"
    export_file = tmp_path / "exports" / "invoices" / "invoice.pdf"
    backup_file = tmp_path / "backups" / "backup.db"
    restore_marker = tmp_path / "restore" / "pending_restore.json"
    for file_path in (sqlite_file, export_file, backup_file, restore_marker):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"sentinel:{file_path.name}", encoding="utf-8")

    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{bad json", encoding="utf-8")
    recovered = store.load_or_create()

    assert UUID(recovered.installation_uuid).version == 4
    for file_path in (sqlite_file, export_file, backup_file, restore_marker):
        assert file_path.read_text(encoding="utf-8") == f"sentinel:{file_path.name}"


def test_metadata_is_versioned_limited_and_written_atomically(tmp_path, monkeypatch):
    store = IdentityMetadataStore(tmp_path / "identity" / "metadata.json")
    metadata = store.load_or_create()
    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert set(payload) <= ALLOWED_METADATA_KEYS
    assert payload["metadata_version"] == 1

    with pytest.raises(MetadataStoreError):
        parse_metadata({**payload, "email": "teacher@example.com"})

    def fail_replace(_temporary, _destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("app.identity.metadata_store.os.replace", fail_replace)
    with pytest.raises(MetadataStoreError):
        store.save(metadata)

    assert list(store.path.parent.glob("*.tmp")) == []


def test_installation_identity_uses_no_hardware_identifiers():
    source = (IDENTITY_ROOT / "installation_identity.py").read_text(encoding="utf-8")
    source += (IDENTITY_ROOT / "metadata_store.py").read_text(encoding="utf-8")
    forbidden = [
        "MachineGuid",
        "getnode",
        "hostname",
        "cpu",
        "serial",
        "mac",
        "disk",
    ]
    assert all(item.lower() not in source.lower() for item in forbidden)


def test_identity_diagnostics_reject_sensitive_strings_and_write_safe_events(tmp_path):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")

    assert not diagnostics.record(
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            error_category="teacher@example.com",
        )
    )
    assert not diagnostics.log_path.exists()

    assert diagnostics.record(
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            transition_from=AccountState.SIGNED_OUT,
            transition_to=AccountState.SIGN_IN_PENDING,
            app_version="Sprint 3A-1",
            elapsed_ms=3,
        )
    )
    written = diagnostics.log_path.read_text(encoding="utf-8")
    assert "SIGNED_OUT" in written
    assert "teacher@example.com" not in written
    assert "token" not in written.lower()


def test_identity_diagnostics_reject_public_correlation_id_keyword(tmp_path):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")
    caller_supplied = "a" * 32

    with pytest.raises(TypeError):
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            transition_from=AccountState.SIGNED_OUT,
            transition_to=AccountState.SIGN_IN_PENDING,
            correlation_id=caller_supplied,
        )

    assert diagnostics.record(
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            transition_from=AccountState.SIGNED_OUT,
            transition_to=AccountState.SIGN_IN_PENDING,
        )
    )
    written = diagnostics.log_path.read_text(encoding="utf-8")
    assert caller_supplied not in written


def test_identity_diagnostics_ignore_object_style_correlation_injection(tmp_path):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")
    caller_supplied = "b" * 32
    event = IdentityDiagnosticEvent(
        event_category=DiagnosticEventCategory.STATE_TRANSITION,
        transition_from=AccountState.SIGNED_OUT,
        transition_to=AccountState.SIGN_IN_PENDING,
    )

    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(event, "correlation_id", caller_supplied)

    assert diagnostics.record(event)
    written = diagnostics.log_path.read_text(encoding="utf-8")
    assert caller_supplied not in written


def test_identity_diagnostics_generate_independent_correlation_ids(tmp_path):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")
    caller_supplied = "c" * 32
    for _ in range(2):
        assert diagnostics.record(
            IdentityDiagnosticEvent(
                event_category=DiagnosticEventCategory.STATE_TRANSITION,
                transition_from=AccountState.SIGNED_OUT,
                transition_to=AccountState.SIGN_IN_PENDING,
            )
        )

    lines = [
        json.loads(line)
        for line in diagnostics.log_path.read_text(encoding="utf-8").splitlines()
    ]
    correlation_ids = [line["correlation_id"] for line in lines]
    assert len(correlation_ids) == 2
    assert len(set(correlation_ids)) == 2
    assert all(re.fullmatch(r"[a-f0-9]{32}", item) for item in correlation_ids)
    assert caller_supplied not in correlation_ids


@pytest.mark.parametrize(
    "sensitive",
    [
        "eyJhbGciOi.fake.jwt",
        "Bearer abc",
        "bearer ABC123",
        "01012345678",
        "+20 101 234 5678",
        "access_token=abc123",
        "refresh-token abc123",
        "id_token abc123",
        "password=abc",
        "authorization_code=abc",
        "nonce=abc",
        "state=abc",
        "verifier=abc",
        "teacher@example.com",
        "student Ali",
        "invoice 500",
        "invoice.pdf",
        "https://example.test/callback?code=abc",
    ],
)
def test_identity_diagnostics_never_persist_sensitive_inputs(tmp_path, sensitive):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")

    assert not diagnostics.record(
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            error_category=sensitive,
        )
    )
    if diagnostics.log_path.exists():
        written = diagnostics.log_path.read_text(encoding="utf-8")
        assert sensitive not in written
        for fragment in ("eyJ", "Bearer", "bearer", "010123", "101234", "token"):
            assert fragment not in written


def test_identity_diagnostics_allow_only_fixed_error_categories(tmp_path):
    diagnostics = IdentityDiagnostics(tmp_path / "logs")

    assert diagnostics.record(
        IdentityDiagnosticEvent(
            event_category=DiagnosticEventCategory.STATE_TRANSITION,
            transition_from=AccountState.SIGNED_OUT,
            transition_to=AccountState.SIGN_IN_PENDING,
            error_category=DiagnosticErrorCategory.REDACTED_SENSITIVE_INPUT,
        )
    )
    written = diagnostics.log_path.read_text(encoding="utf-8")
    assert DiagnosticErrorCategory.REDACTED_SENSITIVE_INPUT.value in written
    assert "sensitive" in written
    assert "eyJ" not in written


def test_controller_is_lazy_pure_python_and_does_not_touch_business_database(tmp_path):
    metadata_store = IdentityMetadataStore(tmp_path / "identity" / "metadata.json")
    diagnostics = IdentityDiagnostics(tmp_path / "logs")
    controller = IdentityController(
        credential_store=InMemoryCredentialStore(),
        metadata_store=metadata_store,
        diagnostics=diagnostics,
    )

    snapshot = controller.transition(AccountState.SIGN_IN_PENDING)

    assert snapshot.state is AccountState.SIGN_IN_PENDING
    assert metadata_store.path.exists()
    assert (tmp_path / "logs" / "identity.log").exists()
    assert not (tmp_path / "data").exists()


def test_identity_package_does_not_import_business_services_orm_or_qt():
    forbidden_roots = {
        "app.db",
        "app.services",
        "app.ui",
        "PySide6",
        "requests",
        "http",
        "urllib",
    }
    for path in IDENTITY_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
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


def test_existing_settings_service_has_no_identity_secret_logic():
    source = (ROOT / "app" / "services" / "settings_service.py").read_text(encoding="utf-8")
    forbidden = ["token", "credential", "authorization_code", "pkce", "nonce"]
    assert all(item not in source.lower() for item in forbidden)


def test_no_token_like_values_are_written_to_metadata_or_logs(tmp_path):
    metadata_store = IdentityMetadataStore(tmp_path / "identity" / "metadata.json")
    diagnostics = IdentityDiagnostics(tmp_path / "logs")
    credentials = InMemoryCredentialStore()
    credentials.write_credential("refresh", "token-like-secret-value")
    controller = IdentityController(
        credential_store=credentials,
        metadata_store=metadata_store,
        diagnostics=diagnostics,
    )

    controller.transition(AccountState.SIGN_IN_PENDING)

    combined = metadata_store.path.read_text(encoding="utf-8")
    combined += diagnostics.log_path.read_text(encoding="utf-8")
    assert "token-like-secret-value" not in combined
    assert "refresh" not in combined

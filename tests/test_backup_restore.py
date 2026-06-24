import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.db import engine as engine_mod
from app.db.models import Invoice
from app.db.safety import integrity_check
from app.services import backup_restore_service as portability
from app.services import billing_service as billing
from app.services.backup_restore_service import (
    BackupArchiveError,
    RestoreError,
    StorageLayout,
    apply_pending_restore,
    create_full_backup,
    stage_restore,
    validate_backup_archive,
)
from app.services.group_service import create_group, list_groups
from app.services.session_service import add_session, add_video
from app.services.settings_service import get_setting, set_setting
from app.services.student_service import create_student, list_students


def _layout(root: Path) -> StorageLayout:
    return StorageLayout(
        user_root=root,
        database_path=root / "data" / "teacher.db",
        backups_dir=root / "backups",
        exports_dir=root / "exports",
        invoices_dir=root / "exports" / "invoices",
        restore_dir=root / "restore",
    )


def _activate(layout: StorageLayout) -> None:
    layout.ensure_directories()
    engine_mod.configure_engine(f"sqlite:///{layout.database_path}")
    engine_mod.init_db()


def _write_valid_xlsx(path: Path, value: str = "Teacher Hub") -> bytes:
    workbook = Workbook()
    workbook.active["A1"] = value
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def _populate_teacher(layout: StorageLayout) -> dict:
    _activate(layout)
    paid_student = create_student(
        "طالبة النسخة", phone="01000000000", price_per_session=75, sessions_per_cycle=1
    )
    add_session(paid_student.id)
    add_video(paid_student.id, "شرح")
    paid_pdf = layout.invoices_dir / "فاتورة طالبة النسخة.pdf"
    paid_pdf.write_bytes(b"%PDF-1.4 paid invoice\n%%EOF")
    paid_invoice = billing.record_invoice(
        paid_student.id, 1, 1, 75, str(paid_pdf), notes="مدفوعة"
    )
    billing.mark_invoice_paid(paid_invoice.id, True)

    unpaid_student = create_student(
        "طالب غير مدفوع", phone="01111111111", price_per_session=60, sessions_per_cycle=1
    )
    add_session(unpaid_student.id)
    unpaid_pdf = layout.invoices_dir / "فاتورة غير مدفوعة.pdf"
    unpaid_pdf.write_bytes(b"%PDF-1.4 unpaid invoice\n%%EOF")
    unpaid_invoice = billing.record_invoice(
        unpaid_student.id, 1, 0, 60, str(unpaid_pdf), notes="غير مدفوعة"
    )

    spreadsheet = layout.exports_dir / "students_export.xlsx"
    spreadsheet_bytes = _write_valid_xlsx(spreadsheet)
    set_setting("theme", "dark")
    set_setting("migration_probe", {"ok": True})
    create_group("مجموعة المتابعة", "https://chat.whatsapp.com/AbCd1234")
    engine_mod.SessionLocal.remove()
    return {
        "paid_invoice": paid_invoice.id,
        "unpaid_invoice": unpaid_invoice.id,
        "pdf_bytes": {paid_pdf.read_bytes(), unpaid_pdf.read_bytes()},
        "xlsx_bytes": spreadsheet_bytes,
    }


def _manifest(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))


def _rewrite_archive(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source) as archive:
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    mutate(members)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _add_manifest_member(members: dict[str, bytes], name: str, content: bytes) -> None:
    manifest = json.loads(members["manifest.json"])
    manifest["files"].append(
        {
            "path": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
    )
    members[name] = content
    members["manifest.json"] = json.dumps(manifest).encode()


def _minimal_archive(tmp_path: Path) -> tuple[StorageLayout, Path]:
    layout = _layout(tmp_path / "source")
    _activate(layout)
    engine_mod.SessionLocal.remove()
    return layout, create_full_backup(tmp_path / "portable", layout=layout).archive_path


def test_full_archive_contains_verified_database_exports_and_private_manifest(
    tmp_path,
):
    layout = _layout(tmp_path / "source")
    expected = _populate_teacher(layout)

    info = create_full_backup(tmp_path / "portable", layout=layout)

    assert info.archive_path.name.endswith(".teacherhub.zip")
    assert integrity_check(layout.database_path) == (True, "ok")
    with zipfile.ZipFile(info.archive_path) as archive:
        names = set(archive.namelist())
        manifest_text = archive.read("manifest.json").decode("utf-8")
    assert "data/teacher.db" in names
    assert len([name for name in names if name.endswith(".pdf")]) == 2
    assert len([name for name in names if name.endswith(".xlsx")]) == 1
    assert "طالبة النسخة" not in manifest_text
    assert "01000000000" not in manifest_text
    assert "فاتورة طالبة النسخة" not in manifest_text
    manifest = json.loads(manifest_text)
    assert manifest["database_integrity"] == "ok"
    assert manifest["schema_version"] == engine_mod.SCHEMA_VERSION
    assert len(manifest["files"]) == 4
    assert validate_backup_archive(info.archive_path, layout=layout).export_count == 3
    assert expected["xlsx_bytes"]


def test_database_snapshot_uses_online_backup_and_passes_integrity(
    tmp_path, monkeypatch
):
    layout = _layout(tmp_path / "source")
    _populate_teacher(layout)
    calls = []
    real_backup = portability.online_backup

    def tracked_backup(source, destination, prefix="teacher_backup"):
        calls.append((Path(source), Path(destination), prefix))
        return real_backup(source, destination, prefix)

    monkeypatch.setattr(portability, "online_backup", tracked_backup)
    info = create_full_backup(tmp_path / "portable", layout=layout)

    assert calls and calls[0][0] == layout.database_path
    with zipfile.ZipFile(info.archive_path) as archive:
        extracted = tmp_path / "snapshot.db"
        extracted.write_bytes(archive.read("data/teacher.db"))
    assert integrity_check(extracted) == (True, "ok")


def test_rapid_full_backups_have_distinct_names(tmp_path):
    layout = _layout(tmp_path / "source")
    _populate_teacher(layout)
    first = create_full_backup(tmp_path / "portable", layout=layout)
    second = create_full_backup(tmp_path / "portable", layout=layout)
    assert first.archive_path != second.archive_path
    assert first.archive_path.exists() and second.archive_path.exists()


def test_failed_archive_validation_publishes_no_partial_backup(tmp_path, monkeypatch):
    layout = _layout(tmp_path / "source")
    _populate_teacher(layout)
    destination = tmp_path / "portable"

    def fail_validation(*_args, **_kwargs):
        raise BackupArchiveError("simulated final archive validation failure")

    monkeypatch.setattr(portability, "validate_backup_archive", fail_validation)
    with pytest.raises(BackupArchiveError, match="simulated"):
        create_full_backup(destination, layout=layout)
    assert not list(destination.iterdir())


def test_rejects_single_entry_compression_ratio_above_limit(tmp_path):
    layout, valid = _minimal_archive(tmp_path)
    tampered = tmp_path / "single-ratio.teacherhub.zip"

    def add_high_ratio_pdf(members):
        _add_manifest_member(
            members,
            "exports/invoices/invoice_000001.pdf",
            b"%PDF-1.4\n" + b"0" * (8 * 1024 * 1024),
        )

    _rewrite_archive(valid, tampered, add_high_ratio_pdf)
    with zipfile.ZipFile(tampered) as archive:
        info = archive.getinfo("exports/invoices/invoice_000001.pdf")
        assert info.file_size / info.compress_size > 1_000
    with pytest.raises(BackupArchiveError):
        validate_backup_archive(tampered, layout=layout)


def test_rejects_aggregate_compression_ratio_above_limit(
    tmp_path, monkeypatch
):
    layout, archive = _minimal_archive(tmp_path)
    monkeypatch.setattr(portability, "MAX_ENTRY_COMPRESSION_RATIO", 10_000)
    monkeypatch.setattr(portability, "MAX_TOTAL_COMPRESSION_RATIO", 1)
    with pytest.raises(BackupArchiveError):
        validate_backup_archive(archive, layout=layout)


def test_rejects_nonempty_entry_with_zero_compressed_size(tmp_path):
    layout, archive = _minimal_archive(tmp_path)
    tampered = tmp_path / "zero-compressed.teacherhub.zip"
    content = bytearray(archive.read_bytes())
    signature = b"PK\x01\x02"
    offset = 0
    patched = False
    while True:
        offset = content.find(signature, offset)
        if offset < 0:
            break
        name_length = int.from_bytes(content[offset + 28 : offset + 30], "little")
        name_start = offset + 46
        name = bytes(content[name_start : name_start + name_length])
        if name == b"data/teacher.db":
            assert int.from_bytes(content[offset + 24 : offset + 28], "little") > 0
            content[offset + 20 : offset + 24] = b"\0\0\0\0"
            patched = True
            break
        offset = name_start + name_length
    assert patched
    tampered.write_bytes(content)
    with pytest.raises(BackupArchiveError):
        validate_backup_archive(tampered, layout=layout)


def test_rejects_excessive_physical_archive_size(tmp_path, monkeypatch):
    layout, archive = _minimal_archive(tmp_path)
    monkeypatch.setattr(
        portability,
        "_archive_physical_size",
        lambda _path: portability.MAX_ARCHIVE_COMPRESSED_BYTES + 1,
    )
    with pytest.raises(BackupArchiveError):
        validate_backup_archive(archive, layout=layout)


def test_archive_just_below_all_limits_remains_valid(tmp_path, monkeypatch):
    layout, archive_path = _minimal_archive(tmp_path)
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        manifest_size = archive.getinfo("manifest.json").file_size
    total_uncompressed = sum(info.file_size for info in infos)
    total_compressed = sum(info.compress_size for info in infos)
    maximum_ratio = max(info.file_size / info.compress_size for info in infos)
    total_ratio = total_uncompressed / total_compressed
    monkeypatch.setattr(portability, "MAX_ARCHIVE_FILES", len(infos) + 1)
    monkeypatch.setattr(
        portability,
        "MAX_ARCHIVE_UNCOMPRESSED_BYTES",
        total_uncompressed + 1,
    )
    monkeypatch.setattr(
        portability,
        "MAX_ARCHIVE_COMPRESSED_BYTES",
        archive_path.stat().st_size + 1,
    )
    monkeypatch.setattr(portability, "MAX_MANIFEST_BYTES", manifest_size + 1)
    monkeypatch.setattr(
        portability,
        "MAX_ENTRY_COMPRESSION_RATIO",
        maximum_ratio + 0.001,
    )
    monkeypatch.setattr(
        portability,
        "MAX_TOTAL_COMPRESSION_RATIO",
        total_ratio + 0.001,
    )
    assert validate_backup_archive(archive_path, layout=layout).file_count == 1


def test_export_collection_uses_canonical_paths_and_valid_file_types(tmp_path):
    layout, _archive = _minimal_archive(tmp_path)
    valid_pdf = layout.invoices_dir / "legacy invoice name.pdf"
    valid_pdf.write_bytes(b"%PDF-1.4\nvalid\n%%EOF")
    valid_xlsx = layout.exports_dir / "legacy students name.xlsx"
    valid_xlsx_bytes = _write_valid_xlsx(valid_xlsx)
    (layout.invoices_dir / "renamed-text.pdf").write_text("not a pdf")
    (layout.exports_dir / "renamed-text.xlsx").write_text("not an xlsx")
    outside = layout.exports_dir / "other"
    outside.mkdir()
    (outside / "outside.pdf").write_bytes(b"%PDF-1.4\noutside")
    _write_valid_xlsx(outside / "outside.xlsx")
    (layout.invoices_dir / ".cached.pdf").write_bytes(b"%PDF-1.4\nhidden")
    _write_valid_xlsx(layout.exports_dir / "~$temporary.xlsx")

    info = create_full_backup(tmp_path / "canonical", layout=layout)
    with zipfile.ZipFile(info.archive_path) as archive:
        pdfs = [name for name in archive.namelist() if name.endswith(".pdf")]
        spreadsheets = [
            name for name in archive.namelist() if name.endswith(".xlsx")
        ]
        assert len(pdfs) == 1
        assert len(spreadsheets) == 1
        assert archive.read(pdfs[0]) == valid_pdf.read_bytes()
        assert archive.read(spreadsheets[0]) == valid_xlsx_bytes


def test_symlinked_exports_are_excluded(tmp_path, monkeypatch):
    layout, _archive = _minimal_archive(tmp_path)
    target = tmp_path / "external.pdf"
    target.write_bytes(b"%PDF-1.4\nexternal")
    link = layout.invoices_dir / "linked.pdf"
    try:
        link.symlink_to(target)
    except OSError:
        link.write_bytes(target.read_bytes())
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link or real_is_symlink(path),
        )
    info = create_full_backup(tmp_path / "symlink", layout=layout)
    with zipfile.ZipFile(info.archive_path) as archive:
        assert not any(name.endswith(".pdf") for name in archive.namelist())


def _bad_manifest(members):
    members["manifest.json"] = b"{}"


def _bad_hash(members):
    manifest = json.loads(members["manifest.json"])
    manifest["files"][0]["sha256"] = "0" * 64
    members["manifest.json"] = json.dumps(manifest).encode()


def _missing_database(members):
    members.pop("data/teacher.db")


def _corrupt_database(members):
    corrupted = b"not a sqlite database"
    manifest = json.loads(members["manifest.json"])
    digest = hashlib.sha256(corrupted).hexdigest()
    for record in manifest["files"]:
        if record["path"] == "data/teacher.db":
            record["sha256"] = digest
            record["size"] = len(corrupted)
    manifest["database_sha256"] = digest
    members["data/teacher.db"] = corrupted
    members["manifest.json"] = json.dumps(manifest).encode()


def _incomplete_database(members):
    descriptor, filename = tempfile.mkstemp(suffix=".db")
    os.close(descriptor)
    database = Path(filename)
    try:
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO schema_meta VALUES ('schema_version', '2')")
            connection.commit()
        finally:
            connection.close()
        content = database.read_bytes()
    finally:
        database.unlink(missing_ok=True)
    manifest = json.loads(members["manifest.json"])
    digest = hashlib.sha256(content).hexdigest()
    for record in manifest["files"]:
        if record["path"] == "data/teacher.db":
            record["sha256"] = digest
            record["size"] = len(content)
    manifest["database_sha256"] = digest
    members["data/teacher.db"] = content
    members["manifest.json"] = json.dumps(manifest).encode()


def _path_traversal(members):
    members["../outside.txt"] = b"escape"


def _unexpected_source(members):
    members["app/main.py"] = b"print('unexpected')"


def _replace_export_with_invalid_content(members, suffix, content):
    name = next(name for name in members if name.endswith(suffix))
    manifest = json.loads(members["manifest.json"])
    for record in manifest["files"]:
        if record["path"] == name:
            record["sha256"] = hashlib.sha256(content).hexdigest()
            record["size"] = len(content)
            break
    members[name] = content
    members["manifest.json"] = json.dumps(manifest).encode()


def _malformed_pdf(members):
    _replace_export_with_invalid_content(members, ".pdf", b"plain text")


def _malformed_xlsx(members):
    _replace_export_with_invalid_content(members, ".xlsx", b"plain text")


@pytest.mark.parametrize(
    "mutation",
    [
        _bad_manifest,
        _bad_hash,
        _missing_database,
        _corrupt_database,
        _incomplete_database,
        _path_traversal,
        _unexpected_source,
        _malformed_pdf,
        _malformed_xlsx,
    ],
    ids=[
        "bad-manifest",
        "invalid-hash",
        "missing-database",
        "corrupt-database",
        "incomplete-database",
        "zip-path-traversal",
        "unexpected-source",
        "malformed-pdf",
        "malformed-xlsx",
    ],
)
def test_archive_validation_rejects_unsafe_or_malformed_content(
    tmp_path, mutation
):
    layout = _layout(tmp_path / "source")
    _populate_teacher(layout)
    valid = create_full_backup(tmp_path / "portable", layout=layout).archive_path
    tampered = tmp_path / f"tampered-{mutation.__name__}.teacherhub.zip"
    _rewrite_archive(valid, tampered, mutation)

    with pytest.raises(BackupArchiveError):
        validate_backup_archive(tampered, layout=layout)
    assert not (tmp_path / "outside.txt").exists()


def test_valid_restore_preserves_database_exports_and_portable_pdf_links(tmp_path):
    source = _layout(tmp_path / "old-computer")
    expected = _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path

    target = _layout(tmp_path / "new-computer")
    _activate(target)
    create_student("بيانات ستُستبدل")
    (target.invoices_dir / "old.pdf").write_bytes(b"old export")
    engine_mod.SessionLocal.remove()

    staged = stage_restore(archive, layout=target)
    assert staged.emergency_backup.exists()
    assert validate_backup_archive(staged.emergency_backup, layout=target).database_integrity == "ok"
    engine_mod.SessionLocal.remove()
    engine_mod.engine.dispose()
    restored = apply_pending_restore(layout=target)

    assert restored is not None and restored.export_count == 3
    _activate(target)
    assert {student.name for student in list_students(active_only=False)} == {
        "طالبة النسخة",
        "طالب غير مدفوع",
    }
    invoices = engine_mod.get_session().query(Invoice).order_by(Invoice.id).all()
    assert {invoice.is_paid for invoice in invoices} == {True, False}
    assert get_setting("theme") == "dark"
    assert [group.name for group in list_groups()] == ["مجموعة المتابعة"]
    restored_pdfs = list(target.invoices_dir.glob("*.pdf"))
    restored_xlsx = list(target.exports_dir.rglob("*.xlsx"))
    assert {path.read_bytes() for path in restored_pdfs} == expected["pdf_bytes"]
    assert [path.read_bytes() for path in restored_xlsx] == [expected["xlsx_bytes"]]
    assert all(Path(invoice.pdf_path).is_file() for invoice in invoices)
    assert all(Path(invoice.pdf_path).is_relative_to(target.user_root) for invoice in invoices)
    assert integrity_check(target.database_path) == (True, "ok")
    with sqlite3.connect(target.database_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert version == str(engine_mod.SCHEMA_VERSION)
    assert staged.emergency_backup.exists()
    assert not target.pending_marker.exists()


def test_restore_failure_rolls_back_database_and_exports_exactly(
    tmp_path, monkeypatch
):
    source = _layout(tmp_path / "source")
    _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path

    target = _layout(tmp_path / "target")
    _activate(target)
    create_student("البيانات الأصلية")
    old_pdf = target.invoices_dir / "old.pdf"
    old_pdf.write_bytes(b"old pdf")
    old_xlsx = target.exports_dir / "old.xlsx"
    old_xlsx.write_bytes(b"old xlsx")
    engine_mod.SessionLocal.remove()
    stage_restore(archive, layout=target)
    before_database = hashlib.sha256(target.database_path.read_bytes()).hexdigest()
    before_exports = {
        path.relative_to(target.exports_dir): path.read_bytes()
        for path in target.exports_dir.rglob("*")
        if path.is_file()
    }
    real_replace = portability.os.replace

    def fail_database_install(source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if (
            source_path.name.startswith(".teacher-restore-")
            and destination_path == target.database_path
        ):
            raise OSError("simulated database replacement failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(portability.os, "replace", fail_database_install)
    engine_mod.engine.dispose()
    with pytest.raises(RestoreError, match="تمت إعادة البيانات السابقة"):
        apply_pending_restore(layout=target)

    assert hashlib.sha256(target.database_path.read_bytes()).hexdigest() == before_database
    after_exports = {
        path.relative_to(target.exports_dir): path.read_bytes()
        for path in target.exports_dir.rglob("*")
        if path.is_file()
    }
    assert after_exports == before_exports
    assert target.pending_marker.exists()
    with sqlite3.connect(target.database_path) as connection:
        assert connection.execute("SELECT name FROM students").fetchone()[0] == "البيانات الأصلية"


def test_exports_install_failure_restores_original_exports(tmp_path, monkeypatch):
    source = _layout(tmp_path / "source")
    _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path
    target = _layout(tmp_path / "target")
    _activate(target)
    old_pdf = target.invoices_dir / "keep.pdf"
    old_pdf.write_bytes(b"keep this export")
    engine_mod.SessionLocal.remove()
    stage_restore(archive, layout=target)
    real_replace = portability.os.replace

    def fail_exports_install(source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if (
            source_path.name.startswith(".exports-restore-")
            and destination_path == target.exports_dir
        ):
            raise OSError("simulated exports installation failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(portability.os, "replace", fail_exports_install)
    engine_mod.engine.dispose()
    with pytest.raises(RestoreError, match="تمت إعادة البيانات السابقة"):
        apply_pending_restore(layout=target)
    assert old_pdf.read_bytes() == b"keep this export"
    assert target.pending_marker.exists()


def test_emergency_full_backup_exists_before_restore_marker(tmp_path, monkeypatch):
    source = _layout(tmp_path / "source")
    _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path
    target = _layout(tmp_path / "target")
    _activate(target)
    create_student("قبل الاستعادة")
    engine_mod.SessionLocal.remove()
    observations = []
    real_atomic_json = portability._atomic_json

    def observe_marker(path, payload):
        emergency = Path(payload["emergency_backup"])
        observations.append(
            emergency.exists()
            and validate_backup_archive(emergency, layout=target).database_integrity == "ok"
        )
        real_atomic_json(path, payload)

    monkeypatch.setattr(portability, "_atomic_json", observe_marker)
    stage_restore(archive, layout=target)
    assert observations == [True]


def test_emergency_backup_failure_creates_no_marker_or_live_change(tmp_path, monkeypatch):
    source = _layout(tmp_path / "source")
    _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path
    target = _layout(tmp_path / "target")
    _activate(target)
    create_student("الحالة الحالية")
    engine_mod.SessionLocal.remove()
    before = hashlib.sha256(target.database_path.read_bytes()).hexdigest()

    def fail_emergency(*_args, **_kwargs):
        raise OSError("simulated emergency backup failure")

    monkeypatch.setattr(portability, "create_full_backup", fail_emergency)
    with pytest.raises(OSError, match="simulated emergency"):
        stage_restore(archive, layout=target)
    assert hashlib.sha256(target.database_path.read_bytes()).hexdigest() == before
    assert not target.pending_marker.exists()
    assert not list(target.restore_dir.glob("incoming-*"))


def test_pending_restore_runs_before_migration_and_database_initialization(monkeypatch):
    from app import startup

    order = []
    monkeypatch.setattr(startup, "ensure_dirs", lambda: order.append("directories"))
    monkeypatch.setattr(
        startup, "apply_pending_restore", lambda: order.append("restore")
    )
    monkeypatch.setattr(
        startup, "migrate_legacy_data", lambda: order.append("legacy")
    )
    monkeypatch.setattr(startup, "init_db", lambda: order.append("database"))

    startup.initialize_application_data()

    assert order == ["directories", "restore", "legacy", "database"]


def test_clean_root_device_migration_and_source_restart_after_restore(tmp_path):
    source = _layout(tmp_path / "device-one")
    expected = _populate_teacher(source)
    archive = create_full_backup(tmp_path / "portable", layout=source).archive_path

    target = _layout(tmp_path / "device-two")
    _activate(target)
    engine_mod.SessionLocal.remove()
    stage_restore(archive, layout=target)
    engine_mod.engine.dispose()

    repository = Path(__file__).resolve().parent.parent
    legacy_empty = tmp_path / "legacy-empty"
    legacy_empty.mkdir()
    environment = os.environ.copy()
    environment["TEACHER_HUB_HOME"] = str(target.user_root)
    environment["TEACHER_HUB_LEGACY_ROOT"] = str(legacy_empty)
    environment.pop("TEACHER_DB_URL", None)
    command = [
        sys.executable,
        "-c",
        "from app.startup import initialize_application_data; "
        "initialize_application_data(); print('STARTUP_OK')",
    ]
    first = subprocess.run(
        command, cwd=repository, env=environment, capture_output=True, text=True, timeout=60
    )
    second = subprocess.run(
        command, cwd=repository, env=environment, capture_output=True, text=True, timeout=60
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout.strip() == "STARTUP_OK"
    assert second.stdout.strip() == "STARTUP_OK"
    assert not target.pending_marker.exists()

    with sqlite3.connect(target.database_path) as connection:
        students = connection.execute("SELECT count(*) FROM students").fetchone()[0]
        sessions = connection.execute("SELECT count(*) FROM sessions").fetchone()[0]
        invoices = connection.execute(
            "SELECT count(*), sum(is_paid) FROM invoices"
        ).fetchone()
        settings = connection.execute(
            "SELECT value FROM settings WHERE key='theme'"
        ).fetchone()[0]
        groups = connection.execute("SELECT count(*) FROM whatsapp_groups").fetchone()[0]
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert students == 2 and sessions == 2
    assert invoices == (2, 1)
    assert settings == '"dark"'
    assert groups == 1
    assert schema == str(engine_mod.SCHEMA_VERSION)
    assert {path.read_bytes() for path in target.invoices_dir.glob("*.pdf")} == expected["pdf_bytes"]
    assert [path.read_bytes() for path in target.exports_dir.rglob("*.xlsx")] == [
        expected["xlsx_bytes"]
    ]
    assert integrity_check(target.database_path) == (True, "ok")
    assert target.user_root.is_relative_to(tmp_path)


def test_backup_worker_callbacks_run_on_gui_thread(qapp):
    from PySide6.QtCore import QEventLoop, QObject, QThread

    from app.ui.helpers.worker import run_in_background

    owner = QObject()
    loop = QEventLoop()
    callback_threads = []
    values = []
    run_in_background(
        owner,
        lambda: "backup-ready",
        on_result=lambda value: (
            values.append(value),
            callback_threads.append(QThread.currentThread() == qapp.thread()),
        ),
        on_error=lambda error: (_ for _ in ()).throw(error),
        on_finished=loop.quit,
    )
    loop.exec()
    assert values == ["backup-ready"]
    assert callback_threads == [True]


def test_settings_dialog_contains_backup_restore_controls(qtbot):
    from PySide6.QtWidgets import QPushButton

    from app.ui.pages.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    assert "إنشاء نسخة احتياطية كاملة" in labels
    assert "استعادة نسخة احتياطية" in labels
    assert dialog.latest_backup_label.text() == "لم تُنشأ نسخة احتياطية كاملة بعد."

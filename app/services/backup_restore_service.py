from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from openpyxl import load_workbook

from app import config
from app.db.engine import SCHEMA_VERSION
from app.db.models import Base
from app.db.safety import integrity_check, online_backup


ARCHIVE_FORMAT = "teacherhub-full-backup"
ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_SUFFIX = ".teacherhub.zip"
MANIFEST_NAME = "manifest.json"
DATABASE_ARCHIVE_PATH = "data/teacher.db"
PENDING_MARKER_NAME = "pending_restore.json"

# Portable archives are checked from ZIP metadata before any member is
# extracted. These limits bound disk use and reject suspicious compression for
# a single-teacher desktop backup.
MAX_ARCHIVE_FILES = 2_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_COMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ENTRY_COMPRESSION_RATIO = 100
MAX_TOTAL_COMPRESSION_RATIO = 100

_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_TEMPORARY = 0x100
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class BackupArchiveError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageLayout:
    user_root: Path
    database_path: Path
    backups_dir: Path
    exports_dir: Path
    invoices_dir: Path
    restore_dir: Path

    @classmethod
    def live(cls) -> "StorageLayout":
        return cls(
            user_root=Path(config.USER_ROOT),
            database_path=Path(config.DB_PATH),
            backups_dir=Path(config.BACKUPS_DIR),
            exports_dir=Path(config.EXPORTS_DIR),
            invoices_dir=Path(config.INVOICES_DIR),
            restore_dir=Path(config.RESTORE_DIR),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.user_root,
            self.database_path.parent,
            self.backups_dir,
            self.exports_dir,
            self.invoices_dir,
            self.restore_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def pending_marker(self) -> Path:
        return self.restore_dir / PENDING_MARKER_NAME


@dataclass(frozen=True)
class ArchiveInfo:
    archive_path: Path
    created_at: str
    schema_version: int
    export_count: int
    file_count: int
    database_integrity: str
    app_release: str


@dataclass(frozen=True)
class StagedRestore:
    archive_info: ArchiveInfo
    emergency_backup: Path
    marker_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupArchiveError("يحتوي ملف تعريف النسخة على حقول مكررة.")
        result[key] = value
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_physical_size(path: Path) -> int:
    return path.stat().st_size


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_regular_local_file(path: Path, root: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return (
        stat.S_ISREG(metadata.st_mode)
        and not path.is_symlink()
        and not is_junction()
        and not attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        and not attributes & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_TEMPORARY)
        and not path.name.startswith((".", "~$"))
        and _inside(path, root)
    )


def _zip_metadata_within_limits(infos: list[zipfile.ZipInfo]) -> bool:
    if len(infos) > MAX_ARCHIVE_FILES:
        return False
    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        if info.file_size > 0 and info.compress_size == 0:
            return False
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_ENTRY_COMPRESSION_RATIO:
                return False
        total_uncompressed += info.file_size
        total_compressed += info.compress_size
    if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        return False
    if total_compressed > MAX_ARCHIVE_COMPRESSED_BYTES:
        return False
    if total_uncompressed > 0 and total_compressed == 0:
        return False
    if total_compressed > 0:
        aggregate_ratio = total_uncompressed / total_compressed
        if aggregate_ratio > MAX_TOTAL_COMPRESSION_RATIO:
            return False
    return True


def _valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _valid_xlsx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as workbook_archive:
            infos = workbook_archive.infolist()
            if not _zip_metadata_within_limits(infos):
                return False
            if any(info.flag_bits & 0x1 for info in infos):
                return False
            names = {info.filename for info in infos}
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                return False
            lowered = {name.casefold() for name in names}
            if any(
                "vbaproject.bin" in name
                or name.startswith("xl/embeddings/")
                for name in lowered
            ):
                return False
        workbook = load_workbook(
            path,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        workbook.close()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return False
    return True


def _safe_archive_path(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise BackupArchiveError("مسار غير صالح داخل النسخة الاحتياطية.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise BackupArchiveError("مسار غير آمن داخل النسخة الاحتياطية.")
    return path


def _allowed_payload_path(name: str) -> bool:
    if name == DATABASE_ARCHIVE_PATH:
        return True
    path = PurePosixPath(name)
    if len(path.parts) != 3 or path.parts[0] != "exports":
        return False
    if path.parts[1] == "invoices":
        return bool(re.fullmatch(r"invoice_\d{6}\.pdf", path.name))
    if path.parts[1] == "spreadsheets":
        return bool(re.fullmatch(r"export_\d{6}\.xlsx", path.name))
    return False


def _database_metadata(database_path: Path) -> tuple[int, str]:
    valid, detail = integrity_check(database_path)
    if not valid:
        raise BackupArchiveError(f"فشل فحص سلامة قاعدة البيانات: {detail}")
    connection = None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=10
        )
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        tables = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected_tables = {table.name for table in Base.metadata.sorted_tables}
        missing_tables = expected_tables - tables
        if missing_tables:
            raise BackupArchiveError("قاعدة البيانات داخل النسخة ناقصة الجداول الأساسية.")
        for table in Base.metadata.sorted_tables:
            columns = {
                item[1]
                for item in connection.execute(
                    f"PRAGMA table_info('{table.name}')"
                ).fetchall()
            }
            expected_columns = {column.name for column in table.columns}
            if expected_columns - columns:
                raise BackupArchiveError("قاعدة البيانات داخل النسخة ناقصة الحقول الأساسية.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupArchiveError("تحتوي قاعدة البيانات داخل النسخة على روابط غير سليمة.")
    except sqlite3.Error as error:
        raise BackupArchiveError("قاعدة البيانات داخل النسخة غير مكتملة.") from error
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        raise BackupArchiveError("إصدار بيانات النسخة الاحتياطية غير موجود.")
    try:
        schema_version = int(row[0])
    except (TypeError, ValueError) as error:
        raise BackupArchiveError("إصدار بيانات النسخة الاحتياطية غير صالح.") from error
    if schema_version != SCHEMA_VERSION:
        raise BackupArchiveError("إصدار بيانات النسخة الاحتياطية غير مدعوم.")
    return schema_version, detail


def _export_sources(layout: StorageLayout) -> list[tuple[Path, str]]:
    if not layout.exports_dir.exists():
        return []
    pdfs = sorted(
        (
            path
            for path in layout.invoices_dir.glob("*.pdf")
            if _is_regular_local_file(path, layout.invoices_dir)
            and _valid_pdf(path)
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    spreadsheets = sorted(
        (
            path
            for path in layout.exports_dir.glob("*.xlsx")
            if _is_regular_local_file(path, layout.exports_dir)
            and _valid_xlsx(path)
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    result = [
        (path, f"exports/invoices/invoice_{index:06d}.pdf")
        for index, path in enumerate(pdfs, start=1)
    ]
    result.extend(
        (path, f"exports/spreadsheets/export_{index:06d}.xlsx")
        for index, path in enumerate(spreadsheets, start=1)
    )
    return result


def _resolved_invoice_path(value: str, layout: StorageLayout) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    candidate = (layout.user_root / path).resolve()
    if candidate.exists():
        return candidate
    return (layout.exports_dir / path).resolve()


def _make_snapshot_paths_portable(
    database_path: Path,
    exports: list[tuple[Path, str]],
    layout: StorageLayout,
) -> None:
    mapping = {source.resolve(): archive_name for source, archive_name in exports}
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        has_invoices = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoices'"
        ).fetchone()
        if not has_invoices:
            return
        rows = connection.execute(
            "SELECT id, pdf_path FROM invoices WHERE pdf_path IS NOT NULL AND pdf_path != ''"
        ).fetchall()
        for invoice_id, value in rows:
            portable = mapping.get(_resolved_invoice_path(str(value), layout))
            if portable:
                connection.execute(
                    "UPDATE invoices SET pdf_path=? WHERE id=?", (portable, invoice_id)
                )
        connection.commit()
    finally:
        connection.close()


def _make_restored_paths_local(database_path: Path, layout: StorageLayout) -> None:
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        rows = connection.execute(
            "SELECT id, pdf_path FROM invoices WHERE pdf_path LIKE 'exports/invoices/%'"
        ).fetchall()
        for invoice_id, value in rows:
            name = PurePosixPath(str(value)).name
            target = (layout.invoices_dir / name).resolve()
            connection.execute(
                "UPDATE invoices SET pdf_path=? WHERE id=?", (str(target), invoice_id)
            )
        connection.commit()
    finally:
        connection.close()


def _write_zip_member(archive: zipfile.ZipFile, source: Path, name: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_handle, archive.open(name, "w") as output_handle:
        for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            output_handle.write(chunk)
    return {"path": name, "sha256": digest.hexdigest(), "size": size}


def _archive_filename(prefix: str) -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix).strip("_") or "TeacherHub_Backup"
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    return f"{safe_prefix}_{stamp}_{uuid.uuid4().hex[:8]}{ARCHIVE_SUFFIX}"


def create_full_backup(
    destination_dir: Path,
    *,
    layout: StorageLayout | None = None,
    filename_prefix: str = "TeacherHub_Backup",
) -> ArchiveInfo:
    layout = layout or StorageLayout.live()
    layout.ensure_directories()
    destination_dir = Path(destination_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not layout.database_path.exists():
        raise BackupArchiveError("لا توجد بيانات حالية لإنشاء نسخة احتياطية.")

    work_parent = layout.restore_dir / "work"
    work_parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="backup-", dir=work_parent))
    final_path = destination_dir / _archive_filename(filename_prefix)
    temporary_archive = destination_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        snapshot = online_backup(
            layout.database_path, work_dir, prefix="portable_snapshot"
        )
        exports = _export_sources(layout)
        _make_snapshot_paths_portable(snapshot, exports, layout)
        schema_version, integrity_result = _database_metadata(snapshot)

        records: list[dict[str, Any]] = []
        with zipfile.ZipFile(
            temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            database_record = _write_zip_member(
                archive, snapshot, DATABASE_ARCHIVE_PATH
            )
            records.append(database_record)
            for source, archive_name in exports:
                records.append(_write_zip_member(archive, source, archive_name))
            manifest = {
                "format": ARCHIVE_FORMAT,
                "format_version": ARCHIVE_FORMAT_VERSION,
                "app_release": config.APP_RELEASE_REFERENCE,
                "schema_version": schema_version,
                "created_at": _utc_now(),
                "database_integrity": integrity_result,
                "database_sha256": database_record["sha256"],
                "files": records,
            }
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            )

        info = validate_backup_archive(
            temporary_archive, layout=layout
        )
        os.replace(temporary_archive, final_path)
        return ArchiveInfo(
            archive_path=final_path,
            created_at=info.created_at,
            schema_version=info.schema_version,
            export_count=info.export_count,
            file_count=info.file_count,
            database_integrity=info.database_integrity,
            app_release=info.app_release,
        )
    except Exception:
        temporary_archive.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _validate_manifest(manifest: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    required_keys = {
        "format",
        "format_version",
        "app_release",
        "schema_version",
        "created_at",
        "database_integrity",
        "database_sha256",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != required_keys:
        raise BackupArchiveError("بيانات تعريف النسخة الاحتياطية غير صالحة.")
    if manifest["format"] != ARCHIVE_FORMAT:
        raise BackupArchiveError("هذا الملف ليس نسخة Teacher Hub كاملة.")
    if manifest["format_version"] != ARCHIVE_FORMAT_VERSION:
        raise BackupArchiveError("إصدار ملف النسخة الاحتياطية غير مدعوم.")
    if not isinstance(manifest["app_release"], str) or not re.fullmatch(
        r"[A-Za-z0-9 ._+\-]{1,100}", manifest["app_release"]
    ):
        raise BackupArchiveError("بيانات تعريف النسخة الاحتياطية غير مكتملة.")
    if not isinstance(manifest["created_at"], str):
        raise BackupArchiveError("تاريخ النسخة الاحتياطية غير صالح.")
    try:
        datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupArchiveError("تاريخ النسخة الاحتياطية غير صالح.") from error
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BackupArchiveError("إصدار البيانات داخل النسخة غير صالح.")
    if manifest["database_integrity"] != "ok":
        raise BackupArchiveError("قاعدة البيانات داخل النسخة لم تجتز فحص السلامة.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest["database_sha256"])):
        raise BackupArchiveError("بصمة قاعدة البيانات داخل النسخة غير صالحة.")
    files = manifest["files"]
    if (
        not isinstance(files, list)
        or not files
        or len(files) > MAX_ARCHIVE_FILES - 1
    ):
        raise BackupArchiveError("قائمة ملفات النسخة الاحتياطية غير صالحة.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise BackupArchiveError("أحد سجلات الملفات داخل النسخة غير صالح.")
        name = str(record["path"])
        _safe_archive_path(name)
        if not _allowed_payload_path(name) or name in seen:
            raise BackupArchiveError("تحتوي النسخة على ملف غير متوقع.")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"])):
            raise BackupArchiveError("إحدى بصمات الملفات غير صالحة.")
        if not isinstance(record["size"], int) or record["size"] < 0:
            raise BackupArchiveError("حجم أحد الملفات داخل النسخة غير صالح.")
        normalized.append(record)
        seen.add(name)
    database_records = [
        record for record in normalized if record["path"] == DATABASE_ARCHIVE_PATH
    ]
    if len(database_records) != 1:
        raise BackupArchiveError("قاعدة البيانات مفقودة من النسخة الاحتياطية.")
    if database_records[0]["sha256"] != manifest["database_sha256"]:
        raise BackupArchiveError("بصمة قاعدة البيانات لا تطابق بيانات النسخة.")
    return manifest, normalized


def _empty_directory(path: Path) -> None:
    if path.exists():
        if any(path.iterdir()):
            raise BackupArchiveError("مجلد تجهيز الاستعادة ليس فارغاً.")
    else:
        path.mkdir(parents=True)


def validate_backup_archive(
    archive_path: Path,
    *,
    layout: StorageLayout | None = None,
    extract_to: Path | None = None,
) -> ArchiveInfo:
    layout = layout or StorageLayout.live()
    layout.ensure_directories()
    archive_path = Path(archive_path).expanduser().resolve()
    if not archive_path.is_file():
        raise BackupArchiveError("ملف النسخة الاحتياطية غير موجود.")
    if _archive_physical_size(archive_path) > MAX_ARCHIVE_COMPRESSED_BYTES:
        raise BackupArchiveError(
            "حجم ملف النسخة المضغوط يتجاوز الحد الآمن."
        )

    temporary_extraction = extract_to is None
    if extract_to is None:
        validation_root = layout.restore_dir / "validation"
        validation_root.mkdir(parents=True, exist_ok=True)
        extract_to = Path(tempfile.mkdtemp(prefix="validate-", dir=validation_root))
    else:
        extract_to = Path(extract_to).resolve()
        if not _inside(extract_to, layout.restore_dir):
            raise BackupArchiveError("مجلد تجهيز الاستعادة خارج المساحة الآمنة.")
        _empty_directory(extract_to)

    try:
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise BackupArchiveError("ملف النسخة الاحتياطية تالف أو غير مكتمل.") from error
        with archive:
            infos = archive.infolist()
            if not _zip_metadata_within_limits(infos):
                raise BackupArchiveError(
                    "حجم أو نسبة ضغط النسخة تتجاوز حدود الأمان."
                )
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or MANIFEST_NAME not in names:
                raise BackupArchiveError("بنية النسخة الاحتياطية غير صالحة.")
            for info in infos:
                _safe_archive_path(info.filename)
                if info.is_dir() or info.flag_bits & 0x1:
                    raise BackupArchiveError("تحتوي النسخة على عنصر غير مدعوم.")
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise BackupArchiveError("تحتوي النسخة على رابط غير آمن.")
            manifest_info = next(info for info in infos if info.filename == MANIFEST_NAME)
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise BackupArchiveError("حجم ملف تعريف النسخة غير صالح.")
            try:
                manifest = json.loads(
                    archive.read(MANIFEST_NAME).decode("utf-8"),
                    object_pairs_hook=_json_object_without_duplicates,
                )
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BackupArchiveError("ملف تعريف النسخة الاحتياطية غير صالح.") from error
            manifest, records = _validate_manifest(manifest)
            expected_names = {MANIFEST_NAME, *(record["path"] for record in records)}
            if set(names) != expected_names:
                raise BackupArchiveError("تحتوي النسخة على ملفات مفقودة أو غير متوقعة.")

            manifest_target = extract_to / MANIFEST_NAME
            manifest_target.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            record_by_name = {record["path"]: record for record in records}
            for info in infos:
                if info.filename == MANIFEST_NAME:
                    continue
                target = extract_to.joinpath(*PurePosixPath(info.filename).parts)
                if not _inside(target, extract_to):
                    raise BackupArchiveError("محاولة كتابة ملف خارج مساحة الاستعادة.")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        output.write(chunk)
                record = record_by_name[info.filename]
                if size != record["size"] or digest.hexdigest() != record["sha256"]:
                    raise BackupArchiveError("فشل التحقق من بصمة أحد ملفات النسخة.")
                if info.filename.endswith(".pdf") and not _valid_pdf(target):
                    raise BackupArchiveError(
                        "أحد ملفات PDF داخل النسخة غير صالح."
                    )
                if info.filename.endswith(".xlsx") and not _valid_xlsx(target):
                    raise BackupArchiveError(
                        "أحد ملفات Excel داخل النسخة غير صالح."
                    )

        database_path = extract_to / DATABASE_ARCHIVE_PATH
        schema_version, integrity_result = _database_metadata(database_path)
        if schema_version != manifest["schema_version"]:
            raise BackupArchiveError("إصدار البيانات لا يطابق ملف تعريف النسخة.")
        return ArchiveInfo(
            archive_path=archive_path,
            created_at=manifest["created_at"],
            schema_version=schema_version,
            export_count=len(records) - 1,
            file_count=len(records),
            database_integrity=integrity_result,
            app_release=manifest["app_release"],
        )
    finally:
        if temporary_extraction:
            shutil.rmtree(extract_to, ignore_errors=True)


def _validate_staged_payload(staging_dir: Path) -> ArchiveInfo:
    manifest_path = staging_dir / MANIFEST_NAME
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise BackupArchiveError("حجم ملف تعريف الاستعادة غير صالح.")
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupArchiveError("ملف تعريف الاستعادة غير صالح.") from error
    manifest, records = _validate_manifest(manifest)
    expected = {MANIFEST_NAME, *(record["path"] for record in records)}
    actual = {
        path.relative_to(staging_dir).as_posix()
        for path in staging_dir.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise BackupArchiveError("ملفات الاستعادة مفقودة أو غير متوقعة.")
    for record in records:
        path = staging_dir.joinpath(*PurePosixPath(record["path"]).parts)
        if not _inside(path, staging_dir):
            raise BackupArchiveError("مسار غير آمن في ملفات الاستعادة.")
        if path.stat().st_size != record["size"] or _sha256_file(path) != record["sha256"]:
            raise BackupArchiveError("تغيّر أحد ملفات الاستعادة بعد التحقق منه.")
    schema_version, integrity_result = _database_metadata(
        staging_dir / DATABASE_ARCHIVE_PATH
    )
    if schema_version != manifest["schema_version"]:
        raise BackupArchiveError("إصدار بيانات الاستعادة غير متطابق.")
    return ArchiveInfo(
        archive_path=staging_dir,
        created_at=manifest["created_at"],
        schema_version=schema_version,
        export_count=len(records) - 1,
        file_count=len(records),
        database_integrity=integrity_result,
        app_release=manifest["app_release"],
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def stage_restore(
    archive_path: Path,
    *,
    layout: StorageLayout | None = None,
) -> StagedRestore:
    layout = layout or StorageLayout.live()
    layout.ensure_directories()
    if layout.pending_marker.exists():
        raise RestoreError("توجد استعادة مجهزة بالفعل. أعد تشغيل التطبيق لإكمالها.")
    staging_dir = layout.restore_dir / f"incoming-{uuid.uuid4().hex}"
    emergency_backup: Path | None = None
    try:
        info = validate_backup_archive(
            archive_path, layout=layout, extract_to=staging_dir
        )
        emergency = create_full_backup(
            layout.backups_dir,
            layout=layout,
            filename_prefix="TeacherHub_Emergency",
        )
        emergency_backup = emergency.archive_path
        marker = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "staging_name": staging_dir.name,
            "manifest_sha256": _sha256_file(staging_dir / MANIFEST_NAME),
            "emergency_backup": str(emergency_backup),
            "created_at": _utc_now(),
        }
        _atomic_json(layout.pending_marker, marker)
        return StagedRestore(info, emergency_backup, layout.pending_marker)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def has_pending_restore(*, layout: StorageLayout | None = None) -> bool:
    layout = layout or StorageLayout.live()
    return layout.pending_marker.is_file()


def _read_marker(layout: StorageLayout) -> tuple[dict[str, Any], Path]:
    try:
        marker = json.loads(layout.pending_marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreError("ملف خطة الاستعادة غير صالح.") from error
    required = {
        "format_version",
        "staging_name",
        "manifest_sha256",
        "emergency_backup",
        "created_at",
    }
    if not isinstance(marker, dict) or set(marker) != required:
        raise RestoreError("بيانات خطة الاستعادة غير مكتملة.")
    if marker["format_version"] != ARCHIVE_FORMAT_VERSION:
        raise RestoreError("إصدار خطة الاستعادة غير مدعوم.")
    staging_name = str(marker["staging_name"])
    if not re.fullmatch(r"incoming-[0-9a-f]{32}", staging_name):
        raise RestoreError("مسار ملفات الاستعادة غير صالح.")
    staging_dir = (layout.restore_dir / staging_name).resolve()
    if not _inside(staging_dir, layout.restore_dir) or not staging_dir.is_dir():
        raise RestoreError("ملفات الاستعادة المجهزة غير موجودة.")
    if _sha256_file(staging_dir / MANIFEST_NAME) != marker["manifest_sha256"]:
        raise RestoreError("تغيّر ملف تعريف الاستعادة بعد تجهيزه.")
    emergency = Path(str(marker["emergency_backup"])).resolve()
    if not emergency.is_file() or not _inside(emergency, layout.backups_dir):
        raise RestoreError("نسخة الأمان السابقة للاستعادة غير موجودة.")
    return marker, staging_dir


def _remove_database_sidecars(database_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)


def _checkpoint_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    finally:
        connection.close()


def _restore_database_snapshot(snapshot: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.rollback-{uuid.uuid4().hex}")
    shutil.copy2(snapshot, temporary)
    os.replace(temporary, target)


def apply_pending_restore(
    *,
    layout: StorageLayout | None = None,
) -> ArchiveInfo | None:
    layout = layout or StorageLayout.live()
    layout.ensure_directories()
    if not layout.pending_marker.exists():
        return None

    marker, staging_dir = _read_marker(layout)
    info = _validate_staged_payload(staging_dir)
    rollback_dir = layout.restore_dir / f"rollback-{uuid.uuid4().hex}"
    rollback_dir.mkdir(parents=True)
    rollback_database: Path | None = None
    prior_exports = rollback_dir / "live_exports"
    exports_original_moved = False
    exports_swapped = False
    database_swapped = False
    new_exports = layout.user_root / f".exports-restore-{uuid.uuid4().hex}"
    new_database = layout.database_path.parent / f".teacher-restore-{uuid.uuid4().hex}.db"
    prior_database = rollback_dir / "live_teacher.db"
    try:
        if layout.database_path.exists() and layout.database_path.stat().st_size > 0:
            _checkpoint_database(layout.database_path)
            rollback_database = online_backup(
                layout.database_path, rollback_dir, prefix="pre_restore_state"
            )
        if layout.exports_dir.exists():
            shutil.copytree(layout.exports_dir, rollback_dir / "exports_snapshot")

        source_exports = staging_dir / "exports"
        if source_exports.exists():
            shutil.copytree(source_exports, new_exports)
        else:
            new_exports.mkdir(parents=True)
        (new_exports / "invoices").mkdir(parents=True, exist_ok=True)

        shutil.copy2(staging_dir / DATABASE_ARCHIVE_PATH, new_database)
        _make_restored_paths_local(new_database, layout)
        _database_metadata(new_database)

        if layout.exports_dir.exists():
            os.replace(layout.exports_dir, prior_exports)
            exports_original_moved = True
        os.replace(new_exports, layout.exports_dir)
        exports_swapped = True

        _remove_database_sidecars(layout.database_path)
        if layout.database_path.exists():
            os.replace(layout.database_path, prior_database)
        os.replace(new_database, layout.database_path)
        database_swapped = True
        _remove_database_sidecars(layout.database_path)

        restored_schema, restored_integrity = _database_metadata(layout.database_path)
        if restored_schema != info.schema_version or restored_integrity != "ok":
            raise RestoreError("لم تجتز البيانات المستعادة فحص السلامة النهائي.")

        layout.pending_marker.unlink()
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(rollback_dir, ignore_errors=True)
        return info
    except Exception as error:
        rollback_errors: list[str] = []
        try:
            if database_swapped or prior_database.exists():
                _remove_database_sidecars(layout.database_path)
                layout.database_path.unlink(missing_ok=True)
                if prior_database.exists():
                    os.replace(prior_database, layout.database_path)
                elif rollback_database is not None:
                    _restore_database_snapshot(rollback_database, layout.database_path)
                else:
                    layout.database_path.unlink(missing_ok=True)
                if layout.database_path.exists():
                    valid, detail = integrity_check(layout.database_path)
                    if not valid:
                        raise RestoreError(f"فشل فحص البيانات السابقة: {detail}")
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        try:
            if exports_swapped or exports_original_moved:
                shutil.rmtree(layout.exports_dir, ignore_errors=True)
                if prior_exports.exists():
                    os.replace(prior_exports, layout.exports_dir)
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        new_database.unlink(missing_ok=True)
        shutil.rmtree(new_exports, ignore_errors=True)
        emergency = marker.get("emergency_backup", "")
        detail = f"تعذرت الاستعادة، وتمت إعادة البيانات السابقة. نسخة الأمان: {emergency}"
        if rollback_errors:
            detail = (
                "تعذرت الاستعادة التلقائية الكاملة. لا تحذف الملفات الحالية، "
                f"واستخدم نسخة الأمان: {emergency}. التفاصيل: {'; '.join(rollback_errors)}"
            )
        raise RestoreError(detail) from error

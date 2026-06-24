import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


def integrity_check(path: Path) -> tuple[bool, str]:
    path = Path(path)
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as error:
        return False, str(error)
    return result == "ok", str(result)


def online_backup(source: Path, destination_dir: Path, prefix: str = "teacher_backup") -> Path:
    source = Path(source)
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        raise FileNotFoundError(f"Database does not exist: {source}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = destination_dir / f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}.db"
    source_connection = sqlite3.connect(source, timeout=10)
    destination_connection = sqlite3.connect(destination, timeout=10)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    except Exception:
        destination_connection.close()
        source_connection.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        destination_connection.close()
        source_connection.close()

    valid, detail = integrity_check(destination)
    if not valid:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity_check failed: {detail}")
    return destination

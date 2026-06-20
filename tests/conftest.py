"""Shared pytest fixtures.

The autouse `temp_db` fixture redirects ALL database + file output to a
throwaway temp directory, so no test can ever touch the real
data/teacher.db or write into the project's exports/ folder.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    from app.db import engine as engine_mod

    db_file = tmp_path / "test.db"
    engine_mod.configure_engine(f"sqlite:///{db_file}")
    engine_mod.init_db()

    invoices_dir = tmp_path / "invoices"
    exports_dir = tmp_path / "exports"
    invoices_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Rebind config paths that the services captured at import time.
    import app.services.billing_service as billing
    import app.services.pdf_service as pdf
    import app.services.excel_service as excel

    monkeypatch.setattr(billing, "DB_PATH", db_file, raising=False)
    monkeypatch.setattr(billing, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(pdf, "INVOICES_DIR", invoices_dir, raising=False)
    monkeypatch.setattr(excel, "EXPORTS_DIR", exports_dir, raising=False)

    yield engine_mod.SessionLocal

    engine_mod.SessionLocal.remove()

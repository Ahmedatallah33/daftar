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
    from app import config
    from app.db import engine as engine_mod

    original_root = config.USER_ROOT
    test_root = tmp_path / "_runtime"
    config.apply_user_root(test_root)
    engine_mod.configure_engine(config.DB_URL)
    engine_mod.init_db()

    yield engine_mod.SessionLocal

    from app.ui.helpers.worker import set_background_operations_enabled
    from app.account_context import deactivate_account_context

    set_background_operations_enabled(True)
    deactivate_account_context()
    config.apply_user_root(original_root)

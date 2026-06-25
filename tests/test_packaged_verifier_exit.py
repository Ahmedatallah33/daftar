from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_packaged_persistence.ps1"


def _run_probe(mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ExitSemanticsProbe",
            mode,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_packaged_verifier_success_probe_exits_zero():
    result = _run_probe("Success")

    assert result.returncode == 0
    assert "PACKAGED_VERIFIER_EXIT_PROBE success" in result.stdout


def test_packaged_verifier_failure_probe_exits_nonzero():
    result = _run_probe("Failure")

    assert result.returncode != 0
    assert "controlled failure" in result.stderr


def test_activation_modules_import_for_packaging_probe():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import app.activation; "
                "import app.cloud.auth_identity; "
                "import app.cloud.supabase_auth; "
                "import app.cloud.supabase_provider; "
                "import app.cloud.supabase_workspace_repository; "
                "import app.identity.credential_store; "
                "import app.restart; "
                "import app.ui.pages.workspace_picker_dialog; "
                "print('ACTIVATION_IMPORT_OK')"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ACTIVATION_IMPORT_OK" in result.stdout

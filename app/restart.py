from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence


_restart_requested = False
_restart_callback: Callable[[], None] | None = None


def request_restart_after_exit(callback: Callable[[], None] | None = None) -> None:
    global _restart_requested, _restart_callback
    _restart_requested = True
    _restart_callback = callback


def restart_requested() -> bool:
    return _restart_requested


def reset_restart_request() -> None:
    global _restart_requested, _restart_callback
    _restart_requested = False
    _restart_callback = None


def build_restart_command(
    *,
    executable: str | None = None,
    argv: Sequence[str] | None = None,
    frozen: bool | None = None,
) -> list[str]:
    selected_executable = executable or sys.executable
    selected_argv = list(sys.argv if argv is None else argv)
    is_frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    if is_frozen:
        return [selected_executable, *selected_argv[1:]]
    return [selected_executable, *selected_argv]


def launch_replacement_process() -> None:
    if _restart_callback is not None:
        _restart_callback()
        return
    subprocess.Popen(build_restart_command(), close_fds=True)

"""Black-box checks for the commands documented in the collector README."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module,args",
    [
        ("collector.pipeline.dataset_assembler", ["2026-06-03"]),
        ("collector.scripts.verify_dataset", ["2026-06-03"]),
        ("collector.scripts.gap_report", ["2026-06-03"]),
        ("collector.pipeline.stats_computer", []),
        ("collector.pipeline.label_generator", ["2026-06-03"]),
        ("collector.pipeline.split_generator", []),
    ],
)
def test_entrypoints_execute_without_import_errors(module: str, args: list[str]) -> None:
    """Run each finite documented command in a real interpreter from repo root.

    The repository has no fixture data at this date, so verification commands
    may correctly return non-zero for absent market data.  No documented
    command may fail before its application code because the package cannot be
    imported.
    """
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        # This is a fresh interpreter, so pytest's sys.path hook is not
        # involved. The command is launched exactly as documented.
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    output = completed.stdout + completed.stderr
    assert "ModuleNotFoundError" not in output
    assert "ImportError" not in output

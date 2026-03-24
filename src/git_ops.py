from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import DATA_DIR

logger = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        logger.warning("Command %s failed: %s", cmd, result.stderr.strip())
    return result.returncode, result.stdout.strip()


def commit_and_push_data(date_str: str) -> bool:
    data_dir = DATA_DIR

    _run(["git", "add", "-A"], cwd=data_dir)

    rc, _ = _run(["git", "diff", "--cached", "--quiet"], cwd=data_dir)
    if rc == 0:
        logger.info("No changes in data directory, skipping commit")
        return True

    rc, _ = _run(
        ["git", "commit", "-m", f"daily update: {date_str}"],
        cwd=data_dir,
    )
    if rc != 0:
        return False

    rc, _ = _run(["git", "push"], cwd=data_dir)
    if rc != 0:
        logger.error("Failed to push data submodule")
        return False

    # Update submodule pointer in main repo
    main_dir = data_dir.parent
    _run(["git", "add", "data"], cwd=main_dir)
    rc, _ = _run(["git", "diff", "--cached", "--quiet"], cwd=main_dir)
    if rc != 0:
        _run(
            ["git", "commit", "-m", "update data submodule pointer"],
            cwd=main_dir,
        )
        _run(["git", "push"], cwd=main_dir)

    logger.info("Data committed and pushed for %s", date_str)
    return True

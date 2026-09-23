"""
Quiet daily backup of Neural's own personal/runtime data -- memory/,
tutor_memory/, tutor_knowledge/, automations.json, tutor_docs_cache.json,
and push_subscriptions.json. These are exactly the files
Neural/.gitignore keeps OUT of the main git repo on purpose (so personal
facts/contacts/automations never end up in a repo that might get pushed
somewhere), which means an accidental overwrite or a bad memory_write
has no version history to fall back on. This gives them one, without
putting any of it in the main repo or anywhere off this machine.

Mechanism: a completely separate, LOCAL-ONLY git repository at
Neural/.memory_backups/ (also gitignored from the main repo -- see
.gitignore), mirroring just those files. Once a day (see schedule_backup,
wired up at app startup in main.py) the current contents are copied in
and committed -- git's own storage dedups unchanged content, so daily
commits of mostly-unchanged small files stay cheap indefinitely, and
`git log`/`git diff`/`git checkout` inside that folder gives real
history if something needs recovering. If `git` isn't on PATH at all,
this falls back to a single always-overwritten plain-copy snapshot
instead of hard-failing -- worse (no history) but still something.
"""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger("neural.backup")

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / ".memory_backups"
FALLBACK_DIR = ROOT / ".memory_backup_snapshot"  # used only if git isn't available at all

# Relative to ROOT. Files/dirs that don't exist yet (e.g. tutor_knowledge/
# before fastembed is installed) are silently skipped, not an error.
BACKUP_SOURCES = [
    "memory",
    "tutor_memory",
    "tutor_knowledge",
    "automations.json",
    "tutor_docs_cache.json",
    "push_subscriptions.json",
]

_git_available_cache: "bool | None" = None


def _git_available() -> bool:
    global _git_available_cache
    if _git_available_cache is None:
        _git_available_cache = shutil.which("git") is not None
    return _git_available_cache


async def _run_git(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _mirror_sources(dest: Path) -> list[str]:
    """Copy every existing BACKUP_SOURCES entry into dest, mirroring its
    relative path. Returns the list of sources actually found/copied."""
    copied = []
    for rel in BACKUP_SOURCES:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = dest / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied.append(rel)
    return copied


async def run_backup() -> dict[str, Any]:
    """Snapshot every BACKUP_SOURCES entry as it stands right now. Safe
    to call as often as you like -- a run with nothing changed since the
    last one just doesn't create a new commit (or, in fallback mode,
    just re-overwrites the same snapshot)."""
    if not _git_available():
        FALLBACK_DIR.mkdir(exist_ok=True)
        # Fresh each time, not accumulated -- with no git history to fall
        # back on anyway, keeping N old copies around just wastes disk.
        for rel in BACKUP_SOURCES:
            dst = FALLBACK_DIR / rel
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        copied = _mirror_sources(FALLBACK_DIR)
        logger.info("Memory backup: git not found on PATH -- wrote a plain snapshot to %s instead.", FALLBACK_DIR)
        return {"mode": "snapshot", "path": str(FALLBACK_DIR), "sources": copied}

    BACKUP_DIR.mkdir(exist_ok=True)
    if not (BACKUP_DIR / ".git").exists():
        code, _, err = await _run_git("init", cwd=BACKUP_DIR)
        if code != 0:
            logger.error("Memory backup: `git init` failed (%s) -- falling back to plain copy this run.", err.strip())
            copied = _mirror_sources(BACKUP_DIR)
            return {"mode": "copy-no-history", "path": str(BACKUP_DIR), "sources": copied}

    copied = _mirror_sources(BACKUP_DIR)
    await _run_git("add", "-A", cwd=BACKUP_DIR)
    code, out, _ = await _run_git("status", "--porcelain", cwd=BACKUP_DIR)
    if code == 0 and not out.strip():
        return {"mode": "git", "path": str(BACKUP_DIR), "sources": copied, "committed": False}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    code, _, err = await _run_git(
        "-c", "user.name=Neural Backup", "-c", "user.email=neural-backup@localhost",
        "commit", "-m", f"Backup {stamp}",
        cwd=BACKUP_DIR,
    )
    if code != 0:
        logger.error("Memory backup: `git commit` failed: %s", err.strip())
        return {"mode": "git", "path": str(BACKUP_DIR), "sources": copied, "committed": False, "error": err.strip()}

    logger.info("Memory backup: committed a snapshot of %s to %s.", ", ".join(copied) or "(nothing)", BACKUP_DIR)
    return {"mode": "git", "path": str(BACKUP_DIR), "sources": copied, "committed": True}


def schedule_backup(hour: int = 3, minute: int = 0) -> None:
    """Register the daily backup job on automations.py's existing
    APScheduler instance -- this is Neural's own infrastructure, not a
    user-facing automation, so it deliberately does NOT go through
    automations.json/the Automations UI (it doesn't run an agent prompt
    at all, just a file/git operation). Call once at app startup."""
    from .automations import scheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler.add_job(
        run_backup,
        CronTrigger(hour=hour, minute=minute),
        id="memory_backup",
        replace_existing=True,
    )


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "backup_now",
    "Trigger an out-of-band snapshot of every memory/CRM/automation file right "
    "now, instead of waiting for the automatic daily backup. A daily backup "
    "already runs on its own in the background -- only use this when the user "
    "explicitly asks for a backup, or right before you're about to do something "
    "to memory files that feels risky (a big restructure, a bulk delete).",
    {},
)
async def backup_now(args: dict) -> dict[str, Any]:
    result = await run_backup()
    if result["mode"] == "snapshot":
        return _text("Backed up (plain copy -- git isn't installed on this machine, so there's no history, just the latest snapshot).")
    if not result.get("committed", True) and "error" not in result:
        return _text("Nothing's changed since the last backup -- no new snapshot needed.")
    if not result.get("committed", True):
        return _text(f"Backup attempt hit an error: {result.get('error')}")
    return _text(f"Backed up {', '.join(result['sources']) or 'nothing (no files exist yet)'} just now.")


BACKUP_TOOLS = ["backup_now"]
backup_server = create_sdk_mcp_server(name="backup", version="1.0.0", tools=[backup_now])

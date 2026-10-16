"""
Reaps stuck video-rendering/recording/export processes that outlive
Neural's own understanding of them.

THE BUG THIS FIXES: when a tool call shells out to something that can
hang -- `video_record`/`video_edit`/etc. (the mcp-video MCP server,
which drives ffmpeg underneath), or Cap's CLI (`cap caps wait ... --for
all`, which blocks polling Cap's own transcription/AI-summary pipeline,
or `cap export`) -- main.py's `_run_turn` idle-timeout
(config.TURN_IDLE_TIMEOUT_SECONDS) correctly notices the SDK
conversation went silent and discards/resets that session after a few
minutes. But `client.disconnect()` only tears down the `claude` CLI
subprocess itself; it does NOT reach down into whatever OS process tree
that subprocess (or its MCP servers) spawned underneath it -- node ->
@studiomeyer/mcp-video -> ffmpeg, or a Bash-invoked `cap` CLI call that's
stuck polling. On Windows especially, terminating a parent process does
not automatically kill its still-alive descendants -- they become
orphans and keep running completely invisibly to Neural, for however
long the underlying operation takes to error out on its own (which, for
a wedged ffmpeg filter graph or a `cap caps wait` loop stuck on a job
that will never finish server-side, can be never). That is the literal
mechanism behind "it's been rendering for days": there is a genuinely
orphaned process still alive on the machine, consuming CPU, with
nothing left anywhere in Neural's own state pointing back to it -- the
chat conversation itself already recovered and moved on after the
5-minute idle-timeout; only the OS process didn't.

THE FIX: an independent, deterministic watchdog -- not an LLM-driven
automation. Its periodic job (automations.py's start_scheduler) scans
for Neural-owned render/record/export processes over the generous
config.VIDEO_RENDER_MAX_SECONDS ceiling. When `_run_turn` times out, it
also snapshots descendants of that *specific* SDK client before
disconnecting and kills only those exact processes immediately. The
client-specific snapshot matters: a project-wide zero-age sweep could
otherwise kill a healthy render owned by Tutor mode or an automation.
Both paths kill the WHOLE process tree (not just the top process).

SAFETY: the one thing standing between this and an agent that kills
other things on the user's machine named "node" or "ffmpeg" is the cwd
check in _is_stale_render_process below. None of Neural's video-related
MCP servers (or the `cap` CLI, invoked via Bash) set an explicit `cwd`
when spawned (see agent.py's ClaudeAgentOptions / mcp_servers dict) --
so they, and anything THEY spawn in turn, inherit the Neural backend
process's own cwd, which is always this project's root folder. A
process whose cwd is somewhere else entirely -- the user's own,
completely unrelated ffmpeg/node usage anywhere else on their machine --
never matches and is never touched.
"""
import logging
import os
import time

from . import config

logger = logging.getLogger("neural.process_watchdog")

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_FFMPEG_NAMES = {"ffmpeg", "ffmpeg.exe"}
_CAP_NAMES = {"cap", "cap.exe"}
_NODE_NAMES = {"node", "node.exe"}
_NODE_CMDLINE_MARKERS = ("mcp-video", "studiomeyer")


def _matches_cwd(proc: "psutil.Process") -> bool:
    try:
        cwd = proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
    try:
        return os.path.normcase(os.path.abspath(cwd)) == os.path.normcase(PROJECT_ROOT)
    except Exception:  # noqa: BLE001
        return False


def _classify(proc: "psutil.Process") -> "str | None":
    """Returns a short label ("ffmpeg", "cap", "mcp-video") if this
    process looks like one of Neural's own video jobs AND its cwd
    confirms it actually belongs to THIS project (see module docstring),
    or None if it's unrelated and should never be touched."""
    try:
        name = (proc.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    if name in _FFMPEG_NAMES:
        return "ffmpeg" if _matches_cwd(proc) else None

    if name in _CAP_NAMES:
        return "cap" if _matches_cwd(proc) else None

    if name in _NODE_NAMES:
        try:
            cmdline = " ".join(proc.cmdline()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        if any(marker in cmdline for marker in _NODE_CMDLINE_MARKERS):
            return "mcp-video (node)" if _matches_cwd(proc) else None
        return None

    return None


def _kill_tree(proc: "psutil.Process") -> None:
    try:
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.kill()
    except psutil.NoSuchProcess:
        pass


def snapshot_client_render_processes(client) -> list[dict]:
    """Return video-process identities descended from *this exact* SDK client.

    The normal age-based reaper deliberately works project-wide, because a
    process that has exceeded the generous background ceiling is no longer a
    plausible active job.  A just-timed-out turn is different: using an age of
    zero there would also match a perfectly healthy render started by another
    chat mode or an automation in the same project.  Snapshot the descendants
    of the current client's own ``claude`` subprocess *before* disconnecting
    it, then use those stable pid/create-time pairs for the immediate cleanup.

    ``_transport._process`` is private SDK state, so this is intentionally
    best-effort.  A missing/changed SDK implementation simply returns an empty
    list and leaves the conservative periodic reaper as the fallback.
    """
    if not PSUTIL_AVAILABLE:
        return []

    try:
        transport = client._transport
        process = transport._process if transport is not None else None
        parent_pid = process.pid if process is not None else None
        if parent_pid is None:
            return []
        descendants = psutil.Process(parent_pid).children(recursive=True)
    except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return []

    matches = []
    for proc in descendants:
        try:
            kind = _classify(proc)
            if not kind:
                continue
            matches.append(
                {
                    "name": proc.name(),
                    "pid": proc.pid,
                    "created_at": proc.create_time(),
                    "kind": kind,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


def kill_snapshotted_processes(processes: list[dict]) -> list[dict]:
    """Kill only processes returned by ``snapshot_client_render_processes``.

    Checking the original creation timestamp prevents a recycled PID from ever
    being mistaken for the process that belonged to the timed-out client.
    """
    if not PSUTIL_AVAILABLE:
        return []

    killed = []
    now = time.time()
    for item in processes:
        try:
            proc = psutil.Process(item["pid"])
            if abs(proc.create_time() - item["created_at"]) > 0.01:
                continue
            # The process may have been re-parented after client.disconnect(),
            # so validate its identity and project ownership again rather than
            # trusting the old snapshot blindly.
            if _classify(proc) != item["kind"]:
                continue
            _kill_tree(proc)
        except (KeyError, psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:  # noqa: BLE001 -- cleanup must never block recovery
            logger.exception("Failed to kill snapshotted render process")
            continue

        info = {
            "name": item["name"],
            "pid": item["pid"],
            "age_seconds": int(now - item["created_at"]),
            "kind": item["kind"],
        }
        logger.error(
            "Killing %s process from the timed-out client (pid %s, running %ds).",
            info["kind"], info["pid"], info["age_seconds"],
        )
        killed.append(info)
    return killed


def reap_stale_render_processes(on_kill=None, min_age_seconds: "float | None" = None) -> list[dict]:
    """Scans every running process once; kills (with its full descendant
    tree) anything matching a Neural-owned render/record/export process
    whose age exceeds `min_age_seconds` (config.VIDEO_RENDER_MAX_SECONDS
    if not given). Returns a list of {"name", "pid", "age_seconds",
    "kind"} dicts for whatever it killed (empty if nothing was stale, or
    if psutil isn't installed). Cheap and safe to call as often as you
    like -- a quiet scan does nothing.

    The periodic scheduler uses the default threshold so it does not kill a
    potentially legitimate job. ``main.py`` does not use a zero-age
    project-wide sweep after a timeout: it snapshots and kills descendants of
    the exact client that timed out via ``snapshot_client_render_processes``.
    """
    if not PSUTIL_AVAILABLE:
        return []

    threshold = config.VIDEO_RENDER_MAX_SECONDS if min_age_seconds is None else min_age_seconds

    killed = []
    now = time.time()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            kind = _classify(proc)
            if not kind:
                continue
            age = now - proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:  # noqa: BLE001 -- never let one weird process abort the whole scan
            continue

        if age < threshold:
            continue

        try:
            pid = proc.pid
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        logger.error(
            "Killing stale %s process (pid %s, running %ds, over the %ds ceiling) -- "
            "see backend/process_watchdog.py's module docstring for why this exists.",
            kind, pid, int(age), int(threshold),
        )
        _kill_tree(proc)
        info = {"name": name, "pid": pid, "age_seconds": int(age), "kind": kind}
        killed.append(info)
        if on_kill:
            try:
                on_kill(info)
            except Exception:  # noqa: BLE001
                pass
    return killed


if __name__ == "__main__":
    # Manual one-off: `python -m backend.process_watchdog` from the
    # project root -- for clearing out anything ALREADY stuck right now,
    # without waiting for a restart or the next scheduled sweep.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not PSUTIL_AVAILABLE:
        print("psutil isn't installed -- run: pip install psutil")
    else:
        result = reap_stale_render_processes()
        if not result:
            print("Nothing stale found.")
        else:
            for item in result:
                print(f"Killed {item['kind']} (pid {item['pid']}, was running {item['age_seconds']}s)")

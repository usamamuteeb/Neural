"""
Analyze a social media post or video from its link.

Give Neural a URL -- YouTube, TikTok, Instagram, X/Twitter, Facebook,
LinkedIn, Reddit, Vimeo, Twitch clips, and the ~1800 other sites yt-dlp
supports -- and the analyze_media_link tool gathers everything Claude needs
to actually understand it, without spending model tokens on the legwork:

  1. Metadata via yt-dlp: title, creator, date, duration, view/like counts,
     the post's caption/description, hashtags, chapters.
  2. What's being SAID: the platform's own captions when they exist
     (manual first, then auto-generated); otherwise the audio is
     transcribed locally with Whisper (faster-whisper, or openai-whisper as
     a fallback) -- free, offline after a one-time model download.
  3. What's being SHOWN: a handful of key frames (evenly spaced, or exact
     moments via frame_times) saved as JPEGs, plus the thumbnail. Claude
     opens them with its own Read tool -- that's its vision, so a screen
     recording of a new feature's UI gets understood, not just the voice-over.
  4. Everything is written to media/analysis/<site-id>/analysis.md (full
     transcript included) and cached, so follow-up questions or a second
     call for more frames never re-download or re-transcribe. Folders older
     than KEEP_DAYS are pruned automatically.

Token budget: the tool result carries metadata, a capped transcript (the
full text stays in the report file for Grep), and file paths for the
frames -- the images themselves only cost tokens if Claude opens them.

Everything runs locally on the Claude subscription: no API keys, no paid
services. Optional dependencies, each degrading gracefully with a clear
install hint: yt-dlp (required for links), faster-whisper (transcription
when a video has no captions), ffmpeg on PATH (frames; PyAV is used if
ffmpeg is missing).

The analysis core imports no Agent-SDK code so it can be tested alone; the
MCP tool wrapper sits at the bottom.
"""

import asyncio
import glob
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import config, stt

logger = logging.getLogger("neural.media_analysis")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = PROJECT_ROOT / "media" / "analysis"
KEEP_DAYS = 7
TRANSCRIPT_RETURN_CHARS = 12000
DESCRIPTION_CHARS = 2500
MAX_FRAMES = 12
FRAME_WIDTH = 768
_SMALL_FORMAT = "18/b[height<=480][vcodec!=none][acodec!=none]/b[height<=720]/bv*[height<=480]/b/bv*"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)



class MediaAnalysisError(Exception):
    """A problem the model should relay/handle (with guidance attached)."""


def _pip_hint(packages: str) -> str:
    return f'"{sys.executable}" -m pip install -U {packages}'


# ---------------------------------------------------------------- helpers


def _fmt_ts(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _parse_ts(ts: str) -> float:
    parts = ts.strip().replace(",", ".").split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


_VTT_CUE = re.compile(r"^(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s*-->\s*((\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})")
_TAGS = re.compile(r"<[^>]+>")


def parse_vtt(text: str) -> list:
    """WebVTT/SRT -> [(start_seconds, text)], collapsing the rolling
    duplicate lines YouTube's auto-captions are full of."""
    segments: list = []
    start: Optional[float] = None
    buf: list = []
    seen_recent: list = []

    def flush():
        # Line by line, not cue by cue: YouTube's rolling auto-captions
        # show the previous line again on top of each new one, so a cue's
        # lines are emitted only if they weren't among the last few.
        nonlocal buf
        if start is not None:
            for raw_line in buf:
                line = re.sub(r"\s+", " ", raw_line).strip()
                if line and line not in seen_recent:
                    segments.append((start, line))
                    seen_recent.append(line)
                    del seen_recent[:-4]
        buf = []

    for raw in text.splitlines():
        line = raw.strip("﻿").strip()
        m = _VTT_CUE.match(line)
        if m:
            flush()
            start = _parse_ts(line.split("-->")[0])
            continue
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")) or line.isdigit():
            if not line:
                flush()
            continue
        clean = _TAGS.sub("", line).replace("&nbsp;", " ").replace("&amp;", "&").strip()
        if clean and (not buf or buf[-1] != clean):
            buf.append(clean)
    flush()
    # Some captioners grow a line word by word ("today we" -> "today we look");
    # keep only the new words each time.
    merged: list = []
    for t, line in segments:
        if merged and line.startswith(merged[-1][1]) and len(line) > len(merged[-1][1]):
            line = line[len(merged[-1][1]):].strip()
        if not line or (merged and line == merged[-1][1]):
            continue
        merged.append((t, line))
    return merged


def group_transcript(segments: list, block_seconds: float = 30.0) -> str:
    """[(t, text)] -> '[mm:ss] text ...' paragraphs, one per ~30s."""
    out, block, block_start = [], [], None
    for t, text in segments:
        if block_start is None:
            block_start = t
        if t - block_start >= block_seconds and block:
            out.append(f"[{_fmt_ts(block_start)}] " + " ".join(block))
            block, block_start = [], t
        block.append(text)
    if block:
        out.append(f"[{_fmt_ts(block_start or 0)}] " + " ".join(block))
    return "\n".join(out)


def frame_times_for(duration: Optional[float], count: int) -> list:
    if not duration or duration <= 0 or count <= 0:
        return [0.0] if count > 0 else []
    if duration < 4:
        return [duration / 2]
    count = min(count, MAX_FRAMES)
    lo, hi = duration * 0.04, duration * 0.96
    if count == 1:
        return [duration / 2]
    step = (hi - lo) / (count - 1)
    return [round(lo + i * step, 2) for i in range(count)]


def _slug(info: dict) -> str:
    raw = f"{info.get('extractor_key') or info.get('extractor') or 'site'}-{info.get('id') or 'item'}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)[:80]


def prune_old(now: Optional[float] = None) -> int:
    if not ANALYSIS_DIR.is_dir():
        return 0
    cutoff = (now or time.time()) - KEEP_DAYS * 86400
    removed = 0
    for d in ANALYSIS_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------- yt-dlp


def _yt_dlp():
    try:
        import yt_dlp  # noqa: F401
        return yt_dlp
    except ImportError:
        raise MediaAnalysisError(
            "yt-dlp isn't installed, so links can't be fetched yet. Install it (one time, free) with: "
            + _pip_hint("yt-dlp faster-whisper")
            + " -- then try again. Until then, open the link with the browser tools instead."
        ) from None


def _base_opts(workdir: Optional[Path] = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "restrictfilenames": True,
        "playlist_items": "1-10",
    }
    if workdir is not None:
        opts["outtmpl"] = str(workdir / "%(id)s.%(ext)s")
        opts["paths"] = {"home": str(workdir)}
    if config.MEDIA_COOKIES_FILE:
        opts["cookiefile"] = config.MEDIA_COOKIES_FILE
    elif config.MEDIA_COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (config.MEDIA_COOKIES_FROM_BROWSER,)
    return opts


def _explain_fetch_error(url: str, exc: Exception) -> str:
    msg = str(exc).replace("\n", " ")
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)[:400]
    lower = msg.lower()
    hints = []
    if any(k in lower for k in ("login", "sign in", "cookies", "private", "rate-limit", "rate limit", "not available")):
        hints.append(
            "the site wants a logged-in session -- set MEDIA_COOKIES_FROM_BROWSER (e.g. firefox) or "
            "MEDIA_COOKIES_FILE in .env, or just open it with the browser tools, which have your logins"
        )
    if any(k in lower for k in ("unsupported url", "no video", "no media", "there is no video")):
        hints.append("it may be a text or image-only post: open it with the browser tools and read it there")
    if "unable to extract" in lower or "extractor" in lower:
        hints.append("yt-dlp may be outdated for this site: update it with " + _pip_hint("yt-dlp") + " and retry once")
    if not hints:
        hints.append("try the browser tools on the link instead")
    return f"Couldn't fetch {url}: {msg}. Next step: " + "; ".join(hints) + "."


def _extract(url: str) -> dict:
    yt_dlp = _yt_dlp()
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 -- yt-dlp raises many types
        raise MediaAnalysisError(_explain_fetch_error(url, exc)) from None


def _has_video(info: dict) -> bool:
    fmts = info.get("formats") or []
    if fmts:
        return any((f.get("vcodec") or "none") != "none" for f in fmts)
    return bool(info.get("duration"))


def _has_audio(info: dict) -> bool:
    fmts = info.get("formats") or []
    if fmts:
        return any((f.get("acodec") or "none") != "none" for f in fmts) or _has_video(info)
    return bool(info.get("duration"))


def _download_captions_and_thumb(url: str, info: dict, workdir: Path) -> None:
    yt_dlp = _yt_dlp()
    langs = ["en", "en-orig", "en.*"]
    if info.get("language") and not str(info["language"]).startswith("en"):
        langs.append(str(info["language"]))
    opts = _base_opts(workdir)
    opts.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "vtt/srt/best",
            "writethumbnail": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 -- captions are best-effort
        logger.warning("caption/thumbnail download failed for %s: %s", url, exc)


def _pick_caption_file(workdir: Path) -> Optional[Path]:
    files = [Path(p) for p in glob.glob(str(workdir / "*.vtt")) + glob.glob(str(workdir / "*.srt"))]
    if not files:
        return None

    def rank(p: Path) -> tuple:
        name = p.name.lower()
        is_en = ".en." in name or ".en-" in name
        is_orig = "-orig" in name
        return (0 if is_en and not is_orig else 1 if is_en else 2, len(name))

    return sorted(files, key=rank)[0]


def _download_media(url: str, workdir: Path) -> Optional[Path]:
    existing = [p for p in workdir.glob("media.*") if p.suffix not in (".part", ".ytdl")]
    if existing:
        return existing[0]
    yt_dlp = _yt_dlp()
    opts = _base_opts(workdir)
    opts.update({"format": _SMALL_FORMAT, "outtmpl": str(workdir / "media.%(ext)s")})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("media download failed for %s: %s", url, exc)
        return None
    found = [p for p in workdir.glob("media.*") if p.suffix not in (".part", ".ytdl")]
    return found[0] if found else None


# ---------------------------------------------------------------- transcription


def transcribe(path: Path) -> "tuple[list, str]":
    """Local Whisper. Returns (segments, description). Raises
    MediaAnalysisError with an install hint if no engine is installed."""
    name = config.MEDIA_WHISPER_MODEL
    # Shared loader (backend/stt.py) -- the desktop app's voice input uses
    # the same cache, so one model in memory serves both when the names match.
    try:
        kind, model = stt.get_model(name)
    except stt.STTUnavailable:
        raise MediaAnalysisError(
            "no captions on this video and no local transcriber installed. Install one (free, "
            "offline) with: " + _pip_hint("faster-whisper")
            + " (if that fails on this Python version: " + _pip_hint("openai-whisper") + ")"
        ) from None
    if kind == "faster-whisper":
        segs, info = model.transcribe(str(path), beam_size=1, vad_filter=True)
        segments = [(s.start, s.text.strip()) for s in segs if s.text.strip()]
        return segments, f"local Whisper ({name}), language: {getattr(info, 'language', '?')}"
    result = model.transcribe(str(path), fp16=False)
    segments = [(s["start"], s["text"].strip()) for s in result.get("segments", []) if s["text"].strip()]
    return segments, f"local Whisper ({name}), language: {result.get('language', '?')}"


# ---------------------------------------------------------------- frames


def extract_frames(video: Path, times: list, workdir: Path) -> list:
    """Returns [(t, path)] for each frame that could be grabbed."""
    out = []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        for i, t in enumerate(times, 1):
            dest = workdir / f"frame_{int(t * 1000):08d}.jpg"
            if not dest.exists():
                try:
                    subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                         "-frames:v", "1", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "4", "-y", str(dest)],
                        timeout=45, check=False, capture_output=True, creationflags=_NO_WINDOW,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    logger.warning("ffmpeg frame %s failed: %s", t, exc)
            if dest.exists() and dest.stat().st_size > 0:
                out.append((t, dest))
        return out
    try:
        import av  # PyAV ships with faster-whisper
    except ImportError:
        return out
    try:
        with av.open(str(video)) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                return out
            for t in times:
                dest = workdir / f"frame_{int(t * 1000):08d}.jpg"
                if not dest.exists():
                    container.seek(int(t / stream.time_base), stream=stream)
                    frame = next(container.decode(stream), None)
                    if frame is None:
                        continue
                    img = frame.to_image()
                    if img.width > FRAME_WIDTH:
                        img = img.resize((FRAME_WIDTH, int(img.height * FRAME_WIDTH / img.width)))
                    img.convert("RGB").save(dest, "JPEG", quality=82)
                out.append((t, dest))
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyAV frame extraction failed: %s", exc)
    return out


# ---------------------------------------------------------------- main entry


def _meta_lines(info: dict) -> list:
    lines = []
    site = info.get("extractor_key") or info.get("extractor") or "?"
    who = info.get("uploader") or info.get("channel") or info.get("creator") or info.get("uploader_id")
    date = info.get("upload_date")
    if date and len(str(date)) == 8:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    bits = [site]
    if who:
        bits.append(str(who))
    if date:
        bits.append(str(date))
    if info.get("duration"):
        bits.append("length " + _fmt_ts(info["duration"]))
    for key, label in (("view_count", "views"), ("like_count", "likes"), ("comment_count", "comments")):
        if isinstance(info.get(key), int):
            bits.append(f"{info[key]:,} {label}")
    lines.append("Source: " + " | ".join(bits))
    lines.append("URL: " + str(info.get("webpage_url") or info.get("original_url") or ""))
    if info.get("title"):
        lines.append("Title: " + str(info["title"]))
    tags = info.get("tags") or []
    if tags:
        lines.append("Tags: " + ", ".join(str(t) for t in tags[:25]))
    chapters = info.get("chapters") or []
    if chapters:
        lines.append("Chapters: " + "; ".join(f"{_fmt_ts(c.get('start_time', 0))} {c.get('title', '')}" for c in chapters[:30]))
    return lines


def analyze(url: str, frames: int = 6, frame_times: Optional[list] = None) -> dict:
    """Blocking. Returns {"text": tool result text, "report": path, ...}."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise MediaAnalysisError("Need a full http(s) link to a post or video.")
    frames = max(0, min(int(frames if frames is not None else 6), MAX_FRAMES))

    info = _extract(url)
    entries = [e for e in (info.get("entries") or []) if e] if info.get("_type") in ("playlist", "multi_video") else []
    main = entries[0] if entries else info
    if entries and not main.get("formats"):
        # A carousel/multi-item post: re-extract the first item on its own.
        try:
            main = _extract(main.get("webpage_url") or main.get("url") or url)
        except MediaAnalysisError:
            pass
    item_url = main.get("webpage_url") or url

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    workdir = ANALYSIS_DIR / _slug(main)
    workdir.mkdir(parents=True, exist_ok=True)
    cache_path = workdir / "analysis.json"
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}

    duration = main.get("duration")
    notes: list = []

    # --- what's being said ---
    segments = [tuple(s) for s in cache.get("segments", [])]
    transcript_source = cache.get("transcript_source", "")
    if not segments:
        _download_captions_and_thumb(item_url, main, workdir)
        cap = _pick_caption_file(workdir)
        if cap:
            try:
                segments = parse_vtt(cap.read_text(encoding="utf-8", errors="replace"))
                transcript_source = f"platform captions ({cap.name.split('.')[-2] if cap.name.count('.') >= 2 else 'captions'})"
            except OSError:
                segments = []
    media_file: Optional[Path] = None
    if not segments and _has_audio(main):
        if duration and duration > config.MEDIA_MAX_TRANSCRIBE_MINUTES * 60:
            notes.append(
                f"No captions, and at {_fmt_ts(duration)} it's longer than the {config.MEDIA_MAX_TRANSCRIBE_MINUTES}-minute "
                "transcription limit (MEDIA_MAX_TRANSCRIBE_MINUTES) -- transcript skipped."
            )
        else:
            media_file = _download_media(item_url, workdir)
            if media_file is None:
                notes.append("Couldn't download the media to transcribe it.")
            else:
                try:
                    segments, transcript_source = transcribe(media_file)
                except MediaAnalysisError as exc:
                    notes.append(str(exc))
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Transcription failed: {exc}")

    # --- what's being shown ---
    frame_list: list = []
    wanted = [float(t) for t in (frame_times or [])] or (frame_times_for(duration, frames) if frames else [])
    if wanted and _has_video(main):
        if duration and duration > config.MEDIA_MAX_DOWNLOAD_MINUTES * 60 and not frame_times:
            notes.append(
                f"Video is longer than MEDIA_MAX_DOWNLOAD_MINUTES ({config.MEDIA_MAX_DOWNLOAD_MINUTES}) -- no frames "
                "grabbed; ask for specific moments with frame_times if needed."
            )
        else:
            media_file = media_file or _download_media(item_url, workdir)
            if media_file:
                if duration:
                    wanted = [min(max(0.0, t), max(0.0, duration - 0.5)) for t in wanted]
                frame_list = extract_frames(media_file, wanted, workdir)
                if not frame_list:
                    notes.append("Couldn't extract frames (install ffmpeg, or PyAV via faster-whisper).")
    thumbs = sorted(p for p in workdir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and not p.name.startswith("frame_"))

    # --- cache + report ---
    try:
        cache_path.write_text(json.dumps({"segments": segments, "transcript_source": transcript_source}), encoding="utf-8")
    except OSError:
        pass
    transcript = group_transcript(segments) if segments else ""
    description = (main.get("description") or info.get("description") or "").strip()

    report = workdir / "analysis.md"
    meta = _meta_lines(main)
    report_parts = ["# Media analysis", "", *meta, "", "## Caption / description", description or "(none)", ""]
    if entries and len(entries) > 1:
        report_parts += [f"## Post has {len(entries)} items", *[f"- {e.get('title') or e.get('id')}" for e in entries[:10]], ""]
    report_parts += ["## Transcript" + (f" ({transcript_source})" if transcript_source else ""), transcript or "(none)", ""]
    report_parts += ["## Frames", *[f"- {_fmt_ts(t)} {p}" for t, p in frame_list], *[f"- thumbnail {p}" for p in thumbs[:1]]]
    if notes:
        report_parts += ["", "## Notes", *[f"- {n}" for n in notes]]
    try:
        report.write_text("\n".join(report_parts), encoding="utf-8")
    except OSError:
        pass

    # --- compact tool result ---
    text = list(meta)
    if entries and len(entries) > 1:
        text.append(f"(Post has {len(entries)} items -- analyzed the first.)")
    if description:
        d = description if len(description) <= DESCRIPTION_CHARS else description[:DESCRIPTION_CHARS] + " ..."
        text += ["", "Caption/description:", d]
    if transcript:
        t = transcript
        if len(t) > TRANSCRIPT_RETURN_CHARS:
            t = t[:TRANSCRIPT_RETURN_CHARS] + f"\n... (transcript truncated -- full text in {report}; Grep it for specifics)"
        text += ["", f"Transcript ({transcript_source}):", t]
    else:
        text += ["", "Transcript: none available."]
    if frame_list or thumbs:
        text += ["", "Frames -- open the ones you need with Read to SEE the video (screens, UI, text on screen):"]
        text += [f"- {_fmt_ts(t)}  {p}" for t, p in frame_list]
        if thumbs:
            text.append(f"- thumbnail  {thumbs[0]}")
    if notes:
        text += ["", "Notes:", *[f"- {n}" for n in notes]]
    text += ["", f"Full report: {report}"]
    return {
        "text": "\n".join(text),
        "report": str(report),
        "frames": [str(p) for _, p in frame_list],
        "transcript_chars": len(transcript),
        "has_transcript": bool(transcript),
    }


def parse_frame_times(value: Any) -> list:
    """'12, 1:05, 90' -> [12.0, 65.0, 90.0]"""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,\s]+", str(value).strip())
    out = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        try:
            out.append(_parse_ts(item) if ":" in item else float(item))
        except ValueError:
            continue
    return out[:MAX_FRAMES]


# ---------------------------------------------------------------- MCP tool

from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: E402


@tool(
    "analyze_media_link",
    "Analyze a social media post or video from its link (YouTube, TikTok, Instagram, X/Twitter, Facebook, "
    "LinkedIn, Reddit, Vimeo, and ~1800 more sites): metadata, the caption/description, the full spoken "
    "transcript (platform captions or local Whisper), and key frames saved as images you open with Read to "
    "see what's on screen. Pass frame_times (e.g. \"12, 1:05\") to grab exact moments after reading the "
    "transcript; results are cached, so a second call is cheap.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Link to the post or video."},
            "frames": {"type": "integer", "description": "How many evenly spaced frames to grab (0-12, default 6)."},
            "frame_times": {"type": "string", "description": "Optional exact moments, comma-separated seconds or mm:ss."},
        },
        "required": ["url"],
    },
)
async def analyze_media_link(args: dict) -> dict:
    try:
        result = await asyncio.to_thread(
            analyze, args.get("url", ""), args.get("frames", 6), parse_frame_times(args.get("frame_times"))
        )
        text = result["text"]
    except MediaAnalysisError as exc:
        text = str(exc)
    except Exception as exc:  # noqa: BLE001 -- never crash the turn
        logger.exception("analyze_media_link failed")
        text = f"Analysis failed unexpectedly: {exc}. Try the browser tools on the link instead."
    return {"content": [{"type": "text", "text": text}]}


MEDIA_ANALYSIS_TOOLS = ["analyze_media_link"]
media_analysis_server = create_sdk_mcp_server(name="media_analysis", version="1.0.0", tools=[analyze_media_link])

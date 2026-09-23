"""
Local knowledge base for Tutor mode (layer 1 of 3 -- see tutor.py's
module docstring for the full three-layer design, and the README's
Technical Tutor section for setup). Point it at a book or PDF on this
machine (via the Knowledge Base panel in the UI's Tutor mode, or
POST /api/tutor/knowledge-base/ingest) and it's chunked, embedded
locally, and stored so the tutor can retrieve the most relevant
passages before answering, grounding its answers in the user's own
reference material instead of training data alone.

Embeddings run fully locally via fastembed (ONNX runtime under the
hood, no torch, no API key, no per-call cost, no internet needed after
the model's one-time download) -- default model BAAI/bge-small-en-v1.5,
384 dimensions. This holds to the same "genuinely free, not a trial"
bar the rest of Neural's integrations were held to this session (see
stock_footage.py's docstring for that whole saga) -- a model that runs
on this machine can't run out of free credits or turn out to have been
a signup trial in disguise.

Storage is plain SQLite (tutor_knowledge/kb.sqlite3): one `sources` row
per ingested file, one `chunks` row per chunk with its embedding stored
as a raw float32 BLOB. Retrieval is brute-force cosine similarity over
every chunk, in Python/numpy -- for a personal library (hundreds to a
few thousand chunks), that's fast enough with zero extra
infrastructure (no hnswlib/Chroma server, no ANN index to maintain).

Setup: `pip install fastembed pypdf numpy` (see backend/requirements.txt).
Both this module's tools degrade to a plain "not set up yet" message
if those packages aren't installed, rather than crashing Tutor mode
entirely -- so Tutor mode's other two layers (docs cache, session
memory) work fine before you ever touch this one.
"""

import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger("neural.knowledge_base")

KB_DIR = Path(__file__).resolve().parent.parent / "tutor_knowledge"
DB_PATH = KB_DIR / "kb.sqlite3"

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150

try:
    import numpy as np
    from fastembed import TextEmbedding

    KB_DEPS_AVAILABLE = True
except ImportError:
    KB_DEPS_AVAILABLE = False

try:
    from pypdf import PdfReader

    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

_NOT_SET_UP = (
    "The knowledge base isn't set up yet -- the required packages aren't installed. "
    "Relay this plainly to the user rather than trying to work around it: "
    "`pip install fastembed pypdf numpy` in the same Python environment Neural runs "
    "in, then restart the server. The first ingestion after that downloads a small "
    "local embedding model (~130MB, one-time)."
)

_embedder: "TextEmbedding | None" = None


def _get_embedder() -> "TextEmbedding":
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _embed(texts: list[str]) -> list["np.ndarray"]:
    return list(_get_embedder().embed(texts))


def _connect() -> sqlite3.Connection:
    KB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            path TEXT,
            title TEXT,
            added_at TEXT,
            chunk_count INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            chunk_index INTEGER,
            text TEXT,
            embedding BLOB
        )"""
    )
    return conn


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _extract_text(path: Path) -> tuple[str, str]:
    """Returns (text, title)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if not PDF_SUPPORT:
            raise ValueError("pypdf isn't installed -- pip install pypdf")
        reader = PdfReader(str(path))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n\n".join(pages)
        title = None
        try:
            title = (reader.metadata or {}).get("/Title")
        except Exception:  # noqa: BLE001 -- metadata access is best-effort
            pass
        return text, (title or path.stem)
    if suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        first_line = next((l.strip("# ").strip() for l in text.splitlines() if l.strip()), "")
        return text, (first_line[:120] or path.stem)
    raise ValueError(f"unsupported file type '{suffix}' -- only .pdf, .txt, and .md are supported")


def _chunk_text(text: str) -> list[str]:
    """Simple, robust chunking: split on paragraph breaks, then greedily
    pack paragraphs into ~CHUNK_CHARS-sized chunks, carrying a bit of
    trailing text forward as overlap so a fact near a chunk boundary
    isn't split away from its context entirely."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > CHUNK_CHARS:
            chunks.append(current)
            # carry the tail of the previous chunk forward as overlap
            current = current[-CHUNK_OVERLAP:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para) if current else para
    if current:
        chunks.append(current)

    # A single paragraph longer than CHUNK_CHARS (common in a PDF with no
    # paragraph breaks at all) still needs to be split further.
    final: list[str] = []
    for c in chunks:
        if len(c) <= CHUNK_CHARS * 1.5:
            final.append(c)
            continue
        for i in range(0, len(c), CHUNK_CHARS - CHUNK_OVERLAP):
            final.append(c[i : i + CHUNK_CHARS])
    return final


def _ingest_file(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    text, title = _extract_text(path)
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError(f"no extractable text in {path.name}")

    vectors = _embed(chunks)

    # Re-ingesting the same path replaces the old source/chunks rather
    # than duplicating them.
    existing = conn.execute("SELECT id FROM sources WHERE path = ?", (str(path),)).fetchone()
    if existing:
        conn.execute("DELETE FROM chunks WHERE source_id = ?", (existing[0],))
        conn.execute("DELETE FROM sources WHERE id = ?", (existing[0],))

    source_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO sources (id, path, title, added_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
        (source_id, str(path), title, datetime.now(timezone.utc).isoformat(), len(chunks)),
    )
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        conn.execute(
            "INSERT INTO chunks (source_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
            (source_id, i, chunk, np.asarray(vec, dtype=np.float32).tobytes()),
        )
    conn.commit()
    return {"id": source_id, "title": title, "path": str(path), "chunk_count": len(chunks)}


async def ingest_path(path_str: str) -> dict[str, Any]:
    """Ingest a single file, or every supported file directly inside a
    folder (non-recursive -- a book collection is usually one flat
    folder, and recursing risks silently pulling in unrelated files)."""
    if not KB_DEPS_AVAILABLE:
        raise RuntimeError(_NOT_SET_UP)

    path = Path(path_str).expanduser()
    if not path.exists():
        raise ValueError(f"no such file or folder: {path_str}")

    conn = _connect()
    try:
        if path.is_dir():
            targets = sorted(
                p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".pdf", ".txt", ".md")
            )
            if not targets:
                raise ValueError(f"no .pdf/.txt/.md files found directly inside {path_str}")
            results = []
            errors = []
            for p in targets:
                try:
                    results.append(_ingest_file(conn, p))
                except Exception as e:  # noqa: BLE001 -- one bad file shouldn't abort the whole folder
                    errors.append(f"{p.name}: {e}")
            return {"ingested": results, "errors": errors, "count": len(results)}
        else:
            result = _ingest_file(conn, path)
            return {"ingested": [result], "errors": [], "count": 1}
    finally:
        conn.close()


def list_sources() -> list[dict[str, Any]]:
    if not KB_DEPS_AVAILABLE or not DB_PATH.exists():
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, path, title, added_at, chunk_count FROM sources ORDER BY added_at DESC"
        ).fetchall()
        return [
            {"id": r[0], "path": r[1], "title": r[2], "added_at": r[3], "chunk_count": r[4]} for r in rows
        ]
    finally:
        conn.close()


def delete_source(source_id: str) -> bool:
    if not KB_DEPS_AVAILABLE or not DB_PATH.exists():
        return False
    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            return False
        conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def _search(query: str, num_results: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT c.source_id, c.chunk_index, c.text, c.embedding, s.title, s.path "
            "FROM chunks c JOIN sources s ON s.id = c.source_id"
        ).fetchall()
        if not rows:
            return []

        query_vec = _embed([query])[0].astype(np.float32)
        query_norm = np.linalg.norm(query_vec) or 1.0

        scored = []
        for source_id, chunk_index, text, blob, title, path in rows:
            vec = np.frombuffer(blob, dtype=np.float32)
            denom = (np.linalg.norm(vec) or 1.0) * query_norm
            score = float(np.dot(query_vec, vec) / denom)
            scored.append((score, title, path, chunk_index, text))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {"score": s, "title": title, "path": path, "chunk_index": ci, "text": text}
            for s, title, path, ci, text in scored[:num_results]
        ]
    finally:
        conn.close()


@tool(
    "search_knowledge_base",
    "Search the user's own ingested books/PDFs for passages relevant to a topic or "
    "question. ALWAYS call this before answering a technical question from general "
    "knowledge if any sources have been ingested (check list_knowledge_base_sources "
    "if unsure) -- ground your answer in the user's own reference material and say "
    "which source it came from. If nothing's been ingested, or the search comes up "
    "empty/irrelevant, say so plainly and answer from general knowledge instead.",
    {"query": str, "num_results": int},
)
async def search_knowledge_base(args: dict) -> dict[str, Any]:
    if not KB_DEPS_AVAILABLE:
        return _text(_NOT_SET_UP)

    query = (args.get("query") or "").strip()
    if not query:
        return _text("Need a topic or question to search the knowledge base for.")

    try:
        num_results = max(1, min(int(args.get("num_results") or 5), 15))
    except (TypeError, ValueError):
        num_results = 5

    if not list_sources():
        return _text("No books or PDFs have been added to the knowledge base yet.")

    try:
        results = _search(query, num_results)
    except Exception as e:  # noqa: BLE001 -- surface plainly rather than crash the turn
        logger.error("Knowledge base search failed: %s", e)
        return _text(f"Knowledge base search failed: {e}")

    if not results:
        return _text(f'No relevant passages found for "{query}".')

    lines = [f'{len(results)} relevant passage(s) for "{query}":']
    for r in results:
        lines.append(
            f"\n--- {r['title']} (chunk {r['chunk_index']}, relevance {r['score']:.2f}) ---\n{r['text']}"
        )
    return _text("\n".join(lines))


@tool(
    "list_knowledge_base_sources",
    "List every book/PDF currently ingested into the knowledge base, with how many "
    "chunks each produced and when it was added.",
    {},
)
async def list_knowledge_base_sources(args: dict) -> dict[str, Any]:
    if not KB_DEPS_AVAILABLE:
        return _text(_NOT_SET_UP)
    sources = list_sources()
    if not sources:
        return _text("No books or PDFs have been added to the knowledge base yet.")
    lines = [
        f"- {s['title']} -- {s['chunk_count']} chunks -- added {s['added_at'][:10]} -- {s['path']}"
        for s in sources
    ]
    return _text("Knowledge base sources:\n" + "\n".join(lines))


KNOWLEDGE_BASE_TOOLS = ["search_knowledge_base", "list_knowledge_base_sources"]
knowledge_base_server = create_sdk_mcp_server(
    name="tutor_kb", version="1.0.0", tools=[search_knowledge_base, list_knowledge_base_sources]
)

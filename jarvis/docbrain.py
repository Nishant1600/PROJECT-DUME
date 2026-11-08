"""Offline document brain for JARVIS (README roadmap: "point JARVIS at a folder ... fully offline").

Point it at a folder of notes and documents; it answers from them without touching the
network. It builds a small keyword index (BM25, pure Python) over the text in that folder and
returns the most relevant passages, which the brain then reads to answer. Nothing leaves the
machine: no embeddings service, no upload.

Plain text, Markdown, and code files always work. PDFs and Word docs work too *if* a reader is
installed (pypdf for .pdf, python-docx for .docx); if not, those files are skipped with a note
and everything else still indexes.

    python -m jarvis.docbrain index "C:/Users/me/Notes"
    python -m jarvis.docbrain ask "what did I note about transformers" --folder "C:/Users/me/Notes"
    python -m jarvis.docbrain folders
    python -m jarvis.docbrain forget "C:/Users/me/Notes"

The index lives under runtime/docbrain/ and refreshes automatically when files change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

from .config import RUNTIME_DIR

INDEX_DIR = RUNTIME_DIR / "docbrain"

TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".log", ".csv",
             ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h",
             ".html", ".css", ".json", ".yaml", ".yml", ".tex"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}

MAX_FILE_BYTES = 5_000_000      # skip anything bigger (likely not notes)
MAX_CHUNKS = 6000               # keep the index bounded
CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was",
         "were", "be", "as", "at", "by", "it", "this", "that", "with", "from", "but", "not",
         "we", "you", "i", "my", "me", "do", "did", "what", "which", "about", "into", "if"}


# --------------------------------------------------------------------------- extraction

def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2 and w not in _STOP]


def _extract(path: Path, notes: list[str]) -> str:
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXTS:
            return path.read_text(encoding="utf-8", errors="ignore")
        if ext in PDF_EXTS:
            return _extract_pdf(path, notes)
        if ext in DOCX_EXTS:
            return _extract_docx(path, notes)
    except Exception as e:
        notes.append(f"skip {path.name}: {e}")
    return ""


def _extract_pdf(path: Path, notes: list[str]) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            notes.append(f"skip {path.name}: PDFs need `pip install pypdf`")
            return ""
    reader = PdfReader(str(path))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


def _extract_docx(path: Path, notes: list[str]) -> str:
    try:
        import docx  # python-docx
    except Exception:
        notes.append(f"skip {path.name}: .docx needs `pip install python-docx`")
        return ""
    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def _chunks(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        end = min(i + CHUNK_CHARS, len(text))
        # try to break on a paragraph/sentence boundary
        if end < len(text):
            br = text.rfind("\n\n", i, end)
            if br == -1:
                br = text.rfind(". ", i, end)
            if br > i + CHUNK_CHARS // 2:
                end = br + 1
        chunk = text[i:end].strip()
        if len(chunk) >= 40:
            out.append(chunk)
        if end >= len(text):
            break
        i = max(i + 1, end - CHUNK_OVERLAP)   # always moves forward
    return out


# --------------------------------------------------------------------------- index

def _slug(folder: Path) -> str:
    h = hashlib.sha1(str(folder).lower().encode("utf-8")).hexdigest()[:12]
    return f"{folder.name or 'root'}-{h}"


def _index_path(folder: Path) -> Path:
    return INDEX_DIR / f"{_slug(folder)}.json"


def _manifest(folder: Path) -> dict[str, float]:
    man = {}
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (TEXT_EXTS | PDF_EXTS | DOCX_EXTS):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            continue
        if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".venv", ".git")
               for part in p.parts):
            continue
        man[str(p)] = st.st_mtime
    return man


def index(folder: str | Path) -> dict:
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        return {"error": f"not a folder: {folder}"}
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    man = _manifest(folder)
    chunks: list[dict] = []
    notes: list[str] = []
    for fp in sorted(man):
        if len(chunks) >= MAX_CHUNKS:
            notes.append(f"stopped at {MAX_CHUNKS} passages (folder is large)")
            break
        text = _extract(Path(fp), notes)
        for ch in _chunks(text):
            chunks.append({"file": fp, "text": ch})
            if len(chunks) >= MAX_CHUNKS:
                break
    data = {"folder": str(folder), "manifest": man, "chunks": chunks, "notes": notes,
            "files": len(man)}
    _index_path(folder).write_text(json.dumps(data), encoding="utf-8")
    return {"folder": str(folder), "files": len(man), "passages": len(chunks), "notes": notes}


def _load(folder: Path) -> dict | None:
    p = _index_path(folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_stale(data: dict, folder: Path) -> bool:
    return data.get("manifest", {}) != _manifest(folder)


# --------------------------------------------------------------------------- BM25 search

def search(folder: str | Path, query: str, k: int = 5, auto: bool = True) -> dict:
    folder = Path(folder).expanduser().resolve()
    data = _load(folder)
    if auto and (data is None or _is_stale(data, folder)):
        index(folder)
        data = _load(folder)
    if not data:
        return {"error": f"couldn't index {folder}", "hits": []}
    chunks = data["chunks"]
    if not chunks:
        return {"error": "no readable documents in that folder", "hits": [], "notes": data.get("notes", [])}

    terms = _tokenize(query)
    if not terms:
        return {"hits": [], "notes": data.get("notes", [])}

    tokenized = [_tokenize(c["text"]) for c in chunks]
    N = len(tokenized)
    avgdl = sum(len(t) for t in tokenized) / N or 1.0
    df: dict[str, int] = {}
    for toks in tokenized:
        for w in set(toks):
            df[w] = df.get(w, 0) + 1

    k1, b = 1.5, 0.75
    scored = []
    qset = set(terms)
    for i, toks in enumerate(tokenized):
        if not any(w in toks for w in qset):
            continue
        dl = len(toks) or 1
        score = 0.0
        for w in qset:
            tf = toks.count(w)
            if not tf:
                continue
            idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scored.append((score, i))
    scored.sort(reverse=True)

    hits = []
    for score, i in scored[:k]:
        c = chunks[i]
        snippet = _snippet(c["text"], qset)
        hits.append({"file": Path(c["file"]).name, "path": c["file"],
                     "score": round(score, 2), "text": snippet})
    return {"hits": hits, "files": data.get("files"), "passages": N,
            "notes": data.get("notes", [])}


def _snippet(text: str, qset: set[str], width: int = 320) -> str:
    low = text.lower()
    pos = min((low.find(w) for w in qset if low.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    snip = text[start:start + width].strip()
    if start > 0:
        snip = "…" + snip
    if start + width < len(text):
        snip = snip + "…"
    return " ".join(snip.split())


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.docbrain", description="Offline Q&A over a folder")
    sub = ap.add_subparsers(dest="cmd")

    i = sub.add_parser("index", help="(re)index a folder")
    i.add_argument("folder")

    a = sub.add_parser("ask", help="find the passages that answer a question")
    a.add_argument("query")
    a.add_argument("--folder", required=True)
    a.add_argument("--k", type=int, default=5)
    a.add_argument("--json", action="store_true")

    sub.add_parser("folders", help="list indexed folders")
    f = sub.add_parser("forget", help="delete a folder's index")
    f.add_argument("folder")

    args = ap.parse_args(argv)

    if args.cmd == "index":
        r = index(args.folder)
        if r.get("error"):
            print(r["error"]); return 1
        print(f"Indexed {r['files']} file(s), {r['passages']} passage(s) from {r['folder']}.")
        for n in r.get("notes", []):
            print(f"  note: {n}")
        return 0

    if args.cmd == "folders":
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        any_ = False
        for p in INDEX_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                print(f"  {d.get('folder')} ({len(d.get('chunks', []))} passages)")
                any_ = True
            except (OSError, ValueError):
                continue
        if not any_:
            print("No folders indexed yet.")
        return 0

    if args.cmd == "forget":
        p = _index_path(Path(args.folder).expanduser().resolve())
        if p.exists():
            p.unlink(); print("Forgotten."); return 0
        print("That folder wasn't indexed."); return 1

    if args.cmd == "ask":
        r = search(args.folder, args.query, k=args.k)
        if args.json:
            print(json.dumps(r, indent=2)); return 0
        if r.get("error"):
            print(r["error"]); return 1
        if not r["hits"]:
            print(f"Nothing in that folder matches {args.query!r}."); return 1
        print(f"Top passages for {args.query!r}:\n")
        for h in r["hits"]:
            print(f"  [{h['file']}] {h['text']}\n")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

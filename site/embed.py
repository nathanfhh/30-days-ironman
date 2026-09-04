#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Embed the series (article chunks + graph nodes) with a paid API and write one JSON file.

Run by the author, who holds the key; the output is what the site work needs and
carries no credentials. Two providers, same output shape, so the downstream steps
(day×day similarity, chunk UMAP, edge health check) never care which one ran:

    OPENAI_API_KEY=… uv run site/embed.py --provider openai --article article.md
    GEMINI_API_KEY=… uv run site/embed.py --provider gemini --article article.md
    uv run site/embed.py --provider fake --article article.md      # no network, checks chunking
    GEMINI_API_KEY=… uv run site/embed.py --provider gemini-2 --article article.md --out embeddings-2.json
                                                                   # same chunks, newer model: feed both to embed_compare.py

Why chunks and not one vector per day: a day is 10–20k tokens, past every
embedding model's input limit, and a mean of chunk vectors is also what the
chunk-level plots need. Why the graph nodes too: comparing a node's description
with the chunks of the day it is linked to is the cheap audit of hand-labelled
edges. Why urllib instead of the vendors' SDKs: the two REST calls are three
fields each, and a stdlib script has no version to drift.

Pick one provider and keep it; vectors from different models do not share a space.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DAY_HEAD = re.compile(r"^# Day (\d+)｜(.*)$", re.MULTILINE)
SUB_HEAD = re.compile(r"^#{2,4} +(.*)$", re.MULTILINE)

PROVIDERS = {
    # model, default dims, max inputs per request
    "openai": ("text-embedding-3-large", 512, 64),
    "gemini": ("gemini-embedding-001", 768, 50),
    "gemini-2": ("gemini-embedding-2", 768, 50),
    "fake": ("fake-sha256", 64, 1000),
}
# which HTTP client each provider name uses; "gemini-2" is the same endpoint with a newer model
ENDPOINT = {
    "openai": "openai",
    "gemini": "gemini",
    "gemini-2": "gemini",
    "fake": "fake",
}


# ---------------------------------------------------------------- chunking
def split_days(md: str) -> list[tuple[int, str, int, str]]:
    """(day, title, offset, body) for each `# Day N｜` section, in file order."""
    heads = list(DAY_HEAD.finditer(md))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        out.append((int(h.group(1)), h.group(2).strip(), h.end(), md[h.end() : end]))
    return out


def chunk_day(body: str, base: int, max_chars: int, overlap: int) -> list[dict]:
    """Split on sub-headings first, then pack paragraphs up to max_chars.

    A paragraph longer than max_chars (a big code block) is sliced with overlap
    rather than dropped: the point is coverage of the text, not pretty chunks.
    """
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError(
            f"need 0 <= overlap < max_chars, got overlap={overlap}, max_chars={max_chars}"
        )
    # sections: (heading, start_offset_in_body, text)
    marks = list(SUB_HEAD.finditer(body))
    sections = []
    if not marks or marks[0].start() > 0:
        sections.append(("", 0, body[: marks[0].start()] if marks else body))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sections.append((m.group(1).strip(), m.end(), body[m.end() : end]))
    chunks = []
    for heading, off, text in sections:
        paras = [
            (m.start(), m.group(0)) for m in re.finditer(r"[^\n]+(?:\n[^\n]+)*", text)
        ]
        buf, buf_start = "", None

        # explicit arguments rather than closing over the loop variables: the
        # closure is only ever called inside the same iteration, but a reader
        # (and ruff's B023) cannot tell that from the definition
        def flush(heading: str, off: int) -> None:
            nonlocal buf, buf_start
            if buf.strip():
                chunks.append(
                    {
                        "heading": heading,
                        "start": base + off + buf_start,
                        "text": buf.strip(),
                    }
                )
            buf, buf_start = "", None

        for p_off, p in paras:
            if len(p) > max_chars:
                flush(heading, off)
                step = max_chars - overlap
                for k in range(0, len(p), step):
                    piece = p[k : k + max_chars]
                    chunks.append(
                        {
                            "heading": heading,
                            "start": base + off + p_off + k,
                            "text": piece.strip(),
                        }
                    )
                    if k + max_chars >= len(p):
                        break
                continue
            if buf and len(buf) + len(p) + 2 > max_chars:
                flush(heading, off)
            if not buf:
                buf_start = p_off
            buf = (buf + "\n\n" + p) if buf else p
        flush(heading, off)
    return chunks


def node_texts() -> list[dict]:
    """One text per graph node, built the way the panel shows it."""
    days = json.loads((DATA / "days.json").read_text(encoding="utf-8"))
    concepts = json.loads((DATA / "concepts.json").read_text(encoding="utf-8"))
    repo = json.loads((DATA / "repo.json").read_text(encoding="utf-8"))
    out = []
    for d in days:
        out.append(
            {
                "id": d["id"],
                "kind": "day",
                "text": f"Day {d['n']}｜{d['title']}\n{d.get('sub', '')}\n{d.get('summary', '')}",
            }
        )
    for c in concepts:
        out.append(
            {
                "id": c["id"],
                "kind": "concept",
                "text": f"{c['name']}\n{c.get('desc', '')}",
            }
        )
    for r in repo:
        out.append(
            {
                "id": r["id"],
                "kind": "repo",
                "text": f"{r['name']} ({r['path']})\n{r.get('desc', '')}",
            }
        )
    return out


# ---------------------------------------------------------------- providers
def _post(url: str, body: dict, headers: dict, tries: int = 4) -> dict:
    data = json.dumps(body).encode("utf-8")
    for attempt in range(tries):
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  HTTP {e.code}, retry in {wait}s: {detail}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"HTTP {e.code} from {url}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SystemExit(f"network error talking to {url}: {e}") from None
    raise AssertionError("unreachable")


def embed_openai(
    texts: list[str], model: str, dims: int, key: str
) -> list[list[float]]:
    r = _post(
        "https://api.openai.com/v1/embeddings",
        {"model": model, "input": texts, "dimensions": dims},
        {"Authorization": f"Bearer {key}"},
    )
    rows = sorted(r["data"], key=lambda x: x["index"])
    return [row["embedding"] for row in rows]


def embed_gemini(
    texts: list[str], model: str, dims: int, key: str
) -> list[list[float]]:
    r = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents",
        {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": "SEMANTIC_SIMILARITY",
                    "outputDimensionality": dims,
                }
                for t in texts
            ]
        },
        {"x-goog-api-key": key},
    )
    return [e["values"] for e in r["embeddings"]]


def embed_fake(texts: list[str], model: str, dims: int, key: str) -> list[list[float]]:
    # Deterministic pseudo-vectors from the text hash: exercises chunking and
    # the output format without a key or a network. Not meaningful as vectors.
    out = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        seed = struct.unpack("<Q", h[:8])[0]
        vals = []
        for _ in range(dims):
            seed = (seed * 6364136223846793005 + 1442695040888963407) % (1 << 64)
            vals.append((seed >> 11) / (1 << 53) - 0.5)
        out.append(vals)
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_key(node_id: str, text_sha: str) -> str:
    # Node vectors are keyed by id in the output and carry only a hash of the text
    # they were made from (the text itself is reproducible from site/data), so a
    # reuse hit requires both to match: same node, same wording.
    return f"\x00node:{node_id}:{text_sha}"


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 6) for x in v]


def embed_all(
    texts: list[str], provider: str, model: str, dims: int, batch: int, key: str
) -> list[list[float]]:
    fn = {"openai": embed_openai, "gemini": embed_gemini, "fake": embed_fake}[
        ENDPOINT[provider]
    ]
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        vecs = fn(part, model, dims, key)
        if len(vecs) != len(part):
            raise SystemExit(
                f"provider returned {len(vecs)} vectors for {len(part)} inputs"
            )
        out.extend(normalize(v) for v in vecs)
        print(f"  {min(i + batch, len(texts))}/{len(texts)}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- preview
def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def day_vectors(chunks: list[dict], vecs: list[list[float]]) -> dict[int, list[float]]:
    acc: dict[int, list[float]] = {}
    for c, v in zip(chunks, vecs):
        a = acc.setdefault(c["day"], [0.0] * len(v))
        for i, x in enumerate(v):
            a[i] += x
    return {d: normalize(v) for d, v in acc.items()}


def preview(chunks: list[dict], vecs: list[list[float]]) -> None:
    dv = day_vectors(chunks, vecs)
    days = sorted(dv)
    pairs = sorted(
        (
            (cosine(dv[a], dv[b]), a, b)
            for i, a in enumerate(days)
            for b in days[i + 1 :]
        ),
        reverse=True,
    )
    print("\n最相近的十對日子（chunk 平均向量的 cosine）：")
    for s, a, b in pairs[:10]:
        print(f"  Day {a:>2} ↔ Day {b:>2}  {s:.3f}")
    print("最不相近的三對：")
    for s, a, b in pairs[-3:]:
        print(f"  Day {a:>2} ↔ Day {b:>2}  {s:.3f}")


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", choices=PROVIDERS, required=True)
    ap.add_argument(
        "--article",
        type=Path,
        required=True,
        help="the full series markdown (`# Day N｜` per day)",
    )
    ap.add_argument("--out", type=Path, default=Path("embeddings.json"))
    ap.add_argument("--model", help="override the provider's default model")
    ap.add_argument(
        "--dims", type=int, help="override the default output dimensionality"
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=900,
        help="chunk size in characters (default 900 ≈ 1.2–1.5k tokens of zh-TW)",
    )
    ap.add_argument("--overlap", type=int, default=120)
    ap.add_argument(
        "--no-nodes", action="store_true", help="skip the site/data node texts"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="chunk and count, do not call the API"
    )
    ap.add_argument(
        "--reuse",
        type=Path,
        help="a previous embeddings.json: chunks whose text is unchanged keep their vector, only new or edited text is sent",
    )
    args = ap.parse_args(argv)
    # step = max_chars - overlap drives the slicing of oversized paragraphs; a
    # non-positive step would raise inside range() or silently drop text, so
    # refuse the combination at the door instead of half-way through the article.
    if args.max_chars <= 0 or args.overlap < 0 or args.overlap >= args.max_chars:
        print(
            f"--overlap must be 0 <= overlap < --max-chars (got overlap={args.overlap}, max-chars={args.max_chars})",
            file=sys.stderr,
        )
        return 2

    model, dims, batch = PROVIDERS[args.provider]
    model = args.model or model
    dims = args.dims or dims
    key = ""
    if args.provider != "fake" and not args.dry_run:
        env = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}[
            ENDPOINT[args.provider]
        ]
        key = os.environ.get(env, "")
        if not key:
            print(f"{env} is not set", file=sys.stderr)
            return 2

    md = args.article.read_text(encoding="utf-8")
    src_sha = sha(md)
    chunks: list[dict] = []
    for day, title, off, body in split_days(md):
        for k, c in enumerate(chunk_day(body, off, args.max_chars, args.overlap), 1):
            chunks.append(
                {
                    "id": f"d{day:02d}-{k:02d}",
                    "day": day,
                    "day_title": title,
                    **c,
                    "chars": len(c["text"]),
                }
            )
    if not chunks:
        print("no `# Day N｜` sections found in the article", file=sys.stderr)
        return 1
    nodes = [] if args.no_nodes else node_texts()
    total_chars = sum(c["chars"] for c in chunks) + sum(len(n["text"]) for n in nodes)
    days_seen = sorted({c["day"] for c in chunks})
    print(
        f"{len(chunks)} chunks over {len(days_seen)} days (Day {days_seen[0]}–{days_seen[-1]}), {len(nodes)} nodes, {total_chars:,} chars → {args.provider}/{model} @ {dims}d",
        file=sys.stderr,
    )
    if args.dry_run:
        for d in days_seen:
            n = sum(1 for c in chunks if c["day"] == d)
            print(f"  Day {d:>2}: {n:>2} chunks", file=sys.stderr)
        return 0

    # A revised day changes a handful of chunks; everything else is byte-identical
    # and would be paid for again. Reuse by text, never by id: ids shift when a
    # paragraph is inserted, text does not lie.
    cache: dict[str, list[float]] = {}
    if args.reuse:
        prev = json.loads(args.reuse.read_text(encoding="utf-8"))
        pm = prev.get("meta", {})
        if (pm.get("provider"), pm.get("model"), pm.get("dims")) != (
            args.provider,
            model,
            dims,
        ):
            print(
                f"--reuse refused: {args.reuse} is {pm.get('provider')}/{pm.get('model')}@{pm.get('dims')}, this run is {args.provider}/{model}@{dims}",
                file=sys.stderr,
            )
            return 2
        for c in prev.get("chunks", []):
            cache[c["text"]] = c["vector"]
        for n in prev.get("nodes", []):
            cache[node_key(n["id"], n.get("text_sha256", ""))] = n["vector"]

    def embed_cached(texts: list[str], keys: list[str], what: str) -> list[list[float]]:
        out: list[list[float] | None] = [cache.get(k) for k in keys]
        todo = [i for i, v in enumerate(out) if v is None]
        print(
            f"embedding {what}: {len(todo)} to send, {len(texts) - len(todo)} reused",
            file=sys.stderr,
        )
        if todo:
            fresh = embed_all(
                [texts[i] for i in todo], args.provider, model, dims, batch, key
            )
            for i, v in zip(todo, fresh):
                out[i] = v
        return out  # type: ignore[return-value]

    cvecs = embed_cached(
        [c["text"] for c in chunks], [c["text"] for c in chunks], "chunks"
    )
    nvecs = []
    if nodes:
        nvecs = embed_cached(
            [n["text"] for n in nodes],
            [node_key(n["id"], sha(n["text"])) for n in nodes],
            "nodes",
        )

    out = {
        "meta": {
            "provider": args.provider,
            "model": model,
            "dims": dims,
            "task": "SEMANTIC_SIMILARITY",
            "normalized": True,
            "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "article_sha256": src_sha,
            "article_bytes": len(md.encode("utf-8")),
            "chunk_max_chars": args.max_chars,
            "chunk_overlap": args.overlap,
            "n_chunks": len(chunks),
            "n_nodes": len(nodes),
        },
        "chunks": [{**c, "vector": v} for c, v in zip(chunks, cvecs)],
        "nodes": [
            {
                "id": n["id"],
                "kind": n["kind"],
                "text_sha256": sha(n["text"]),
                "vector": v,
            }
            for n, v in zip(nodes, nvecs)
        ],
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes)", file=sys.stderr)
    preview(chunks, cvecs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

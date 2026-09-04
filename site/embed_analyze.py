#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "scikit-learn>=1.4", "umap-learn>=0.5.6"]
# ///
"""Turn `embeddings.json` (from embed.py) into what the pages and the edge audit need.

Writes `site/data/embed.json` — small: 3-D coordinates, the 30×30 day similarity,
the most similar day pairs with the chunk pair that explains each — and prints an
audit of the hand-labelled edges against the vectors. The vectors themselves stay
out of the repo; this is the only step that needs numpy/umap, and it runs once per
embedding run, not per build.

Why chunks and not days go through UMAP: 30 points give UMAP nothing to hold on to
and the picture changes with the seed. ~400 chunks make a day a cloud, and two days
being close is two clouds overlapping. The day similarity itself is computed on the
mean chunk vector without any reduction, so the heatmap is the honest view and the
3-D plot is the illustration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def load(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    chunks = d["chunks"]
    X = np.array([c["vector"] for c in chunks], dtype=np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    nodes = d["nodes"]
    N = (
        np.array([n["vector"] for n in nodes], dtype=np.float32)
        if nodes
        else np.zeros((0, X.shape[1]), np.float32)
    )
    if len(N):
        N /= np.linalg.norm(N, axis=1, keepdims=True)
    return d["meta"], chunks, X, nodes, N


def reduce3(X: np.ndarray, seed: int, n_neighbors: int, min_dist: float):
    import umap
    from sklearn.decomposition import PCA

    pca = PCA(n_components=3, random_state=seed)
    P = pca.fit_transform(X)
    U = umap.UMAP(
        n_components=3,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric="cosine",
        random_state=seed,
    ).fit_transform(X)

    def norm01(
        A,
    ):  # keep both plots in the same box so the toggle does not rescale the axes
        lo, hi = A.min(axis=0), A.max(axis=0)
        return (A - lo) / np.where(hi - lo == 0, 1, hi - lo) * 2 - 1

    return (
        norm01(P),
        norm01(U),
        [round(float(v), 4) for v in pca.explained_variance_ratio_],
    )


def preview(t: str, n: int = 110) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("embeddings", type=Path)
    ap.add_argument("--out", type=Path, default=DATA / "embed.json")
    ap.add_argument(
        "--audit", type=Path, help="write the edge audit as JSON here (also printed)"
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--neighbors", type=int, default=15)
    ap.add_argument("--min-dist", type=float, default=0.1)
    ap.add_argument(
        "--pairs",
        type=int,
        default=0,
        help="keep only the N most similar day pairs (default: all 435, so any heatmap cell can show its chunk pair)",
    )
    args = ap.parse_args(argv)

    meta, chunks, X, nodes, N = load(args.embeddings)
    days = sorted({c["day"] for c in chunks})
    idx = {d: [i for i, c in enumerate(chunks) if c["day"] == d] for d in days}
    D = np.stack([X[idx[d]].mean(axis=0) for d in days])
    D /= np.linalg.norm(D, axis=1, keepdims=True)
    S = D @ D.T  # day × day, cosine of mean chunk vectors
    C = X @ X.T  # chunk × chunk, for "which pair explains it"

    P, U, evr = reduce3(X, args.seed, args.neighbors, args.min_dist)
    cent = {}
    for d in days:
        cent[d] = {
            "umap": [round(float(v), 4) for v in U[idx[d]].mean(axis=0)],
            "pca": [round(float(v), 4) for v in P[idx[d]].mean(axis=0)],
        }

    pairs = []
    for i, a in enumerate(days):
        for j in range(i + 1, len(days)):
            b = days[j]
            sub = C[np.ix_(idx[a], idx[b])]
            k = np.unravel_index(int(sub.argmax()), sub.shape)
            ca, cb = chunks[idx[a][k[0]]], chunks[idx[b][k[1]]]
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "sim": round(float(S[i, j]), 4),
                    "best": {
                        "sim": round(float(sub.max()), 4),
                        "a": {
                            "id": ca["id"],
                            "heading": ca["heading"],
                            "preview": preview(ca["text"]),
                        },
                        "b": {
                            "id": cb["id"],
                            "heading": cb["heading"],
                            "preview": preview(cb["text"]),
                        },
                    },
                }
            )
    pairs.sort(key=lambda p: -p["sim"])
    # baseline for the reader: what "similar" means on this model's scale
    off = S[~np.eye(len(days), dtype=bool)]
    scale = {
        "min": round(float(off.min()), 4),
        "median": round(float(np.median(off)), 4),
        "max": round(float(off.max()), 4),
    }
    # each day's nearest other day
    nearest = {}
    for i, a in enumerate(days):
        row = S[i].copy()
        row[i] = -1
        j = int(row.argmax())
        nearest[a] = {"day": days[j], "sim": round(float(row[j]), 4)}

    out = {
        "meta": {
            **{
                k: meta[k]
                for k in (
                    "provider",
                    "model",
                    "dims",
                    "created",
                    "article_sha256",
                    "n_chunks",
                )
            },
            "umap": {
                "n_neighbors": args.neighbors,
                "min_dist": args.min_dist,
                "metric": "cosine",
                "seed": args.seed,
            },
            "pca_explained_variance": evr,
            "scale": scale,
        },
        "days": [
            {"n": d, "chunks": len(idx[d]), "centroid": cent[d], "nearest": nearest[d]}
            for d in days
        ],
        "sim": [[round(float(v), 4) for v in row] for row in S],
        "pairs": pairs[: args.pairs] if args.pairs else pairs,
        "chunks": [
            {
                "id": c["id"],
                "day": c["day"],
                "heading": c["heading"],
                "preview": preview(c["text"]),
                "umap": [round(float(v), 4) for v in U[i]],
                "pca": [round(float(v), 4) for v in P[i]],
            }
            for i, c in enumerate(chunks)
        ],
    }
    args.out.write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(
        f"wrote {args.out} ({args.out.stat().st_size:,} bytes); PCA 3 comps explain {sum(evr):.1%}; off-diagonal sim min/median/max {scale['min']}/{scale['median']}/{scale['max']}",
        file=sys.stderr,
    )

    if not len(N):
        return 0
    # ---- edge audit: a node's description against the chunks of the days it is linked to.
    # Per (node, day) the score is the best chunk of that day, because "this day discusses
    # this mechanism" is a claim about one passage, not about the day's average. Ranks
    # rather than raw cosine, because this model's cosines sit in a narrow band.
    site_days = json.loads((DATA / "days.json").read_text(encoding="utf-8"))
    linked: dict[str, set[int]] = {}
    for d in site_days:
        for r in d.get("repo", []) + d.get("repo_ext", []) + d.get("concepts", []):
            linked.setdefault(r, set()).add(d["n"])
    M = N @ X.T  # node × chunk
    best = np.stack([M[:, idx[d]].max(axis=1) for d in days], axis=1)  # node × day
    suspects, candidates, summaries = [], [], []
    for ni, node in enumerate(nodes):
        row = best[ni]
        order = np.argsort(-row)
        rank = {days[j]: r + 1 for r, j in enumerate(order)}
        if node["kind"] == "day":
            n = int(node["id"][1:])
            summaries.append(
                {
                    "day": n,
                    "rank_of_own_day": rank[n],
                    "sim": round(float(row[days.index(n)]), 4),
                    "top": days[int(order[0])],
                }
            )
            continue
        L = linked.get(node["id"], set())
        if not L:
            continue
        floor = min(row[days.index(d)] for d in L)
        for d in sorted(L):
            if rank[d] > 8:
                suspects.append(
                    {
                        "node": node["id"],
                        "day": d,
                        "rank": rank[d],
                        "sim": round(float(row[days.index(d)]), 4),
                        "top_day": days[int(order[0])],
                        "top_sim": round(float(row[order[0]]), 4),
                    }
                )
        for r, j in enumerate(order[:3]):
            d = days[j]
            if d not in L and row[j] >= floor:
                candidates.append(
                    {
                        "node": node["id"],
                        "day": d,
                        "rank": r + 1,
                        "sim": round(float(row[j]), 4),
                        "linked": sorted(L),
                        "linked_floor": round(float(floor), 4),
                    }
                )
    suspects.sort(key=lambda s: -s["rank"])
    candidates.sort(key=lambda c: -(c["sim"] - c["linked_floor"]))
    audit = {
        "suspect_edges": suspects,
        "candidate_edges": candidates,
        "day_summaries": summaries,
    }
    if args.audit:
        args.audit.write_text(
            json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    bad = [s for s in summaries if s["rank_of_own_day"] != 1]
    print(
        f"audit: {len(suspects)} suspect edges (linked day ranks >8), {len(candidates)} candidate edges (unlinked day in top 3 and above the linked floor); day summaries not ranking their own day first: {[(s['day'], s['rank_of_own_day'], s['top']) for s in bad]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

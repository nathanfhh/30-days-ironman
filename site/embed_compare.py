#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""Compare two embeddings.json files (same article, different models) and say which to keep.

There is no ground truth for "the better embedding of this series", so the report
leans on the two things this repo already has: hand-labelled, read-verified edges
between nodes and days, and the author's own per-day summaries. A model that
ranks a node's linked days higher, and a day's summary closest to that day's own
chunks, agrees more with a human who read the text. The rest of the report is
about how much the two models disagree with each other, so the switch is a known
quantity: day×day correlation, nearest-day agreement, top-pair overlap, chunk kNN
overlap.

    uv run site/embed_compare.py embeddings.json embeddings-2.json
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
    X = np.array([c["vector"] for c in d["chunks"]], dtype=np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    N = (
        np.array([n["vector"] for n in d["nodes"]], dtype=np.float32)
        if d.get("nodes")
        else np.zeros((0, X.shape[1]), np.float32)
    )
    if len(N):
        N /= np.linalg.norm(N, axis=1, keepdims=True)
    return d, X, N


def align(a: dict, b: dict):
    """Chunks are matched by text, never by position: a re-chunk must not be compared as if aligned."""
    ib = {c["text"]: i for i, c in enumerate(b["chunks"])}
    pairs = [(i, ib[c["text"]]) for i, c in enumerate(a["chunks"]) if c["text"] in ib]
    nb = {n["id"]: i for i, n in enumerate(b.get("nodes", []))}
    npairs = [
        (i, nb[n["id"]]) for i, n in enumerate(a.get("nodes", [])) if n["id"] in nb
    ]
    return pairs, npairs


def day_matrix(chunks, X, idx):
    days = sorted({chunks[i]["day"] for i in idx})
    D = np.stack(
        [X[[i for i in idx if chunks[i]["day"] == d]].mean(axis=0) for d in days]
    )
    D /= np.linalg.norm(D, axis=1, keepdims=True)
    return days, D @ D.T


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = x.argsort().argsort(), y.argsort().argsort()
    return float(np.corrcoef(rx, ry)[0, 1])


def audit_scores(d, X, N, days, chunk_idx):
    """Mean rank (1 = best) of the days a node is hand-linked to, and how many day
    summaries rank their own day first. Lower / higher = agrees more with the reader."""
    if not len(N):
        return None
    site_days = json.loads((DATA / "days.json").read_text(encoding="utf-8"))
    linked = {}
    for x in site_days:
        for r in x.get("repo", []) + x.get("repo_ext", []) + x.get("concepts", []):
            linked.setdefault(r, set()).add(x["n"])
    by_day = {dd: [i for i in chunk_idx if d["chunks"][i]["day"] == dd] for dd in days}
    M = N @ X.T
    best = np.stack([M[:, by_day[dd]].max(axis=1) for dd in days], axis=1)
    ranks, own_first, n_days = [], 0, 0
    for ni, node in enumerate(d["nodes"]):
        order = np.argsort(-best[ni])
        rank = {days[j]: r + 1 for r, j in enumerate(order)}
        if node["kind"] == "day":
            n_days += 1
            own_first += rank.get(int(node["id"][1:]), 99) == 1
            continue
        for dd in linked.get(node["id"], ()):
            if dd in rank:
                ranks.append(rank[dd])
    return {
        "linked_mean_rank": float(np.mean(ranks)) if ranks else None,
        "linked_top3": float(np.mean([r <= 3 for r in ranks])) if ranks else None,
        "summary_own_day_first": f"{own_first}/{n_days}",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument(
        "--k", type=int, default=10, help="neighbourhood size for the chunk kNN overlap"
    )
    ap.add_argument(
        "--top", type=int, default=25, help="how many most-similar day pairs to compare"
    )
    ap.add_argument("--out", type=Path, help="also write the numbers as JSON")
    args = ap.parse_args(argv)

    da, Xa, Na = load(args.a)
    db, Xb, Nb = load(args.b)
    pairs, npairs = align(da, db)
    if not pairs:
        print(
            "no chunk text in common: were these made from the same article with the same chunking?",
            file=sys.stderr,
        )
        return 1
    ia, ib = [p[0] for p in pairs], [p[1] for p in pairs]
    name_a = f"{da['meta']['model']}@{da['meta']['dims']}"
    name_b = f"{db['meta']['model']}@{db['meta']['dims']}"
    print(
        f"A = {name_a}  ({len(da['chunks'])} 段)\nB = {name_b}  ({len(db['chunks'])} 段)\n共同段落 {len(pairs)}，共同節點 {len(npairs)}\n"
    )

    # day × day
    days_a, Sa = day_matrix(da["chunks"], Xa, ia)
    days_b, Sb = day_matrix(db["chunks"], Xb, ib)
    assert days_a == days_b
    days = days_a
    off = ~np.eye(len(days), dtype=bool)
    r_pearson = float(np.corrcoef(Sa[off], Sb[off])[0, 1])
    r_spear = spearman(Sa[off], Sb[off])
    scale = lambda S: (S[off].min(), np.median(S[off]), S[off].max())
    near = lambda S: [
        days[int(np.argmax(np.where(off[i], S[i], -1)))] for i in range(len(days))
    ]
    na, nb_ = near(Sa), near(Sb)
    near_agree = sum(x == y for x, y in zip(na, nb_))
    top = lambda S: {
        (days[i], days[j])
        for i, j in sorted(
            ((i, j) for i in range(len(days)) for j in range(i + 1, len(days))),
            key=lambda ij: -S[ij],
        )[: args.top]
    }
    ta, tb = top(Sa), top(Sb)

    # chunk kNN overlap
    Ca, Cb = Xa[ia] @ Xa[ia].T, Xb[ib] @ Xb[ib].T
    np.fill_diagonal(Ca, -1)
    np.fill_diagonal(Cb, -1)
    ka = np.argsort(-Ca, axis=1)[:, : args.k]
    kb = np.argsort(-Cb, axis=1)[:, : args.k]
    knn = float(
        np.mean([len(set(ka[i]) & set(kb[i])) / args.k for i in range(len(ia))])
    )

    # agreement with the hand-labelled edges
    aud_a = audit_scores(da, Xa, Na, days, ia)
    aud_b = audit_scores(db, Xb, Nb, days, ib)

    def row(label, va, vb, fmt="{:.3f}", note=""):
        f = lambda v: (
            "—" if v is None else (fmt.format(v) if not isinstance(v, str) else v)
        )
        print(f"| {label} | {f(va)} | {f(vb)} | {note} |")

    print("| 指標 | A | B | 讀法 |\n|---|---|---|---|")
    sa, sb = scale(Sa), scale(Sb)
    row(
        "天×天 cosine 最低／中位／最高",
        f"{sa[0]:.3f} / {sa[1]:.3f} / {sa[2]:.3f}",
        f"{sb[0]:.3f} / {sb[1]:.3f} / {sb[2]:.3f}",
        note="範圍越寬，熱圖越有對比",
    )
    row(
        "天×天矩陣相關（Pearson／Spearman）",
        f"{r_pearson:.3f} / {r_spear:.3f}",
        "同左",
        note="兩個模型對「哪兩天像」的看法有多一致",
    )
    row(
        "每一天的最近鄰一致",
        f"{near_agree}/{len(days)}",
        "同左",
        note="不一致的天下面列出",
    )
    row(f"最相近 {args.top} 對日子的交集", f"{len(ta & tb)}/{args.top}", "同左")
    row(
        f"段落 {args.k}-NN 重疊率",
        f"{knn:.3f}",
        "同左",
        note="1 = 兩個模型的鄰居完全一樣",
    )
    if aud_a and aud_b:
        row(
            "手標邊：所連的天的平均名次（1 最好）",
            aud_a["linked_mean_rank"],
            aud_b["linked_mean_rank"],
            "{:.2f}",
            "越低越貼近人讀出來的邊",
        )
        row(
            "手標邊：所連的天落在前三名的比例",
            aud_a["linked_top3"],
            aud_b["linked_top3"],
            "{:.3f}",
            "越高越好",
        )
        row(
            "每天摘要最像自己那一天的天數",
            aud_a["summary_own_day_first"],
            aud_b["summary_own_day_first"],
            note="越高越好",
        )
    diff = [(d, x, y) for d, x, y in zip(days, na, nb_) if x != y]
    if diff:
        print(
            "\n最近鄰不同的天："
            + "、".join(f"Day {d}（A→{x}，B→{y}）" for d, x, y in diff)
        )
    only_a, only_b = sorted(ta - tb), sorted(tb - ta)
    if only_a:
        print(
            f"只有 A 排進前 {args.top} 的配對："
            + "、".join(f"{a}↔{b}" for a, b in only_a)
        )
    if only_b:
        print(
            f"只有 B 排進前 {args.top} 的配對："
            + "、".join(f"{a}↔{b}" for a, b in only_b)
        )

    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "a": name_a,
                    "b": name_b,
                    "common_chunks": len(pairs),
                    "pearson": r_pearson,
                    "spearman": r_spear,
                    "nearest_agree": near_agree,
                    "top_overlap": len(ta & tb),
                    "knn_overlap": knn,
                    "audit_a": aud_a,
                    "audit_b": aud_b,
                    "scale_a": [float(v) for v in sa],
                    "scale_b": [float(v) for v in sb],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Build the static 關聯圖 site: `site/template.html` + `site/data/*.json` → one HTML file.

Why a build step instead of `fetch("data.json")` at runtime: the page must also
open from `file://` (a reader who clones the repo) and must survive a CDN or
network hiccup, so everything is inlined and the output has zero external
script dependencies. The template carries a single `/*DATA*/` marker.

Why `--check` is separate from `--strict`: the series is still being published,
so unpublished days legitimately hold `null` URLs today. `--check` reports them
(and any `{{…}}` placeholder that leaked in from a draft); `--strict` turns the
report into a non-zero exit for the final pass, when nothing may be missing.
Dangling node references are always fatal — they are bugs, not a stage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
TEMPLATE = HERE / "template.html"
VENDOR = HERE / "vendor"
MARKER = "/*DATA*/"
VENDOR_MARKER = "<!--VENDOR-->"
PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")

# The template keys on these three vocabularies (layer chips, the coverage
# matrix rows, the concept-kind label). A value outside them is not a crash
# but a node that quietly falls off the page, so they are checked here as
# hard errors, same as a dangling id. Keep in sync with LAYERS / GROUPS / CK
# in template.html.
LAYERS = {"intro", "skill", "eval", "boundary", "observe", "platform", "outro"}
GROUPS = {
    "root",
    "skill",
    "test",
    "bench",
    "dc",
    "proxy",
    "otel",
    "mitm",
    "pty",
    "adr",
    "ptytest",
    "ttyd",
    "ttyd-rust",
    "ttyd-tests",
    "ttyd-parity",
}
CONCEPT_KINDS = {"frame", "method", "rule", "incident", "insight", "gap"}


@dataclass
class Report:
    dangling: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    unpublished: list[int] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    @property
    def fatal(self) -> bool:
        return bool(self.dangling)

    @property
    def incomplete(self) -> bool:
        return bool(self.placeholders or self.unpublished)

    def lines(self) -> list[str]:
        out = []
        for d in self.dangling:
            out.append(f"dangling reference: {d}")
        for p in self.placeholders:
            out.append(f"placeholder left in data: {p}")
        if self.unpublished:
            out.append(
                "article URL still null for day(s): "
                + ", ".join(map(str, self.unpublished))
            )
        for o in self.orphans:
            out.append(f"repo node referenced by no day: {o}")
        return out


def load_data(data_dir: Path | None = None) -> dict:
    # Resolved at call time, not definition time, so tests can point DATA_DIR
    # at a scratch copy.
    data_dir = data_dir or DATA_DIR

    def read(name: str):
        return json.loads((data_dir / name).read_text(encoding="utf-8"))

    days = read("days.json")
    articles = read("articles.json")
    urls = articles.get("days", {})
    for d in days:
        d["url"] = urls.get(str(d["n"]))
    # embed.json is optional: it only exists after someone ran embed.py +
    # embed_analyze.py with a paid key. Without it the site is the graph alone.
    embed = read("embed.json") if (data_dir / "embed.json").exists() else None
    return {
        "days": days,
        "concepts": read("concepts.json"),
        "repo": read("repo.json"),
        "promises": read("promises.json"),
        "tours": read("tours.json"),
        "review": read("review.json"),
        "series": articles.get("series"),
        "embed": embed,
    }


def node_ids(data: dict) -> set[str]:
    return (
        {n["id"] for n in data["days"]}
        | {n["id"] for n in data["concepts"]}
        | {n["id"] for n in data["repo"]}
    )


def check(data: dict) -> Report:
    rep = Report()
    ids = node_ids(data)
    referenced: set[str] = set()
    for d in data["days"]:
        if d.get("layer") not in LAYERS:
            rep.dangling.append(
                f"{d['id']} layer {d.get('layer')!r} is not one of {sorted(LAYERS)}"
            )
        for ref in d.get("repo", []) + d.get("repo_ext", []) + d.get("concepts", []):
            referenced.add(ref)
            if ref not in ids:
                rep.dangling.append(f"{d['id']} -> {ref}")
        if d.get("url") is None:
            rep.unpublished.append(d["n"])
    for r in data["repo"]:
        if r.get("group") not in GROUPS:
            rep.dangling.append(
                f"{r['id']} group {r.get('group')!r} is not one of {sorted(GROUPS)}"
            )
    for c in data["concepts"]:
        if c.get("kind") not in CONCEPT_KINDS:
            rep.dangling.append(
                f"{c['id']} kind {c.get('kind')!r} is not one of {sorted(CONCEPT_KINDS)}"
            )
    for p in data["promises"]:
        for ref in (p["from"], p["to"]):
            if ref not in ids:
                rep.dangling.append(f"promise -> {ref}")
    for t in data["tours"]:
        for s in t["stops"]:
            if s["node"] not in ids:
                rep.dangling.append(f"tour {t['id']} -> {s['node']}")
    review = data["review"]
    for k in review.get("notes", {}):
        if k not in ids:
            rep.dangling.append(f"review note -> {k}")
    for lamp in review.get("lamps", {}).values():
        for item in lamp:
            if item["node"] not in ids:
                rep.dangling.append(f"review lamp -> {item['node']}")
    rep.orphans = [r["id"] for r in data["repo"] if r["id"] not in referenced]
    if data.get("embed"):
        # The vector page indexes days by number, so a day it knows and the
        # graph does not is the same class of bug as a dangling id.
        known = {d["n"] for d in data["days"]}
        emb = data["embed"]
        for d in emb.get("days", []):
            if d["n"] not in known:
                rep.dangling.append(f"embed.json day {d['n']} is not in days.json")
        for c in emb.get("chunks", []):
            if c["day"] not in known:
                rep.dangling.append(f"embed.json chunk {c['id']} -> day {c['day']}")
                break
        n = len(emb.get("days", []))
        if (
            any(len(row) != n for row in emb.get("sim", []))
            or len(emb.get("sim", [])) != n
        ):
            rep.dangling.append("embed.json sim matrix is not days × days")

    def walk(obj, path):
        if isinstance(obj, str):
            if m := PLACEHOLDER.search(obj):
                rep.placeholders.append(f"{path}: {m.group(0)}")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(data, "data")
    return rep


def refs(data: dict, node_id: str) -> dict[str, list[str]] | None:
    """Everything on the page that would have to be re-verified if `node_id` changes.

    Built for the playbook: when an article is published or edited, the day's own
    summary is the obvious thing to update, but the promises it anchors, the tour
    stops that quote it, the review notes that cite it and the repo nodes it
    points at are the ones that drift silently. Returns None for an unknown id;
    an existing node nothing points at returns all-empty buckets.
    """
    if node_id not in node_ids(data):
        return None
    out: dict[str, list[str]] = {
        "day": [],
        "repo": [],
        "repo_ext": [],
        "concepts": [],
        "linked_from_days": [],
        "promises": [],
        "tours": [],
        "review_notes": [],
        "review_lamps": [],
    }
    day_n = None
    for d in data["days"]:
        if d["id"] == node_id:
            day_n = d["n"]
            out["day"].append(f"Day {d['n']}｜{d['title']}")
            out["repo"] = list(d.get("repo", []))
            out["repo_ext"] = list(d.get("repo_ext", []))
            out["concepts"] = list(d.get("concepts", []))
        elif node_id in d.get("repo", []) + d.get("repo_ext", []) + d.get(
            "concepts", []
        ):
            out["linked_from_days"].append(d["id"])
    for p in data["promises"]:
        if node_id in (p["from"], p["to"]):
            out["promises"].append(f"{p['from']} → {p['to']}: {p['text']}")
    for t in data["tours"]:
        for i, s in enumerate(t["stops"], 1):
            if s["node"] == node_id:
                out["tours"].append(f"{t['id']} 第 {i} 站")
    # Prose that names the day counts as a reference even without the id. The
    # lookahead keeps "Day 2" from matching "Day 27".
    names_day = re.compile(rf"Day {day_n}(?!\d)") if day_n is not None else None
    review = data["review"]
    for k, note in review.get("notes", {}).items():
        if k == node_id or (names_day and names_day.search(note.get("text", ""))):
            out["review_notes"].append(k)
    for lamp, items in review.get("lamps", {}).items():
        for i, item in enumerate(items, 1):
            text = item["title"] + item["text"]
            if item["node"] == node_id or (names_day and names_day.search(text)):
                out["review_lamps"].append(f"{lamp}#{i} {item['title']}")
    return out


def render(data: dict, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    if MARKER not in html:
        raise ValueError(f"template has no {MARKER} marker")
    # `</script>` inside a JSON string would end the inline script early.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    # ECharts is only loaded when there is a vector section to draw; without
    # embed.json the page keeps its zero-external-script shape.
    vendor = (
        "".join(
            f'<script src="vendor/{f.name}"></script>\n'
            for f in sorted(VENDOR.glob("*.js"))
        )
        if data.get("embed")
        else ""
    )
    return html.replace(MARKER, "const DATA=" + payload + ";", 1).replace(
        VENDOR_MARKER, vendor, 1
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        type=Path,
        default=HERE / "dist",
        help="output directory (default: site/dist)",
    )
    ap.add_argument("--check", action="store_true", help="print the data report")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail when any article URL or placeholder is unresolved",
    )
    ap.add_argument("--open", action="store_true", help="open the result in a browser")
    ap.add_argument(
        "--refs",
        metavar="NODE_ID",
        help="list everything that references NODE_ID (e.g. d27) and exit; no build",
    )
    args = ap.parse_args(argv)

    data = load_data()
    if args.refs:
        found = refs(data, args.refs)
        if found is None:
            print(f"no node named {args.refs!r}", file=sys.stderr)
            return 1
        if not any(found.values()):
            print(
                f"{args.refs} exists but nothing on the page references it",
                file=sys.stderr,
            )
            return 0
        for key, items in found.items():
            if items:
                print(f"{key}:")
                for it in items:
                    print(f"  - {it}")
        return 0
    rep = check(data)
    if args.check or rep.fatal or (args.strict and rep.incomplete):
        for line in rep.lines():
            print(line, file=sys.stderr)
    if rep.fatal:
        return 1
    if args.strict and rep.incomplete:
        print("strict: data is incomplete, refusing to build", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "index.html"
    target.write_text(render(data), encoding="utf-8")
    (args.out / ".nojekyll").write_text("", encoding="utf-8")
    nodes = len(data["days"]) + len(data["concepts"]) + len(data["repo"])
    print(f"{target} ({target.stat().st_size:,} bytes, {nodes} nodes)")
    if data.get("embed"):
        # Vendored ECharts is copied next to the page rather than inlined: 1.7 MB
        # the browser can cache, and the page still opens from file://.
        vend = args.out / "vendor"
        vend.mkdir(exist_ok=True)
        for f in VENDOR.glob("*.js"):
            (vend / f.name).write_bytes(f.read_bytes())
        print(f"  + vector section ({len(data['embed']['chunks'])} chunks) and vendor/")
    if args.open:
        webbrowser.open(target.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

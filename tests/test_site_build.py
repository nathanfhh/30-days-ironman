"""Guards for the 關聯圖 site data and its builder.

The page is prose-shaped data (ids referencing ids), so the failure mode is the
same as the skill's: a renamed node silently orphans every link to it and the
graph just draws fewer edges. Nothing in the browser would complain.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def _load_build():
    spec = importlib.util.spec_from_file_location("site_build", SITE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    # build.py uses `from __future__ import annotations` + dataclasses, which
    # resolve field types through sys.modules[cls.__module__]; register first.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build():
    return _load_build()


@pytest.fixture(scope="module")
def data(build):
    return build.load_data()


def test_data_has_no_dangling_references(build, data):
    rep = build.check(data)
    assert rep.dangling == [], "\n".join(rep.lines())


def test_every_repo_node_is_reachable_from_a_day(build, data):
    # An unreferenced repo node is not fatal for the build, but it is invisible
    # on the page (it floats to the middle with no edges), which is a data bug.
    rep = build.check(data)
    assert rep.orphans == [], rep.orphans


def test_thirty_days_in_order(data):
    assert [d["n"] for d in data["days"]] == list(range(1, 31))
    assert all(d["id"] == f"d{d['n']:02d}" for d in data["days"])


def test_article_urls_are_ithelp_or_null(data):
    for d in data["days"]:
        url = d["url"]
        assert url is None or url.startswith(
            "https://ithelp.ithome.com.tw/articles/"
        ), (d["n"], url)


def test_render_inlines_data_and_escapes_script_terminator(build, data):
    html = build.render(data)
    assert build.MARKER not in html
    body = html.split("const DATA=", 1)[1]
    payload = body[
        : body.index(";\n(function")
    ]  # the JSON literal ends where the page code begins
    # Every "</" in the JSON is escaped to "<\\/", so the real closing tag is the
    # first one the parser meets; a raw "</" here would end the script early.
    assert "</" not in payload
    assert (
        "<code>" in payload
    )  # the review notes carry markup, so the escape path is exercised


def test_strict_refuses_incomplete_data(build, tmp_path, monkeypatch):
    # Copy the real data, blank one URL and plant one placeholder, then make
    # sure --strict is the gate that catches both. Non-strict must still build:
    # the series is published one day at a time, and a half-published site is
    # the normal state until Day 30.
    src = SITE / "data"
    for p in src.iterdir():
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    arts = json.loads((tmp_path / "articles.json").read_text(encoding="utf-8"))
    arts["days"]["1"] = None
    (tmp_path / "articles.json").write_text(json.dumps(arts), encoding="utf-8")
    days = json.loads((tmp_path / "days.json").read_text(encoding="utf-8"))
    days[0]["summary"] += " {{DAY01_URL}}"
    (tmp_path / "days.json").write_text(json.dumps(days), encoding="utf-8")

    monkeypatch.setattr(build, "DATA_DIR", tmp_path)
    out = tmp_path / "dist"
    assert build.main(["--out", str(out)]) == 0
    assert (out / "index.html").exists()
    assert build.main(["--out", str(out), "--strict"]) == 1


def test_dangling_reference_is_always_fatal(build, tmp_path, monkeypatch):
    src = SITE / "data"
    for p in src.iterdir():
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    days = json.loads((tmp_path / "days.json").read_text(encoding="utf-8"))
    days[4]["repo"].append("r-does-not-exist")
    (tmp_path / "days.json").write_text(json.dumps(days), encoding="utf-8")
    monkeypatch.setattr(build, "DATA_DIR", tmp_path)
    assert build.main(["--out", str(tmp_path / "dist")]) == 1


def test_refs_lists_everything_that_would_drift(build, data, capsys):
    found = build.refs(data, "d27")
    assert found["day"] == ["Day 27｜把「不做」變成結構"]
    assert "r-adr-0019" in found["repo"]
    assert any(p.startswith("d09 → d27") for p in found["promises"])
    # tours reference concept/repo ids, so ask through one of the day's concepts
    assert build.refs(data, "c-structure-not-discipline")["tours"]
    # review text that names the day counts as a reference even without the id
    assert found["review_lamps"] or found["review_notes"]
    assert build.main(["--refs", "d27"]) == 0
    assert "promises:" in capsys.readouterr().out
    assert build.main(["--refs", "nope"]) == 1


def test_refs_day_number_is_a_whole_word(build, data):
    # "Day 2" must not match the "Day 27" that review notes talk about.
    d02 = build.refs(data, "d02")
    assert d02["review_notes"] == [] and d02["review_lamps"] == []
    assert build.refs(data, "d27")["review_lamps"]


def test_refs_distinguishes_unknown_from_unreferenced(build, data, capsys):
    assert build.refs(data, "r-not-a-node") is None
    extra = {
        **data,
        "repo": data["repo"]
        + [{"id": "r-lonely", "name": "x", "path": "x", "group": "root", "desc": ""}],
    }
    found = build.refs(extra, "r-lonely")
    assert found is not None and not any(found.values())


def test_check_rejects_vocabulary_typos(build, data):
    bad = json.loads(json.dumps(data))
    bad["repo"][0]["group"] = "tests"
    bad["days"][0]["layer"] = "observ"
    bad["concepts"][0]["kind"] = "framework"
    rep = build.check(bad)
    assert rep.fatal
    joined = "\n".join(rep.lines())
    assert (
        "group 'tests'" in joined
        and "layer 'observ'" in joined
        and "kind 'framework'" in joined
    )


def test_repo_ext_is_checked_like_repo(build, data):
    bad = json.loads(json.dumps(data))
    bad["days"][21]["repo_ext"] = ["r-nope"]
    assert build.check(bad).fatal
    assert "r-adr-0021" in build.refs(data, "d22")["repo_ext"]
    assert "d22" in build.refs(data, "r-adr-0021")["linked_from_days"]


# ---- the vector page (site/data/embed.json is optional; these run only when it exists)

EMBED = SITE / "data" / "embed.json"
needs_embed = pytest.mark.skipif(not EMBED.exists(), reason="no site/data/embed.json")


@needs_embed
def test_embed_data_contract(data):
    emb = data["embed"]
    days = [d["n"] for d in emb["days"]]
    assert days == list(range(1, 31))
    sim = emb["sim"]
    assert len(sim) == 30 and all(len(r) == 30 for r in sim)
    for i in range(30):
        assert sim[i][i] == pytest.approx(1.0, abs=1e-3)
        for j in range(30):
            assert sim[i][j] == pytest.approx(sim[j][i], abs=1e-6)
    assert all(
        len(c["umap"]) == 3 and len(c["pca"]) == 3 and c["day"] in days
        for c in emb["chunks"]
    )
    sims = [p["sim"] for p in emb["pairs"]]
    assert sims == sorted(sims, reverse=True) and len(sims) == 30 * 29 // 2
    assert (
        emb["meta"]["scale"]["min"] <= min(sims)
        and max(sims) <= emb["meta"]["scale"]["max"]
    )


def test_vector_section_is_inlined_and_only_vendored_scripts_load(
    build, data, tmp_path
):
    import re

    html = build.render(data)
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    if data.get("embed"):
        assert '"umap":[' in html and '"sim":[[' in html
        assert srcs and all(s.startswith("vendor/") for s in srcs), srcs
        for s in srcs:
            assert (SITE / s).exists(), s
        out = tmp_path / "dist"
        assert build.main(["--out", str(out)]) == 0
        assert list((out / "vendor").glob("*.js"))
    # without embed.json the page must stay free of external scripts
    bare = build.render({**data, "embed": None})
    assert not re.findall(r"<script[^>]+src=", bare) and build.VENDOR_MARKER not in bare
    assert '"embed":null' in bare


@needs_embed
def test_embed_day_outside_graph_is_fatal(build, data):
    bad = json.loads(json.dumps(data))
    bad["embed"]["days"].append({"n": 31, "chunks": 1, "centroid": {}, "nearest": {}})
    rep = build.check(bad)
    assert rep.fatal and any("embed.json day 31" in line for line in rep.lines())

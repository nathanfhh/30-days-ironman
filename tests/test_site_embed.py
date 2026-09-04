"""site/embed.py: the chunker and the output shape, exercised with the fake provider.

The real providers need a key and a network, which the tests must not have; the
fake provider hashes text into vectors so the file format and the chunk bookkeeping
(ids, offsets, coverage) are still checked end to end.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MD = (
    """# Day 1｜第一天

開頭一段。

## 小節甲

甲的內容。甲的內容。

## 小節乙

乙的內容。

# Day 2｜第二天

第二天的開頭。

"""
    + "很長的一段。" * 400
)


@pytest.fixture(scope="module")
def embed():
    spec = importlib.util.spec_from_file_location(
        "site_embed", ROOT / "site" / "embed.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_chunks_cover_every_day_and_point_back_into_the_source(embed):
    days = embed.split_days(MD)
    assert [d[0] for d in days] == [1, 2]
    chunks = []
    for day, _title, off, body in days:
        for c in embed.chunk_day(body, off, max_chars=300, overlap=40):
            chunks.append({"day": day, **c})
    assert {c["day"] for c in chunks} == {1, 2}
    # offsets index the original markdown, so a chunk can be shown in context
    for c in chunks:
        first_line = c["text"].split("\n", 1)[0][:20]
        assert MD[c["start"] :].lstrip().startswith(first_line), (
            c["id"] if "id" in c else c["start"]
        )
        assert len(c["text"]) <= 300
    # the oversized paragraph is sliced, not dropped
    long_chunks = [c for c in chunks if c["day"] == 2]
    assert len(long_chunks) >= 8
    assert {c["heading"] for c in chunks if c["day"] == 1} == {"", "小節甲", "小節乙"}


def test_fake_provider_writes_the_agreed_shape(embed, tmp_path):
    src = tmp_path / "a.md"
    src.write_text(MD, encoding="utf-8")
    out = tmp_path / "e.json"
    assert (
        embed.main(
            [
                "--provider",
                "fake",
                "--article",
                str(src),
                "--out",
                str(out),
                "--dims",
                "8",
            ]
        )
        == 0
    )
    d = json.loads(out.read_text(encoding="utf-8"))
    assert (
        d["meta"]["provider"] == "fake"
        and d["meta"]["dims"] == 8
        and d["meta"]["normalized"]
    )
    assert d["meta"]["n_chunks"] == len(d["chunks"]) and d["meta"]["n_nodes"] == len(
        d["nodes"]
    )
    assert d["chunks"][0]["id"] == "d01-01" and len(d["chunks"][0]["vector"]) == 8
    assert {n["kind"] for n in d["nodes"]} == {"day", "concept", "repo"}
    assert abs(sum(x * x for x in d["chunks"][0]["vector"]) - 1) < 1e-4
    assert "key" not in json.dumps(d["meta"]).lower()


def test_missing_key_is_a_clean_exit(embed, tmp_path, monkeypatch):
    src = tmp_path / "a.md"
    src.write_text(MD, encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert embed.main(["--provider", "openai", "--article", str(src)]) == 2
    # gemini-2 is the gemini endpoint with a newer model: same key, same failure mode
    assert embed.main(["--provider", "gemini-2", "--article", str(src)]) == 2
    assert embed.PROVIDERS["gemini-2"][0].startswith("gemini-embedding-2")


def test_reuse_keeps_vectors_for_unchanged_text(embed, tmp_path, capsys):
    src = tmp_path / "a.md"
    src.write_text(MD, encoding="utf-8")
    first = tmp_path / "1.json"
    assert (
        embed.main(
            [
                "--provider",
                "fake",
                "--article",
                str(src),
                "--out",
                str(first),
                "--dims",
                "8",
            ]
        )
        == 0
    )
    # edit one paragraph of Day 1; Day 2 and every node are byte-identical
    src.write_text(MD.replace("甲的內容。甲的內容。", "甲改寫了。"), encoding="utf-8")
    second = tmp_path / "2.json"
    assert (
        embed.main(
            [
                "--provider",
                "fake",
                "--article",
                str(src),
                "--out",
                str(second),
                "--dims",
                "8",
                "--reuse",
                str(first),
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    a = json.loads(first.read_text(encoding="utf-8"))
    b = json.loads(second.read_text(encoding="utf-8"))
    by_text = {c["text"]: c["vector"] for c in a["chunks"]}
    changed = [c for c in b["chunks"] if c["text"] not in by_text]
    assert changed and all(c["day"] == 1 for c in changed)
    assert all(
        c["vector"] == by_text[c["text"]] for c in b["chunks"] if c["text"] in by_text
    )
    assert f"embedding nodes: 0 to send, {len(a['nodes'])} reused" in err
    # a different model must not be mixed in
    a["meta"]["dims"] = 16
    first.write_text(json.dumps(a), encoding="utf-8")
    assert (
        embed.main(
            [
                "--provider",
                "fake",
                "--article",
                str(src),
                "--out",
                str(second),
                "--dims",
                "8",
                "--reuse",
                str(first),
            ]
        )
        == 2
    )


def test_bad_chunk_sizes_fail_fast(embed, tmp_path):
    # step = max_chars - overlap must stay positive, otherwise range() raises or
    # text is silently dropped; both the CLI and the function refuse it up front
    src = tmp_path / "a.md"
    src.write_text(MD, encoding="utf-8")
    for extra in (
        ["--overlap", "900"],
        ["--overlap", "1000"],
        ["--overlap", "-1"],
        ["--max-chars", "0"],
    ):
        assert (
            embed.main(
                ["--provider", "fake", "--article", str(src), "--dry-run", *extra]
            )
            == 2
        )
    with pytest.raises(ValueError):
        embed.chunk_day("x", 0, max_chars=100, overlap=100)
    assert (
        embed.main(
            ["--provider", "fake", "--article", str(src), "--dry-run", "--overlap", "0"]
        )
        == 0
    )

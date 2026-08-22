"""dev-container 遙測分析腳本的單元測試（telemetry-report.py / cost-report.py）。

兩支都是 PEP 723 單檔且檔名帶連字號，不能用一般 import——比照 conftest 的
load_script 以路徑載入。所有資料都是合成的，完全離線。

釘住的行為都是「算錯不會報錯、只會給出錯的數字」的那種：
- 角色歸因走 span 父子鏈（實測派遣 span 自帶的 agent_id 會指錯人）
- usage 去重對同一則訊息取最終值（streaming 會寫多行、數字遞增，取首行少算數倍）
- cache 寫入按 5m/1h TTL 分開計價（混在一起會低估 1h 的部分）
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

OTEL_DIR = Path(__file__).resolve().parent.parent / "opentelemetry"


def load_dev_script(filename: str) -> ModuleType:
    module_name = "_otel_" + filename.removesuffix(".py").replace("-", "_")
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, OTEL_DIR / filename)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"無法載入腳本：{filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def tr() -> ModuleType:
    return load_dev_script("telemetry-report.py")


@pytest.fixture(scope="session")
def cost() -> ModuleType:
    return load_dev_script("cost-report.py")


# ---------------------------------------------------------------- telemetry


def span(span_id, start_us, dur_us, parent=None, **tags):
    tag_list = [{"key": k, "value": v} for k, v in tags.items()]
    s = {
        "traceID": "t1",
        "spanID": span_id,
        "operationName": "op",
        "startTime": start_us,
        "duration": dur_us,
        "tags": tag_list,
        "processID": "p1",
    }
    if parent:
        s["references"] = [{"refType": "CHILD_OF", "spanID": parent}]
    return s


def make_trace(spans, experiment="exp-a"):
    return {
        "traceID": "t1",
        "spans": spans,
        "processes": {
            "p1": {
                "serviceName": "claude-code",
                "tags": [
                    {"key": "experiment", "value": experiment},
                    {"key": "skill.version", "value": "2026.01.01.01"},
                ],
            }
        },
    }


SESSION = "aaaabbbb-0000-0000-0000-000000000000"


def sample_trace():
    common = {"session.id": SESSION}
    return make_trace(
        [
            # 主線程自己的一次 LLM 請求
            span(
                "s-main",
                0,
                1_000_000,
                **common,
                **{
                    "span.type": "llm_request",
                    "output_tokens": 50,
                    "cache_read_tokens": 500,
                    "cache_creation_tokens": 5,
                },
            ),
            # 派遣 span：故意帶一個「指錯人」的 agent_id，歸因不准用它
            span(
                "s-dispatch",
                500_000,
                17_000,
                **common,
                **{
                    "span.type": "tool",
                    "tool_name": "Task",
                    "subagent_type": "ncr-scan-lint",
                    "agent_id": "WRONG",
                },
            ),
            # 子代理的 LLM 請求，父子鏈掛在派遣 span 下（隔一層 tool span）
            span(
                "s-child-tool",
                1_000_000,
                3_000_000,
                parent="s-dispatch",
                **common,
                **{
                    "span.type": "tool",
                    "tool_name": "Bash",
                },
            ),
            span(
                "s-child-llm",
                1_500_000,
                2_000_000,
                parent="s-child-tool",
                **common,
                **{
                    "span.type": "llm_request",
                    "output_tokens": 100,
                    "cache_read_tokens": 1_000,
                    "cache_creation_tokens": 10,
                },
            ),
        ]
    )


def test_flatten_indexes_spans_and_resources_per_process(tr):
    spans, resources = tr.flatten([sample_trace()])
    assert len(spans) == 4
    assert spans["s-child-llm"]["_parent"] == "s-child-tool"
    assert resources[SESSION]["experiment"] == "exp-a"


def test_role_walks_parent_chain_not_agent_id(tr):
    spans, _ = tr.flatten([sample_trace()])
    dispatch = {
        k: v
        for k, v in spans.items()
        if v.get("span.type") == "tool" and v.get("tool_name") in tr.DISPATCH_TOOLS
    }
    # 隔一層也要認得回來；orphan 歸主線程
    assert tr.role_of(spans["s-child-llm"], spans, dispatch) == "ncr-scan-lint"
    assert tr.role_of(spans["s-main"], spans, dispatch) == "主線程"


def test_report_aggregates_per_role(tr, capsys):
    spans, resources = tr.flatten([sample_trace()])
    tr.report(spans, resources, None)
    out = capsys.readouterr().out
    assert "experiment=exp-a" in out
    # 子代理：LLM 2 秒、輸出 100、cache 讀 1,000
    lint = next(line for line in out.splitlines() if line.startswith("ncr-scan-lint"))
    assert "2s" in lint and "100" in lint and "1,000" in lint
    # 主線程：LLM 1 秒、輸出 50
    main = next(line for line in out.splitlines() if line.startswith("主線程"))
    assert "1s" in main and "50" in main


def test_report_session_filter_rejects_unknown_prefix(tr):
    spans, resources = tr.flatten([sample_trace()])
    with pytest.raises(SystemExit):
        tr.report(spans, resources, "ffffffff")


def test_load_files_accepts_both_shapes_and_fails_loudly(tr, tmp_path):
    api_shape = tmp_path / "api.json"
    api_shape.write_text(json.dumps({"data": [sample_trace()]}))
    bare_shape = tmp_path / "bare.json"
    bare_shape.write_text(json.dumps(sample_trace()))
    assert len(tr.load_files([str(api_shape), str(bare_shape)])) == 2

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(SystemExit):
        tr.load_files([str(broken)])


# ---------------------------------------------------------------- cost


def usage_line(req, msg, model, out, cw=None, **extra):
    u = {
        "input_tokens": extra.get("inp", 10),
        "output_tokens": out,
        "cache_read_input_tokens": extra.get("cr", 0),
    }
    if cw is not None:
        u["cache_creation"] = cw
    if "cw_flat" in extra:
        u["cache_creation_input_tokens"] = extra["cw_flat"]
    return json.dumps(
        {"requestId": req, "message": {"id": msg, "model": model, "usage": u}}
    )


def test_tally_dedupes_by_taking_final_value(cost, tmp_path):
    # streaming：同一則訊息寫三行、output 遞增，只能算最後一行
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(
            [
                usage_line("r1", "m1", "claude-haiku-4-5", 4),
                usage_line("r1", "m1", "claude-haiku-4-5", 4),
                usage_line("r1", "m1", "claude-haiku-4-5", 402),
                usage_line("r2", "m2", "claude-haiku-4-5", 7),
            ]
        )
    )
    agg = cost.tally([str(p)])
    assert agg["claude-haiku-4-5"]["out"] == 409
    assert agg["claude-haiku-4-5"]["n"] == 2


def test_cost_prices_cache_ttl_tiers_separately(cost):
    # haiku：in $1、out $5、cache 讀 0.1×、寫 5m 1.25×、寫 1h 2×（per MTok）
    u = {
        "in": 1_000_000,
        "out": 1_000_000,
        "cr": 1_000_000,
        "cw5m": 1_000_000,
        "cw1h": 1_000_000,
    }
    assert cost.cost_usd("claude-haiku-4-5-20251001", u) == pytest.approx(
        1 + 5 + 0.1 + 1.25 + 2
    )
    assert cost.cost_usd("some-unknown-model", u) is None


def test_tally_old_format_falls_back_to_flat_cache_creation(cost, tmp_path):
    # 舊格式沒有 ephemeral 細分，只有 cache_creation_input_tokens 整包
    p = tmp_path / "old.jsonl"
    p.write_text(usage_line("r1", "m1", "claude-haiku-4-5", 1, cw_flat=1234))
    agg = cost.tally([str(p)])
    assert agg["claude-haiku-4-5"]["cw5m"] == 1234
    assert agg["claude-haiku-4-5"]["cw1h"] == 0


def test_main_attributes_roles_from_subagent_meta(cost, tmp_path, monkeypatch, capsys):
    session = tmp_path / "abc123"
    sub = session / "subagents"
    sub.mkdir(parents=True)
    (tmp_path / "abc123.jsonl").write_text(
        usage_line(
            "r1",
            "m1",
            "claude-haiku-4-5",
            100,
            cw={"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
        )
    )
    (sub / "agent-x.jsonl").write_text(usage_line("r2", "m2", "claude-haiku-4-5", 200))
    (sub / "agent-x.meta.json").write_text(json.dumps({"agentType": "ncr-scan-lint"}))

    # 🔴 **測試不得連外。** main() 會去抓上游費率表，這裡用 --offline 讓它直接用快照。
    #    忘了這一行的話，這支測試會在沒網路的機器上變慢又不穩，而且是靜默的。
    monkeypatch.setattr(
        sys, "argv", ["cost-report.py", "--offline", str(tmp_path / "abc123.jsonl")]
    )
    cost.main()
    out = capsys.readouterr().out
    assert "ncr-scan-lint" in out and "主線程" in out
    assert "總成本" in out and "無牌價" not in out


def test_rate_for_picks_longest_matching_prefix(cost):
    # 前綴表若有互為前綴的項，必須取最長命中，不吃清單順序
    cost.RATES.append(("claude-haiku-4-5-turbo", (99.0, 99.0)))
    try:
        assert cost.rate_for("claude-haiku-4-5-turbo-20990101") == (99.0, 99.0)
        assert cost.rate_for("claude-haiku-4-5-20251001") == (1.0, 5.0)
    finally:
        cost.RATES.pop()


def test_main_skips_zero_usage_synthetic_without_disclaimer(
    cost, tmp_path, monkeypatch, capsys
):
    # <synthetic> usage 全 0：不進表、不觸發「不含無牌價的模型」免責
    p = tmp_path / "s.jsonl"
    p.write_text(
        usage_line("r1", "m1", "claude-haiku-4-5", 100)
        + "\n"
        + json.dumps(
            {
                "requestId": "r2",
                "message": {
                    "id": "m2",
                    "model": "<synthetic>",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["cost-report.py", str(p)])
    cost.main()
    out = capsys.readouterr().out
    assert "<synthetic>" not in out
    assert "無牌價" not in out


def test_find_session_accepts_session_dir_and_picks_latest_in_project_dir(
    cost, tmp_path, capsys
):
    import os
    import time

    # session 目錄本身（旁邊有同名 .jsonl）→ 用那份
    (tmp_path / "abc.jsonl").write_text("")
    (tmp_path / "abc").mkdir()
    main, base = cost.find_session(str(tmp_path / "abc"))
    assert main.endswith("abc.jsonl") and base.endswith("abc")
    # 專案目錄含多場 → 取 mtime 最新並說出選了誰
    old, new = tmp_path / "a-old.jsonl", tmp_path / "z-new.jsonl"
    old.write_text("")
    new.write_text("")
    # mtime 全部釘死：abc.jsonl 與 z-new.jsonl 同瞬間寫入時會平手，
    # 「挑最新」就退化成看列舉順序（CI 第一跑抓到的 flake）。
    now = time.time()
    past = now - 3600
    os.utime(tmp_path / "abc.jsonl", (past, past))
    os.utime(old, (past, past))
    os.utime(new, (now, now))
    main, _ = cost.find_session(str(tmp_path))
    assert main.endswith("z-new.jsonl")
    assert "取 mtime 最新" in capsys.readouterr().out


# ---------------------------------------------------------------- session-report


@pytest.fixture(scope="session")
def sr() -> ModuleType:
    return load_dev_script("session-report.py")


def jspan(span_id, start_us, dur_us, sid, parent=None, **tags):
    tag_list = [{"key": "session.id", "value": sid}]
    tag_list += [{"key": k, "value": v} for k, v in tags.items()]
    s = {"spanID": span_id, "startTime": start_us, "duration": dur_us, "tags": tag_list}
    if parent:
        s["references"] = [{"refType": "CHILD_OF", "spanID": parent}]
    return s


def test_split_by_session_keeps_sessions_apart(sr):
    data = [
        {"spans": [jspan("a", 0, 10, "s1-full"), jspan("b", 5, 10, "s2-full")]},
        {"spans": [jspan("c", 20, 10, "s1-full")]},
    ]
    by_sid, all_sids = sr.split_by_session(data, "s1")
    assert set(by_sid) == {"s1-full"} and len(by_sid["s1-full"]) == 2
    assert all_sids == {"s1-full", "s2-full"}
    both, _ = sr.split_by_session(data, "s")
    assert set(both) == {"s1-full", "s2-full"}  # 命中兩場要分開回，讓呼叫端拒絕合併


def test_collect_roles_attributes_by_parent_chain_and_charts_input_tokens(sr):
    spans = {
        "d1": {
            "span.type": "tool",
            "tool_name": "Task",
            "subagent_type": "ncr-scan-lint",
            "_start": 0,
            "_dur": 5,
            "_parent": None,
        },
        "c1": {
            "span.type": "llm_request",
            "model": "claude-sonnet-5",
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_tokens": 100,
            "cache_creation_tokens": 20,
            "_start": 10,
            "_dur": 30,
            "_parent": "d1",
        },
        "m1": {
            "span.type": "llm_request",
            "model": "claude-fable-5",
            "_start": 0,
            "_dur": 8,
            "_parent": None,
        },
    }
    agg, chart = sr.collect_roles(spans)
    assert set(agg) == {"主線程", "ncr-scan-lint"}
    assert agg["ncr-scan-lint"]["llm_us"] == 30
    lint_span = next(c for c in chart if c["role"] == "ncr-scan-lint")
    assert lint_span["in"] == 7 and lint_span["cr"] == 100  # tooltip 命中率分母要有輸入


def test_find_gaps_only_reports_long_idle(sr):
    chart = [{"s": 0, "e": 10_000_000}, {"s": 200_000_000, "e": 210_000_000}]
    assert sr.find_gaps(chart, gap_min_s=120) == [(10_000_000, 200_000_000)]
    assert sr.find_gaps(chart, gap_min_s=300) == []
    assert sr.find_gaps([]) == []


def test_hit_rate_zero_denominator_is_none(sr):
    assert sr.hit_rate({"in": 0, "cr": 0, "cw": 0}) is None
    assert sr.hit_rate({"in": 10, "cr": 90, "cw": 0}) == pytest.approx(0.9)


def _report(tmp_path, name, mtime, mr):
    import os

    p = tmp_path / name
    p.write_text(json.dumps({"conclusion": "Approved", "mr": mr, "findings": []}))
    os.utime(p, (mtime, mtime))
    return p


def test_find_report_json_matches_by_session_window_not_just_latest(sr, tmp_path):
    t0, t1 = 1_000_000_000 * 1_000_000, 1_000_003_600 * 1_000_000  # µs
    _report(tmp_path, "in_window.json", 1_000_003_700, {"iid": 1, "title": "對的"})
    _report(
        tmp_path,
        "newer_but_far.json",
        1_000_003_600 + 7 * 3600,
        {"iid": 2, "title": "錯的"},
    )
    got = sr.find_report_json(t0, t1, root=str(tmp_path))
    assert got and got["mr"]["iid"] == 1  # 視窗內取最近，不是全域最新
    # 視窗內一份都沒有 → None（寧可未封存，不亂配）
    assert sr.find_report_json(t0 + 10**14, t1 + 10**14, root=str(tmp_path)) is None


def test_find_report_json_accepts_null_mr_and_skips_broken_files(sr, tmp_path):
    import os

    t0, t1 = 2_000_000_000 * 1_000_000, 2_000_000_100 * 1_000_000
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    os.utime(broken, (2_000_000_050, 2_000_000_050))
    _report(tmp_path, "local.json", 2_000_000_060, None)  # local_branch：mr 是 null
    got = sr.find_report_json(t0, t1, root=str(tmp_path))
    assert got and got["mr"] is None


def test_build_page_survives_null_mr_and_escapes_script_close(sr):
    agg = {
        "主線程": {"first": 0, "last": 60_000_000, "llm_us": 1_000_000},
        "x</script>y": {"first": 0, "last": 30_000_000, "llm_us": 0},
    }
    chart = [
        {
            "role": "x</script>y",
            "s": 0,
            "e": 1_000_000,
            "kind": "llm_request",
            "label": "m",
            "in": 1,
            "out": 1,
            "cr": 0,
            "cw": 0,
        }
    ]
    page = sr.build_page(
        "sid12345",
        agg,
        chart,
        [],
        {},
        {"conclusion": "Approved", "mr": None, "findings": []},
    )
    assert "MR !" not in page  # mr null → 標題略過，不炸
    assert (
        "</script>y" not in page.split("const DATA")[1].split(";")[0]
    )  # DATA 內已逸出


def test_build_page_lists_transcript_only_roles_and_counts_their_cost(sr):
    agg = {"主線程": {"first": 0, "last": 60_000_000, "llm_us": 0}}
    tokens = {
        "主線程": {
            "in": 1,
            "out": 1,
            "cr": 0,
            "cw": 0,
            "cost": 1.0,
            "model": "fable-5",
        },
        "ncr-scan-lint": {
            "in": 9,
            "out": 9,
            "cr": 0,
            "cw": 0,
            "cost": 99.0,
            "model": "sonnet-5",
        },
    }
    page = sr.build_page("sid12345", agg, [], [], tokens, None)
    assert "trace 未拍到" in page and "ncr-scan-lint" in page
    assert "$100.00" in page  # 總成本卡含 transcript-only 角色，不靜默少算


# ------------------------------------------------- 費率來源（LiteLLM 上游 vs 快照）
#
# 這張表原本是寫死的。2026-08-10 Anthropic 宣布 Sonnet 5 推廣價永久維持（原訂 9/1 調成
# 3/15 的那次取消），寫死的那份當場把 Sonnet 高估 1.5 倍，而報表照樣印得理直氣壯。
# 改成跟 ccusage 同一個上游（LiteLLM 的 model_prices JSON）之後，這幾條守住的是：
# 解析對、fallback 會動、而且**不准連外**。

FAKE_LITELLM = {
    "claude-sonnet-5": {"input_cost_per_token": 2e-6, "output_cost_per_token": 1e-5},
    "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 2.5e-5},
    # 雲端的區域價：貴 10%，而且鍵名帶 provider 前綴。**不可以被收進來**
    "us.anthropic.claude-sonnet-5": {
        "input_cost_per_token": 2.2e-6,
        "output_cost_per_token": 1.1e-5,
    },
    "vertex_ai/claude-sonnet-5": {
        "input_cost_per_token": 2e-6,
        "output_cost_per_token": 1e-5,
    },
    # 非 Claude、以及沒有價格欄位的髒資料
    "gpt-4o": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6},
    "claude-broken": {"input_cost_per_token": None, "output_cost_per_token": None},
    "sample_spec": {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
}


def test_litellm_rates_take_first_party_keys_only(cost):
    got = dict(cost._rates_from_litellm(FAKE_LITELLM))
    assert got["claude-sonnet-5"] == (2.0, 10.0)
    # 🔴 區域價混進來的話，最長前綴比對會挑到貴 10% 的那個，而金額看起來很合理
    assert not [k for k in got if "/" in k or k.startswith(("us.", "eu.", "vertex"))]
    assert "gpt-4o" not in got  # 只收 claude-*
    assert "claude-broken" not in got  # 價格欄位不是數字就跳過，不要當 0


def test_refresh_rates_uses_upstream_then_reports_source(cost):
    try:
        src = cost.refresh_rates(FAKE_LITELLM)
        assert "LiteLLM" in src
        assert cost.rate_for("claude-sonnet-5-20260630") == (2.0, 10.0)
        # 上游沒收的型號由快照兜底，不會突然變成無牌價
        assert cost.rate_for("claude-haiku-4-5-20251001") == (1.0, 5.0)
    finally:
        cost.refresh_rates({})


def test_refresh_rates_falls_back_to_snapshot_offline(cost):
    # 🔴 連不到上游不可以讓報表死掉，也不可以靜默用舊價：要退回快照**並且說出來**
    src = cost.refresh_rates({})
    assert "快照" in src
    assert cost.rate_for("claude-sonnet-5") == (2.0, 10.0)
    assert cost.rate_for("some-unknown-model") is None


def test_snapshot_matches_upstream_shape(cost):
    # 快照與上游對同一個模型不該給出不同的價：真的分岔了就是快照該更新的訊號。
    # （這裡比的是**形狀**——上游有的那幾格要一致；沒有網路，用假資料。）
    try:
        cost.refresh_rates(FAKE_LITELLM)
        for key, snap in cost.SNAPSHOT:
            if key in FAKE_LITELLM:
                assert cost.rate_for(key) == snap, f"{key} 快照與上游分岔"
    finally:
        cost.refresh_rates({})

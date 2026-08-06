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
            span("s-main", 0, 1_000_000, **common, **{
                "span.type": "llm_request", "output_tokens": 50,
                "cache_read_tokens": 500, "cache_creation_tokens": 5,
            }),
            # 派遣 span：故意帶一個「指錯人」的 agent_id，歸因不准用它
            span("s-dispatch", 500_000, 17_000, **common, **{
                "span.type": "tool", "tool_name": "Task",
                "subagent_type": "ncr-scan-lint", "agent_id": "WRONG",
            }),
            # 子代理的 LLM 請求，父子鏈掛在派遣 span 下（隔一層 tool span）
            span("s-child-tool", 1_000_000, 3_000_000, parent="s-dispatch", **common, **{
                "span.type": "tool", "tool_name": "Bash",
            }),
            span("s-child-llm", 1_500_000, 2_000_000, parent="s-child-tool", **common, **{
                "span.type": "llm_request", "output_tokens": 100,
                "cache_read_tokens": 1_000, "cache_creation_tokens": 10,
            }),
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
    u = {"input_tokens": extra.get("inp", 10), "output_tokens": out,
         "cache_read_input_tokens": extra.get("cr", 0)}
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
    u = {"in": 1_000_000, "out": 1_000_000, "cr": 1_000_000,
         "cw5m": 1_000_000, "cw1h": 1_000_000}
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
        usage_line("r1", "m1", "claude-haiku-4-5", 100,
                   cw={"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20})
    )
    (sub / "agent-x.jsonl").write_text(usage_line("r2", "m2", "claude-haiku-4-5", 200))
    (sub / "agent-x.meta.json").write_text(json.dumps({"agentType": "ncr-scan-lint"}))

    monkeypatch.setattr(sys, "argv", ["cost-report.py", str(tmp_path / "abc123.jsonl")])
    cost.main()
    out = capsys.readouterr().out
    assert "ncr-scan-lint" in out and "主線程" in out
    assert "總成本" in out and "無牌價" not in out

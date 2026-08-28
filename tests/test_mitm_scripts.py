"""mitm/ 底下兩支腳本的測試。

redact.py 是純 stdlib，直接 import。wire_report.py 有 PEP 723 標頭、宣告了
mitmproxy，但讀檔的部分是延後 import 的，所以這裡測得到它的指標運算而不需要
裝 mitmproxy。所有資料都是合成的，完全離線。

flow 物件用最小的替身（headers 支援 items(multi=True) 與 __setitem__、
get_text 可拋例外），因為 redact 只碰得到這幾個介面。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

MITM_DIR = Path(__file__).resolve().parent.parent / "mitm"


def load_script(filename: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, MITM_DIR / filename)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rd() -> ModuleType:
    return load_script("redact.py", "_mitm_redact")


@pytest.fixture(scope="module")
def wr() -> ModuleType:
    return load_script("wire_report.py", "_mitm_wire_report")


# ----------------------------------------------------------------------------- redact


class FakeHeaders(dict):
    def items(self, multi: bool = False):
        return list(super().items())


class FakeMessage:
    def __init__(self, headers=None, text="", raise_on_text=False, content=b"x"):
        self.headers = FakeHeaders(headers or {})
        self._text = text
        self._raise = raise_on_text
        self.content = content

    def get_text(self, strict: bool = True):
        if self._raise:
            raise ValueError("undecodable")
        return self._text

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value


class FakeFlow:
    def __init__(self, request=None, response=None):
        self.request = request
        self.response = response


def test_sensitive_headers_are_replaced_wholesale(rd):
    assert (
        rd.redact_header_value("Authorization", "Bearer sk-ant-verysecret")
        == rd.REDACTED
    )
    assert rd.redact_header_value("x-api-key", "sk-ant-123") == rd.REDACTED


def test_ordinary_headers_survive(rd):
    assert (
        rd.redact_header_value("content-type", "application/json") == "application/json"
    )


def test_sensitive_json_keys_are_replaced_at_any_depth(rd):
    payload = {"a": {"b": [{"api_key": "sk-ant-1234567890abcdef", "keep": "yes"}]}}
    out = rd.redact_json_value(payload)
    assert out["a"]["b"][0]["api_key"] == rd.REDACTED
    assert out["a"]["b"][0]["keep"] == "yes"


def test_inline_secret_patterns_in_free_text(rd):
    text = "run with api_key=sk-ant-abcdefghijklmnop then done"
    out = rd.redact_text(text)
    assert "sk-ant-abcdefghijklmnop" not in out
    assert out.startswith("run with api_key=")


def test_short_values_are_not_mistaken_for_secrets(rd):
    # `token: 3` 是計數欄位，不是憑證。12 字元下限就是為了這個。
    assert rd.redact_text("token=3") == "token=3"


def test_content_is_kept_only_secrets_go(rd):
    payload = {
        "messages": [{"role": "user", "content": "審一下這段 code"}],
        "token": "abc",
    }
    out = rd.redact_json_value(payload)
    assert out["messages"][0]["content"] == "審一下這段 code"
    assert out["token"] == rd.REDACTED


def test_undecodable_body_is_blanked_not_passed_through(rd):
    msg = FakeMessage(
        headers={"content-type": "application/octet-stream"},
        raise_on_text=True,
        content=b"\xff\xfe binary",
    )
    rd._redact_message(msg)
    assert msg.content == b"<redacted: undecodable body>"


def test_redact_flow_touches_both_directions(rd):
    flow = FakeFlow(
        request=FakeMessage(
            headers={"authorization": "Bearer sk-ant-xyz1234567890"},
            text=json.dumps({"api_key": "sk-ant-1234567890ab"}),
        ),
        response=FakeMessage(headers={"set-cookie": "s=1"}, text='{"ok": true}'),
    )
    rd.redact_flow(flow)
    assert flow.request.headers["authorization"] == rd.REDACTED
    assert json.loads(flow.request.text)["api_key"] == rd.REDACTED
    assert flow.response.headers["set-cookie"] == rd.REDACTED
    assert json.loads(flow.response.text)["ok"] is True


# ------------------------------------------------------------------------- wire_report


def test_common_prefix_length_is_exact(wr):
    assert wr._common_prefix_len(b"abcdef", b"abcXYZ") == 3
    assert wr._common_prefix_len(b"", b"abc") == 0
    assert wr._common_prefix_len(b"same", b"same") == 4


def test_breakpoints_counted_at_any_depth(wr):
    request = {
        "system": [
            {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}
        ],
        "tools": [{"name": "t"}, {"name": "u", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    assert wr._count_all_breakpoints(request) == 2


def test_breakpoint_sites_report_where_not_just_how_many(wr):
    request = {
        "system": [
            {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}
        ],
        "tools": [{"name": "t"}, {"name": "u", "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "x",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ],
    }
    # system 只有一項＝最後一項，tools 掛在最後一個工具上，messages 標到「第幾則」。
    assert wr._breakpoint_sites(request) == ["system[-1]", "tools[-1]", "messages[-1]"]


def test_breakpoint_in_an_unknown_section_is_still_disclosed(wr):
    """API 之後在別的欄位放斷點時，不能靜靜漏掉——總數要對得上。"""
    request = {"future_field": [{"cache_control": {"type": "ephemeral"}}]}
    assert wr._breakpoint_sites(request) == ["其他×1"]


def test_lane_key_follows_the_first_message(wr):
    a = {
        "messages": [
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "x"},
        ]
    }
    b = {"messages": [{"role": "user", "content": "第一句"}]}
    c = {"messages": [{"role": "user", "content": "另一條對話"}]}
    assert wr._lane_key(a) == wr._lane_key(b)
    assert wr._lane_key(a) != wr._lane_key(c)


def test_sse_usage_merges_start_and_delta(wr):
    stream = (
        "event: message_start\n"
        'data: {"type":"message_start","message":{"usage":'
        '{"input_tokens":10,"cache_read_input_tokens":900,"output_tokens":1}}}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n'
        "data: [DONE]\n"
    )
    usage = wr._sse_usage(stream)
    assert usage["input_tokens"] == 10
    assert usage["cache_read_input_tokens"] == 900
    # delta 覆蓋 start 的暫定值，取第一個會少算好幾倍。
    assert usage["output_tokens"] == 42


def test_repeat_compares_within_a_lane_not_across_time(wr):
    """subagent 交錯進來時，拿時間相鄰的兩發去比會得到沒有意義的數字。"""
    rows = [
        {"ts": 1, "lane": "main", "_raw_up": b"PREFIX-aaa", "up": 10},
        {"ts": 2, "lane": "sub", "_raw_up": b"OTHER-zzz", "up": 9},
        {"ts": 3, "lane": "main", "_raw_up": b"PREFIX-bbb", "up": 10},
    ]
    wr.annotate_repeat(rows)
    assert rows[0]["repeat"] == 0  # 這條對話的第一發，沒有前一發可比
    assert rows[1]["repeat"] == 0  # 另一條對話的第一發
    assert rows[2]["repeat"] == len(b"PREFIX-")  # 跟同一條的前一發比，不是跟 sub 比


def test_summary_totals_and_ratio(wr):
    rows = [
        {
            "ts": 1,
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "method": "POST",
            "status": 200,
            "up": 100,
            "down": 40,
            "req_encoding": "",
            "resp_encoding": "",
            "model": "claude-sonnet-5",
            "lane": "main",
            "breakpoints": 2,
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 90,
                "output_tokens": 7,
            },
            "repeat": 60,
        },
        {
            "ts": 2,
            "host": "api.anthropic.com",
            "path": "/v1/messages/count_tokens",
            "method": "POST",
            "status": 200,
            "up": 20,
            "down": 10,
            "req_encoding": "",
            "resp_encoding": "",
            "model": None,
            "lane": None,
            "breakpoints": 0,
            "usage": {},
            "repeat": 0,
        },
    ]
    summary = wr.summarize(rows)
    cap = summary["capture"]
    assert cap["requests"] == 2
    assert cap["up"] == 120
    assert cap["down"] == 50
    assert cap["repeated"] == 60
    assert cap["repeated_ratio"] == pytest.approx(0.5)
    assert cap["requests_compressed"] == 0
    # 端點依上行量排序，非模型端點也要列出來——「還有誰在講話」是這份報表的重點之一。
    assert [e["path"] for e in summary["endpoints"]] == [
        "/v1/messages",
        "/v1/messages/count_tokens",
    ]


def test_unpriced_model_is_flagged_not_guessed(wr):
    rows = [
        {
            "ts": 1,
            "host": "h",
            "path": "/v1/messages",
            "method": "POST",
            "status": 200,
            "up": 1,
            "down": 1,
            "req_encoding": "",
            "resp_encoding": "",
            "model": "claude-model-that-does-not-exist",
            "lane": "m",
            "breakpoints": 0,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "repeat": 0,
        }
    ]
    summary = wr.summarize(rows)
    assert summary["models"][0]["priced"] is False


def test_page_renders_without_external_assets(wr):
    rows = [
        {
            "ts": 1.0,
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "method": "POST",
            "status": 200,
            "up": 100,
            "down": 40,
            "req_encoding": "",
            "resp_encoding": "",
            "model": "claude-sonnet-5",
            "lane": "main",
            "breakpoints": 1,
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 90,
                "output_tokens": 7,
            },
            "repeat": 60,
        }
    ]
    page = wr.build_page("flows-test.mitm", wr.summarize(rows), rows)
    assert "<svg" in page
    # 這頁要能離線開、能寄給別人：不可以有任何外連。
    assert "http://" not in page.replace("http://localhost", "")
    assert "https://" not in page
    assert "cdn" not in page.lower()


def test_firewall_counters_are_parsed(wr):
    dump = """# generated at 2026-08-06T20:00:00+08:00
Chain OUTPUT (policy DROP 0 packets, 0 bytes)
 pkts bytes target     prot opt in     out     source               destination
 3489   13M ACCEPT     0    --  *      lo      0.0.0.0/0            0.0.0.0/0
   68  4080 ACCEPT     0    --  *      *       0.0.0.0/0            0.0.0.0/0            match-set allowed-domains dst
   60  3600 REJECT     0    --  *      *       0.0.0.0/0            0.0.0.0/0            reject-with icmp-admin-prohibited
"""
    fw = wr.parse_firewall(dump)
    assert fw["blocked"] == 60
    assert fw["allowed"] == 3489 + 68
    assert fw["chains"][0]["policy"] == "DROP"


def test_missing_firewall_file_is_disclosed_not_treated_as_clean(wr):
    """沒有量到，跟量到零，是兩件完全不同的事。"""
    card = wr.build_firewall_card({"firewall": None, "meta": None})
    assert "是沒有量，不是沒有擋" in card
    # 不可以出現任何數字，那會讓人以為量到了零
    assert "放行" not in card


def test_unrestricted_session_says_why_there_are_no_counters(wr):
    card = wr.build_firewall_card(
        {"firewall": None, "meta": {"network": "unrestricted"}}
    )
    assert "完全開放" in card


def test_sidecars_are_optional(wr, tmp_path):
    capture = tmp_path / "flows-20260806T120000.mitm"
    capture.write_bytes(b"")
    assert wr.load_sidecars(capture) == {"meta": None, "firewall": None}


def test_capture_can_be_named_by_its_session_directory(wr, tmp_path):
    session = tmp_path / "a4d27229-8110-4c73-b1b9-89780b924abd"
    session.mkdir()
    (session / "flows.mitm").write_bytes(b"")
    assert wr.resolve_capture(session) == session / "flows.mitm"
    assert wr.resolve_capture(session / "flows.mitm") == session / "flows.mitm"


def test_sidecars_found_in_the_session_directory(wr, tmp_path):
    session = tmp_path / "sess"
    session.mkdir()
    capture = session / "flows.mitm"
    capture.write_bytes(b"")
    (session / "meta.json").write_text('{"session_id": "abc", "network": "restricted"}')
    (session / "firewall.txt").write_text(
        "Chain OUTPUT (policy DROP 0 packets, 0 bytes)\n"
        " 5 300 REJECT 0 -- * * 0.0.0.0/0 0.0.0.0/0 reject-with icmp-admin-prohibited\n"
    )
    got = wr.load_sidecars(capture)
    assert got["meta"]["session_id"] == "abc"
    assert got["firewall"]["blocked"] == 5


def test_script_embedded_json_cannot_close_the_script_tag(wr):
    """capture 裡的字串會被嵌進 <script>。json.dumps 不逸出 `<`，所以要自己來。"""
    out = wr.json_for_script({"path": "/x</script><script>alert(1)</script>"})
    assert "</script>" not in out
    assert "<" not in out and ">" not in out
    assert "\\u003c" in out


def test_hostile_path_from_capture_does_not_reach_the_page_as_markup(wr):
    """path 是容器裡的東西影響得到的資料，不可以在 host 的瀏覽器變成標記。"""
    hostile = "/v1/messages</script><img src=x onerror=alert(1)>"
    rows = [
        {
            "ts": 1.0,
            "host": "api.anthropic.com",
            "path": hostile,
            "method": "POST",
            "status": 200,
            "up": 100,
            "down": 40,
            "req_encoding": "",
            "resp_encoding": "",
            "model": "claude-sonnet-5",
            "lane": "main",
            "breakpoints": 1,
            "breakpoint_sites": ["messages[-1]"],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "repeat": 0,
        }
    ]
    page = wr.build_page("flows.mitm", wr.summarize(rows), rows)
    # 守的是「不會形成標記」，不是「這串字不准出現」。逸出之後字還在、但已經是文字。
    assert "</script><img" not in page
    assert "<img src=x" not in page
    assert "&lt;/script&gt;&lt;img" in page  # 表格：HTML 逸出
    assert "\\u003c/script\\u003e" in page  # <script> 裡：JSON 逸出


def test_iptables_counter_survives_the_k_m_g_shorthand(wr):
    """我們自己的 dump 加了 -x，但那之前錄的檔案還在，解析不能因此炸掉。"""
    assert wr._counter("1234") == 1234
    assert wr._counter("13K") == 13_000
    assert wr._counter("4.8M") == 4_800_000
    assert wr._counter("target") is None
    dump = (
        "Chain OUTPUT (policy DROP 0 packets, 0 bytes)\n"
        " 3489K  13M ACCEPT 0 -- * lo 0.0.0.0/0 0.0.0.0/0\n"
        "   60 3600 REJECT 0 -- * * 0.0.0.0/0 0.0.0.0/0 reject-with icmp-admin-prohibited\n"
    )
    fw = wr.parse_firewall(dump)  # 先前這行會 ValueError，整份報表產不出來
    assert fw["blocked"] == 60
    assert fw["allowed"] == 3_489_000


def test_query_string_secrets_are_redacted(rd):
    path = "/callback?code=abc123def456&state=xyz789abcdef&keep=1"
    out = rd.redact_query(path)
    assert "abc123def456" not in out
    assert "xyz789abcdef" not in out
    assert "keep=1" in out


def test_lane_key_does_not_collide_on_a_long_shared_prefix(wr):
    """subagent 的派遣 prompt 常共用一大段前言，截前綴會把它們判成同一條。"""
    shared = "前言" * 3000
    a = {"messages": [{"role": "user", "content": shared + "任務 A"}]}
    b = {"messages": [{"role": "user", "content": shared + "任務 B"}]}
    assert wr._lane_key(a) != wr._lane_key(b)


# --------------------------------------------------------------- capture_addon 的不變式
#
# addon 本身 import mitmproxy，測試環境沒有它，所以用最小替身把它需要的介面補上。
# 守的是這支腳本存在的兩個理由：出錯就丟、以及絕不動到活的 flow。


class _FakeWriter:
    def __init__(self):
        self.records = []

    def add(self, flow):
        self.records.append(flow)


class _FakeFlowForAddon:
    def __init__(self, host="api.anthropic.com"):
        self.id = "flow-1"
        self.request = type("R", (), {"pretty_host": host})()
        self.response = type("S", (), {"status_code": 200})()
        self.websocket = None
        self.copied = False

    def copy(self):
        clone = _FakeFlowForAddon(self.request.pretty_host)
        clone.id = "a-different-id"  # mitmproxy 的 copy() 會發新 id
        clone.is_copy = True
        self.copied = True
        return clone


def _addon(monkeypatch, redact_impl):
    """載入 capture_addon，把它的 mitmproxy 相依換成替身。"""
    import sys
    import types

    mitm = types.ModuleType("mitmproxy")
    ctx = types.SimpleNamespace(
        options=types.SimpleNamespace(
            capture_out="", capture_hosts="api.anthropic.com"
        ),
        log=types.SimpleNamespace(
            info=lambda *a: None, warn=lambda *a: None, error=lambda *a: None
        ),
    )
    mitm.ctx = ctx
    mitm.io = types.SimpleNamespace(FlowWriter=lambda fh: _FakeWriter())
    monkeypatch.setitem(sys.modules, "mitmproxy", mitm)
    redact_mod = types.ModuleType("redact")
    redact_mod.redact_flow = redact_impl
    monkeypatch.setitem(sys.modules, "redact", redact_mod)
    mod = load_script("capture_addon.py", "_mitm_capture_addon")
    inst = mod.RedactedCapture()
    inst._fh = type("F", (), {"flush": lambda self: None})()
    inst._writer = _FakeWriter()
    return inst, ctx


def test_addon_writes_a_copy_and_never_touches_the_live_flow(monkeypatch):
    """動到活的 flow，使用者收到的回應就會變成 <redacted>。"""
    scrubbed = []
    inst, _ = _addon(monkeypatch, lambda f: scrubbed.append(f))
    live = _FakeFlowForAddon()
    inst.response(live)
    assert live.copied is True
    assert scrubbed and getattr(scrubbed[0], "is_copy", False) is True
    assert scrubbed[0] is not live
    assert inst._writer.records[0].id == live.id  # 紀錄沿用活 flow 的 id


def test_addon_drops_the_flow_when_redaction_raises(monkeypatch):
    """fail-closed：脫敏出錯就整條丟掉，絕不改寫成未脫敏版落地。"""

    def boom(_flow):
        raise RuntimeError("脫敏爆了")

    inst, _ = _addon(monkeypatch, boom)
    inst.response(_FakeFlowForAddon())
    assert inst._writer.records == []
    assert inst.dropped == 1
    assert inst.written == 0


def test_addon_skips_hosts_outside_the_list(monkeypatch):
    inst, _ctx = _addon(monkeypatch, lambda f: None)
    inst.response(_FakeFlowForAddon(host="example.com"))
    assert inst._writer.records == []
    assert inst.dropped == 0  # 不是錯誤，是不收


def test_redacted_copy_keeps_the_body_compact(rd):
    """重新序列化不可以把 body 撐大——下游拿這顆檔案的 byte 數當「線上傳了多少」。"""
    import json as _json

    original = _json.dumps(
        {
            "model": "m",
            "tools": [{"name": "a"}, {"name": "b"}],
            "token": "sk-ant-1234567890",
        },
        separators=(",", ":"),
    )
    msg = FakeMessage(headers={"content-type": "application/json"}, text=original)
    rd._redact_message(msg)
    assert ", " not in msg.text and '": ' not in msg.text
    # 只有被換掉的那個值造成長度差，結構本身不膨脹
    assert len(msg.text) == len(original) - len("sk-ant-1234567890") + len(rd.REDACTED)


def test_cache_boundary_picks_the_deepest_marker_on_a_tie(wr):
    """同一段裡有兩個標記時，決定省下多少的是比較深的那一個。"""
    assert wr.cache_boundary(["system[0]", "messages[3]", "messages[-1]"]).startswith(
        "整段對話"
    )


def test_endpoint_authority_keeps_the_port_when_it_is_not_the_default(wr):
    """port 是端點身分的一部分，不是可有可無的裝飾。

    `pretty_host` 不含 port，而端點清單按 (host, path) 聚合——同一台主機上不同 port 的
    兩個服務會塌成同一列，兩份流量被加在一起。實測 2026-08-28：`gitlab-proxy:5678`
    印成 `gitlab-proxy`。
    """
    assert (
        wr.endpoint_authority("api.anthropic.com", 443, "https") == "api.anthropic.com"
    )
    assert wr.endpoint_authority("example.com", 80, "http") == "example.com"
    assert wr.endpoint_authority("gitlab-proxy", 5678, "http") == "gitlab-proxy:5678"
    assert (
        wr.endpoint_authority("api.anthropic.com", 8443, "https")
        == "api.anthropic.com:8443"
    )
    # scheme 不明時寧可多印一個數字，也不要無聲地把兩個端點併成一個
    assert wr.endpoint_authority("h", 443, "") == "h:443"
    assert wr.endpoint_authority("h", None, "https") == "h"


def test_same_host_different_ports_do_not_collapse_into_one_endpoint(wr):
    """這才是 port 消失的真正代價：兩個服務的流量被加在一起。"""
    rows = [
        {
            "ts": 1,
            "host": "h",
            "port": 80,
            "scheme": "http",
            "path": "/a",
            "up": 10,
            "down": 1,
            "req_encoding": "",
        },
        {
            "ts": 2,
            "host": "h",
            "port": 9000,
            "scheme": "http",
            "path": "/a",
            "up": 20,
            "down": 2,
            "req_encoding": "",
        },
    ]
    summary = wr.summarize(rows)
    eps = {e["authority"]: e for e in summary["endpoints"]}
    assert set(eps) == {"h", "h:9000"}
    assert eps["h"]["up"] == 10 and eps["h:9000"]["up"] == 20


def test_site_labels_say_where_the_boundary_ends_not_which_message(wr):
    """措詞要讀得出「存到哪為止」。

    先前 `messages[-1]` 印成「最後一則訊息」，而在一張每列都是一次請求的表裡，那六個字
    第一眼會被讀成「最後一次請求」——滿滿一欄看起來像壞掉，實際上那是最健康的情況。
    """
    assert wr.site_label("messages[-1]") == "整段對話"
    assert wr.site_label("messages[166]") == "對話前 167 則"
    assert wr.site_label("tools[-1]") == "工具定義為止"
    assert wr.site_label("system[0]") == "系統提示第 1 段為止"
    # 只報最深的那一個，其餘帶過——並講清楚被略過的是「較淺的」，不是同級的另一個選擇
    assert (
        wr.cache_boundary(["system[0]", "messages[-1]"])
        == "整段對話（另有 1 個較淺的）"
    )
    assert wr.cache_boundary([]) == "沒有標記（整段重算）"


def test_compression_note_counts_only_the_table_it_sits_under(wr):
    """註腳掛在「每一個模型呼叫」底下，就不能拿整顆 capture 的壓縮次數來講。

    實測那一場的 3 次壓縮全是 git clone 的 upload-pack、模型呼叫一次都沒有，
    於是註腳說「3 次請求有壓縮」，讀的人卻在表裡怎麼也找不到那三列。
    """
    rows = [
        {"model": "claude-opus-5", "req_encoding": ""},
        {"model": None, "req_encoding": "gzip"},  # git clone，不在這張表裡
    ]
    note = wr.calls_compression_note(rows, capture_compressed=1)
    assert note.startswith("這張表裡的模型呼叫都沒有壓縮")
    assert "另有 1 次" in note

    rows[0]["req_encoding"] = "gzip"
    assert wr.calls_compression_note(rows, capture_compressed=2).startswith(
        "1 次模型呼叫有壓縮"
    )
    assert (
        wr.calls_compression_note([{"model": "m", "req_encoding": ""}], 0)
        == "沒有任何一次請求壓縮過，所以這些數字可以直接跟網卡上的量對帳。"
    )


def test_requests_without_messages_do_not_share_a_lane(wr):
    rows = [
        {"ts": 1, "lane": wr._lane_key({}), "_raw_up": b"AAAA-1", "up": 6},
        {"ts": 2, "lane": wr._lane_key({}), "_raw_up": b"AAAA-2", "up": 6},
    ]
    wr.annotate_repeat(rows)
    assert rows[1]["repeat"] == 0  # 兩者不該互比


def test_null_usage_values_do_not_crash_the_report(wr):
    rows = [
        {
            "ts": 1,
            "host": "h",
            "path": "/v1/messages",
            "method": "POST",
            "status": 200,
            "up": 1,
            "down": 1,
            "req_encoding": "",
            "resp_encoding": "",
            "model": "claude-sonnet-5",
            "lane": "m",
            "breakpoints": 0,
            "breakpoint_sites": [],
            "usage": {"input_tokens": None, "output_tokens": 5},
            "repeat": 0,
        }
    ]
    summary = wr.summarize(rows)  # 先前這行 TypeError，整份報表掛掉
    assert summary["models"][0]["priced"] is True


# ------------------------------------------------------------------ entrypoint 的 run_cli
#
# 開了錄製之後 run_cli 走背景執行 ＋ trap（為了讓 docker stop 收得到尾）。
# 那條路徑上 stdin 很容易被吃掉：非互動 shell 沒有 job control，背景命令的 stdin
# 依 POSIX 被指到 /dev/null。症狀是「開錄的 session 鍵盤沒反應」，而且用 -p 測不出來。


def _run_cli_source() -> str:
    """只抽 run_cli 這個函式。

    第一版切到 `resolve_session_id "$@"` 為止，把後面整份選單都抓了進來：跑起來時
    `read` 先吃掉餵給它的 stdin，再把那行字原樣印進「無效輸入」訊息裡，斷言就命中了。
    測試兩邊都綠，卻跟它要守的東西無關。切函式就要切到函式的結尾。
    """
    entrypoint = (
        Path(__file__).resolve().parent.parent / "dev-container" / "entrypoint.sh"
    )
    src = entrypoint.read_text()
    start = src.index("run_cli() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def test_run_cli_passes_stdin_through_on_the_capture_path():
    import subprocess

    script = (
        "NCR_INJECT_SESSION=0\n"
        "CAPTURE_PID=1\n"  # 非空 ＝ 走背景那條路徑
        "stop_capture() { :; }\n"
        "write_capture_sidecar() { :; }\n"
        # run_cli 啟動 CLI 前會呼叫它（見 entrypoint 的 prepare_token_fd）。這支守的是
        # stdin、不是憑證，所以 stub 掉。不 stub 的話斷言其實照樣過（實測），只是 stderr
        # 會多一行 command not found——真的壞掉那天，噪音會蓋掉訊號。
        "prepare_token_fd() { :; }\n" + _run_cli_source() + "\nrun_cli cat\n"
    )
    out = subprocess.run(
        ["bash", "-c", script],
        input="鍵盤打進去的字\n",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert "鍵盤打進去的字" in out.stdout, (
        f"stdin 沒有傳進去（背景執行吃掉了）：{out.stdout!r} {out.stderr!r}"
    )

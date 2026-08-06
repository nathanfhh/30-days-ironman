"""落盤前的脫敏規則。

錄流量錄的是自己跟模型之間的完整對話，那正是這件事的目的；但憑證不該跟著落地。
這支負責的就是這條界線：**抹掉 secret，保留 content**。

三層規則，由可靠到盡力：

1. **敏感標頭**——名字在清單裡就整個換掉。可靠，因為認的是名字不是值。
2. **敏感 JSON key**——遞迴走到任何深度，key 命中就換掉值。同樣認名字。
3. **自由文字裡的 secret 樣式**——`api_key: …`、`authorization=…` 這種。
   這一層是盡力而為：換個寫法、換個 key 名稱（AWS 的 `AKIA…`、GitHub 的 `ghp_…`、
   PEM 區塊）就抓不到。不要把它當保證。

不做長度截斷。截斷是給「要餵給模型的摘要」用的，這裡要的是完整的原始流量。

刻意不 import mitmproxy：這支只用 duck typing 碰 flow 物件，所以離線也能單元測試。
"""

from __future__ import annotations

import json
import re
from typing import Any

REDACTED = "<redacted>"

# 認名字，不認值——所以這一層是可靠的。
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "anthropic-api-key",
    "x-anthropic-api-key",
}

SENSITIVE_JSON_KEYS = {
    "authorization",
    "proxy_authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api_key",
    "api-key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "secret",
    "client_secret",
    "password",
    "session",
    "session_id",
}

# key 名稱兩側的引號是選配，所以 JSON 形式（"api_key": "sk-…"）與裸寫法
# （api_key=sk-…、api_key: sk-…）都吃得到。12 字元下限是為了不要把
# `token: 3` 這種計數欄位誤當成憑證。
SECRET_TEXT_PATTERNS = [
    re.compile(r"(?i)(['\"]?authorization['\"]?\s*[:=]\s*['\"]?)(bearer\s+)?[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(['\"]?x-api-key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(['\"]?api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(['\"]?access_token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(['\"]?refresh_token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{12,}"),
]


def redact_text(value: str) -> str:
    """自由文字裡的 secret 樣式換掉，其餘原樣保留。"""
    for pattern in SECRET_TEXT_PATTERNS:
        value = pattern.sub(r"\1" + REDACTED, value)
    return value


def redact_header_value(name: str, value: str) -> str:
    if name.lower() in SENSITIVE_HEADER_NAMES:
        return REDACTED
    return redact_text(value)


def redact_json_value(value: Any) -> Any:
    """遞迴脫敏。dict 的 key 命中清單就換值，其餘往下走；字串走文字樣式。"""
    if isinstance(value, dict):
        return {
            str(k): REDACTED if str(k).lower() in SENSITIVE_JSON_KEYS else redact_json_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _parse_json(text: str) -> Any | None:
    """看起來像 JSON 才解析。SSE 的 body 不是 JSON，會落到 None、走文字路徑。"""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


QUERY_SECRET = re.compile(
    r"(?i)([?&][^=&]*(?:key|token|secret|password|code|state|signature)[^=&]*=)[^&#]+")


def redact_query(path: str) -> str:
    """URL 的 query string 也可能帶憑證。

    預設只錄模型 API，憑證都在 header 裡，query 乾淨。但 capture_hosts 是可調的，
    手動錄製更可以指到任何地方——OAuth 的 `code=`、`access_token=` 就在網址上，
    而網址會原樣寫進 .mitm。這一層跟自由文字那層一樣是盡力而為，認的是常見的
    參數名稱。
    """
    return QUERY_SECRET.sub(r"\1" + REDACTED, path)


def _redact_message(msg: Any) -> None:
    """就地脫敏一則 HTTP message（request 或 response）。"""
    for name in {n for n, _ in msg.headers.items(multi=True)}:
        msg.headers[name] = redact_header_value(name, msg.headers[name])

    # trailer 跟 header 同一種東西，只是在 body 後面。同樣的規則套一次。
    trailers = getattr(msg, "trailers", None)
    if trailers:
        for name in {n for n, _ in trailers.items(multi=True)}:
            trailers[name] = redact_header_value(name, trailers[name])

    path = getattr(msg, "path", None)
    if isinstance(path, str) and "?" in path:
        msg.path = redact_query(path)

    try:
        text = msg.get_text(strict=False)
    except Exception:  # noqa: BLE001 - 解不出文字就走下面的 fail-closed，不必分辨是哪種錯
        text = None

    if text is None:
        # 有 body 但解不出文字。**寧可整塊丟掉也不寫出沒掃過的位元組**——
        # 這是整支腳本的預設姿態：不確定就不落地。
        if msg.content:
            msg.content = b"<redacted: undecodable body>"
        return

    parsed = _parse_json(text)
    if parsed is not None:
        msg.text = json.dumps(redact_json_value(parsed), ensure_ascii=False)
    else:
        msg.text = redact_text(text)


def redact_flow(flow: Any) -> None:
    """就地脫敏一整條 flow。

    呼叫端**必須**傳 `flow.copy()`，不可以傳活的 flow：在 live proxy 裡，response hook
    改到的東西會真的送到 client 手上，脫敏活 flow 等於讓 Claude 收到一份 `<redacted>`。
    """
    if getattr(flow, "request", None) is not None:
        _redact_message(flow.request)
    if getattr(flow, "response", None) is not None:
        _redact_message(flow.response)

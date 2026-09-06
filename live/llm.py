"""live.llm —— 课程网关的 LLM 客户端。纯标准库（urllib），Anthropic Messages 协议。

    text = llm.chat(system, messages, max_tokens=800, tag="coder")
    blocks, stop = llm.chat_tools(system, messages, tools, tag="agent")

每次调用都：
  1. 先问 bus.current().check_budget() —— 预算在花钱之前查（第四课 A 部分的规矩）；
  2. 发 llm.call（发出去的 system + messages，单块截 6000 字；LIVE_FULL_PROMPTS=1 不截）；
  3. POST /v1/messages；429/5xx 重试三次；别的错抛 GatewayError（不再 sys.exit —— 让看板看到 stop）；
  4. 用服务端返回的 usage 记 token（不估算），按价目表算成本，发 llm.done，累计到 Run（总账 + 按 tag）。

端点解析顺序：PARALLIGHT_BASE_URL/PARALLIGHT_API_KEY（课程网关）→ ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY（自带 key）
→ ANTHROPIC_AUTH_TOKEN。从 demos/.env、examples/.env、当前目录 .env 读。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent


def _load_env() -> None:
    for candidate in (DEMOS / ".env", DEMOS.parent / "examples" / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.split(" #", 1)[0].strip().strip('"').strip("'")
            if key.strip() and val and key.strip() not in os.environ:
                os.environ[key.strip()] = val
        break


_load_env()

_KEY_VARS = ("PARALLIGHT_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_BASE_VARS = ("PARALLIGHT_BASE_URL", "ANTHROPIC_BASE_URL")
BASE = (os.environ.get("PARALLIGHT_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
KEY = next((os.environ[v] for v in _KEY_VARS if os.environ.get(v)), "")
# 用的是哪个变量、还有哪些也被设了 —— shell 里一个残留的 ANTHROPIC_API_KEY 会静默压过刚注入的
# ANTHROPIC_AUTH_TOKEN，表现是「key 明明是新的却 401」。preflight.py 会把这个当面说清楚。
KEY_SOURCE = next((v for v in _KEY_VARS if os.environ.get(v)), None)
KEY_ALSO_SET = [v for v in _KEY_VARS if os.environ.get(v) and v != KEY_SOURCE]
BASE_SOURCE = next((v for v in _BASE_VARS if os.environ.get(v)), None)
MODEL = os.environ.get("MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"

# 第二个模型：判题的（a0 的 checker）、被观察的弱模型（c1 采样 / c2 写码 / c4 路由 / c10 学生）。
# 课程网关上是 haiku；换了 provider（比如 GLM）就得显式给 SMALL_MODEL，否则退回 MODEL——
# 退回意味着「做题的和判题的是同一个模型」、「弱模型的 pass@k 曲线变成平线」，preflight.py 会当面说清楚。
SMALL = (os.environ.get("SMALL_MODEL") or os.environ.get("ANTHROPIC_SMALL_MODEL")
         or ("claude-haiku-4-5" if MODEL.startswith("claude-") else MODEL))
CLIP = None if os.environ.get("LIVE_FULL_PROMPTS") == "1" else 6000

# 价目：$ / 百万 token（输入, 输出）。按模型名前缀匹配；不认识的按 sonnet 估并标 price_known=False
# （账目本身用服务端返回的真实 token 数，只有单价可能不准——页面会标出来）
PRICES = {"claude-sonnet-5": (3.0, 15.0), "claude-sonnet-4": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
          "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-5": (5.0, 25.0), "claude-opus": (5.0, 25.0),
          "glm-4.7": (0.6, 2.2), "glm-4-7": (0.6, 2.2), "glm-5": (0.6, 2.2), "zai.glm": (0.6, 2.2),
          "kimi": (0.6, 2.5), "minimax": (0.6, 1.2), "deepseek": (0.3, 1.2)}

_counter_lock = threading.Lock()
_counters: dict[str, int] = {}


def _bus():
    """拿 live.bus。既支持 `from live import llm`（包内相对导入），也支持直接 `python3 live/llm.py` 冒烟（那时没有父包）。"""
    try:
        from . import bus
    except ImportError:
        sys.path.insert(0, str(DEMOS))
        from live import bus
    return bus


class GatewayError(RuntimeError):
    pass


def price_of(model: str) -> tuple[tuple[float, float], bool]:
    for k, v in PRICES.items():
        if model.startswith(k):
            return v, True
    return PRICES["claude-sonnet-5"], False


def cost(model: str, in_tok: int, out_tok: int) -> float:
    (pi, po), _ = price_of(model)
    return in_tok / 1e6 * pi + out_tok / 1e6 * po


def est_tokens(text: str) -> int:
    """粗估（只用于「发出去多大」的提示，账目一律用服务端 usage）。"""
    return len(text) // 4 + 1


def _clip(s):
    if CLIP is None or not isinstance(s, str) or len(s) <= CLIP:
        return s
    return s[:CLIP] + f"\n…[截断，共 {len(s)} 字]"


def _clip_messages(messages: list) -> list:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append({"role": m["role"], "content": _clip(c)})
        else:
            blocks = []
            for b in c or []:
                b2 = dict(b)
                for k in ("text", "content"):
                    if isinstance(b2.get(k), str):
                        b2[k] = _clip(b2[k])
                    elif isinstance(b2.get(k), list):
                        b2[k] = [{**x, "text": _clip(x.get("text", ""))} if isinstance(x, dict) else x for x in b2[k]]
                blocks.append(b2)
            out.append({"role": m["role"], "content": blocks})
    return out


def _next_id(tag: str) -> str:
    bus = _bus()
    with _counter_lock:
        _counters[tag] = _counters.get(tag, 0) + 1
        run = bus.current()
        pre = f"p{os.getpid() % 10000}·" if (run and run.client_url) else ""     # 子进程：id 带 pid，别和别的轮撞
        return f"{pre}{tag}-{_counters[tag]}"


def _post(body: dict, timeout: int) -> dict:
    if not KEY:
        raise GatewayError("没有 API key：把 demos/.env.example 复制成 demos/.env，贴上 plk_ 网关 key")
    req = urllib.request.Request(f"{BASE}/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise GatewayError(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise GatewayError(last)
    raise GatewayError(last or "unknown")


def _call(system: str, messages: list, *, max_tokens: int, model: str | None, temperature: float,
          tag: str, tools: list | None, timeout: int) -> dict:
    bus = _bus()
    run = bus.current()
    model = model or MODEL
    if run:
        run.check_budget()
    cid = _next_id(tag)
    if run:
        run.emit("llm.call", id=cid, tag=tag, model=model, system=_clip(system), messages=_clip_messages(messages),
                 tools=[t["name"] for t in (tools or [])], sys_chars=len(system),
                 msg_chars=len(json.dumps(messages, ensure_ascii=False)),
                 est_in=est_tokens(system) + est_tokens(json.dumps(messages, ensure_ascii=False)))
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    t0 = time.time()
    try:
        data = _post(body, timeout)
    except GatewayError as e:
        if run:
            run.emit("llm.done", id=cid, tag=tag, model=model, **{"in": 0, "out": 0}, cache_read=0, sec=round(time.time() - t0, 2),
                     cost=0.0, price_known=True, stop_reason="error", text=str(e), tool_uses=[], error=str(e),
                     cum=run.totals(), tag_cum=run.totals(tag))
        raise
    sec = time.time() - t0
    usage = data.get("usage", {}) or {}
    in_tok, out_tok = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    served = data.get("model", model)
    (pi, po), known = price_of(served)
    c = in_tok / 1e6 * pi + out_tok / 1e6 * po
    content = data.get("content", []) or []
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    tool_uses = [{"name": b.get("name"), "input": b.get("input")} for b in content if b.get("type") == "tool_use"]
    if run:
        cum, tag_cum = run.account(tag, in_tok, out_tok, c, sec)
        run.emit("llm.done", id=cid, tag=tag, model=served, **{"in": in_tok, "out": out_tok}, cache_read=cache_read,
                 sec=round(sec, 2), cost=round(c, 6), price_known=known, stop_reason=data.get("stop_reason"),
                 text=_clip(text), tool_uses=tool_uses, cum=cum, tag_cum=tag_cum)
    return {"content": content, "text": text, "stop_reason": data.get("stop_reason"), "in": in_tok, "out": out_tok,
            "sec": sec, "cost": c, "id": cid}


def chat(system: str, messages: list, *, max_tokens: int = 1024, model: str | None = None,
         temperature: float = 0.0, tag: str = "main") -> str:
    """一次调用，返回文本。"""
    return _call(system, messages, max_tokens=max_tokens, model=model, temperature=temperature, tag=tag,
                 tools=None, timeout=120)["text"]


def chat_tools(system: str, messages: list, tools: list, *, max_tokens: int = 1500, model: str | None = None,
               tag: str = "main") -> tuple[list, str]:
    """带工具的一次调用，返回 (content_blocks, stop_reason)。工具循环由调用方自己写——那个循环就是 agent。"""
    r = _call(system, messages, max_tokens=max_tokens, model=model, temperature=0.0, tag=tag, tools=tools, timeout=180)
    return r["content"], r["stop_reason"]


def extract_code(text: str) -> str:
    """取第一个 ```python 代码围栏里的内容（没有围栏就整段）。"""
    if "```" not in text:
        return text.strip()
    start = text.find("```")
    nl = text.find("\n", start)
    end = text.find("```", nl + 1)
    return text[nl + 1:end if end != -1 else None].strip()


if __name__ == "__main__":
    print("BASE", BASE, "MODEL", MODEL, "SMALL", SMALL, "KEY", (KEY[:8] + "…") if KEY else "(none)")
    print(chat("Reply with one word.", [{"role": "user", "content": "ping"}], max_tokens=8))

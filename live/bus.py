"""live.bus —— 演示的「事件总线 + 实时看板」。纯标准库，零安装。

每个演示脚本这样用：

    from live import bus, llm
    ap = bus.argparser("A1 · 验证-重试")          # 统一的命令行开关
    args = ap.parse_args()
    with bus.start("a1_verify_retry", "A1 · 验证-重试", args, budget={"usd": 0.75}) as run:
        run.iter("task", 1, 6, label="slugify")   # 循环走到哪
        text = llm.chat(system, messages)          # 每次调用自动发 llm.call / llm.done（含 token、成本）
        run.verify("slugify", ok, feedback)        # 尺子说了什么
        run.prompt("system", new_prompt)           # prompt 又变了一版（页面会画 diff）
        run.score("public", x=1, y=2)              # 曲线上一个点
        run.stop("goal")                           # 为什么停

它做的事：
  1. 每个事件 → 内存 history → 逐行 flush 进 runs/<name>/<run_id>.jsonl（kill -9 也不丢）
     → 推给每个已连接的浏览器（SSE，每个订阅者一个 queue，演示线程永远不会被慢浏览器卡住）。
  2. 起一个 ThreadingHTTPServer（127.0.0.1:8642，占用就往后找），自动打开浏览器。
     页面 `GET /events?since=0` 先把历史回放一遍再直播，所以晚开的 tab 也能追上。
  3. 结束时把「为什么停」统一成一个 stop 事件（正常 / 超预算 / Ctrl-C / 异常），打印账单，
     然后挂住不退出（页面保持可看），按 Enter 或页面上的「结束」按钮才退出。
  4. 子进程模式：start() 会把 LIVE_BUS_URL 放进环境；子进程里再 bus.start() 得到的是一个
     「转发器」——事件 POST 回父进程，落在同一张页面上（a0 每轮开新进程就靠这个）。

事件封套：{"seq", "t"(距 run.start 秒), "ts", "kind", "pid", ...字段}。kind 是闭集，见 KINDS。
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent
RUNS = DEMOS / "runs"

KINDS = {
    "run.start", "loop.iter", "llm.call", "llm.done", "tool.call", "tool.result", "verify",
    "prompt.version", "artifact", "checkpoint", "graph.def", "graph.node", "graph.edge",
    "score", "stop", "log",
}

_current: "Run | None" = None


def current() -> "Run | None":
    """当前进程里正在跑的 Run（llm.py 靠它记账和发事件）。"""
    return _current


def argparser(description: str) -> argparse.ArgumentParser:
    """所有演示共用的命令行开关。演示自己再 add_argument 加自己的。"""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器，也不在结尾挂住（冒烟测试用）")
    ap.add_argument("--hold", action="store_true", help="即使 --no-browser 也在结尾挂住（截图脚本用）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LIVE_PORT", "8642")))
    ap.add_argument("--fast", action="store_true", help="缩小规模（每个演示自己定义缩什么）")
    ap.add_argument("--model", default=None, help="覆盖 MODEL（默认 .env 里的，课上 claude-sonnet-5）")
    ap.add_argument("--budget-usd", type=float, default=None, help="覆盖这个演示的默认美元预算")
    return ap


class BudgetExceeded(RuntimeError):
    """预算在花钱之前查；查到就抛这个，演示体外层统一停成 stop 事件。"""


# ────────────────────────────────────────────────────────────────────────── Run
class Run:
    def __init__(self, name: str, title: str, args=None, *, budget: dict | None = None,
                 checker_model: str | None = None, resumed: bool = False, extra: dict | None = None):
        self.name, self.title = name, title
        self.args = args
        self.run_id = datetime.now().strftime("%H%M%S") + f"-{os.getpid() % 10000:04d}"
        self.started = time.time()
        self.budget = dict(budget or {})
        if args is not None and getattr(args, "budget_usd", None):
            self.budget["usd"] = args.budget_usd
        self.headless = bool(args and getattr(args, "no_browser", False)) or os.environ.get("LIVE_NO_BROWSER") == "1"
        self.hold = bool(args and getattr(args, "hold", False))
        self.fast = bool(args and getattr(args, "fast", False))
        self.checker_model = checker_model
        self.resumed = resumed
        self.extra = extra or {}
        # 记账（llm.py 每次调用后 account()）
        self._lock = threading.Lock()
        self.seq = 0
        self.history: list[dict] = []
        self.subs: list[queue.Queue] = []
        self.cum = {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "sec": 0.0}
        self.by_tag: dict[str, dict] = {}
        self._versions: dict[str, int] = {}
        self._stopped = False
        self.done_event = threading.Event()
        # 子进程模式：只转发，不开服务
        self.client_url = os.environ.get("LIVE_BUS_URL")
        self.log_path: Path | None = None
        self.port = None
        self.url = self.client_url

    # ── 事件 ────────────────────────────────────────────────────────────
    def emit(self, kind: str, **fields) -> int:
        """发一个事件。绝不抛异常（看板坏了不能把演示带死）。"""
        try:
            if kind not in KINDS:
                kind, fields = "log", {"level": "warn", "text": f"unknown kind {kind}: {fields}"}
            if self.client_url:
                ev = {"kind": kind, "pid": os.getpid(), **fields}
                req = urllib.request.Request(self.client_url + "/api/emit", data=json.dumps(ev, ensure_ascii=False, default=str).encode(),
                                             headers={"content-type": "application/json"})
                try:
                    urllib.request.urlopen(req, timeout=2).read()
                except Exception:
                    pass
                return -1
            return self._ingest({"kind": kind, "pid": os.getpid(), **fields})
        except Exception as e:      # noqa: BLE001
            print(f"[bus] emit failed: {e}", file=sys.stderr)
            return -1

    def _ingest(self, ev: dict) -> int:
        with self._lock:
            self.seq += 1
            ev = {"seq": self.seq, "t": round(time.time() - self.started, 3),
                  "ts": datetime.now().strftime("%H:%M:%S"), **ev}
            self.history.append(ev)
            if self.log_path:
                with self.log_path.open("a") as f:
                    f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass
        return ev["seq"]

    # ── 演示代码用的「像说话一样」的封装 ────────────────────────────────
    def iter(self, loop: str, i: int, n: int | None = None, label: str = "", **kv) -> None:
        self.emit("loop.iter", loop=loop, i=i, n=n, label=label, **kv)

    def verify(self, target: str, ok: bool, feedback: str, *, score=None, max=None, kind: str = "tests",
               private: bool = False, **kv) -> None:
        """kind：tests / judge / validator / human / answer（事件里叫 check，别和事件的 kind 撞名）。"""
        self.emit("verify", target=target, ok=bool(ok), feedback=str(feedback)[:2000], check=kind,
                  score=score, max=max, private=private, **kv)

    def prompt(self, name: str, text: str, *, note: str | None = None, accepted=None, **kv) -> int:
        v = self._next_version("prompt:" + name)
        self.emit("prompt.version", name=name, v=v, text=text, note=note, accepted=accepted, **kv)
        return v

    def artifact(self, name: str, text: str, *, lang: str = "text", note: str | None = None, accepted=None,
                 render: str | None = None, **kv) -> int:
        v = self._next_version("artifact:" + name)
        self.emit("artifact", name=name, v=v, text=text, lang=lang, note=note, accepted=accepted, render=render, **kv)
        return v

    def score(self, series: str, x, y, *, group: str = "score", label: str | None = None) -> None:
        self.emit("score", series=series, x=x, y=y, group=group, label=label)

    def checkpoint(self, path, **kv) -> None:
        p = Path(path)
        self.emit("checkpoint", path=str(p), bytes=p.stat().st_size if p.exists() else 0, **kv)

    def graph_def(self, spec: dict) -> None:
        self.emit("graph.def", **spec)

    def log(self, text: str, level: str = "info") -> None:
        self.emit("log", level=level, text=str(text))

    def say(self, *parts) -> None:
        """终端打印 + 页面时间线一行（讲师旁白就用它）。"""
        text = " ".join(str(p) for p in parts)
        print(text, flush=True)
        self.emit("log", level="say", text=text)

    def status(self, text: str) -> None:
        self.emit("log", level="status", text=text)

    def _next_version(self, key: str) -> int:
        with self._lock:
            self._versions[key] = self._versions.get(key, 0) + 1
            return self._versions[key]

    # ── 记账与预算 ─────────────────────────────────────────────────────
    def account(self, tag: str, in_tok: int, out_tok: int, cost: float, sec: float) -> tuple[dict, dict]:
        with self._lock:
            for d in (self.cum, self.by_tag.setdefault(tag, {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "sec": 0.0})):
                d["calls"] += 1; d["in"] += in_tok; d["out"] += out_tok; d["cost"] += cost; d["sec"] += sec
            return dict(self.cum), dict(self.by_tag[tag])

    def totals(self, tag: str | None = None) -> dict:
        with self._lock:
            return dict(self.by_tag.get(tag, {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "sec": 0.0}) if tag else self.cum)

    def check_budget(self) -> None:
        """在花钱之前查。事后查恒超一步，而最后一步最贵。"""
        b = self.budget
        if b.get("usd") is not None and self.cum["cost"] >= b["usd"]:
            raise BudgetExceeded("usd_budget")
        if b.get("tokens") is not None and self.cum["in"] + self.cum["out"] >= b["tokens"]:
            raise BudgetExceeded("token_budget")
        if b.get("seconds") is not None and time.time() - self.started >= b["seconds"]:
            raise BudgetExceeded("wall_clock")

    # ── 收尾 ───────────────────────────────────────────────────────────
    def stop(self, reason: str, ok: bool | None = None, **summary) -> None:
        if self._stopped:
            return
        self._stopped = True
        bill = {**self.totals(), "sec_total": round(time.time() - self.started, 1)}
        self.emit("stop", reason=reason, ok=ok, summary=summary, bill=bill, by_tag=dict(self.by_tag))
        if os.environ.get("TB") == "1":
            print("\n%%tb:ledger " + json.dumps({"calls": bill["calls"], "input_tokens": bill["in"], "output_tokens": bill["out"],
                                                  "cost_usd": round(bill["cost"], 4), "stop": reason}, ensure_ascii=False), flush=True)
        c = self.cum
        print(f"\n⏹ 停机：{reason}" + (f"  {summary}" if summary else ""))
        print(f"账单：{c['calls']} 次调用 · 输入 {c['in']:,} · 输出 {c['out']:,} tokens · ≈ ${c['cost']:.3f} · 用时 {bill['sec_total']}s", flush=True)

    def wait(self) -> None:
        """挂住让页面继续可看。按 Enter / 页面「结束」/ 再按 Ctrl-C 退出。"""
        if self.client_url or (self.headless and not self.hold):
            return
        print(f"看板仍在 {self.url} —— 按 Enter 结束（或点页面右上「结束本次运行」）", flush=True)
        signal.signal(signal.SIGINT, lambda *_: os._exit(130))
        t = threading.Thread(target=lambda: (self.done_event.wait(), sys.stdout.flush(), os._exit(0)), daemon=True)
        t.start()
        try:
            input()
        except EOFError:
            self.done_event.wait()

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, et, ev, tb) -> bool:
        if et is None:
            self.stop("done", ok=True)
        elif issubclass(et, BudgetExceeded):
            self.stop(str(ev), ok=False)
        elif issubclass(et, KeyboardInterrupt):
            self.stop("ctrl-c", ok=False)
        else:
            traceback.print_exception(et, ev, tb)
            self.stop(f"error: {et.__name__}: {str(ev)[:200]}", ok=False)
        self.wait()
        return True        # 异常已变成 stop 事件；进程按正常路径退出


# ────────────────────────────────────────────────────────────── HTTP / SSE
def _make_handler(run: Run):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # 别把每个请求刷进演示终端
            pass

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                body = (HERE / "dashboard.html").read_bytes()      # 每次读盘：改页面刷新即见
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("cache-control", "no-store")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/events":
                since = int(parse_qs(u.query).get("since", ["0"])[0] or 0)
                last = self.headers.get("Last-Event-ID")
                if last and last.isdigit():
                    since = int(last)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream; charset=utf-8")
                self.send_header("cache-control", "no-cache")
                self.send_header("x-accel-buffering", "no")
                self.end_headers()
                q: queue.Queue = queue.Queue(maxsize=10000)
                with run._lock:                       # 快照 + 订阅必须原子，否则中间的事件会漏
                    backlog = run.history[since:]
                    run.subs.append(q)
                try:
                    self.wfile.write(b"retry: 1000\n\n")
                    for ev in backlog:
                        self._frame(ev)
                    while True:
                        try:
                            ev = q.get(timeout=15)
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n"); self.wfile.flush()
                            continue
                        self._frame(ev)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with run._lock:
                        if q in run.subs:
                            run.subs.remove(q)
            elif u.path == "/api/history":
                with run._lock:
                    hist = list(run.history)
                self._json(200, hist)
            elif u.path == "/api/ping":
                self._json(200, {"run_id": run.run_id, "name": run.name, "seq": run.seq})
            elif u.path == "/favicon.ico":
                self.send_response(204); self.end_headers()
            else:
                self._json(404, {"error": "not found"})

        def _frame(self, ev: dict) -> None:
            self.wfile.write(f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n".encode())
            self.wfile.flush()

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b""
            if u.path == "/api/emit":                 # 子进程转发过来的事件
                try:
                    ev = json.loads(raw or b"{}")
                    kind = ev.pop("kind", "log")
                    if kind not in KINDS:
                        kind, ev = "log", {"level": "warn", "text": f"child sent unknown kind {kind}"}
                    if kind == "llm.done":                 # 子进程的账记到父进程总账上（页面/预算看的是父账）
                        cum, tag_cum = run.account(ev.get("tag", "child"), int(ev.get("in", 0)), int(ev.get("out", 0)),
                                                   float(ev.get("cost", 0.0)), float(ev.get("sec", 0.0)))
                        ev["cum"], ev["tag_cum"] = cum, tag_cum
                    seq = run._ingest({"kind": kind, **ev})
                    self._json(200, {"ok": True, "seq": seq})
                except Exception as e:                # noqa: BLE001
                    self._json(400, {"ok": False, "error": str(e)})
            elif u.path == "/api/done":
                run.done_event.set()
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
    return H


def _serve(run: Run, port: int) -> ThreadingHTTPServer:
    last_err = None
    for p in range(port, port + 21):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), _make_handler(run))
            srv.daemon_threads = True
            run.port = p
            run.url = f"http://127.0.0.1:{p}"
            threading.Thread(target=srv.serve_forever, daemon=True, name="live-http").start()
            return srv
        except OSError as e:
            last_err = e
    raise RuntimeError(f"端口 {port}..{port + 20} 全被占：{last_err}")


# ────────────────────────────────────────────────────────────────── start
def start(name: str, title: str, args=None, *, budget: dict | None = None, checker_model: str | None = None,
          resumed: bool = False, extra: dict | None = None) -> Run:
    """建一个 Run：开服务、开日志、开浏览器、发 run.start。子进程模式下只建转发器。"""
    global _current
    run = Run(name, title, args, budget=budget, checker_model=checker_model, resumed=resumed, extra=extra)
    _current = run
    if run.client_url:
        run.log(f"子进程 {os.getpid()} 起来了（{title}）", level="status")
        return run
    RUNS.joinpath(name).mkdir(parents=True, exist_ok=True)
    run.log_path = RUNS / name / f"{run.run_id}.jsonl"
    _serve(run, int(getattr(args, "port", None) or os.environ.get("LIVE_PORT", "8642")))
    os.environ["LIVE_BUS_URL"] = run.url
    from . import llm as _llm                            # 模型名此刻才定（--model 可能覆盖）
    if args is not None and getattr(args, "model", None):
        _llm.MODEL = args.model
    run.emit("run.start", name=name, title=title, run_id=run.run_id, model=_llm.MODEL,
             checker_model=checker_model, argv=sys.argv[1:], budget=run.budget, resumed=resumed,
             fast=run.fast, extra=run.extra, prices=_llm.PRICES)
    print(f"▶ {title}  ·  模型 {_llm.MODEL}  ·  看板 {run.url}  ·  日志 {run.log_path.relative_to(DEMOS)}", flush=True)
    if not run.headless:
        try:
            webbrowser.open(run.url)
        except Exception:
            pass
    return run


# ───────────────────────────────────────── 开发工具：回放一份日志来调页面
def _replay_main() -> None:
    ap = argparse.ArgumentParser(description="回放 runs/*.jsonl 到看板（零成本，只用于改页面）")
    ap.add_argument("--serve", required=True)
    ap.add_argument("--port", type=int, default=int(os.environ.get("LIVE_PORT", "8642")))
    ap.add_argument("--speed", type=float, default=20.0, help="时间压缩倍数")
    ap.add_argument("--hold", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    events = [json.loads(l) for l in Path(a.serve).read_text().splitlines() if l.strip()]
    run = Run("replay", "回放 " + Path(a.serve).name, a)
    _serve(run, a.port)
    print(f"▶ 回放 {len(events)} 个事件  ·  {run.url}", flush=True)
    if not a.no_browser:
        webbrowser.open(run.url)
    last_t = 0.0
    for ev in events:
        time.sleep(max(0.0, (ev.get("t", 0) - last_t) / a.speed))
        last_t = ev.get("t", 0)
        ev = {k: v for k, v in ev.items() if k not in ("seq", "t", "ts")}
        run._ingest(ev)
    run.hold = True
    run.wait()


if __name__ == "__main__":
    _replay_main()

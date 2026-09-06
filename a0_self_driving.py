#!/usr/bin/env python3
"""A0 · 自驱环 —— 没有人写 prompt：agent 自己从仓库里把上下文组装出来，自己跑。

这个文件证明什么
  以前 prompt 是人喂的（A1–A3 都是）。这里人只写五行「驱动词」DRIVER，写一次不再改。
  每一轮都是一个**全新的操作系统进程**，上下文从空开始；agent 用五个工具（list / read / write /
  run_tests / git_log）自己去读 SPEC.md、PLAN.md、PROGRESS.md、git log、代码、测试 ——
  它读到的东西，就是它这一轮的 prompt。然后挑一项未完成任务、写代码、跑测试、记进度、退出。
  harness 负责 commit；**另一个模型**（checker）判断整体做完没有（做题的不给自己打分）。
  这就是 Ralph（Huntley 2025）/ Codex `/goal` 的骨架：驱动词固定，状态在磁盘，判题分离。

看板上看哪几栏
  · LLM 调用：点开任意一轮的最后一次调用 —— 五行驱动词变成了几千 token 的上下文，全是它自己读进来的
  · 「每次调用发出去的上下文」曲线：一轮之内逐次长大（每读一个文件长一截），跨轮从空重来
  · 循环时间线：outer 一轮一组，里面是 🔧 工具调用（读了什么、写了什么）、✓/✗ 测试、checker 判决
  · Prompt/产物：driver 只有 v1（人从没改过）；PLAN.md / PROGRESS.md 每轮一版，diff 就是它勾掉的那一项
  · 分数曲线：tests_passed（尺子）、context_tokens（每轮组装出的上下文大小）

课上怎么跑
  python3 a0_self_driving.py --reset            # 播种仓库（六个空函数 + 29 条测试）然后跑到 goal / 预算 / gutter
  python3 a0_self_driving.py --reset --max-iters 3   # 只看三轮（课上够了，每轮约 40–90 秒）
  python3 a0_self_driving.py                    # 从上次停的地方接着跑（状态在 checkpoints/a0_state.json）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402
from live.tasks import TASKS, public_examples  # noqa: E402

WORK = Path(__file__).resolve().parent / "checkpoints" / "a0_work"
STATE = Path(__file__).resolve().parent / "checkpoints" / "a0_state.json"
CHECKER = os.environ.get("EVAL_MODEL") or llm.SMALL      # 判题的模型，故意和做题的不是同一个

# ── 人类的全部贡献：五行。写一次，永远不改。 ──────────────────────────────────
DRIVER = """Read SPEC.md, PLAN.md, PROGRESS.md and `git log`. Study the code and the tests before assuming anything is unimplemented.
Pick the ONE most important unchecked item in PLAN.md and implement it in textkit.py.
Run the tests. If your item passes, tick its checkbox in PLAN.md.
Append one line to PROGRESS.md: what you did, what you learned, what is still failing.
Then stop with ONE line: "DONE: <what you did>". Do not start a second item.
"""

# ── agent 能用的五个工具（它就靠这五个把上下文读进来、把结果写出去） ───────────
TOOLS = [
    {"name": "list_files", "description": "List files in the repo.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Read a file in the repo.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Overwrite a file in the repo with new content.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "run_tests", "description": "Run the test suite; returns pass/fail per function and the first failure detail.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "git_log", "description": "Show the last commits.", "input_schema": {"type": "object", "properties": {}}},
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=WORK, capture_output=True, text=True).stdout


def seed() -> None:
    """播种一个小仓库：SPEC（六个函数的说明）、PLAN（六个空勾）、PROGRESS、六个空桩、29 条测试。"""
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    spec, plan, stubs, tests = ["# textkit — six small text utilities\n"], ["# PLAN\n"], ['"""textkit: implemented one item at a time by the loop."""\n'], ["import textkit\n\nCASES = [\n"]
    for t in TASKS:
        spec.append(f"## {t['name']}\n`{t['signature']}`\n\n{t['spec']}\n\nExamples:\n{public_examples(t)}\n")
        plan.append(f"- [ ] {t['name']}\n")
        stubs.append(f"\n{t['signature']}:\n    raise NotImplementedError\n")
        for args, expected in t["public"] + t["hidden"]:
            tests.append(f"    ({t['name']!r}, {args!r}, {expected!r}),\n")
    tests.append("]\n")
    (WORK / "SPEC.md").write_text("\n".join(spec)); (WORK / "PLAN.md").write_text("".join(plan))
    (WORK / "PROGRESS.md").write_text("# PROGRESS\n(one line per iteration)\n")
    (WORK / "textkit.py").write_text("".join(stubs)); (WORK / "test_textkit.py").write_text("".join(tests))
    (WORK / "DRIVER.md").write_text(DRIVER)
    git("init", "-q"); git("add", "-A"); git("-c", "user.name=seed", "-c", "user.email=seed@x", "commit", "-q", "-m", "seed: spec, plan, stubs, tests")
    if STATE.exists():
        STATE.unlink()


# ── 尺子（确定性）：跑全部 29 条测试 ─────────────────────────────────────────
RUNNER = r'''
import json, sys, importlib
sys.path.insert(0, ".")
import test_textkit as T
import textkit
res = []
for name, args, expected in T.CASES:
    try:
        got = eval(f"textkit.{name}({args})")
        same = (abs(got - expected) < 1e-9) if isinstance(expected, float) and isinstance(got, (int, float)) else got == expected
        res.append({"name": name, "args": args, "ok": bool(same), "detail": None if same else f"returned {got!r}; expected {expected!r}"})
    except Exception as e:
        res.append({"name": name, "args": args, "ok": False, "detail": f"raised {type(e).__name__}: {e}"})
print(json.dumps(res))
'''


def run_tests() -> dict:
    p = subprocess.run([sys.executable, "-I", "-c", RUNNER], cwd=WORK, capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        return {"passed": 0, "total": 0, "error": (p.stderr.strip().splitlines() or ["no output"])[-1]}
    res = json.loads(p.stdout.strip().splitlines()[-1])
    by: dict[str, list] = {}
    for r in res:
        by.setdefault(r["name"], []).append(r)
    first = next((r for r in res if not r["ok"]), None)
    return {"passed": sum(r["ok"] for r in res), "total": len(res),
            "per_function": {n: f"{sum(x['ok'] for x in rs)}/{len(rs)}" for n, rs in by.items()},
            "first_failure": (f"{first['name']}({first['args']}) {first['detail']}" if first else None)}


def call_tool(name: str, inp: dict) -> str:
    if name == "list_files":
        return "\n".join(sorted(p.name for p in WORK.iterdir() if p.name not in (".git", "__pycache__")))
    if name in ("read_file", "write_file"):
        path = (WORK / inp["path"]).resolve()
        if WORK not in path.parents and path != WORK:
            return "error: path outside repo"
        if name == "read_file":
            return path.read_text() if path.exists() else "error: no such file"
        path.write_text(inp["content"])
        return f"wrote {len(inp['content'])} chars to {inp['path']}"
    if name == "run_tests":
        return json.dumps(run_tests())
    if name == "git_log":
        return git("log", "--format=%s", "-n", "8") or "(no commits)"
    return f"error: unknown tool {name}"


# ── 一轮 = 一个全新进程 ──────────────────────────────────────────────────────
def iteration(n: int) -> dict:
    """子进程里跑：上下文从空开始，只有五行驱动词；工具循环就是 agent 本体。"""
    run = bus.start("a0_iter", f"第 {n} 轮")                 # 子进程模式：事件转发回父页面
    messages: list = [{"role": "user", "content": DRIVER}]
    calls, final, max_in = [], "", 0
    for turn in range(16):
        content, stop = llm.chat_tools("You are a careful engineer working inside a small repo. Use the tools.",
                                       messages, TOOLS, max_tokens=2500, tag="maker")
        messages.append({"role": "assistant", "content": content})
        uses = [b for b in content if b.get("type") == "tool_use"]
        final = "".join(b.get("text", "") for b in content if b.get("type") == "text") or final
        if not uses or stop == "end_turn":
            break
        results = []
        for u in uses:
            run.emit("tool.call", id=u["id"], name=u["name"], input=u.get("input", {}), iter=n)
            out = call_tool(u["name"], u.get("input", {}))
            run.emit("tool.result", id=u["id"], name=u["name"], ok=not out.startswith("error"), output=out[:2000])
            calls.append(u["name"] + (f"({u['input'].get('path', '')})" if "path" in u.get("input", {}) else "()"))
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": out[:6000]})
        messages.append({"role": "user", "content": results})
    t = run.totals("maker")
    summary = final.strip().splitlines()[-1] if final.strip() else "no message"
    git("add", "-A"); git("-c", "user.name=loop", "-c", "user.email=loop@x", "commit", "-q", "-m", f"iter {n}: {summary[:72]}")
    return {"calls": calls, "final": summary, "in": t["in"], "out": t["out"], "n_calls": t["calls"]}


def evaluate(tests: dict) -> dict:
    """checker：另一个模型读 PLAN 和测试结果，判「整体做完了没」。做题的不给自己打分。"""
    plan = (WORK / "PLAN.md").read_text()
    sysmsg = ('You are the evaluator, not the maker. Decide if the GOAL is met: every PLAN item ticked AND all tests pass. '
              'Reply with JSON only: {"done": bool, "reason": str}')
    raw = llm.chat(sysmsg, [{"role": "user", "content": f"PLAN.md:\n{plan}\n\nTest run: {json.dumps(tests)}"}],
                   max_tokens=400, model=CHECKER, tag="checker")   # 别调小：理由写长一点就会被截断，
                                                                   # 截断的 ```json 没有闭合围栏 → 解析失败 → 判成「没做完」
    try:
        r = json.loads(llm.extract_code(raw))
        return {"done": bool(r["done"]), "reason": str(r.get("reason", ""))[:160]}
    except Exception:
        return {"done": False, "reason": f"checker replied non-JSON: {raw[:80]}"}


def plan_ticks() -> tuple[int, int]:
    p = (WORK / "PLAN.md").read_text()
    return p.count("- [x]"), p.count("- [")


def main() -> None:
    ap = bus.argparser("A0 · 自驱环：没有人写 prompt")
    ap.add_argument("--reset", action="store_true", help="重新播种仓库")
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=400_000)   # 六道题跑到全绿约 160k–260k，留出余量；
                                                                 # 真想演「预算停机」把它调小，那是 A2 的戏
    ap.add_argument("--gutter", type=int, default=3, help="连续 N 轮测试没进展就停（Ralph 说的 gutter）")
    args = ap.parse_args()
    if args.fast:
        args.max_iters = min(args.max_iters, 2)
    if args.reset or not WORK.exists():
        seed()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"iter": 0, "history": []}

    with bus.start("a0_self_driving", "A0 · 自驱环：没有人写 prompt", args,
                   budget={"usd": 2.5, "tokens": args.max_tokens}, checker_model=CHECKER,
                   resumed=state["iter"] > 0) as run:
        run.prompt("driver", DRIVER, note="人类的全部贡献：五行，写一次不再改")
        run.artifact("PLAN.md", (WORK / "PLAN.md").read_text(), lang="md", note="播种时")
        before = run_tests()
        run.say(f"仓库 {WORK.name} · 测试 {before['passed']}/{before['total']} · 计划 {plan_ticks()[0]}/{plan_ticks()[1]} 勾 · maker={llm.MODEL} checker={CHECKER}")
        stale, stop = 0, None
        while True:
            if state["iter"] >= args.max_iters:
                stop = "max_iters"; break
            try:
                run.check_budget()
            except bus.BudgetExceeded as e:
                stop = str(e); break
            n = state["iter"] + 1
            run.iter("outer", n, args.max_iters, label="新进程 · 空上下文")
            # 🔴 新进程：上一轮什么都不记得；能带过去的只有磁盘上的仓库
            p = subprocess.run([sys.executable, __file__, "--_iter", str(n)], capture_output=True, text=True, env={**os.environ})
            if p.returncode != 0:
                run.log(p.stderr[-800:], level="error"); stop = "iteration_crashed"; break
            rec = json.loads(p.stdout.strip().splitlines()[-1])
            after = run_tests()
            ticks, total = plan_ticks()
            run.artifact("PLAN.md", (WORK / "PLAN.md").read_text(), lang="md", note=f"第 {n} 轮后")
            run.artifact("PROGRESS.md", (WORK / "PROGRESS.md").read_text(), lang="md", note=f"第 {n} 轮后")
            run.verify(f"iter {n}", after["passed"] == after["total"], f"tests {before['passed']}→{after['passed']}/{after['total']}" + (f" · {after['first_failure']}" if after.get("first_failure") else ""),
                       kind="tests", score=after["passed"], max=after["total"])
            run.score("tests_passed", n, after["passed"])
            run.score("context_tokens", n, rec["in"] // max(rec["n_calls"], 1), group="context")
            run.say(f"  第 {n} 轮：{' → '.join(rec['calls'])}")
            run.say(f"  驱动词 ≈ {llm.est_tokens(DRIVER)} tok → 这轮平均每次调用发出 {rec['in'] // max(rec['n_calls'], 1):,} tok · 测试 {before['passed']}→{after['passed']}/{after['total']} · 计划 {ticks}/{total} 勾 · commit: {git('log', '--format=%s', '-n', '1').strip()}")
            verdict = evaluate(after)
            run.verify(f"iter {n}", verdict["done"], verdict["reason"], kind="judge", judge=CHECKER)
            run.say(f"  checker（{CHECKER}）：done={verdict['done']} — {verdict['reason']}")
            stale = stale + 1 if after["passed"] <= before["passed"] else 0
            state["iter"] = n
            state["history"].append({"iter": n, "passed": after["passed"], "calls": rec["n_calls"], "in": rec["in"], "out": rec["out"]})
            STATE.write_text(json.dumps(state, indent=1)); run.checkpoint(STATE, status="running", iter=n)
            before = after
            if verdict["done"] and after["passed"] == after["total"]:
                stop = "goal"; break
            if stale >= args.gutter:
                stop = f"gutter（{args.gutter} 轮无进展）"; break
        run.say("没有人给这个任务写过 prompt。五行驱动词从没变过；变的是上下文——每一轮它都重新读自己刚改过的仓库。")
        run.stop(stop or "done", ok=(stop == "goal"), iterations=state["iter"], tests=f"{before['passed']}/{before['total']}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--_iter":
        print(json.dumps(iteration(int(sys.argv[2]))))
    else:
        main()

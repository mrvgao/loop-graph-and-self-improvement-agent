#!/usr/bin/env python3
"""lab-4-a 的结构级自检 —— python3 tools/lab_check.py t3

每个 task 一条：只查机械可查的东西（文件在不在、日志里有没有那个事件、停机原因对不对）。
「你是不是真懂了」那半在对话里做，这里不管。查不过会告诉你差什么，不会只说「失败」。
读的是 runs/<demo>/*.jsonl —— 也就是你真跑过留下的日志，所以这些检查骗不过去。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def load(demo: str, *, newest: int = 1) -> list[list[dict]]:
    """最近 newest 份该演示的日志，每份是事件列表。"""
    d = RUNS / demo
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:newest]:
        evs = []
        for line in f.read_text().splitlines():
            try:
                evs.append(json.loads(line))
            except Exception:                                    # noqa: BLE001, S112
                pass
        if evs:
            out.append(evs)
    return out


def stop_of(evs: list[dict]) -> dict:
    return next((e for e in reversed(evs) if e.get("kind") == "stop"), {})


def kinds(evs: list[dict]) -> set[str]:
    return {e.get("kind") for e in evs}


def calls(evs: list[dict]) -> int:
    return sum(1 for e in evs if e.get("kind") == "llm.done")


def need(cond: bool, ok_msg: str, bad_msg: str, errs: list[str]) -> bool:
    if cond:
        print(f"  ✅ {ok_msg}")
    else:
        errs.append(bad_msg)
        print(f"  ❌ {bad_msg}")
    return cond


# ── 每个 task 一个函数，返回错误列表 ──────────────────────────────────────────
def t1(errs: list[str]) -> None:
    """跑通 a1，并且日志是合法的。"""
    logs = load("a1_verify_retry")
    if not need(bool(logs), "跑过 a1_verify_retry", "还没跑过 a1_verify_retry.py（runs/a1_verify_retry/ 是空的）", errs):
        return
    evs = logs[0]
    need(stop_of(evs).get("reason") == "done", "a1 跑完了（stop=done）",
         f"a1 没跑到底，停机原因是 {stop_of(evs).get('reason')!r}", errs)
    arms = {e.get("arm") for e in evs if e.get("kind") == "verify"}
    need({"arm_A", "arm_B", "arm_C"} <= arms, "三条臂都跑了（A/B/C）",
         f"只跑了 {sorted(a for a in arms if a)} —— 默认就是三条臂，别加 --arms", errs)
    need({"llm.call", "llm.done", "verify", "score", "stop"} <= kinds(evs),
         "看板该有的事件都发了（调用 / 验证 / 分数 / 停机）", "日志里缺事件，这份 run 可能没跑完", errs)


def t2(errs: list[str]) -> None:
    """自己的题库，而且 a1 在它上面真跑过。"""
    mine = ROOT / "mine" / "tasks.py"
    if not need(mine.exists(), "mine/tasks.py 存在", "还没写 mine/tasks.py", errs):
        return
    sys.path.insert(0, str(ROOT))
    try:
        import importlib
        m = importlib.import_module("mine.tasks")
        importlib.reload(m)
        tasks = getattr(m, "TASKS", [])
    except Exception as e:                                       # noqa: BLE001
        errs.append(f"mine/tasks.py 导不进来：{type(e).__name__}: {e}")
        print(f"  ❌ {errs[-1]}")
        return
    need(len(tasks) >= 6, f"题目 {len(tasks)} 道（≥6）",
         f"只有 {len(tasks)} 道题。a3 要把前 3 道当公开集、后面当私有集，少于 6 道就分不开", errs)
    need(all(len(t.get("hidden", [])) >= 2 for t in tasks),
         "每道题至少 2 条隐藏用例", "有题的 hidden 少于 2 条 —— 隐藏用例就是尺子，太少量不出差别", errs)
    names = [t.get("name") for t in tasks]
    from live import tasks as builtin
    need(not (set(names) & {t["name"] for t in builtin.BUILTIN_TASKS}),
         "题目是你自己的（和内置题库不重名）",
         f"这些题名和内置题库一样：{sorted(set(names) & {t['name'] for t in builtin.TASKS})}", errs)
    logs = [e for e in load("a1_verify_retry", newest=6)]
    hit = [evs for evs in logs if any(e.get("target") in names for e in evs if e.get("kind") == "verify")]
    need(bool(hit), "a1 在你的题上跑过", "还没在你的题上跑过 a1（LIVE_TASKS=mine.tasks python3 a1_verify_retry.py）", errs)
    if hit:
        solved = (stop_of(hit[0]).get("summary") or {}).get("solved", {})
        need(isinstance(solved, dict) and len(set(solved.values())) > 1,
             f"三条臂的结果不一样：{solved}",
             f"三条臂结果一样（{solved}）—— 题要么太简单（都过）要么太难（都不过），量不出循环的价值，调一下难度", errs)


def t3(errs: list[str]) -> None:
    """自己的批处理：真崩溃、真续跑、真被预算停住。"""
    f = ROOT / "mine" / "batch.py"
    if not need(f.exists(), "mine/batch.py 存在", "还没写 mine/batch.py", errs):
        return
    logs = load("mine_batch", newest=8)
    if not need(bool(logs), "mine/batch.py 跑过（日志在 runs/mine_batch/）",
                "runs/mine_batch/ 里没日志 —— bus.start() 的第一个参数要叫 mine_batch", errs):
        return
    need(any("checkpoint" in kinds(evs) for evs in logs), "写过检查点", "没有 checkpoint 事件 —— 七件套缺了检查点", errs)
    need(any(e.get("resumed") for evs in logs for e in evs if e.get("kind") == "run.start"),
         "有过一次续跑（resumed）", "没有一次 run.start 带 resumed —— 崩了之后要能接着跑", errs)
    reasons = {stop_of(evs).get("reason") for evs in logs}
    budgety = {r for r in reasons if r and ("budget" in str(r) or "max_" in str(r) or "wall" in str(r))}
    need(bool(budgety), f"被预算/上限停住过：{sorted(budgety)}",
         f"从没被预算或上限停住过（见过的停机原因：{sorted(r for r in reasons if r)}）—— 把 --max-tokens 调小演一次", errs)


def t4(errs: list[str]) -> None:
    """自己的图：带上限的环 + 人类闸 + 零调用续跑。"""
    f = ROOT / "mine" / "graph.py"
    if not need(f.exists(), "mine/graph.py 存在", "还没写 mine/graph.py", errs):
        return
    logs = load("mine_graph", newest=8)
    if not need(bool(logs), "mine/graph.py 跑过（日志在 runs/mine_graph/）",
                "runs/mine_graph/ 里没日志 —— bus.start() 的第一个参数要叫 mine_graph", errs):
        return
    defs = [e for evs in logs for e in evs if e.get("kind") == "graph.def"]
    need(bool(defs), "画出了图（有 graph.def 事件）", "没有 graph.def —— 图要用 live/tinygraph.py 跑，它才会发事件", errs)
    need(any(e.get("interrupt_before") for e in defs), "图里声明了中断点（人类闸）",
         "graph.def 里 interrupt_before 是空的 —— compile(interrupt_before=[...]) 那一项没设", errs)
    need(any(e.get("phase") == "interrupt" for evs in logs for e in evs if e.get("kind") == "graph.node"),
         "真的在闸前停过", "没停在闸前过 —— 跑到 human_gate 之前它应该存盘并退出进程", errs)
    resumed_zero = [evs for evs in logs
                    if any(e.get("resumed") for e in evs if e.get("kind") == "run.start") and calls(evs) == 0]
    need(bool(resumed_zero), "续跑那个进程 0 次模型调用",
         "没有一次「续跑且零调用」的运行 —— 闸后面续跑不该重付闸前面的账", errs)
    # 「带上限的环」查声明的图有没有真的环，不查这一次跑没跑到回边
    # （评委心情好一次就给过，环不转是合法出口 —— 但图里必须有那条回边）
    adj: dict[str, set[str]] = {}
    conds = 0
    for e in defs:
        for n in e.get("nodes") or []:
            adj.setdefault(n.get("id"), set())
        for ed in e.get("edges") or []:
            adj.setdefault(ed.get("from"), set()).add(ed.get("to"))
            conds += ed.get("kind") == "cond"

    def has_cycle() -> bool:
        seen, stack = set(), set()

        def walk(u: str) -> bool:
            seen.add(u); stack.add(u)
            for v in adj.get(u, ()):
                if v in stack or (v not in seen and walk(v)):
                    return True
            stack.discard(u)
            return False
        return any(n not in seen and walk(n) for n in list(adj))

    need(has_cycle(), "图里有环（有一条回边）",
         "图里没有环 —— 这个 task 要的是「带上限的环」：至少一条边指回前面的节点", errs)
    need(conds >= 2, f"router 有 {conds} 个分支（条件边）",
         "没有条件边 —— 环的上限要靠 add_conditional_edges 的 router 来兜（否则它凭什么停）", errs)


def _my_task_names() -> list[str]:
    """mine/tasks.py 里的函数名；没有就返回空（t5 那条检查跳过）。"""
    f = ROOT / "mine" / "tasks.py"
    if not f.exists():
        return []
    sys.path.insert(0, str(ROOT))
    try:
        import importlib
        m = importlib.import_module("mine.tasks")
        importlib.reload(m)
        return [t["name"] for t in getattr(m, "TASKS", []) if t.get("name")]
    except Exception:                                            # noqa: BLE001
        return []


def t5(errs: list[str]) -> None:
    """A0 在你的题上跑到 goal。"""
    logs = load("a0_self_driving", newest=6)
    if not need(bool(logs), "跑过 a0_self_driving", "还没跑过 a0（LIVE_TASKS=mine.tasks python3 a0_self_driving.py --reset）", errs):
        return
    names = _my_task_names()
    mine_runs = [evs for evs in logs
                 if any(n in (e.get("text") or "") for e in evs if e.get("kind") == "artifact" for n in names)]
    if names:
        need(bool(mine_runs), "a0 跑的是你自己的题（PLAN.md 里是你的函数名）",
             "a0 跑的还是内置题库 —— 前面加 LIVE_TASKS=mine.tasks 再 --reset 一次", errs)
    logs = mine_runs or logs
    goal = [evs for evs in logs if stop_of(evs).get("reason") == "goal"]
    need(bool(goal), "a0 跑到了 goal（不是 max_iters / 预算 / gutter）",
         f"a0 还没跑到 goal（最近几次的停机原因：{[stop_of(e).get('reason') for e in logs]}）", errs)
    evs = (goal or logs)[0]
    tools = {e.get("name") for e in evs if e.get("kind") == "tool.call"}
    need(len(tools) >= 3, f"agent 自己用了 {len(tools)} 种工具组装上下文：{sorted(t for t in tools if t)}",
         "工具调用太少 —— 子进程的事件应该转发回同一份日志，检查它是不是没连上父进程", errs)
    ctx = [e.get("y") for e in evs if e.get("kind") == "score" and e.get("series") == "context_tokens"]
    need(len(ctx) >= 2 and ctx[-1] > ctx[0],
         f"每轮组装出的上下文在长大：{ctx[0]} → {ctx[-1]} tok",
         f"上下文没有随轮次长大（{ctx}）—— 至少跑两轮才看得到这条曲线", errs)


def t6(errs: list[str]) -> None:
    """两把尺子：a3 在你的题上跑过，而且你写下了结论。"""
    logs = load("a3_prompt_evolution", newest=6)
    if not need(bool(logs), "跑过 a3_prompt_evolution",
                "还没跑过 a3（LIVE_TASKS=mine.tasks python3 a3_prompt_evolution.py）", errs):
        return
    evs = logs[0]
    series = {e.get("series") for e in evs if e.get("kind") == "score"}
    need({"public", "private"} <= series, "public 和 private 两条曲线都有",
         f"只看到 {sorted(s for s in series if s)} —— 两条一起读才是这个演示的全部内容", errs)
    vers = [e for e in evs if e.get("kind") == "prompt.version"]
    need(len(vers) >= 2, f"prompt 改写过 {len(vers)} 版", "prompt 只有一版 —— 至少要跑一代", errs)
    md = ROOT / "mine" / "RULER.md"
    if need(md.exists(), "mine/RULER.md 存在", "还没写 mine/RULER.md（写下你的结论：它学到的是习惯还是答案）", errs):
        text = md.read_text()
        need(len(text.strip()) >= 200, f"RULER.md 有内容（{len(text.strip())} 字）",
             "RULER.md 太短 —— 要写清楚：两条曲线各是什么走势、你据此判断它学到的是习惯还是答案、证据是 prompt 里的哪一行", errs)


def t7(errs: list[str]) -> None:
    """交付：一页说明，外加日志确实存在。"""
    md = ROOT / "mine" / "README.md"
    if not need(md.exists(), "mine/README.md 存在", "还没写 mine/README.md", errs):
        return
    text = md.read_text()
    need(len(text.strip()) >= 600, f"内容够一页（{len(text.strip())} 字）", "太短了，交付要能让别人看懂你做了什么", errs)
    for key, why in [("题", "你的题是什么、为什么隐藏用例猜不到"),
                     ("尺子", "尺子长什么样、它决定了什么"),
                     ("停", "你的循环怎么停的、检查点救了什么")]:
        need(key in text, f"提到了「{key}」", f"没写到 {why}", errs)
    demos = [d.name for d in RUNS.iterdir() if d.is_dir() and any(d.glob("*.jsonl"))] if RUNS.exists() else []
    need(len(demos) >= 6, f"跑过 {len(demos)} 个演示的日志还在：{sorted(demos)}",
         f"只找到 {len(demos)} 个演示的日志 —— 这个 lab 要求你至少真跑过 6 个", errs)


CHECKS = {"t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5, "t6": t6, "t7": t7}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in CHECKS:
        print(f"用法：python3 tools/lab_check.py <{'|'.join(CHECKS)}>")
        sys.exit(2)
    print(f"── {which} ──")
    errors: list[str] = []
    CHECKS[which](errors)
    print()
    if errors:
        print(f"❌ {len(errors)} 项没过。")
        sys.exit(1)
    print("✅ 通过。")

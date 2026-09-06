#!/usr/bin/env python3
"""B0 · 没有人画图 —— planner 从「目标 + 节点目录 + contracts」自己吐出图规格，运行时锁死锚点和上限。

这个文件证明什么
  B1/B2 的 build() 是我写的。这里没有：图是**数据**。planner 模型读目标、节点目录（llm 角色 / 两个确定性
  校验器 / router / human_gate）和 contracts（每种节点读写哪些 state 键），输出 JSON 规格；运行时校验、
  编译进 tinygraph、跑。planner 碰不了的两条 = graph engineering 说的「锚点」：
    · 锚点校验器（隐藏测试 / brief 约束）必须在每条到 END 的路上，且**不带参数**
    · 每个环必须经过 router，上限（MAX_REV）是运行时的
  违规的规格被拒绝，拒绝信原句还给 planner —— planner 自己也在验证-重试环里。
  同一运行时，两个目标（写代码 / 写简报）→ 两张不同的图。

看板上看哪几栏
  · Prompt/产物：graph_spec.json 每次尝试一版（被拒的空心 + 拒绝理由）
  · 图：每个目标一张 tab；锚点节点紫色双边；router 条件边 pass/fail/cap
  · 验证：validator 行 = 锚点判决（测试 / 字数 / 标题 / 禁词）

课上怎么跑
  python3 b0_planned_graph.py                 # 两个目标
  python3 b0_planned_graph.py --goal brief
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                          # noqa: E402
from live.tasks import TASKS, public_examples      # noqa: E402
from live.tinygraph import END, Graph              # noqa: E402
from live.verify import verify                     # noqa: E402

MAX_REV = 2
GOALS = {
    "code": {"goal": "Deliver a correct Python implementation of `word_wrap(text, width)` per SPEC. Correct means the held-out tests pass.",
             "spec": f"{TASKS[4]['signature']}\n{TASKS[4]['spec']}\nExamples:\n{public_examples(TASKS[4])}", "anchor": "validate_tests"},
    "brief": {"goal": "Deliver a 120-180 word briefing for engineering managers on when to use a graph engine vs a plain loop for an agent, with sections 'When a loop is enough' and 'When you need a graph', no hype words (revolutionary, game-changing, unlock).",
              "spec": "Audience: engineering managers. Tone: plain. Must contain both section headings verbatim.", "anchor": "validate_brief"},
}
CATALOG = {
    "llm": {"description": "An LLM role. You give it a `role` prompt; it reads state['spec'], state['artifact'] (if any) and state['feedback'] (if any) and writes state['artifact'].",
            "examples": ["writer", "coder", "reviewer", "editor"]},
    "validate_tests": {"description": "ANCHOR (deterministic). Runs the held-out tests on state['artifact'] (Python code). Writes state['ok'] and state['feedback']. Parameters are frozen."},
    "validate_brief": {"description": "ANCHOR (deterministic). Checks word count 120-180, both section headings, no hype words. Writes state['ok'] and state['feedback']. Parameters are frozen."},
    "router": {"description": "Conditional edge. If state['ok'] → `on_pass`; else if revisions < cap → `on_fail`; else → END (gives up). Every cycle must go through one."},
    "human_gate": {"description": "Pauses for a human before continuing (interrupt). Optional."},
}
PLANNER_SYS = """You are a workflow planner. Given a GOAL, a SPEC and a CATALOG of node types, design the smallest graph that reliably reaches the goal.
Rules: (1) the ANCHOR validator named in the goal must be on every path to END; (2) any cycle must pass through a `router` node; (3) 2-5 nodes total; (4) node names are lowercase identifiers.
Reply with JSON only:
{"entry": "<node>", "nodes": [{"name": "...", "kind": "llm|validate_tests|validate_brief|router|human_gate",
  "role": "<prompt, llm nodes only>", "next": "<node or END, all non-router nodes>", "on_pass": "<node or END>", "on_fail": "<node>"}]}
Every non-router node needs `next`; router nodes need `on_pass` and `on_fail` instead."""
HYPE = ("revolutionary", "game-changing", "unlock")


def make_llm(run: bus.Run, role: str, name: str):
    def node(state: dict) -> dict:
        user = f"SPEC:\n{state['spec']}"
        if state.get("artifact"):
            user += f"\n\nCurrent artifact:\n{state['artifact']}"
        if state.get("feedback"):
            user += f"\n\nValidator feedback: {state['feedback']}\nFix it."
        user += "\n\nReply with the artifact only (code in a ```python fence if code)."
        out = llm.chat(role, [{"role": "user", "content": user}], max_tokens=700, tag=name)
        art = llm.extract_code(out) if "```" in out else out.strip()
        run.artifact("artifact", art, lang="python" if "```" in out else "md", note=f"节点 {name}")
        return {"artifact": art, "revisions": state.get("revisions", 0) + (1 if state.get("artifact") else 0)}
    return node


def anchors(run: bus.Run):
    def validate_tests(state: dict) -> dict:                 # 锚点 —— 冻结
        ok, fb = verify(TASKS[4], state["artifact"])
        run.verify("validate_tests", ok, fb, kind="validator", private=True)
        return {"ok": ok, "feedback": None if ok else fb}

    def validate_brief(state: dict) -> dict:                 # 锚点 —— 冻结
        text = state["artifact"]; words = len(re.findall(r"[A-Za-z0-9'-]+", text)); problems = []
        if not 120 <= words <= 180: problems.append(f"word count {words} not in 120-180")
        for h in ("When a loop is enough", "When you need a graph"):
            if h.lower() not in text.lower(): problems.append(f"missing heading '{h}'")
        for w in HYPE:
            if w in text.lower(): problems.append(f"hype word '{w}'")
        run.verify("validate_brief", not problems, "; ".join(problems) or "pass", kind="validator", private=True)
        return {"ok": not problems, "feedback": None if not problems else "; ".join(problems)}
    return {"validate_tests": validate_tests, "validate_brief": validate_brief}


def compile_spec(run: bus.Run, spec: dict, anchor: str, gname: str) -> Graph:
    """把 planner 的 JSON 变成图；两条运行时规则在这里锁死。"""
    nodes = {n["name"]: n for n in spec["nodes"]}; kinds = {k: n["kind"] for k, n in nodes.items()}
    if anchor not in kinds.values(): raise ValueError(f"the ANCHOR {anchor} is missing")
    for k, n in nodes.items():
        if n["kind"] != "router" and n.get("next") is None: raise ValueError(f"node {k} has no `next`")
        if n["kind"] == "router" and (n.get("on_pass") is None or n.get("on_fail") is None): raise ValueError(f"router {k} needs on_pass and on_fail")
        for tgt in (n.get("next"), n.get("on_pass"), n.get("on_fail")):
            if tgt not in (None, "END") and tgt not in nodes: raise ValueError(f"node {k} points at unknown node {tgt}")
    if spec["entry"] not in nodes: raise ValueError("entry is not a node")
    for k, n in nodes.items():                                            # 规则 1：END 只能从锚点或锚点喂的 router 到达
        if n.get("next") == "END" and kinds[k] != anchor: raise ValueError(f"{k} reaches END without passing the anchor")
        if kinds[k] == "router" and n.get("on_pass") == "END":
            feeders = [m for m, o in nodes.items() if o.get("next") == k]
            if not feeders or not all(kinds[f] == anchor for f in feeders): raise ValueError(f"router {k} lets non-anchor nodes reach END: fed by {feeders}")
    for start in nodes:                                                   # 规则 2：只靠 next 成环 = 没上限，拒绝
        cur, path = start, set()
        while cur in nodes and kinds[cur] != "router":
            if cur in path: raise ValueError(f"cycle through {cur} has no router (no cap)")
            path.add(cur); cur = nodes[cur].get("next")
    A = anchors(run)
    g = Graph(gname)
    for k, n in nodes.items():
        kind = n["kind"]
        if kind == "llm": g.add_node(k, make_llm(run, n.get("role") or "You are a careful writer.", k), kind="llm")
        elif kind in A: g.add_node(k, A[kind], kind="anchor")                    # 冻结：planner 传不了参数
        elif kind in ("router", "human_gate"): g.add_node(k, lambda s: {}, kind="router" if kind == "router" else "gate")
        else: raise ValueError(f"unknown kind {kind}")
        if kind == "router":
            on_pass, on_fail = n["on_pass"], n["on_fail"]
            g.add_conditional_edges(k, lambda s: "pass" if s.get("ok") else ("fail" if s.get("revisions", 0) < MAX_REV else "cap"),
                                    {"pass": END if on_pass == "END" else on_pass, "fail": on_fail, "cap": END})
        else:
            g.add_edge(k, END if n["next"] == "END" else n["next"])
    g.set_entry(spec["entry"])
    return g


def plan(goal_key: str, rejection: str | None, previous: dict | None) -> dict:
    gdef = GOALS[goal_key]
    user = f"GOAL: {gdef['goal']}\nANCHOR: {gdef['anchor']}\nSPEC:\n{gdef['spec']}\n\nCATALOG:\n" + json.dumps(CATALOG, indent=1)
    if rejection:
        user += f"\n\nYour previous spec was REJECTED by the runtime: {rejection}\nPrevious spec:\n{json.dumps(previous)}\nFix it."
    return json.loads(llm.extract_code(llm.chat(PLANNER_SYS, [{"role": "user", "content": user}], max_tokens=900, tag="planner")))


def run_goal(run: bus.Run, goal_key: str) -> bool:
    gdef = GOALS[goal_key]
    run.say(f"══ 目标 {goal_key} ══ {gdef['goal']}")
    spec, g, rejection = None, None, None
    for attempt in range(1, 4):                                     # planner 自己也在验证-重试环里
        run.iter("planner", attempt, 3, label=goal_key)
        spec = plan(goal_key, rejection, spec)
        try:
            g = compile_spec(run, spec, gdef["anchor"], f"{goal_key} 的图")
            run.artifact(f"graph_spec.{goal_key}", json.dumps(spec, indent=1), lang="json", accepted=True, note="运行时校验通过")
            run.verify(f"spec@{goal_key}", True, "通过：锚点在路上，环有上限", kind="validator")
            break
        except (ValueError, KeyError) as e:
            rejection = str(e); g = None
            run.artifact(f"graph_spec.{goal_key}", json.dumps(spec, indent=1), lang="json", accepted=False, note=f"拒绝：{rejection}")
            run.verify(f"spec@{goal_key}", False, rejection, kind="validator")
            run.say(f"  ✗ 运行时拒绝：{rejection} → 那句话还给 planner")
    if g is None:
        run.say("planner 三次都没交出合法规格，放弃这个目标"); return False
    run.say(g.mermaid())
    runner = g.compile(checkpoint_dir=Path(__file__).resolve().parent / "checkpoints" / "b0", max_steps=20)
    state = runner.invoke({"spec": gdef["spec"]}, thread_id=goal_key, log=lambda m: run.log(m))
    run.say("路径：" + " → ".join(runner.path()))
    run.say(f"结果：{'✓ 锚点通过' if state.get('ok') else '✗ 撞上限放弃'}，改了 {state.get('revisions', 0)} 次")
    return bool(state.get("ok"))


def main() -> None:
    ap = bus.argparser("B0 · 没有人画图")
    ap.add_argument("--goal", choices=list(GOALS), default=None)
    args = ap.parse_args()
    goals = [args.goal] if args.goal else (["code"] if args.fast else list(GOALS))
    with bus.start("b0_planned_graph", "B0 · 没有人画图", args, budget={"usd": 0.75}) as run:
        results = {k: run_goal(run, k) for k in goals}
        run.say("同一运行时，同一目录，两个目标，两张图 —— planner 碰不了的两样（锚点、上限）正是让结果可信的两样。")
        run.stop("done", ok=all(results.values()), results=results)


if __name__ == "__main__":
    main()

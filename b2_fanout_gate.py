#!/usr/bin/env python3
"""B2 · 扇出、扇入，以及进程可以走开的人类闸。

这个文件证明什么
  plan → [security ∥ cost ∥ rollback] → merge → ⏸ human_gate → publish
    · 三个审稿人在线程池里**并行**跑，各拿一份 state 副本；findings 是 append-reducer 键 —— 三份拼接，不是最后写者赢
    · human_gate 声明在 interrupt_before：运行时写完检查点就 return，**进程退出**。没有任何线程在等 input()
    · 第二个进程 --approve 读检查点续跑闸后面两步，账单 0 次调用（审稿人不重付）
    · --reject "why" 把 next 指回 plan，findings 用 Replace() 清空，再来一轮
  图比循环多买到的东西：能停在一个**有名字的状态**上，让另一个进程（十秒后、十天后）接着跑。

看板上看哪几栏
  · 图：三个审稿人同时脉冲（parallel_group）；human_gate 虚线红框（中断点）；停机标签「interrupted before human_gate」
  · 第二个进程：页面横幅「已重连到新进程（续跑）」，Token 表 0 次调用，图从 human_gate 接着亮
  · Prompt/产物：plan 每轮一版；--reject 后 diff

课上怎么跑（两个进程）
  python3 b2_fanout_gate.py                # 到闸停下，进程退出
  python3 b2_fanout_gate.py --approve      # 新进程续跑：0 次调用
  python3 b2_fanout_gate.py --reject "cost delta is hand-wavy"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                          # noqa: E402
from live.tinygraph import END, Graph, Replace    # noqa: E402

CKPT = Path(__file__).resolve().parent / "checkpoints" / "b2"
TASK = ("Write a deployment plan (max 180 words) for moving a course platform's LLM gateway from one "
        "region to two regions with failover. Include: rollout steps, a rollback trigger, and a rough monthly cost delta.")


def build(run: bus.Run) -> Graph:
    def plan(state: dict) -> dict:
        user = TASK
        if state.get("human_note"):
            user += f"\n\nA human reviewer rejected the previous plan: {state['human_note']}\nPrevious plan:\n{state['plan']}"
        text = llm.chat("You are a senior SRE. Be concrete.", [{"role": "user", "content": user}], max_tokens=500, tag="plan").strip()
        run.artifact("plan", text, lang="md", note=f"第 {state.get('round', 0) + 1} 轮" + (f"（人类打回：{state['human_note']}）" if state.get("human_note") else ""))
        return {"plan": text, "findings": Replace(), "round": state.get("round", 0) + 1}     # 新一轮：findings 清空

    def reviewer(lens: str):
        def node(state: dict) -> dict:
            text = llm.chat(f"You review deployment plans strictly through the lens of {lens}. Reply with at most 3 bullet findings, each one line.",
                            [{"role": "user", "content": state["plan"]}], max_tokens=200, tag=f"review_{lens}")
            lines = [l.strip("-•* ").strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
            return {"findings": [f"[{lens}] {l}" for l in lines]}
        return node

    def merge(state: dict) -> dict:
        run.artifact("findings", "\n".join(f"- {f}" for f in state["findings"]), lang="md", note=f"{len(state['findings'])} 条，三个审稿人拼接")
        return {"summary": f"{len(state['findings'])} findings from 3 reviewers (round {state['round']})"}

    def publish(state: dict) -> dict:
        out = CKPT / "published.md"
        out.write_text(f"# Deployment plan (round {state['round']})\n\n{state['plan']}\n\n## Review findings\n" + "\n".join(f"- {f}" for f in state["findings"]) + "\n")
        return {"published": str(out)}

    g = Graph("b2").append_key("findings")
    g.add_node("plan", plan, kind="llm").add_node("merge", merge).add_node("human_gate", lambda s: {}, kind="gate").add_node("publish", publish)
    for lens in ("security", "cost", "rollback"):
        g.add_node(f"review_{lens}", reviewer(lens), kind="llm")
    g.set_entry("plan").add_fanout("plan", ["review_security", "review_cost", "review_rollback"], "merge")
    g.add_edge("merge", "human_gate").add_edge("human_gate", "publish").add_edge("publish", END)
    return g


def main() -> None:
    ap = bus.argparser("B2 · 扇出扇入 + 人类闸")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject", metavar="NOTE")
    args = ap.parse_args()
    ck = CKPT / "b2-t1.json"
    resuming = bool(args.approve or args.reject)
    with bus.start("b2_fanout_gate", "B2 · 扇出扇入 + 人类闸", args, budget={"usd": 0.75}, resumed=resuming) as run:
        g = build(run)
        runner = g.compile(checkpoint_dir=CKPT, interrupt_before=["human_gate"])
        if resuming:
            if not ck.exists():
                run.stop("error: 没有检查点，先不带参数跑一次", ok=False); return
            saved = json.loads(ck.read_text())
            if args.approve:
                saved["state"]["__approved__human_gate"] = True; saved["next"] = "human_gate"       # 开闸
                run.say("人类：approve → 从 human_gate 续跑（审稿人不会重跑）")
            else:
                saved["state"]["human_note"] = args.reject; saved["next"] = "plan"; saved["status"] = "rejected"   # 打回 plan
                run.say(f"人类：reject「{args.reject}」→ 回到 plan 再来一轮")
            ck.write_text(json.dumps(saved, indent=1, ensure_ascii=False))
            state = runner.invoke({}, thread_id="t1", resume=True, log=lambda m: run.log(m))
        else:
            run.say(g.mermaid())
            state = runner.invoke({}, thread_id="t1", log=lambda m: run.log(m))
        run.say("路径：" + " → ".join(runner.path()))
        if state["__status__"] == "interrupted":
            run.say(f"⏸ 到闸了。检查点 {ck.name} 已写好；这个进程现在退出。等人：--approve 或 --reject \"<why>\"")
            for f in state["findings"]:
                run.say("   - " + f)
            run.stop("interrupted before human_gate", ok=None, findings=len(state["findings"]))
        else:
            run.say(f"已发布 → {state['published']}")
            run.stop("done", ok=True, published=state["published"], calls_this_process=run.totals()["calls"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""B1 · 带上限的环 —— draft → critique → route → {END, revise → critique}。

这个文件证明什么
  循环是只有一个节点、一条回边的图；一旦循环体里有两种步（写 / 评），你就在画图。
  四个节点，全部工程在 route 的三行：
      score ≥ PASS        → accept
      revisions ≥ MAX_REV → give_up          ← 上限：环唯一保证会终止的东西
      否则                 → revise
  评委也是模型 = 有噪声的尺子。图不让它更准，只让噪声**有界、可见**：执行路径打印出来，分数进 state。
  同一张图两次跑，一次 7,6,7,7 撞上限 give_up、一次 7,6,9 accept —— 两种出口都合法。

看板上看哪几栏
  · 图：当前节点实心脉冲，走过的淡蓝，revise→critique 回边加粗；下方执行路径 chips
  · Prompt/产物：draft 每改一版 diff（评委的 violations 有没有真被修）
  · 分数曲线：critic_score 逐轮；验证表：judge 每轮分数 + violations

课上怎么跑
  python3 b1_cycle_cap.py               # PASS=9, MAX_REV=3（排练：sonnet 8,8,9 → 改两次 accept）
  python3 b1_cycle_cap.py --pass 8      # 松一点的评委 → sonnet 一稿就过，环一次都不转
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                  # noqa: E402
from live.tinygraph import END, Graph      # noqa: E402

BRIEF = ("Write a 90-120 word announcement for an evening class called 'Loop Engineering and Graph "
         "Engineering' for working engineers. It must: name one concrete thing attendees will build, "
         "state the date (Thursday 19:30, online), avoid hype words (revolutionary, game-changing, unlock), "
         "and end with a one-sentence call to action.")
PASS, MAX_REV = 9, 3


def build(run: bus.Run) -> Graph:
    def draft(state: dict) -> dict:
        text = llm.chat("You are a precise copywriter.", [{"role": "user", "content": state["brief"]}], max_tokens=400, tag="draft").strip()
        run.artifact("draft", text, note="初稿")
        return {"draft": text, "revisions": 0}

    def critique(state: dict) -> dict:
        sysmsg = ("You are a strict editor. Score the text 1-10 against the brief and list concrete violations. "
                  'Reply with JSON only: {"score": int, "issues": [str, ...]}')
        raw = llm.chat(sysmsg, [{"role": "user", "content": f"Brief:\n{state['brief']}\n\nText:\n{state['draft']}"}], max_tokens=300, tag="critic")
        try:
            r = json.loads(llm.extract_code(raw)); score, issues = int(r["score"]), [str(i) for i in r.get("issues", [])]
        except Exception:
            score, issues = 0, [f"critic replied non-JSON: {raw[:80]}"]
        run.verify(f"rev {state.get('revisions', 0)}", score >= PASS, "; ".join(issues) or "no issues", kind="judge", score=score, max=10)
        run.score("critic_score", state.get("revisions", 0), score)
        return {"score": score, "issues": issues, "history": {"rev": state.get("revisions", 0), "score": score}}

    def revise(state: dict) -> dict:
        user = (f"Brief:\n{state['brief']}\n\nCurrent text:\n{state['draft']}\n\nEditor's issues:\n- " + "\n- ".join(state["issues"]))
        text = llm.chat("You revise text to satisfy an editor. Keep what works; fix every listed issue. Reply with the text only.",
                        [{"role": "user", "content": user}], max_tokens=400, tag="revise").strip()
        run.artifact("draft", text, note=f"第 {state['revisions'] + 1} 次修改，针对：{'; '.join(state['issues'])[:80]}")
        return {"draft": text, "revisions": state["revisions"] + 1}

    def route(state: dict) -> str:                  # ← 全部工程就在这三行
        if state["score"] >= PASS:
            return "accept"
        if state["revisions"] >= MAX_REV:
            return "give_up"                        # 上限：没有它，噪声评委的寿命就是循环的寿命
        return "revise"

    g = Graph("b1").append_key("history")
    g.add_node("draft", draft, kind="llm").add_node("critique", critique, kind="validator").add_node("revise", revise, kind="llm")
    g.set_entry("draft").add_edge("draft", "critique").add_edge("revise", "critique")
    g.add_conditional_edges("critique", route, {"accept": END, "revise": "revise", "give_up": END})
    return g


def main() -> None:
    global PASS, MAX_REV
    ap = bus.argparser("B1 · 带上限的环")
    ap.add_argument("--pass", dest="pass_", type=int, default=9)
    ap.add_argument("--max-rev", type=int, default=3)
    args = ap.parse_args()
    PASS, MAX_REV = args.pass_, (1 if args.fast else args.max_rev)
    with bus.start("b1_cycle_cap", "B1 · 带上限的环", args, budget={"usd": 0.75}) as run:
        g = build(run)
        run.say(g.mermaid())
        runner = g.compile(checkpoint_dir=Path(__file__).resolve().parent / "checkpoints" / "b1")
        state = runner.invoke({"brief": BRIEF}, log=lambda m: run.log(m, "info"))
        run.say("路径：" + " → ".join(runner.path()))
        run.say(f"分数：{[(h['rev'], h['score']) for h in state['history']]}")
        exit_ = "accept" if state["score"] >= PASS else "give_up（撞上限）"
        run.say(f"出口：{exit_}（score {state['score']}，改了 {state['revisions']} 次）\n\n{state['draft']}")
        run.stop(exit_, ok=state["score"] >= PASS, score=state["score"], revisions=state["revisions"])


if __name__ == "__main__":
    main()

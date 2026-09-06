#!/usr/bin/env python3
"""C4 · 进化上下文 —— ACE：Generator / Reflector / Curator 增量 playbook vs 整段重写（阶梯第 1 档）。

这个文件证明什么
  17 条工单要路由到 7 个**乱起名**的团队（ORCA、VESTA…），规则猜不到，只能从错例里学。两条臂从同一句种子指令出发：
    ACE 臂    Generator 拿 playbook 路由 → Reflector 读这一批的错例写教训 → Curator 把教训变成对编号条目的
              ADD / UPDATE / REMOVE 增量（delta）→ playbook 只累积，绝不整段重写
    重写臂    同样的错例，让模型把整个 prompt 重写一遍（大家常见的做法）
  在线学习：每轮只看 5 条的 mini-batch，全集评估。准确率两臂常常打平；分开的是**写出成本**：
  ACE 只写 Θ(Δ)，重写臂每轮烧 Θ(上下文) 且随上下文增长。以及：ACE 的每条规则有出处、可逐条否决。
  一期第八课的诚实发现：论文说的「上下文塌缩」在玩具规模复现不出来（现代模型压缩几乎无损），所以只卖数据能证明的东西。

看板上看哪几栏
  · Prompt/产物：playbook（ACE，每轮 diff = delta）vs rewrite_prompt（重写臂，每轮整段变）
  · 分数曲线「acc」：两臂全集准确率；「chars」：两臂每轮写出的字符数（重写臂越写越多）
  · 验证：每轮每臂 17 条判决（answer）

课上怎么跑
  python3 c4_ace_playbook.py               # 3 轮，约 130 次调用、4 分钟（路由用 haiku：便宜，也更能看出学习）
  python3 c4_ace_playbook.py --rounds 5    # 排练：ACE 5→9→16→17→17 / 重写 9→12→12→16→16（/17）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402

GEN_MODEL = os.environ.get("GEN_MODEL") or llm.SMALL     # 路由用便宜模型：便宜，且裸模型确实不会，才看得到学习
TEAMS = ["ORCA", "VESTA", "LUMEN", "ATLAS", "PINE", "KESTREL", "REDLINE"]
# 规则只存在于数据里（含两条 override）：紧急+账单 → REDLINE（压过 LUMEN）；API 相关 → KESTREL（压过一切）
DATA = [
    ("Export to CSV fails for large date ranges", "ORCA"), ("Can I download my invoices as a spreadsheet?", "ORCA"),
    ("Dark mode toggle doesn't persist after reload", "VESTA"), ("The sidebar overlaps the chart on small screens", "VESTA"),
    ("I was charged twice this month", "LUMEN"), ("Need a receipt with our VAT number", "LUMEN"),
    ("URGENT: card charged three times, please refund now", "REDLINE"), ("Billing emergency — account suspended for non-payment but we paid", "REDLINE"),
    ("How do I add a teammate to the workspace?", "ATLAS"), ("Where do I change my notification settings?", "ATLAS"),
    ("Search returns nothing for exact ticket IDs", "PINE"), ("Full-text search ignores accented characters", "PINE"),
    ("The REST API returns 500 on /v2/export", "KESTREL"), ("API rate limit headers are missing", "KESTREL"),
    ("Webhook payload changed and broke our API integration", "KESTREL"), ("Urgent billing question about API overage charges", "KESTREL"),
    ("Password reset email never arrives", "ATLAS"),
]
TRICK = {6, 7, 15}      # override 题：只靠字面规则会错
SEED = "Route the support ticket to exactly one team. Teams: " + ", ".join(TEAMS) + ". Reply with the team name only."


def route(instr: str, playbook: list[str], msg: str) -> str:
    sysmsg = instr + ("\n\nPlaybook (numbered rules, learned from mistakes):\n" + "\n".join(f"[{i+1}] {r}" for i, r in enumerate(playbook)) if playbook else "")
    out = llm.chat(sysmsg, [{"role": "user", "content": msg}], max_tokens=10, model=GEN_MODEL, tag="generator").strip().upper()
    return next((t for t in TEAMS if t in out), out[:10])


def evaluate(run: bus.Run, arm: str, instr: str, playbook: list[str], rnd: int) -> tuple[int, int, list[str]]:
    acc = trick = 0; mistakes = []
    for i, (msg, gold) in enumerate(DATA):
        got = route(instr, playbook, msg)
        ok = got == gold
        acc += ok; trick += ok and i in TRICK
        run.verify(f"{arm}·r{rnd}·#{i}", ok, f"{msg[:50]} → {got} (gold {gold})", kind="answer", arm=arm)
        if not ok:
            mistakes.append(f"ticket: {msg}\n  predicted {got}, correct {gold}")
    return acc, trick, mistakes


def main() -> None:
    ap = bus.argparser("C4 · ACE playbook vs 整段重写")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    R = 2 if args.fast else args.rounds
    with bus.start("c4_ace_playbook", "C4 · ACE：增量 playbook vs 整段重写", args, budget={"usd": 0.75}) as run:
        run.say(f"{len(DATA)} 条工单 → 7 个乱名团队 · {R} 轮 · mini-batch 5 · 路由 {GEN_MODEL} · 反思/策展/重写 {llm.MODEL}")
        playbook: list[str] = []; rewrite = SEED
        ace_chars = naive_chars = 0
        run.artifact("playbook", "(空)", note="第 0 轮：什么都没学"); run.artifact("rewrite_prompt", rewrite, note="第 0 轮：种子指令")
        for rnd in range(R + 1):
            run.iter("round", rnd, R, label="全集评估" if rnd == 0 else f"batch {[(rnd-1)*4 + j for j in range(5)]}")
            a_acc, a_trick, a_mist = evaluate(run, "ACE", SEED, playbook, rnd)
            n_acc, n_trick, n_mist = evaluate(run, "rewrite", rewrite, [], rnd)
            run.score("acc_ACE", rnd, a_acc, group="acc"); run.score("acc_rewrite", rnd, n_acc, group="acc")
            run.say(f"round {rnd}: ACE {a_acc}/17 (trick {a_trick}/4, {len(playbook)} 条)   rewrite {n_acc}/17 (trick {n_trick}/4)")
            if rnd == R:
                break
            batch = [(i % len(DATA)) for i in range((rnd) * 4, (rnd) * 4 + 5)]
            a_batch = [m for m in a_mist if any(DATA[i][0] in m for i in batch)]
            n_batch = [m for m in n_mist if any(DATA[i][0] in m for i in batch)]
            # ── ACE 臂：Reflector 写教训 → Curator 出 delta ─────────────────────
            lessons = llm.chat("You are the REFLECTOR in an Agentic Context Engineering loop. Read this batch's routing mistakes and write concrete lessons (which kinds of tickets go to which team, including override rules). Be specific; do not generalize away edge cases.",
                               [{"role": "user", "content": "Current playbook:\n" + ("\n".join(f"[{i+1}] {r}" for i, r in enumerate(playbook)) or "(empty)") + "\n\nMistakes in this batch:\n" + ("\n".join(a_batch) or "(none)")}],
                               max_tokens=450, tag="reflector")
            raw = llm.chat('You are the CURATOR. You maintain a structured playbook of numbered bullets. You NEVER rewrite the whole playbook. Emit a minimal list of structured deltas. Return ONLY JSON: {"deltas":[{"op":"ADD","text":"..."},{"op":"UPDATE","id":N,"text":"..."},{"op":"REMOVE","id":N}]}. Preserve specific edge-case rules.',
                           [{"role": "user", "content": "Playbook:\n" + ("\n".join(f"[{i+1}] {r}" for i, r in enumerate(playbook)) or "(empty)") + "\n\nLessons:\n" + lessons}],
                           max_tokens=700, tag="curator")
            try:                                                   # 取最外层 {...}：模型爱加围栏和废话
                deltas = json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("deltas", [])
            except Exception as e:
                deltas = []; run.log(f"curator 输出解析失败：{e}", level="warn")
            for d in deltas:
                if d.get("op") == "ADD" and d.get("text"): playbook.append(d["text"])
                elif d.get("op") == "UPDATE" and 1 <= int(d.get("id", 0)) <= len(playbook): playbook[int(d["id"]) - 1] = d.get("text", playbook[int(d["id"]) - 1])
                elif d.get("op") == "REMOVE" and 1 <= int(d.get("id", 0)) <= len(playbook): playbook.pop(int(d["id"]) - 1)
            written = sum(len(d.get("text", "")) for d in deltas); ace_chars += written
            run.artifact("playbook", "\n".join(f"[{i+1}] {r}" for i, r in enumerate(playbook)), note=f"第 {rnd+1} 轮 delta：{[d.get('op') for d in deltas]}，写出 {written} 字符")
            # ── 重写臂：整段重写 ───────────────────────────────────────────────
            rewrite = llm.chat("You improve a system prompt. Given the current prompt and how it did on the latest batch, rewrite a single, clear, improved system prompt that will do better. Keep it concise and well-structured. Output ONLY the new prompt text.",
                               [{"role": "user", "content": f"Current prompt:\n{rewrite}\n\nMistakes in this batch:\n" + ("\n".join(n_batch) or "(none)")}],
                               max_tokens=500, tag="rewriter").strip()
            naive_chars += len(rewrite)
            run.artifact("rewrite_prompt", rewrite, note=f"第 {rnd+1} 轮整段重写，写出 {len(rewrite)} 字符")
            run.score("chars_ACE", rnd + 1, ace_chars, group="chars"); run.score("chars_rewrite", rnd + 1, naive_chars, group="chars")
        run.say(f"\n写出成本：ACE 累计 {ace_chars} 字符（Θ(Δ)） · 重写 累计 {naive_chars} 字符（Θ(上下文)，{naive_chars / max(ace_chars, 1):.1f}×）")
        if ace_chars < naive_chars:
            run.say("ACE 赢在 delta 经济学（只写改动）和可审计：每条规则有出处、可逐条 veto。准确率打平不打平，看上面的数据。")
        else:
            run.say("这次 ACE 写出的字符没少于重写臂（策展模型把 delta 写得太啰嗦）—— 它这轮赢的只是可审计：每条规则有出处、可逐条 veto。诚实地说。")
        run.stop("done", ok=True, ace_rules=len(playbook), ace_chars=ace_chars, rewrite_chars=naive_chars)


if __name__ == "__main__":
    main()

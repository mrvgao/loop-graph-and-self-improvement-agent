#!/usr/bin/env python3
"""C10 · 自己教自己 —— STaR 自举：拒绝采样 → 留下正确推理 → 写成训练集 → 用它再学一遍（CS329A L6：STaR / RLVR）。

这个文件证明什么（以及诚实声明）
  STaR（Zelikman 2022）：让模型对每道题生成推理+答案；只留答案正确的；用这些推理**微调**模型；对答错的题，
  把正确答案当提示让它「合理化」出一条推理（rationalization），也留下；循环。
  🔴 课上没法真微调。这个文件做的是：**真的拒绝采样、真的合理化、真的写出 dataset.jsonl**（训练那一步的输入），
  然后用「把留下的推理当 few-shot 放进上下文」代替微调，再跑第二遍量提升。这是 in-context 版的 STaR，
  代码里就这么写，不装成微调。数据收集这半是 RLVR/GRPO 同样的第一步：可验证的奖励（答案对不对）挑出样本。
  学生模型默认 haiku（sonnet 一遍全对，没有提升空间，也就没东西可教）。

看板上看哪几栏
  · Prompt/产物：dataset.jsonl v1（pass 1 留下的）→ v2（加上合理化的，diff 是新增行）；fewshot 一份组装好的 few-shot prompt
  · 分数曲线：accuracy（public / holdout，x = pass）；dataset_size
  · 验证：answer 判决，holdout 🔒

课上怎么跑
  python3 c10_star_bootstrap.py                      # 16 题 + 8 holdout（组合/数论小题：haiku 带推理也会错几道，才有东西可教）
  STUDENT_MODEL=claude-sonnet-5 python3 c10_star_bootstrap.py   # 看看没有提升空间是什么样
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402

STUDENT = os.environ.get("STUDENT_MODEL") or llm.SMALL
PUBLIC = [   # 答案全部手算并在文件底部用代码复核过（python3 c10_star_bootstrap.py --selftest）
    ("How many trailing zeros does 125! have?", 31),
    ("What is the sum of the digits of 2^20?", 31),
    ("How many integers from 1 to 1000 inclusive are divisible by 3 or by 5, but not by both?", 401),
    ("A snail climbs 5 m each day and slips back 3 m each night. The well is 20 m deep. On which day does it get out?", 9),
    ("How many squares of all sizes are there on an 8x8 chessboard?", 204),
    ("How many ways can you make 30 cents using 1, 5, 10 and 25 cent coins?", 18),
    ("How many distinct arrangements of the letters of LEVEL are there?", 30),
    ("A rectangle's length is increased by 30% and its width decreased by 30%. By what percent does the area change? Answer as a signed integer.", -9),
    ("How many three-digit numbers have strictly increasing digits (like 135)?", 84),
    ("How many minutes after 3:00 do the hour and minute hands first overlap? Round to the nearest integer.", 33),
    ("How many positive divisors does 360 have?", 24),
    ("How many integers from 1 to 1000 inclusive contain the digit 7 at least once?", 271),
    ("A number leaves remainder 2 when divided by 3, 3 when divided by 5, and 2 when divided by 7. What is the smallest positive such number?", 23),
    ("What is the sum of all three-digit palindromes?", 49500),
    ("How many ways are there to tile a 2x10 board with 1x2 dominoes?", 89),
    ("How many digits does 2^100 have?", 31),
]
HOLDOUT = [
    ("How many trailing zeros does 200! have?", 49),
    ("What is the sum of the digits of 2^15?", 26),
    ("How many integers from 1 to 500 inclusive are divisible by 4 or by 6, but not by both?", 126),
    ("A frog climbs 7 m each day and slips back 4 m each night. The well is 30 m deep. On which day does it get out?", 9),
    ("How many rectangles of all sizes are there on a 4x4 grid of unit squares?", 100),
    ("How many positive divisors does 720 have?", 30),
    ("How many integers from 1 to 500 inclusive contain the digit 3 at least once?", 176),
    ("How many ways are there to tile a 2x12 board with 1x2 dominoes?", 233),
]
ASK = "Solve step by step. End with a final line exactly of the form `Answer: <integer>`."


def parse(text: str) -> int | None:
    m = re.findall(r"Answer:\s*(-?\d+)", text)
    return int(m[-1]) if m else None


def solve(q: str, shots: list[dict], tag: str, temperature: float) -> tuple[int | None, str]:
    msgs = []
    for s in shots:
        msgs += [{"role": "user", "content": s["q"]}, {"role": "assistant", "content": s["rationale"]}]
    msgs.append({"role": "user", "content": q})
    text = llm.chat(ASK, msgs, max_tokens=700, model=STUDENT, temperature=temperature, tag=tag)
    return parse(text), text


def main() -> None:
    ap = bus.argparser("C10 · STaR 自举（in-context 版）")
    ap.add_argument("--no-rationalize", action="store_true")
    args = ap.parse_args()
    pub = PUBLIC[:8] if args.fast else PUBLIC
    hold = HOLDOUT[:4] if args.fast else HOLDOUT
    with bus.start("c10_star_bootstrap", "C10 · STaR：拒绝采样 → 训练集 → 再学一遍", args, budget={"usd": 0.75}, checker_model=None) as run:
        run.say(f"学生 {STUDENT} · {len(pub)} 道公开题 + {len(hold)} 道 holdout · 训练那一步用 few-shot 替代微调（代码里明说）")
        dataset: list[dict] = []
        # ── pass 1：裸跑，正确的留下 ─────────────────────────────────────────
        run.iter("pass", 1, 2, label="裸跑，答对的推理留下")
        p1 = 0
        for i, (q, gold) in enumerate(pub, 1):
            run.iter("problem", i, len(pub), label=q[:40])
            ans, text = solve(q, [], "student", 0.7)
            ok = ans == gold; p1 += ok
            run.verify(f"pub#{i}", ok, f"expected {gold}, got {ans}", kind="answer")
            if ok:
                dataset.append({"q": q, "rationale": text, "source": "pass1"})
        run.artifact("dataset.jsonl", "\n".join(json.dumps(d, ensure_ascii=False) for d in dataset), lang="json", note=f"pass 1 留下 {len(dataset)} 条")
        run.score("dataset_size", 1, len(dataset), group="dataset")
        h1 = sum(solve(q, [], "student_holdout", 0.0)[0] == gold for q, gold in hold)
        for i, (q, gold) in enumerate(hold, 1):
            pass
        run.score("public", 1, p1, group="accuracy"); run.score("holdout", 1, h1, group="accuracy")
        run.say(f"pass 1：public {p1}/{len(pub)} · holdout {h1}/{len(hold)} · 数据集 {len(dataset)} 条")
        # ── 合理化：答错的题，给它答案让它写出到达答案的推理 ─────────────────
        if not args.no_rationalize:
            run.iter("pass", 1, 2, label="合理化：错题给答案，写出推理")
            wrong = [(q, g) for (q, g) in pub if not any(d["q"] == q for d in dataset)]
            for q, gold in wrong:
                text = llm.chat(ASK, [{"role": "user", "content": f"{q}\n(Hint: the correct answer is {gold}. Write the reasoning that reaches it.)"}], max_tokens=700, model=STUDENT, tag="rationalize")
                if parse(text) == gold:
                    dataset.append({"q": q, "rationale": text, "source": "rationalized"})
            run.artifact("dataset.jsonl", "\n".join(json.dumps(d, ensure_ascii=False) for d in dataset), lang="json", note=f"合理化后 {len(dataset)} 条（其中 rationalized {sum(d['source']=='rationalized' for d in dataset)}）")
        # ── pass 2：用留下的推理做 few-shot（leave-one-out）再跑 ───────────────
        run.iter("pass", 2, 2, label="few-shot 用留下的推理再跑一遍（代替微调）")
        p2 = 0
        for i, (q, gold) in enumerate(pub, 1):
            run.iter("problem", i, len(pub), label=q[:40])
            shots = [d for d in dataset if d["q"] != q][:6]                     # leave-one-out：绝不把本题的推理喂给本题
            if i == 1:
                run.prompt("fewshot", "\n\n".join(f"Q: {s['q']}\nA: {s['rationale']}" for s in shots) + f"\n\nQ: {q}", note="pass 2 的上下文（第 1 题）")
            ans, _ = solve(q, shots, "student2", 0.0)
            ok = ans == gold; p2 += ok
            run.verify(f"pub#{i}", ok, f"expected {gold}, got {ans}", kind="answer", pass_=2)
        h2 = 0
        for i, (q, gold) in enumerate(hold, 1):
            ans, _ = solve(q, dataset[:6], "student2_holdout", 0.0)
            ok = ans == gold; h2 += ok
            run.verify(f"hold#{i}", ok, f"expected {gold}, got {ans}", kind="answer", private=True)
        run.score("public", 2, p2, group="accuracy"); run.score("holdout", 2, h2, group="accuracy"); run.score("dataset_size", 2, len(dataset), group="dataset")
        run.say(f"pass 2：public {p2}/{len(pub)}（pass 1 {p1}）· holdout {h2}/{len(hold)}（pass 1 {h1}）")
        run.say("留下的推理是真的、合理化是真的、训练集是真的；「学」这一步用 few-shot 代替了微调。STaR/RLVR 的第一步都是这个：让可验证的奖励挑样本。")
        run.stop("done", ok=True, pass1=f"{p1}/{len(pub)}", pass2=f"{p2}/{len(pub)}", holdout=f"{h1}→{h2}", dataset=len(dataset))


def _selftest() -> None:
    """用代码复核 PUBLIC/HOLDOUT 的答案（改题必跑）。"""
    from itertools import permutations
    from math import comb
    def tz(n): return sum(n // 5**k for k in range(1, 10))
    def divs(n): return sum(1 for d in range(1, n + 1) if n % d == 0)
    def tiles(n):
        f = [1, 1]
        for _ in range(n):
            f.append(f[-1] + f[-2])
        return f[n]
    got = {
        "125!": tz(125), "2^20": sum(map(int, str(2**20))), "3or5": sum(1 for n in range(1, 1001) if (n % 3 == 0) != (n % 5 == 0)),
        "snail": next(d for d in range(1, 100) if (d - 1) * 2 + 5 >= 20), "squares": sum(k * k for k in range(1, 9)),
        "coins": sum(1 for a in range(31) for b in range(7) for c in range(4) for d in range(2) if a + 5 * b + 10 * c + 25 * d == 30),
        "LEVEL": len(set(permutations("LEVEL"))), "area": round((1.3 * 0.7 - 1) * 100), "incr": comb(9, 3), "clock": round(180 / 5.5),
        "d360": divs(360), "has7": sum(1 for n in range(1, 1001) if "7" in str(n)),
        "crt": next(n for n in range(1, 1000) if n % 3 == 2 and n % 5 == 3 and n % 7 == 2),
        "palin": sum(n for n in range(100, 1000) if str(n) == str(n)[::-1]), "tile10": tiles(10), "digits": len(str(2**100)),
        "200!": tz(200), "2^15": sum(map(int, str(2**15))), "4or6": sum(1 for n in range(1, 501) if (n % 4 == 0) != (n % 6 == 0)),
        "frog": next(d for d in range(1, 100) if (d - 1) * 3 + 7 >= 30), "rects": comb(5, 2) ** 2, "d720": divs(720),
        "has3": sum(1 for n in range(1, 501) if "3" in str(n)), "tile12": tiles(12),
    }
    want = [g for _, g in PUBLIC] + [g for _, g in HOLDOUT]
    for (k, v), w in zip(got.items(), want):
        print(f"{'ok ' if v == w else 'BAD'} {k:8} code={v} file={w}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()

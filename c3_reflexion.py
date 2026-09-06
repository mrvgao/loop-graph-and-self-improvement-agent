#!/usr/bin/env python3
"""C3 · 自己给自己挑毛病 —— Reflexion：试 → 自我批评 → 带着反思记忆再试（CS329A L4：self-critique / Constitutional）。

这个文件证明什么
  A1 的重试把**尺子的一句话**喂回去。Reflexion 多一层：失败后先让模型对着一份「宪法」（几条原则）
  写一段语言反思 ——「我为什么错、下次要怎么做」—— 存进情景记忆；下一次尝试把全部反思放进上下文。
  反思是文字的（不是梯度），跨试次累积（不是每次清零），这就是「语言当梯度」的最小版本。
  什么时候自评可信？验证比生成容易的时候。所以这里评的不是模型自己的意见，而是尺子那句话 + 原则。

看板上看哪几栏
  · Prompt/产物：reflections.md 每次失败后长一条；diff 就是新长出来的那条反思
  · 循环时间线：task → trial；每次 trial 的 ✓/✗；critic 调用（tag reflector）
  · 分数曲线：solved_after_trial —— 第 1 / 2 / 3 次尝试后累计解决几题

课上怎么跑
  python3 c3_reflexion.py               # 6 题 × 最多 3 次尝试
  python3 c3_reflexion.py --no-reflect  # 对照：同样 3 次但不写反思（= A1 的盲重试）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402
from live.tasks import TASKS, public_examples  # noqa: E402
from live.verify import verify                 # noqa: E402

SYSTEM = "You write one Python function. Standard library only. Reply with a single ```python fence containing only the function."
CONSTITUTION = """Principles for self-critique:
1. The examples are the spec: every example implies a general rule; state the rule, not the example.
2. Real inputs are messy: whitespace, case, separators, prefixes/suffixes, empty input.
3. A failing test names one input; ask what *class* of inputs it stands for.
4. Prefer the smallest change that keeps all previously passing cases passing.
5. Write the lesson so that a stranger could apply it to a similar task."""


def act(task: dict, reflections: list[str]) -> str:
    user = f"Write `{task['signature']}`.\nSpec: {task['spec']}\nExamples:\n{public_examples(task)}"
    if reflections:
        user += "\n\nYour reflections from previous attempts (apply them):\n" + "\n".join(f"- {r}" for r in reflections)
    return llm.extract_code(llm.chat(SYSTEM, [{"role": "user", "content": user}], max_tokens=700, tag="actor"))


def reflect(task: dict, code: str, feedback: str) -> str:
    sysmsg = CONSTITUTION + "\nYou are reflecting on a failed attempt. Write ONE reflection (≤ 60 words): why it failed and the general rule to apply next time. Reply with the reflection only."
    user = f"Task: {task['signature']}\nSpec: {task['spec']}\n\nAttempt:\n```python\n{code}\n```\nVerifier said: {feedback}"
    return llm.chat(sysmsg, [{"role": "user", "content": user}], max_tokens=160, tag="reflector").strip()


def main() -> None:
    ap = bus.argparser("C3 · Reflexion")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--no-reflect", action="store_true", help="对照：不写反思，只盲重试")
    args = ap.parse_args()
    tasks = TASKS[:3] if args.fast else TASKS
    T = 2 if args.fast else args.trials

    with bus.start("c3_reflexion", "C3 · Reflexion：自我批评 + 反思记忆", args, budget={"usd": 0.75}) as run:
        run.say(f"{len(tasks)} 题 · 每题最多 {T} 次 · {'不写反思（对照）' if args.no_reflect else '失败后写反思进记忆'}")
        solved_by_trial = [0] * (T + 1)
        memory_md = "# Reflections\n"
        for ti, task in enumerate(tasks, 1):
            run.iter("task", ti, len(tasks), label=task["name"])
            reflections: list[str] = []
            for t in range(1, T + 1):
                run.iter("trial", t, T, label=task["name"])
                code = act(task, reflections)
                ok, fb = verify(task, code)
                run.verify(task["name"], ok, fb, kind="tests", private=True, trial=t)
                if ok:
                    for tt in range(t, T + 1):
                        solved_by_trial[tt] += 1
                    run.say(f"  {task['name']:14} 第 {t} 次 ✓")
                    break
                if args.no_reflect or t == T:
                    run.say(f"  {task['name']:14} 第 {t} 次 ✗ {fb[:70]}")
                    continue
                r = reflect(task, code, fb)                                   # 自我批评：对着宪法写一条反思
                reflections.append(r)
                memory_md += f"\n## {task['name']} · trial {t}\n- {fb}\n- 反思：{r}\n"
                run.artifact("reflections.md", memory_md, lang="md", note=f"{task['name']} 第 {t} 次失败后")
                run.say(f"  {task['name']:14} 第 {t} 次 ✗ → 反思：{r[:90]}")
        for t in range(1, T + 1):
            run.score("solved_after_trial", t, solved_by_trial[t])
        run.say(f"\n累计解决：{[f'第{t}次后 {solved_by_trial[t]}' for t in range(1, T+1)]}")
        run.say("反思是文字不是梯度，跨试次累积不清零 —— 这是「语言当梯度」的最小版本；前提是尺子可信，不然反思的是错误的错误。")
        run.stop("done", ok=True, solved=solved_by_trial[T])


if __name__ == "__main__":
    main()

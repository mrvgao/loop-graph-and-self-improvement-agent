#!/usr/bin/env python3
"""A3 · 同一个骨架，往上套一层 —— 让循环改写 system prompt；public 涨、private 动不动？

这个文件证明什么
  A1 里人写 prompt。这里把 A1 的内层重试**关掉**（K=1，逼 prompt 自己扛能力），外面再套一个循环，
  被改的对象 θ 是 system prompt 本身：
    Propose   反思模型读尺子的句子（哪题错、错在哪），把 prompt 改写一版
    Evaluate  用新 prompt 在三个 PUBLIC 任务上一次性写代码打分
    Select    只在 public 分数**涨**时接受
    Retain    归档每个候选；被拒的下次给反思模型看，免得重复
  三个 PRIVATE 任务算了分、记了分，但**从不给 Propose 看**。看板两条线一起读：
  public 涨 private 不动 = 它学的是答案不是能力（隐藏用例被逐字抄进了 prompt）。
  这就是 GEPA 去掉 Pareto 前沿的样子，也是 C 部分「尺子决定形状」的第一次现场。

看板上看哪几栏
  · Prompt/产物：system v0 → v3，accepted 实心 / rejected 空心；并排 diff 看它往 prompt 里写了什么
  · 分数曲线：public / private 两条线，接受点实心、拒绝点空心
  · 验证：私有那几行带 🔒

课上怎么跑
  python3 a3_prompt_evolution.py            # 3 代（约 27 次调用）
  python3 a3_prompt_evolution.py --gens 5
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402
from live.tasks import TASKS, public_examples  # noqa: E402
from live.verify import verify                 # noqa: E402

PUBLIC, PRIVATE = TASKS[:3], TASKS[3:]         # 优化器只看得见 PUBLIC
SEED = "You write one Python function. Reply with a single ```python fence containing only the function."


def coder(system: str, task: dict) -> str:
    user = f"Write `{task['signature']}`.\nSpec: {task['spec']}\nExamples:\n{public_examples(task)}"
    return llm.extract_code(llm.chat(system, [{"role": "user", "content": user}], max_tokens=800, tag="coder"))


def evaluate(run: bus.Run, system: str, tasks: list[dict], *, private: bool) -> tuple[int, list[str]]:
    """一次性（K=1）逐题打分 —— 内层循环故意关掉，prompt 必须自己扛。"""
    score, failures = 0, []
    for t in tasks:
        ok, fb = verify(t, coder(system, t))
        run.verify(t["name"], ok, fb, kind="tests", private=private)
        score += ok
        if not ok:
            failures.append(fb)
    return score, failures


def propose(system: str, failures: list[str], rejected: list[str]) -> str:
    """GEPA 式：反思模型读尺子的句子，用散文改写 prompt。故意不禁止它写具体规则 —— 这正是要讲的。"""
    sysmsg = ("You improve a system prompt for a code-writing model. Read the failures and rewrite the prompt "
              "so the coder would not make them again: name the input variations it must tolerate, and the "
              "habits (read examples for implied rules, handle messy input, test edge cases mentally). "
              "Keep it under 150 words. Reply with the new prompt only.")
    user = f"Current prompt:\n{system}\n\nFailures on the last run:\n- " + "\n- ".join(failures or ["(none)"])
    if rejected:
        user += "\n\nThese rewrites were already tried and did NOT improve the score; do something different:\n" + "\n---\n".join(r[:400] for r in rejected[-2:])
    return llm.chat(sysmsg, [{"role": "user", "content": user}], max_tokens=350, temperature=0.7, tag="reflector").strip()


def main() -> None:
    ap = bus.argparser("A3 · prompt 进化（外层循环）")
    ap.add_argument("--gens", type=int, default=3)
    args = ap.parse_args()
    gens = 2 if args.fast else args.gens

    with bus.start("a3_prompt_evolution", "A3 · 让循环改写 prompt", args, budget={"usd": 0.75}) as run:
        theta = SEED
        run.iter("gen", 0, gens, label="种子 prompt")
        pub, fails = evaluate(run, theta, PUBLIC, private=False)
        priv, _ = evaluate(run, theta, PRIVATE, private=True)
        run.prompt("system", theta, note="种子", accepted=True, score={"public": pub, "private": priv})
        run.score("public", 0, pub, label="accept"); run.score("private", 0, priv, label="accept")
        run.say(f"gen 0  public {pub}/{len(PUBLIC)}  private {priv}/{len(PRIVATE)}  （种子 {len(theta)} 字符）")
        rejected: list[str] = []
        accepted_n = 0
        for g in range(1, gens + 1):
            run.iter("gen", g, gens, label="Propose → Evaluate → Select → Retain")
            cand = propose(theta, fails, rejected)                              # Propose（看得见拒稿归档）
            c_pub, c_fails = evaluate(run, cand, PUBLIC, private=False)         # Evaluate（只用 public！）
            c_priv, _ = evaluate(run, cand, PRIVATE, private=True)              # 只记不喂
            accepted = c_pub > pub                                              # Select
            run.prompt("system", cand, note=f"gen {g} · {'接受' if accepted else '拒绝'}", accepted=accepted,
                       score={"public": c_pub, "private": c_priv})
            run.score("public", g, c_pub, label="accept" if accepted else "reject")
            run.score("private", g, c_priv, label="accept" if accepted else "reject")
            run.say(f"gen {g}  public {c_pub}/{len(PUBLIC)}  private {c_priv}/{len(PRIVATE)}  {'ACCEPT' if accepted else 'reject'}  （{len(cand)} 字符）")
            if accepted:
                theta, pub, fails = cand, c_pub, c_fails                        # Retain
                accepted_n += 1
            else:
                rejected.append(cand)
        run.say("\n最终 prompt：\n" + theta)
        run.say("两列一起读：public 涨且 private 涨 = 学到了习惯；public 涨 private 不动 = 学到了答案。同一个循环，只有第二列告诉你是哪种。")
        run.stop("done", ok=True, accepted=f"{accepted_n}/{gens}", public=pub)


if __name__ == "__main__":
    main()

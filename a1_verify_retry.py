#!/usr/bin/env python3
"""A1 · 一次性调用就是 K=1 的循环 —— 加验证 / 不加验证，差在哪。

这个文件证明什么
  同一个函数、同一个 prompt，只改一个参数 K（最多尝试几次）：
    arm A  K=1           一次性调用 —— 你以为的「单次调用」其实就是 K=1 的循环
    arm B  K=4 盲重试     失败了换个种子再来，不看尺子 —— 这是**对照组**，缺了它 A→C 就一次动了两个变量
                          （次数 1→4 且反馈 无→有），课上「你不就是多调了四次模型吗」这句反驳就站得住
    arm C  K=4 验证-重试  失败了把尺子那句话「输入 / 期望 / 实际」拼回输入再来
  尺子 = 隐藏测试（live/tasks.py 里每题两到四条、spec 里故意没写的边界规则）。

看板上看哪几栏
  · 循环时间线：task → attempt 两层嵌套；每次 verify 一行 ✓/✗ + 那句话
  · 分数曲线「arms」：三条 arm 各解决几题（柱）；「per_solved」：每解决一题花多少 token
  · LLM 调用：点开任意一条，看 arm C 第二次尝试时上下文里多出来的那一句反馈

课上怎么跑
  python3 a1_verify_retry.py                # 三条臂全跑，6 题，K=4（约 2 分钟）
  python3 a1_verify_retry.py --arms A,C     # 只看有没有循环（快，但归因不干净）
  python3 a1_verify_retry.py --fast         # 3 题，K=2（排练用）
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402
from live.tasks import TASKS, public_examples  # noqa: E402
from live.verify import verify                 # noqa: E402

SYSTEM = ("You write one Python function. Standard library only, no imports unless essential. "
          "Reply with a single ```python fence containing only the function.")


def propose(task: dict, feedback: str | None, previous: str | None, *, tag: str, temperature: float) -> str:
    """Act：让模型写代码。重试时把上一版和尺子那句话一起给它（arm C）；盲重试只换温度不给反馈（arm B）。"""
    user = f"Write `{task['signature']}`.\nSpec: {task['spec']}\nExamples:\n{public_examples(task)}"
    if feedback:
        user += (f"\n\nYour previous version:\n```python\n{previous}\n```\n"
                 f"It failed: {feedback}\nFix it. Reply with the full function only.")
    return llm.extract_code(llm.chat(SYSTEM, [{"role": "user", "content": user}], max_tokens=800, tag=tag, temperature=temperature))


@dataclass
class Outcome:
    ok: bool
    attempts: int
    stop: str                      # 为什么停：goal / no-progress / max_attempts
    feedback: list[str] = field(default_factory=list)


def verify_retry_loop(run: bus.Run, task: dict, *, max_attempts: int, use_feedback: bool, tag: str) -> Outcome:
    """就是这个循环。Plan-Act-Verify，尺子那句话回流；三个停机条件。"""
    feedback, code, seen = None, None, set()
    out = Outcome(ok=False, attempts=0, stop="")
    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        run.iter("attempt", attempt, max_attempts, label=f"{tag} · {task['name']}", task=task["name"])
        code = propose(task, feedback if use_feedback else None, code, tag=tag,
                       temperature=0.0 if (use_feedback or attempt == 1) else 0.8)      # Act
        ok, feedback = verify(task, code)                                                  # Verify —— 尺子
        run.verify(task["name"], ok, feedback, kind="tests", private=True, arm=tag, attempt=attempt)
        out.feedback.append(feedback)
        if ok:
            out.ok, out.stop = True, "goal"                                                # 停机 1：达标
            return out
        h = hashlib.md5(code.encode()).hexdigest()
        if h in seen:
            out.stop = "no-progress"                                                       # 停机 2：同样的代码第二次 = 反馈通道已空
            return out
        seen.add(h)
    out.stop = "max_attempts"                                                              # 停机 3：预算用完
    return out


ARMS = {"A": dict(k=1, fb=False, name="A · K=1 一次性"),
        "B": dict(k=4, fb=False, name="B · K=4 盲重试"),
        "C": dict(k=4, fb=True, name="C · K=4 验证-重试")}


def main() -> None:
    ap = bus.argparser("A1 · 一次性 vs 验证-重试")
    ap.add_argument("--arms", default="A,B,C", help="A,B,C 任意组合（默认三条：没有 B 就无法把「多试几次」和「看反馈」拆开）")
    ap.add_argument("--k", type=int, default=4)
    args = ap.parse_args()
    tasks = TASKS[:3] if args.fast else TASKS
    arms = [a.strip().upper() for a in args.arms.split(",")]
    K = 2 if args.fast else args.k

    with bus.start("a1_verify_retry", "A1 · 一次性 vs 验证-重试", args, budget={"usd": 0.75}) as run:
        run.say(f"{len(tasks)} 题 · arms {arms} · K={K} · 尺子 = 隐藏测试")
        results = {a: [] for a in arms}
        for ti, task in enumerate(tasks, 1):
            run.iter("task", ti, len(tasks), label=task["name"])
            for a in arms:
                cfg = ARMS[a]
                out = verify_retry_loop(run, task, max_attempts=1 if a == "A" else K, use_feedback=cfg["fb"], tag=f"arm_{a}")
                results[a].append(out)
                run.say(f"  {cfg['name']:16} {task['name']:14} {'✓' if out.ok else '✗'} ({out.attempts} 次, {out.stop})")
        # 账单：每个 arm 解决几题、每解决一题花多少 token
        run.say("")
        for a in arms:
            solved = sum(o.ok for o in results[a])
            t = run.totals(f"arm_{a}")
            per = (t["in"] + t["out"]) / max(solved, 1)
            run.score(ARMS[a]["name"], 0, solved, group="arms")
            run.score(ARMS[a]["name"], 0, round(per), group="per_solved")
            run.say(f"{ARMS[a]['name']:16} 解决 {solved}/{len(tasks)} · {t['calls']} 次调用 · 每解决一题 {per:,.0f} tok · ${t['cost']:.3f}")
        # 第一课的算术，现在是测出来的
        # A→B→C 三条臂：一次只动一个变量，所以收益能分开归因
        n = {a: sum(o.ok for o in results[a]) for a in arms}
        if {"A", "B", "C"} <= set(arms):
            run.say(f"多试几次买到的：A {n['A']} → B {n['B']}（+{n['B']-n['A']}）；把尺子那句话放进输入买到的：B {n['B']} → C {n['C']}（+{n['C']-n['B']}）")
            run.say("B 的第一次尝试和 A 是同一次调用（同 prompt、同温度 0），所以 B 把 A 包住了；三条臂一次只动一个变量。")
            np = sum(o.stop == "no-progress" for o in results["B"])
            if np:
                saved = sum(K - o.attempts for o in results["B"] if o.stop == "no-progress")
                run.say(f"注意 B 的停机原因：{np}/{len(tasks)} 题停在 no-progress —— 温度 0.8 也写出了字节相同的代码。"
                        "没有新信息进来，模型只会把同一个错答案再交一遍。"
                        + (f"「无进展」这条规则替这几题省下了 {saved} 次调用。" if saved else ""))
        pA = n.get("A")
        if pA is not None:
            pA = pA / len(tasks)
            run.say(f"实测单步 p≈{pA:.2f} → 六步链存活 p^6≈{pA**6:.3f}；K={K} 重试后每步 {1-(1-pA)**K:.2f} → 链存活 {(1-(1-pA)**K)**6:.2f}")
            run.say("循环没有让模型更聪明，它只是把尺子那句话放进了输入。")
        run.stop("done", ok=True, solved={a: sum(o.ok for o in results[a]) for a in arms})


if __name__ == "__main__":
    main()

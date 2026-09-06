#!/usr/bin/env python3
"""A2 · 有目的地停，免费地续 —— 四种停机条件、原子检查点、幂等续跑。

这个文件证明什么
  一个「批处理式」agent 循环（12 条客户留言逐条抽成 JSON）欠你的三样东西：
    · 停机条件按你想听到的顺序排：goal → max_steps → token 预算 → 墙钟 → 无进展；预算**在花钱之前**查
    · 每步之后写检查点：临时文件 + os.replace（崩在写一半也不坏）；按 item 编号做键（幂等：续跑零重复）
    · 无进展规则：同一原因拒两次就跳过、记原因、往下走 —— 否则 12 条的活变成 1 条的账单
  没上限的循环是没封顶的账单；没检查点的循环是付两次的账单。

看板上看哪几栏
  · 循环时间线：item → attempt 两层；每条 💾 检查点；停机原因大标签
  · Token 与成本：预算条（--max-tokens 会让它涨到 80% 变红，然后停成一份报告不是栈回溯）
  · 崩溃演示：--crash-after 5 是 os._exit(137)（= kill -9），页面左上角的点会变红「连接断开」；
    再跑一次，页面横幅「已重连到新进程（续跑）」，时间线从第 6 条接着走，零重复调用

课上怎么跑（三连）
  python3 a2_stop_resume.py --reset --crash-after 5
  python3 a2_stop_resume.py                       # resumed：5 done, 7 to go
  python3 a2_stop_resume.py --reset --max-tokens 800   # 12 条约 1500 tok，800 停在第 6 条左右
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402

CKPT = Path(__file__).resolve().parent / "checkpoints" / "a2_state.json"

ITEMS = [
    "The new dashboard is great but exports still time out on large ranges.",
    "Charged twice this month. Please refund one of them.",
    "How do I add a teammate to my workspace?",
    "Loving the API — shipped our integration in a day.",
    "Password reset email never arrives (checked spam).",
    "Please add dark mode. My eyes.",
    "The mobile app crashes when I open notifications.",
    "Can I get an invoice with our VAT number on it?",
    "Docs for webhooks are out of date; the payload changed.",
    "Cancel my subscription effective end of month.",
    "Your support team was fast and kind. Thank you.",
    "Search returns nothing for exact ticket IDs.",
]
SYSTEM = ('Extract from the customer message a JSON object with exactly these keys: '
          '"sentiment" (one of "pos","neg","neu"), "topic" (2-4 words), '
          '"action" (one imperative sentence for the support team). Reply with JSON only.')


@dataclass
class Budget:
    max_steps: int
    max_tokens: int
    max_seconds: float
    max_inner: int = 2                      # 内层校验-重试最多几次

    def check(self, steps: int, tokens: int, started: float) -> str | None:
        """返回停机原因，或 None。顺序 = 你想听到的顺序。**每步之前**调用。"""
        if steps >= self.max_steps:
            return "max_steps"
        if tokens >= self.max_tokens:
            return "token_budget"
        if time.time() - started >= self.max_seconds:
            return "wall_clock"
        return None


def validate(text: str) -> tuple[dict | None, str]:
    """内层尺子：是不是我要的那个 JSON？"""
    try:
        obj = json.loads(llm.extract_code(text))
    except Exception as e:
        return None, f"not valid JSON ({e})"
    if set(obj) != {"sentiment", "topic", "action"}:
        return None, f"keys were {sorted(obj)}; expected sentiment/topic/action"
    if obj["sentiment"] not in ("pos", "neg", "neu"):
        return None, f'sentiment was {obj["sentiment"]!r}; must be pos/neg/neu'
    return obj, "ok"


def load_state() -> dict:
    return json.loads(CKPT.read_text()) if CKPT.exists() else {"done": {}, "skipped": {}}


def save_state(state: dict) -> None:
    CKPT.parent.mkdir(exist_ok=True)
    tmp = CKPT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    os.replace(tmp, CKPT)                    # 原子：崩在写一半也不会留下半个文件


def main() -> None:
    ap = bus.argparser("A2 · 停机与续跑")
    ap.add_argument("--crash-after", type=int, default=0, help="做完 N 条后 os._exit(137)（模拟 kill -9）")
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--max-tokens", type=int, default=100_000)
    ap.add_argument("--max-seconds", type=float, default=600)
    ap.add_argument("--reset", action="store_true", help="先删检查点")
    args = ap.parse_args()
    if args.reset and CKPT.exists():
        CKPT.unlink()
    items = ITEMS[:6] if args.fast else ITEMS
    budget = Budget(args.max_steps, args.max_tokens, args.max_seconds)
    state = load_state()
    todo = [i for i in range(len(items)) if str(i) not in state["done"] and str(i) not in state["skipped"]]

    with bus.start("a2_stop_resume", "A2 · 停机与续跑", args, budget={"usd": 0.75, "tokens": args.max_tokens},
                   resumed=bool(state["done"] or state["skipped"])) as run:
        run.say(f"{len(items)} 条 · 已做 {len(state['done'])} · 已跳过 {len(state['skipped'])} · 待做 {len(todo)} · {budget}")
        started, steps, stop = time.time(), 0, None
        for i in todo:
            stop = budget.check(steps, run.totals()["in"] + run.totals()["out"], started)      # 花钱之前查
            if stop:
                break
            run.iter("item", i + 1, len(items), label=items[i][:40])
            feedback = None
            for attempt in range(1, budget.max_inner + 1):                                     # 内层校验-重试
                run.iter("attempt", attempt, budget.max_inner, label="抽 JSON")
                user = f"Message: {items[i]}"
                if feedback:
                    user += f"\n\nYour previous reply was rejected: {feedback}. Reply with JSON only."
                text = llm.chat(SYSTEM, [{"role": "user", "content": user}], max_tokens=200, tag="extract")
                obj, feedback = validate(text)
                run.verify(f"#{i:02d}", obj is not None, feedback, kind="validator")
                if obj:
                    state["done"][str(i)] = obj
                    run.say(f"  #{i:02d} ✓ {obj['sentiment']:3} | {obj['topic']:24} | {obj['action'][:60]}")
                    break
            else:
                state["skipped"][str(i)] = feedback                        # 无进展：跳过记原因，不空转
                run.say(f"  #{i:02d} ✗ 跳过（{budget.max_inner} 次都不对）：{feedback}")
            steps += 1
            save_state(state); run.checkpoint(CKPT, status="running", done=len(state["done"]), skipped=len(state["skipped"]))
            if args.crash_after and steps >= args.crash_after:
                run.say(f"💥 模拟崩溃（kill -9）：做完 {steps} 条，检查点里有 {len(state['done'])} 条")
                sys.stdout.flush()                 # os._exit 不冲缓冲，和真 kill -9 一样 —— 这里 flush 是为了让你看见这句
                time.sleep(0.5)                    # 让最后一个事件推到页面
                os._exit(137)
        remaining = len(items) - len(state["done"]) - len(state["skipped"])
        if stop:
            run.say(f"⏹ 停机：{stop}（还剩 {remaining} 条；再跑一次会从这里接着做）")
            run.stop(stop, ok=False, remaining=remaining)
        else:
            run.say(f"✓ 全部 {len(items)} 条都有交代：{len(state['done'])} 做完、{len(state['skipped'])} 跳过")
            run.stop("goal", ok=True, done=len(state["done"]), skipped=len(state["skipped"]))


if __name__ == "__main__":
    main()

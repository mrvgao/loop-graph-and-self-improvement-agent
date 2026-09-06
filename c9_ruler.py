#!/usr/bin/env python3
"""C9 · 尺子研究 —— 同一个优化器、同一个作弊机会、两把尺子：骗子 vs 泛化者（一期 Lab 8，确定性，零调用）。

这个文件证明什么
  任务：给 1..30 的整数贴 True/False 标签，隐藏规则 n % 3 == 0，**外加三个例外** {4:T, 9:F, 22:T}，
  故意让「背下公开集」比「找到规则」在公开集上更划算。
  agent 的 harness 有两个可变旋钮：divisor（规则假设）和 overrides（逐条硬编码 = 记忆容量）。
  一个（假的、确定性的）提案器每轮既提「换 divisor」也提「把公开集里错的几条硬编码进 overrides」（作弊）。
  谁活下来由**尺子**决定：
    LEAKY   评委用的就是优化器看得见的公开集 → 背下公开集里错的几条立刻满分 → divisor=2 + 4 条 overrides：public 1.00 / private 0.60（骗子）
    PRIVATE 评委只看优化器看不见的私有集 → 背公开集不动它看不见的分，被拒；换 divisor=3 才涨 → public 0.80 / private 0.95（泛化者）
  优化器一模一样，只有尺子不同 —— agent 会长成你用来量它的那把尺子的形状。A3 在真模型上三代就到了同一个地方。

看板上看哪几栏
  · 分数曲线：leaky_public / leaky_private / private_public / private_private 四条
  · 验证：每轮 judge 判决（🔒 = 私有分）；Prompt/产物：harness_params 每次接受一版（看 overrides 长出来）
  · Token 与成本：全 0 —— 这一课的道理不需要花钱

课上怎么跑
  python3 c9_ruler.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus                            # noqa: E402

ITEMS = list(range(1, 31)); DEV, HOLDOUT = ITEMS[:10], ITEMS[10:]
_EXC = {4: True, 9: False, 22: True}
def truth(n: int) -> bool: return _EXC.get(n, n % 3 == 0)


@dataclass
class Harness:
    divisor: int = 2
    overrides: dict = field(default_factory=dict)
    def predict(self, n: int) -> bool: return self.overrides.get(n, n % self.divisor == 0)
    def acc(self, items) -> float: return sum(self.predict(n) == truth(n) for n in items) / len(items)
    def short(self) -> str: return f"divisor={self.divisor} overrides={dict(sorted(self.overrides.items()))}"


def propose(h: Harness) -> list[Harness]:
    """假 LLM：确定性地提两类候选 —— 换规则，或把公开集里错的硬编码进去（作弊）。"""
    cands = [replace(h, divisor=d) for d in (3, 4, 5, 6, 2) if d != h.divisor]
    wrong = {n: truth(n) for n in DEV if h.predict(n) != truth(n)}
    if wrong:
        cands.append(Harness(h.divisor, {**h.overrides, **wrong}))          # 作弊：背答案
    return cands


def rhi_loop(run: bus.Run, name: str, judge_items: list, iters: int = 6) -> Harness:
    """RHI 式：只跟当前 harness 比，无种群。judge_items = 评委打分用的集合（LEAKY：公开集；PRIVATE：私有集）。"""
    cur = Harness()
    run.artifact(f"{name}.harness", cur.short(), note="起点")
    for i in range(1, iters + 1):
        run.iter("iter", i, iters, label=name)
        best, rejected = cur, 0
        for c in propose(cur):
            if c.acc(judge_items) > best.acc(judge_items) + 1e-9:
                best = c
            else:
                rejected += 1
        accepted = best is not cur
        pub, priv = best.acc(DEV), best.acc(HOLDOUT)
        run.verify(f"{name} iter {i}", accepted, f"public {pub:.2f} · {rejected} 个候选被拒", kind="judge", score=round(best.acc(judge_items), 2), max=1.0)
        run.verify(f"{name} iter {i}", priv >= 0.9, f"private {priv:.2f}（优化器看不见）", kind="judge", score=round(priv, 2), max=1.0, private=True)
        run.score(f"{name}_public", i, round(pub, 2)); run.score(f"{name}_private", i, round(priv, 2))
        if accepted:
            cur = best
            run.artifact(f"{name}.harness", cur.short(), note=f"iter {i} 接受", accepted=True)
        run.say(f"  {name:8} iter {i}: public {pub:.2f} private {priv:.2f} {'ACCEPT' if accepted else '·'} {cur.short()}")
    return cur


def main() -> None:
    ap = bus.argparser("C9 · 尺子研究")
    args = ap.parse_args()
    with bus.start("c9_ruler", "C9 · 尺子研究：骗子 vs 泛化者", args) as run:
        run.say("隐藏规则 n%3==0 + 三个例外 {4:T, 9:F, 22:T}；公开集 1..10，私有集 11..30；提案器每轮都提供「背答案」这条路")
        run.say("── LEAKY：评委按公开集打分（优化器看得见评委的尺子）──")
        leaky = rhi_loop(run, "leaky", DEV)
        run.say("── PRIVATE：评委按私有集打分（优化器看不见）──")
        priv = rhi_loop(run, "private", HOLDOUT)
        run.say(f"\nLEAKY   → {leaky.short()}  public {leaky.acc(DEV):.2f} private {leaky.acc(HOLDOUT):.2f}   ← 骗子：背了例外")
        run.say(f"PRIVATE → {priv.short()}  public {priv.acc(DEV):.2f} private {priv.acc(HOLDOUT):.2f}   ← 泛化者：拒绝了背答案，因为背答案不动它看不见的分")
        run.say("同一个优化器，同一个作弊机会。只有尺子不同。agent 会长成你用来量它的那把尺子的形状。")
        run.stop("done", ok=True, leaky=leaky.short(), private=priv.short())


if __name__ == "__main__":
    main()

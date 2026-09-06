#!/usr/bin/env python3
"""C1 · 多试几次 —— pass@k、多数表决、验证器挑选（CS329A L2–L3：test-time scaling 与 verifiers）。

这个文件证明什么
  题目来自 live/tasks_hard.py：规格**写全了**但一次写对不容易（限流窗口、时间片调度、贪吃蛇、购物车凑三免一……全是自编规则，模型没背过）。
  （A1 那六题 spec 故意不全，隐藏规则采样多少次也猜不到 —— 那是「验证-重试」的地形，不是「多采样」的地形；排练时 8 个样本全错在同一处。）
  同一道题采 N 个样本（温度 1.0），四种「从 N 个里挑一个交卷」的策略：
    first     交第一个 —— 就是一次性调用
    majority  多数表决：**只用输入不用答案**，让每个候选跑一遍探针输入，按输出向量聚类，交最大簇的代表（AlphaCode 式行为聚类；没有验证器时的自洽性）
    verifier  交第一个通过**公开**测试的（验证器 = 尺子，看不到隐藏用例）
    oracle    N 个里只要有一个过隐藏测试就算过 —— 这是 pass@N 的上界，画成空心
  pass@k 用 Chen et al. 的无偏估计：每题 c 个候选过隐藏测试，pass@k = 1 − C(N−c,k)/C(N,k)，对题取平均。
  要看到的：coverage 随 k 对数线性地涨；majority ≈ verifier 说明测试信息量够；oracle 和它们之间的差，就是更好的尺子能买到的。
  采样模型默认 **haiku**（SAMPLER_MODEL）。排练（0905）：sonnet-5 六题 pass@1 = 0.97 → pass@6 = 1.00，四种挑法全 6/6 ——
  强模型在这个尺度上没有 coverage 可买；haiku pass@1 = 0.47 → pass@6 = 1.00，first 2 / majority 3 / verifier 3 / oracle 6。
  这本身就是 CS329A 的一课：test-time compute 买的是「pass@1 到 pass@k 之间的那段」，模型越弱那段越长。

看板上看哪几栏
  · 分数曲线「passk」：pass@k 曲线（实测，不是公式）；「selection」：四种策略各解决几题（柱）
  · 验证：每个候选两行 —— 公开（验证器看的）和 🔒 隐藏（只有 oracle 看）
  · Token 与成本：采样 tag 的账单 —— 多试几次不是免费的

课上怎么跑
  python3 c1_best_of_n.py                          # haiku 采样：6 题 × N=6，36 次调用，约 50 秒、$0.17
  python3 c1_best_of_n.py --model claude-sonnet-5  # 看强模型的平线：0.97 → 1.00，约 90 秒、$0.60
  python3 c1_best_of_n.py --n 8                    # 曲线更平滑
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                                              # noqa: E402
from live.tasks_hard import TASKS, forbidden, hidden_cases, probe_args, public_cases, public_examples  # noqa: E402
from live.verify import verify_detail                                  # noqa: E402

SAMPLER = os.environ.get("SAMPLER_MODEL") or llm.SMALL   # 采样模型默认 haiku：sonnet-5 在这六题上 pass@1 = 0.97，没有 coverage 可买（见文件头）
SYSTEM = "You write one Python function (helpers allowed). Standard library only. Compact code: no comments, no docstrings. Reply with a single ```python fence."


def sample(task: dict, j: int) -> str:
    user = f"Write `{task['signature']}`.\nSpec: {task['spec']}\nExamples:\n{public_examples(task)}"
    return llm.extract_code(llm.chat(SYSTEM, [{"role": "user", "content": user}], max_tokens=4000, temperature=1.0, model=SAMPLER, tag="sample"))


def behavior(task: dict, code: str) -> str:
    """跑探针输入（不带期望值），返回输出向量的签名 —— 多数表决只看行为不看答案。"""
    if forbidden(task, code):
        return "FORBIDDEN"
    runner = "import json,sys\nexec(open(sys.argv[1]).read(),globals())\nout=[]\nfor a in json.load(open(sys.argv[2])):\n    try: out.append(repr(eval(f\"%s({a})\")))\n    except Exception as e: out.append('ERR:'+type(e).__name__)\nprint(json.dumps(out))" % task["name"]
    with tempfile.TemporaryDirectory() as d:
        Path(d, "sol.py").write_text(code); Path(d, "args.json").write_text(json.dumps(probe_args(task))); Path(d, "r.py").write_text(runner)
        try:
            p = subprocess.run([sys.executable, "-I", f"{d}/r.py", f"{d}/sol.py", f"{d}/args.json"], capture_output=True, text=True, timeout=5)
            return p.stdout.strip() or "LOAD_ERROR"
        except subprocess.TimeoutExpired:
            return "TIMEOUT"


def pass_at_k(n: int, c: int, k: int) -> float:
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def main() -> None:
    ap = bus.argparser("C1 · best-of-N / pass@k / 多数表决 vs 验证器")
    ap.add_argument("--n", type=int, default=6)
    args = ap.parse_args()
    global SAMPLER
    if args.model:
        SAMPLER = args.model
    N = 3 if args.fast else args.n
    tasks = TASKS[:3] if args.fast else TASKS

    with bus.start("c1_best_of_n", "C1 · 多试几次：pass@k · 多数表决 · 验证器", args, budget={"usd": 1.5}) as run:
        run.say(f"采样模型 {SAMPLER} · {len(tasks)} 题 × N={N} 样本（温度 1.0，8 线程并行）")
        solved = {"first": 0, "majority": 0, "verifier": 0, "oracle": 0}
        c_per_task = []
        for ti, task in enumerate(tasks, 1):
            run.iter("task", ti, len(tasks), label=task["name"])
            with ThreadPoolExecutor(8) as ex:
                cands = list(ex.map(lambda j: sample(task, j), range(N)))
            pub = [verify_detail(task, c, public_cases(task)) for c in cands]
            hid = [verify_detail(task, c, hidden_cases(task)) for c in cands]
            for j, c in enumerate(cands):
                run.iter("sample", j + 1, N, label=task["name"])
                run.verify(f"{task['name']}#{j+1}", pub[j]["ok"], pub[j]["feedback"], kind="tests", score=pub[j]["passed"], max=pub[j]["total"])
                run.verify(f"{task['name']}#{j+1}", hid[j]["ok"], hid[j]["feedback"], kind="tests", score=hid[j]["passed"], max=hid[j]["total"], private=True)
            c = sum(h["ok"] for h in hid); c_per_task.append(c)
            # 四种挑法
            pick = {"first": 0}
            sig = [behavior(task, x) for x in cands]
            top = Counter(sig).most_common(1)[0][0]
            pick["majority"] = sig.index(top)
            pick["verifier"] = next((j for j in range(N) if pub[j]["ok"]), 0)
            for name, j in pick.items():
                ok = hid[j]["ok"]; solved[name] += ok
                run.say(f"  {task['name']:14} {name:9} 交 #{j+1} → 隐藏测试 {'✓' if ok else '✗'}")
            solved["oracle"] += c > 0
            run.say(f"  {task['name']:14} 隐藏测试通过的候选 {c}/{N} · 行为簇 {len(set(sig))} 个，最大簇 {Counter(sig).most_common(1)[0][1]}")
            run.artifact(f"{task['name']}.majority_pick", cands[pick["majority"]], lang="python", note=f"多数表决交的候选（簇大小 {Counter(sig).most_common(1)[0][1]}/{N}）")
        for k in range(1, N + 1):
            run.score("pass@k", k, round(sum(pass_at_k(N, c, k) for c in c_per_task) / len(tasks), 3), group="passk")
        for name, v in solved.items():
            run.score(name, 0, v / len(tasks), group="selection", label="hollow" if name == "oracle" else None)
        run.say("\n" + "  ".join(f"{k} {v}/{len(tasks)}" for k, v in solved.items()))
        run.say(f"pass@1 = {sum(pass_at_k(N, c, 1) for c in c_per_task) / len(tasks):.2f} → pass@{N} = {sum(pass_at_k(N, c, N) for c in c_per_task) / len(tasks):.2f}")
        run.say("多试几次买到的是 coverage；把 coverage 变成正确率，靠的是挑法 —— 挑法就是尺子。oracle 和 verifier 之间的差，是一把更好的尺子的价钱。")
        run.stop("done", ok=True, **solved)


if __name__ == "__main__":
    main()

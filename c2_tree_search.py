#!/usr/bin/env python3
"""C2 · 走多步的题 —— 树搜索（ToT / LATS-lite）：best-first，两种算子，beam 2 深 3（CS329A L5：规划与搜索）。

这个文件证明什么
  A1 的重试是一条线；这里是一棵树。状态 = 一个实现全部六个函数的模块（分数有 30 多级，不是 0/1）。
  题目来自 live/tasks_hard.py（规格写全、一次写对不容易）—— 用 A1 那六题时 sonnet 根节点就 15/16，树长不出来。
  尺子（搜索用）= 公开用例 + 每题隐藏用例的前一半（dev，14 条）；剩下 6 条 holdout 只汇报不参与选择（🔒）。
  每轮从 frontier 里按 (dev 分, -深度) 取最好的 beam=2 个节点，各扩展两个孩子（一轮的四个孩子并行生成）：
    fix      「修这几条失败用例，别动其它」（温度 0.3）
    rethink  「按 spec 重写失败的函数」（温度 0.9）
  孩子打分入 frontier；有节点全过或到第 2 轮就停。总调用 ≤ 1 + 2×4 = 9（约 3 分钟）。
  对照 A1：同样十几次调用，线性重试只能修一条线上的错；树能同时试两种改法，并且**回到更早更好的节点**再扩。
  写码模型默认 **haiku**（WORKER_MODEL）。排练（0905）：sonnet 根 5/18（它 import 了被禁的 re，尺子抓住）→ fix 一步 18/18，树只有三个节点；
  haiku 根 2/18 → n1 14/18 → 四个孙子 10–11 都没超过 n1 —— 最优停在更早的节点上，这正是要看的。

看板上看哪几栏
  · 图：树一边长一边画（mode=merge），节点标签「n5 · 11/14」，当前最优实心
  · Prompt/产物：program 每个节点一版，新最优 accepted
  · 分数曲线：best_so_far / node_score（x = 第几次调用）

课上怎么跑
  python3 c2_tree_search.py                          # haiku：beam 2 深 2，7 次调用，约 1 分钟、$0.15
  python3 c2_tree_search.py --depth 3                # 再长一层
  python3 c2_tree_search.py --model claude-sonnet-5  # 强模型：三个节点就结束
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                                                   # noqa: E402
from live.tasks_hard import TASKS as _ALL, public_cases, hidden_cases, public_examples  # noqa: E402

TASKS = [t for t in _ALL if t["name"] in ("mini_regex", "spreadsheet", "decimal_add")]   # 三题一个模块（六题一次写不完）
from live.verify import verify_detail                                       # noqa: E402

SPEC = "\n\n".join(f"### {t['name']}\n`{t['signature']}`\n{t['spec']}\nExamples:\n{public_examples(t)}" for t in TASKS)
WORKER = os.environ.get("WORKER_MODEL") or llm.SMALL   # 写码模型默认 haiku：sonnet 一步 fix 就全过，树只有三个节点（见文件头）
SYSTEM = "You write a Python module with three functions (helpers allowed). Standard library only; some functions forbid specific modules — obey. Compact code: no comments, no docstrings. Reply with ONE ```python fence containing the whole module."
DEV = {t["name"]: public_cases(t) + hidden_cases(t)[: max(1, len(hidden_cases(t)) // 2)] for t in TASKS}
HOLD = {t["name"]: hidden_cases(t)[max(1, len(hidden_cases(t)) // 2):] for t in TASKS}


def score(code: str, split: dict) -> tuple[int, int, list[str]]:
    passed = total = 0; fails = []
    for t in TASKS:
        d = verify_detail(t, code, split[t["name"]])
        passed += d["passed"]; total += d["total"]
        fails += [f"{t['name']}({r['args']}) {'raised ' + r['error'] if r['error'] else 'returned ' + str(r['got'])}; expected {r['expected']!r}" for r in d["results"] if not r["ok"]]
        if d["load_error"]:
            fails.append(f"{t['name']}: {d['load_error']}")
    return passed, total, fails


def expand(code: str, fails: list[str], op: str) -> str:
    if op == "fix":
        user = f"SPEC:\n{SPEC}\n\nCurrent module:\n```python\n{code}\n```\nFailing cases:\n- " + "\n- ".join(fails[:5]) + "\nFix these; keep everything else unchanged. Reply with the full module."
        temp = 0.3
    else:
        user = f"SPEC:\n{SPEC}\n\nCurrent module:\n```python\n{code}\n```\nThese cases fail:\n- " + "\n- ".join(fails[:5]) + "\nRewrite the failing functions from the spec (think about what input variations the examples imply). Reply with the full module."
        temp = 0.9
    return llm.extract_code(llm.chat(SYSTEM, [{"role": "user", "content": user}], max_tokens=5000, temperature=temp, model=WORKER, tag=op))


def main() -> None:
    ap = bus.argparser("C2 · 树搜索")
    nodes_ref: list = []

    def path(n):
        out = []
        while n is not None:
            out.append(n["id"]); n = nodes_ref[int(n["parent"][1:])] if n["parent"] else None
        return reversed(out)

    ap.add_argument("--beam", type=int, default=2); ap.add_argument("--depth", type=int, default=2)
    args = ap.parse_args()
    global WORKER
    if args.model:
        WORKER = args.model
    beam, depth = (1, 2) if args.fast else (args.beam, args.depth)
    with bus.start("c2_tree_search", "C2 · 树搜索：beam-first 修与重写", args, budget={"usd": 1.0}) as run:
        nodes: list[dict] = nodes_ref; calls = 0
        def add_node(code, parent, op):
            nonlocal calls
            calls += 1
            p, tot, fails = score(code, DEV); hp, htot, _ = score(code, HOLD)
            n = {"id": f"n{len(nodes)}", "parent": parent, "op": op, "code": code, "score": p, "total": tot, "fails": fails, "depth": 0 if parent is None else nodes[int(parent[1:])]["depth"] + 1, "hold": hp}
            nodes.append(n)
            run.verify(n["id"], p == tot, f"dev {p}/{tot}" + (f" · {fails[0]}" if fails else ""), kind="tests", score=p, max=tot)
            run.verify(n["id"], hp == htot, f"holdout {hp}/{htot}", kind="tests", score=hp, max=htot, private=True)
            run.emit("graph.def", graph="搜索树", entry="n0", mode="merge", nodes=[{"id": n["id"], "kind": "llm", "label": f"{n['id']} · {p}/{tot}" + (f" ({op})" if op else "")}],
                     edges=[{"from": parent, "to": n["id"], "kind": "edge", "label": op}] if parent else [], interrupt_before=[], anchors=[])
            run.emit("graph.node", graph="搜索树", node=n["id"], phase="end", step=calls)
            run.score("node_score", calls, p); run.score("best_so_far", calls, max(x["score"] for x in nodes))
            return n
        run.iter("round", 0, depth, label="根：一次实现六个函数")
        root = add_node(llm.extract_code(llm.chat(SYSTEM, [{"role": "user", "content": f"SPEC:\n{SPEC}\n\nImplement all three."}], max_tokens=5000, model=WORKER, tag="root")), None, "")
        best = root; run.artifact("program", root["code"], lang="python", note=f"根 {root['score']}/{root['total']}", accepted=True)
        frontier = [root]
        for r in range(1, depth + 1):
            if best["score"] == best["total"]:
                break
            run.iter("round", r, depth, label=f"扩展 frontier 最好的 {beam} 个")
            frontier.sort(key=lambda n: (-n["score"], n["depth"]))
            picked, frontier = frontier[:beam], frontier[beam:]
            jobs = [(pn, op) for pn in picked if pn["score"] < pn["total"] for op in ("fix", "rethink")]
            for pn, op in jobs:
                run.emit("graph.node", graph="搜索树", node=pn["id"], phase="start", step=calls)
            with ThreadPoolExecutor(4) as ex:                                        # 一轮的孩子并行生成（LATS 也这么干）
                codes = list(ex.map(lambda j: expand(j[0]["code"], j[0]["fails"], j[1]), jobs))
            for (pn, op), code in zip(jobs, codes):
                    run.iter("expand", calls + 1, None, label=f"{pn['id']} → {op}")
                    child = add_node(code, pn["id"], op)
                    frontier.append(child)
                    if child["score"] > best["score"]:
                        best = child; run.artifact("program", child["code"], lang="python", note=f"{child['id']} 新最优 {child['score']}/{child['total']}（{op} of {pn['id']}）", accepted=True)
                    else:
                        run.artifact("program", child["code"], lang="python", note=f"{child['id']} {child['score']}/{child['total']}（{op} of {pn['id']}），没超过最优", accepted=False)
                    run.say(f"  {child['id']} = {op}({pn['id']}) → dev {child['score']}/{child['total']} · holdout {child['hold']}")
        run.say(f"\n{calls} 次调用 · 最优 {best['id']} dev {best['score']}/{best['total']} · holdout {best['hold']}/{sum(len(v) for v in HOLD.values())} · 路径：{' → '.join(path(best))}")
        run.say("树和线的区别：能同时试两种改法，并且能回到更早、更好的节点再扩 —— 线性重试做不到第二件事。")
        run.stop("done", ok=best["score"] == best["total"], best=f"{best['score']}/{best['total']}", calls=calls)


if __name__ == "__main__":
    main()

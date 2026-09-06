#!/usr/bin/env python3
"""C6 · 进化一个程序 —— AlphaEvolve-lite：LLM 变异「构造程序」，确定性评估器当尺子，归档当种群（阶梯第 6 档）。

这个文件证明什么
  题：把 n=8 个圆装进单位正方形，最大化半径和（一期第八课 E1 的小号版，评估只要 1ms，图能放上板）。
  循环从不直接动坐标 —— 它进化的是一个 `pack()` 构造函数的**源码**，改一处代码 8 个圆一起动。
    评估器  纯 Python、子进程 `-I`、2 秒超时；合法 = 8 个圆、r>0、都在方框内、两两不重叠；分 = Σr，否则 0 + 一句原因
    归档    每个评估过的程序 + 分数 + 父节点；亲本按排名加权从前四里抽（4:3:2:1）
    变异    给 LLM：父程序 + 它的分 + 迄今最优 + （若非法）评估器那句原因 + 「换一种构造，允许不等半径」，温度 0.8
    5 代 × 每代 5 个孩子（并行），种子 = 3×3 网格去一个角 r=1/6 → 1.333
  要看到的：大多数变异非法或更差（稀疏成功）；大跳跃来自换构造策略（不等半径、一大四小…），不是调参数。

看板上看哪几栏
  · Prompt/产物：pack.py 每个新最优一版（diff = 策略级改写）；packing 用 SVG 画出当前最优的圆
  · 分数曲线：best / gen_max / gen_mean
  · 验证：validator 每个孩子一行（合法与否、Σr 或原因）

课上怎么跑
  python3 c6_evolve_program.py              # 5 代 × 5（25 次调用）
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402

N = 8
SEED_PROGRAM = '''def pack():
    """Return a list of (x, y, r) for 8 circles inside the unit square, no overlaps. Maximize sum of r."""
    r = 1 / 6
    pts = []
    for i in range(3):
        for j in range(3):
            if (i, j) != (2, 2):
                pts.append(((2 * i + 1) * r, (2 * j + 1) * r, r))
    return pts
'''
EVAL = r'''
import json, math, random, sys
random.seed(0)
src = open(sys.argv[1]).read()
ns = {"math": math, "random": random}
exec(src, ns)
cs = ns["pack"]()
def bad(msg): print(json.dumps({"ok": False, "score": 0, "reason": msg})); sys.exit(0)
if not isinstance(cs, (list, tuple)) or len(cs) != %d: bad(f"need exactly %d circles, got {len(cs) if isinstance(cs,(list,tuple)) else type(cs).__name__}")
cs = [tuple(map(float, c)) for c in cs]
for i, (x, y, r) in enumerate(cs):
    if r <= 0: bad(f"circle {i} has r<=0")
    if x - r < -1e-9 or x + r > 1 + 1e-9 or y - r < -1e-9 or y + r > 1 + 1e-9: bad(f"circle {i} leaves the unit square (x={x:.3f} y={y:.3f} r={r:.3f})")
for i in range(len(cs)):
    for j in range(i + 1, len(cs)):
        d = math.dist(cs[i][:2], cs[j][:2])
        if d < cs[i][2] + cs[j][2] - 1e-9: bad(f"circles {i} and {j} overlap by {cs[i][2] + cs[j][2] - d:.3f}")
print(json.dumps({"ok": True, "score": sum(c[2] for c in cs), "circles": cs}))
''' % (N, N)
FORBID = re.compile(r"^\s*(import|from)\s+(?!math\b|random\b|itertools\b)", re.M)


def evaluate(src: str) -> dict:
    if FORBID.search(src):
        return {"ok": False, "score": 0, "reason": "only math/random/itertools may be imported"}
    with tempfile.TemporaryDirectory() as d:
        Path(d, "p.py").write_text(src); Path(d, "e.py").write_text(EVAL)
        try:
            p = subprocess.run([sys.executable, "-I", f"{d}/e.py", f"{d}/p.py"], capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            return {"ok": False, "score": 0, "reason": "timeout (2s)"}
    if p.returncode != 0 or not p.stdout.strip():
        return {"ok": False, "score": 0, "reason": (p.stderr.strip().splitlines() or ["crash"])[-1][:120]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def mutate(parent: dict, best: float) -> str:
    sysmsg = ("You are an expert in circle packing and computational geometry. You improve a Python constructor function "
              "`pack()` that returns 8 circles (x, y, r) inside the unit square with no overlaps, maximizing the sum of radii. "
              "Unequal radii are allowed and usually better. Only math/random/itertools may be imported. "
              "Reply with the full function in one ```python fence.")
    user = f"Parent program (score {parent['score']:.4f}; best so far {best:.4f}):\n```python\n{parent['src']}\n```\n"
    if not parent["ok"]:
        user += f"The evaluator rejected it: {parent['reason']}\n"
    user += ("Validity rules the evaluator enforces: exactly 8 circles, r>0, each circle fully inside [0,1]², no two circles overlap "
             "(distance between centers ≥ r1+r2). Compute radii FROM the distances (e.g. r = half the smallest center gap, or "
             "r = distance to the nearest wall) so overlaps are impossible by construction. Propose a DIFFERENT arrangement strategy "
             "(unequal radii, one large + small corner circles, hexagonal rows, ...), not just different numbers. Keep it deterministic.")
    return llm.extract_code(llm.chat(sysmsg, [{"role": "user", "content": user}], max_tokens=1500, temperature=0.8, tag="mutate"))


def main() -> None:
    ap = bus.argparser("C6 · 进化一个程序（AlphaEvolve-lite）")
    ap.add_argument("--gens", type=int, default=5); ap.add_argument("--children", type=int, default=5)
    args = ap.parse_args()
    gens, kids = (2, 3) if args.fast else (args.gens, args.children)
    with bus.start("c6_evolve_program", "C6 · 进化一个程序：8 圆装箱", args, budget={"usd": 0.75}) as run:
        seed = {"id": "g0", "src": SEED_PROGRAM, **evaluate(SEED_PROGRAM), "parent": None}
        archive = [seed]; best = seed
        run.artifact("pack.py", seed["src"], lang="python", note=f"种子 Σr={seed['score']:.4f}", accepted=True)
        run.artifact("packing", json.dumps(seed.get("circles", [])), render="circles", note=f"种子 Σr={seed['score']:.4f}")
        run.score("best", 0, round(seed["score"], 4))
        run.say(f"种子：3×3 网格去一角，Σr = {seed['score']:.4f}（AlphaEvolve 纪录风格的题，n=8 小号版）")
        invalid = worse = 0
        for g in range(1, gens + 1):
            run.iter("gen", g, gens, label=f"{kids} 个孩子并行 · 归档 {len(archive)}")
            top = sorted(archive, key=lambda a: -a["score"])[:4]
            parents = random.choices(top, weights=[4, 3, 2, 1][:len(top)], k=kids)     # 排名加权选亲本
            with ThreadPoolExecutor(kids) as ex:
                srcs = list(ex.map(lambda p: mutate(p, best["score"]), parents))
            scores = []
            for c, (p, src) in enumerate(zip(parents, srcs), 1):
                run.iter("child", c, kids, label=f"gen {g} · 亲本 {p['id']}")
                r = evaluate(src); child = {"id": f"g{g}c{c}", "src": src, **r, "parent": p["id"]}
                archive.append(child); scores.append(r["score"])
                run.verify(child["id"], r["ok"], f"Σr={r['score']:.4f}" if r["ok"] else r["reason"], kind="validator", score=round(r["score"], 4))
                if not r["ok"]: invalid += 1
                elif r["score"] <= best["score"]: worse += 1
                if r["ok"] and r["score"] > best["score"]:
                    best = child
                    run.artifact("pack.py", src, lang="python", note=f"{child['id']} 新最优 Σr={r['score']:.4f}（亲本 {p['id']}）", accepted=True)
                    run.artifact("packing", json.dumps(r["circles"]), render="circles", note=f"{child['id']} Σr={r['score']:.4f}")
                run.say(f"  {child['id']} ← {p['id']}: {'Σr=%.4f' % r['score'] if r['ok'] else '✗ ' + r['reason'][:70]}{'  ◀ 新最优' if best is child else ''}")
            valid = [s for s in scores if s > 0]
            run.score("best", g, round(best["score"], 4)); run.score("gen_max", g, round(max(scores), 4)); run.score("gen_mean", g, round(sum(valid) / len(valid), 4) if valid else 0)
        total = gens * kids
        run.say(f"\n种子 {seed['score']:.4f} → 最优 {best['score']:.4f}（{best['id']}）· {total} 次变异：非法 {invalid}、更差 {worse}、更好 {total - invalid - worse} —— 稀疏成功")
        run.say("循环没有动过坐标：它改的是构造程序。大跳跃来自换策略，不是调参数。有精确便宜的尺子，才能用这一档。")
        run.stop("done", ok=best["score"] > seed["score"], best=round(best["score"], 4), invalid=invalid, worse=worse)


if __name__ == "__main__":
    main()

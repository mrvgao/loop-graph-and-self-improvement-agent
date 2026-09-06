#!/usr/bin/env python3
"""C7 · 改自己代码的 agent —— DGM-lite：solve() 的源码被 meta 模型改写，隐藏测试当尺子，严格更好才接受，归档（阶梯第 5 档）。

这个文件证明什么
  改进者和被改进者是同一个对象：agent 的 `solve(task)` 循环就是基因。它是一个源码字符串，被 exec 进一个
  受限命名空间，只能用这几样：
    llm_write(user, system=DEFAULT) -> str   一次模型调用（每题最多 6 次，跑飞了会被掐）
    run_public_tests(code) -> dict           用公开例子自测（允许自验证；隐藏尺子它拿不到）
    extract_code(text) -> str
    task                                     spec / signature / 公开例子
  基准 = 六道题过隐藏测试的数量。每代：meta 模型看 solve() 源码 + 每题失败的尺子句子 + 工具契约，
  改写 solve()（可以加重试、把报错喂回去、自测、反思）；严格高于亲本才接受；全部候选归档。
  要看到的：DGM 论文发现的东西在你眼前长出来 —— 它加的全是 harness 零件（重试、读报错、自检、反思），
  因为「一个更会写代码的 agent」需要的正是这些，所以改进落回到了改进者自身。

看板上看哪几栏
  · Prompt/产物：solve.py v0 → v4，accepted 实心；并排 diff 看 for-循环 / 报错回流 / 自测长出来
  · 分数曲线：benchmark 每代（accept/reject 标记）；验证：每代每题 🔒
  · 循环时间线：agent 的 llm_write 与 run_public_tests 工具调用

课上怎么跑
  python3 c7_self_modify.py            # 4 代（约 60–80 次调用，5–7 分钟）
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                                      # noqa: E402
from live.tasks import TASKS, public_cases, public_examples    # noqa: E402
from live.verify import verify_detail                          # noqa: E402

DEFAULT_SYSTEM = "You write one Python function. Standard library only. Reply with a single ```python fence containing only the function."
AGENT_V0 = '''def solve(task):
    """v0: one shot. Ask once, return whatever comes back."""
    prompt = f"Write `{task['signature']}`.\\nSpec: {task['spec']}\\nExamples:\\n{task['examples']}"
    return extract_code(llm_write(prompt))
'''
MAX_CALLS_PER_TASK = 6


class TooManyCalls(RuntimeError):
    pass


def make_namespace(run: bus.Run, task: dict, budget: list) -> dict:
    def llm_write(user: str, system: str = DEFAULT_SYSTEM) -> str:
        if budget[0] >= MAX_CALLS_PER_TASK:
            raise TooManyCalls(f"agent exceeded {MAX_CALLS_PER_TASK} llm_write calls on one task")
        budget[0] += 1
        return llm.chat(system, [{"role": "user", "content": user}], max_tokens=800, tag="agent")

    def run_public_tests(code: str) -> dict:
        d = verify_detail(task, code, public_cases(task))
        run.emit("tool.call", id=f"pt-{task['name']}-{budget[0]}", name="run_public_tests", input={"task": task["name"]})
        run.emit("tool.result", id=f"pt-{task['name']}-{budget[0]}", name="run_public_tests", ok=d["ok"], output=d["feedback"])
        return {"ok": d["ok"], "passed": d["passed"], "total": d["total"], "feedback": d["feedback"]}
    safe_builtins = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                     for k in ("range", "len", "str", "int", "float", "list", "dict", "min", "max", "enumerate", "isinstance", "print", "sorted", "any", "all", "bool", "Exception", "ValueError", "RuntimeError")}
    return {"__builtins__": safe_builtins, "llm_write": llm_write, "run_public_tests": run_public_tests, "extract_code": llm.extract_code}


def benchmark(run: bus.Run, src: str, gen: int, tasks: list) -> tuple[int, list[str]]:
    """跑 solve() 六道题，隐藏测试计分；每题的尺子句子回收给 meta。"""
    def one(task):
        budget = [0]
        ns = make_namespace(run, task, budget)
        t = {"name": task["name"], "signature": task["signature"], "spec": task["spec"], "examples": public_examples(task)}
        try:
            exec(src, ns)
            code = ns["solve"](t)
            d = verify_detail(task, code or "")
            note = f"{task['name']}: {'pass' if d['ok'] else d['feedback']} ({budget[0]} llm calls)"
            return d["ok"], note, budget[0], d
        except TooManyCalls as e:
            return False, f"{task['name']}: {e}", budget[0], None
        except Exception as e:
            return False, f"{task['name']}: solve() crashed: {type(e).__name__}: {str(e)[:80]}", budget[0], None
    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(one, tasks))
    notes, score = [], 0
    for task, (ok, note, calls, d) in zip(tasks, results):
        run.verify(f"gen{gen}·{task['name']}", ok, note, kind="tests", private=True, score=(d or {}).get("passed"), max=(d or {}).get("total"))
        score += ok; notes.append(note)
    return score, notes


def meta_rewrite(src: str, notes: list[str], score: int, n: int) -> str:
    sysmsg = ("You improve the source code of a coding agent's `solve(task)` function so it scores higher on hidden tests. "
              "Available inside solve (nothing else; no imports): llm_write(user, system=DEFAULT) -> str (at most 6 calls per task), "
              "run_public_tests(code) -> {ok, passed, total, feedback} (public examples only), extract_code(text) -> str, and `task` "
              "(dict with name/signature/spec/examples). Ideas: retry, feed the failing message back, self-test on public examples first, "
              "ask the model to reflect on implied edge rules. Reply with the full `def solve(task):` only, in one ```python fence.")
    user = f"Current solve() scored {score}/{n}:\n```python\n{src}\n```\nBenchmark log:\n- " + "\n- ".join(notes) + "\nRewrite solve() to do better."
    return llm.extract_code(llm.chat(sysmsg, [{"role": "user", "content": user}], max_tokens=1200, temperature=0.5, tag="meta"))


def main() -> None:
    ap = bus.argparser("C7 · 改自己代码的 agent（DGM-lite）")
    ap.add_argument("--gens", type=int, default=4)
    args = ap.parse_args()
    tasks = TASKS[:3] if args.fast else TASKS
    gens = 2 if args.fast else args.gens
    ARCH = Path(__file__).resolve().parent / "checkpoints" / "c7"; ARCH.mkdir(parents=True, exist_ok=True)
    with bus.start("c7_self_modify", "C7 · 改自己代码的 agent", args, budget={"usd": 1.5}) as run:
        run.iter("gen", 0, gens, label="基准：v0 一次性")
        score, notes = benchmark(run, AGENT_V0, 0, tasks)
        archive = [{"v": 0, "src": AGENT_V0, "score": score, "accepted": True}]
        run.artifact("solve.py", AGENT_V0, lang="python", note=f"v0 基准 {score}/{len(tasks)}", accepted=True, score={"solved": score})
        run.score("benchmark", 0, score, label="accept")
        run.say(f"v0：{score}/{len(tasks)}")
        parent = archive[0]
        for g in range(1, gens + 1):
            run.iter("gen", g, gens, label=f"meta 改写 v{parent['v']} → 跑基准")
            src = meta_rewrite(parent["src"], notes, parent["score"], len(tasks))
            try:
                compile(src, "<solve>", "exec")
            except SyntaxError as e:
                run.say(f"  v{g} 语法错误：{e}"); run.artifact("solve.py", src, lang="python", note=f"v{g} 语法错误，拒绝", accepted=False)
                run.score("benchmark", g, 0, label="reject"); continue
            s, new_notes = benchmark(run, src, g, tasks)
            accepted = s > parent["score"]                                   # 严格更好才接受
            archive.append({"v": g, "src": src, "score": s, "accepted": accepted})
            run.artifact("solve.py", src, lang="python", note=f"v{g} {s}/{len(tasks)} · {'接受' if accepted else '拒绝（没超过 v%d 的 %d）' % (parent['v'], parent['score'])}", accepted=accepted, score={"solved": s})
            run.score("benchmark", g, s, label="accept" if accepted else "reject")
            run.say(f"  v{g}：{s}/{len(tasks)} {'ACCEPT' if accepted else 'reject'}")
            if accepted:
                parent, notes = archive[-1], new_notes
            (ARCH / "archive.json").write_text(json.dumps([{k: v for k, v in a.items()} for a in archive], indent=1, ensure_ascii=False))
            run.checkpoint(ARCH / "archive.json", status="running", gen=g)
        run.say(f"\n最终：v{parent['v']} {parent['score']}/{len(tasks)}（接受 {sum(a['accepted'] for a in archive) - 1}/{gens}）")
        run.say("看 diff：它加的全是 harness 零件 —— 重试、读报错、自测、反思。改进「一个会写代码的 agent」= 改进它的 harness，所以改进落回改进者自身。")
        run.stop("done", ok=parent["score"] > archive[0]["score"], best=f"{parent['score']}/{len(tasks)}", version=parent["v"])


if __name__ == "__main__":
    main()

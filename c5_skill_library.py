#!/usr/bin/env python3
"""C5 · 技能库的复利 —— Voyager-lite：写码 → 跑 → 读报错 → 修 → 自验 → 入库，后面的任务直接调用（阶梯第 3 档）。

这个文件证明什么
  五个递进的 CSV 小任务。每个任务：先从库里检索相关技能，写函数时**可以直接调用库里的技能**（不重新实现）；
  跑自验测试，失败就把报错喂回去修（最多 4 次）；通过的函数起名入库。数据里埋了一个环境惊喜：
  数值列里混着 "10%" 这样的字符串 —— 只有读了报错才会发现。
  技能是软基底里唯一**可执行、可测试、可组合**的进化单元：它的自验证就是它的验收测试；学一次的 parse_csv
  会被后面三个技能调用。阴面：过拟合测试的技能会被反复复用。

看板上看哪几栏
  · Prompt/产物：library.py 每入库一版，diff 就是新技能；每次 LLM 调用的上下文里能看到「你可以直接调用的技能」列表在长
  · 验证：每次尝试 ✓/✗ + 报错那句话
  · 分数曲线：library_size、attempts_per_task

课上怎么跑
  python3 c5_skill_library.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402

CSV = "name,dept,score,bonus\nAda,eng,91,10%\nBob,ops,78,5%\nCy,eng,85,0%\nDee,sales,62,12%\nEve,eng,99,15%\n"
TASKS = [
    {"name": "parse_csv", "sig": "def parse_csv(text: str) -> list[dict]", "desc": "Parse CSV text (first line header) into a list of dicts of strings.",
     "test": "rows=parse_csv(CSV); assert len(rows)==5 and rows[0]['name']=='Ada' and rows[4]['dept']=='eng', rows"},
    {"name": "column_sum", "sig": "def column_sum(text: str, col: str) -> float", "desc": "Sum a numeric column of the CSV. Values like '10%' mean 10.",
     "test": "assert abs(column_sum(CSV,'score')-415)<1e-9; assert abs(column_sum(CSV,'bonus')-42)<1e-9, column_sum(CSV,'bonus')"},
    {"name": "filter_rows", "sig": "def filter_rows(text: str, col: str, value: str) -> list[dict]", "desc": "Rows whose column equals value.",
     "test": "r=filter_rows(CSV,'dept','eng'); assert [x['name'] for x in r]==['Ada','Cy','Eve'], r"},
    {"name": "top_row", "sig": "def top_row(text: str, col: str, dept: str) -> dict", "desc": "Among rows of a department, the row with the highest numeric value in col.",
     "test": "assert top_row(CSV,'score','eng')['name']=='Eve'; assert top_row(CSV,'bonus','eng')['name']=='Eve'"},
    {"name": "dept_report", "sig": "def dept_report(text: str) -> dict", "desc": "Map dept -> {'n': count, 'avg_score': mean score rounded to 1 decimal}.",
     "test": "r=dept_report(CSV); assert r['eng']=={'n':3,'avg_score':91.7} and r['ops']['n']==1, r"},
]
SYSTEM = ("You are writing a Python function to complete a task. Use only standard Python; no imports. "
          "You have the following already-built skills you may CALL DIRECTLY (do not re-implement them):\n{skills}\n"
          "Reply with the full function only, in a ```python fence.")


def run_test(library: str, code: str, test: str) -> tuple[bool, str]:
    prog = f"CSV = {CSV!r}\n{library}\n{code}\n{test}\nprint('OK')"
    with tempfile.TemporaryDirectory() as d:
        Path(d, "t.py").write_text(prog)
        try:
            p = subprocess.run([sys.executable, "-I", f"{d}/t.py"], capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if p.returncode == 0 and "OK" in p.stdout:
        return True, "self-test passed"
    return False, (p.stderr.strip().splitlines() or ["unknown error"])[-1]


def main() -> None:
    ap = bus.argparser("C5 · 技能库（Voyager-lite）")
    args = ap.parse_args()
    tasks = TASKS[:3] if args.fast else TASKS
    with bus.start("c5_skill_library", "C5 · 技能库的复利", args, budget={"usd": 0.75}) as run:
        library: dict[str, str] = {}
        run.artifact("library.py", "# (empty)", lang="python", note="开始：库是空的")
        for ti, t in enumerate(tasks, 1):
            run.iter("task", ti, len(tasks), label=t["name"])
            skills = "\n".join(f"  - {n}(...)  # {TASKS[[x['name'] for x in TASKS].index(n)]['desc']}" for n in library) or "  (none yet)"
            messages = [{"role": "user", "content": f"Task: {t['desc']}\nSignature: `{t['sig']}`\nThe CSV in tests is:\n{CSV}"}]
            ok, code = False, ""
            for attempt in range(1, 5):
                run.iter("try", attempt, 4, label=t["name"])
                code = llm.extract_code(llm.chat(SYSTEM.format(skills=skills), messages, max_tokens=600, tag="coder"))
                ok, detail = run_test("\n".join(library.values()), code, t["test"])
                run.verify(t["name"], ok, detail, kind="tests", attempt=attempt)
                if ok:
                    break
                messages += [{"role": "assistant", "content": f"```python\n{code}\n```"},
                             {"role": "user", "content": f"Your previous version failed: {detail}\nFix it. Reply with the full function only."}]
                run.say(f"  {t['name']:12} 第 {attempt} 次 ✗ {detail[:80]} → 报错喂回去")
            if ok:
                library[t["name"]] = code                                     # Retain：起名入库
                reused = [n for n in library if n != t["name"] and n + "(" in code]
                run.artifact("library.py", "\n\n".join(library.values()), lang="python", note=f"入库 {t['name']}（第 {attempt} 次通过）" + (f"，调用了库里的 {reused}" if reused else ""))
                run.say(f"  {t['name']:12} 第 {attempt} 次 ✓ 入库" + (f" · 复用 {reused}" if reused else ""))
            else:
                run.say(f"  {t['name']:12} 4 次都没过，不入库")
            run.score("library_size", ti, len(library)); run.score("attempts", ti, attempt, group="attempts")
        run.say("\n技能是可执行、可测试、可组合的：自验证就是验收测试；学一次，后面直接调。阴面：过拟合测试的技能会被反复复用。")
        run.stop("done", ok=True, skills=list(library))


if __name__ == "__main__":
    main()

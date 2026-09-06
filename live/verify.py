"""live.verify —— 尺子：把模型写的代码放进子进程跑用例。

    ok, feedback = verify(task, code)          # 只报第一个失败的那一句（A 部分：一句话就是一次迭代的全部信息量）
    d = verify_detail(task, code)              # 跑全部用例，返回 {ok, passed, total, feedback, results}（c1/c2 要计数）

反馈的措辞故意像同事说话：输入、期望、实际。沉默是最难调试的失败，所以永远给一句人话。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .tasks import all_cases

HARNESS = r'''
import json, sys
exec(open(sys.argv[1]).read(), globals())
cases = json.load(open(sys.argv[2]))
name = sys.argv[3]
out = []
for args, expected, kind in cases:
    try:
        got = eval(f"{name}({args})")
    except Exception as e:
        out.append({"ok": False, "kind": kind, "args": args, "expected": expected, "got": None, "error": f"{type(e).__name__}: {e}"})
        continue
    same = (abs(got - expected) < 1e-9) if isinstance(expected, float) and isinstance(got, (int, float)) else got == expected
    out.append({"ok": bool(same), "kind": kind, "args": args, "expected": expected, "got": repr(got), "error": None})
print(json.dumps(out))
'''


def _sentence(task: dict, r: dict) -> str:
    if r["error"]:
        return f"{task['name']}({r['args']}) raised {r['error']}; expected {r['expected']!r}"
    return f"{task['name']}({r['args']}) returned {r['got']}; expected {r['expected']!r}"


def verify_detail(task: dict, code: str, cases: list | None = None, timeout: float = 5.0) -> dict:
    cases = cases if cases is not None else all_cases(task)
    for pat in task.get("forbid", []):                       # 题目禁用的库（tasks_hard：没有这条 re.search 一行就完了）
        if re.search(pat, code, re.M):
            return {"ok": False, "passed": 0, "total": len(cases), "feedback": f"用了题目禁止的库/函数（匹配 {pat!r}）",
                    "results": [], "load_error": f"forbidden: {pat}"}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "sol.py").write_text(code)
        (d / "cases.json").write_text(json.dumps(cases))
        (d / "harness.py").write_text(HARNESS)
        try:
            p = subprocess.run([sys.executable, "-I", str(d / "harness.py"), str(d / "sol.py"), str(d / "cases.json"), task["name"]],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "passed": 0, "total": len(cases), "feedback": f"你的代码 {timeout}s 内没跑完（死循环？）",
                    "results": [], "load_error": "timeout"}
    if p.returncode != 0 or not p.stdout.strip():
        tail = " | ".join((p.stderr or p.stdout).strip().splitlines()[-3:])
        return {"ok": False, "passed": 0, "total": len(cases), "feedback": "你的代码加载失败: " + tail, "results": [], "load_error": tail}
    results = json.loads(p.stdout.strip().splitlines()[-1])
    passed = sum(r["ok"] for r in results)
    first = next((r for r in results if not r["ok"]), None)
    return {"ok": passed == len(results), "passed": passed, "total": len(results),
            "feedback": "all cases pass" if first is None else _sentence(task, first), "results": results, "load_error": None}


def verify(task: dict, code: str, cases: list | None = None, timeout: float = 5.0) -> tuple[bool, str]:
    d = verify_detail(task, code, cases, timeout)
    return d["ok"], d["feedback"]

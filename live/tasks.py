"""六个小编程任务，每个带「公开例子」和「隐藏用例」—— 第四课 A/C 部分的尺子。

模型只看到一句 spec + PUBLIC 例子；验证器跑的是 HIDDEN 用例。spec 故意写得不全：
隐藏用例就是真实用户忘了写下来的边界规则（'1,250%'、'1H30M'、比宽度还长的词、v 前缀）。
正因为如此，「验证 → 把那句报错喂回去 → 重试」才值它的 token。

**换成你自己的题**：写一个同结构的模块（六个 dict，字段 name/signature/spec/public/hidden），然后

    LIVE_TASKS=mine.tasks python3 a1_verify_retry.py

a0 / a1 / a3 / b0 / c3 / c7 全都跟着变成你的题 —— 它们都从这里拿 TASKS。
（c1 / c2 用的是 live/tasks_hard.py，另一套地形，不受这个变量影响。）
"""
import os

TASKS = [
    {
        "name": "parse_percent",
        "signature": "def parse_percent(s: str) -> float",
        "spec": "Turn a percentage string into a fraction. '10%' -> 0.1. Whitespace anywhere is ignored. "
                "If there is no % sign the value is already a fraction and is returned as-is.",
        "public": [("'10%'", 0.1), ("'0.25'", 0.25)],
        "hidden": [("' 7.5 % '", 0.075), ("'100%'", 1.0), ("'1,250%'", 12.5)],
    },
    {
        "name": "slugify",
        "signature": "def slugify(title: str) -> str",
        "spec": "Make a URL slug: lowercase, words joined by single hyphens, only a-z and 0-9 kept.",
        "public": [("'Hello World'", "hello-world")],
        "hidden": [("'  Loop   Engineering!! '", "loop-engineering"), ("'C++ & Rust: 2026'", "c-rust-2026"),
                   ("'--a--b--'", "a-b")],
    },
    {
        "name": "parse_duration",
        "signature": "def parse_duration(s: str) -> int",
        "spec": "Parse a duration like '1h30m' into total seconds. Units: h, m, s.",
        "public": [("'1h30m'", 5400), ("'45s'", 45)],
        "hidden": [("'2h'", 7200), ("'1h 5m 3s'", 3903), ("'0m'", 0), ("'90m'", 5400), ("'1H30M'", 5400)],
    },
    {
        "name": "dedupe_emails",
        "signature": "def dedupe_emails(emails: list[str]) -> list[str]",
        "spec": "Remove duplicate email addresses, keeping first-seen order and the first-seen spelling. "
                "Addresses are case-insensitive; surrounding whitespace is not part of an address.",
        "public": [("['A@x.com','a@x.com','b@x.com']", ["A@x.com", "b@x.com"])],
        "hidden": [("[' a@x.com','a@x.com ']", ["a@x.com"]),
                   ("['me+tag@x.com','me@x.com']", ["me+tag@x.com", "me@x.com"]),
                   ("['x@A.COM','x@a.com','y@a.com']", ["x@A.COM", "y@a.com"])],
    },
    {
        "name": "word_wrap",
        "signature": "def word_wrap(text: str, width: int) -> list[str]",
        "spec": "Greedy word wrap: return lines no longer than width, breaking at spaces.",
        "public": [("'the quick brown fox', 10", ["the quick", "brown fox"])],
        "hidden": [("'a  b   c', 3", ["a b", "c"]), ("'supercalifragilistic', 5", ["super", "calif", "ragil", "istic"]),
                   ("'', 4", []), ("'one two', 3", ["one", "two"])],
    },
    {
        "name": "next_version",
        "signature": "def next_version(v: str, part: str) -> str",
        "spec": "Bump a semantic version 'MAJOR.MINOR.PATCH' by part in {'major','minor','patch'}.",
        "public": [("'1.2.3', 'minor'", "1.3.0")],
        "hidden": [("'1.2.3', 'major'", "2.0.0"), ("'1.2.3-beta.1', 'patch'", "1.2.4"),
                   ("'v1.2.3', 'patch'", "v1.2.4")],
    },
]


def _load_override():
    """LIVE_TASKS=<模块名> 指向你自己的题库（同结构）。找不到就大声报错，不静默退回默认题。"""
    mod = os.environ.get("LIVE_TASKS")
    if not mod:
        return None
    import importlib
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        m = importlib.import_module(mod)
    except ModuleNotFoundError as e:
        raise SystemExit(f"LIVE_TASKS={mod} 找不到这个模块（{e}）。写法是 Python 模块路径，"
                         f"比如文件 mine/tasks.py 就写 LIVE_TASKS=mine.tasks（要有 mine/__init__.py 或同目录可导入）") from None
    tasks = getattr(m, "TASKS", None)
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit(f"LIVE_TASKS={mod} 里没有非空的 TASKS 列表")
    need = {"name", "signature", "spec", "public", "hidden"}
    for i, t in enumerate(tasks):
        missing = need - set(t or {})
        if missing:
            raise SystemExit(f"LIVE_TASKS={mod} 第 {i + 1} 题缺字段：{sorted(missing)}")
        if not t["hidden"]:
            raise SystemExit(f"LIVE_TASKS={mod} 的 {t['name']} 没有隐藏用例 —— 那就没有尺子了")
    return tasks


BUILTIN_TASKS = TASKS          # 内置的那六道（LIVE_TASKS 覆盖之前），lab 的自检拿它比对重名
_override = _load_override()
if _override:
    TASKS = _override


def public_examples(task) -> str:
    return "\n".join(f"  {task['name']}({args}) == {expected!r}" for args, expected in task["public"])


def all_cases(task):
    return [(a, e, "public") for a, e in task["public"]] + [(a, e, "hidden") for a, e in task["hidden"]]


def public_cases(task):
    return [(a, e, "public") for a, e in task["public"]]


def hidden_cases(task):
    return [(a, e, "hidden") for a, e in task["hidden"]]


def probe_args(task):
    """只给输入不给期望 —— 多数表决 / 行为聚类用（c1）：谁也不该看到答案。"""
    return [a for a, _ in task["public"] + task["hidden"]]


def by_name(name: str):
    return next(t for t in TASKS if t["name"] == name)

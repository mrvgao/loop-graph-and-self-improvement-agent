"""六个「规则写全了、但一次做对不容易」的编程题 —— c1（best-of-N）和 c2（树搜索）的尺子。

和 live/tasks.py 的区别：那六题 spec 故意**不全**，隐藏用例是没写下来的边界规则 —— 所以多采样没用，
只有「验证 → 把那句话喂回去」才有用（A 部分的课）。这六题相反：spec 把规则**全写了**，隐藏用例只是
规则之间的组合。

排练发现（0905，三轮）：LeetCode 味的题（文本对齐、通配符、数字转英文）和自编的小规则题（限流窗口、
时间片调度、贪吃蛇）sonnet / haiku 在温度 1.0 下 8 个样本**全对、行为完全一样** —— 短函数买不到 coverage。
只有「一段规格要写 60–150 行才能做对」的题（回溯正则引擎、带环检测的电子表格、RFC 4180 CSV、
semver 范围、大数十进制字符串）才让 pass@1 掉到 1 以下。所以题在这个尺度；有的题禁用现成库
（`forbid`），否则 `re.search` 一行就完了。期望值全部来自**库或参考实现**（`python3 live/tasks_hard.py` 自测）。
"""
from __future__ import annotations

TASKS = [
    {
        "name": "mini_regex",
        "signature": "def mini_regex(pattern: str, text: str) -> bool",
        "spec": "A small regex engine with SEARCH semantics: return True if the pattern matches anywhere in text (use ^ and $ to "
                "anchor). Support: literal characters; '.' (any single character); character classes [abc], ranges [a-z], negated "
                "classes [^a-c]; the quantifiers '*', '+', '?' applied to a single character, a class, or a parenthesised group; "
                "grouping with ( ); alternation '|' with the lowest precedence (also inside groups); anchors ^ and $. Quantifiers "
                "are greedy but must backtrack, e.g. 'a?a?a?aaa' matches 'aaa' and '(x+x+)+y' matches 'xxxxxxy'. "
                "You must implement the matching yourself: the `re` module is forbidden.",
        "forbid": [r"^\s*(import|from)\s+re\b", r"\bre\.(search|match|fullmatch|compile|finditer|findall)\("],
        "public": [("'a*b', 'xaaab'", True), ("'^(ab|cd)+$', 'abcdab'", True), ("'x+', 'yyy'", False)],
        "hidden": [("'[^a-c]x', 'dx'", True), ("'colou?r', 'color'", True), ("'^$', ''", True), ("'a.c', 'abc'", True),
                   ("'(a|b)*c$', 'xababc'", True), ("'^a[0-9]+[.][0-9]+$', 'a1.25'", True), ("'^(a|ab)(c|bcd)$', 'abcd'", True),
                   ("'b+$', 'abbba'", False), ("'^[A-Z][a-z]*( [A-Z][a-z]*)*$', 'Hello World'", True), ("'a?a?a?aaa', 'aaa'", True),
                   ("'(x+x+)+y', 'xxxxxxy'", True)],
    },
    {
        "name": "spreadsheet",
        "signature": "def spreadsheet(cells: dict[str, str]) -> dict[str, float | str]",
        "spec": "Evaluate a tiny spreadsheet. Keys are cell names like 'A1'..'Z99'; values are either a number written as a string "
                "('5', '2.5') or a formula starting with '=' using + - * /, parentheses, unary minus, cell references and "
                "SUM(X1:Y2) over a rectangular range (columns X..Y, rows 1..2). A missing cell or an empty string is 0. Return a dict "
                "mapping every key in the input to its value as a float, except: a cell that is part of a reference cycle, or that "
                "depends (directly or indirectly) on one, is the string '#CYCLE!'; a division by zero makes the cell '#DIV/0!', and "
                "any cell depending on a '#DIV/0!' cell is also '#DIV/0!'. Usual operator precedence.",
        "public": [("{'A1': '1', 'A2': '=A1+1', 'A3': '=A1*A2'}", {"A1": 1.0, "A2": 2.0, "A3": 2.0}),
                   ("{'A1': '=B1', 'B1': '=A1', 'C1': '=A1+1', 'D1': '5'}", {"A1": "#CYCLE!", "B1": "#CYCLE!", "C1": "#CYCLE!", "D1": 5.0})],
        "hidden": [("{'A1': '4', 'A2': '=A1/0', 'A3': '=A2+1'}", {"A1": 4.0, "A2": "#DIV/0!", "A3": "#DIV/0!"}),
                   ("{'A1': '1', 'B1': '2', 'A2': '3', 'B2': '4', 'C3': '=SUM(A1:B2)*2'}", {"A1": 1.0, "B1": 2.0, "A2": 3.0, "B2": 4.0, "C3": 20.0}),
                   ("{'A1': '=2+3*4', 'A2': '=(2+3)*4', 'A3': '=-A1+Z9'}", {"A1": 14.0, "A2": 20.0, "A3": -14.0}),
                   ("{'A1': '=A1'}", {"A1": "#CYCLE!"}),
                   ("{'A1': '=SUM(A2:A4)', 'A2': '1', 'A3': '', 'A4': '=A2*10'}", {"A1": 11.0, "A2": 1.0, "A3": 0.0, "A4": 10.0}),
                   ("{'A1': '10', 'A2': '=A1/4', 'A3': '=A2-2.5'}", {"A1": 10.0, "A2": 2.5, "A3": 0.0}),
                   ("{'A1': '=B1+1', 'B1': '=C1+1', 'C1': '=D1', 'D1': '=B1'}", {"A1": "#CYCLE!", "B1": "#CYCLE!", "C1": "#CYCLE!", "D1": "#CYCLE!"})],
    },
    {
        "name": "csv_parse",
        "signature": "def csv_parse(text: str) -> list[list[str]]",
        "spec": "Parse CSV text (RFC 4180 rules) into a list of records, each a list of strings. Fields are separated by commas. "
                "A field may be enclosed in double quotes; inside quotes, commas and newlines are literal and a doubled quote \"\" "
                "stands for one quote character. Records are separated by '\\n' or '\\r\\n'. A trailing newline at the end of the "
                "text does NOT produce an extra record; an empty text gives []. Unquoted fields are kept verbatim (no trimming). "
                "The `csv` module is forbidden.",
        "forbid": [r"^\s*(import|from)\s+csv\b"],
        "public": [("'a,b\\nc,d\\n'", [["a", "b"], ["c", "d"]]), ("'\"a,b\",c'", [["a,b", "c"]])],
        "hidden": [("'x,\"he said \"\"hi\"\"\",z'", [["x", "he said \"hi\"", "z"]]),
                   ("'\"multi\\nline\",1\\r\\n2,3'", [["multi\nline", "1"], ["2", "3"]]),
                   ("''", []), ("'a,,b\\r\\nc'", [["a", "", "b"], ["c"]]), ("' a , b '", [[" a ", " b "]]), ("'\"\",\"\"'", [["", ""]]),
                   ("'\"x\"\"\",\"\"\"y\"\\n'", [["x\"", "\"y"]])],
    },
    {
        "name": "semver_satisfies",
        "signature": "def semver_satisfies(version: str, range_: str) -> bool",
        "spec": "version is 'MAJOR.MINOR.PATCH'. range_ is one or more alternatives separated by '||' (OR); each alternative is a "
                "whitespace-separated list of comparators that must ALL hold. Comparator forms: '>=V', '>V', '<=V', '<V', '=V' and "
                "a bare full version 'V' (exact) where V is a full version; '^V' = >=V and below the next major (if MAJOR is 0: "
                "below the next minor; if MAJOR and MINOR are both 0: below the next patch); '~V' = >=V and below the next minor "
                "('~1' means >=1.0.0 <2.0.0); x-ranges '1.x', '1.2.x', and partial versions '1', '1.2' mean the corresponding "
                "range (>=1.0.0 <2.0.0, >=1.2.0 <1.3.0); '*' matches everything. Versions compare numerically component by component.",
        "public": [("'1.2.3', '^1.2.0'", True), ("'2.0.0', '^1.2.0'", False), ("'1.2.7', '1.2'", True)],
        "hidden": [("'0.2.9', '^0.2.3'", True), ("'0.3.0', '^0.2.3'", False), ("'0.0.3', '^0.0.3'", True), ("'0.0.4', '^0.0.3'", False),
                   ("'1.2.9', '~1.2.3'", True), ("'1.3.0', '~1.2.3'", False), ("'1.5.0', '1.x'", True), ("'1.3.0', '1.2'", False),
                   ("'3.0.0', '*'", True), ("'1.2.3', '>=1.0.0 <2.0.0 || >=3.0.0'", True), ("'3.1.0', '>=1.0.0 <2.0.0 || >=3.0.0'", True),
                   ("'2.5.0', '>=1.0.0 <2.0.0 || >=3.0.0'", False), ("'1.2.3', '1.2.3'", True), ("'1.2.4', '=1.2.3'", False),
                   ("'1.2.3', '>1.2.3'", False), ("'1.2.3', '>=1.2.3 <=1.2.3'", True), ("'1.0.0', '~1'", True), ("'2.0.0', '~1'", False)],
    },
    {
        "name": "decimal_add",
        "signature": "def decimal_add(a: str, b: str) -> str",
        "spec": "Add two signed decimal numbers given as strings of arbitrary length (optional leading '-', digits, optional '.' and "
                "fraction digits) and return the exact sum as a string in canonical form: no leading zeros before the point (a lone "
                "'0' stays), no trailing zeros after the point, no trailing '.', and negative zero is written '0'. "
                "Exact arithmetic is required: floats and the `decimal` / `fractions` modules are forbidden.",
        "forbid": [r"^\s*(import|from)\s+(decimal|fractions)\b", r"\bfloat\("],
        "public": [("'1.5', '2.25'", "3.75"), ("'-12.50', '3.005'", "-9.495")],
        "hidden": [("'0.1', '0.2'", "0.3"), ("'-0.0', '0'", "0"), ("'999999999999999999', '1'", "1000000000000000000"), ("'-5', '5'", "0"),
                   ("'1.10', '-1.1'", "0"), ("'123.456', '-0.456'", "123"), ("'-0.5', '-0.5'", "-1")],
    },
    {
        "name": "topo_order",
        "signature": "def topo_order(deps: dict[str, list[str]]) -> list[str] | str",
        "spec": "deps maps a node to the list of nodes it depends on (which must come before it). Nodes that only appear inside a "
                "dependency list are nodes too. Return a topological order; whenever several nodes are available, take the "
                "alphabetically smallest first (so the answer is unique). If the graph has a cycle, return the string 'cycle'.",
        "public": [("{'b': ['a'], 'c': ['b']}", ["a", "b", "c"]), ("{'a': ['b'], 'b': ['a']}", "cycle")],
        "hidden": [("{'x': [], 'y': [], 'z': ['y', 'x']}", ["x", "y", "z"]), ("{}", []),
                   ("{'app': ['lib', 'log'], 'lib': ['log'], 'log': []}", ["log", "lib", "app"]), ("{'a': ['a']}", "cycle"),
                   ("{'d': ['c'], 'c': ['b'], 'b': ['a'], 'e': []}", ["a", "b", "c", "d", "e"])],
    },
]


def public_examples(task) -> str:
    return "\n".join(f"  {task['name']}({args}) == {expected!r}" for args, expected in task["public"])


def public_cases(task):
    return [(a, e, "public") for a, e in task["public"]]


def hidden_cases(task):
    return [(a, e, "hidden") for a, e in task["hidden"]]


def probe_args(task):
    """只给输入不给期望 —— 多数表决 / 行为聚类用（c1）：谁也不该看到答案。"""
    return [a for a, _ in task["public"] + task["hidden"]]


def forbidden(task, code: str) -> str | None:
    """代码用了题目禁用的库？返回那条规则（尺子的一部分：mini_regex 允许 import re 就没题了）。"""
    import re
    for pat in task.get("forbid", []):
        if re.search(pat, code, re.M):
            return pat
    return None


# ── 参考实现：只用于自测（模型看不到这个文件；它用的正是被禁的库 —— 所以它是可信的尺子） ──
REFERENCE = r"""
import re, csv, io, heapq, json
from decimal import Decimal

def mini_regex(pattern, text):
    return re.search(pattern, text) is not None

def csv_parse(text):
    return [row for row in csv.reader(text.splitlines(keepends=True))]

def decimal_add(a, b):
    s = format(Decimal(a) + Decimal(b), "f")
    if "." in s: s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s

def topo_order(deps):
    nodes = set(deps) | {d for v in deps.values() for d in v}
    indeg = {n: 0 for n in nodes}; out = {n: [] for n in nodes}
    for n, ds in deps.items():
        for d in ds: indeg[n] += 1; out[d].append(n)
    heap = [n for n in nodes if indeg[n] == 0]; heapq.heapify(heap); order = []
    while heap:
        n = heapq.heappop(heap); order.append(n)
        for m in out[n]:
            indeg[m] -= 1
            if indeg[m] == 0: heapq.heappush(heap, m)
    return order if len(order) == len(nodes) else "cycle"

def _v(s):
    p = s.split("."); return tuple(int(x) for x in p)
def _cmp(op, v, w):
    return {"<": v < w, "<=": v <= w, ">": v > w, ">=": v >= w, "=": v == w}[op]
def _comparator(c, v):
    if c in ("*", "x", "X", ""): return True
    m = re.match(r"^(>=|<=|>|<|=|\^|~)?(.*)$", c); op, ver = m.group(1) or "", m.group(2)
    parts = ver.split(".")
    parts = [p for p in parts if p not in ("x", "X", "*")]
    nums = [int(p) for p in parts]
    if op in (">=", "<=", ">", "<", "="):
        return _cmp(op, v, tuple(nums))
    lo = tuple(nums + [0] * (3 - len(nums)))
    if op == "^":
        if nums[0] != 0 or len(nums) == 1: hi = (nums[0] + 1, 0, 0)
        elif nums[1] != 0 or len(nums) == 2: hi = (0, nums[1] + 1, 0)
        else: hi = (0, 0, nums[2] + 1)
    elif op == "~":
        hi = (nums[0] + 1, 0, 0) if len(nums) == 1 else (nums[0], nums[1] + 1, 0)
    else:  # x-range / partial / exact
        if len(nums) == 3: return v == lo
        hi = (nums[0] + 1, 0, 0) if len(nums) == 1 else (nums[0], nums[1] + 1, 0)
    return lo <= v < hi
def semver_satisfies(version, rng):
    v = _v(version)
    return any(all(_comparator(c, v) for c in alt.split()) for alt in rng.split("||"))

# spreadsheet
CYC, DIV = "#CYCLE!", "#DIV/0!"
def spreadsheet(cells):
    memo = {}; state = {}
    def tokenize(s):
        return re.findall(r"[A-Z]+[0-9]+:[A-Z]+[0-9]+|[A-Z]+[0-9]+|SUM|\d+\.?\d*|[-+*/()]", s)
    def refs_in_range(r):
        a, b = r.split(":"); ca, ra = re.match(r"([A-Z]+)(\d+)", a).groups(); cb, rb = re.match(r"([A-Z]+)(\d+)", b).groups()
        return [f"{chr(c)}{rr}" for c in range(ord(ca), ord(cb) + 1) for rr in range(int(ra), int(rb) + 1)]
    class Err(Exception):
        def __init__(self, kind): self.kind = kind
    def value(ref):
        if ref in memo:
            if memo[ref] in (CYC, DIV): raise Err(memo[ref])
            return memo[ref]
        if state.get(ref) == "visiting": raise Err(CYC)
        raw = cells.get(ref, "")
        state[ref] = "visiting"
        try:
            if raw == "": val = 0.0
            elif raw.startswith("="): val = evaluate(raw[1:])
            else: val = float(raw)
        except Err as e:
            memo[ref] = e.kind; state[ref] = "done"; raise
        memo[ref] = val; state[ref] = "done"; return val
    def evaluate(expr):
        toks = tokenize(expr); pos = [0]
        def peek(): return toks[pos[0]] if pos[0] < len(toks) else None
        def take(): t = toks[pos[0]]; pos[0] += 1; return t
        def atom():
            t = take()
            if t == "(": v = add(); take(); return v
            if t == "-": return -atom()
            if t == "SUM": take(); r = take(); take(); return sum(value(x) for x in refs_in_range(r))
            if re.match(r"[A-Z]+\d+$", t): return value(t)
            return float(t)
        def mul():
            v = atom()
            while peek() in ("*", "/"):
                op = take(); w = atom()
                if op == "*": v *= w
                else:
                    if w == 0: raise Err(DIV)
                    v /= w
            return v
        def add():
            v = mul()
            while peek() in ("+", "-"):
                op = take(); w = mul(); v = v + w if op == "+" else v - w
            return v
        return add()
    out = {}
    for ref in cells:
        try: out[ref] = value(ref)
        except Err as e: out[ref] = e.kind
    return out
"""

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from live.verify import verify_detail
    bad = 0
    for t in TASKS:
        t2 = {**t, "forbid": []}                     # 参考实现故意用被禁的库
        d = verify_detail(t2, REFERENCE, public_cases(t) + hidden_cases(t))
        print(f"{t['name']:16} {d['passed']}/{d['total']}  {'' if d['ok'] else d['feedback']}")
        bad += not d["ok"]
    sys.exit(1 if bad else 0)

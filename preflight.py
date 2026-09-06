#!/usr/bin/env python3
"""环境自检 —— 跑任何演示之前先跑这个：python3 preflight.py

逐项检查，任何一项红都会告诉你缺什么、怎么补。全绿之后 `python3 a1_verify_retry.py` 就该看到看板。
这个脚本只读不写，不会花掉超过两次最小调用的钱（约 $0.0001）。
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OK, BAD, WARN = "✅", "❌", "⚠️ "
fails = 0


def check(name: str, ok: bool, fix: str = "", *, warn_only: bool = False) -> bool:
    global fails
    mark = OK if ok else (WARN if warn_only else BAD)
    print(f"{mark} {name}")
    if not ok:
        if fix:
            for line in fix.splitlines():
                print(f"      {line}")
        if not warn_only:
            fails += 1
    return ok


print("── 环境 ──────────────────────────────────────────────")
v = sys.version_info
check(f"Python {v.major}.{v.minor}.{v.micro}（需要 ≥ 3.10）", v >= (3, 10),
      "macOS: brew install python@3.12\nWindows: python.org 下载 3.12，安装时勾 Add to PATH")

check("纯标准库（不需要 pip install 任何东西）", True)

port_free = True
try:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 8642))
except OSError:
    port_free = False
check("看板端口 8642 空闲", port_free,
      "被占了不影响：脚本会自动往后找端口，看终端打印的看板地址。\n"
      "想腾出来：lsof -i :8642（macOS）/ netstat -ano | findstr 8642（Windows）", warn_only=True)

print("\n── 凭据与网关 ────────────────────────────────────────")
try:
    from live import llm
except Exception as e:                                          # noqa: BLE001
    print(f"{BAD} 无法 import live/llm.py：{type(e).__name__}: {e}")
    print("      确认你在这个目录下运行：python3 preflight.py")
    sys.exit(1)

has_key = bool(llm.KEY)
check("LLM 凭据已就绪", has_key,
      "三种来源，任选一种（llm.py 按这个顺序找）：\n"
      "  1. 课程网关：PARALLIGHT_BASE_URL + PARALLIGHT_API_KEY（plk_ 开头）\n"
      "  2. lab 注入：ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN（/lab-start 会 export）\n"
      "  3. 自己的 key：ANTHROPIC_API_KEY\n"
      "把它们写进这个目录下的 .env（照 .env.example 抄），或者 export 到 shell 里")

print(f"      端点  {llm.BASE}" + (f"（来自 {llm.BASE_SOURCE}）" if llm.BASE_SOURCE else "（默认，没设过任何 BASE 变量）"))
print(f"      key   来自 {llm.KEY_SOURCE}，{llm.KEY[:8]}…（{len(llm.KEY)} 字符）")
if llm.KEY_ALSO_SET:
    check(f"环境里有不止一个 key：{llm.KEY_SOURCE} 胜出，{'、'.join(llm.KEY_ALSO_SET)} 被忽略", False, warn_only=True, fix=(
        "如果 401 说 key 不对，多半就是这条：优先级是 PARALLIGHT_API_KEY > ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN，\n"
        "所以 ~/.zshrc 里一个残留的 ANTHROPIC_API_KEY 会压过刚给你的那个。\n"
        f"办法：unset {' '.join(llm.KEY_ALSO_SET + [llm.KEY_SOURCE])} 之后只 export 你要用的那一个，\n"
        "或者把要用的那个写进这个目录下的 .env（.env 里的值也遵守同一个优先级）。"))
print(f"      主模型 MODEL={llm.MODEL}")
print(f"      小模型 SMALL={llm.SMALL}")

env_file = HERE / ".env"
check(".env 存在（可选：也可以用 export）", env_file.exists(),
      "cp .env.example .env 然后填；或者直接 export 环境变量", warn_only=True)

if not has_key:
    print(f"\n{BAD} 没有凭据，后面的连通性检查跳过。")
    sys.exit(1)

print("\n── 连通性（各花一次最小调用）────────────────────────")


def ping(model: str, label: str) -> str | None:
    try:
        text = llm.chat("Reply with the single word: pong.",
                        [{"role": "user", "content": "ping"}], max_tokens=8, model=model, tag="preflight")
        return text.strip()
    except Exception as e:                                      # noqa: BLE001
        print(f"{BAD} {label}（{model}）：{type(e).__name__}: {str(e)[:200]}")
        return None


main_ok = ping(llm.MODEL, "主模型可用")
if main_ok is not None:
    check(f"主模型可用（{llm.MODEL} → {main_ok[:20]}）", True)
else:
    fails += 1
    print("      401/403 = key 不对或没权限；404 = 模型名这个网关不认；超时 = 网络或端点不对")
    print(f"      想看这个网关支持哪些模型名，问发你 key 的人，或试 curl {llm.BASE}/v1/models")

if llm.SMALL != llm.MODEL:
    small_ok = ping(llm.SMALL, "小模型可用")
    if small_ok is not None:
        check(f"小模型可用（{llm.SMALL} → {small_ok[:20]}）", True)
    else:
        fails += 1
        print(f"      SMALL_MODEL={llm.SMALL} 这个网关不认。要么改成它认的便宜模型，要么删掉这个变量")
        print("      （删掉 = 退回主模型，下面那条警告会告诉你代价）")
else:
    check(f"小模型和主模型是同一个（{llm.MODEL}）", False, warn_only=True, fix=(
        "不影响运行，但有四个演示的教学效果会打折，因为它们要的是「两个不同的模型」：\n"
        "  a0  判题的模型和做题的是同一个 —— 「做题的不给自己打分」这条演不出来\n"
        "  c1  采样的是强模型 → pass@k 大概率是一条平线（这本身也是结论，但看不到曲线）\n"
        "  c2  写码的是强模型 → 树可能只长三个节点\n"
        "  c10 学生一遍全对 → 没有提升空间，也就没东西可教\n"
        "补法：export SMALL_MODEL=<这个网关上更便宜/更弱的模型名>"))

print("\n── 尺子（不花钱）─────────────────────────────────────")
try:
    from live.tasks import TASKS
    from live.tasks_hard import TASKS as HARD, REFERENCE, public_cases, hidden_cases
    from live.verify import verify_detail
    check(f"题库可读（基础 {len(TASKS)} 题 · 难题 {len(HARD)} 题）", len(TASKS) >= 1 and len(HARD) >= 1)
    t = HARD[0]
    d = verify_detail({**t, "forbid": []}, REFERENCE, public_cases(t) + hidden_cases(t))
    check(f"验证器能在子进程里跑代码（参考实现 {d['passed']}/{d['total']}）", d["ok"],
          "子进程起不来或超时：确认 sys.executable 可用，杀毒软件没拦 python 子进程")
except Exception as e:                                          # noqa: BLE001
    check(f"尺子自检：{type(e).__name__}: {e}", False)

print()
if fails:
    print(f"{BAD} {fails} 项没过。修完再跑一次。")
    sys.exit(1)
print(f"{OK} 全绿。下一步：python3 a1_verify_retry.py（浏览器会自动打开看板）")

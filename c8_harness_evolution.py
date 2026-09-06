#!/usr/bin/env python3
"""C8 · 进化 harness —— RHI-lite：meta 模型改写流水线的 JSON 规格（角色 / contract / hop），只跟上一版比（阶梯第 4 档）。

这个文件证明什么
  一条两节点的 tinygraph 流水线（reader 读 changelog → writer 写发布说明）由一份 JSON 规格定义。种子规格故意写得松
  （「总结一下」「写个友好的说明」），像大多数人第一版 prompt 那样。
  五个**确定性缺陷探测器**在每次运行后打分：
    D1 items 是带 type/text/issue 的 JSON 数组   D2 changelog 里每个 issue id 都出现在 note 里   D3 ≤150 词
    D4 有 Added / Fixed 两个标题                  D5 note 里没有 changelog 里不存在的 issue id（幻觉）
  每代：跑 3 份输入 → 缺陷清单（一句一句，荒唐地具体）→ meta 模型拿到规格 + 缺陷 + 节点目录，改写规格
  ——可以改角色、改 contract、**加裁判节点**（validator:Dx，发现问题就打回指定节点，回环封顶 1 次）、加 hop。
  运行时校验规格（未知节点类型 / 悬空目标 / 没上限的环 → 拒绝信还给 meta），**严格更少缺陷才接受，只跟上一版比**。
  要看到的：RHI 论文附录 B 那条曲线在你眼前长出来 —— 先长出来的不是任务能力，是裁判。

看板上看哪几栏
  · 图：每代一张 tab（harness v0…v4）；新长出的 validator 节点紫色双边
  · Prompt/产物：harness.json 每代一版，diff 就是新节点/新 hop
  · 分数曲线：defects / referees / hops（x = 代）；验证：每代每输入每探测器一行

课上怎么跑
  python3 c8_harness_evolution.py           # 4 代（约 45–55 次调用）
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live import bus, llm                      # noqa: E402
from live.tinygraph import END, Graph          # noqa: E402

INPUTS = [
    "v2.3.1 changelog:\n- Added CSV export for reports (#4821)\n- Fixed timezone bug in scheduler (#4790)\n- Added dark mode toggle (#4802)\n- Fixed crash when notifications list is empty (#4811)",
    "v2.4.0 changelog:\n- Added webhook retries with backoff (#4850)\n- Fixed search ignoring accented characters (#4833)\n- Internal: refactored auth middleware (no user-facing change)\n- Added VAT number on invoices (#4861)",
    "v2.4.1 changelog:\n- Fixed duplicate charge on retry (#4877)\n- Added teammate invitations by email (#4869)\n- Fixed password reset emails landing in spam (#4880)\n- Note: ticket #4899 is still open, not shipped",
]
SEED_SPEC = {
    "entry": "reader",
    "nodes": [
        {"name": "reader", "kind": "llm", "role": "Summarize what changed in this release.", "contract": {"writes": "items"}, "next": "writer"},
        {"name": "writer", "kind": "llm", "role": "Write a friendly release note for users.", "contract": {"writes": "note"}, "next": "END"},
    ],
}
CATALOG = {
    "llm": "an LLM role: fields role (prompt), contract (what it writes: items or note), next",
    "validator:D1": "referee: checks items is a JSON array with keys type/text/issue; on failure writes feedback and routes to `on_fail` (cap 1 retry per run)",
    "validator:D2": "referee: every issue id in the changelog appears in note; on failure routes to `on_fail`",
    "validator:D3": "referee: note <= 150 words; on failure routes to `on_fail`",
    "validator:D4": "referee: note has headings Added and Fixed; on failure routes to `on_fail`",
    "validator:D5": "referee: note cites no issue id absent from the changelog (hallucination); on failure routes to `on_fail`",
}
IDS = lambda s: set(re.findall(r"#(\d{4})", s))


def detectors(changelog: str, state: dict) -> dict:
    note = state.get("note", "") or ""; items = state.get("items")
    d = {}
    try:
        arr = json.loads(items) if isinstance(items, str) else items
        d["D1"] = isinstance(arr, list) and all(isinstance(x, dict) and {"type", "text", "issue"} <= set(x) for x in arr)
    except Exception:
        d["D1"] = False
    shipped = IDS(changelog) - {i for i in IDS(changelog) if "still open" in changelog.split("#" + i)[0][-80:] or "not shipped" in changelog.split("#" + i)[1][:40]}
    d["D2"] = shipped <= IDS(note)
    d["D3"] = len(re.findall(r"[A-Za-z0-9'#-]+", note)) <= 150
    d["D4"] = "added" in note.lower() and "fixed" in note.lower()
    d["D5"] = IDS(note) <= IDS(changelog)
    return d


def build(run: bus.Run, spec: dict, changelog: str, gname: str) -> Graph:
    nodes = {n["name"]: n for n in spec["nodes"]}
    for n in spec["nodes"]:
        if n["kind"] not in CATALOG: raise ValueError(f"unknown node kind {n['kind']}")
        tgt = n.get("on_fail") if n["kind"].startswith("validator") else n.get("next")
        if n["kind"].startswith("validator") and (n.get("next") is None or n.get("on_fail") is None): raise ValueError(f"validator {n['name']} needs next and on_fail")
        for t in (n.get("next"), n.get("on_fail")):
            if t not in (None, "END") and t not in nodes: raise ValueError(f"{n['name']} points at unknown node {t}")
    if spec["entry"] not in nodes: raise ValueError("entry is not a node")
    g = Graph(gname)
    for n in spec["nodes"]:
        name = n["name"]
        if n["kind"] == "llm":
            def mk(n=n):
                def node(state: dict) -> dict:
                    ctx = f"Changelog:\n{changelog}\n"
                    if state.get("items"): ctx += f"\nExtracted items:\n{state['items']}\n"
                    if state.get("feedback"): ctx += f"\nA referee rejected the previous output: {state['feedback']}\nFix it.\n"
                    out = llm.chat(n["role"] + " Reply with the output only.", [{"role": "user", "content": ctx}], max_tokens=500, tag=n["name"]).strip()
                    return {n.get("contract", {}).get("writes", "note"): llm.extract_code(out) if "```" in out else out, "feedback": None}
                return node
            g.add_node(name, mk(), kind="llm"); g.add_edge(name, END if n["next"] == "END" else n["next"])
        else:
            dkey = n["kind"].split(":")[1]
            def mkv(dkey=dkey, n=n):
                def node(state: dict) -> dict:
                    ok = detectors(changelog, state)[dkey]
                    state.setdefault("__retries__", {})
                    return {"ok": ok, "feedback": None if ok else f"{dkey} failed: {CATALOG[n['kind']].split(':',1)[1].split(';')[0].strip()}"}
                return node
            g.add_node(name, mkv(), kind="validator")
            def router(state: dict, name=name) -> str:
                if state.get("ok"): return "pass"
                r = state.setdefault("__retries__", {}); r[name] = r.get(name, 0) + 1
                return "fail" if r[name] <= 1 else "cap"                    # 回环封顶 1 次：运行时的，不是 meta 的
            g.add_conditional_edges(name, router, {"pass": END if n["next"] == "END" else n["next"], "fail": n["on_fail"], "cap": END if n["next"] == "END" else n["next"]})
    g.set_entry(spec["entry"])
    return g


def run_spec(run: bus.Run, spec: dict, gen: int, inputs: list) -> tuple[int, list[str]]:
    def one(i_cl):
        i, cl = i_cl
        g = build(run, spec, cl, f"harness v{gen}")
        st = g.compile(max_steps=12).invoke({}, thread_id=f"g{gen}i{i}", log=lambda m: None)
        d = detectors(cl, st)
        for k, ok in d.items():
            run.verify(f"{k}@input{i}", ok, "ok" if ok else CATALOG["validator:" + k].split(":", 1)[1].split(";")[0].strip(), kind="validator", gen=gen)
        return [f"input {i}: {k} failed ({CATALOG['validator:' + k].split(':',1)[1].split(';')[0].strip()})" for k, ok in d.items() if not ok]
    with ThreadPoolExecutor(len(inputs)) as ex:
        per = list(ex.map(one, list(enumerate(inputs, 1))))
    defects = [x for lst in per for x in lst]
    return len(defects), defects


def meta_edit(spec: dict, defects: list[str], rejection: str | None) -> dict:
    sysmsg = ("You evolve the HARNESS of a two-role pipeline: a JSON spec of nodes (roles, contracts, hops). Given the observed defects, "
              "edit the spec so the defects stop: sharpen roles/contracts, and/or ADD referee nodes from the catalog (kind validator:Dx with "
              "`next` and `on_fail`) so failures are caught and routed back. Keep node names lowercase. Reply with the full spec as JSON only.")
    user = f"Current spec:\n{json.dumps(spec, indent=1)}\n\nCatalog:\n{json.dumps(CATALOG, indent=1)}\n\nDefects observed on the last run:\n- " + ("\n- ".join(defects) or "(none)")
    if rejection:
        user += f"\n\nYour previous spec was REJECTED by the runtime: {rejection}. Fix it."
    raw = llm.chat(sysmsg, [{"role": "user", "content": user}], max_tokens=1200, temperature=0.4, tag="meta")
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def main() -> None:
    ap = bus.argparser("C8 · harness 进化（RHI-lite）")
    ap.add_argument("--gens", type=int, default=4)
    args = ap.parse_args()
    inputs, gens = (INPUTS[2:], 2) if args.fast else (INPUTS, args.gens)     # fast 用带幻觉陷阱的那份输入
    with bus.start("c8_harness_evolution", "C8 · harness 进化：裁判长出来", args, budget={"usd": 1.0}) as run:
        spec = SEED_SPEC
        run.iter("gen", 0, gens, label="种子 harness：reader → writer")
        n_def, defects = run_spec(run, spec, 0, inputs)
        stat = lambda s: (sum(n["kind"].startswith("validator") for n in s["nodes"]), sum(1 for n in s["nodes"] for k in ("next", "on_fail") if n.get(k) and n[k] != "END"))
        run.artifact("harness.json", json.dumps(spec, indent=1), lang="json", note=f"v0 · 缺陷 {n_def}", accepted=True)
        refs, hops = stat(spec); run.score("defects", 0, n_def); run.score("referees", 0, refs, group="structure"); run.score("hops", 0, hops, group="structure")
        run.say(f"v0：缺陷 {n_def}（裁判 {refs}，hop {hops}）")
        for d in defects: run.say("   · " + d)
        for g in range(1, gens + 1):
            run.iter("gen", g, gens, label="meta 改 harness → 跑 → 只跟上一版比")
            cand, rejection = None, None
            for attempt in range(3):
                try:
                    cand = meta_edit(spec, defects, rejection)
                    build(run, cand, INPUTS[0], "probe")           # 运行时校验（不跑）
                    break
                except (ValueError, KeyError, json.JSONDecodeError) as e:
                    rejection = str(e); run.say(f"  运行时拒绝规格：{rejection}"); cand = None
            if cand is None:
                run.say("  meta 三次都没交出合法规格"); continue
            c_def, c_defects = run_spec(run, cand, g, inputs)
            accepted = c_def < n_def                                     # 严格更少缺陷；只跟上一版比
            refs, hops = stat(cand)
            run.artifact("harness.json", json.dumps(cand, indent=1), lang="json", note=f"v{g} · 缺陷 {c_def}（上一版 {n_def}）· 裁判 {refs} · hop {hops} · {'接受' if accepted else '拒绝'}", accepted=accepted)
            run.score("defects", g, c_def); run.score("referees", g, refs, group="structure"); run.score("hops", g, hops, group="structure")
            run.say(f"v{g}：缺陷 {c_def} · 裁判 {refs} · hop {hops} · {'ACCEPT' if accepted else 'reject'}")
            for d in c_defects: run.say("   · " + d)
            if accepted:
                spec, n_def, defects = cand, c_def, c_defects
        refs, hops = stat(spec)
        run.say(f"\n最终：缺陷 {n_def} · 裁判 {refs} · hop {hops}。看 harness.json 的 diff：先长出来的是裁判，不是任务能力。")
        run.stop("done", ok=True, defects=n_def, referees=refs, hops=hops)


if __name__ == "__main__":
    main()

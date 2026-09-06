#!/usr/bin/env python3
"""校验一份 runs/<demo>/<id>.jsonl：kind 闭集、seq 递增、首 run.start 末 stop、call/done 配对、成本、版本号连续、边引用合法、账单一致。
    python3 tools/check_log.py runs/a1_verify_retry/<id>.jsonl [--no-stop]
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live.bus import KINDS

def check(path: Path, expect_stop=True) -> list[str]:
    evs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    bad = []
    if not evs: return ["empty log"]
    if evs[0]["kind"] != "run.start": bad.append("first event is not run.start")
    if expect_stop and evs[-1]["kind"] != "stop": bad.append(f"last event is {evs[-1]['kind']}, not stop")
    for i, e in enumerate(evs, 1):
        if e["kind"] not in KINDS: bad.append(f"seq {e.get('seq')}: unknown kind {e['kind']}")
        if e["seq"] != i: bad.append(f"seq gap at {i}: got {e['seq']}"); break
    calls = [e for e in evs if e["kind"] == "llm.call"]; dones = [e for e in evs if e["kind"] == "llm.done"]
    if len(calls) != len(dones): bad.append(f"llm.call {len(calls)} != llm.done {len(dones)}")
    for d in dones:
        if d["in"] + d["out"] > 0 and not d["cost"] > 0: bad.append(f"{d['id']}: cost not > 0")
    vers = {}
    for e in evs:
        if e["kind"] in ("prompt.version", "artifact"):
            k = e["kind"] + ":" + e["name"]; vers.setdefault(k, 0)
            if e["v"] != vers[k] + 1: bad.append(f"{k}: version {e['v']} after {vers[k]}")
            vers[k] = e["v"]
    graphs = {}
    for e in evs:
        if e["kind"] == "graph.def":
            g = graphs.setdefault(e.get("graph", "graph"), set())
            if e.get("mode") != "merge": g.clear()
            g.update(n["id"] for n in e["nodes"]); g.add("END")
        if e["kind"] == "graph.edge":
            g = graphs.get(e.get("graph", "graph"), set())
            if e["from"] not in g or e["to"] not in g: bad.append(f"graph.edge {e['from']}→{e['to']} references unknown node")
    if expect_stop and dones:
        bill = evs[-1].get("bill", {}); last = dones[-1].get("cum", {})
        if bill.get("calls") != last.get("calls"): bad.append(f"bill.calls {bill.get('calls')} != cum.calls {last.get('calls')}")
    return bad

if __name__ == "__main__":
    p = Path(sys.argv[1]); bad = check(p, "--no-stop" not in sys.argv)
    evs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    stop = next((e for e in reversed(evs) if e["kind"] == "stop"), None)
    b = (stop or {}).get("bill", {})
    print(f"{p.parent.name:22} events={len(evs):4} calls={b.get('calls','?'):>3} in={b.get('in','?'):>6} out={b.get('out','?'):>6} ${b.get('cost',0):.3f} {b.get('sec_total','?')}s stop={(stop or {}).get('reason','-')}")
    for x in bad: print("  ❌", x)
    sys.exit(1 if bad else 0)

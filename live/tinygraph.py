"""tinygraph —— 150 行的状态图运行时，纯标准库。看板版：每个节点/边/检查点都发事件。

The point of writing it ourselves: a "graph engine" is not magic. It is
    nodes      = functions  state -> partial update
    edges      = which node runs next (fixed, or decided by a router function)
    state      = one dict that every node reads and writes
    checkpoint = the state + the name of the next node, saved after EVERY node
    interrupt  = "save and stop before this node"; resuming = "load and go on"
    fan-out    = run several nodes on the same state, merge their updates
Same vocabulary as LangGraph (add_node / add_edge / add_conditional_edges /
compile / invoke / interrupt_before / thread_id); see g3_langgraph_port.py.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import bus

END = "__end__"
START = "__start__"

NodeFn = Callable[[dict], dict]


class Replace(list):
    """Wrap a list to REPLACE an append-reducer key instead of appending to it."""


class Graph:
    def __init__(self, name: str):
        self.name = name
        self.nodes: dict[str, NodeFn] = {}
        self.edges: dict[str, str] = {}                              # a -> b
        self.routers: dict[str, tuple[Callable[[dict], str], dict[str, str]]] = {}
        self.fanouts: dict[str, tuple[list[str], str]] = {}          # a -> ([b1,b2], join)
        self.appenders: set[str] = set()                             # state keys merged by list-append
        self.kinds: dict[str, str] = {}
        self.entry: str | None = None

    # ---- building -------------------------------------------------------
    def add_node(self, name: str, fn: NodeFn, kind: str = "node") -> "Graph":
        """kind 只影响画法：node / llm / validator / router / gate / anchor。"""
        self.nodes[name] = fn
        self.kinds[name] = kind
        return self

    def add_edge(self, a: str, b: str) -> "Graph":
        self.edges[a] = b
        return self

    def add_conditional_edges(self, a: str, router: Callable[[dict], str], mapping: dict[str, str]) -> "Graph":
        self.routers[a] = (router, mapping)
        return self

    def add_fanout(self, a: str, branches: list[str], join: str) -> "Graph":
        """After `a`, run every branch on a copy of the state (in parallel), merge, then `join`."""
        self.fanouts[a] = (branches, join)
        return self

    def set_entry(self, name: str) -> "Graph":
        self.entry = name
        return self

    def append_key(self, *keys: str) -> "Graph":
        """Declare state keys whose updates are appended (reducer), not replaced."""
        self.appenders.update(keys)
        return self

    def compile(self, checkpoint_dir: str | Path | None = None, interrupt_before: list[str] = (),
                max_steps: int = 40, run=None) -> "Runner":
        assert self.entry, "set_entry() first"
        return Runner(self, Path(checkpoint_dir) if checkpoint_dir else None, list(interrupt_before), max_steps,
                      run if run is not None else bus.current())

    def spec(self, interrupt_before: list[str] = (), anchors: list[str] = ()) -> dict:
        """graph.def 事件的内容：节点（带 kind）、边（带 kind/label）、入口、中断点、锚点。"""
        E = lambda x: "END" if x == END else x
        nodes = [{"id": n, "kind": self.kinds.get(n, "node")} for n in self.nodes]
        edges = [{"from": a, "to": E(b), "kind": "edge"} for a, b in self.edges.items()]
        for a, (_, mapping) in self.routers.items():
            edges += [{"from": a, "to": E(b), "label": label, "kind": "cond"} for label, b in mapping.items()]
        for a, (branches, join) in self.fanouts.items():
            edges += [{"from": a, "to": b, "kind": "fanout"} for b in branches]
            edges += [{"from": b, "to": join, "kind": "join"} for b in branches]
        return {"graph": self.name, "entry": self.entry, "nodes": nodes, "edges": edges,
                "interrupt_before": list(interrupt_before), "anchors": list(anchors), "mode": "replace"}

    # ---- drawing --------------------------------------------------------
    def mermaid(self, path: list[str] | None = None) -> str:
        """Mermaid source; nodes on `path` are highlighted so the executed route is visible."""
        lines = ["flowchart TD", f"  {START}([start]) --> {self.entry}"]
        for a, b in self.edges.items():
            lines.append(f"  {a} --> {b if b != END else 'END([end])'}")
        for a, (_, mapping) in self.routers.items():
            for label, b in mapping.items():
                lines.append(f"  {a} -- {label} --> {b if b != END else 'END([end])'}")
        for a, (branches, join) in self.fanouts.items():
            for b in branches:
                lines.append(f"  {a} --> {b}")
                lines.append(f"  {b} --> {join}")
        seen: set[str] = set()
        for n in (path or []):
            for m in n.strip("[]").split("|"):          # fan-out steps are logged as "[b1|b2]"
                if m in self.nodes and m not in seen:
                    seen.add(m)
                    lines.append(f"  style {m} fill:#2b9d78,color:#fff")
        return "\n".join(lines)


class Runner:
    def __init__(self, g: Graph, ckpt_dir: Path | None, interrupt_before: list[str], max_steps: int, run=None):
        self.g, self.ckpt_dir, self.interrupt_before, self.max_steps = g, ckpt_dir, interrupt_before, max_steps
        self.run = run
        self.trace: list[dict] = []

    def _ev(self, kind: str, **kv) -> None:
        if self.run:
            for k in ("from", "to", "node", "next"):          # 页面里 END 就叫 END，不暴露内部的 __end__
                if kv.get(k) == END:
                    kv[k] = "END"
            self.run.emit(kind, graph=self.g.name, **kv)

    def _ckpt(self, thread_id: str) -> Path | None:
        return self.ckpt_dir / f"{self.g.name}-{thread_id}.json" if self.ckpt_dir else None

    def _save(self, thread_id: str, state: dict, next_node: str, status: str) -> None:
        p = self._ckpt(thread_id)
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": state, "next": next_node, "status": status,
                                   "trace": self.trace}, indent=1, ensure_ascii=False))
        os.replace(tmp, p)
        if self.run:
            self.run.checkpoint(p, status=status, next=next_node, step=len(self.trace))

    def _merge(self, state: dict, delta: dict) -> None:
        for k, v in (delta or {}).items():
            if k in self.g.appenders and not isinstance(v, Replace):
                state.setdefault(k, [])
                state[k] = state[k] + (v if isinstance(v, list) else [v])
            else:
                state[k] = v

    def _next(self, node: str, state: dict) -> str:
        if node in self.g.routers:
            router, mapping = self.g.routers[node]
            label = router(state)
            if label not in mapping:
                raise KeyError(f"router at {node} returned {label!r}; known: {list(mapping)}")
            return mapping[label]
        if node in self.g.fanouts:
            return "__fanout__:" + node
        return self.g.edges.get(node, END)

    def invoke(self, state: dict, thread_id: str = "t1", resume: bool = False, log=print) -> dict:
        """Run to END or to an interrupt. With resume=True, continue from the checkpoint."""
        node = self.g.entry
        p = self._ckpt(thread_id)
        resumed = False
        if resume and p and p.exists():
            saved = json.loads(p.read_text())
            state, node, self.trace = saved["state"], saved["next"], saved.get("trace", [])
            resumed = True
            log(f"↩ resumed thread {thread_id} at node {node!r} (status was {saved['status']})")
        elif p and p.exists():
            p.unlink()
        step = len(self.trace)
        self._ev("graph.def", **{k: v for k, v in self.g.spec(self.interrupt_before).items() if k != "graph"},
                 thread_id=thread_id, resumed=resumed, path=[t["node"] for t in self.trace])
        while node != END:
            if step >= self.max_steps:
                self._save(thread_id, state, node, "max_steps")
                self._ev("graph.node", node=node, phase="skip", step=step, note="max_steps")
                state["__status__"] = "max_steps"
                return state
            if node in self.interrupt_before and not state.get(f"__approved__{node}"):
                self._save(thread_id, state, node, "interrupted")
                self._ev("graph.node", node=node, phase="interrupt", step=step)
                log(f"⏸ interrupted before {node!r}; state saved → {p}")
                state["__status__"] = "interrupted"
                return state
            if node.startswith("__fanout__:"):
                src = node.split(":", 1)[1]
                branches, join = self.g.fanouts[src]
                t0 = time.time()
                self._ev("graph.node", node=f"[{'|'.join(branches)}]", phase="start", step=step + 1, parallel_group=branches)
                for b in branches:
                    self._ev("graph.edge", **{"from": src, "to": b})
                with ThreadPoolExecutor(max_workers=len(branches)) as ex:
                    results = list(ex.map(lambda b: (b, self.g.nodes[b](dict(state))), branches))
                for b, delta in results:
                    self._merge(state, delta)
                step += 1
                self.trace.append({"step": step, "node": f"[{'|'.join(branches)}]", "sec": round(time.time() - t0, 2)})
                self._ev("graph.node", node=f"[{'|'.join(branches)}]", phase="end", step=step, sec=round(time.time() - t0, 2), parallel_group=branches)
                log(f"  {step:2d}. {'+'.join(branches)}  (parallel, {time.time() - t0:.1f}s)")
                for b in branches:
                    self._ev("graph.edge", **{"from": b, "to": join})
                node = join
                self._save(thread_id, state, node, "running")
                continue
            t0 = time.time()
            self._ev("graph.node", node=node, phase="start", step=step + 1)
            if self.run:
                self.run.iter("node", step + 1, None, label=node)
            delta = self.g.nodes[node](state)
            self._merge(state, delta)
            step += 1
            self.trace.append({"step": step, "node": node, "sec": round(time.time() - t0, 2),
                               "wrote": sorted(delta or {})})
            self._ev("graph.node", node=node, phase="end", step=step, sec=round(time.time() - t0, 2), wrote=sorted(delta or {}))
            log(f"  {step:2d}. {node:12} wrote {sorted(delta or {})}  ({time.time() - t0:.1f}s)")
            prev = node
            node = self._next(node, state)
            if not node.startswith("__fanout__"):
                label = None
                if prev in self.g.routers:
                    label = self.g.routers[prev][0](state)
                self._ev("graph.edge", **{"from": prev, "to": node, "label": label})
            self._save(thread_id, state, node, "running" if node != END else "done")
            if node == END:
                self._ev("graph.node", node="END", phase="end", step=step)
        state["__status__"] = "done"
        return state

    def path(self) -> list[str]:
        return [t["node"] for t in self.trace]

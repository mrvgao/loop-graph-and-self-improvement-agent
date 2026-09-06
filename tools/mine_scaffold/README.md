# mine/ —— 你自己的东西放这里

内置的 17 个演示（`a0…c10`）一个字都不用改。你在这里写的东西复用同一套运行时：
同一个 `live/bus.py` 事件总线、同一个看板、同一个 `live/tinygraph.py`。

自检脚本按**固定的名字**找东西，写错了跑通了也判不过：

| 文件 | 对应 task | 硬性约定 |
|---|---|---|
| `tasks.py` | t2 | 模块里叫 `TASKS`，六道以上，每道 `name/signature/spec/public/hidden` |
| `batch.py` | t3 | `bus.start("mine_batch", …)` —— 日志要落在 `runs/mine_batch/` |
| `graph.py` | t4 | `bus.start("mine_graph", …)` —— 日志要落在 `runs/mine_graph/` |
| `RULER.md` | t6 | 两条曲线的结论 |
| `README.md` | t7 | 一页交付说明 |

`__init__.py` 别删，`LIVE_TASKS=mine.tasks` 靠它才导得进来。

```bash
python3 mine/tasks.py                              # 你的题库自测（先写个参考实现验期望值）
LIVE_TASKS=mine.tasks python3 a1_verify_retry.py   # 内置演示跑你的题
python3 tools/lab_check.py t2                      # 自检
```

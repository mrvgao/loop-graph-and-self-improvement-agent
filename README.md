# Loop, Graph and Self-Improvement Agent

Parallight「Agentic Engineering 2026」二期第四课的全部现场演示：**Loop Engineering · Graph Engineering · Self-Improving Agent Systems**。
讲义 PDF：<https://parallight.ai/lectures/c2-lecture-4.pdf>。

17 个源码文件，纯标准库（Python 3.10+），每个文件一运行就自动打开一个实时看板（`http://127.0.0.1:8642`）：循环走到哪、prompt 怎么变、每次调用发出去的上下文、token 与成本、验证结果、图的节点在走、分数曲线。全部真调用模型，没有回放。

```bash
cp .env.example .env      # 填课程网关的 plk_ key，或自己的 ANTHROPIC_API_KEY
python3 live/llm.py       # 冒烟
python3 preflight.py       # 环境自检
python3 a0_self_driving.py --reset
```

想把它用到自己的任务上：写一份同结构的题库，然后 `LIVE_TASKS=mine.tasks python3 a1_verify_retry.py` ——
`a0 / a1 / a3 / b0 / c3 / c7` 六个演示会全部跑你的题，它们一个字都不用改。
`tools/mine_scaffold/` 是脚手架，`tools/lab_check.py` 是配套的结构级自检。

---


每个技术点一个源码文件。**运行它，浏览器自动打开一个实时看板**：循环走到哪、prompt 怎么变（并排 diff）、每次调用发出去的上下文、token 与成本、验证结果、图的节点在走、分数曲线。全部真调用课程网关，纯标准库，没有回放。

课上：VS Code 左边开源码讲，右边终端跑，投影切到看板。每个文件顶部一段「这个文件证明什么 · 看板上看哪几栏 · 课上怎么跑」——讲稿就在那里。

**先读 [GUIDE.md](GUIDE.md)**：17 张场景卡，每个演示解决什么问题、机制是什么、看板看哪一栏、排练打出来的数字、要讲的那句话。

## 先跑起来

```bash
cd course-content/c2-lecture-4/demos
cp .env.example .env            # PARALLIGHT_API_KEY=plk_…；MODEL=claude-sonnet-5（课上）/ claude-haiku-4-5（开发）
python3 preflight.py            # 环境自检：凭据来自哪个变量、网关通不通、两个模型都在不在、尺子能不能跑
python3 a1_verify_retry.py      # 看板 http://127.0.0.1:8642 自动打开；结束后页面保持，终端按 Enter 或页面点「结束本次运行」
```

**换成你自己的题**：写一个同结构的模块（`TASKS` 列表，每题 name/signature/spec/public/hidden），然后

```bash
LIVE_TASKS=mine.tasks python3 a1_verify_retry.py    # a0 / a1 / a3 / b0 / c3 / c7 全跟着变成你的题
```

**两个模型**：a0 判 done 的、c1 采样的、c2 写码的、c10 当学生的，要的是一个**更弱的**模型（`SMALL_MODEL`）。
没分开设的话 preflight 会告诉你哪四个演示的效果会打折。演示里不写死模型名，换 provider 也能跑。

所有演示共用的开关：`--no-browser`（不开浏览器、结尾不挂住，冒烟用）· `--hold` · `--port 8642` · `--fast`（缩小规模）· `--model X`（覆盖 MODEL）· `--budget-usd`（覆盖默认美元预算，超了 = `stop usd_budget`）。

## 演示表（排练实测 · 2026-09-05 · 课上设置 = `claude-sonnet-5`，另注者例外）

| 文件 | 证明什么 | 课上怎么跑 | 调用 | 用时 | $ | 排练结果 |
|---|---|---|---|---|---|---|
| `a0_self_driving.py` | **没有人写 prompt**：五行驱动词，每轮新进程、空上下文，agent 用 list/read/write/run_tests/git_log 自己组装上下文；另一模型判 done；四种停机 | `--reset` | 54 | 189 s | 0.62 | 6 轮到 goal，测试 0→5→9→16→20→25→29/29；每轮平均上下文 2.5k→4.1k tok |
| `a1_verify_retry.py` | 一次性调用就是 K=1 的循环；把「多试几次」和「看反馈」拆成两个变量 | 默认（A+B+C 三条臂） | 28 | 85 s | 0.06 | A 2/6 · B 2/6 · C 6/6；B 有 4 题停在 no-progress（温度 0.8 也写出字节相同的代码） |
| `a2_stop_resume.py` | 停机条件的顺序、预算在花钱之前查、原子检查点、幂等续跑、无进展规则 | `--reset --crash-after 5` → 默认 → `--reset --max-tokens 800` | 5+7+7 | 15+17+16 s | 0.02 | 崩溃后续跑 7 次调用零重复；800 tok 停在剩 5 条 |
| `a3_prompt_evolution.py` | 同一骨架套在 system prompt 上；public 涨 private 动不动 | 默认（3 代） | 27 | 94 s | 0.10 | 接受 1/3（每次结果不同，温度 0.7） |
| `b0_planned_graph.py` | **没有人画图**：planner 吐图规格；锚点与上限运行时锁死；拒绝信回环 | 默认（code + brief 两个目标） | 5 | 20 s | 0.02 | 两个目标都过锚点 |
| `b1_cycle_cap.py` | draft→critique→route 的三行；评委是噪声尺子；上限是环唯一的终止保证 | 默认（PASS=9） | 6 | 20 s | 0.02 | 评分 8,8,9 → 改两次 accept（`--pass 8` 一稿就过，环不转） |
| `b2_fanout_gate.py` | 扇出并行三审 + interrupt_before；进程退出；第二进程 `--approve` 零调用续跑 | 默认 → `--approve` | 4+0 | 15 s | 0.02 | 9 条 findings；续跑 0 次调用 |
| `c1_best_of_n.py` | pass@k（Chen 无偏估计）· 多数表决（行为聚类）· 验证器选 · oracle 上界 | 默认（**采样用 haiku**，6 题 × N=6） | 36 | 46 s | 0.17 | pass@1 0.47 → pass@6 1.00；first 2 / majority 3 / verifier 3 / oracle 6。sonnet 采样：0.97→1.00，全 6/6，$0.60 |
| `c2_tree_search.py` | ToT/LATS-lite：beam 2 深 2，fix / rethink 两算子，dev/holdout 分尺 | 默认（**写码用 haiku**） | 7 | 55 s | 0.15 | 根 2/18 → n1 14/18；四个孙子都没超过 n1。sonnet：根 5/18（import 了被禁的 re）→ 一步 18/18 |
| `c3_reflexion.py` | 试 → 对着宪法写反思 → 带记忆再试 | 默认（6 题 × 3 次） | 18 | 53 s | 0.04 | 解决 5/6 |
| `c4_ace_playbook.py` | ACE 增量 playbook vs 整段重写（路由用 haiku） | 默认（3 轮） | 145 | 279 s | 0.11 | ACE 6→7→15/17 · 重写 9→12→12/17；写出字符 ACE 3642 vs 重写 2665（这次 ACE 没省字，赢在可审计） |
| `c5_skill_library.py` | Voyager-lite：写→跑→读错→修→自验→入库→后续任务调用 | 默认 | 5 | 16 s | 0.01 | 5 个技能入库 |
| `c6_evolve_program.py` | AlphaEvolve-lite：进化 `pack()` 源码，确定性评估器，归档 + 排名加权亲本 | 默认（5 代 × 5） | 25 | 100 s | 0.52 | 1.3333 → 1.3539；25 次变异：非法 13、更差 10、更好 2 |
| `c7_self_modify.py` | DGM-lite：`solve()` 源码被 meta 模型改写，受限命名空间，严格更好才接受 | 默认（4 代） | 22 | 66 s | 0.12 | v0 1/6 → v1 5/6 → v2 6/6；v3/v4 语法错误被拒 |
| `c8_harness_evolution.py` | RHI-lite：JSON 规格的流水线，五个缺陷探测器，meta 改规格、加裁判节点 | 默认（4 代） | 34 | 57 s | 0.12 | v0 缺陷 8 → v1 缺陷 0（裁判 5、hop 11）ACCEPT |
| `c9_ruler.py` | 尺子研究：同一优化器两把尺，骗子 vs 泛化者（确定性） | 默认 | 0 | <1 s | 0 | LEAKY public 1.00 / private 0.60；PRIVATE 0.80 / 0.95 |
| `c10_star_bootstrap.py` | STaR：拒绝采样 → 合理化 → dataset.jsonl → few-shot 代替微调（**学生用 haiku**） | 默认（16 题 + 8 holdout） | 52 | 188 s | 0.19 | public 12/16 → 14/16 · holdout 6/8 → 7/8 · 数据集 14 条 |

一堂课全跑：约 **$2.6**、等待时间约 **20 分钟**（大头 a0 3 分钟、c4 5 分钟、c10 3 分钟）。预算默认：a0 2.5、c1 1.5、c7 1.5、c8 / c2 1.0，其余 0.75。

三个演示故意用 haiku 当被观察的模型（c1 采样、c2 写码、c10 学生）：sonnet-5 在这个尺度上一步全对，曲线是平的，没东西可看。这本身是 CS329A 的一课 —— test-time compute 买的是 pass@1 到 pass@k 之间那段，模型越弱那段越长。`--model claude-sonnet-5` 随时可以看平线。

## `live/` —— 共享运行时（演示只 import 这个）

| 文件 | 作用 |
|---|---|
| `bus.py` | 事件总线：`emit` → 内存 → `runs/<name>/<id>.jsonl` → SSE 订阅者。自带 `ThreadingHTTPServer`（`/` 看板、`/events` SSE、`/api/emit` 子进程转发、`/api/done`）。`with bus.start(...) as run:` 把正常 / 预算超支 / Ctrl-C / 异常统一成 `stop`，结尾挂住等你看。`python3 live/bus.py --serve runs/x.jsonl` 回放日志调页面（只给开发用） |
| `llm.py` | 网关客户端（Anthropic Messages 协议，urllib）。每次调用：先 `check_budget()`，发 `llm.call`（发出去的整个上下文），POST，用服务端 usage 记账，发 `llm.done`。`chat / chat_tools / extract_code` |
| `dashboard.html` | 单文件看板：时间线 · Token 与预算 · 验证表 · Prompt/产物并排 diff · 每次调用的上下文 · 图（SVG 自动布局）· 分数曲线。按事件自动显隐面板 |
| `tinygraph.py` | 150 行图运行时（add_node / add_edge / add_conditional_edges / add_fanout / compile(checkpoint_dir, interrupt_before, max_steps) / invoke(resume)），带事件钩子 |
| `tasks.py` | A 部分六题：spec **故意不全**，隐藏用例 = 没写下来的边界规则（验证-重试的地形） |
| `tasks_hard.py` | c1/c2 六题：spec **写全**但要 60–150 行才做得对（正则引擎、电子表格、CSV、semver、大数加法、拓扑序），部分题禁用现成库；`python3 live/tasks_hard.py` 用库/参考实现自测期望值 |
| `verify.py` | 尺子：子进程跑用例，只报第一个失败那一句（`verify`）或全量计数（`verify_detail`），认 `forbid` |

## `tools/`

```bash
tools/smoke.sh                       # haiku --fast --no-browser 跑全部 17 个 + check_log（约 $0.6、10 分钟）
tools/smoke.sh a1 c6                 # 只跑这几个
python3 tools/check_log.py runs/c6_evolve_program/<id>.jsonl    # 日志校验：kind 闭集、seq 递增、call/done 配对、版本连续、边引用、账单一致
node tools/shot.mjs c6_evolve_program -- --fast                 # Playwright 起演示、等 stop、截图 shots/<demo>.png、断言零控制台错误
python3 tools/lab_check.py t3        # lab-4-a 的结构级自检（读 runs/ 里的真日志）
tools/sync_lab.sh                    # 把这份 demos 同步进 parallight-labs 的 lab-4-a/starter/
tools/publish_github.sh              # 同步公开镜像 mrvgao/loop-graph-and-self-improvement-agent
```

## 学员版：lab-4-a

同一套代码也是 **lab-4-a** 的 starter（`parallight-labs/lab-4-a/`）：学员在自己机器上跑这 17 个演示，
然后用同一个 `live/` 运行时给自己的任务造循环和图。真相源是这个目录，改完跑 `tools/sync_lab.sh`
同步过去，**不要直接改 lab 的 starter**。

## 排练时踩过的坑（改代码前先读）

- `Run.verify` 的类型字段叫 `check`，不叫 `kind`（和 `emit(kind=…)` 撞名）。
- 子进程（a0 每轮）的 LLM 调用 id 带 `p<pid>·` 前缀，否则页面按 id 的 Map 会互相覆盖；子进程的 `llm.done` 在父进程 `/api/emit` 里重记账，否则父账单少一截。
- tinygraph 内部的 `__end__` 对外一律译成 `END`。
- 输出 `max_tokens` 太小会**静默**变成「0 条 delta」「NoneType」「语法错误」（c4 的 curator、c6 的变异、c1 的采样都栽过）：先看 `llm.done.stop_reason == "max_tokens"` 再怀疑模型。
- 短函数题（LeetCode 味的、自编小规则的）sonnet / haiku 温度 1.0 下 8 个样本全对且行为一样 —— 买不到 coverage；要看 pass@k 曲线得用 `tasks_hard.py` 那种尺度，还得用弱模型采样。
- c9 尺子研究：LEAKY = 评委按公开集打分，PRIVATE = 按私有集；写反了整个结论反过来。
- c8 的种子 harness 要故意写弱（「总结一下」「写个友好的说明」），否则零缺陷无事可修。
- `.env`、`runs/`、`checkpoints/`、`shots/` 都在 `.gitignore`；网关 key 绝不进 git。

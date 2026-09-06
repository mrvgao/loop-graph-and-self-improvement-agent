#!/usr/bin/env bash
# 把 demos/ 镜像到公开仓库 github.com/mrvgao/loop-graph-and-self-improvement-agent（学员看代码用）。
#   tools/publish_github.sh          # 只推 git 里已提交的文件（runs/ checkpoints/ .env 天然不会进去）+ .env.example
# 每次同步 = 一个 squash 提交覆盖 main（镜像不保留历史；真相源永远是 parallight 仓库的 course-content/c2-lecture-4/demos）。
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="mrvgao/loop-graph-and-self-improvement-agent"
SRC_SHA=$(git rev-parse --short HEAD)
TMP=$(mktemp -d)
git archive HEAD . | tar -x -C "$TMP"
cp .env.example "$TMP/.env.example"                       # 根 .gitignore 的 **/.env.* 把它挡在 parallight 仓库外，镜像里要有
grep -qE "^PARALLIGHT_API_KEY=plk_\.\.\.?$|plk_…" "$TMP/.env.example" || { echo "❌ .env.example 里不是占位 key，拒绝推送"; exit 1; }
{
  cat <<'HDR'
# Loop, Graph and Self-Improvement Agent

Parallight「Agentic Engineering 2026」二期第四课的全部现场演示：**Loop Engineering · Graph Engineering · Self-Improving Agent Systems**。
讲义 PDF：<https://parallight.ai/lectures/c2-lecture-4.pdf>。

17 个源码文件，纯标准库（Python 3.10+），每个文件一运行就自动打开一个实时看板（`http://127.0.0.1:8642`）：循环走到哪、prompt 怎么变、每次调用发出去的上下文、token 与成本、验证结果、图的节点在走、分数曲线。全部真调用模型，没有回放。

```bash
cp .env.example .env      # 填课程网关的 plk_ key，或自己的 ANTHROPIC_API_KEY
python3 live/llm.py       # 冒烟
python3 a0_self_driving.py --reset
```

---

HDR
  sed '1d' README.md                                       # 去掉 demos/README 自己的标题行，其余原样
} > "$TMP/README.md"
cd "$TMP"
git init -q -b main && git add -A && git -c user.name="Marvin Gao" -c user.email="marvin.gao.cs@gmail.com" commit -q -m "sync from parallight@$SRC_SHA"
if gh repo view "$REPO" >/dev/null 2>&1; then
  git remote add origin "https://github.com/$REPO.git"
  git push -q --force origin main
else
  gh repo create "$REPO" --public --source . --push \
    --description "Loop, Graph and Self-Improvement Agent — Parallight 二期第四课：17 个源码级演示 + 实时看板（纯标准库）" >/dev/null
fi
echo "✅ https://github.com/$REPO  ← parallight@$SRC_SHA"
rm -rf "$TMP"

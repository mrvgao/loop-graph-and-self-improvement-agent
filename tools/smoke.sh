#!/usr/bin/env bash
# 冒烟：用 haiku 把 17 个演示全部 --fast --no-browser 跑一遍，每份日志过 check_log.py。约 $0.6、10 分钟。
#   tools/smoke.sh            全部
#   tools/smoke.sh a1 c6      只跑这几个
set -uo pipefail
cd "$(dirname "$0")/.."
export MODEL="${MODEL:-claude-haiku-4-5}" LIVE_NO_BROWSER=1 PYTHONUNBUFFERED=1
ALL="a0 a1 a2 a3 b0 b1 b2 c1 c2 c3 c4 c5 c6 c7 c8 c9 c10"
want="${*:-$ALL}"; fail=0
run() { echo "── $1"; python3 "$@" > /dev/null 2> /tmp/smoke-err.txt || { echo "  ❌ exit $? "; tail -5 /tmp/smoke-err.txt; fail=$((fail+1)); return 1; }; }
for d in $want; do
  f=$(ls ${d}_*.py | head -1); name="${f%.py}"
  case $d in
    a0) run "$f" --reset --max-iters 2 --fast ;;
    a2) run "$f" --reset --crash-after 2 --fast; run "$f" --fast ;;
    b2) run "$f" --fast; run "$f" --approve ;;
    c9) run "$f" ;;
    *)  run "$f" --fast ;;
  esac
  python3 tools/check_log.py "$(ls -t runs/$name/*.jsonl | head -1)" || fail=$((fail+1))
done
echo; [ $fail = 0 ] && echo "✅ 全部通过" || { echo "❌ $fail 项失败"; exit 1; }

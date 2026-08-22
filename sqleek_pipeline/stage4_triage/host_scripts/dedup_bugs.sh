#!/bin/bash
# Bug 去重：基于堆栈信息输出不重复的 Bug（每个唯一堆栈只保留一条代表行，带文件路径）
# 结果写入 logSaved 同级目录下的 unique_bugs/ 文件夹。
#
# 用法: ./dedup_bugs.sh [logSaved目录路径]
# 示例: ./dedup_bugs.sh griffin_output/griffin_postgres1/logSaved

set -e

LOG_DIR="${1:-./logSaved}"

if [[ ! -d "$LOG_DIR" ]]; then
  echo "错误: 目录不存在: $LOG_DIR" >&2
  echo "用法: $0 <logSaved目录路径>" >&2
  exit 1
fi

# 在 logSaved 同级创建英文文件夹 unique_bugs，输出到该目录下
PARENT_DIR="$(cd "$LOG_DIR/.." && pwd)"
OUT_DIR="$PARENT_DIR/unique_bugs"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/dedup_list.txt"

# 只搜索日志文件，避免扫描无关文件导致卡顿
GREP_INCLUDE="--include=griffin.log*"
GREP_OPTS="-r $GREP_INCLUDE --binary-files=text -H --color=never"

{
  # 1) 非 gsignal：用 #0 帧去重（排除 #0 为 gsignal 的，留给下面用 #7 处理）
  grep $GREP_OPTS "force_exit_all.*#0 " "$LOG_DIR" 2>/dev/null | grep -v gsignal | sort -k 4 | uniq -f 3
  # 2) gsignal：在包含 #0.*gsignal 的文件里用 #7 帧去重（避免信号帧干扰）
  grep -rl $GREP_INCLUDE "force_exit_all.*#0.*gsignal" "$LOG_DIR" 2>/dev/null | xargs -r grep $GREP_OPTS "force_exit_all.*#7 " 2>/dev/null | sort -k 4 | uniq -f 3
} | tee "$OUT_FILE"

echo "" >&2
echo "去重结果已写入: $OUT_FILE" >&2

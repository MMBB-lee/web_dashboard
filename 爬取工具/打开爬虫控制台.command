#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print '请先安装 Python 3，再打开控制台。'
  read '?按回车关闭'
  exit 1
fi
exec python3 scripts/dashboard.py --open "$@"

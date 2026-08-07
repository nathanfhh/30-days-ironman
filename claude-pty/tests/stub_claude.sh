#!/bin/bash
# 測試用 claude stub（免 token）：--version 早期被呼叫；driver 啟動時印 marker 後 sleep 保活。
case "$1" in
  --version) echo "0.0.0-stub"; exit 0 ;;
esac
echo "REACHED-DRIVER-LAUNCH args=$*"
exec sleep 300

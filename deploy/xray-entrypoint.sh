#!/bin/sh
set -eu

config="${CLAUDE_REGISTER_XRAY_CONFIG:-/app/data/xray/config.json}"
enabled="${CLAUDE_REGISTER_XRAY_ENABLED:-/app/data/xray/enabled}"
if [ ! -r "$config" ] || [ ! -f "$enabled" ]; then
  echo "Xray 尚未启用，等待面板操作" >&2
  exec tail -f /dev/null
fi

exec /usr/local/bin/xray run -c "$config"

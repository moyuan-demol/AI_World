#!/bin/sh
# 容器 / 云平台启动脚本：优先使用平台注入的 PORT（Render / Railway / Fly 等）
set -e

PORT_TO_USE="$PORT"
if [ -z "$PORT_TO_USE" ]; then
  PORT_TO_USE=8000
fi

echo "[AI World] starting API on 0.0.0.0:$PORT_TO_USE"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT_TO_USE"

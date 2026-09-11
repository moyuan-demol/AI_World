#!/usr/bin/env bash
# AI World 一键启动脚本（本地开发模式）
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[AI World] 已根据 .env.example 生成 .env（如需真实模型回答，请填写 DEEPSEEK_API_KEY）"
fi

echo "[AI World] 准备后端环境 ..."
cd "$ROOT_DIR/backend"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[AI World] 启动后端 -> http://127.0.0.1:8000 (docs: /docs)"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

cd "$ROOT_DIR/frontend"
echo "[AI World] 安装前端依赖 ..."
npm install

echo "[AI World] 启动前端 -> http://127.0.0.1:5173"
npm run dev &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "[AI World] 正在停止服务 ..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait

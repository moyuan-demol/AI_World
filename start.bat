@echo off
REM AI World 一键启动脚本（Windows 本地开发模式）
setlocal

cd /d "%~dp0"

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [AI World] 已根据 .env.example 生成 .env，如需真实模型回答请填写 DEEPSEEK_API_KEY
)

echo [AI World] 准备后端环境 ...
cd backend
if not exist ".venv" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [AI World] 启动后端 -> http://127.0.0.1:8000
start "AI World Backend" cmd /k "call .venv\Scripts\activate.bat && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

cd ..\frontend
echo [AI World] 安装前端依赖 ...
call npm install

echo [AI World] 启动前端 -> http://127.0.0.1:5173
start "AI World Frontend" cmd /k "npm run dev"

echo.
echo [AI World] 服务已启动：前端 http://127.0.0.1:5173 ，后端 http://127.0.0.1:8000/docs
endlocal

#!/bin/sh
set -eu
cd "$(dirname "$0")"
# Configuration stays local and is never committed.
task_config=${SPIKE_E2B_ENV_FILE:-./.env.local}
if [ -f "$task_config" ]; then
  set -a
  . "$task_config"
  set +a
fi
export SPIKE_WORKSPACE=e2b
export E2B_SANDBOX_TIMEOUT=${E2B_SANDBOX_TIMEOUT:-1800}
task_python=${SPIKE_PYTHON:-.venv/bin/python}
if [ ! -x "$task_python" ]; then
  echo 'Python 环境不存在。请按 README 安装 .venv，或设置 SPIKE_PYTHON。' >&2
  exit 1
fi
if [ ! -f chat-ui/dist/chat.js ]; then
  echo '前端产物不存在。请执行 npm --prefix chat-ui ci 和 npm --prefix chat-ui run build。' >&2
  exit 1
fi
if [ -n "${SPIKE_PROXY_URL:-}" ]; then
  export HTTP_PROXY="$SPIKE_PROXY_URL"
  export HTTPS_PROXY="$SPIKE_PROXY_URL"
fi
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export PYDANTIC_AI_NO_BANNER=1
exec "$task_python" -m uvicorn server:app --host 127.0.0.1 --port 7948 --no-access-log

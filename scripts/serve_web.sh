#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

action=start
case ${1:-} in
  start|status|logs|stop|foreground) action=$1; shift ;;
esac

log_file="$PWD/logs/web.log"
pid_file="$PWD/logs/web.pid"
if [[ "$action" == logs ]]; then
  if [[ ! -f "$log_file" ]]; then
    printf '还没有 Web 日志。请先运行 bash scripts/serve_web.sh。\n' >&2
    exit 1
  fi
  exec tail -n 50 -F -- "$log_file"
fi

web_host=${PIXELTJCS_WEB_HOST:-${ROAMCHESTRA_WEB_HOST:-100.64.162.146}}
probe_host=$web_host
case "$probe_host" in
  0.0.0.0) probe_host=127.0.0.1 ;;
  ::) probe_host=::1 ;;
esac
url_host=$probe_host
if [[ "$probe_host" == *:* ]]; then url_host="[$probe_host]"; fi
scheme=http
for arg in "$@"; do
  case "$arg" in
    --ssl-certfile|--ssl-certfile=*) scheme=https ;;
  esac
done
ready_url="$scheme://$url_host:8765/api/health"

mkdir -p -- "$PWD/logs"
exec 9>"$PWD/logs/web.lock"
flock 9
read -r boot_id < /proc/sys/kernel/random/boot_id
source scripts/background_service.sh

if [[ -r "$pid_file" ]] && read -r pid saved_boot saved_start running_url < "$pid_file" \
  && [[ "$pid" =~ ^[1-9][0-9]*$ && "$saved_boot" == "$boot_id" ]] \
  && start_time=$(process_start_time "$pid") && [[ "$start_time" == "$saved_start" ]]; then
  if [[ "$action" == stop ]]; then
    kill -TERM "$pid"
    printf '已向 Web 后端发送停止信号（PID %s）。\n' "$pid"
  elif curl --noproxy '*' --insecure --fail --silent --max-time 2 "$running_url" >/dev/null; then
    printf 'Web 已就绪（PID %s）。访问 %s/http-preview/\n' "$pid" "${running_url%/api/health}"
  else
    printf 'Web 进程正在运行，接口尚未就绪（PID %s）。日志 %s\n' "$pid" "$log_file"
  fi
  exit 0
fi

rm -f -- "$pid_file"
case "$action" in
  stop) printf '后台 Web 服务已停止。\n'; exit 0 ;;
  status) printf '后台 Web 服务未运行。日志 %s\n' "$log_file"; exit 1 ;;
esac
if ( : > "/dev/tcp/$probe_host/8765" ) 2>/dev/null; then
  printf 'Web 端口 8765 已在使用。如果旧后端在前台运行，请先在原终端按 Ctrl+C。\n' >&2
  exit 1
fi

export PIXELTJCS_LLM=${PIXELTJCS_LLM:-${ROAMCHESTRA_LLM:-hybrid}}
case "$PIXELTJCS_LLM" in
  hybrid|local)
    export LLM_BASE_URL="http://127.0.0.1:${QWEN_PORT:-8909}/v1"
    export LLM_API_KEY=local
    export LLM_CHAT_MODEL=Qwen3.5-9B
    export LLM_THINK_MODEL=Qwen3.5-9B
    export LLM_EMBED_MODEL=''
    export LLM_MAX_CONCURRENT_REQUESTS=1
    if ! curl --noproxy '*' --fail --silent --max-time 2 "$LLM_BASE_URL/models" >/dev/null; then
      printf '本地模型尚未就绪。可运行 bash scripts/serve_qwen.sh 启动。\n' >&2
    fi
    ;;
  env) ;; # Use the existing environment and .env for gateway settings.
  *) printf 'PIXELTJCS_LLM 只接受 hybrid、local 或 env。\n' >&2; exit 2 ;;
esac

command=(.venv/bin/python -u -X utf8 -B -m uvicorn backend.web:app
  --host "$web_host" --port 8765 --workers 1 "$@")
if [[ "$action" == foreground ]]; then
  exec "${command[@]}" 9>&-
fi

set +m
nohup setsid "${command[@]}" </dev/null >>"$log_file" 2>&1 9>&- &
pid=$!
if ! start_time=$(process_start_time "$pid"); then
  printf 'Web 进程启动失败。日志 %s\n' "$log_file" >&2
  exit 1
fi
printf '%s %s %s %s\n' "$pid" "$boot_id" "$start_time" "$ready_url" > "$pid_file"
printf 'Web 已在后台启动（PID %s）。日志 %s\n' "$pid" "$log_file"

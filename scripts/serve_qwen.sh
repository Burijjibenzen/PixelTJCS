#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

action=${1:-start}
if [[ $# -gt 1 ]]; then
  printf '用法：bash scripts/serve_qwen.sh [start|status|logs|stop]\n' >&2
  exit 2
fi

log_file="$PWD/logs/qwen.log"
pid_file="$PWD/logs/qwen.pid"

case "$action" in
  logs)
    if [[ ! -f "$log_file" ]]; then
      printf '还没有模型日志。请先运行 bash scripts/serve_qwen.sh。\n' >&2
      exit 1
    fi
    exec tail -n 50 -F -- "$log_file"
    ;;
  start|status|stop) ;;
  *) printf '用法：bash scripts/serve_qwen.sh [start|status|logs|stop]\n' >&2; exit 2 ;;
esac

mkdir -p -- "$PWD/logs"
exec 9>"$PWD/logs/qwen.lock"
flock 9
read -r boot_id < /proc/sys/kernel/random/boot_id

source scripts/background_service.sh

if [[ -r "$pid_file" ]] && read -r pid saved_boot saved_start running_port < "$pid_file" \
  && [[ "$pid" =~ ^[1-9][0-9]*$ && "$saved_boot" == "$boot_id" ]] \
  && start_time=$(process_start_time "$pid") && [[ "$start_time" == "$saved_start" ]]; then
  if [[ "$action" == stop ]]; then
    kill -TERM "$pid"
    printf '已向模型进程发送停止信号（PID %s）。\n' "$pid"
  elif curl --noproxy '*' --fail --silent --max-time 2 "http://127.0.0.1:$running_port/v1/models" >/dev/null; then
    printf '模型已就绪，监听 127.0.0.1:%s（PID %s）。\n' "$running_port" "$pid"
  else
    printf '模型进程正在运行，接口尚未就绪（PID %s）。日志 %s\n' "$pid" "$log_file"
  fi
  exit 0
fi

rm -f -- "$pid_file"
case "$action" in
  stop) printf '后台模型服务已停止。\n'; exit 0 ;;
  status) printf '后台模型服务未运行。日志 %s\n' "$log_file"; exit 1 ;;
esac

qwen_python=${QWEN_PYTHON:-$HOME/miniforge3/envs/qwen35-serve/bin/python}
qwen_model_dir=${QWEN_MODEL_DIR:-/data/zhuyakun/Qwen3.5-9B}
qwen_port=${QWEN_PORT:-8909}
if [[ ! -x "$qwen_python" ]]; then
  printf '找不到 qwen35-serve 的 Python：%s\n' "$qwen_python" >&2
  exit 1
fi
if [[ ! -r "$qwen_model_dir/config.json" || ! -r "$qwen_model_dir/model.safetensors.index.json" ]]; then
  printf '找不到 Qwen3.5-9B 的本地权重：%s\n' "$qwen_model_dir" >&2
  exit 1
fi
if [[ ! "$qwen_port" =~ ^[1-9][0-9]{0,4}$ ]] || (( qwen_port > 65535 )); then
  printf 'QWEN_PORT 必须介于 1 和 65535。\n' >&2
  exit 2
fi
if ( : > "/dev/tcp/127.0.0.1/$qwen_port" ) 2>/dev/null; then
  printf '本地端口 %s 已在使用。如果模型仍以前台方式运行，请先在原终端按 Ctrl+C。\n' "$qwen_port" >&2
  exit 1
fi

context_args=()
if [[ -n ${QWEN_CONTEXT_TOKENS:-} ]]; then
  context_args=(--context-tokens "$QWEN_CONTEXT_TOKENS")
fi
set +m
nohup setsid "$qwen_python" -u -X utf8 -B scripts/local_qwen_api.py \
  --model-dir "$qwen_model_dir" --port "$qwen_port" "${context_args[@]}" \
  </dev/null >>"$log_file" 2>&1 9>&- &
pid=$!
if ! start_time=$(process_start_time "$pid"); then
  printf '模型进程启动失败。日志 %s\n' "$log_file" >&2
  exit 1
fi
printf '%s %s %s %s\n' "$pid" "$boot_id" "$start_time" "$qwen_port" > "$pid_file"
printf '模型已在后台启动（PID %s），正在加载权重。日志 %s\n' "$pid" "$log_file"

"""Windows 本地启动入口，由项目根目录的批处理调用。"""

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import uvicorn

from scripts.runtime_paths import resolve_godot


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    try:
        godot = resolve_godot()
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error
    if not (frontend / "project.godot").is_file():
        raise SystemExit(f"找不到前端项目：{frontend / 'project.godot'}")

    host, port = "127.0.0.1", 8000
    server = uvicorn.Server(uvicorn.Config("backend.api:app", host=host, port=port, workers=1, access_log=False))
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as executor:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((host, port))
        except OSError as error:
            raise SystemExit(f"无法使用 {host}:{port}。请检查是否已有后端在运行。\n{error}") from error

        print("正在启动 Roamchestra 后端…", flush=True)
        backend = executor.submit(server.run, [listener])
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if backend.done():
                    backend.result()
                    raise SystemExit("后端在就绪前已退出，请查看上方日志。")
                if time.monotonic() >= deadline:
                    raise SystemExit("等待后端启动超时，请查看上方日志。")
                time.sleep(0.1)

            with httpx.Client(trust_env=False, timeout=3) as client:
                health = client.get(f"http://{host}:{port}/health").raise_for_status().json()
            if health.get("service") != "roamchestra" or health.get("status") != "ok":
                raise SystemExit("Roamchestra 后端健康检查未通过。")

            print("后端已就绪，正在打开 Godot。关闭游戏窗口后将停止后端。", flush=True)
            subprocess.run([str(godot), "--path", str(frontend)], cwd=root, check=True)
        finally:
            server.should_exit = True
        backend.result()


if __name__ == "__main__":
    main()

"""临时数据库+模拟模型启动真实API，并以Godot验证角色公开资料；可选渲染截图。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend import api
from backend.config import SOCIAL
from test_social import SocialTests


def main():
    fixture = SocialTests()
    fixture.setUp()
    root = Path(__file__).resolve().parents[1]
    work = root.parent
    service = fixture.s
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, log_level='error', access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    env = dict(os.environ, ROAM_PROFILE_BACKEND=f'http://127.0.0.1:{port}',
               ROAM_PROFILE_NAMES=json.dumps([fixture.a, fixture.b]),
               ROAM_PROFILE_WORLD=json.dumps(service.world_snapshot(), ensure_ascii=False))
    command = [str(work / 'tools/godot/Godot_v4.5.2-stable_win64_console.exe'),
               '--path', str(root / 'frontend'), '--script', str(root / 'tests/check_profile_http.gd'),
               '--log-file', str(work / 'tools/godot-profile-http.log')]
    if not env.get('ROAM_PROFILE_PREVIEW'):
        command.append('--headless')
    try:
        with patch.object(api, '_service', service), patch.object(SOCIAL, 'base_chat_probability', 0):
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError('Profile test API did not start')
            result = subprocess.run(command, env=env, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=45,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            print(result.stdout)
            print(result.stderr)
            if result.returncode or 'PASS:' not in result.stdout or 'SCRIPT ERROR' in result.stderr:
                raise RuntimeError('Godot public profile integration failed')
    finally:
        server.should_exit = True
        if thread.ident is not None:
            thread.join(timeout=20)
        sock.close()
        fixture.tearDown()


if __name__ == '__main__':
    main()

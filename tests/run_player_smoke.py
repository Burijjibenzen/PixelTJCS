"""随机端口、临时数据库、模拟模型与真实Godot玩家聊天面板联调。"""
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
    fixture = SocialTests();fixture.setUp()
    service = fixture.s
    root = Path(__file__).resolve().parents[1];work = root.parent
    @api.app.post('/test/player/busy/{name}')
    def busy(name: str, value: bool):
        with service.lock:
            if value:service.busy.add(name)
            else:service.busy.discard(name)
        return {'ok':True}
    def reply(*args, **kwargs):
        time.sleep(.35)
        return '欢迎来校园！我们可以一起讨论研究计划。'
    sock = socket.socket();sock.bind(('127.0.0.1',0))
    server = uvicorn.Server(uvicorn.Config(api.app,log_level='error',access_log=False))
    thread = threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    env = dict(os.environ, APPDATA=str(work/'.cache/godot-user'),
        ROAM_PLAYER_BACKEND=f'http://127.0.0.1:{sock.getsockname()[1]}')
    command = [os.environ.get('GODOT_EXE',str(work/'tools/godot/Godot_v4.5.2-stable_win64_console.exe')),
        '--path',str(root/'frontend'),'--disable-vsync','--fixed-fps','60','--script',str(root/'tests/check_player_chat.gd'),
        '--log-file',str(work/'.cache/player-chat-http.log')]
    if not env.get('ROAM_PLAYER_PREVIEW'):command.append('--headless')
    try:
        with patch.object(api,'_service',service),patch.object(SOCIAL,'base_chat_probability',0),patch.object(service.llm,'chat',side_effect=reply):
            thread.start()
            deadline = time.monotonic()+10
            while not server.started and time.monotonic()<deadline:time.sleep(.05)
            if not server.started:raise RuntimeError('Player test API did not start')
            result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8',errors='replace',
                timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            print(result.stdout);print(result.stderr)
            if result.returncode or 'PASS:' not in result.stdout or 'SCRIPT ERROR' in result.stderr:
                raise RuntimeError('Player chat Godot integration failed')
    finally:
        server.should_exit=True
        if thread.ident is not None:thread.join(timeout=20)
        sock.close();fixture.tearDown()


if __name__=='__main__':main()

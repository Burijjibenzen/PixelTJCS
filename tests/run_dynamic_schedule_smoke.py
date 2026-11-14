"""真实 Godot 目标同步、跨馆寻路及到达 HTTP 闭环；临时数据库/模拟模型。"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend import api
from backend.config import SOCIAL
from test_social import SocialTests


def main():
    fixture=SocialTests();fixture.setUp();service=fixture.s
    root=Path(__file__).resolve().parents[1];work=root.parent
    subject='周冲'
    service.world.inject_event('去智信馆图书分馆查资料',[subject])
    service.planner.tick(time.monotonic(),0)
    @api.app.post('/test/dynamic/advance')
    def change_slot():
        with service.lock:
            service.world.clock.advance(1500)
            service.planner.tick(time.monotonic(),0)
            return service.world_snapshot()
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(api.app,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    env=dict(os.environ,APPDATA=str(work/'.cache/godot-user'),ROAM_DYNAMIC_BACKEND=url)
    command=[os.environ.get('GODOT_EXE',str(work/'tools/godot/Godot_v4.5.2-stable_win64_console.exe')),
             '--path',str(root/'frontend'),'--disable-vsync','--fixed-fps','60','--script',str(root/'tests/check_dynamic_schedule.gd'),
             '--log-file',str(work/'.cache/dynamic-schedule.log')]
    if not env.get('ROAM_DYNAMIC_PREVIEW'):command.append('--headless')
    try:
        with patch.object(api,'_service',service),patch.object(SOCIAL,'base_chat_probability',0):
            thread.start()
            deadline=time.monotonic()+10
            while not server.started and time.monotonic()<deadline:time.sleep(.05)
            if not server.started:raise RuntimeError('Test API failed to start')
            result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
                                  timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            print(result.stdout);print(result.stderr)
            if result.returncode or 'PASS:' not in result.stdout or 'SCRIPT ERROR' in result.stderr:
                raise RuntimeError('Dynamic schedule Godot integration failed')
            task=service.planner.current_item(subject)
            assert task and task['kind']=='library' and task['phase']=='active'
            assert service.positions[subject]['area']=='zhixinguan_floor2/zx2_lib'
    finally:
        server.should_exit=True
        if thread.ident is not None:thread.join(timeout=20)
        sock.close();fixture.tearDown()


if __name__=='__main__':main()

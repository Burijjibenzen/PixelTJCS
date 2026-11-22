"""临时后端+真实Godot：公告界面发布、两个入口、实际行走阅读与去重。"""
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
from scripts.runtime_paths import resolve_godot
from backend import api
from backend.agent_runtime import WhisperAction
from backend.config import SOCIAL
from test_social import SocialTests


def main():
    fixture=SocialTests();fixture.setUp();service=fixture.s
    service.speed=0
    root=Path(__file__).resolve().parents[1];work=root.parent
    @api.app.get('/test/notice/status')
    def status():
        with service.lock:
            with service.database() as db:
                reads=[dict(npc=r[0],notice=r[1],status=r[2]) for r in db.execute('SELECT npc,notice,status FROM board_reads')]
            comments=[e for e in service.bus.history(limit=1000)
                      if e.get('speech')=='event_self_talk' and e.get('game_event_id','').startswith('notice:')]
            return dict(reading=list(service.reading),reads=reads,comments=comments)
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    server=uvicorn.Server(uvicorn.Config(api.app,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    env=dict(os.environ,APPDATA=str(work/'.cache/godot-user'),ROAM_NOTICE_BACKEND=f'http://127.0.0.1:{sock.getsockname()[1]}')
    command=[str(resolve_godot()),
             '--path',str(root/'frontend'),'--disable-vsync','--fixed-fps','60','--script',str(root/'tests/check_noticeboard.gd'),
             '--log-file',str(work/'.cache/notice-board-http.log')]
    if not env.get('ROAM_NOTICE_PREVIEW'):command.append('--headless')
    def model(_system, _prompt, schema, **_kwargs):
        if schema is WhisperAction:
            return WhisperAction(type='go_to',area='j3_lab')
        return None
    try:
        with patch.object(api,'_service',service),patch.object(SOCIAL,'base_chat_probability',0), \
                patch.object(service.llm,'chat_json',side_effect=model):
            thread.start()
            try:
                deadline=time.monotonic()+10
                while not server.started and time.monotonic()<deadline:time.sleep(.05)
                if not server.started:raise RuntimeError('Test API failed to start')
                result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                print(result.stdout);print(result.stderr)
                if result.returncode or 'PASS:' not in result.stdout or 'SCRIPT ERROR' in result.stderr:
                    raise RuntimeError('Notice board integration failed')
            finally:
                server.should_exit=True
                thread.join()
    finally:
        sock.close();fixture.tearDown()


if __name__=='__main__':main()

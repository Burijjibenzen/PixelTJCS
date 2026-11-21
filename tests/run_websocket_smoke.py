"""M3: 使用临时数据库、假模型、随机本机端口验证真实 Godot WebSocket 传输。"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from fastapi import FastAPI
from test_social import SocialTests
from backend.social import install
from backend.config import SOCIAL

def main():
    fixture=SocialTests();fixture.setUp();service=fixture.s
    service.speed=0
    # M3: 首次连接前已有台词，验证实际前端能补日志而不重放旧气泡。
    service.bus.publish('dialogue_line', conversation_id='history-test', sequence=1,
                           participants=[fixture.a,fixture.b], speaker=fixture.a, text='连接前的台词')
    app=FastAPI();install(app,lambda:service)
    app.get('/health')(lambda:{'status':'ok'})
    @app.post('/test/start')
    def start():
        with service.lock:
            fixture.report_positions([fixture.a,fixture.b])
            service.scheduler.submit(fixture.a,fixture.b,'真实 WS 集成测试')
        return {'ok':True}
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False,ws='auto'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    root=Path(__file__).resolve().parents[1];work=root.parent
    env=dict(os.environ,APPDATA=str(work/'.cache/godot-user'),M3_TEST_WS_URL=f'ws://127.0.0.1:{port}', M3_TEST_NAMES=json.dumps([fixture.a,fixture.b]))
    try:
        with patch.object(SOCIAL,'base_chat_probability',0):
            thread.start()
            deadline=time.monotonic()+10
            while not server.started and time.monotonic()<deadline:time.sleep(0.05)
            with httpx.Client(trust_env=False,timeout=3) as client:
                response=client.get(f'http://127.0.0.1:{port}/health');response.raise_for_status()
            command=[os.environ.get('GODOT_EXE',str(root.parents[1]/'.tools/godot-4.5.2/Godot_v4.5.2-stable_win64_console.exe')),'--headless','--path',str(root/'frontend'),
                '--script',str(root/'tests/check_websocket_peer.gd'),'--log-file',str(work/'.cache/m3-real-ws.log')]
            result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            print(result.stdout);print(result.stderr)
            if result.returncode or 'PASS:' not in result.stdout:raise RuntimeError('Godot WS test failed')
    finally:
        server.should_exit=True;thread.join(timeout=20);sock.close();fixture.tearDown()

if __name__=='__main__':main()

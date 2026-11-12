"""M3 装配与查询：LangGraph 调度在 scheduler.py；事件仅经 /ws/events 推送。"""
import asyncio
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from backend.service import GameService
from backend.config import SOCIAL
from backend.world import AREAS, grid_of
from backend.event_stream import EventStream
from backend.scheduler import Scheduler


class SocialService(GameService):
    def __init__(self,fresh_db=False):
        super().__init__(fresh_db=fresh_db)
        self.lock=threading.RLock()
        self.busy=set();self.reflecting=set();self.seen={};self.release_at={}
        self.meetings=[];self.live_lines={};self.future=None
        self._closed=False
        self.paused=False;self.speed=SOCIAL.speed;self.epoch=uuid.uuid4().hex
        self.bus=EventStream(self.memory.cfg.db_path,SOCIAL.websocket_queue)
        self.engine.require_colocated=True
        self.engine.emit=self.emit
        self.engine.gossip_writer=self.write_gossip
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS social_state(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS social_changes(a TEXT,b TEXT,delta REAL,PRIMARY KEY(a,b));
                CREATE TABLE IF NOT EXISTS gossip(root TEXT,holder TEXT,source TEXT,content TEXT,hops INTEGER,memory_id TEXT,PRIMARY KEY(root,holder));
                CREATE TABLE IF NOT EXISTS m3_memory_history(id INTEGER PRIMARY KEY AUTOINCREMENT,npc TEXT,game_time REAL,payload TEXT);
            """)
            row=db.execute("SELECT value FROM social_state WHERE key='clock'").fetchone()
            if row:self.world.clock.minutes=max(self.world.clock.now,float(row[0]))
        self.scheduler=Scheduler(self)
        for name in self.npcs:self.capture_memory(name)

    @contextmanager
    def database(self):
        conn=self.memory._connect()
        try:
            with conn:yield conn
        finally:conn.close()

    def emit(self,kind,**data):
        with self.lock:
            if kind=='dialogue_line':
                self.live_lines[data['conversation_id']]=dict(data)
            if kind=='relation_changed':
                with self.database() as db:
                    db.execute('INSERT OR REPLACE INTO social_changes VALUES(?,?,?)',(*sorted((data['a'],data['b'])),data['delta']))
        if kind=='memory_changed':self.capture_memory(data['npc'])
        return self.bus.publish(kind,game_time=self.world.clock.now,**data)

    def save_clock(self):
        with self.database() as db:
            db.execute("INSERT OR REPLACE INTO social_state VALUES('clock',?)",(str(self.world.clock.now),))

    def start(self):self.scheduler.start()
    def stop(self):
        if self._closed:return
        self._closed=True
        self.scheduler.stop()
        self.save_clock()
        self.engine.close()

    def heartbeat(self):
        with self.lock:
            for name in self.seen:self.seen[name]=time.monotonic()

    def active_meetings(self):
        return [m for m in self.meetings if m['state'] not in ('completed','cancelled','failed')]

    def meeting(self,key):
        return next((m for m in self.meetings if m['id']==key),None)

    def request_meeting(self,a,b,area=None,trigger='用户安排会面，聊聊近况'):
        with self.lock:
            if a==b or a not in self.npcs or b not in self.npcs:raise HTTPException(400,'请选择两个不同角色')
            if area is not None and area not in AREAS:raise HTTPException(400,'未知区域')
            for m in self.active_meetings():
                if {a,b}=={m['a'],m['b']}:return m
            if len(self.active_meetings())>=50:raise HTTPException(409,'会面队列已满')
            self.meetings=self.active_meetings()+[m for m in self.meetings if m not in self.active_meetings()][-50:]
            m=dict(id=uuid.uuid4().hex,a=a,b=b,area=AREAS[area]['id'] if area else None,
                   state='waiting',detail='等待双方空闲',travel_seconds=0.0,trigger=trigger)
            self.meetings.append(m)
            return m

    def cancel_meeting(self,key):
        with self.lock:
            m=self.meeting(key)
            if not m:raise HTTPException(404,'会面不存在')
            if m['state'] in ('deciding','talking','playing'):raise HTTPException(409,'已开始处理，请等待结束')
            if m in self.active_meetings():m.update(state='cancelled',detail='已取消')
            return m

    def available(self,a,b):return not ({a,b}&(self.busy|self.reflecting))
    def valid_pair(self,a,b):
        return (a!=b and a in self.npcs and b in self.npcs and
                AREAS[self.world.area_of(a)]['id']==AREAS[self.world.area_of(b)]['id'] and self.world.can_chat(a,b)[0])

    def report_arrival(self,npc_name,grid_id=None,map_id=None,room_id=None):
        with self.lock:
            if npc_name in self.busy:return True,'busy'
            result=super().report_arrival(npc_name,grid_id,map_id,room_id)
            if result[0]:self.seen[npc_name]=time.monotonic()
        if result[0] and result[1]=='moved':
            self.emit('movement',npc=npc_name,area=self.world.area_of(npc_name))
        return result

    def run_conversation(self,a,b,trigger,scheduled=None):
        # M3: scheduler 始终通过装配根进入对话；已占用角色的图任务不重复预约。
        if scheduled is not None:
            return self.perform(a,b,trigger,**scheduled)
        with self.lock:
            if self.paused or not self.valid_pair(a,b) or not self.available(a,b) or len(self.busy)>=SOCIAL.max_conversations*2:return None
            if any({a,b}&{m['a'],m['b']} for m in self.active_meetings()):return None
            self.busy.update((a,b))
        # 手动端点也经过同一 LangGraph 调度图，不保留绕过决策的旧路径。
        return self.scheduler.execute(dict(a=a,b=b,trigger=trigger,forced=True,meeting_id='',conversation_id=uuid.uuid4().hex))

    def perform(self,a,b,trigger,run_id,candidate,meeting_id=''):
        with self.lock:
            if not self.valid_pair(a,b):raise ValueError('位置或冷却条件已变化')
            m=self.meeting(meeting_id)
            if m:m.update(state='talking',detail='LangGraph 对话中')
        result=self.engine.run(a,b,trigger,run_id=run_id,gossip=candidate)
        if not result:raise ValueError('对话校验未通过')
        self.finish(a,b,meeting_id)
        return result

    def finish(self,a,b,meeting_id=''):
        with self.lock:
            self.release_at[tuple(sorted((a,b)))]=time.monotonic()+SOCIAL.bubble_seconds
            m=self.meeting(meeting_id)
            if m:m.update(state='playing',detail='最后一句播放中')
        self.save_clock()

    def fail(self,run_id,a,b,error,meeting_id=''):
        with self.lock:
            self.live_lines.pop(run_id,None)
            m=self.meeting(meeting_id)
            if m:m.update(state='failed',detail='对话失败：'+error+'；可从检查点恢复')
        self.emit('error',conversation_id=run_id,participants=[a,b],message=f'{a}与{b}对话失败：{error}')

    def resume(self,run_id):
        snapshot=self.engine.graph.get_state({'configurable':{'thread_id':run_id}})
        if not snapshot.values:raise HTTPException(404,'未知检查点')
        a,b=snapshot.values['a'],snapshot.values['b']
        with self.lock:
            if self.paused or not self.available(a,b) or len(self.busy)>=SOCIAL.max_conversations*2:raise HTTPException(409,'角色或系统忙碌')
            if any(AREAS[self.world.area_of(n)]['id']!=snapshot.values['area'] for n in (a,b)):raise HTTPException(409,'请先让双方返回原会面区域')
            self.busy.update((a,b))
        try:
            result=self.engine.resume(run_id)
            self.finish(a,b)
            return result
        finally:
            with self.lock:
                if tuple(sorted((a,b))) not in self.release_at:self.busy.difference_update((a,b))

    def gossip_candidate(self,speaker,listener):
        with self.database() as db:
            row=db.execute("""SELECT g.root,g.content,g.hops FROM gossip g JOIN memories m ON m.id=g.memory_id
                WHERE g.holder=? AND m.forgotten=0 AND g.hops<?
                AND NOT EXISTS(SELECT 1 FROM gossip x WHERE x.root=g.root AND x.holder=?)
                ORDER BY m.game_time DESC LIMIT 1""",(speaker,SOCIAL.gossip_max_hops,listener)).fetchone()
        return dict(root=row[0],content=row[1],hops=row[2]) if row else None

    def write_gossip(self,state):
        # M3: 只有模型选择转述且开场已实际发言，第三人才获得听说记忆。
        a,b=state['a'],state['b'];candidate=state['gossip']
        if candidate:
            mid=f"gossip:{candidate['root']}:{b}"
            item=self.memory.get_by_id(mid) or self.memory.add(agent=b,content=f"听{a}说：{candidate['content']}",
                type='gossip',importance=0.35,game_time=state['time'],location=state['area'],participants=[a],
                tags=['gossip',candidate['root'],f'source:{a}'],memory_id=mid)
            with self.database() as db:
                db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',
                           (candidate['root'],b,a,candidate['content'],candidate['hops']+1,item.id))
            self.emit('memory_changed',event_key=state['id']+':gossip-memory',npc=b)
            return dict(root=candidate['root'],source=a,recipient=b,hops=candidate['hops']+1)
        for name in (a,b):
            item=state['memories'].get(name)
            if item:
                with self.database() as db:
                    db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',
                        (state['id'],name,name,f"{name}的经历：{item['content']}",0,item['id']))
        return {}

    def maintain(self):
        for name in self.npcs:
            with self.lock:
                reserved={n for m in self.active_meetings() for n in (m['a'],m['b'])}
                if self.scheduler.stop_event.is_set():break
                if name in self.busy or name in reserved:continue
                self.reflecting.add(name)
            try:
                self.engine.agents[name].graph.invoke({'task':'reflect'})
            finally:
                with self.lock:self.reflecting.discard(name)
        self.memory.full_forget_pass(self.world.clock.now)
        for name in self.npcs:self.emit('memory_changed',npc=name)
        self.save_clock()

    def advance_time(self,minutes,maintenance=True):
        # M3: 同一入口推进时钟；轻 tick 不触发全员模型反思。
        with self.lock:
            if not maintenance:
                self.world.clock.advance(minutes)
                return {'now':self.world.clock.fmt()}
            if self.busy or (self.future and not self.future.done()):raise HTTPException(409,'对话或维护中，请稍后快进')
            self.world.clock.advance(minutes)
            self.future=self.scheduler.maintenance.submit(self.maintain)
        return {'now':self.world.clock.fmt(),'maintenance':'queued'}

    def memory_panel(self,npc_name,snapshot_id=None):
        if snapshot_id is not None:
            with self.database() as db:
                row=db.execute('SELECT payload FROM m3_memory_history WHERE id=? AND npc=?',(snapshot_id,npc_name)).fetchone()
            if not row:raise HTTPException(404,'该角色没有此历史快照')
            return json.loads(row[0])
        panel=super().memory_panel(npc_name)
        if panel:
            for section in ('active','forgotten','reflections'):
                for item in panel[section]:item['strength']=round(self.memory._strength(item['game_time'],item['importance'],self.world.clock.now),3)
            panel['as_of']=self.world.clock.now
        return panel

    def capture_memory(self,name):
        panel=self.memory_panel(name)
        with self.database() as db:
            db.execute('INSERT INTO m3_memory_history(npc,game_time,payload) VALUES(?,?,?)',
                       (name,self.world.clock.now,json.dumps(panel,ensure_ascii=False)))

    def world_snapshot(self):
        with self.lock:
            result=super().world_snapshot()
            for name,npc in result['npcs'].items():npc['busy']=name in self.busy
            targeted=set()
            for m in self.active_meetings():
                for name in (m['a'],m['b']):
                    if name in targeted:continue
                    targeted.add(name)
                    if m['area'] is not None:
                        area=AREAS[m['area']]
                        result['npcs'][name].update(target_map_id=area['map_id'],target_room_id=area['room_id'],target_grid=grid_of(area['id']))
            result['social']=dict(meetings=[dict(m) for m in self.meetings],paused=self.paused,speed=self.speed,epoch=self.epoch,
                                 model_configured=bool(getattr(getattr(self.llm,'cfg',None),'api_key','')))
            return result

class Meet(BaseModel):
    a:str
    b:str
    area:str|None=None
class Notice(BaseModel):
    text:str=Field(min_length=1,max_length=500)
    participants:list[str]
class Control(BaseModel):
    paused:bool=False
    speed:float=Field(default=SOCIAL.speed,ge=0,le=60)


def install(app,get_service):
    @asynccontextmanager
    async def lifespan(_app):
        service=get_service();service.start()
        try:yield
        finally:await asyncio.to_thread(service.stop)
    app.router.lifespan_context=lifespan

    @app.websocket('/ws/events')
    async def websocket_events(ws:WebSocket):
        s=get_service()
        raw=ws.query_params.get('after')
        try:after=max(0,int(raw)) if raw is not None else None
        except ValueError:
            await ws.close(code=1008);return
        await ws.accept()
        queue,latest,replay=s.bus.subscribe(after)
        if after is not None and after>latest:
            after=None;replay=[]  # M3: 数据库被显式重置后，旧游标不能屏蔽新事件。
        with s.lock:live=[dict(line) for line in s.live_lines.values()]
        try:
            await ws.send_json(dict(kind='snapshot',id=after if after is not None else latest,epoch=s.epoch,
                                    live=live,world=await asyncio.to_thread(s.world_snapshot)))
        except (WebSocketDisconnect,RuntimeError):
            s.bus.unsubscribe(queue)
            return
        async def receive():
            while True:
                data=await ws.receive_json()
                if isinstance(data,dict) and data.get('kind')=='ping':
                    s.heartbeat()
        receiver=asyncio.create_task(receive())
        try:
            cursor=after or 0
            if after is not None:
                while replay:
                    for event in replay:
                        if event['id']>latest:break
                        await ws.send_json(dict(event,replay=True));cursor=event['id']
                    if cursor>=latest:break
                    replay=await asyncio.to_thread(s.bus.history,cursor,512)
            while True:
                getter=asyncio.create_task(queue.get())
                done,_=await asyncio.wait([getter,receiver],timeout=5,return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    getter.cancel();await receiver;break
                if getter not in done:
                    getter.cancel()
                    await ws.send_json({'kind':'heartbeat'});continue
                event=getter.result()
                if event['kind']=='overflow':
                    await ws.close(code=1013,reason='slow client; reconnect with cursor');break
                if event['id']>cursor or after is None:
                    await ws.send_json(event);cursor=event['id']
        except (WebSocketDisconnect,RuntimeError,ValueError):pass
        finally:
            receiver.cancel()
            await asyncio.gather(receiver,return_exceptions=True)
            s.bus.unsubscribe(queue)

    @app.post('/social/control')
    @app.post('/world/speed')
    def control(req:Control):
        s=get_service()
        with s.lock:s.paused,s.speed=req.paused,req.speed
        s.emit('world',snapshot=s.world_snapshot())
        return req.model_dump()

    @app.get('/world/time')
    def world_time():
        s=get_service();return dict(minutes=s.world.clock.now,time=s.world.clock.fmt(),speed=s.speed,paused=s.paused)

    @app.get('/social/relations')
    def relations():
        s=get_service();names=list(s.npcs)
        with s.database() as db:deltas={(a,b):delta for a,b,delta in db.execute('SELECT a,b,delta FROM social_changes')}
        return [dict(a=a,b=b,delta=deltas.get(tuple(sorted((a,b))),0),score=s.relations.get(a,b),
                     level=s.relations.level(s.relations.get(a,b)),reason=s.relations.last_reason(a,b))
                for i,a in enumerate(names) for b in names[i+1:]]

    @app.post('/social/meet')
    @app.post('/debug/force_meet')
    def meet(req:Meet):
        m=get_service().request_meeting(req.a,req.b,req.area)
        return dict(ok=True,detail='会面已排队；实际抵达、空闲且冷却结束后触发',meeting=m)

    @app.post('/social/meet/{meeting_id}/cancel')
    def cancel(meeting_id:str):return dict(ok=True,detail='会面已取消',meeting=get_service().cancel_meeting(meeting_id))

    @app.post('/social/event')
    @app.post('/debug/event')
    def event(req:Notice):
        s=get_service()
        if any(n not in s.npcs for n in req.participants):raise HTTPException(400,'未知角色')
        def record():
            for name in req.participants:
                s.observe(name,req.text,0.4);s.emit('memory_changed',npc=name)
            s.world.inject_event(req.text,req.participants)
            # 事件引导实际会面；队列依次安排，绝不直接传送。
            if len(req.participants)>1:
                for name in req.participants[1:]:s.request_meeting(req.participants[0],name,trigger=req.text)
            s.emit('world_event',text=req.text,participants=req.participants)
        with s.lock:
            if s.future and not s.future.done():raise HTTPException(409,'上一项维护/事件尚未完成')
            s.future=s.scheduler.maintenance.submit(record)
        return {'ok':True}

    @app.get('/npcs/{name}/memory/history')
    def memory_history(name:str):
        s=get_service()
        if name not in s.npcs:raise HTTPException(404,'未知角色')
        with s.database() as db:
            return [dict(id=row[0],game_time=row[1]) for row in db.execute(
                'SELECT id,game_time FROM m3_memory_history WHERE npc=? ORDER BY id DESC LIMIT 200',(name,))][::-1]

    @app.get('/debug/events')
    def history(after:int=0):return get_service().bus.history(max(0,after))

    @app.get('/debug/runs')
    def runs():
        with get_service().database() as db:
            return [dict(id=r[0],a=r[1],b=r[2],status=r[3],error=r[4]) for r in db.execute('SELECT * FROM m3_runs ORDER BY rowid DESC LIMIT 50')]

    @app.post('/debug/runs/{run_id}/resume')
    def resume(run_id:str):return get_service().resume(run_id)

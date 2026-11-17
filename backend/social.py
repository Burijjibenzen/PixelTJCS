"""M3 装配与查询：LangGraph 调度在 scheduler.py；事件仅经 /ws/events 推送。"""
import asyncio
import json
import math
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from backend.service import GameService
from backend.config import SOCIAL
from backend.world import AREAS, ROOMS, grid_of
from backend.event_stream import EventStream
from backend.scheduler import Scheduler
from backend.playback import dialogue_segments
from backend.daily_planner import DynamicSchedule
from backend.noticeboards import NoticeBoards, BOARDS


class SocialService(GameService):
    def __init__(self,fresh_db=False):
        super().__init__(fresh_db=fresh_db)
        self.reflecting=set();self.seen={}
        self.reading=set()
        self.reading_waiting=set()
        self.notice_approaching=set()
        self.positions={}  # 临时实际坐标，不持久化；与WebSocket心跳在线状态分开。
        self.meetings=[];self.live_lines={};self.future=None
        self._closed=False
        self.paused=False;self.speed=SOCIAL.speed;self.epoch=uuid.uuid4().hex
        self.fast_forward_target=None
        self.resume_speed=SOCIAL.speed
        self.clock_sample=None
        self.maintenance_due=False
        self.bus=EventStream(self.memory.cfg.db_path,SOCIAL.websocket_queue,SOCIAL.event_history_limit)
        self.engine.require_colocated=True
        self.engine.emit=self.emit
        self.engine.gossip_writer=self.write_gossip
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS social_state(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS social_changes(a TEXT,b TEXT,delta REAL,PRIMARY KEY(a,b));
                CREATE TABLE IF NOT EXISTS gossip(root TEXT,holder TEXT,source TEXT,content TEXT,hops INTEGER,memory_id TEXT,PRIMARY KEY(root,holder));
                CREATE TABLE IF NOT EXISTS m3_memory_history(id INTEGER PRIMARY KEY AUTOINCREMENT,npc TEXT,game_time REAL,payload TEXT);
                CREATE INDEX IF NOT EXISTS idx_m3_memory_history_npc_id ON m3_memory_history(npc,id);
            """)
            row=db.execute("SELECT value FROM social_state WHERE key='clock'").fetchone()
            if row:
                self.world.clock.minutes=max(self.world.clock.now,float(row[0]))
                self.world.apply_schedules()
            for key, target in (('npc_area',self.world.npc_area),('last_chat_at',self.world.last_chat_at)):
                saved=db.execute('SELECT value FROM social_state WHERE key=?',(key,)).fetchone()
                if saved:
                    try:
                        values=json.loads(saved[0])
                        if key=='npc_area':
                            target.update({name:area for name,area in values.items()
                                           if name in self.npcs and area in AREAS})
                        else:
                            target.update({tuple(pair):float(at) for pair,at in values
                                           if len(pair)==2 and all(name in self.npcs for name in pair)})
                    except (TypeError,ValueError):
                        pass
            for name in self.npcs:self._prune_memory_history(db,name)
        self.scheduler=Scheduler(self)
        self.planner=DynamicSchedule(self)
        self.boards=NoticeBoards(self)
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
                data['segments']=dialogue_segments(data['text'])
                current=self.live_lines.get(data['conversation_id'])
                if current is None or data['sequence']>current['data']['sequence']:
                    start=time.monotonic()
                    self.live_lines[data['conversation_id']]=dict(data=dict(data),
                        participants=data['participants'],start=start,
                        end=start+sum(segment['seconds'] for segment in data['segments']))
            if kind=='dialogue_finished':
                current=self.live_lines.get(data['conversation_id'])
                if current:current['finished']=True
            if kind=='relation_changed':
                with self.database() as db:
                    db.execute('INSERT OR REPLACE INTO social_changes VALUES(?,?,?)',(*sorted((data['a'],data['b'])),data['delta']))
        if kind=='memory_changed':self.capture_memory(data['npc'])
        return self.bus.publish(kind,game_time=self.world.clock.now,**data)

    def live_dialogue(self):
        """重连只恢复当前仍在显示的最新一句，不重播之前的台词。"""
        with self.lock:
            now=time.monotonic()
            return [dict(line['data'],playback_elapsed=max(0,now-line['start']))
                    for line in self.live_lines.values() if line['end']>now and not line.get('finished')]

    def save_clock(self):
        with self.lock:
            with self.database() as db:
                db.execute("INSERT OR REPLACE INTO social_state VALUES('clock',?)",(str(self.world.clock.now),))
                db.execute("INSERT OR REPLACE INTO social_state VALUES('npc_area',?)",
                           (json.dumps(self.world.npc_area,ensure_ascii=False),))
                db.execute("INSERT OR REPLACE INTO social_state VALUES('last_chat_at',?)",
                           (json.dumps([[list(pair),at] for pair,at in self.world.last_chat_at.items()]),))

    def start(self):self.scheduler.start()
    def stop(self):
        if self._closed:return
        self._closed=True
        self.player.stop()
        self.scheduler.stop()
        self.boards.stop()
        self.planner.stop()
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
                   state='waiting',detail='等待双方空闲',travel_seconds=0.0,
                   wait_seconds=0.0,trigger=trigger)
            self.meetings.append(m)
            return m

    def cancel_meeting(self,key):
        with self.lock:
            m=self.meeting(key)
            if not m:raise HTTPException(404,'会面不存在')
            if m['state'] in ('deciding','talking'):raise HTTPException(409,'已开始处理，请等待结束')
            if m in self.active_meetings():m.update(state='cancelled',detail='已取消')
            return m

    def available(self,a,b):return not ({a,b}&(self.busy|self.reflecting|self.reading|self.reading_waiting|self.notice_approaching))

    def nearby(self,a,b,now=None,allow_legacy=False):
        if a==b or a not in self.npcs or b not in self.npcs:return False
        area=AREAS[self.world.area_of(a)]['id']
        if area!=AREAS[self.world.area_of(b)]['id']:return False
        first,second=self.positions.get(a),self.positions.get(b)
        # 旧接口显式对话/会面继续兼容；自然相遇始终要求双方最新坐标。
        if first is None or second is None:return allow_legacy and first is None and second is None
        now=time.monotonic() if now is None else now
        if any(p['area']!=area or not 0<=now-p['at']<=SOCIAL.position_max_age_seconds for p in (first,second)):
            return False
        return math.hypot(first['x']-second['x'],first['y']-second['y'])<=SOCIAL.encounter_radius

    def valid_pair(self,a,b):
        return (a!=b and a in self.npcs and b in self.npcs and
                self.nearby(a,b,allow_legacy=True) and self.world.can_chat(a,b)[0])

    def assign_destination(self,name,area):
        with self.lock:
            if name not in self.npcs or area not in AREAS:return False
            if name in self.reading or name in self.reading_waiting or name in self.notice_approaching:self.boards.cancel(name)
            return self.planner.command(name,area)

    def report_arrival(self,npc_name,grid_id=None,map_id=None,room_id=None,arrived=None,activity_id=None,x=None,y=None):
        with self.lock:
            if npc_name not in self.npcs:return False,'未知角色'
            sample=None
            if x is not None or y is not None:
                room=ROOMS.get((map_id,room_id))
                if room is None or x is None or y is None or not all(math.isfinite(v) for v in (x,y)):
                    return False,'坐标需要有效的楼层、房间以及有限的x和y'
                sample=dict(area=room['id'],x=x,y=y,at=time.monotonic(),travelling=arrived is False)
            if npc_name in self.busy:
                # 决策期间仍接收实际坐标，不能用预约前的旧位置开始远距离对话。
                if sample:self.positions[npc_name]=sample
                else:self.positions.pop(npc_name,None)
                return True,'busy'
            result=super().report_arrival(npc_name,grid_id,map_id,room_id)
            if result[0]:
                self.seen[npc_name]=time.monotonic()
                if sample:self.positions[npc_name]=sample
                else:self.positions.pop(npc_name,None)
                self.planner.arrived(npc_name,arrived,activity_id)
        if result[0] and result[1]=='moved':
            self.emit('movement',npc=npc_name,area=self.world.area_of(npc_name))
            self.save_clock()
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

    def perform(self,a,b,trigger,run_id,candidate,meeting_id='',require_near=False):
        with self.lock:
            if not self.valid_pair(a,b):raise ValueError('位置或冷却条件已变化')
            if require_near and not self.nearby(a,b):raise ValueError('双方已不在相遇距离内')
            m=self.meeting(meeting_id)
            if m:m.update(state='talking',detail='LangGraph 对话中')
        result=self.engine.run(a,b,trigger,run_id=run_id,gossip=candidate)
        if not result:raise ValueError('对话校验未通过')
        self.finish(a,b,meeting_id,run_id)
        return result

    def _hold_final_bubble(self, run_id):
        # 没有前端观察者时不增加模拟运行时间；有观察者也只等待最后一句的前三秒。
        with self.bus.lock:observed=bool(self.bus.clients)
        if not observed:return
        with self.lock:
            line=self.live_lines.get(run_id)
            deadline=line['start']+SOCIAL.bubble_exit_hold_seconds if line else 0.0
        delay=max(0.0,deadline-time.monotonic())
        if delay:time.sleep(delay)

    def finish(self,a,b,meeting_id='',run_id=''):
        if run_id:self._hold_final_bubble(run_id)
        with self.lock:
            if run_id:self.emit('dialogue_finished',event_key=run_id+':visual_end',
                                conversation_id=run_id,participants=[a,b])
            self.busy.difference_update((a,b))
            m=self.meeting(meeting_id)
            if m:m.update(state='completed',detail='会面完成')
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
            self.finish(a,b,run_id=run_id)
            return result
        finally:
            with self.lock:
                self.busy.difference_update((a,b))

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
                if name in self.busy or name in self.reading or name in self.reading_waiting or name in self.notice_approaching or name in reserved:continue
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

    def step_clock(self,elapsed):
        """按实际经过时间连续推进；到达快进目标后，剩余时间按原倍速运行。"""
        with self.lock:
            if self.paused or self.speed<=0 or elapsed<=0:
                return
            if self.fast_forward_target is not None:
                remaining=max(0,self.fast_forward_target-self.world.clock.now)
                accelerated=min(elapsed,remaining/self.speed)
                self.advance_time(accelerated*self.speed,maintenance=False)
                elapsed-=accelerated
                if self.world.clock.now>=self.fast_forward_target-1e-8:
                    self.speed=self.resume_speed
                    self.fast_forward_target=None
                    self.maintenance_due=True
            self.advance_time(elapsed*self.speed,maintenance=False)

    def sync_clock(self,now=None):
        """切换倍率和暂停前结算上一段现实时间，不能用新倍率回算旧时间。"""
        with self.lock:
            if self.clock_sample is not None:
                now=time.monotonic() if now is None else now
                now=max(now,self.clock_sample)
                self.step_clock(max(0,now-self.clock_sample))
                self.clock_sample=now

    def control_state(self):
        return dict(paused=self.paused,speed=self.speed,base_speed=SOCIAL.speed,target_minutes=self.fast_forward_target)

    def set_control(self,paused=None,speed=None):
        with self.lock:
            self.sync_clock()
            if paused is not None:self.paused=paused
            if speed is not None:
                self.speed=speed
                self.fast_forward_target=None
            result=self.control_state()
        self.emit('world',snapshot=self.world_snapshot())
        return result

    def fast_forward(self,minutes,speed=SOCIAL.fast_forward_speed):
        with self.lock:
            self.sync_clock()
            if self.fast_forward_target is None:self.resume_speed=self.speed
            self.fast_forward_target=self.world.clock.now+minutes
            self.speed=speed
            self.paused=False
            result=dict(self.control_state(),now=self.world.clock.fmt())
        self.emit('world',snapshot=self.world_snapshot())
        return result

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
        signature=json.dumps({section:[{k:v for k,v in item.items() if k!='strength'}
                                       for item in panel[section]]
                              for section in ('active','forgotten','reflections')},sort_keys=True,ensure_ascii=False)
        with self.database() as db:
            previous=db.execute('SELECT payload FROM m3_memory_history WHERE npc=? ORDER BY id DESC LIMIT 1',
                                (name,)).fetchone()
            if previous:
                old=json.loads(previous[0])
                old_signature=json.dumps({section:[{k:v for k,v in item.items() if k!='strength'}
                                                   for item in old[section]]
                                          for section in ('active','forgotten','reflections')},
                                         sort_keys=True,ensure_ascii=False)
                if signature==old_signature:return
            db.execute('INSERT INTO m3_memory_history(npc,game_time,payload) VALUES(?,?,?)',
                       (name,self.world.clock.now,json.dumps(panel,ensure_ascii=False)))
            self._prune_memory_history(db,name)

    @staticmethod
    def _prune_memory_history(db,name):
        threshold=db.execute('SELECT id FROM m3_memory_history WHERE npc=? ORDER BY id DESC LIMIT 1 OFFSET ?',
                             (name,SOCIAL.memory_history_limit)).fetchone()
        if threshold:db.execute('DELETE FROM m3_memory_history WHERE npc=? AND id<=?',(name,threshold[0]))

    def world_snapshot(self):
        with self.lock:
            self.sync_clock()
            result=super().world_snapshot()
            for name,npc in result['npcs'].items():
                npc['busy']=name in self.busy or name in self.reflecting or name in self.reading or name in self.reading_waiting
                npc['activity']=self.planner.describe(name)
                if self.player.engaged_npc==name:
                    npc['activity'].update(title='与玩家交谈',source='player',state='active',state_label='交谈中',
                        reason='玩家主动发起交流',detail='等待玩家输入或回复，完成后继续原来的安排')
                if self.player.pending_npc==name:
                    npc['activity'].update(title='与玩家交谈',source='player',state='active',state_label='交谈中',
                        reason='玩家主动发起交流',detail='正在回复，完成后继续原来的安排')
                if name in self.reading_waiting:
                    npc['activity'].update(title='等待读公告',source='notice',state='waiting',state_label='等待阅读',
                        reason='已安排在公告栏前阅读',detail=self.boards.wait_reason(name))
                if name in self.reading:
                    npc['activity'].update(title='阅读公告',source='notice',state='active',state_label='阅读中',
                        reason='在公告栏前了解校园通知',detail='正在阅读，读完后继续原来的安排')
            targeted=set()
            for m in self.planner.eligible_meetings():
                for name in (m['a'],m['b']):
                    if name in targeted:continue
                    targeted.add(name)
                    if m['area'] is not None:
                        area=AREAS[m['area']]
                        result['npcs'][name].update(target_map_id=area['map_id'],target_room_id=area['room_id'],target_grid=grid_of(area['id']))
            result['social']=dict(meetings=[dict(m) for m in self.meetings],**self.control_state(),epoch=self.epoch,
                                 position_report_seconds=SOCIAL.position_report_seconds,
                                 model_configured=bool(getattr(getattr(self.llm,'cfg',None),'api_key','')))
            for name,trip in self.boards.approaches.items():
                board=BOARDS[trip['board_id']]
                npc=result['npcs'][name]
                npc.update(target_map_id=board['map_id'],target_room_id=board['room_id'],target_grid=grid_of(board['area']),
                    target_position=dict(x=trip['x'],y=trip['y']))
                npc['activity'].update(title='走近公告栏',area=board['area'],source='notice',state='travelling',state_label='正在前往',
                    reason='附近发现未读公告',detail='走到公告栏前阅读，随后继续原安排')
            return result

class Meet(BaseModel):
    a:str
    b:str
    area:str|None=None
class Notice(BaseModel):
    text:str=Field(min_length=1,max_length=500)
    participants:list[str]
class Control(BaseModel):
    paused:bool|None=None
    speed:float|None=Field(default=None,ge=0,le=60,allow_inf_nan=False)

class BoardNotice(BaseModel):
    title:str=Field(min_length=1,max_length=80)
    content:str=Field(min_length=1,max_length=500)

class BoardReader(BaseModel):
    npc:str


def install(app,get_service):
    @app.get('/noticeboards')
    def noticeboards():return {'boards':[dict(b) for b in BOARDS.values()]}

    @app.get('/noticeboards/{board_id}/notices')
    def board_notices(board_id:str):
        s=get_service()
        with s.lock:
            try:return s.boards.listing(board_id)
            except KeyError:raise HTTPException(404,'公告栏不存在')

    @app.post('/noticeboards/{board_id}/notices')
    def publish_notice(board_id:str,req:BoardNotice):
        s=get_service()
        with s.lock:
            try:return s.boards.publish(board_id,req.title,req.content)
            except KeyError:raise HTTPException(404,'公告栏不存在')
            except ValueError as exc:raise HTTPException(400,str(exc))

    @app.post('/noticeboards/{board_id}/read')
    def read_notice(board_id:str,req:BoardReader):
        s=get_service()
        with s.lock:
            try:
                result=s.boards.request_read(req.npc,board_id)
                return dict(ok=True,detail=result['detail'],reading=result)
            except KeyError:raise HTTPException(404,'未知角色或公告栏')
            except ValueError as exc:raise HTTPException(409,str(exc))

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
        live=s.live_dialogue()
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
        return s.set_control(paused=req.paused,speed=req.speed)

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
            with s.lock:
                notice=s.world.inject_event(req.text,req.participants)
                planned=s.planner.notice(req.text,req.participants,notice['key'])
                # 有明确活动含义的通知交给日程；其它群体事件保留原会面队列。
                if not planned and len(req.participants)>1:
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

    @app.get('/npcs/{name}/activities')
    def activities(name:str):
        s=get_service()
        with s.lock:
            if name not in s.npcs:raise HTTPException(404,'未知角色')
            return s.planner.listing(name)

    @app.post('/npcs/{name}/activities/{identity}/cancel')
    def cancel_activity(name:str,identity:str):
        s=get_service()
        with s.lock:
            if name not in s.npcs or identity not in s.planner.records or s.planner.records[identity]['npc']!=name:
                raise HTTPException(404,'活动不存在')
            if not s.planner.cancel(name,identity):raise HTTPException(409,'会面已开始处理，请等待结束')
            return {'ok':True}

    @app.get('/debug/events')
    def history(after:int=0):return get_service().bus.history(max(0,after))

    @app.get('/debug/runs')
    def runs():
        with get_service().database() as db:
            return [dict(id=r[0],a=r[1],b=r[2],status=r[3],error=r[4]) for r in db.execute('SELECT * FROM m3_runs ORDER BY rowid DESC LIMIT 50')]

    @app.post('/debug/runs/{run_id}/resume')
    def resume(run_id:str):return get_service().resume(run_id)

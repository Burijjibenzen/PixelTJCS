"""M3 装配与查询：LangGraph 调度在 scheduler.py；事件仅经 /ws/events 推送。"""
import asyncio
import json
import math
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fastapi import HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from backend.service import GameService
from backend.config import NOTICEBOARD, SOCIAL
from backend.world import AREAS, ROOMS, CastChanged, grid_of
from backend.event_stream import EventStream
from backend.game_events import GameEvent, GameEvents
from backend.event_runtime import EventRuntime
from backend.scheduler import Scheduler
from backend.playback import dialogue_segments
from backend.activity_catalog import CATALOG
from backend.agent_runtime import WhisperAction
from backend.daily_planner import DynamicSchedule
from backend.noticeboards import NoticeBoards, BOARDS, NAV_GRIDS
from backend.logger import get_logger

log = get_logger(__name__)


class SocialService(GameService):
    def __init__(self,fresh_db=False):
        super().__init__(fresh_db=fresh_db)
        self.reflecting=set();self.seen={}
        self.reading=set()
        self.reading_waiting=set()
        self.notice_approaching=set()
        self.positions={}  # 临时实际坐标，不持久化；与WebSocket心跳在线状态分开。
        self.observer_driver = None
        self.observer_driver_until = 0.0
        self.meetings=[];self.live_lines={};self.future=None
        self.heard_dialogue={}
        self.hearing_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='overheard-memory')
        self.active_conversations: dict[str, tuple[str, str]] = {}
        self.cast_versions = dict.fromkeys(self.npcs,0)
        self.cast_context = ContextVar('cast_context',default={})
        self.conversation_context = ContextVar('conversation_context',default=None)
        self.cast_duos = {}
        self._closed=False
        self.paused=False;self.speed=SOCIAL.speed;self.epoch=uuid.uuid4().hex
        self.conversation_condition=threading.Condition(self.lock)
        self.paused_at=None
        self.fast_forward_target=None
        self.resume_speed=SOCIAL.speed
        self.clock_sample=None
        self.maintenance_due=False
        self.bus=EventStream(self.memory.cfg.db_path,SOCIAL.websocket_queue,SOCIAL.event_history_limit)
        self.engine.require_colocated=True
        self.engine.pair_ready=self.ready_pair
        self.engine.emit=self.emit
        self.engine.progress=self.conversation_step
        self.engine.wait_for_playback=self.wait_for_dialogue_playback
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
            row=db.execute("SELECT value FROM social_state WHERE key='world_id'").fetchone()
            self.world_id=row[0] if row else uuid.uuid4().hex
            if row is None:
                db.execute("INSERT INTO social_state VALUES('world_id',?)",(self.world_id,))
            saved=db.execute("SELECT value FROM social_state WHERE key='offstage'").fetchone()
            self.offstage=set(json.loads(saved[0])) & self.npcs.keys() if saved else set()
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
        self.game_events=GameEvents(self.database)
        self.boards=NoticeBoards(self)
        self.event_runtime=EventRuntime(self)
        self.boards.backfill_events()
        self.engine.record_outcome=self._record_conversation_outcome
        for agent in self.engine.agents.values():
            agent.map_perception=self.perception
            agent.progress=self.conversation_step
        for name in self.npcs:self.capture_memory(name)

    @contextmanager
    def database(self):
        conn=self.memory._connect()
        try:
            with conn:yield conn
        finally:conn.close()

    def observe(self,name,content,importance=0.25):
        with self.lock:
            if not self.onstage(name):
                return False
            stamp=self.cast_stamp(name)
        try:
            with self.cast_task(stamp):
                self.npcs[name].observe(content,importance,commit=self.cast_step)
                return True
        except CastChanged:
            return False

    def store_game_event(self, db, event: GameEvent, known_by):
        """写事件和获知范围，并在同一事务中保存角色决定及日程变更。"""
        active=db.execute('SELECT content FROM game_events WHERE id=? AND ended_at IS NULL',(event.id,)).fetchone()
        if active:
            event=replace(event,content=dict(json.loads(active[0]),**event.content))
            self.game_events.conclude(db,event,event.game_time)
            self.event_runtime.release(db,event.id)
        elif not self.game_events.record(db,event):
            return None
        changes=[]
        for name,memory_id in known_by.items():
            self.game_events.learn(db,name,event.id,event.game_time,memory_id)
            if event.kind=='notice_read':
                continue
            decision, updates=self.planner.event_changes(
                name,event.result,event.id,event.game_time,source_event_id=event.id)
            self.game_events.record_reaction(db,name,event.id,self.world.clock.now,decision)
            for item in updates:self.planner.save_activity(db,item)
            changes.extend(updates)
        if event.kind=='conversation_completed':
            self.event_runtime.queue(db,event.id,known_by,'fact')
        return changes

    def apply_game_event(self, event, known_by, changes):
        if changes is None:return
        self.planner.apply_event_changes(changes)
        if known_by:
            self.emit('game_event_recorded',event_key=event.id+':timeline',
                      game_event_id=event.id,actors=list(known_by))

    def _record_conversation_outcome(self, a, b, completed_at, result):
        place=AREAS[result['area']]
        known_by={name:(result['memories'].get(name) or {}).get('id','') for name in (a,b)}
        gossip=result['gossip']
        with self.lock:
            consent=result.get('consent')
            with self.database() as db:
                source_id=None
                if gossip:
                    root=gossip['root']
                    candidate=root if root.startswith('notice:') else 'conversation:'+root
                    if db.execute('SELECT 1 FROM game_events WHERE id=?',(candidate,)).fetchone():
                        source_id=candidate
                event=GameEvent(id='conversation:'+result['conversation_id'],kind='conversation_completed',
                    game_time=completed_at,actor=a,participants=(a,b),
                    map_id=place['map_id'],room_id=place['room_id'],target_type='npc',target_id=b,
                    content={'trigger':result['trigger'],'transcript':result['transcript']},
                    result={'kind':'conversation_completed','relation':result['relation'],
                            'gossip':gossip,'memory_ids':{name:mid for name,mid in known_by.items() if mid},
                            **({'consent':consent} if consent else {})},
                    source_event_id=source_id)
                changes=self.store_game_event(db,event,known_by)
            self.apply_game_event(event,known_by,changes)

    def invitation_line(self,run_id,a,b,topic,transcript,consent=None,*,tier=None,reason=''):
        identity='conversation:'+run_id
        event=self.event_runtime.make(identity,'conversation',a,f'找{b}交谈','npc',b,
                                       purpose=topic,tier=tier,decision_reason=reason)
        participants=[a,b]
        with self.database() as db:
            if self.game_events.focus(db,a)[0]!=identity:
                self.event_runtime.enter(db,event,'waiting')
            db.execute("""UPDATE game_events SET content=json_patch(content,?),result=json_patch(result,?),
                participants=?,map_id=?,room_id=?
                WHERE id=? AND ended_at IS NULL""",
                (json.dumps(dict(transcript=transcript),ensure_ascii=False),
                 json.dumps(dict(consent=consent) if consent else {},ensure_ascii=False),
                 json.dumps(participants,ensure_ascii=False),
                 event.map_id,event.room_id,identity))
        self.active_conversations[run_id]=(a,b)
        self.emit('dialogue_line',event_key=f'{run_id}:line:{len(transcript)}',conversation_id=run_id,
                  sequence=len(transcript),participants=participants,area=self.world.area_of(a),
                  ordered=True,speech='npc_dialogue',game_event_id=identity,**transcript[-1])

    def self_talk(self,actor,event_id,source_id,text):
        self.emit('dialogue_line',event_key=event_id+':self_talk',conversation_id=event_id+':self_talk',
                  sequence=1,participants=[actor],speaker=actor,text='（自言自语）'+text,
                  ordered=True,speech='event_self_talk',game_event_id=event_id,source_event_id=source_id)

    def finish_invitation(self,run_id,a,b,status,detail,consent=None,meeting_id=''):
        self.wait_for_dialogue_playback(run_id)
        identity='conversation:'+run_id
        result=dict(status=status,detail=detail,**({'consent':consent} if consent else {}))
        with self.database() as db:
            if self.event_runtime.close(db,identity,result):
                actors=[row[0] for row in db.execute('SELECT actor FROM event_knowledge WHERE event_id=?',(identity,))]
                self.event_runtime.queue(db,identity,actors,'fact')
        self.event_runtime.changed(identity)
        self.emit('dialogue_finished',event_key=run_id+':visual_end',conversation_id=run_id,participants=[a,b])
        if status in ('declined','deferred'):
            self.world.mark_chatted(a,b)
            self.save_clock()
        if meeting_id:
            self.meeting(meeting_id).update(state='cancelled',detail=detail)

    def emit(self,kind,**data):
        with self.lock:
            if kind=='conversation_started':
                self.active_conversations[data['conversation_id']]=tuple(data['participants'])
                self.begin_dialogue_event(data['conversation_id'],*data['participants'])
            if kind=='dialogue_line':
                data['segments']=dialogue_segments(data['text'])
                current=self.live_lines.get(data['conversation_id'])
                if (data.get('speech') in ('npc_dialogue','event_self_talk')
                        and (current is None or data['sequence']>current['data']['sequence'])):
                    now=time.monotonic()
                    ordered=data.get('ordered') and data.get('speech')=='npc_dialogue'
                    if current:
                        self.capture_playing_lines(current,now)
                    queued=[line for line in current['lines'] if line['end']>now] if ordered and current else []
                    start=queued[-1]['end'] if queued else now
                    line=dict(data=dict(data),
                        participants=data['participants'],start=start,
                        end=start+sum(segment['seconds'] for segment in data['segments']))
                    self.live_lines[data['conversation_id']]=dict(line,lines=queued+[line])
                    with self.bus.lock:
                        has_viewers=bool(self.bus.clients)
                    if ordered and not has_viewers:
                        self.capture_playing_lines({'lines':[line]},start,position_time=now)
                    else:
                        self.capture_playing_lines(self.live_lines[data['conversation_id']],now)
                if data.get('activity')=='player_chat' and self.player.interaction:
                    event=self.player.interaction['event']
                    participants=[event.actor,event.target_id]
                    speaker=data['speaker']
                    position=self.positions.get(speaker) if speaker==event.actor else self.player.position
                    self.capture_heard_line(event.id,participants,position,speaker,data['text'],time.monotonic())
            if kind=='dialogue_finished':
                self.active_conversations.pop(data['conversation_id'],None)
                current=self.live_lines.get(data['conversation_id'])
                if current:
                    self.capture_playing_lines(current,self.paused_at or time.monotonic())
                    current['finished']=True
                self.queue_overheard('conversation:'+data['conversation_id'])
            if kind=='relation_changed':
                with self.database() as db:
                    db.execute('INSERT OR REPLACE INTO social_changes VALUES(?,?,?)',(*sorted((data['a'],data['b'])),data['delta']))
        if kind=='memory_changed':self.capture_memory(data['npc'])
        return self.bus.publish(kind,game_time=self.world.clock.now,**data)

    def capture_playing_lines(self,playback,now,*,position_time=None):
        for line in playback['lines']:
            data=line['data']
            if data.get('speech')!='npc_dialogue' or line.get('sampled'):
                continue
            if line['start']<=now<line['end']:
                line['sampled']=True
                speaker=data['speaker']
                self.capture_heard_line(data['game_event_id'],data['participants'],
                    self.positions.get(speaker),speaker,data['text'],position_time if position_time is not None else now)

    def capture_heard_line(self,source,participants,position,speaker,text,now):
        if not position or not 0<=now-position['at']<=SOCIAL.position_max_age_seconds:
            return
        area=AREAS[position['area']]
        if speaker in self.npcs and self.world.area_of(speaker)!=area['id']:
            return
        floor=area['map_id']
        for name, sample in self.positions.items():
            if (name in participants or not self.onstage(name) or sample['map_id']!=floor
                    or sample['area']!=area['id'] or self.world.area_of(name)!=area['id']
                    or not 0<=now-sample['at']<=SOCIAL.position_max_age_seconds
                    or math.hypot(position['x']-sample['x'],position['y']-sample['y'])>SOCIAL.overhearing_radius):
                continue
            listeners=self.heard_dialogue.setdefault(source,{})
            stamp=self.cast_stamp(name)
            witness=listeners.get(name)
            if not witness or witness['stamp']!=stamp:
                witness=dict(stamp=stamp,area=area['id'],at=self.world.clock.now,lines=[],participants=participants)
                listeners[name]=witness
            witness['lines'].append(f'{speaker}：{text}')

    def queue_overheard(self,source):
        listeners=self.heard_dialogue.pop(source,{})
        if listeners and not self._closed:
            job=self.hearing_pool.submit(self.remember_overheard,source,listeners)
            job.add_done_callback(self.report_overhearing_error)

    @staticmethod
    def report_overhearing_error(job):
        if not job.cancelled() and job.exception() is not None:
            log.error('旁听记忆保存失败',exc_info=job.exception())

    def remember_overheard(self,source,listeners):
        for name, witness in listeners.items():
            area=AREAS[witness['area']]
            identity=f'overheard:{source}:{name}'
            evidence='\n'.join(witness['lines'])
            try:
                with self.cast_task(witness['stamp']):
                    with self.cast_step():
                        pass
                    item=self.npcs[name].remember(evidence,perspective='你只是路过旁听，以下是你听到的片段',
                        kind='overheard',game_time=witness['at'],location=area['id'],importance=0.35,
                        participants=witness['participants'],memory_id=identity,tags=['overheard',source],
                        fallback=f'我在附近听到几句话。\n{evidence}',commit=self.cast_step)
                    event=GameEvent(id=identity,kind='conversation_overheard',game_time=witness['at'],
                        actor=name,participants=(name,),map_id=area['map_id'],room_id=area['room_id'],
                        target_type='conversation',target_id=source,source_event_id=source,
                        content={'title':'旁听交谈'},result={'detail':item.content})
                    with self.cast_step():
                        with self.database() as db:
                            if not self.game_events.record(db,event):
                                continue
                            self.game_events.learn(db,name,event.id,event.game_time,item.id)
                        self.apply_game_event(event,{name:item.id},[])
                        self.emit('memory_changed',event_key=identity+':memory',npc=name)
            except CastChanged:
                continue

    def begin_dialogue_event(self, run_id, a, b):
        identity='conversation:'+run_id
        event=self.event_runtime.make(identity,'conversation',a,f'与{b}交谈','npc',b)
        with self.database() as db:
            row=db.execute('SELECT ended_at FROM game_events WHERE id=?',(identity,)).fetchone()
            if row is None or row[0] is None:
                self.event_runtime.enter(db,replace(event,participants=(a,b)),'active')
        self.planner.finish_encounter(run_id,'completed','双方已到位，开始交谈')

    def live_dialogue(self):
        """重连恢复正在播放和还未显示的台词。"""
        with self.lock:
            now=self.paused_at if self.paused_at is not None else time.monotonic()
            return [dict(line['data'],playback_elapsed=max(0,now-line['start']))
                    for playback in self.live_lines.values() if not playback.get('finished')
                    for line in playback['lines'] if line['end']>now]

    def wait_for_dialogue_playback(self,run_id):
        with self.bus.lock:
            observed=bool(self.bus.clients)
        if not observed:
            return
        with self.conversation_step():
            while True:
                playback=self.live_lines.get(run_id)
                remaining=playback['end']-time.monotonic() if playback else 0
                if remaining<=0:
                    return
                self.conversation_condition.wait(timeout=remaining)
                with self.conversation_step():
                    pass

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
        with self.conversation_condition:
            self._closed=True
            self.conversation_condition.notify_all()
        self.player.stop()
        self.scheduler.stop()
        self.hearing_pool.shutdown(wait=True, cancel_futures=True)
        self.boards.stop()
        self.planner.stop()
        self.event_runtime.stop()
        self.save_clock()
        self.engine.close()

    def heartbeat(self):
        with self.lock:
            for name in self.seen.keys()-self.offstage:self.seen[name]=time.monotonic()

    def claim_observer_driver(self, token):
        with self.lock:
            now = time.monotonic()
            if self.player.session:
                return False
            if self.observer_driver != token and now < self.observer_driver_until:
                return False
            self.observer_driver = token
            self.observer_driver_until = now + 8.0
            return True

    def can_report_web_positions(self, session, observer):
        with self.lock:
            if self.player.session:
                return self.player.owns_session(session)
            return bool(observer and observer == self.observer_driver and time.monotonic() < self.observer_driver_until)

    def revoke_observer_driver(self):
        self.observer_driver = None
        self.observer_driver_until = 0.0

    def active_meetings(self):
        return [m for m in self.meetings if m['state'] not in ('completed','cancelled','failed')]

    def meeting(self,key):
        return next((m for m in self.meetings if m['id']==key),None)

    def request_meeting(self,a,b,area=None,trigger='用户安排会面，聊聊近况',*,activity_id=None,tier=None):
        with self.lock:
            if a==b or not self.onstage(a) or not self.onstage(b):raise HTTPException(400,'请选择两个正在出演的不同角色')
            if area is not None and area not in AREAS:raise HTTPException(400,'未知区域')
            for m in self.active_meetings():
                if {a,b}=={m['a'],m['b']}:return m
            if len(self.active_meetings())>=50:raise HTTPException(409,'会面队列已满')
            self.meetings=self.active_meetings()+[m for m in self.meetings if m not in self.active_meetings()][-50:]
            m=dict(id=uuid.uuid4().hex,a=a,b=b,area=AREAS[area]['id'] if area else None,
                   state='waiting',detail='等待双方空闲',travel_seconds=0.0,
                   wait_seconds=0.0,trigger=trigger)
            if activity_id is not None:
                m['activity_id']=activity_id
            if tier is not None:
                m['tier']=tier
            self.meetings.append(m)
            return m

    def cancel_meeting(self,key):
        with self.lock:
            m=self.meeting(key)
            if not m:raise HTTPException(404,'会面不存在')
            if m['state'] in ('deciding','talking'):raise HTTPException(409,'已开始处理，请等待结束')
            if m in self.active_meetings():m.update(state='cancelled',detail='已取消')
            return m

    def onstage(self,name):
        return name in self.npcs and name not in self.offstage

    def cast_stamp(self,*names):
        return {name:self.cast_versions[name] for name in names}

    def cast_matches(self,stamp):
        return all(self.onstage(name) and self.cast_versions[name]==version for name,version in stamp.items())

    @contextmanager
    def cast_task(self,stamp,*,conversation=None):
        token=self.cast_context.set(stamp)
        conversation_token=self.conversation_context.set(conversation)
        try:
            yield
        finally:
            self.conversation_context.reset(conversation_token)
            self.cast_context.reset(token)

    def _interrupt_conversation(self,run,reason,status,*,resume_meeting=False):
        with self.conversation_condition:
            pair,_,meeting_id=self.cast_duos.pop(run)
            self.busy.difference_update(pair)
            self.live_lines.pop(run,None)
            meeting=self.meeting(meeting_id)
            if meeting:
                meeting.update(state='waiting' if resume_meeting else 'cancelled',detail=reason)
            self.event_runtime.end_external('conversation:'+run,status,reason)
            self.emit('dialogue_finished',conversation_id=run,participants=list(pair),interrupted=True)
            self.conversation_condition.notify_all()

    def preempt_conversation(self,name,reason,*,before_speech=False,player_priority=False):
        for run,(pair,tier,_) in self.cast_duos.items():
            if name not in pair:
                continue
            if before_speech and (tier=='high' or run in self.active_conversations or run in self.live_lines
                                  or run in self.scheduler.event_talks):
                return False
            if not player_priority and tier=='high' and self.planner.encounter_waits.get(name)!=run:
                return False
            self._interrupt_conversation(run,reason,'interrupted',resume_meeting=before_speech or player_priority)
            future=self.scheduler.event_talks.pop(run,None)
            if future is not None:
                future.cancel()
                item=self.planner.records[run]
                reverse=self.planner.current_item(item['partner'])
                if self.planner.current_item(item['npc']) is item:
                    self.planner.finish_event_talk(item,'cancelled',reason)
                if reverse and reverse['partner']==item['npc'] and reverse['tier']!='high':
                    self.planner.finish_event_talk(reverse,'cancelled',reason,shared_event_id='conversation:'+run)
            return True
        return not (before_speech or player_priority) and self.player.interrupt(name,reason)

    def take_player_focus(self, name):
        reason = '玩家发起交谈，暂缓原安排'
        self.preempt_conversation(name, reason, player_priority=True)
        for pursuit_id, (event, _) in list(self.planner.encounter_requests.items()):
            if name in (event.actor, event.target_id):
                self.planner.finish_encounter(pursuit_id, 'interrupted', reason)
        self.boards.suspend_for_encounter(name, time.monotonic())

    @contextmanager
    def cast_step(self):
        with self.lock:
            if not self.cast_matches(self.cast_context.get()):
                raise CastChanged('角色已退场，本次行动已中断')
            yield

    def set_cast(self,name,onstage):
        with self.conversation_condition:
            if name not in self.npcs:
                raise HTTPException(404,'角色不存在')
            if self.onstage(name)==onstage:
                return
            self.sync_clock()
            self.cast_versions[name]+=1
            self.positions.pop(name,None)
            self.seen.pop(name,None)
            if onstage:
                self.offstage.remove(name)
            else:
                self.offstage.add(name)
                self.conversation_condition.notify_all()
                self.boards.cancel(name)
                self.player.interrupt(name,'角色退场，对话中断')
                for meeting in self.active_meetings():
                    if name in (meeting['a'],meeting['b']):
                        meeting.update(state='cancelled',detail='参与角色退场')
                for run,(pair,_,_) in list(self.cast_duos.items()):
                    if name in pair:
                        self._interrupt_conversation(run,'参与角色退场','cancelled')
                for run,line in list(self.live_lines.items()):
                    if name in line['participants']:
                        self.live_lines.pop(run)
                self.planner.remove_actor(name)
                self.event_runtime.pause_actor(name)
                self.reflecting.discard(name)
                self.busy.discard(name)
            kind,title=('stage_enter','出演') if onstage else ('stage_exit','退场')
            event=self.event_runtime.make(uuid.uuid4().hex,kind,name,title,'npc',name,
                                          purpose='恢复世界中的行动' if onstage else '暂停世界中的行动')
            with self.database() as db:
                db.execute("INSERT OR REPLACE INTO social_state VALUES('offstage',?)",
                           (json.dumps(sorted(self.offstage),ensure_ascii=False),))
                if onstage:
                    previous,_=self.game_events.focus(db,name)
                    self.event_runtime.close(db,previous,dict(status='completed',detail='角色重新出演'))
                    self.game_events.record(db,replace(event,result=dict(status='completed',detail='在离场地点重新出演')))
                else:
                    self.event_runtime.enter(db,event,'active')
                self.game_events.learn(db,name,event.id,self.world.clock.now,'')
            self.event_runtime.changed(event.id)
            self.emit('world',snapshot=self.world_snapshot())

    def available(self,a,b):
        return self.onstage(a) and self.onstage(b) and not ({a,b}&(
            self.busy|self.reflecting|self.reading|self.reading_waiting|self.notice_approaching))

    def perception(self, name):
        """给角色当前处境和可接触对象，按实际位置与上报有效期计算。"""
        with self.lock:
            now=time.monotonic()
            sample=self.positions.get(name)
            fresh=bool(sample and 0<=now-sample['at']<=SOCIAL.position_max_age_seconds)
            area=AREAS[sample['area'] if fresh else self.world.area_of(name)]
            location={key:area[key] for key in ('map_id','room_id','name')}
            location['fresh']=fresh
            nearby=[]
            objects=[]
            if fresh:
                location.update(x=sample['x'],y=sample['y'])
                for other, position in self.positions.items():
                    if other==name or position['area']!=area['id'] or not 0<=now-position['at']<=SOCIAL.position_max_age_seconds:
                        continue
                    distance=math.hypot(sample['x']-position['x'],sample['y']-position['y'])
                    if distance<=SOCIAL.perception_radius:
                        nearby.append(dict(name=other,distance=round(distance,1)))
                for board in BOARDS.values():
                    distance=math.hypot(sample['x']-board['x'],sample['y']-board['y'])
                    if self.boards.in_range(name,board['id'],now,NOTICEBOARD.discovery_radius):
                        objects.append(dict(id=board['id'],type='noticeboard',name=board['name'],
                                            distance=round(distance,1),in_read_range=distance<=NOTICEBOARD.read_radius))
            return dict(game_time=self.world.clock.now,time=self.world.clock.fmt(), location=location,
                        nearby_characters=nearby,nearby_objects=objects)

    def nearby(self,a,b,now=None):
        if a==b or not self.onstage(a) or not self.onstage(b):return False
        area=AREAS[self.world.area_of(a)]['id']
        if area!=AREAS[self.world.area_of(b)]['id']:return False
        first,second=self.positions.get(a),self.positions.get(b)
        if first is None or second is None:return False
        now=time.monotonic() if now is None else now
        floor=AREAS[area]['map_id']
        if any(p['map_id']!=floor or p['area']!=area or not 0<=now-p['at']<=SOCIAL.position_max_age_seconds
               for p in (first,second)):
            return False
        return math.hypot(first['x']-second['x'],first['y']-second['y'])<=SOCIAL.encounter_radius

    def valid_pair(self,a,b):
        return self.ready_pair(a,b) and self.world.can_chat(a,b)[0]

    def ready_pair(self,a,b):
        return self.nearby(a,b) and not any(self.positions[n]['travelling'] for n in (a,b))

    def interaction_point(self,actor,partner,now):
        target=self.positions.get(partner)
        if target is None or not 0<=now-target['at']<=SOCIAL.position_max_age_seconds:
            return None
        room=AREAS[self.world.area_of(partner)]
        if target['area']!=room['id']:
            return None
        grid,codes=NAV_GRIDS[room['map_id']]
        size=grid['tile_size']
        ox,oy=grid['origin']
        x,y=math.floor(target['x']/size)-ox,math.floor(target['y']/size)-oy
        origin=self.positions.get(actor)
        candidates=[]
        tiles=math.ceil(SOCIAL.encounter_radius/size)
        for ny in range(max(0,y-tiles),min(grid['size'][1],y+tiles+1)):
            for nx in range(max(0,x-tiles),min(grid['size'][0],x+tiles+1)):
                if (nx,ny)==(x,y) or grid['obstacle'][ny][nx]!='0' or grid['room_grid'][ny][nx]!=codes[room['room_id']]:
                    continue
                point=((nx+ox+.5)*size,(ny+oy+.5)*size)
                if size/2<=math.hypot(point[0]-target['x'],point[1]-target['y'])<=SOCIAL.encounter_radius:
                    candidates.append(point)
        if not candidates:
            return None
        if origin and origin['area']==target['area']:
            candidates.sort(key=lambda p:math.hypot(p[0]-origin['x'],p[1]-origin['y']))
        px,py=candidates[0]
        return dict(x=px,y=py)

    def assign_destination(self,name,area):
        with self.lock:
            if not self.onstage(name) or area not in AREAS:return False
            if name in self.reading or name in self.reading_waiting or name in self.notice_approaching:self.boards.cancel(name)
            return self.planner.command(name,area)

    def report_arrival(self,npc_name,grid_id=None,map_id=None,room_id=None,arrived=None,activity_id=None,x=None,y=None,
                       encounter_wait=None,cast_version=None):
        with self.lock:
            if not self.onstage(npc_name):return False,'角色退场或不存在'
            if cast_version is not None and cast_version!=self.cast_versions[npc_name]:
                return False,'旧的角色位置已失效'
            sample=None
            if x is not None or y is not None:
                room=ROOMS.get((map_id,room_id))
                if room is None or x is None or y is None or not all(math.isfinite(v) for v in (x,y)):
                    return False,'坐标需要有效的楼层、房间以及有限的x和y'
                waiting=encounter_wait if encounter_wait==self.planner.encounter_waits.get(npc_name) else None
                sample=dict(area=room['id'],map_id=map_id,x=x,y=y,at=time.monotonic(),
                            travelling=arrived is not True and not waiting,encounter_wait=waiting)
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
                if npc_name not in self.planner.encounter_waits and not encounter_wait:
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
            run=uuid.uuid4().hex
            self.cast_duos[run]=((a,b),'high','')
            stamp=self.cast_stamp(a,b)
        return self.scheduler.execute(dict(a=a,b=b,trigger=trigger,forced=True,meeting_id='',
                                           conversation_id=run,tier='high'),stamp)

    def perform(self,a,b,trigger,run_id,candidate,meeting_id='',require_near=False,invitation=None,tier=None,reason=''):
        with self.conversation_step():
            if not self.valid_pair(a,b):raise ValueError('位置或冷却条件已变化')
            if require_near and not self.nearby(a,b):raise ValueError('双方已不在相遇距离内')
            m=self.meeting(meeting_id)
            if m:m.update(state='talking',detail='LangGraph 对话中')
            if tier:
                event=self.event_runtime.make('conversation:'+run_id,'conversation',a,f'与{b}交谈','npc',b,
                                               tier=tier,decision_reason=reason)
                with self.database() as db:
                    self.game_events.begin(db,event,'scheduled')
        result=self.engine.run(a,b,trigger,run_id=run_id,gossip=candidate,invitation=invitation)
        if not result:raise ValueError('对话校验未通过')
        self.finish(a,b,meeting_id,run_id)
        return result

    def finish(self,a,b,meeting_id='',run_id=''):
        with self.conversation_step():
            if run_id:self.emit('dialogue_finished',event_key=run_id+':visual_end',
                                conversation_id=run_id,participants=[a,b])
            m=self.meeting(meeting_id)
            if m:m.update(state='completed',detail='会面完成')
        self.save_clock()

    def fail(self,run_id,a,b,error,meeting_id=''):
        with self.lock:
            self.active_conversations.pop(run_id,None)
            self.live_lines.pop(run_id,None)
            m=self.meeting(meeting_id)
            if m:m.update(state='failed',detail='对话失败：'+error+'；可从检查点恢复')
            # 对话检查点仍可恢复；先释放参与者的运行位置，完成时沿用原事件 ID。
            with self.database() as db:
                self.event_runtime.interrupt_dialogue(db,'conversation:'+run_id)
            self.queue_overheard('conversation:'+run_id)
        self.emit('error',conversation_id=run_id,participants=[a,b],message=f'{a}与{b}对话失败：{error}')

    def resume(self,run_id):
        snapshot=self.engine.graph.get_state({'configurable':{'thread_id':run_id}})
        if not snapshot.values:raise HTTPException(404,'未知检查点')
        a,b=snapshot.values['a'],snapshot.values['b']
        with self.lock:
            with self.database() as db:
                ended=db.execute('SELECT ended_at,content FROM game_events WHERE id=?',('conversation:'+run_id,)).fetchone()
            if snapshot.next and ended and ended[0] is not None:raise HTTPException(409,'该对话已结束')
            if self.paused or not self.available(a,b) or len(self.busy)>=SOCIAL.max_conversations*2:raise HTTPException(409,'角色或系统忙碌')
            if any(AREAS[self.world.area_of(n)]['id']!=snapshot.values['area'] for n in (a,b)):raise HTTPException(409,'请先让双方返回原会面区域')
            if snapshot.next and not self.ready_pair(a,b):raise HTTPException(409,'请先让双方抵达交谈位置')
            self.busy.update((a,b))
            tier=json.loads(ended[1]).get('tier','medium') if ended else 'medium'
            self.cast_duos[run_id]=((a,b),tier,'')
            stamp=self.cast_stamp(a,b)
            if snapshot.next:
                self.active_conversations[run_id]=(a,b)
                self.begin_dialogue_event(run_id,a,b)
        try:
            with self.cast_task(stamp,conversation=run_id):
                result=self.engine.resume(run_id)
                self.finish(a,b,run_id=run_id)
                return result
        except CastChanged as error:
            raise HTTPException(409,str(error)) from error
        finally:
            with self.lock:
                if self.cast_duos.pop(run_id,None) is not None:
                    self.active_conversations.pop(run_id,None)
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
            item=self.npcs[b].remember(candidate['content'],perspective=f'你听{a}转述了一件事',
                kind='gossip',importance=0.35,game_time=state['time'],location=state['area'],participants=[a],
                fallback=f"听{a}说：{candidate['content']}",
                tags=['gossip',candidate['root'],f'source:{a}'],memory_id=mid,commit=self.conversation_step)
            with self.conversation_step():
                with self.database() as db:
                    db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',
                               (candidate['root'],b,a,candidate['content'],candidate['hops']+1,item.id))
                self.emit('memory_changed',event_key=state['id']+':gossip-memory',npc=b)
            return dict(root=candidate['root'],source=a,recipient=b,hops=candidate['hops']+1)
        for name in (a,b):
            item=state['memories'].get(name)
            if item:
                with self.conversation_step(), self.database() as db:
                    db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',
                        (state['id'],name,name,f"{name}的经历：{item['content']}",0,item['id']))
        return {}

    def maintain(self):
        for name in self.npcs:
            with self.lock:
                reserved={n for m in self.active_meetings() for n in (m['a'],m['b'])} | self.planner.encounter_participants()
                if self.scheduler.stop_event.is_set():break
                if not self.available(name,name) or name in reserved:continue
                self.reflecting.add(name)
                stamp=self.cast_stamp(name)
            try:
                with self.cast_task(stamp):
                    self.engine.agents[name].graph.invoke({'task':'reflect'})
            except CastChanged:
                continue
            finally:
                with self.lock:
                    if self.cast_matches(stamp):
                        self.reflecting.discard(name)
        with self.lock:
            self.memory.full_forget_pass(self.world.clock.now,exclude=self.offstage)
        with self.lock:
            for name in self.npcs:
                if self.onstage(name):self.emit('memory_changed',npc=name)
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

    @contextmanager
    def conversation_step(self):
        with self.conversation_condition:
            stamp=self.cast_context.get()
            run=self.conversation_context.get()
            self.conversation_condition.wait_for(lambda: not self.paused or self._closed
                                                or not self.cast_matches(stamp) or (run and run not in self.cast_duos))
            if self._closed:
                raise RuntimeError('世界已关闭')
            if run and run not in self.cast_duos:
                raise CastChanged('对话已中断')
            with self.cast_step():
                yield

    def set_control(self,paused=None,speed=None):
        with self.lock:
            self.sync_clock()
            if paused is not None and paused!=self.paused:
                now=time.monotonic()
                self.paused=paused
                if paused:
                    self.paused_at=now
                else:
                    duration=now-self.paused_at
                    for playback in self.live_lines.values():
                        playback['start']+=duration
                        playback['end']+=duration
                        for line in playback['lines']:
                            line['start']+=duration
                            line['end']+=duration
                    self.paused_at=None
                    self.conversation_condition.notify_all()
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
            current_events=self.game_events.current()
            partners={name: other for a,b in self.active_conversations.values() for name,other in ((a,b),(b,a))}
            for name,npc in result['npcs'].items():
                npc['on_stage']=self.onstage(name)
                npc['cast_version']=self.cast_versions[name]
                npc['busy']=name in self.busy or name in self.reflecting or name in self.reading or name in self.reading_waiting
                npc['player_engaged']=self.player.holds(name)
                position=self.positions.get(name)
                if position:
                    npc['position']={key:position[key] for key in ('map_id', 'x', 'y')}
                npc['activity']=self.planner.describe(name)
                npc['current_event']=current_events[name]
                if name in partners:
                    npc['conversation_partner']=partners[name]
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
            now=time.monotonic()
            occupied=self.reading|self.reading_waiting|self.notice_approaching|self.planner.encounter_participants()
            for m in self.planner.eligible_meetings():
                if {m['a'],m['b']}&occupied:
                    continue
                anchor=next((name for name in (m['a'],m['b']) if (sample:=self.positions.get(name))
                             and sample['area']==m['area'] and not sample['travelling']
                             and 0<=now-sample['at']<=SOCIAL.position_max_age_seconds),None)
                close_enough=anchor is not None and self.nearby(m['a'],m['b'],now)
                for name in (m['a'],m['b']):
                    if name in targeted:continue
                    targeted.add(name)
                    if m['area'] is not None:
                        area=AREAS[m['area']]
                        result['npcs'][name].update(target_map_id=area['map_id'],target_room_id=area['room_id'],target_grid=grid_of(area['id']))
                        if name==anchor or close_enough:
                            result['npcs'][name]['hold_position']=True
                        elif anchor is not None:
                            point=self.interaction_point(name,anchor,now)
                            if point:
                                result['npcs'][name]['target_position']=point
            result['social']=dict(meetings=[dict(m) for m in self.meetings],**self.control_state(),epoch=self.epoch,
                                 world_id=self.world_id,
                                 player_occupied=self.player.session is not None,
                                 position_report_seconds=SOCIAL.position_report_seconds,
                                 model_configured=bool(getattr(getattr(self.llm,'cfg',None),'api_key','')))
            player_position = self.player.position
            if self.player.session and player_position:
                result['social']['player_position'] = dict(
                    map_id=player_position['map_id'],
                    x=player_position['x'], y=player_position['y'])
            for name,trip in self.boards.approaches.items():
                board=BOARDS[trip['board_id']]
                npc=result['npcs'][name]
                npc.update(target_map_id=board['map_id'],target_room_id=board['room_id'],target_grid=grid_of(board['area']),
                    target_position=dict(x=trip['x'],y=trip['y']))
                npc['activity'].update(title='走近公告栏',area=board['area'],source='notice',state='travelling',state_label='正在前往',
                    reason='附近发现未读公告',detail='走到公告栏前阅读，随后继续原安排')
            for name,npc in result['npcs'].items():
                item=self.planner.current_item(name)
                if name in self.planner.encounter_waits:
                    key=self.planner.encounter_waits[name]
                    npc.update(encounter_wait=key,encounter_wait_for=self.planner.records[key]['npc'])
                if item and item.get('hold_position') and item['state'] in ('active','waiting'):
                    npc['hold_position']=True
                if (not item or item['source'] not in ('event_followup','whisper') or not item['partner']
                        or npc['current_event']['id']!=self.event_runtime.activity_id(item)
                        or 'target_position' in npc):
                    continue
                area=AREAS[item['area']]
                if item.get('encounter_stop'):
                    npc['hold_position']=True
                point=item.get('interaction_point')
                if point:
                    npc.update(target_map_id=area['map_id'],target_room_id=area['room_id'],
                               target_grid=grid_of(area['id']),target_position=point)
            return result

class CastRequest(BaseModel):
    npc:str
    on_stage:bool
    world_id:str

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
    content:str=Field(min_length=1,max_length=500)

class BoardReader(BaseModel):
    npc:str

class WhisperRequest(BaseModel):
    npc:str
    text:str=Field(min_length=1,max_length=500,pattern=r'\S')


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
            try:return s.boards.publish(board_id,req.content)
            except KeyError:raise HTTPException(404,'公告栏不存在')
            except ValueError as exc:raise HTTPException(400,str(exc))

    @app.delete('/noticeboards/{board_id}/notices/{notice_id}')
    def delete_notice(board_id:str,notice_id:str):
        s=get_service()
        with s.lock:
            try:s.boards.delete(board_id,notice_id)
            except KeyError:raise HTTPException(404,'公告不存在或已删除')
            return s.boards.listing(board_id)

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
        finally:await asyncio.to_thread(get_service().stop)
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
        if s._closed:
            s.bus.unsubscribe(queue)
            await ws.close(code=1012)
            return
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
                if (not ws.scope.get('pixeltjcs.web')
                        and isinstance(data,dict) and data.get('kind')=='ping'):
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
                if event['kind']=='world_reset':
                    await ws.send_json(event)
                    await ws.close(code=1012)
                    break
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

    @app.post('/social/visit')
    def visit(req:Meet):
        s=get_service()
        with s.lock:
            if req.a==req.b or not s.onstage(req.a) or not s.onstage(req.b):
                raise HTTPException(400,'请选择两个正在出演的不同角色')
            item=s.planner.queue_social_visit(req.a,req.b,'聊聊近况',directed=True)
            return dict(ok=True,activity=s.planner.public(item))

    @app.post('/world/cast')
    def cast(req:CastRequest):
        s=get_service()
        with s.lock:
            if s._closed or req.world_id!=s.world_id:
                raise HTTPException(409,'世界已经更新')
            s.set_cast(req.npc,req.on_stage)
            return dict(ok=True,world=s.world_snapshot())

    @app.get('/world/whisper/history')
    def whisper_history():
        s=get_service()
        with s.lock,s.database() as db:
            rows=db.execute('SELECT activity_id,npc,text,action,game_time FROM whisper_history ORDER BY game_time DESC,rowid DESC')
            return dict(history=[dict(activity_id=key,npc=npc,text=text,action=action,game_time=at)
                                 for key,npc,text,action,at in rows])

    @app.post('/world/whisper')
    def whisper(req:WhisperRequest):
        s=get_service()
        with s.lock:
            if not s.onstage(req.npc):
                raise HTTPException(409,'角色退场或不存在')
            stamp=s.cast_stamp(req.npc)
            people=[name for name in s.npcs if s.onstage(name)]
        places=[dict(id=room['id'],name=room['name']) for room in ROOMS.values()]
        activities=[dict(id=key,name=item['title'],areas=item['rooms'])
                    for key,item in CATALOG.items() if key!='visit']
        action=s.llm.chat_json(
            '把世界操作者给指定角色的自然语言指令转换为一种可执行动作。角色本人没有拒绝指令的环节。'
            'go_to 前往地点，stay 原地停留（也能延长当前停留），talk_to 寻找指定 NPC 交谈，'
            'perform_at 去地点做活动。只使用提供的角色、地点和活动 ID。'
            '若最接近的可执行动作是前往地点，用 go_to。只有明确要求停留时才用 stay。'
            '若无法判断有效动作，返回 go_to 并将 area 留空。'
            'purpose 只简述要做的事或面谈话题。minutes 是停留的游戏分钟。'
            '\n'+json.dumps(dict(actor=req.npc,people=people,places=places,activities=activities),
                            ensure_ascii=False),
            req.text,WhisperAction,timeout=12)
        with s.lock:
            if s._closed:
                raise HTTPException(409,'世界正在重置，请在新世界重新发出指令')
            if not s.cast_matches(stamp):
                raise HTTPException(409,'角色已退场，本次悄悄话取消')
            if action is None:
                raise HTTPException(503,'模型暂时无法解析悄悄话')
            try:item=s.planner.whisper(req.npc,action,req.text.strip())
            except ValueError as error:raise HTTPException(422,str(error)) from error
            return dict(ok=True,activity=s.planner.public(item))

    @app.post('/social/event')
    @app.post('/debug/event')
    def event(req:Notice):
        s=get_service()
        def record():
            participants=[name for name in req.participants if s.observe(name,req.text,0.4)]
            with s.lock:
                participants=[name for name in participants if s.onstage(name)]
                if not participants:
                    return
                for name in participants:s.emit('memory_changed',npc=name)
                notice=s.world.inject_event(req.text,participants)
                planned=s.planner.notice(req.text,participants,notice['key'])
                if not planned and len(participants)>1:
                    for name in participants[1:]:s.request_meeting(participants[0],name,trigger=req.text)
                s.emit('world_event',text=req.text,participants=participants)
        with s.lock:
            if any(not s.onstage(n) for n in req.participants):raise HTTPException(400,'角色退场或不存在')
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

    @app.get('/npcs/{name}/timeline')
    def timeline(name: str, before: str | None = Query(None,max_length=256),
                 limit: int = Query(30,ge=1,le=100)):
        s=get_service()
        if name not in s.npcs:raise HTTPException(404,'未知角色')
        try:
            return s.game_events.timeline(name,before,limit)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

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

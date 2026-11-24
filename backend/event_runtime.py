"""运行事件和结果使用同一条 GameEvent；活动调度器只负责移动和计时。"""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend.agent_runtime import EventAssessment
from backend.config import ACTIVITY
from backend.game_events import GameEvent
from backend.world import AREAS
from pydantic import ValidationError

log = logging.getLogger(__name__)


class EventRuntime:
    def __init__(self, service):
        self.s = service
        self.store = service.game_events
        self.pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='event-outcome')
        self.jobs = {}
        self.deferred_assessments = {}
        with service.database() as db:
            db.execute("""UPDATE event_assessments SET status='remembered',payload='{}'
                WHERE status='pending' AND event_id IN (SELECT id FROM game_events WHERE kind='notice_read')""")
            last = db.execute('''SELECT MAX(at) FROM (
                SELECT MAX(MAX(game_time,COALESCE(ended_at,game_time))) AS at FROM game_events
                UNION ALL SELECT MAX(started_at) FROM npc_event_spans)''').fetchone()[0]
            if last is not None:
                service.world.clock.minutes = max(service.world.clock.now,last)
            # Close the live focus of any character removed since the save was made.
            removed = [(name,) for name, in db.execute('SELECT actor FROM npc_event_focus')
                       if name not in service.npcs]
            db.executemany('DELETE FROM npc_event_focus WHERE actor=?', removed)
            db.executemany('UPDATE npc_event_spans SET ended_at=? WHERE actor=? AND ended_at IS NULL',
                           [(service.world.clock.now, name) for name, in removed])
            for name in service.npcs:
                identity,_ = self.store.focus(db,name)
                if identity is None:
                    self.idle(db,name)
            # 临时会话和客户端接近动作不能在进程间延续。已提交的阅读和活动等待恢复。
            for identity,kind,content in db.execute('SELECT id,kind,content FROM game_events WHERE ended_at IS NULL').fetchall():
                data = json.loads(content)
                if kind=='conversation' and db.execute('SELECT 1 FROM m3_runs WHERE id=?',(identity.removeprefix('conversation:'),)).fetchone():
                    self.interrupt_dialogue(db,identity)
                    item=service.planner.records.get(data.get('activity_id'))
                    if item:
                        item.update(state='cancelled',detail='对话中断，等待检查点恢复')
                        service.planner.save_activity(db,item)
                        if service.planner.current.get(item['npc'])==item['id']:
                            service.planner.current.pop(item['npc'],None)
                    continue
                if kind=='conversation' and data.get('transcript'):
                    self.close(db,identity,{'status':'cancelled','detail':'服务重启，当面邀约中断'})
                    continue
                if kind in ('idle','stage_exit') or data.get('activity_id') in service.planner.records:
                    continue
                if kind == 'notice_read' and db.execute(
                    """SELECT 1 FROM board_read_events m JOIN board_reads r ON r.npc=m.npc AND r.notice=m.notice
                       WHERE m.event_id=? AND r.status='pending'""",(identity,)
                ).fetchone():
                    self.release(db,identity)
                    continue
                self.close(db,identity,{'status':'interrupted','detail':'服务重启，会话已中断'})
            for item in service.planner.records.values():
                if item['state'] in ('waiting','travelling','active','evaluating','suspended'):
                    event = self.activity(item)
                    existing = db.execute('SELECT ended_at,result,content FROM game_events WHERE id=?',(event.id,)).fetchone()
                    if existing is None:
                        self.enter(db,event,item['state'])
                    elif existing[0] is not None:
                        result = json.loads(existing[1])
                        item.update(state=result.get('status','completed'),detail=result.get('detail','已结束'))
                        service.planner.save_activity(db,item)
                        if service.planner.current.get(item['npc'])==item['id']:
                            service.planner.current.pop(item['npc'],None)
                    elif 'tier' not in json.loads(existing[2]):
                        db.execute("UPDATE game_events SET content=json_set(content,'$.tier',?,'$.decision_reason',?) WHERE id=?",
                                   (item['tier'],item['reason'],event.id))
            for name,identity in service.planner.current.items():
                item=service.planner.records[identity]
                if not item['meeting_id']:
                    service.world.activity_targets[name]=item['area']

    @property
    def model_ready(self):
        return ACTIVITY.model_enabled and bool(getattr(getattr(self.s.llm,'cfg',None),'api_key',''))

    def make(self, identity, kind, name, title, target_type='', target_id='', source=None, **content):
        area = AREAS[self.s.world.area_of(name)]
        return GameEvent(identity,kind,self.s.world.clock.now,name,(name,),area['map_id'],area['room_id'],
                         target_type,target_id,dict(title=title,**content),{},source)

    def activity(self,item):
        name = item['npc']
        target = item['partner']
        area = AREAS[item['area']]
        title = item['title'] if item['kind']!='baseline' else area['name']+' · 基础作息'
        return self.make(self.activity_id(item),'conversation' if target else 'activity',
                         name,title,'npc' if target else 'area',target or item['area'],
                         item.get('source_event_id'),activity_id=item['id'],activity_kind=item['kind'],
                         tier=item['tier'],decision_reason=item['reason'],
                         purpose=item.get('purpose',''),
                         destination=area['name'],
                         prior_progress_minutes=item.get('prior_progress_minutes',0))

    @staticmethod
    def activity_id(item):
        return 'conversation:'+item['id'] if item['source'] in ('event_followup','whisper') and item['partner'] else item['id']

    def idle(self, db, name, suspended=()):
        event = self.make(uuid.uuid4().hex,'idle',name,'空闲等待')
        self.store.begin(db,event,'waiting')
        self.store.set_focus(db,name,event.id,list(suspended),event.game_time)

    def enter(self, db, event, phase):
        content=event.content
        if not self.store.begin(db,event,phase):
            stored=db.execute('SELECT content FROM game_events WHERE id=?',(event.id,)).fetchone()[0]
            content=json.loads(stored)
        at = self.s.world.clock.now
        for name in event.participants:
            current,stack = self.store.focus(db,name)
            if current == event.id:
                continue
            if current:
                kind,ended,encoded = db.execute('SELECT kind,ended_at,content FROM game_events WHERE id=?',(current,)).fetchone()
                if ended is None:
                    if kind == 'idle':
                        self.close(db,current,{'status':'completed','detail':'空闲结束'},release=False)
                    else:
                        stack.append(current)
                        previous=json.loads(encoded)
                        suspends_routine=previous.get('tier')=='low' and content.get('tier') in ('medium','high')
                        suspends_activity=previous.get('tier')=='medium' and event.kind in ('encounter_wait','notice_read')
                        if suspends_routine or suspends_activity:
                            title=content['title']
                            routine=previous.get('title','原日程')
                            if name==event.actor:
                                reason=content.get('decision_reason','')
                                note=f'{reason}，暂挂{routine}，先{title}' if reason else f'暂挂{routine}，先{title}'
                            else:
                                note=f'收到{event.actor}的邀约，暂挂{routine}'
                            self.note_schedule(db,event.id,name,note)
                            self.note_schedule(db,current,name,f'暂挂{routine}，先处理{title}')
            self.store.set_focus(db,name,event.id,stack,at)
        db.execute('''UPDATE game_events SET phase=?,participants=?,
            game_time=CASE WHEN phase='scheduled' THEN ? ELSE game_time END,
            map_id=CASE WHEN phase='scheduled' THEN ? ELSE map_id END,
            room_id=CASE WHEN phase='scheduled' THEN ? ELSE room_id END
            WHERE id=? AND ended_at IS NULL''',
            (phase,json.dumps(event.participants,ensure_ascii=False),at,event.map_id,event.room_id,event.id))

    def note_schedule(self,db,identity,actor,reason):
        note=json.dumps(dict(actor=actor,text=reason,game_time=self.s.world.clock.now),ensure_ascii=False)
        db.execute("""UPDATE game_events SET content=json_insert(
            json_set(content,'$.schedule_notes',json(COALESCE(json_extract(content,'$.schedule_notes'),'[]'))),
            '$.schedule_notes[#]',json(?)) WHERE id=?""",(note,identity))

    def release(self, db, identity):
        at = self.s.world.clock.now
        focuses=db.execute('SELECT actor,suspended FROM npc_event_focus WHERE event_id=?',(identity,)).fetchall()
        if not focuses:
            return
        kind,raw,result=db.execute('SELECT kind,content,result FROM game_events WHERE id=?',(identity,)).fetchone()
        ending=json.loads(raw)
        for name,encoded in focuses:
            if ending.get('tier')=='medium':
                self.s.planner.protect_routine(db,name)
            stack = json.loads(encoded)
            while stack:
                previous = stack.pop()
                row=db.execute('SELECT content FROM game_events WHERE id=? AND ended_at IS NULL',(previous,)).fetchone()
                if row:
                    resumed=json.loads(row[0])
                    due_high=ending.get('tier')=='medium' and any(
                        item['tier']=='high' for item in self.s.planner.due(name))
                    routine=self.s.planner.records.get(resumed.get('activity_id'))
                    resumed_tier=resumed.get('tier')
                    resumes_activity=resumed_tier=='medium' and kind in ('encounter_wait','notice_read','conversation','conversation_completed')
                    if (ending.get('tier') in ('medium','high') and (resumed_tier=='low' or resumes_activity) and not due_high
                            and (routine is None or routine['state']!='suspended' or not self.s.planner.routine_expired(routine))):
                        label={'conversation':'邀约','conversation_completed':'交谈',
                               'player_conversation':'与玩家交谈','notice_read':'阅读公告'}.get(kind,ending['title'])
                        interrupted=json.loads(result).get('status') in ('interrupted','cancelled')
                        note=f'{label}{"中断" if interrupted else "结束"}，恢复{resumed.get("title","原日程")}'
                        self.note_schedule(db,previous,name,note)
                        self.note_schedule(db,identity,name,note)
                    self.store.set_focus(db,name,previous,stack,at)
                    break
            else:
                self.idle(db,name)

    def close(self, db, identity, result, *, release=True):
        row = db.execute('''SELECT kind,game_time,actor,participants,map_id,room_id,
                target_type,target_id,source_event_id,content,result FROM game_events
                WHERE id=? AND ended_at IS NULL''',(identity,)).fetchone()
        if row is None:
            return False
        kind,started,actor,participants,map_id,room_id,target_type,target,source,content,interim = row
        at = self.s.world.clock.now
        event = GameEvent(identity,kind,started,actor,tuple(json.loads(participants)),map_id,room_id,
                          target_type,target,json.loads(content),dict(json.loads(interim),**result),source)
        self.store.conclude(db,event,at)
        if kind != 'idle':
            for name in event.participants:
                self.store.learn(db,name,identity,at,'')
        if release:
            self.release(db,identity)
        return True

    def begin_external(self, event, phase):
        with self.s.database() as db:
            self.enter(db,event,phase)

    def end_external(self, identity, status, detail):
        with self.s.database() as db:
            changed = self.close(db,identity,dict(status=status,detail=detail))
        if changed:
            self.changed(identity)

    def interrupt_dialogue(self,db,identity):
        db.execute("""UPDATE game_events SET phase='interrupted',result=json_patch(result,?) WHERE id=? AND ended_at IS NULL""",
                   (json.dumps({'status':'interrupted','detail':'对话中断，等待检查点恢复'},ensure_ascii=False),identity))
        self.release(db,identity)

    def changed(self, identity):
        with self.s.database() as db:
            names = [row[0] for row in db.execute('SELECT actor FROM event_knowledge WHERE event_id=?',(identity,))]
        if names:
            self.s.emit('game_event_recorded',game_event_id=identity,actors=names)

    def queue(self, db, identity, actors, mode, facts=None):
        status = 'pending' if self.model_ready else 'unavailable'
        db.executemany('''INSERT INTO event_assessments VALUES(?,?,?,?,?,?)
            ON CONFLICT(event_id,actor) DO NOTHING''',
            [(identity,name,mode,status,self.s.world.clock.now,json.dumps(facts or {},ensure_ascii=False))
             for name in actors])

    def poll(self, now):
        for key,outcome in list(self.deferred_assessments.items()):
            if self.resolve(key,*outcome) is not False:
                del self.deferred_assessments[key]
        for key,(future,anchor,deadline) in list(self.jobs.items()):
            if not future.done() and now < deadline:
                continue
            if future.done() and now < deadline:
                try:
                    value = future.result()
                    assessment = EventAssessment.model_validate(value) if value is not None else None
                    status = 'decided' if assessment is not None else 'unavailable'
                except ValidationError:
                    assessment,status = None,'invalid'
                except Exception:
                    log.exception('Event model request failed for %s',key)
                    assessment,status = None,'error'
            else:
                assessment,status = None,'timeout'
            if anchor is not None and self.resolve(key,anchor,assessment,status) is False:
                self.deferred_assessments[key]=(anchor,assessment,status)
            if future.done() or future.cancel():
                del self.jobs[key]
            else:
                self.jobs[key] = (future,None,deadline)
        if len(self.jobs) >= 2:
            return
        with self.s.database() as db:
            queued = db.execute('''SELECT a.event_id,a.actor,a.mode,a.payload,e.kind,e.content,e.result,e.actor,
                f.event_id AS focus FROM event_assessments a JOIN game_events e ON e.id=a.event_id
                JOIN npc_event_focus f ON f.actor=a.actor WHERE a.status='pending'
                ORDER BY a.requested_at,a.rowid''').fetchall()
        working = {key[1] for key in self.jobs.keys() | self.deferred_assessments.keys()}
        for identity,name,mode,facts,kind,content,result,initiator,focus in queued:
            if len(self.jobs) >= 2:
                break
            if name in working or not self.s.available(name,name):
                continue
            item=self.s.planner.current_item(name)
            if mode=='fact' and item and item['state']=='evaluating':
                continue
            data = self.store.visible_content(json.loads(content),kind,name,initiator)
            if mode=='activity' and focus!=identity:
                continue
            if not self.model_ready:
                self.resolve((identity,name),focus,None,'unavailable')
                continue
            context = self.s.planner.context(name)
            observations=json.loads(facts) if mode=='activity' else json.loads(result)
            observations.pop('perspectives',None)
            self.store.public_consent(observations,name)
            context.update(event=dict(kind=kind,content=data,facts=observations),
                can_extend=mode=='activity' and self.s.planner.can_extend(data['activity_id'],json.loads(facts)),
                people=[n for n in self.s.npcs if n!=name and self.s.onstage(n)])
            future = self.pool.submit(self.s.engine.agents[name].assess_event,context)
            self.jobs[(identity,name)] = future,focus,now+ACTIVITY.model_result_deadline_seconds
            working.add(name)

    def resolve(self, key, anchor, assessment, status):
        identity,name = key
        with self.s.database() as db:
            row = db.execute('''SELECT a.mode,a.payload,e.result FROM event_assessments a
                JOIN game_events e ON e.id=a.event_id WHERE a.event_id=? AND a.actor=? AND a.status='pending' ''',key).fetchone()
            if row is None:
                return
            current,_ = self.store.focus(db,name)
        mode,raw,result = row
        facts = json.loads(raw if mode=='activity' else result)
        payload = assessment.model_dump() if assessment else {}
        if mode=='activity' and current!=identity:
            return False  # 暂挂期间先保存模型判断，恢复后再应用。
        if mode=='fact' and (current!=anchor or not self.s.available(name,name)):
            status = 'stale'
        self.s.planner.resolve_event(identity,name,mode,facts,payload,status)

    def pause_actor(self,name):
        self.deferred_assessments={key:outcome for key,outcome in self.deferred_assessments.items() if key[1]!=name}
        for key,(future,_,deadline) in list(self.jobs.items()):
            if key[1]==name:
                self.jobs[key]=future,None,deadline

    def stop(self):
        self.pool.shutdown(wait=True,cancel_futures=True)

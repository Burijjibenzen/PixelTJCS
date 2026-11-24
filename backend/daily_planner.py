"""每日计划、知识范围内的事件响应及有到达确认的活动状态机。

不改变实际位置。仅通过World.activity_targets下发目的地，Godot完成寻路与上报。
"""
import hashlib
import json
import math
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from backend.activity_catalog import CATALOG, DailyPlan
from backend.activity_priority import HIGH_SOURCES, TIERS, activity_tier
from backend.config import ACTIVITY, SOCIAL, WORLD
from backend.world import AREAS

TERMINAL = {'completed', 'cancelled', 'timed_out', 'declined', 'deferred', 'unavailable'}
STATE_TEXT = {'scheduled':'尚未开始','waiting':'等待','travelling':'正在前往','active':'已到达／活动中',
              'evaluating':'正在整理结果','suspended':'已暂挂',
              'completed':'已完成','cancelled':'已取消','timed_out':'已超时',
              'declined':'对方未接受','deferred':'稍后再聊','unavailable':'未取得决定'}

# 历史活动会保存在数据库中；展示时也转换旧文案，不改动活动状态或计时。
DISPLAY_COPY = {
    '沿用人格卡基础作息':'基础作息',
    '沿用人格卡中的基础生活习惯':'基础作息',
    '保留目的地，等待实际到达上报':'正在前往目的地',
    '等待前端位置上报':'行程暂缓，等待恢复连接',
    '已实际到达，开始停留':'已到达，正在进行活动',
    '等待日程调度':'等待出发',
    '对话或反思处理中，保留行程':'正在交谈或整理思绪，稍后继续行程',
    '等待客户端位置上报':'等待恢复连接后继续会面',
    'LangGraph 对话中':'正在交谈',
}


class DynamicSchedule:
    def __init__(self, service):
        self.s = service
        self.records = {}
        self.current = {}
        self.encounter_waits = {}  # 等待者 -> 找人的活动 ID
        self.encounter_requests = {}  # 找人的活动 ID -> 被找者的事件和入队信息
        self.generated = set()
        self.model_attempted = set()
        self.signals = set()
        self.scan_at = 0.0
        self.next_model = 0.0
        self.job = None
        self.pruned_day = None
        self.routine_turns = set()
        self.queue_order = 0
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daily-plan')
        with service.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS npc_activities(id TEXT PRIMARY KEY,npc TEXT,day INTEGER,payload TEXT);
                CREATE TABLE IF NOT EXISTS npc_plan_days(npc TEXT,day INTEGER,model_status TEXT,PRIMARY KEY(npc,day));
                CREATE TABLE IF NOT EXISTS npc_plan_signals(npc TEXT,key TEXT,day INTEGER,PRIMARY KEY(npc,key));
                CREATE TABLE IF NOT EXISTS whisper_history(activity_id TEXT PRIMARY KEY,npc TEXT,
                    text TEXT,action TEXT,game_time REAL);
                CREATE TABLE IF NOT EXISTS npc_routine_turn(npc TEXT PRIMARY KEY);
            ''')
            restored=db.execute('SELECT payload FROM npc_activities ORDER BY rowid').fetchall()
            self.queue_order=max((json.loads(row[0]).get('queue_order',0) for row in restored),default=0)
            for row in restored:
                item = json.loads(row[0])
                item.pop('recipient_event_id',None)
                item.pop('encounter_stop',None)
                if item['npc'] in service.npcs and item['area'] in AREAS and (not item['partner'] or item['partner'] in service.npcs):
                    item['tier']=activity_tier(item['source'])
                    if 'queue_order' not in item:
                        self.queue_order+=1
                        item['queue_order']=self.queue_order
                    self.records[item['id']] = item
                    if item['state'] in ('travelling','active','waiting','evaluating'):
                        self.current[item['npc']] = item['id']
            for name, day, status in db.execute('SELECT npc,day,model_status FROM npc_plan_days'):
                self.generated.add((name, day))
                if status != 'rule': self.model_attempted.add((name, day))
            self.signals = set(db.execute('SELECT npc,key FROM npc_plan_signals'))
            self.routine_turns = {row[0] for row in db.execute('SELECT npc FROM npc_routine_turn')}

    def protect_routine(self,db,name):
        self.routine_turns.add(name)
        db.execute('INSERT OR IGNORE INTO npc_routine_turn VALUES(?)',(name,))

    @property
    def day(self):
        return int(self.s.world.clock.now // WORLD.minutes_per_day)

    def stop(self):
        for item in self.records.values():self._save(item)
        self.pool.shutdown(wait=True, cancel_futures=True)

    @staticmethod
    def rng(name, day):
        seed = int.from_bytes(hashlib.sha256(f'{name}:{day}'.encode()).digest()[:8], 'big')
        return random.Random(seed)

    def _save(self, item):
        with self.s.database() as db:
            self.save_activity(db,item)

    @staticmethod
    def save_activity(db, item):
        db.execute('''INSERT INTO npc_activities VALUES(?,?,?,?) ON CONFLICT(id)
                      DO UPDATE SET npc=excluded.npc,day=excluded.day,payload=excluded.payload''',
                   (item['id'],item['npc'],item['day'],json.dumps(item,ensure_ascii=False)))

    def _state(self, item, state, detail=''):
        if item['state'] == state and item.get('detail','') == detail:
            return
        updated=dict(item,state=state,detail=detail)
        with self.s.database() as db:
            self.save_activity(db,updated)
            if state in TERMINAL or self.current.get(item['npc'])==item['id']:
                self._sync_event(db,updated)
        item.update(updated)
        self.s.emit('activity_changed',npc=item['npc'],activity=self.public(item))
        if state in TERMINAL:
            self.s.event_runtime.changed(self.s.event_runtime.activity_id(item))

    def _make(self, name, kind, area, starts_at, source, reason, duration=None, key=None, partner=''):
        item=self._activity(name,kind,area,starts_at,source,reason,duration,key,partner)
        identity=item['id']
        if identity in self.records:
            return self.records[identity]
        self._save(item)
        self.records[identity]=item
        return item

    def _activity(self, name, kind, area, starts_at, source, reason, duration=None, key=None, partner=''):
        area = AREAS[area]['id']
        day = int(starts_at // WORLD.minutes_per_day)
        identity = uuid.uuid5(uuid.NAMESPACE_URL,f'{name}:{day}:{key}').hex if key else uuid.uuid4().hex
        self.queue_order+=1
        return dict(id=identity,npc=name,day=day,kind=kind,area=area,
                    title=CATALOG[kind]['title'] if kind in CATALOG else '基础作息' if kind=='baseline' else '手动指派',
                    reason=reason,source=source,starts_at=starts_at,tier=activity_tier(source),
                    queue_order=self.queue_order,
                    duration=duration or ACTIVITY.dwell_minutes,state='scheduled',phase='travelling',
                    travel_elapsed=0.0,stay_elapsed=0.0,dwell_minutes=0.0,arrived_at=None,
                    partner=partner,meeting_id='',detail='等待开始')

    @staticmethod
    def deadline(item):
        deadline=item.get('appointment_at')
        if deadline is None:
            deadline=float('inf') if item['tier']=='high' else item['starts_at']+ACTIVITY.pending_expiry_minutes
        return deadline

    def queue_key(self,item):
        return (-TIERS.index(item['tier']),self.deadline(item),item['queue_order'])

    def current_item(self, name):
        item=self.records.get(self.current.get(name,''))
        return item if item and item['state'] not in TERMINAL else None

    def _sync_event(self, db, item):
        runtime=self.s.event_runtime
        event=runtime.activity(item)
        if item['state'] in TERMINAL:
            facts=self._facts(item,item['state'],item['detail'])
            if runtime.close(db,event.id,facts):
                if item['state']=='timed_out':
                    runtime.queue(db,event.id,event.participants,'fact')
                if item['state']=='completed' and not runtime.model_ready:
                    runtime.queue(db,event.id,event.participants,'activity')
                pending=db.execute("""SELECT 1 FROM event_assessments WHERE event_id=? AND actor=?
                    AND mode='activity' AND status='pending'""",(event.id,item['npc'])).fetchone()
                if pending:
                    self.s.game_events.write_assessment(db,event.id,item['npc'],'stale',{})
        elif item['state']!='suspended':
            row=db.execute('SELECT ended_at,phase FROM game_events WHERE id=?',(event.id,)).fetchone()
            if row is None or row[1]=='scheduled':
                runtime.enter(db,event,item['state'])
            elif row[0] is None:
                db.execute('UPDATE game_events SET phase=? WHERE id=?',(item['state'],event.id))
                if item['state']=='active' and item['arrived_at'] is not None:
                    room=AREAS[item['area']]
                    db.execute('UPDATE game_events SET map_id=?,room_id=? WHERE id=?',
                               (room['map_id'],room['room_id'],event.id))

    def due(self,name):
        now=self.s.world.clock.now
        return [item for item in self.records.values() if item['npc']==name and item['state']=='scheduled'
                and item['starts_at']<=now<=self.deadline(item)]

    def blocks_interaction(self,name,tier='medium',*,reverse='',continuing=False,queued=None):
        if tier=='medium' and name in self.encounter_waits:
            return True
        if not continuing and tier=='medium' and name in self.routine_turns:
            return True
        current=self.current_item(name)
        if current and (not reverse or current['partner']!=reverse) and (current['state']=='evaluating'
                or TIERS.index(current['tier'])>=TIERS.index(tier)):
            return True
        for item in self.due(name):
            if item['tier']=='medium' and name in self.routine_turns:
                continue
            if TIERS.index(item['tier'])>TIERS.index(tier):
                return True
            if not continuing and item['tier']==tier and (not reverse or item['partner']!=reverse):
                if queued is None or self.queue_key(item)<self.queue_key(queued):
                    return True
        if not continuing and tier=='medium' and queued is None:
            return any(event.actor==name and event.target_id!=reverse for event,_ in self.encounter_requests.values())
        return False

    def manual(self, name):
        if not ACTIVITY.enabled:return False
        return any(a['npc']==name and a['source'] in ('manual','whisper') and a['state'] not in TERMINAL
                   for a in self.records.values())

    def eligible_meetings(self):
        return [m for m in self.s.active_meetings() if self.meeting_eligible(m)]

    def meeting_eligible(self,meeting):
        if any(self.s.player.holds(name) for name in (meeting['a'], meeting['b'])):
            return False
        if any(self.manual(n) for n in (meeting['a'],meeting['b'])):
            return False
        if meeting.get('tier')=='medium' and meeting['state'] not in ('talking','deciding'):
            return not any(self.blocks_interaction(name) for name in (meeting['a'],meeting['b']))
        identity=meeting.get('activity_id')
        if not identity or meeting['state'] in ('talking','deciding'):
            return True
        item=self.current_item(meeting['a'])
        if not item or item['id']!=identity:
            return False
        target=meeting['b']
        current=self.current_item(target)
        protected=target in self.routine_turns
        return (target not in self.s.reading|self.s.notice_approaching
                and not (current and TIERS.index(current['tier'])>=TIERS.index(item['tier']))
                and not any(TIERS.index(candidate['tier'])>TIERS.index(item['tier'])
                            and (not protected or candidate['tier']!='medium')
                            for candidate in self.due(target)))

    def public(self, item):
        result={k:item[k] for k in ('id','kind','title','area','reason','source','tier','starts_at','duration','state','arrived_at','detail')}
        for key in ('reason','detail'):result[key]=DISPLAY_COPY.get(result[key],result[key])
        if 'source_event_id' in item:result['source_event_id']=item['source_event_id']
        return result

    def describe(self, name):
        if not self.s.onstage(name):
            return dict(id='',kind='stage_exit',title='已退场',area=self.s.world.area_of(name),
                        reason='上帝模式退场',source='stage',state='waiting',
                        state_label='已退场',detail='等待重新出演')
        item=self.current_item(name) if ACTIVITY.enabled else None
        if item:
            result=self.public(item)
        else:
            result=dict(id='',kind='baseline',title='基础作息',area=self.s.world.target_area(name),
                        reason='基础作息',source='baseline',state='waiting',detail='等待出发')
        if name in self.s.busy or name in self.s.reflecting:
            result=dict(result,state='waiting',detail='正在交谈或整理思绪，稍后继续行程')
        for meeting in self.eligible_meetings():
            if name in (meeting['a'],meeting['b']):
                other=meeting['b'] if name==meeting['a'] else meeting['a']
                result=dict(result,kind='meeting',title=f'与{other}会面',source='meeting',area=meeting['area'] or result['area'],
                            state='travelling' if meeting['state']=='moving' else 'active' if meeting['state']=='talking' else 'waiting',
                            reason='已有会面请求优先处理',detail=DISPLAY_COPY.get(meeting['detail'],meeting['detail']))
                break
        result['state_label']=STATE_TEXT[result['state']]
        return result

    def conversation_window(self,name):
        """明确当前活动与下一项有时限的安排对邀约的限制。"""
        now=self.s.world.clock.now
        current=self.current_item(name)
        upcoming=[a for a in self.records.values() if a['npc']==name and a['state']=='scheduled'
                  and (a['source']=='manual' or now-a['starts_at']<=ACTIVITY.pending_expiry_minutes)]
        next_item=min(upcoming,key=lambda a:(a['starts_at'],a['queue_order']),default=None)
        gate=dict(allowed=['accept','brief','defer','decline'],available_minutes=15,
                  reason='普通活动可以暂时打断',
                  current=self.public(current) if current else dict(title='空闲等待',state='waiting'),
                  next_activity=None)
        if next_item:
            gate['next_activity']=dict(title=next_item['title'],in_minutes=round(max(0,next_item['starts_at']-now),1))
        for hhmm,area in self.s.cards[name].schedule.items():
            hour,minute=map(int,hhmm.split(':'))
            offset=(hour*60+minute-now)%WORLD.minutes_per_day or WORLD.minutes_per_day
            if gate['next_activity'] is None or offset<gate['next_activity']['in_minutes']:
                gate['next_activity']=dict(title=AREAS.get(area,AREAS['j3_lab'])['name']+'的基础作息',
                                           in_minutes=round(offset,1))
        gate['available_minutes']=min([15]+[max(0,a['starts_at']-now) for a in upcoming
            if a['source']=='manual' or a['kind'] in ('class','seminar') or a.get('appointment_at') is not None])
        if current:
            gate['current']['remaining_minutes']=round(max(0,current['duration']-current['dwell_minutes']),1)
            if current['source']=='manual' or (current['kind'] in ('class','seminar') and current['phase']=='active'):
                gate.update(available_minutes=0,reason='正在执行不可打断的安排')
            elif current['state'] in ('travelling','evaluating'):
                gate.update(available_minutes=min(5,gate['available_minutes']),reason='正在赶路或收尾，适合短聊')
        if gate['available_minutes']<3:
            gate.update(allowed=['defer','decline'],
                        reason=gate['reason'] if current and gate['reason']=='正在执行不可打断的安排'
                        else '即将开始有时间要求的活动')
        elif gate['available_minutes']<10:
            gate['allowed'].remove('accept')
            if gate['reason']=='普通活动可以暂时打断':
                gate['reason']='下一项安排临近，只适合短聊'
        return gate

    def listing(self, name):
        return dict(current=self.describe(name),activities=[self.public(a) for a in sorted(
            (a for a in self.records.values() if a['npc']==name),key=lambda a:a['starts_at'])][-20:])

    def _friends(self, name):
        return [dict(name=other,score=self.s.relations.get(name,other)) for other in self.s.npcs
                if other!=name and self.s.onstage(other) and self.s.relations.get(name,other)>=ACTIVITY.friend_threshold]

    def context(self, name):
        # 只读取本人活跃记忆、本人收到的事件和本人关系；不传其它人的卡片或秘密。
        memories=[m for m in self.s.memory.get_active(name,12) if m.type!='reflection'][:6]
        insights=self.s.memory.get_reflections(name,3)
        since=self.s.world.clock.now-ACTIVITY.history_days*WORLD.minutes_per_day
        known_events=[dict(text=e['text'],game_time=e['game_time']) for e in self.s.world.events[-30:]
                      if name in e['participants'] and e.get('kind')!='notice_read']
        known_events.extend(self.s.game_events.known_events(name,since))
        return dict(day=self.day,minute=int(self.s.world.clock.now % WORLD.minutes_per_day),
                    perception=self.s.perception(name),
                    base_schedule=self.s.cards[name].schedule,
                    memories=[dict(id=m.id,type=m.type,content=m.content) for m in memories],
                    insights=[dict(id=m.id,content=m.content) for m in insights],
                    relationships=[dict(name=other,score=self.s.relations.get(name,other))
                                   for other in self.s.npcs if other!=name and self.s.onstage(other)],
                    known_events=sorted(known_events,key=lambda e:e['game_time'])[-30:],
                    existing_activities=[self.public(a) for a in self.records.values()
                                         if a['npc']==name and a['state'] not in TERMINAL],
                    friends=self._friends(name),
                    places=[dict(kind=k,area=a,name=AREAS[a]['name']) for k,v in CATALOG.items()
                            if self.s.cards[name].role in v['weights'] for a in v['rooms'] if a in AREAS])

    def _reason(self, name, kind, source, partner=''):
        card=self.s.cards[name]
        if partner:return f'尝试约关系较好的{partner}交流，等待双方空闲'
        prefix={'event':'根据本人收到的通知','memory':'根据本人尚未遗忘的相关经历','model':'结合人格、目标与本人已知信息'}.get(source,'结合本人身份与日常习惯')
        purpose={'library':'查阅研究资料','class':'课程学习' if card.role=='student' else '教学交流与安排核对',
                 'exercise':'运动休息，调整工作节奏','seminar':'参加组会或学术交流','admin':'整理教学与行政资料',
                 'research':'推进科研与项目','break':'短暂休息'}.get(kind,'继续基础安排')
        return f'{prefix}，安排{purpose}'

    def ensure_day(self, name):
        day=self.day
        if (name,day) in self.generated:return
        rng=self.rng(name,day)
        card=self.s.cards[name]
        facts=' '.join(card.traits+[card.goals_short,card.goals_long])
        memories=' '.join(m.content for m in self.s.memory.get_active(name,6))
        friends=self._friends(name)
        weights={k:float(v['weights'].get(card.role,0)) for k,v in CATALOG.items()}
        if any(x in facts for x in ('好奇','研究方向','论文','方法论','省考')):weights['library']*=1.8
        if '好奇' in facts:weights['class']*=2
        if any(x in facts for x in ('焦虑','佛系','准点下班')):weights['exercise']*=2
        if any(x in facts for x in ('低社交','作息刻板')):weights['visit']*=0.2
        if not friends:weights['visit']=0
        if any(x in memories for x in ('查资料','图书','文献','研究问题')):weights['library']*=2
        for index in range(ACTIVITY.daily_optional_count):
            choices=[k for k,w in weights.items() if w>0]
            if not choices:break
            kind=rng.choices(choices,weights=[weights[k] for k in choices])[0]
            weights[kind]=0
            rooms=[a for a in CATALOG[kind]['rooms'] if a in AREAS]
            if not rooms:continue
            starts=day*1440+(9*60 if index==0 else 14*60)+rng.randint(0,90)
            partner=rng.choice(friends)['name'] if kind=='visit' else ''
            self._make(name,kind,rng.choice(rooms),starts,'relationship' if partner else 'rule',
                       self._reason(name,kind,'rule',partner),key=f'daily:{index}',partner=partner)
        self.generated.add((name,day))
        with self.s.database() as db:
            db.execute('INSERT OR IGNORE INTO npc_plan_days VALUES(?,?,?)',(name,day,'rule'))

    @staticmethod
    def notice_kind(text):
        for kind,words in (('seminar',('组会','讲座','报告会')),('library',('图书','查资料','文献','研究问题','讨论项目')),
                           ('class',('上课','课程','教室','考试')),('exercise',('运动','健身'))):
            if any(word in text for word in words):return kind
        return None

    def notice(self, text, participants, key, received_at=None):
        received_at=self.s.world.clock.now if received_at is None else received_at
        result=self.notice_result(text,received_at)
        if result['activity'] is None:return False
        for name in participants:
            signal=(name,str(key))
            if not self.s.onstage(name) or signal in self.signals:continue
            self._remember_signal(signal)
            # 外部注入的通知也只走一次调度。
            for memory in self.s.memory.get_active(name,6):
                if memory.content==text:self._remember_signal((name,'memory:'+memory.id))
            _, changes=self.event_changes(name,result,key,received_at)
            if changes:
                with self.s.database() as db:
                    for item in changes:self.save_activity(db,item)
                self.apply_event_changes(changes)
        return True

    def notice_result(self, text, published_at):
        """从公告原文提取可供调度判断的活动信息。"""
        kind=self.notice_kind(text)
        result=dict(kind='notice_learned',activity=None)
        if kind is None:return result
        clock=re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)',text)
        tomorrow='明天' in text
        appointment=None
        if clock or tomorrow:
            day=int(published_at//WORLD.minutes_per_day)+int(tomorrow)
            appointment=day*WORLD.minutes_per_day+(int(clock[1])*60+int(clock[2]) if clock else 9*60)
        rooms=CATALOG[kind]['rooms']
        area=next((room for room in rooms if room in AREAS and (
            AREAS[room]['room_id'] in text or AREAS[room]['room_id'].removeprefix('zx2_') in text.lower()
            or AREAS[room]['name'].split('·')[-1] in text)),None)
        result['activity']=dict(kind=kind,appointment_at=appointment,area=area,
                                cancel='取消' in text or '不用参加' in text)
        return result

    def event_changes(self, name, result, key, received_at, source_event_id=None):
        """校验角色提出的日程意向，调用方提交记录后再更新运行状态。"""
        intent=self.s.engine.agents[name].react_to_event(result)
        if intent=='continue' or not self.s.onstage(name):
            return dict(action='continue',reason='当前结果没有需要调整的日程事项',activity_ids=[]), []
        activity=result['activity']
        kind=activity['kind']
        if not ACTIVITY.enabled:
            return dict(action='continue',reason='动态日程已关闭，继续基础作息',activity_ids=[]), []
        if intent=='cancel':
            reason='本人收到取消通知' if result['kind']=='notice_learned' else '本人得知相关安排已取消'
            changes=[dict(item,state='cancelled',detail=reason) for item in self.records.values()
                     if item['npc']==name and item['kind']==kind and item['source']=='event'
                     and item['state'] not in TERMINAL]
            if changes:
                return dict(action='cancelled',reason=reason,activity_ids=[item['id'] for item in changes]), changes
            return dict(action='continue',reason='没有与取消通知对应的待办',activity_ids=[]), []
        now=self.s.world.clock.now
        appointment=activity['appointment_at']
        starts=max(now,appointment-ACTIVITY.appointment_lead_minutes) if appointment is not None else now
        expired=appointment<now-1 if appointment is not None else now-received_at>ACTIVITY.pending_expiry_minutes
        if expired:
            return dict(action='continue',reason='公告中的活动时间已过',activity_ids=[]), []
        count=sum(a['npc']==name and a['day']==self.day and a['source']=='event' for a in self.records.values())
        if count>=ACTIVITY.event_reactions_per_day:
            return dict(action='continue',reason='已达到今天的通知活动上限',activity_ids=[]), []
        rooms=[a for a in CATALOG[kind]['rooms'] if a in AREAS]
        if not rooms:
            return dict(action='continue',reason='地图中没有可安排此活动的地点',activity_ids=[]), []
        room=activity['area'] or self.rng(str(key),self.day).choice(rooms)
        reason=self._reason(name,kind,'event') if result['kind']=='notice_learned' else '根据本人获知的结果安排'+CATALOG[kind]['title']
        item=self._activity(name,kind,room,starts,'event',reason,key=str(key))
        if appointment is not None:
            item['appointment_at']=appointment
        if source_event_id is not None:
            item['source_event_id']=source_event_id
        changes=[] if item['id'] in self.records else [item]
        return dict(action='scheduled',reason=reason,activity_ids=[item['id']]), changes

    def apply_event_changes(self, changes):
        for item in changes:
            identity=item['id']
            record=self.records.setdefault(identity,item)
            record.update(item)
            if item['state']=='cancelled':
                name=item['npc']
                if self.current.get(name)==identity:
                    with self.s.database() as db:self._sync_event(db,record)
                    self.current.pop(name,None)
                    self.s.world.activity_targets.pop(name,None)
                self.s.emit('activity_changed',npc=name,activity=self.public(record))

    def _remember_signal(self, signal):
        self.signals.add(signal)
        with self.s.database() as db:
            db.execute('INSERT OR IGNORE INTO npc_plan_signals VALUES(?,?,?)',(*signal,self.day))

    def _prune(self):
        if self.pruned_day==self.day:return
        self.pruned_day=self.day
        cutoff=self.day-ACTIVITY.history_days
        obsolete=[a['id'] for a in self.records.values() if a['day']<cutoff and a['state'] in TERMINAL]
        with self.s.database() as db:
            db.executemany('DELETE FROM npc_activities WHERE id=?',[(key,) for key in obsolete])
            db.execute('DELETE FROM npc_plan_days WHERE day<?',(cutoff,))
            db.execute('DELETE FROM npc_plan_signals WHERE day<?',(cutoff,))
            self.signals=set(db.execute('SELECT npc,key FROM npc_plan_signals'))
        for key in obsolete:del self.records[key]
        self.generated={key for key in self.generated if key[1]>=cutoff}
        self.model_attempted={key for key in self.model_attempted if key[1]>=cutoff}

    def _scan_memories(self):
        for name in self.s.npcs:
            if not self.s.onstage(name):continue
            count=sum(a['npc']==name and a['day']==self.day and a['source']=='memory' for a in self.records.values())
            if count>=ACTIVITY.memory_reactions_per_day:continue
            for memory in self.s.memory.get_active(name,6):
                if memory.type=='observation' and 'notice' in memory.tags:continue
                signal=(name,'memory:'+memory.id)
                if signal in self.signals:continue
                kind=self.notice_kind(memory.content)
                # 聊天记忆只触发软性查资料/运动机会，不能当作强制官方通知。
                if kind not in ('library','exercise'):continue
                if any(word in memory.content for word in ('取消','不去','不用','不需要','别去')) or re.search(r'\d[:：]\d',memory.content):continue
                self._remember_signal(signal)
                self._make(name,kind,CATALOG[kind]['rooms'][0],self.s.world.clock.now,'memory',
                           self._reason(name,kind,'memory'),key=signal[1])
                break

    def command(self, name, area):
        if not self.s.onstage(name) or area not in AREAS:return False
        if not ACTIVITY.enabled:return self.s.world.assign_override(name,area)
        self.s.world.override_area.pop(name,None)
        self.s.world.override_slot.pop(name,None)
        self._make(name,'manual',area,self.s.world.clock.now,'manual','用户手动指派，抵达并短暂停留后恢复日程',
                   duration=30)
        return True

    def whisper(self,name,action,text=None):
        if not self.s.onstage(name):
            raise ValueError('角色已退场')
        area=action.area
        kind='manual'
        partner=''
        if action.type=='talk_to':
            partner=action.npc
            if not self.s.onstage(partner) or partner==name:
                raise ValueError('请指定另一名角色')
            area=self.s.world.area_of(partner)
            kind='visit'
        elif action.type=='stay':
            area=self.s.world.area_of(name)
        elif action.type=='perform_at':
            kind=action.activity
            if kind=='visit' or kind not in CATALOG or area not in CATALOG[kind]['rooms']:
                raise ValueError('该地点无法执行指定活动')
        if area not in AREAS:
            raise ValueError('请指定地图上的有效地点')
        item=self._activity(name,kind,area,self.s.world.clock.now,'whisper','收到悄悄话指派',
                            duration=action.minutes,partner=partner)
        if partner:
            item.update(title=f'找{partner}交谈',purpose=action.purpose or '聊聊近况')
        elif kind=='manual':
            item.update(title='原地停留' if action.type=='stay' else f'前往{AREAS[area]["name"]}',
                        purpose=action.purpose,hold_position=action.type=='stay')
        else:
            item['purpose']=action.purpose
        self.s.world.override_area.pop(name,None)
        self.s.world.override_slot.pop(name,None)
        with self.s.database() as db:
            self.save_activity(db,item)
            if text is not None:
                db.execute('INSERT INTO whisper_history VALUES(?,?,?,?,?)',
                           (item['id'],name,text,item['title'],item['starts_at']))
        self.records[item['id']]=item
        return item

    def cancel(self, name, identity, reason='用户取消活动'):
        item=self.records.get(identity)
        if not item or item['npc']!=name:return False
        if item['state'] in TERMINAL:return True
        if identity in self.s.scheduler.event_talks:return False
        if item['meeting_id']:
            meeting=self.s.meeting(item['meeting_id'])
            if meeting and meeting['state'] in ('deciding','talking'):return False
            if meeting:self.s.cancel_meeting(meeting['id'])
        self.finish_encounter(identity,'cancelled',reason)
        self._state(item,'cancelled',reason)
        if self.current.get(name)==identity:
            self.current.pop(name,None)
            self.s.world.activity_targets.pop(name,None)
        return True

    def remove_actor(self,name):
        for item in list(self.records.values()):
            if item['state'] in TERMINAL or not item['partner'] or name not in (item['npc'],item['partner']):
                continue
            if item['id'] in self.s.scheduler.event_talks:
                self.s.scheduler.event_talks.pop(item['id']).cancel()
            self.cancel(item['npc'],item['id'],'参与角色退场')
        if self.job and self.job['npc']==name:
            self.job['expired']=True
            self._model_status(self.job,'offstage')

    def arrived(self, name, confirmed=None, activity_id=None):
        if not ACTIVITY.enabled:return
        item=self.current_item(name)
        if not item or item['partner'] or confirmed is False or not self.s.available(name,name):return
        if activity_id is not None and activity_id!=item['id']:return
        if AREAS[self.s.world.area_of(name)]['id']==item['area'] and item['phase']=='travelling':
            item['phase']='active'
            if item['arrived_at'] is None:
                item['arrived_at']=self.s.world.clock.now
            self._state(item,'active','已到达，正在进行活动')

    def _blocked(self, name, item, *, preempting=None):
        if name in self.s.notice_approaching:return '正在走近公告栏，随后继续原行程'
        if name in self.s.reading_waiting:return '已安排阅读公告，等待当前任务结束'
        if name in self.s.reading:return '正在阅读公告，稍后继续行程'
        if name in self.s.busy or name in self.s.reflecting:return '正在交谈或整理思绪，稍后继续行程'
        if any(name in pair for pair in self.s.active_conversations.values()):
            return '等待当前对话结束'
        if not self.s.available(name,name):return '等待角色空闲'
        own_meeting=preempting['meeting_id'] if preempting else ''
        if item['source'] not in HIGH_SOURCES and any(name in (m['a'],m['b']) and m['id']!=own_meeting
                                                       and (not m.get('activity_id') or
                                                            TIERS.index(item['tier'])<=TIERS.index(self.records[m['activity_id']]['tier']))
                                                       for m in self.eligible_meetings()):
            return '先处理已有会面，之后恢复活动'
        return ''

    def _start(self, item):
        name=item['npc']
        self.current[name]=item['id']
        if item['partner'] and item['source'] not in ('event_followup','whisper'):
            meeting=self._reserve_meeting(item)
            if meeting is None:
                self._state(item,'timed_out','会面队列已满，回到基础日程')
                self.current.pop(name,None)
                return False
            item['meeting_id']=meeting['id']
            self._state(item,'waiting','已申请会面，等待双方空闲和实际抵达')
            return True
        actual=AREAS[self.s.world.area_of(name)]['id']
        hold=item.get('hold_position')
        if hold or (item.get('continuation') and item['area']==actual):
            item['area']=actual
            item.update(phase='active',arrived_at=self.s.world.clock.now)
            self.s.world.activity_targets[name]=item['area']
            self._state(item,'active','正在原地停留' if hold else '继续当前活动')
        else:
            self.s.world.activity_targets[name]=item['area']
            self._state(item,'travelling','正在前往目的地')
        return True

    def _facts(self,item,status,detail):
        place=AREAS[self.s.world.area_of(item['npc'])]
        return dict(status=status,detail=detail,arrived_at=item['arrived_at'],
                    dwell_minutes=round(item['dwell_minutes'],2),
                    prior_progress_minutes=item.get('prior_progress_minutes',0),
                    actual_location={key:place[key] for key in ('map_id','room_id','name')})

    def _checkpoint(self, item):
        runtime=self.s.event_runtime
        identity=runtime.activity_id(item)
        facts=self._facts(item,'completed','已完成实际到达后的停留')
        if not runtime.model_ready:
            self._state(item,'completed',facts['detail'])
            self.current.pop(item['npc'],None)
            self.s.world.activity_targets.pop(item['npc'],None)
            return
        updated=dict(item,state='evaluating',detail='正在整理结果')
        with self.s.database() as db:
            self.save_activity(db,updated)
            self._sync_event(db,updated)
            runtime.queue(db,identity,(item['npc'],),'activity',facts)
        item.update(updated)

    def can_extend(self, identity, facts):
        item=self.records[identity]
        return (facts['status']=='completed' and not item['partner']
                and item['kind']!='manual' and item['arrived_at'] is not None)

    def _followup(self,name,identity,action,reason,item,facts):
        kind=action['type']
        now=self.s.world.clock.now
        if kind=='talk_to':
            partner=action['npc']
            if partner==name or not self.s.onstage(partner):
                return None
            next_item=self._activity(name,'visit',self.s.world.area_of(partner),now,
                                     'event_followup',reason,partner=partner)
            next_item.update(title=f'找{partner}交谈',purpose=action['topic'])
        elif kind=='perform_at':
            activity,area=action['kind'],action['area']
            if activity=='visit' or activity not in CATALOG or area not in CATALOG[activity]['rooms']:
                return None
            if self.s.cards[name].role not in CATALOG[activity]['weights']:
                return None
            next_item=self._activity(name,activity,area,now,'event_followup',reason,
                                     duration=action['minutes'])
            next_item['purpose']=action['purpose']
        else:
            if item is None or not self.can_extend(item['id'],facts):
                return None
            next_item=self._activity(name,item['kind'],item['area'],now,'event_followup',reason,
                                     duration=action['minutes'])
            next_item.update(title=item['title'],purpose=item.get('purpose',''),continuation=True,
                prior_progress_minutes=round(item.get('prior_progress_minutes',0)+item['dwell_minutes'],2))
        next_item['source_event_id']=identity
        return next_item

    def queue_social_visit(self,name,partner,topic,*,directed=False,reason=''):
        if not self.s.onstage(name) or not self.s.onstage(partner):
            return None
        existing=next((item for item in self.records.values()
                       if item['npc']==name and item['partner']==partner and item['source']=='event_followup'
                       and item['state'] not in TERMINAL),None)
        if existing:
            if directed and existing['state']=='scheduled':
                existing['reason']='上帝模式指派'
                self._save(existing)
            return existing
        with self.s.database() as db:
            current,_=self.s.game_events.focus(db,name)
            reason='上帝模式指派' if directed else reason or '主动找对方交谈'
            item=self._followup(name,current,dict(type='talk_to',npc=partner,topic=topic),reason,None,{})
            self.s.game_events.begin(db,self.s.event_runtime.activity(item),'scheduled')
            self.save_activity(db,item)
        self.records[item['id']]=item
        if not directed:
            self.s.self_talk(name,self.s.event_runtime.activity_id(item),current,f'找{partner}交流一下吧。')
        return item

    def resolve_event(self,identity,name,mode,facts,payload,status):
        runtime=self.s.event_runtime
        item=self.current_item(name) if mode=='activity' else None
        action=payload.get('action')
        followup=None
        if status=='decided' and action:
            followup=self._followup(name,identity,action,payload['reason'],item,facts)
        if action and status=='decided' and followup is None:
            status='rejected'
        if followup:
            payload['next_event_id']=runtime.activity_id(followup)
            if action['type']=='perform_at':
                payload['destination']=AREAS[followup['area']]['name']
        updated=dict(item,state='completed',detail=facts['detail']) if item else None
        immediate=(updated is not None and followup is not None
                   and item['tier']!='medium' and name not in self.routine_turns
                   and not any(self.queue_key(candidate)<self.queue_key(followup) for candidate in self.due(name)))
        with self.s.database() as db:
            if updated is not None:
                self.save_activity(db,updated)
                runtime.close(db,identity,facts,release=not immediate)
            self.s.game_events.write_assessment(db,identity,name,status,payload)
            if followup:
                if immediate:
                    if followup.get('continuation') and AREAS[self.s.world.area_of(name)]['id']==followup['area']:
                        followup.update(state='active',phase='active',arrived_at=self.s.world.clock.now,detail='继续当前活动')
                    else:
                        followup.update(state='travelling',detail='正在前往目的地')
                    runtime.enter(db,runtime.activity(followup),followup['state'])
                    self._sync_event(db,followup)
                else:
                    self.s.game_events.begin(db,runtime.activity(followup),'scheduled')
                self.save_activity(db,followup)
        if updated is not None:
            item.update(updated)
            self.current.pop(name,None)
            self.s.world.activity_targets.pop(name,None)
        if followup:
            self.records[followup['id']]=followup
            if immediate:
                self.current[name]=followup['id']
                self.s.world.activity_targets[name]=followup['area']
            if action['type']=='talk_to':
                prefix='资料查完了，' if item and item['kind']=='library' else ''
                text=f"{prefix}找{action['npc']}交流一下吧。"
            elif action['type']=='perform_at':
                text=f"接下来去{AREAS[followup['area']]['name'].split('·')[-1]}。"
            else:
                text=f"我再继续{followup['title']}一会儿。"
            bubble=' '.join(payload.get('bubble','').replace('（自言自语）','').split())
            if len(bubble)>28 or bubble==payload['outcome'].strip():
                bubble=''
            self.s.self_talk(name,payload['next_event_id'],identity,(bubble or text)[:28])
        runtime.changed(identity)

    def routine_expired(self,item):
        now=self.s.world.clock.now
        if item['kind']=='baseline':
            previous=AREAS[item.get('suspended_schedule_area',item['area'])]['id']
            return previous!=AREAS[self.s.world.schedule_area(self.s.cards[item['npc']],now)]['id']
        return now>item['starts_at']+ACTIVITY.pending_expiry_minutes+item['duration']

    def _resume_suspended(self,name):
        with self.s.database() as db:
            focus,_=self.s.game_events.focus(db,name)
        item=next((a for a in self.records.values() if a['npc']==name and a['state']=='suspended'
                   and self.s.event_runtime.activity_id(a)==focus),None)
        if item:
            if self.routine_expired(item):
                self._state(item,'timed_out','原日程已过，重新选择当前安排')
                return None
            self.current[name]=item['id']
            state=item.pop('resume_state')
            if state=='evaluating':
                self._state(item,state,'继续整理活动结果')
            else:
                self._state(item,state,'恢复先前的活动')
        return item

    def _suspend_routine(self,item):
        name=item['npc']
        if item['meeting_id']:
            self.s.cancel_meeting(item['meeting_id'])
            item['meeting_id']=''
        item['resume_state']=item['state']
        if item['kind']=='baseline':
            item['suspended_schedule_area']=self.s.world.schedule_area(self.s.cards[name],self.s.world.clock.now)
        self._state(item,'suspended','暂挂日程，保留进度')
        self.current.pop(name,None)
        self.s.world.activity_targets.pop(name,None)

    def encounter_participants(self):
        return set(self.encounter_waits) | {self.records[key]['npc'] for key in self.encounter_waits.values()}

    def _within_encounter_range(self,item,now):
        if self.s.player.holds(item['npc']) or self.s.player.holds(item['partner']):
            return False
        first,second=(self.s.positions.get(name) for name in (item['npc'],item['partner']))
        return (first and second and first['map_id']==second['map_id']
                and all(0<=now-p['at']<=SOCIAL.position_max_age_seconds for p in (first,second))
                and math.hypot(first['x']-second['x'],first['y']-second['y'])<=SOCIAL.encounter_wait_radius)

    def finish_encounter(self,pursuit_id,status,detail):
        request=self.encounter_requests.get(pursuit_id)
        if request is None:
            return
        event,_=request
        self.s.event_runtime.end_external(event.id,status,detail)
        del self.encounter_requests[pursuit_id]
        if self.encounter_waits.get(event.actor)==pursuit_id:
            del self.encounter_waits[event.actor]

    def _reserve_encounters(self,pursuits,now):
        in_range={item['id']:item for item in pursuits.values() if self._within_encounter_range(item,now)}
        active=in_range.keys() | self.s.scheduler.event_talks.keys()
        for key in list(self.encounter_requests):
            if key not in active or not self._within_encounter_range(self.records[key],now):
                self.finish_encounter(key,'interrupted','双方位置未确认在相遇范围内，结束等待')
        runtime=self.s.event_runtime
        for key,item in in_range.items():
            if key in self.encounter_requests or key in self.s.scheduler.event_talks:
                continue
            waiter,seeker=item['partner'],item['npc']
            event=runtime.make(uuid.uuid4().hex,'encounter_wait',waiter,f'被{seeker}寻找','npc',seeker,
                               runtime.activity_id(item),tier='medium',decision_reason='对方已进入相遇范围')
            with self.s.database() as db:
                self.s.game_events.begin(db,event,'scheduled')
                self.s.game_events.learn(db,waiter,event.id,event.game_time,'')
            self.queue_order+=1
            self.encounter_requests[key]=(event,dict(tier='medium',starts_at=event.game_time,
                                                     appointment_at=float('inf'),queue_order=self.queue_order))
            runtime.changed(event.id)
        reserved=self.encounter_participants()
        queued=sorted(in_range.keys() & self.encounter_requests.keys(),key=lambda key:self.queue_key(self.encounter_requests[key][1]))
        for key in queued:
            if key not in self.encounter_requests:
                continue
            item=in_range[key]
            actor,partner=item['npc'],item['partner']
            if actor in reserved or partner in self.encounter_waits or not self.s.available(actor,actor) or not self.s.onstage(partner):
                continue
            reverse=pursuits.get(partner)
            mutual=reverse and reverse['partner']==actor and reverse['tier']==item['tier']
            if mutual and not self.blocks_interaction(actor,reverse=partner) and not self.blocks_interaction(partner,reverse=actor):
                item,other=(reverse,item) if random.choice((item,reverse)) is reverse else (item,reverse)
                self.finish_encounter(other['id'],'completed','双方互相寻找，合并为同一次邀约')
            else:
                current=self.current_item(partner)
                if (current and current['tier']=='high') or any(task['tier']=='high' for task in self.due(partner)):
                    continue
                if partner in self.s.busy and not self.s.preempt_conversation(
                        partner,'先处理附近的来访',before_speech=True):
                    continue
                if partner in reserved:
                    outgoing=pursuits.get(partner)
                    if outgoing is None or self.encounter_waits.get(outgoing['partner'])!=outgoing['id']:
                        continue
                    self.finish_encounter(outgoing['id'],'interrupted','找人者先处理附近的来访')
                    reserved=self.encounter_participants()
            event,_=self.encounter_requests[item['id']]
            self.s.boards.suspend_for_encounter(event.actor,now)
            runtime.begin_external(event,'waiting')
            self.encounter_waits[event.actor]=item['id']
            runtime.changed(event.id)
            reserved.update((actor,partner))

    def _talk(self,item,now,elapsed):
        name,partner=item['npc'],item['partner']
        if item['id'] in self.s.scheduler.event_talks:
            return
        if name in self.encounter_waits or self.s.player.holds(name):
            return
        player_talking=self.s.player.holds(partner)
        reservation=self.encounter_waits.get(partner)
        own_reservation=reservation==item['id']
        area=AREAS[self.s.world.area_of(partner)]['id']
        if item['area']!=area:
            item['area']=area
            item.pop('interaction_point',None)
            self._save(item)
        self.s.world.activity_targets[name]=area
        point=item.get('interaction_point')
        target=self.s.positions.get(partner)
        position=self.s.positions.get(name)
        at_point=(point and position and position['area']==area and not position['travelling']
                  and 0<=now-position['at']<=SOCIAL.position_max_age_seconds
                  and math.hypot(position['x']-point['x'],position['y']-point['y'])<=4)
        if player_talking and self.s.nearby(name,partner,now):
            item['encounter_stop']=True
            self._state(item,'waiting','已到对方身边，等待玩家交谈结束')
            return
        stopped=bool(not player_talking and own_reservation and target and target.get('encounter_wait')==reservation)
        if stopped and self.s.nearby(name,partner,now):
            item['encounter_stop']=True
        else:
            item.pop('encounter_stop',None)
        if (stopped and self.s.available(name,partner)
                and self.s.valid_pair(name,partner) and len(self.s.busy)<SOCIAL.max_conversations*2):
            self._state(item,'waiting','已遇见，等待对方决定是否交谈')
            reverse=self.current_item(partner) if own_reservation else None
            if reverse and (reverse['partner']!=name or reverse['source'] not in ('event_followup','whisper')):
                reverse=None
            self.s.scheduler.submit_event_talk(item,reverse)
            return
        if item.get('encounter_stop'):
            self._state(item,'waiting','已到对方身边，等待站定确认')
        else:
            # 对方已站定时可立即更新落点；远处追踪先走完当前一段。
            if point is None or ((at_point or stopped) and target and target['area']==area and
                                 math.hypot(target['x']-point['x'],target['y']-point['y'])>SOCIAL.encounter_radius):
                new_point=self.s.interaction_point(name,partner,now)
                if new_point:
                    item['interaction_point']=new_point
            if at_point and item['interaction_point']==point and self._within_encounter_range(item,now):
                self._state(item,'waiting','已到对方附近，等待会面')
            elif player_talking:
                self._state(item,'travelling','前往对方附近，等待玩家交谈结束')
            else:
                self._state(item,'travelling','前往对方所在地，等待实际遇见')
        if player_talking:
            return
        item['travel_elapsed']+=max(0,elapsed)
        if item['travel_elapsed']>=ACTIVITY.travel_timeout_seconds:
            self.finish_event_talk(item,'timed_out','未能在期限内联系到对方')

    def finish_event_talk(self,item,state,detail,*,shared_event_id=None):
        identity=self.s.event_runtime.activity_id(item)
        self.finish_encounter(item['id'],state,detail)
        updated=dict(item,state=state,detail=detail)
        facts=self._facts(item,state,detail)
        if shared_event_id:
            facts['shared_event_id']=shared_event_id
        with self.s.database() as db:
            self.save_activity(db,updated)
            if self.s.event_runtime.close(db,identity,facts) and not shared_event_id:
                self.s.event_runtime.queue(db,identity,(item['npc'],),'fact')
        item.update(updated)
        self.current.pop(item['npc'],None)
        self.s.world.activity_targets.pop(item['npc'],None)
        self.s.event_runtime.changed(identity)

    def _reserve_meeting(self, item):
        pair={item['npc'],item['partner']}
        meetings=self.s.active_meetings()
        existing=next((m for m in meetings if {m['a'],m['b']}==pair),None)
        if existing:return existing
        if len(meetings)>=50:return None
        return self.s.request_meeting(item['npc'],item['partner'],item['area'],trigger='围绕项目与学习目标交流',
                                      activity_id=item['id'])

    def tick(self, now, elapsed):
        previous_waits=self.encounter_waits.copy()
        if not ACTIVITY.enabled:
            self.s.world.activity_targets.clear()
            for key in list(self.encounter_requests):
                self.finish_encounter(key,'interrupted','动态活动已关闭')
            return bool(previous_waits)
        self._prune()
        for name in self.s.npcs:
            if self.s.onstage(name):
                self.ensure_day(name)
        self._poll_model(now)
        if now>=self.scan_at:
            self.scan_at=now+ACTIVITY.context_scan_seconds
            for event in self.s.world.events:
                if event.get('kind')=='notice_read':continue
                if self.s.world.clock.now-event.get('game_time',self.s.world.clock.now)>ACTIVITY.history_days*1440:continue
                self.notice(event['text'],event['participants'],event.get('key',str(event['id'])),event.get('game_time'))
            if not self.s.event_runtime.model_ready:
                self._scan_memories()
        rate=self.s.speed/SOCIAL.speed
        clock=self.s.world.clock.now
        expiry_notice=max(ACTIVITY.pending_expiry_notice_minutes,self.s.speed*SOCIAL.tick_seconds)
        progress=[]
        routine_progress=set()
        pursuits={}
        for name in self.s.npcs:
            if not self.s.onstage(name):
                continue
            held=self.s.player.holds(name)
            item=self.current_item(name)
            if item is None and not held:
                item=self._resume_suspended(name)
            can_pause_baseline=(item is not None and item['source']=='baseline'
                                and item['state']!='evaluating' and not item['meeting_id']
                                and now-self.s.seen.get(name,-1e9)<SOCIAL.presence_seconds)
            candidates=[]
            for candidate in self.records.values():
                if candidate['npc']!=name or candidate['state']!='scheduled' or candidate['starts_at']>clock:
                    continue
                remaining=self.deadline(candidate)-clock
                if remaining<0:
                    detail='尚未出发时活动窗口已过'
                    if held:
                        detail='等待玩家交谈结束时活动窗口已过'
                    elif item and item['tier']=='high':
                        detail='等待玩家指令完成时活动窗口已过'
                    elif name in self.s.busy or any(name in pair for pair in self.s.active_conversations.values()):
                        detail='等待当前对话结束时活动窗口已过'
                    self._state(candidate,'timed_out',detail)
                    continue
                if candidate['tier']=='medium' and name in self.routine_turns and not (
                        remaining<=expiry_notice and (item is None or can_pause_baseline)):
                    continue
                candidates.append(candidate)
            if held:
                continue
            candidates.sort(key=self.queue_key)
            winner=candidates[0] if candidates else None
            if winner and winner['tier']=='high' and name in self.s.busy:
                reason=f"玩家指派「{winner['title']}」接管，结束交谈"
                if self.s.preempt_conversation(name,reason):
                    winner['reason']=reason
                    item=self.current_item(name) or self._resume_suspended(name)
            if winner and name not in self.s.busy and name not in self.s.reflecting:
                tier=winner['tier']
                if tier=='high' and name in self.encounter_waits:
                    detail='玩家指派接管，结束相遇等待'
                    self.finish_encounter(self.encounter_waits[name],'interrupted',detail)
                    winner['reason']=detail
                if tier=='high' and (name in self.s.reading|self.s.reading_waiting|self.s.notice_approaching
                                      or name in self.s.boards.suspended):
                    if name in self.s.reading|self.s.notice_approaching or name in self.s.boards.suspended:
                        winner['reason']='玩家指派接管，结束公告阅读'
                    self.s.boards.cancel(name)
                feasible=not self._blocked(name,winner,preempting=item)
                imminent_baseline_switch=(tier=='low' and can_pause_baseline and winner['source']!='baseline'
                                          and self.deadline(winner)-clock<=expiry_notice)
                if (item and feasible and (tier=='high' or name not in self.encounter_waits)
                        and (TIERS.index(tier)>TIERS.index(item['tier']) or imminent_baseline_switch)
                        and (item['state']!='evaluating' or tier=='high')):
                    if item['tier']=='low':
                        self._suspend_routine(item)
                        item=None
                    elif self.cancel(name,item['id'],f"玩家指派「{winner['title']}」接管"):
                        winner['reason']=f"玩家指派接管，结束{item['title']}"
                        item=None
            if name in self.encounter_waits:
                continue
            if item is None:
                chosen=winner
                if chosen is None:
                    area=self.s.world.schedule_area(self.s.cards[name],self.s.world.clock.now)
                    chosen=self._make(name,'baseline',area,self.s.world.clock.now,'baseline','基础作息')
                blocked=self._blocked(name,chosen)
                if blocked:
                    # 保持scheduled，不为尚未开始的活动锁定另一条目的地。
                    continue
                if not self._start(chosen):continue
                item=chosen
            if item['state']=='evaluating':
                continue
            with self.s.database() as db:
                focus,_=self.s.game_events.focus(db,name)
            if focus!=self.s.event_runtime.activity_id(item):
                self._state(item,'waiting','暂挂，等待当前事件结束')
                continue
            if item['source'] in ('event_followup','whisper') and item['partner']:
                pursuits[name]=item
                continue
            if item['meeting_id']:
                meeting=self.s.meeting(item['meeting_id'])
                if meeting is None:
                    # 会面队列不持久化；重启后重新排队，继续要求双方实际抵达。
                    meeting=self._reserve_meeting(item)
                    if meeting is None:
                        self._state(item,'timed_out','重启后会面队列已满，请重新安排')
                        self.current.pop(name,None)
                        continue
                    item['meeting_id']=meeting['id']
                    self._save(item)
                if meeting and meeting['state'] in ('completed','failed','cancelled'):
                    self._state(item,'completed' if meeting['state']=='completed' else 'timed_out',meeting['detail'])
                    self.current.pop(name,None)
                    if name in self.routine_turns and item['tier']=='low':
                        routine_progress.add(name)
                continue
            blocked=self._blocked(name,item)
            online=now-self.s.seen.get(name,-1e9)<SOCIAL.presence_seconds
            if blocked or not online:
                self._state(item,'waiting',blocked or '行程暂缓，等待恢复连接')
                continue
            self.s.world.activity_targets[name]=item['area']
            if item['phase']=='active' and AREAS[self.s.world.area_of(name)]['id']!=item['area']:
                item.update(phase='travelling',travel_elapsed=0.0)
                self._state(item,'travelling','会面结束后返回原活动地点')
            if item['state']=='waiting':self._state(item,item['phase'],'继续已有行程或停留')
            if item['phase']=='travelling':
                item['travel_elapsed']+=max(0,elapsed)*rate
                if item['travel_elapsed']>=ACTIVITY.travel_timeout_seconds:
                    self._state(item,'timed_out','行走超时，检查路径或重新安排')
                    self.current.pop(name,None)
                    self.s.world.activity_targets.pop(name,None)
                    if name in self.routine_turns and item['tier']=='low':
                        routine_progress.add(name)
            else:
                item['stay_elapsed']+=max(0,elapsed)*rate
                minutes=max(0,elapsed)*self.s.speed
                item['dwell_minutes']+=minutes
                if name in self.routine_turns and item['tier']=='low' and minutes>0:
                    routine_progress.add(name)
                ready=item['dwell_minutes']>=item['duration'] and (
                    item['source']=='whisper' or item['stay_elapsed']>=ACTIVITY.min_stay_seconds)
                if item['kind']=='baseline' and not item.get('continuation'):
                    next_area=self.s.world.schedule_area(self.s.cards[name],self.s.world.clock.now)
                    ready=ready or item['stay_elapsed']>=ACTIVITY.min_stay_seconds and (
                        AREAS[next_area]['id']!=item['area'] or bool(candidates))
                if ready:
                    self._checkpoint(item)
            if elapsed>0:
                progress.append(item)
        pursuits={name:item for name,item in pursuits.items() if self.current_item(name) is item}
        self._reserve_encounters(pursuits,now)
        for item in pursuits.values():
            self._talk(item,now,elapsed)
            progress.append(item)
        if progress or routine_progress:
            with self.s.database() as db:
                for item in progress:self.save_activity(db,item)
                for name in routine_progress:
                    db.execute('DELETE FROM npc_routine_turn WHERE npc=?',(name,))
                self.routine_turns.difference_update(routine_progress)
                db.execute("INSERT OR REPLACE INTO social_state VALUES('clock',?)",(str(self.s.world.clock.now),))
        self.s.event_runtime.poll(now)
        self._request_model(now)
        return previous_waits!=self.encounter_waits

    def _request_model(self, now):
        configured=bool(getattr(getattr(self.s.llm,'cfg',None),'api_key',''))
        if not ACTIVITY.model_enabled or not configured or self.job or now<self.next_model:return
        for name in self.s.npcs:
            key=(name,self.day)
            if not self.s.onstage(name) or key in self.model_attempted:continue
            context=self.context(name)
            self.model_attempted.add(key)
            with self.s.database() as db:db.execute('UPDATE npc_plan_days SET model_status=? WHERE npc=? AND day=?',('pending',*key))
            future=self.pool.submit(self.s.engine.agents[name].plan_day,context)
            self.job=dict(future=future,npc=name,day=self.day,start=now,expired=False,context=context)
            self.next_model=now+ACTIVITY.model_interval_seconds
            break

    def _poll_model(self, now):
        if not self.job:return
        job=self.job
        if not job['expired'] and now-job['start']>ACTIVITY.model_result_deadline_seconds:
            job['expired']=True
            self._model_status(job,'timeout')
        if not job['future'].done():
            return  # 旧请求完成前不再开新请求，限制并发和调用成本。
        self.job=None
        try:result=job['future'].result()
        except Exception:result=None
        if job['expired'] or job['day']!=self.day:return
        valid=self.apply_model(job['npc'],job['day'],result,job['context'])
        self._model_status(job,'model' if valid else 'invalid')

    def _model_status(self, job, status):
        with self.s.database() as db:db.execute('UPDATE npc_plan_days SET model_status=? WHERE npc=? AND day=?',(status,job['npc'],job['day']))

    def apply_model(self, name, day, result, context):
        try:
            plan=result if isinstance(result,DailyPlan) else DailyPlan.model_validate(result)
            allowed={(a['kind'],a['area']) for a in context['places']}
            for task in plan.activities:
                starts=day*1440+task.start_minute
                if (task.kind,task.area) not in allowed or starts<self.s.world.clock.now or day!=self.day:return False
                if task.basis=='memory' and not (context['memories'] or context.get('insights')):return False
                if task.basis=='event' and not context['known_events']:return False
                if task.basis=='relationship' and not context['friends']:return False
                if task.kind=='visit' and (not self.s.onstage(task.partner) or task.partner not in {f['name'] for f in context['friends']}):return False
                if task.kind!='visit' and task.partner:return False
            ordered=sorted(plan.activities,key=lambda a:a.start_minute)
            if any(a.start_minute+a.duration>b.start_minute for a,b in zip(ordered,ordered[1:])):return False
        except (ValueError,TypeError):return False
        for item in list(self.records.values()):
            if item['npc']==name and item['day']==day and item['source']=='rule' and item['state']=='scheduled':
                self._state(item,'cancelled','由经过校验的每日模型计划替代')
        for index,task in enumerate(plan.activities):
            self._make(name,task.kind,task.area,day*1440+task.start_minute,'model',
                       task.reason.strip() or self._reason(name,task.kind,'model',task.partner),duration=task.duration,
                       key=f'model:{index}',partner=task.partner)
        return True

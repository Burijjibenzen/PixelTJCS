"""每日计划、知识范围内的事件响应及有到达确认的活动状态机。

不改变实际位置。仅通过World.activity_targets下发目的地，Godot完成寻路与上报。
"""
import hashlib
import json
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from backend.activity_catalog import CATALOG, DailyPlan
from backend.config import ACTIVITY, SOCIAL, WORLD
from backend.world import AREAS

TERMINAL = {'completed', 'cancelled', 'timed_out'}
STATE_TEXT = {'scheduled':'尚未开始','waiting':'等待','travelling':'正在前往','active':'已到达／活动中',
              'completed':'已完成','cancelled':'已取消','timed_out':'已超时'}

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
        self.generated = set()
        self.model_attempted = set()
        self.signals = set()
        self.scan_at = 0.0
        self.next_model = 0.0
        self.job = None
        self.pruned_day = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daily-plan')
        with service.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS npc_activities(id TEXT PRIMARY KEY,npc TEXT,day INTEGER,payload TEXT);
                CREATE TABLE IF NOT EXISTS npc_plan_days(npc TEXT,day INTEGER,model_status TEXT,PRIMARY KEY(npc,day));
                CREATE TABLE IF NOT EXISTS npc_plan_signals(npc TEXT,key TEXT,day INTEGER,PRIMARY KEY(npc,key));
            ''')
            for row in db.execute('SELECT payload FROM npc_activities'):
                item = json.loads(row[0])
                if item['npc'] in service.npcs and item['area'] in AREAS and (not item['partner'] or item['partner'] in service.npcs):
                    self.records[item['id']] = item
                    if item['state'] in ('travelling','active','waiting'):
                        self.current[item['npc']] = item['id']
            for name, day, status in db.execute('SELECT npc,day,model_status FROM npc_plan_days'):
                self.generated.add((name, day))
                if status != 'rule': self.model_attempted.add((name, day))
            self.signals = set(db.execute('SELECT npc,key FROM npc_plan_signals'))

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
            db.execute('INSERT OR REPLACE INTO npc_activities VALUES(?,?,?,?)',
                       (item['id'],item['npc'],item['day'],json.dumps(item,ensure_ascii=False)))

    def _state(self, item, state, detail=''):
        if item['state'] == state and item.get('detail','') == detail:
            return
        item.update(state=state,detail=detail)
        self._save(item)
        self.s.emit('activity_changed',npc=item['npc'],activity=self.public(item))

    def _make(self, name, kind, area, starts_at, source, reason, priority=20, duration=None, key=None, partner=''):
        area = AREAS[area]['id']
        day = int(starts_at // WORLD.minutes_per_day)
        identity = uuid.uuid5(uuid.NAMESPACE_URL,f'{name}:{day}:{key}').hex if key else uuid.uuid4().hex
        if identity in self.records:
            return self.records[identity]
        item = dict(id=identity,npc=name,day=day,kind=kind,area=area,
                    title=CATALOG[kind]['title'] if kind in CATALOG else '基础作息' if kind=='baseline' else '手动指派',
                    reason=reason,source=source,priority=priority,starts_at=starts_at,
                    duration=duration or ACTIVITY.dwell_minutes,state='scheduled',phase='travelling',
                    travel_elapsed=0.0,stay_elapsed=0.0,dwell_minutes=0.0,arrived_at=None,
                    partner=partner,meeting_id='',detail='等待开始')
        self.records[identity]=item
        self._save(item)
        return item

    def current_item(self, name):
        item=self.records.get(self.current.get(name,''))
        return item if item and item['state'] not in TERMINAL else None

    def manual(self, name):
        if not ACTIVITY.enabled:return False
        return any(a['npc']==name and a['source']=='manual' and a['state'] not in TERMINAL for a in self.records.values())

    def eligible_meetings(self):
        return [m for m in self.s.active_meetings() if not any(self.manual(n) for n in (m['a'],m['b']))]

    def public(self, item):
        result={k:item[k] for k in ('id','kind','title','area','reason','source','priority','starts_at','duration','state','arrived_at','detail')}
        for key in ('reason','detail'):result[key]=DISPLAY_COPY.get(result[key],result[key])
        return result

    def describe(self, name):
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

    def listing(self, name):
        return dict(current=self.describe(name),activities=[self.public(a) for a in sorted(
            (a for a in self.records.values() if a['npc']==name),key=lambda a:a['starts_at'])][-20:])

    def _friends(self, name):
        return [dict(name=other,score=self.s.relations.get(name,other)) for other in self.s.npcs
                if other!=name and self.s.relations.get(name,other)>=ACTIVITY.friend_threshold]

    def context(self, name):
        # 只读取本人活跃记忆、本人收到的事件和本人关系；不传其它人的卡片或秘密。
        memories=self.s.memory.get_active(name,6)
        return dict(day=self.day,minute=int(self.s.world.clock.now % WORLD.minutes_per_day),
                    base_schedule=self.s.cards[name].schedule,
                    memories=[dict(id=m.id,type=m.type,content=m.content) for m in memories],
                    known_events=[dict(text=e['text'],game_time=e.get('game_time')) for e in self.s.world.events[-30:]
                                  if name in e['participants']],
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
        kind=self.notice_kind(text)
        if kind is None:return False
        now=self.s.world.clock.now
        received_at=now if received_at is None else received_at
        starts=int(received_at//1440)*1440
        if '明天' in text:starts+=1440
        clock=re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)',text)
        starts+=int(clock[1])*60+int(clock[2]) if clock else 9*60 if '明天' in text else received_at%1440
        appointment = starts
        appointment_given=bool(clock or '明天' in text)
        starts=max(now,starts-ACTIVITY.appointment_lead_minutes) if appointment_given else max(now,starts)
        for name in participants:
            signal=(name,str(key))
            if name not in self.s.npcs or signal in self.signals:continue
            self.signals.add(signal)
            self._remember_signal(signal)
            # 通知已经安排活动，不再把同一条观察记忆当作第二个即时邀约。
            for memory in self.s.memory.get_active(name,6):
                if memory.content==text:self._remember_signal((name,'memory:'+memory.id))
            if '取消' in text or '不用参加' in text:
                for item in list(self.records.values()):
                    if item['npc']==name and item['kind']==kind and item['source']=='event' and item['state'] not in TERMINAL:
                        self.cancel(name,item['id'],'本人收到取消通知')
                continue
            count=sum(a['npc']==name and a['day']==self.day and a['source']=='event' for a in self.records.values())
            if count>=ACTIVITY.event_reactions_per_day:continue
            if appointment_given and appointment<now-1:continue
            if not appointment_given and now-received_at>ACTIVITY.pending_expiry_minutes:continue
            rooms=[a for a in CATALOG[kind]['rooms'] if a in AREAS]
            if not rooms:continue
            explicit=[a for a in rooms if AREAS[a]['room_id'] in text or
                      AREAS[a]['room_id'].removeprefix('zx2_') in text.lower() or
                      AREAS[a]['name'].split('·')[-1] in text]
            room=explicit[0] if explicit else self.rng(str(key),self.day).choice(rooms)
            self._make(name,kind,room,starts,'event',self._reason(name,kind,'event'),priority=70,key=str(key))
        return True

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
            count=sum(a['npc']==name and a['day']==self.day and a['source']=='memory' for a in self.records.values())
            if count>=ACTIVITY.memory_reactions_per_day:continue
            for memory in self.s.memory.get_active(name,6):
                signal=(name,'memory:'+memory.id)
                if signal in self.signals:continue
                kind=self.notice_kind(memory.content)
                # 聊天记忆只触发软性查资料/运动机会，不能当作强制官方通知。
                if kind not in ('library','exercise'):continue
                if any(word in memory.content for word in ('取消','不去','不用','不需要','别去')) or re.search(r'\d[:：]\d',memory.content):continue
                self._remember_signal(signal)
                self._make(name,kind,CATALOG[kind]['rooms'][0],self.s.world.clock.now,'memory',
                           self._reason(name,kind,'memory'),priority=40,key=signal[1])
                break

    def command(self, name, area):
        if name not in self.s.npcs or area not in AREAS:return False
        if not ACTIVITY.enabled:return self.s.world.assign_override(name,area)
        for item in list(self.records.values()):
            if item['npc']==name and item['source']=='manual' and item['state'] not in TERMINAL:
                self.cancel(name,item['id'],'由新的手动指派替代')
        self.s.world.override_area.pop(name,None)
        self.s.world.override_slot.pop(name,None)
        self._make(name,'manual',area,self.s.world.clock.now,'manual','用户手动指派，抵达并短暂停留后恢复日程',
                   priority=100,duration=30)
        return True

    def cancel(self, name, identity, reason='用户取消活动'):
        item=self.records.get(identity)
        if not item or item['npc']!=name:return False
        if item['state'] in TERMINAL:return True
        if item['meeting_id']:
            meeting=self.s.meeting(item['meeting_id'])
            if meeting and meeting['state'] in ('deciding','talking'):return False
            if meeting:self.s.cancel_meeting(meeting['id'])
        self._state(item,'cancelled',reason)
        if self.current.get(name)==identity:
            self.current.pop(name,None)
            self.s.world.activity_targets.pop(name,None)
        return True

    def arrived(self, name, confirmed=None, activity_id=None):
        if not ACTIVITY.enabled:return
        item=self.current_item(name)
        if not item or item['meeting_id'] or confirmed is False:return
        if activity_id is not None and activity_id!=item['id']:return
        if AREAS[self.s.world.area_of(name)]['id']==item['area'] and item['phase']=='travelling':
            item.update(phase='active',arrived_at=self.s.world.clock.now,stay_elapsed=0.0,dwell_minutes=0.0)
            self._state(item,'active','已到达，正在进行活动')

    def _blocked(self, name, item):
        if name in self.s.notice_approaching:return '正在走近公告栏，随后继续原行程'
        if name in self.s.reading_waiting:return '已安排阅读公告，等待当前任务结束'
        if name in self.s.reading:return '正在阅读公告，稍后继续行程'
        if name in self.s.busy or name in self.s.reflecting:return '正在交谈或整理思绪，稍后继续行程'
        if not self.s.available(name,name):return '等待角色空闲'
        if item['source']!='manual' and any(name in (m['a'],m['b']) for m in self.eligible_meetings()):return '先处理已有会面，之后恢复活动'
        return ''

    def _start(self, item):
        name=item['npc']
        self.current[name]=item['id']
        if item['partner']:
            meeting=self._reserve_meeting(item)
            if meeting is None:
                self._state(item,'timed_out','会面队列已满，回到基础日程')
                self.current.pop(name,None)
                return False
            item['meeting_id']=meeting['id']
            self._state(item,'waiting','已申请会面，等待双方空闲和实际抵达')
            return True
        self.s.world.activity_targets[name]=item['area']
        self._state(item,'travelling','正在前往目的地')
        return True

    def _reserve_meeting(self, item):
        pair={item['npc'],item['partner']}
        meetings=self.s.active_meetings()
        existing=next((m for m in meetings if {m['a'],m['b']}==pair),None)
        if existing:return existing
        if len(meetings)>=50:return None
        return self.s.request_meeting(item['npc'],item['partner'],item['area'],trigger='围绕项目与学习目标交流')

    def tick(self, now, elapsed):
        if not ACTIVITY.enabled:
            self.s.world.activity_targets.clear()
            return
        self._prune()
        for name in self.s.npcs:self.ensure_day(name)
        self._poll_model(now)
        if now>=self.scan_at:
            self.scan_at=now+ACTIVITY.context_scan_seconds
            for event in self.s.world.events:
                if self.s.world.clock.now-event.get('game_time',self.s.world.clock.now)>ACTIVITY.history_days*1440:continue
                self.notice(event['text'],event['participants'],event.get('key',str(event['id'])),event.get('game_time'))
            self._scan_memories()
        rate=self.s.speed/SOCIAL.speed
        for name in self.s.npcs:
            item=self.current_item(name)
            candidates=sorted((a for a in self.records.values() if a['npc']==name and a['state']=='scheduled'
                               and a['starts_at']<=self.s.world.clock.now), key=lambda a:(-a['priority'],a['starts_at']))
            for candidate in candidates[:]:
                if candidate['source']!='manual' and self.s.world.clock.now-candidate['starts_at']>ACTIVITY.pending_expiry_minutes:
                    self._state(candidate,'timed_out','尚未出发时活动窗口已过')
                    candidates.remove(candidate)
            if item and candidates and candidates[0]['priority']>item['priority'] and name not in self.s.busy and name not in self.s.reflecting:
                if item['phase']=='active' or candidates[0]['priority']>=70:
                    if self.cancel(name,item['id'],'被更高优先级安排替代'):item=None
            if item is None:
                chosen=candidates[0] if candidates else None
                if chosen is None:
                    area=self.s.world.schedule_area(self.s.cards[name],self.s.world.clock.now)
                    chosen=self._make(name,'baseline',area,self.s.world.clock.now,'baseline','基础作息',priority=0)
                blocked=self._blocked(name,chosen)
                if blocked:
                    # 保持scheduled，不为尚未开始的活动锁定另一条目的地。
                    continue
                if not self._start(chosen):continue
                item=chosen
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
            else:
                item['stay_elapsed']+=max(0,elapsed)*rate
                item['dwell_minutes']+=max(0,elapsed)*self.s.speed
                ready=item['stay_elapsed']>=ACTIVITY.min_stay_seconds and item['dwell_minutes']>=item['duration']
                if item['kind']=='baseline':
                    next_area=self.s.world.schedule_area(self.s.cards[name],self.s.world.clock.now)
                    ready=item['stay_elapsed']>=ACTIVITY.min_stay_seconds and (AREAS[next_area]['id']!=item['area'] or bool(candidates))
                if ready:
                    self._state(item,'completed','已完成实际到达后的停留')
                    self.current.pop(name,None)
                    self.s.world.activity_targets.pop(name,None)
        self._request_model(now)

    def _request_model(self, now):
        configured=bool(getattr(getattr(self.s.llm,'cfg',None),'api_key',''))
        if not ACTIVITY.model_enabled or not configured or self.job or now<self.next_model:return
        for name in self.s.npcs:
            key=(name,self.day)
            if key in self.model_attempted:continue
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
                if task.basis=='memory' and not context['memories']:return False
                if task.basis=='event' and not context['known_events']:return False
                if task.basis=='relationship' and not context['friends']:return False
                if task.kind=='visit' and task.partner not in {f['name'] for f in context['friends']}:return False
                if task.kind!='visit' and task.partner:return False
            ordered=sorted(plan.activities,key=lambda a:a.start_minute)
            if any(a.start_minute+a.duration>b.start_minute for a,b in zip(ordered,ordered[1:])):return False
        except (ValueError,TypeError):return False
        for item in list(self.records.values()):
            if item['npc']==name and item['day']==day and item['source']=='rule' and item['state']=='scheduled':
                self._state(item,'cancelled','由经过校验的每日模型计划替代')
        for index,task in enumerate(plan.activities):
            self._make(name,task.kind,task.area,day*1440+task.start_minute,'model',
                       self._reason(name,task.kind,'model',task.partner),duration=task.duration,
                       key=f'model:{index}',partner=task.partner)
        return True

"""单机玩家身份与异步对话。模型处理不占世界锁，不把玩家注册为NPC。"""
import json
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext

from pydantic import BaseModel, Field
from backend.config import PLAYER
from backend.config import SOCIAL
from backend.npc import remember_experience
from backend.world import AREAS, ROOMS, CastChanged

_INTERNAL_STATE = object()

class PlayerError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class PlayerRelationImpact(BaseModel):
    delta: float = Field(ge=-PLAYER.relation_max_delta, le=PLAYER.relation_max_delta, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=100)


class PlayerMode:
    def __init__(self, service):
        self.s = service
        self.session = None  # 服务每次启动默认关闭；身份、历史和记忆仍在数据库中。
        self.pending = None
        self.pending_npc = None
        self.engaged_npc = None
        self.engaged_until = 0.0
        self.last_dialogue_id = None
        self.interaction = None
        self.position = None
        self.closed = False
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='player-chat')
        with self.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS player_profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,token TEXT);
                CREATE TABLE IF NOT EXISTS player_sessions(id TEXT PRIMARY KEY,player TEXT NOT NULL,state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS player_turns(id TEXT PRIMARY KEY,session TEXT NOT NULL,
                    request_id TEXT NOT NULL,npc TEXT NOT NULL,message TEXT NOT NULL,payload TEXT NOT NULL,
                    UNIQUE(session,request_id));
            ''')
            if 'token' not in {column[1] for column in db.execute('PRAGMA table_info(player_profiles)')}:
                db.execute('ALTER TABLE player_profiles ADD COLUMN token TEXT')
            db.execute("UPDATE player_sessions SET state='left' WHERE state='active'")
            for turn_id, raw in db.execute('SELECT id,payload FROM player_turns').fetchall():
                item = json.loads(raw)
                if item['status'] != 'processing':continue
                item['status'] = 'completed' if item.get('relation') and len(item.get('memories', {})) == 2 else 'failed'
                if item['status'] == 'failed':item['error'] = '服务已重启，上一轮未完成；已经保存的台词和记忆仍保留。请重新加入后发起新一轮。'
                db.execute('UPDATE player_turns SET payload=? WHERE id=?', (json.dumps(item, ensure_ascii=False), turn_id))

    @contextmanager
    def database(self):
        db = self.s.memory._connect()
        try:
            with db:yield db
        finally:db.close()

    def _emit(self, kind, **data):
        emit = getattr(self.s, 'emit', None)
        if emit:emit(kind, **data)

    def _world_changed(self):
        if hasattr(self.s, 'emit'):self._emit('world', snapshot=self.s.world_snapshot())

    def _profile(self, session_id):
        with self.database() as db:
            row = db.execute('SELECT p.id,p.name FROM player_sessions s JOIN player_profiles p ON p.id=s.player WHERE s.id=?', (session_id,)).fetchone()
        if not row:raise PlayerError(404, '玩家会话不存在')
        return dict(id=row[0], name=row[1])

    def _active(self, session_id):
        if not self.session or session_id != self.session['id']:raise PlayerError(409, '玩家模式未开启或会话已失效，请重新加入')
        return self.session

    def state(self, session_id=_INTERNAL_STATE):
        with self.s.lock:
            active = self.session
            owner = bool(active and (session_id is _INTERNAL_STATE or session_id == active['id']))
            return dict(enabled=bool(active), session_id=active['id'] if owner else None,
                player=dict(active['player']) if owner else None,
                pending_turn=self.pending if owner else None, pending_npc=self.pending_npc if owner else None,
                engaged_npc=self.engaged_npc if owner else None,
                relations=[dict(npc=n, score=self.s.relations.get(active['player']['id'], n),
                    reason=self.s.relations.last_reason(active['player']['id'], n)) for n in self.s.npcs] if owner else [])

    def join(self, name='玩家', session_id=None, player_token=None):
        name = name.strip()
        if not name or len(name) > PLAYER.name_max_chars or any(ord(c) < 32 for c in name):raise PlayerError(400, '请填写1至24字的玩家称呼')
        if name in self.s.npcs or name.startswith('player:'):raise PlayerError(400, '玩家称呼不能与NPC重名或使用内部身份前缀')
        with self.s.lock:
            if self.closed:raise PlayerError(503, '服务正在关闭')
            if self.session:
                if self.session['player']['name'] != name:raise PlayerError(409, '已有玩家加入，请先离开再更换身份')
                if session_id != self.session['id']:
                    saved_token = self._token(self.session['player']['id'])
                    if not saved_token or not player_token or not secrets.compare_digest(saved_token, player_token):
                        raise PlayerError(409, '该玩家会话已开启，请使用原会话或身份凭据继续')
                return dict(self.state(self.session['id']),player_token=self._token(self.session['player']['id']))
            if self.pending:raise PlayerError(409, '上一轮对话仍在处理，结束后可重新加入')
            with self.database() as db:
                row = db.execute('SELECT id,token FROM player_profiles WHERE name=?', (name,)).fetchone()
                if row and row[1] and not secrets.compare_digest(row[1],player_token or ''):
                    raise PlayerError(403, '该称呼已有身份，请使用原客户端保存的身份凭据')
                player = dict(id=row[0] if row else 'player:'+uuid.uuid4().hex, name=name)
                token = (row[1] if row else None) or secrets.token_urlsafe(32)
                if row and not row[1]:db.execute('UPDATE player_profiles SET token=? WHERE id=?',(token,player['id']))
                if not row:db.execute('INSERT INTO player_profiles(id,name,token) VALUES(?,?,?)', (player['id'], name,token))
                session_id = uuid.uuid4().hex
                db.execute("INSERT INTO player_sessions VALUES(?,?,'active')", (session_id, player['id']))
            for npc in self.s.npcs:self.s.relations.set_initial(player['id'], npc, 50)
            self.session = dict(id=session_id, player=player)
            self.position = None
            return dict(self.state(session_id),player_token=token)

    def _token(self, player_id):
        with self.database() as db:
            row=db.execute('SELECT token FROM player_profiles WHERE id=?',(player_id,)).fetchone()
        return row[0] if row else None

    def leave(self, session_id):
        with self.s.lock:
            self._profile(session_id)
            if self.session and self.session['id'] != session_id:raise PlayerError(409, '旧会话不能关闭当前玩家模式')
            self._release_engagement()
            with self.database() as db:db.execute("UPDATE player_sessions SET state='left' WHERE id=?", (session_id,))
            self.session = None
            self.position = None
            # 已接受的一轮继续完成保存，不能在模型仍处理时提前释放NPC。
            return self.state(session_id)

    def _release_engagement(self,*,interruption=''):
        npc = self.engaged_npc
        last_dialogue_id = self.last_dialogue_id
        self.engaged_npc = None
        self.engaged_until = 0.0
        self.last_dialogue_id = None
        if npc and self.pending_npc != npc:
            self.s.busy.discard(npc)
            self._finish_interaction(interruption=interruption)
        if npc and last_dialogue_id:
            self._emit('dialogue_finished',event_key=last_dialogue_id+':visual_end',
                conversation_id=last_dialogue_id,participants=[npc],interrupted=bool(interruption))

    def interrupt(self,npc,reason):
        if self.pending_npc==npc:
            item=self._load(self.pending)
            if item['status']=='completed' and self.interaction:
                self.interaction['turns'].append(dict(id=item['id'],transcript=item['transcript'],relation=item['relation']))
            elif item['status']=='processing':
                item.update(status='failed',error=reason)
                self._save(item)
            if self.last_dialogue_id!=self.pending:
                self._emit('dialogue_finished',conversation_id=self.pending,participants=[npc],interrupted=True)
            self.pending=self.pending_npc=None
            self.s.busy.discard(npc)
        elif self.engaged_npc!=npc:
            return False
        self._release_engagement(interruption=reason)
        self._finish_interaction(interruption=reason)
        return True

    def _begin_interaction(self,npc,player):
        runtime=getattr(self.s,'event_runtime',None)
        if runtime is None or self.interaction is not None:
            return
        event=runtime.make('player:'+uuid.uuid4().hex,'player_conversation',npc,
                           f'与{player["name"]}交谈','player',player['id'],
                           tier='medium',decision_reason='玩家在附近主动交谈')
        runtime.begin_external(event,'active')
        self.interaction=dict(event=event,turns=[])

    def _finish_interaction(self,*,interruption=''):
        if self.interaction is None:
            return
        interaction,self.interaction=self.interaction,None
        event=interaction['event']
        turns=interaction['turns']
        result=dict(status='cancelled' if interruption else 'completed',
                    detail=interruption or f'完成 {len(turns)} 轮交谈',turns=turns)
        with self.s.database() as db:
            self.s.event_runtime.close(db,event.id,result)
            if turns and not interruption:
                self.s.event_runtime.queue(db,event.id,event.participants,'fact')
        self.s.event_runtime.changed(event.id)
        self.s.queue_overheard(event.id)

    def report_position(self, session_id, map_id, room_id, x, y):
        with self.s.lock:
            self._active(session_id)
            room = ROOMS.get((map_id, room_id))
            if room is None:raise PlayerError(400, '玩家位置不属于已知房间')
            self.position = dict(area=room['id'], x=x, y=y, at=time.monotonic())
            return dict(ok=True)

    def _near(self, npc):
        now = time.monotonic()
        player = self.position
        other = self.s.positions.get(npc)
        area = AREAS[self.s.world.area_of(npc)]['id']
        return bool(player and other and player['area'] == area == other['area']
            and all(0 <= now - position['at'] <= SOCIAL.position_max_age_seconds
                    for position in (player, other))
            and ((player['x'] - other['x']) ** 2 + (player['y'] - other['y']) ** 2)
                <= PLAYER.chat_radius ** 2)

    def engage(self, session_id, npc):
        with self.s.lock:
            session=self._active(session_id)
            if npc not in self.s.npcs:raise PlayerError(404, '角色不存在')
            if self.s.paused:raise PlayerError(409, '世界暂停中，继续后再交谈')
            if not self._near(npc):raise PlayerError(409, '请靠近角色，等待双方位置同步后再交谈')
            if self.pending and self.pending_npc != npc:raise PlayerError(409, '上一轮对话仍在处理')
            if self.engaged_npc != npc:
                if not self.s.available(npc,npc):raise PlayerError(409, '对方正在处理其他事情，请稍后再试')
                planner=getattr(self.s,'planner',None)
                if planner and planner.blocks_interaction(npc):
                    raise PlayerError(409, '角色正在执行受保护的日程或更优先的活动')
                self._release_engagement()
                self.engaged_npc = npc
                self.s.busy.add(npc)
                self._begin_interaction(npc,session['player'])
            self.engaged_until = time.monotonic() + PLAYER.engage_lease_seconds
            self._world_changed()
            return self.state(session_id)

    def disengage(self, session_id):
        with self.s.lock:
            self._active(session_id)
            self._release_engagement()
            self._world_changed()
            return self.state(session_id)

    def expire_engagement(self, now):
        if self.engaged_npc and now >= self.engaged_until:
            self._release_engagement()

    def _load(self, turn_id):
        with self.database() as db:row = db.execute('SELECT payload FROM player_turns WHERE id=?', (turn_id,)).fetchone()
        if not row:raise PlayerError(404, '对话轮次不存在')
        return json.loads(row[0])

    def _save(self, item):
        with self.database() as db:
            db.execute('UPDATE player_turns SET payload=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))

    def turn(self, session_id, turn_id):
        player = self._profile(session_id)
        item = self._load(turn_id)
        if item['player']['id'] != player['id']:raise PlayerError(404, '对话轮次不存在')
        return item

    def history(self, session_id, npc=None, limit=None):
        player = self._profile(session_id)
        if npc is not None and npc not in self.s.npcs:raise PlayerError(404, '角色不存在')
        with self.database() as db:
            rows = db.execute('''SELECT t.payload FROM player_turns t JOIN player_sessions s ON s.id=t.session
                WHERE s.player=? AND (? IS NULL OR t.npc=?) ORDER BY t.rowid DESC LIMIT ?''',
                (player['id'], npc, npc, limit or PLAYER.history_limit)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def memories(self, session_id):
        player = self._profile(session_id)
        return dict(player=player, active=[m.to_dict() for m in self.s.memory.get_active(player['id'], PLAYER.history_limit)],
            forgotten=[m.to_dict() for m in self.s.memory.get_forgotten(player['id'], PLAYER.history_limit)])

    def submit(self, session_id, npc, message, request_id):
        message = message.strip()
        if not message or len(message) > PLAYER.message_max_chars:raise PlayerError(400, '请输入1至500字的对话内容')
        if not request_id or len(request_id) > 100:raise PlayerError(400, '请提供有效的请求编号')
        with self.s.lock:
            with self.database() as db:
                row = db.execute('SELECT id,npc,message FROM player_turns WHERE session=? AND request_id=?', (session_id, request_id)).fetchone()
            if row:
                if (npc, message) != (row[1], row[2]):raise PlayerError(409, '同一请求编号不能用于不同内容')
                return self._load(row[0])
            session = self._active(session_id)
            if self.closed:raise PlayerError(503, '服务正在关闭')
            if npc not in self.s.npcs:raise PlayerError(404, '角色不存在')
            if getattr(self.s, 'paused', False):raise PlayerError(409, '世界暂停中，继续后再交谈')
            if not self._near(npc):raise PlayerError(409, '请靠近角色，等待双方位置同步后再发送')
            if self.pending:raise PlayerError(409, '正在等待上一轮回复，请稍后再发送')
            if self.engaged_npc and self.engaged_npc != npc:raise PlayerError(409, '请先结束当前角色的交谈')
            available = getattr(self.s, 'available', lambda a,b: a not in self.s.busy)
            if self.engaged_npc != npc and not available(npc, npc):raise PlayerError(409, '对方正在交谈、阅读或整理思绪，请稍后再试')
            planner=getattr(self.s,'planner',None)
            if self.engaged_npc != npc and planner and planner.blocks_interaction(npc,continuing=True):
                raise PlayerError(409, '对方正在执行同档或更高档的活动，请稍后再试')
            if hasattr(self.s, 'sync_clock'):self.s.sync_clock()
            turn_id = uuid.uuid4().hex
            item = dict(id=turn_id, conversation_id=uuid.uuid5(uuid.NAMESPACE_URL, session_id+':'+npc).hex,
                request_id=request_id, session_id=session_id, player=dict(session['player']), npc=npc,
                status='processing', message=message, reply=None, transcript=[], memories={}, relation=None,
                game_time=self.s.world.clock.now, area=self.s.world.area_of(npc), error=None)
            with self.database() as db:
                db.execute('INSERT INTO player_turns VALUES(?,?,?,?,?,?)',
                    (turn_id, session_id, request_id, npc, message, json.dumps(item, ensure_ascii=False)))
            self.pending = turn_id
            self.pending_npc = npc
            self.s.busy.add(npc)
            self._begin_interaction(npc,session['player'])
            stamp=self.s.cast_stamp(npc) if hasattr(self.s,'cast_stamp') else None
            try:self.jobs[turn_id] = self.pool.submit(self._work, dict(item, memories={}, transcript=[]),stamp)
            except RuntimeError:
                self.pending = None;self.pending_npc = None
                if self.engaged_npc != npc:
                    self.s.busy.discard(npc)
                    self._finish_interaction()
                item.update(status='failed', error='服务正在关闭，本轮未能开始');self._save(item)
                raise PlayerError(503, item['error'])
            self._world_changed()
            return item

    def chat(self, npc, message, session_id=None, request_id=None):
        """兼容原服务方法；HTTP前端使用submit+查询，不等待模型。"""
        with self.s.lock:
            session_id = session_id or (self.session['id'] if self.session else '')
        item = self.submit(session_id, npc, message, request_id or uuid.uuid4().hex)
        job = self.jobs.get(item['id'])
        if job:job.result()
        return self._load(item['id'])

    def _work(self,item,stamp=None):
        if stamp is None:
            self._play_turn(item,nullcontext)
        else:
            @contextmanager
            def active_turn():
                with self.s.cast_step():
                    if self.pending!=item['id']:
                        raise CastChanged('对话已中断')
                    yield
            with self.s.cast_task(stamp):
                self._play_turn(item,active_turn)

    def _play_turn(self,item,step):
        turn_id = item['id']
        npc = item['npc'];player = item['player'];speaker = '玩家·'+player['name']
        try:
            history = []
            for previous in self.history(item['session_id'], npc, PLAYER.context_turns+1):
                if previous['status'] == 'completed':history.extend(previous['transcript'])
            line = dict(speaker=speaker, text=item['message'])
            history.append(line)
            with step():
                self._emit('dialogue_line', event_key=turn_id+':player', conversation_id=turn_id,
                    sequence=1, participants=[npc], speaker=speaker, text=item['message'], activity='player_chat')
            score = self.s.relations.get(player['id'], npc)
            reply = self.s.npcs[npc].speak(history, f'玩家「{player["name"]}」主动和你交谈，当前好感度{score:.0f}/100。'
                '只根据本人已知的信息回应；好感度低时保持边界，不透露自己的秘密，亲近时也应谨慎。')
            item['reply'] = reply or self.s.cards[npc].fallback_line()
            item['transcript'] = [line, dict(speaker=npc, text=item['reply'])]
            with step(),self.s.lock:
                self._save(item)
                self._emit('dialogue_line', event_key=turn_id+':npc', conversation_id=turn_id,
                    sequence=2, participants=[npc], speaker=npc, text=item['reply'], activity='player_chat')
                if self.engaged_npc == npc:self.last_dialogue_id = turn_id
            text = '\n'.join(t['speaker']+'：'+t['text'] for t in item['transcript'])
            for agent, other in ((npc, player['id']), (player['id'], npc)):
                memory_id = f'player-chat:{turn_id}:{agent}'
                other_name = speaker if agent == npc else npc
                persona = (self.s.cards[npc].render_system_prompt() if agent == npc
                           else f'你代表玩家「{player["name"]}」回顾这次游戏内的经历。')
                memory = remember_experience(self.s.memory,agent,persona,text,
                    perspective=f'你刚和{other_name}交谈',kind='dialogue',
                    importance=PLAYER.memory_importance,game_time=item['game_time'],location=item['area'],
                    relationships=f'{other_name} {score:.0f}/100',participants=[other],
                    tags=['player_chat',turn_id],memory_id=memory_id,
                    fallback=f'我与{other_name}的对话：\n{text}',commit=step)
                with step():
                    item['memories'][agent] = memory.id
                    self._save(item)
            impact = self.s.llm.chat_json('评估校园角色与玩家本轮实际交流的关系变化，不推测未发生的事。',
                f'当前好感度{score:.0f}/100。delta不超过±{PLAYER.relation_max_delta}，reason简述实际依据。\n'+text,
                PlayerRelationImpact)
            try:impact = PlayerRelationImpact.model_validate(impact.model_dump() if hasattr(impact, 'model_dump') else impact)
            except (ValueError, TypeError):impact = PlayerRelationImpact(delta=0, reason='完成一次交流；模型未给出有效评估，保持原好感度')
            with step(),self.s.lock:
                item['relation'] = self.s.relations.apply_delta_once(npc, player['id'], impact.delta, impact.reason, turn_id)
                self._emit('relation_changed', event_key=turn_id+':relation', a=npc, b=player['id'], **item['relation'])
                self._emit('memory_changed', event_key=turn_id+':memory', npc=npc)
                item['status'] = 'completed'
                self._save(item)
        except CastChanged:
            pass
        except Exception:
            with self.s.lock:
                if self.pending==turn_id:
                    item.update(status='failed', error='本轮处理未完成，已生成的台词和记忆仍保留。可查看记录后重新发起对话。')
                    self._save(item)
        finally:
            with self.s.lock:
                self.jobs={key:job for key,job in self.jobs.items() if key!=turn_id and not job.done()}
                if self.pending==turn_id:
                    if self.interaction and item['status']=='completed':
                        self.interaction['turns'].append(dict(id=turn_id,transcript=item['transcript'],relation=item['relation']))
                    released = self.engaged_npc != npc
                    if released:
                        self.s.busy.discard(npc)
                        self._finish_interaction()
                        self._emit('dialogue_finished',event_key=turn_id+':visual_end',
                            conversation_id=turn_id,participants=[npc])
                    self.pending = None;self.pending_npc = None
                    self._world_changed()

    def stop(self):
        with self.s.lock:
            self.closed = True
            if self.session:self.leave(self.session['id'])
        self.pool.shutdown(wait=True)

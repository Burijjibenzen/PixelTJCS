"""单机玩家身份与异步对话。模型处理不占世界锁，不把玩家注册为NPC。"""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from pydantic import BaseModel, Field
from backend.config import PLAYER


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
        self.closed = False
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='player-chat')
        with self.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS player_profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS player_sessions(id TEXT PRIMARY KEY,player TEXT NOT NULL,state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS player_turns(id TEXT PRIMARY KEY,session TEXT NOT NULL,
                    request_id TEXT NOT NULL,npc TEXT NOT NULL,message TEXT NOT NULL,payload TEXT NOT NULL,
                    UNIQUE(session,request_id));
            ''')
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

    def state(self):
        with self.s.lock:
            active = self.session
            return dict(enabled=bool(active), session_id=active['id'] if active else None,
                player=dict(active['player']) if active else None, pending_turn=self.pending, pending_npc=self.pending_npc,
                relations=[dict(npc=n, score=self.s.relations.get(active['player']['id'], n),
                    reason=self.s.relations.last_reason(active['player']['id'], n)) for n in self.s.npcs] if active else [])

    def join(self, name='玩家'):
        name = name.strip()
        if not name or len(name) > PLAYER.name_max_chars or any(ord(c) < 32 for c in name):raise PlayerError(400, '请填写1至24字的玩家称呼')
        if name in self.s.npcs or name.startswith('player:'):raise PlayerError(400, '玩家称呼不能与NPC重名或使用内部身份前缀')
        with self.s.lock:
            if self.closed:raise PlayerError(503, '服务正在关闭')
            if self.session:
                if self.session['player']['name'] != name:raise PlayerError(409, '已有玩家加入，请先离开再更换身份')
                return self.state()
            if self.pending:raise PlayerError(409, '上一轮对话仍在处理，结束后可重新加入')
            with self.database() as db:
                row = db.execute('SELECT id FROM player_profiles WHERE name=?', (name,)).fetchone()
                player = dict(id=row[0] if row else 'player:'+uuid.uuid4().hex, name=name)
                if not row:db.execute('INSERT INTO player_profiles VALUES(?,?)', (player['id'], name))
                session_id = uuid.uuid4().hex
                db.execute("INSERT INTO player_sessions VALUES(?,?,'active')", (session_id, player['id']))
            for npc in self.s.npcs:self.s.relations.set_initial(player['id'], npc, 50)
            self.session = dict(id=session_id, player=player)
            return self.state()

    def leave(self, session_id):
        with self.s.lock:
            self._profile(session_id)
            if self.session and self.session['id'] != session_id:raise PlayerError(409, '旧会话不能关闭当前玩家模式')
            with self.database() as db:db.execute("UPDATE player_sessions SET state='left' WHERE id=?", (session_id,))
            self.session = None
            # 已接受的一轮继续完成保存，不能在模型仍处理时提前释放NPC。
            return self.state()

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
            if self.pending:raise PlayerError(409, '正在等待上一轮回复，请稍后再发送')
            available = getattr(self.s, 'available', lambda a,b: a not in self.s.busy)
            if not available(npc, npc):raise PlayerError(409, '对方正在交谈、阅读或整理思绪，请稍后再试')
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
            try:self.jobs[turn_id] = self.pool.submit(self._work, dict(item, memories={}, transcript=[]))
            except RuntimeError:
                self.pending = None;self.pending_npc = None;self.s.busy.discard(npc)
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

    def _work(self, item):
        turn_id = item['id']
        npc = item['npc'];player = item['player'];speaker = '玩家·'+player['name']
        try:
            history = []
            for previous in self.history(item['session_id'], npc, PLAYER.context_turns+1):
                if previous['status'] == 'completed':history.extend(previous['transcript'])
            line = dict(speaker=speaker, text=item['message'])
            history.append(line)
            self._emit('dialogue_line', event_key=turn_id+':player', conversation_id=turn_id,
                sequence=1, participants=[npc], speaker=speaker, text=item['message'], activity='player_chat')
            score = self.s.relations.get(player['id'], npc)
            reply = self.s.npcs[npc].speak(history, f'玩家「{player["name"]}」主动和你交谈，当前好感度{score:.0f}/100。'
                '只根据本人已知的信息回应；好感度低时保持边界，不透露自己的秘密，亲近时也应谨慎。')
            item['reply'] = reply or self.s.cards[npc].fallback_line()
            item['transcript'] = [line, dict(speaker=npc, text=item['reply'])]
            self._save(item)  # 台词先持久化，后续写记忆失败仍可查询已经发生的交流。
            self._emit('dialogue_line', event_key=turn_id+':npc', conversation_id=turn_id,
                sequence=2, participants=[npc], speaker=npc, text=item['reply'], activity='player_chat')
            text = '\n'.join(t['speaker']+'：'+t['text'] for t in item['transcript'])
            for agent, other in ((npc, player['id']), (player['id'], npc)):
                memory_id = f'player-chat:{turn_id}:{agent}'
                memory = self.s.memory.get_by_id(memory_id) or self.s.memory.add(agent=agent,
                    content=('我与'+(speaker if agent == npc else npc)+'的对话：\n'+text), type='dialogue',
                    importance=PLAYER.memory_importance, game_time=item['game_time'], location=item['area'],
                    participants=[other], tags=['player_chat', turn_id], memory_id=memory_id)
                item['memories'][agent] = memory.id
                self._save(item)
            impact = self.s.llm.chat_json('评估校园角色与玩家本轮实际交流的关系变化，不推测未发生的事。',
                f'当前好感度{score:.0f}/100。delta不超过±{PLAYER.relation_max_delta}，reason简述实际依据。\n'+text,
                PlayerRelationImpact)
            try:impact = PlayerRelationImpact.model_validate(impact.model_dump() if hasattr(impact, 'model_dump') else impact)
            except (ValueError, TypeError):impact = PlayerRelationImpact(delta=0, reason='完成一次交流；模型未给出有效评估，保持原好感度')
            with self.s.lock:
                item['relation'] = self.s.relations.apply_delta_once(npc, player['id'], impact.delta, impact.reason, turn_id)
                self._save(item)
            self._emit('relation_changed', event_key=turn_id+':relation', a=npc, b=player['id'], **item['relation'])
            self._emit('memory_changed', event_key=turn_id+':memory', npc=npc)
            item['status'] = 'completed'
            self._save(item)
        except Exception:
            item.update(status='failed', error='本轮处理未完成，已生成的台词和记忆仍保留。可查看记录后重新发起对话。')
            self._save(item)
        finally:
            with self.s.lock:
                self.s.busy.discard(npc)
                if self.pending == turn_id:self.pending = None;self.pending_npc = None
                self.jobs = {key:job for key,job in self.jobs.items() if not job.done()}
                self._world_changed()

    def stop(self):
        with self.s.lock:
            self.closed = True
            if self.session:self.leave(self.session['id'])
        self.pool.shutdown(wait=True)

"""M3: 用 LangGraph 节点替换原手写轮次循环；HTTP 与 CLI 共用同一引擎。"""
import json
import sqlite3
import uuid
from contextlib import closing
from typing import TypedDict, List
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from backend.agent_runtime import AgentRuntime
from backend.config import WORLD
from backend.world import AREAS
from backend.logger import get_logger

log = get_logger(__name__)

class RelationImpact(BaseModel):
    delta: float = Field(description='好感度变化，-6 到 +6')
    reason: str = Field(description='变化原因，15字以内')

class ConversationState(TypedDict, total=False):
    id: str
    a: str
    b: str
    trigger: str
    area: str
    time: float
    round: int
    max_rounds: int
    transcript: list
    memories: dict
    impact: dict
    relation: dict
    gossip: dict
    result: dict

class ConversationEngine:
    def __init__(self, world, llm, memory, npcs, relations):
        self.world, self.llm, self.memory = world, llm, memory
        self.npcs, self.relations = npcs, relations
        self.require_colocated = False  # CLI 的原演示位置调度保留；HTTP 装配为 True。
        self.emit = lambda kind, **data: None
        self.gossip_writer = lambda state: {}
        self.agents = {n: AgentRuntime(npc, relations) for n, npc in npcs.items()}
        self.connection = sqlite3.connect(memory.cfg.db_path, check_same_thread=False, timeout=10)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.checkpointer = SqliteSaver(self.connection)
        self.checkpointer.setup()
        with self.connection:
            self.connection.execute('CREATE TABLE IF NOT EXISTS m3_effects (id TEXT PRIMARY KEY, payload TEXT)')
            self.connection.execute('CREATE TABLE IF NOT EXISTS m3_runs (id TEXT PRIMARY KEY, a TEXT, b TEXT, status TEXT, error TEXT)')
        graph = StateGraph(ConversationState)
        for name in ('validate', 'observe', 'opener', 'reply_b', 'reply_a', 'closing', 'memory_a', 'memory_b',
                     'assess', 'relation_update', 'gossip_transfer', 'reflect_a', 'reflect_b', 'complete'):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'validate')
        for a,b in [('validate','observe'),('observe','opener'),('opener','reply_b'),('reply_b','reply_a'),
                    ('closing','memory_a'),('memory_a','memory_b'),('memory_b','assess'),
                    ('assess','relation_update'),('relation_update','gossip_transfer'),
                    ('gossip_transfer','reflect_a'),('reflect_a','reflect_b'),('reflect_b','complete')]:
            graph.add_edge(a,b)
        graph.add_conditional_edges('reply_a', lambda s: 'reply_b' if s['round'] < s['max_rounds'] else 'closing')
        graph.add_edge('complete', END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    def close(self):
        self.connection.close()

    def set_status(self, run_id, a, b, status, error=''):
        with closing(sqlite3.connect(self.memory.cfg.db_path, timeout=10)) as db, db:
            db.execute('INSERT OR REPLACE INTO m3_runs VALUES(?,?,?,?,?)', (run_id,a,b,status,error))

    def run(self, a_name, b_name, trigger, run_id=None, gossip=None):
        if a_name == b_name or a_name not in self.npcs or b_name not in self.npcs:
            return None
        if not self.world.can_chat(a_name,b_name)[0]:
            return None
        if self.require_colocated and AREAS[self.world.area_of(a_name)]['id'] != AREAS[self.world.area_of(b_name)]['id']:
            return None
        run_id = run_id or uuid.uuid4().hex
        state = dict(id=run_id,a=a_name,b=b_name,trigger=trigger,round=0,max_rounds=WORLD.max_rounds,
                     transcript=[],memories={},gossip=gossip or {})
        return self._invoke(state, run_id, a_name, b_name)

    def resume(self, run_id):
        config = {'configurable': {'thread_id':run_id}}
        snapshot = self.graph.get_state(config)
        if not snapshot.values:
            raise ValueError('未知对话检查点')
        state = snapshot.values
        if not snapshot.next:
            return state.get('result')
        if self.require_colocated and any(AREAS[self.world.area_of(n)]['id'] != state['area'] for n in (state['a'],state['b'])):
            raise ValueError('恢复前请让双方回到原会面区域')
        return self._invoke(None,run_id,state['a'],state['b'])

    def _invoke(self, state, run_id, a, b):
        self.set_status(run_id,a,b,'running')
        try:
            out = self.graph.invoke(state, {'configurable': {'thread_id':run_id}, 'recursion_limit':80})
            self.set_status(run_id,a,b,'completed')
            return out['result']
        except Exception as exc:
            self.set_status(run_id,a,b,'failed',type(exc).__name__)
            raise

    def validate(self, state):
        a,b = state['a'],state['b']
        if not self.world.can_chat(a,b)[0]:
            raise ValueError('冷却中')
        area = AREAS[self.world.area_of(a)]['id']
        if AREAS[self.world.area_of(b)]['id'] != area:
            if self.require_colocated:
                raise ValueError('必须实际抵达同一区域')
            self.world.set_area(b, area)
        self.world.npc_area[a] = self.world.npc_area[b] = area
        log.info('LangGraph 对话开始: %s × %s',a,b)
        self.emit('conversation_started', conversation_id=state['id'], participants=[a,b], area=area)
        return {'area':area, 'time': self.world.clock.now}

    def observe(self,state):
        # M3: 真实相遇产生本人观察记忆；稳定 ID 防止检查点恢复重复写入。
        for name,other in ((state['a'],state['b']),(state['b'],state['a'])):
            mid=state['id']+':observation:'+name
            if self.memory.get_by_id(mid) is None:
                self.memory.add(agent=name,content=f"我在{AREAS[state['area']]['name']}遇到了{other}",
                    type='observation',importance=0.15,game_time=state['time'],location=state['area'],
                    participants=[other],memory_id=mid)
            self.emit('memory_changed',event_key=mid,npc=name)
        return {}

    def actor(self,state,which,task):
        name,other = state[which], state['b' if which == 'a' else 'a']
        return self.agents[name].graph.invoke(dict(task=task,other=other,trigger=state['trigger'],
            history=state['transcript'],time=state['time'],area=state['area'],
            memory_id=state['id']+':memory:'+name))

    def line(self,state,which,task):
        text = self.actor(state,which,task)['text']
        if task == 'opener' and state['gossip']:
            text = '听说' + state['gossip']['content'] + '。' + text
        transcript = state['transcript'] + [{'speaker':state[which],'text':text}]
        sequence = len(transcript)
        self.emit('dialogue_line', event_key=f"{state['id']}:line:{sequence}", conversation_id=state['id'],
            sequence=sequence,participants=[state['a'],state['b']],speaker=state[which],text=text,area=state['area'])
        log.info('%s: %s',state[which],text)
        return {'transcript':transcript}

    def opener(self,s): return self.line(s,'a','opener')
    def reply_b(self,s): return self.line(s,'b','reply')
    def reply_a(self,s): return dict(self.line(s,'a','reply'),round=s['round']+1)
    def closing(self,s): return self.line(s,'b','closing')

    def save_memory(self,s,which):
        item = self.actor(s,which,'memory')['memory']
        self.emit('memory_changed',event_key=s['id']+':memory:'+which,npc=s[which])
        return {'memories':dict(s['memories'], **{s[which]:item})}
    def memory_a(self,s): return self.save_memory(s,'a')
    def memory_b(self,s): return self.save_memory(s,'b')
    def assess(self,s): return {'impact':self._assess_relation(s['transcript'],s['a'],s['b']).model_dump()}

    def relation_update(self,s):
        # M3: 分数与执行标记在同一 SQLite 事务内，断点恢复不能重复加分。
        key=s['id']+':relation'
        a,b=sorted((s['a'],s['b']))
        with closing(sqlite3.connect(self.memory.cfg.db_path,timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM m3_effects WHERE id=?',(key,)).fetchone()
            if row:
                relation=json.loads(row[0])
            else:
                old=db.execute('SELECT score FROM relations WHERE a=? AND b=?',(a,b)).fetchone()
                score=max(0,min(100,(old[0] if old else 50)+s['impact']['delta']))
                relation=dict(s['impact'],score=score)
                db.execute('INSERT OR REPLACE INTO relations(a,b,score,last_reason) VALUES(?,?,?,?)',(a,b,score,relation['reason']))
                db.execute('INSERT INTO m3_effects VALUES(?,?)',(key,json.dumps(relation,ensure_ascii=False)))
        self.relations._cache[(a,b)]=relation['score']
        self.relations._reason[(a,b)]=relation['reason']
        self.emit('relation_changed',event_key=key,a=a,b=b,**relation)
        return {'relation':relation}

    def gossip_transfer(self,s):
        return {'gossip':self.gossip_writer(s)}
    def reflect_a(self,s):
        if self.actor(s,'a','reflect')['reflected']:
            self.emit('memory_changed',npc=s['a'])
        return {}
    def reflect_b(self,s):
        if self.actor(s,'b','reflect')['reflected']:
            self.emit('memory_changed',npc=s['b'])
        return {}

    def complete(self,s):
        self.world.mark_chatted(s['a'],s['b'])
        result=dict(conversation_id=s['id'], area=s['area'],area_name=AREAS[s['area']]['name'],
            game_time=self.world.clock.fmt(),trigger=s['trigger'],transcript=s['transcript'],
            memories=s['memories'],relation=s['relation'],gossip=s['gossip'])
        self.emit('conversation',event_key=s['id']+':complete',conversation_id=s['id'],
                  participants=[s['a'],s['b']],result=result)
        return {'result':result}

    def _assess_relation(self, transcript: List[dict], a: str, b: str) -> RelationImpact:
        """LLM 评估对话对关系的影响；离线/失败时小幅正向兜底。"""
        old = self.relations.get(a, b)
        transcript_text = "\n".join(f"{t['speaker']}：{t['text']}" for t in transcript)
        impact = self.llm.chat_json(
            f"{a} 和 {b} 是同一课题组的师生/同学，当前好感度 {old:.0f}（满分100）。",
            f"根据下面的对话，评估这次交流对两人关系的影响:\n{transcript_text}",
            RelationImpact,
        )
        if impact is None:
            impact = RelationImpact(delta=1.0, reason="进行了日常交流")
        # 裁剪幅度，防止模型给离谱数值
        impact.delta = max(-6.0, min(6.0, impact.delta))
        return impact


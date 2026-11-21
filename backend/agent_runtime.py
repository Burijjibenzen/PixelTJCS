"""M3: 每个 NPC 一个 StateGraph 子图；NPC 保留原子能力，LLM 唯一出口仍是 llm.py。"""
import json
from contextlib import nullcontext
from typing import Annotated, Literal, TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END


class SocialDecision(BaseModel):
    action: str = Field(description='只能选 chat / move / continue_schedule')
    intent: str = Field(default='聊聊近况', max_length=200)
    share_gossip: bool = False
    reason: str = Field(default='',max_length=200)


class TalkTo(BaseModel):
    type: Literal['talk_to']
    npc: str
    topic: str = Field(min_length=1,max_length=200)


class PerformAt(BaseModel):
    type: Literal['perform_at']
    kind: str
    area: str
    purpose: str = Field(min_length=1,max_length=200)
    minutes: float = Field(ge=15,le=360)


class ExtendEvent(BaseModel):
    type: Literal['extend']
    minutes: float = Field(ge=15,le=180)


class EventAssessment(BaseModel):
    outcome: str = Field(min_length=1,max_length=500)
    reason: str = Field(min_length=1,max_length=200)
    action: Annotated[TalkTo | PerformAt | ExtendEvent, Field(discriminator='type')] | None
    bubble: str = Field(default='',max_length=120)


class WhisperAction(BaseModel):
    type: Literal['go_to','talk_to','perform_at','stay']
    area: str = ''
    npc: str = ''
    activity: str = ''
    purpose: str = Field(default='',max_length=200)
    minutes: int = Field(default=30,ge=1,le=360)


class ConversationConsent(BaseModel):
    decision: Literal['accept','brief','defer','decline']
    reason: str = Field(min_length=1,max_length=200,pattern=r'\S')
    reply: str = Field(min_length=1,max_length=100,pattern=r'\S')


class AgentState(TypedDict, total=False):
    task: str
    other: str
    trigger: str
    history: list
    time: float
    area: str
    context: str
    candidate: str
    co_located: bool
    forced: bool
    decision: dict
    text: str
    memory: dict
    memory_id: str
    reflected: bool


class AgentRuntime:
    def plan_day(self, context):
        """每日一次的可选计划请求；知识只来自该角色自己的上下文。"""
        from backend.activity_catalog import DailyPlan
        from backend.config import ACTIVITY
        return self.npc.llm.chat_json(self.npc.card.render_system_prompt(),
            '结合身份、目标、性格与以下本人已知资料，安排今天剩余时间的1至3项活动。'
            '基础作息继续保留；不要安排全天漫游。只选places中给出的kind/area组合。'
            'start_minute是出发时间，必须不早于minute；预留行走时间，duration是到达后的游戏分钟。'
            'visit必须选择friends中的partner，其他活动partner为空。basis写依据类型，reason简述为何选这项活动或此人。'
            '未知事件、他人的秘密、他人的记忆一律不能当作依据。\n'+json.dumps(context,ensure_ascii=False),
            DailyPlan,timeout=ACTIVITY.model_timeout_seconds)

    def __init__(self, npc, relations):
        self.npc, self.relations = npc, relations
        self.map_perception = None
        self.progress = nullcontext
        graph = StateGraph(AgentState)
        for name in ('perceive', 'decide', 'act', 'dialogue', 'write_memory', 'reflect'):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'perceive')
        graph.add_edge('perceive', 'decide')
        graph.add_edge('decide', 'act')
        graph.add_conditional_edges('act', lambda s: {
            'opener': 'dialogue', 'reply': 'dialogue', 'closing': 'dialogue',
            'memory': 'write_memory', 'reflect': 'reflect'}.get(s['task'], END))
        for name in ('dialogue', 'write_memory', 'reflect'):
            graph.add_edge(name, END)
        self.graph = graph.compile()

    def perceive(self, state):
        other = state.get('other', '')
        score = self.relations.get(self.npc.name, other) if other else 50
        context = f'{self.npc.situation_text()}；对方：{other}；好感度：{score}/100；目标：{self.npc.card.goals_short} / {self.npc.card.goals_long}；日程目标：{self.npc.world.target_area(self.npc.name)}'
        if self.map_perception is not None:
            context += '\n现场感知：' + json.dumps(self.map_perception(self.npc.name),ensure_ascii=False)
        return {'context': context}

    def react_to_event(self, result):
        """把已确认的事件结果转为行动意向，具体日程由调度器校验和执行。"""
        activity = result.get('activity')
        if activity is None:
            return 'continue'
        return 'cancel' if activity['cancel'] else 'schedule'

    def assess_event(self, context):
        from backend.config import ACTIVITY
        return self.npc.llm.chat_json(self.npc.card.render_system_prompt(),
            '结合本人目标、已知信息和刚经历的事件，给出本角色得到的具体结果（outcome）和原因。'
            '根据提供的好感、活跃记忆和洞察判断是否行动、找谁以及接下来做什么。'
            '如果是为了请教某个问题，在 reason 中写明为什么选此人。只引用实际提供的依据。'
            '区分亲眼发生的事实、角色的理解和拟采取的行动。可以发现疑问、形成判断或确认暂时没有进展。'
            '如果对方已婉拒或提议稍后聊，尊重对方刚表达的边界，不要立刻重复同一个邀约。'
            '不得虚构已完成的验证、设备读数、他人同意或秘密。'
            '如有必要，只选择 talk_to（找人交谈）、perform_at（前往地点执行目录中的活动）、'
            'extend（延长刚完成的活动，仅在 can_extend 为 true 时允许）。否则 action 为 null。'
            '有后续动作时，在 bubble 中另写一句角色会说的简短自言自语，最多 28 个字，只说接下来要做什么。'
            '不要照抄结果、原因、原始目的，不加自言自语前缀，不透露私人信息。'
            '人名和地点须使用选项中的原值。\n'+json.dumps(context,ensure_ascii=False),
            EventAssessment,timeout=ACTIVITY.model_timeout_seconds)

    def consent_to_event(self, context):
        from backend.config import ACTIVITY
        return self.npc.llm.chat_json(self.npc.card.render_system_prompt(),
            '有人当面邀请你交谈。decision 只能选 gate.allowed 里的值：'
            'accept 现在聊，brief 只聊一个问题，defer 稍后聊，decline 婉拒。'
            '当前有日程本身不构成拒绝理由。可打断时，结合话题价值、熟悉程度和剩余时间判断，'
            '有兴趣但时间不多时优先考虑简短聊；确实有时间冲突时可建议稍后再聊。'
            'reason 写具体依据。reply 是对来访者当面说的一句台词，说明能否聊及可公开的具体原因。'
            '不要透露私密日程、关系评分或隐藏目标，也不要编造承诺或约定时间。'
            '\n'+json.dumps(context,ensure_ascii=False),ConversationConsent,timeout=ACTIVITY.model_timeout_seconds)

    def decide(self, state):
        if state['task'] != 'choose':
            return {}
        allowed = 'chat, continue_schedule' if state.get('co_located') else 'move, continue_schedule'
        decision = self.npc.llm.chat_json(self.npc.card.render_system_prompt(),
            state['context'] + '\n' + self.npc.recall_text(state.get('trigger',''), self.npc.world.clock.now) + '\n根据本人日程、目标、关系决定是否搭话或赴约。只允许 ' + allowed +
            '\n事件：' + state.get('trigger', '') + '\n你自己知道的可转述信息：' + state.get('candidate', '') +
            '\n决定 action、intent、share_gossip 和 reason。reason 简述选择依据，比如对方熟悉的话题或共同经历。'
            '不要编造他人的私密信息。', SocialDecision)
        if decision is None:
            decision = SocialDecision(action='chat' if state.get('co_located') else 'move',
                                      share_gossip=bool(state.get('candidate')))
        data = decision.model_dump()
        if state.get('forced'):
            data['action'] = 'chat'
        if data['action'] not in allowed.split(', '):
            data['action'] = 'continue_schedule'
        return {'decision': data}

    def act(self, state):
        return {}  # 显式条件边选择原子能力；移动由 Scheduler 下发目标，不传送。

    def dialogue(self, state):
        trigger = state.get('trigger', '') + '\n' + state['context'] + '\n好感度低时保持边界，不透露自己的秘密；关系亲近也应谨慎。'
        if state['task'] == 'opener':
            text = self.npc.opener(trigger)
        else:
            if state['task'] == 'closing':
                trigger += '\n请简短道别，结束本次对话，不再提出新问题。'
            text = self.npc.speak(state.get('history', []), trigger)
        return {'text': text}

    def write_memory(self, state):
        item = self.npc.save_dialogue_memory(state['history'], state['other'],
            state['time'], state['area'], memory_id=state.get('memory_id'),commit=self.progress)
        return {'memory': item.to_dict() if item else None}

    def reflect(self, state):
        return {'reflected': self.npc.maybe_reflect(commit=self.progress)}

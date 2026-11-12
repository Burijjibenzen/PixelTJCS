"""M3: 每个 NPC 一个 StateGraph 子图；NPC 保留原子能力，LLM 唯一出口仍是 llm.py。"""
from typing import TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END


class SocialDecision(BaseModel):
    action: str = Field(description='只能选 chat / move / continue_schedule')
    intent: str = Field(default='聊聊近况', max_length=200)
    share_gossip: bool = False


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
    def __init__(self, npc, relations):
        self.npc, self.relations = npc, relations
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
        return {'context': f'{self.npc.situation_text()}；对方：{other}；好感度：{score}/100；目标：{self.npc.card.goals_short} / {self.npc.card.goals_long}；日程目标：{self.npc.world.target_area(self.npc.name)}'}

    def decide(self, state):
        if state['task'] != 'choose':
            return {}
        allowed = 'chat, continue_schedule' if state.get('co_located') else 'move, continue_schedule'
        decision = self.npc.llm.chat_json(self.npc.card.render_system_prompt(),
            state['context'] + '\n' + self.npc.recall_text(state.get('trigger',''), self.npc.world.clock.now) + '\n根据本人日程、目标、关系决定是否搭话或赴约。只允许 ' + allowed +
            '\n事件：' + state.get('trigger', '') + '\n你自己知道的可转述信息：' + state.get('candidate', '') +
            '\n决定 action、intent、share_gossip；不要编造他人的私密信息。', SocialDecision)
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
            state['time'], state['area'], memory_id=state.get('memory_id'))
        return {'memory': item.to_dict() if item else None}

    def reflect(self, state):
        return {'reflected': self.npc.maybe_reflect()}

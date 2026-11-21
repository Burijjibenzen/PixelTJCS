"""NPC Agent —— 人格 + 记忆 + 关系的载体

一个 NPCAgent 实例 = 一张人格卡 + 一个共享的 MemoryStore 句柄 + 世界状态引用。
它负责回答两个问题:
    "我现在该怎么说话?"    → speak() / opener()
    "我该怎么记住这场对话?" → save_dialogue_memory() / observe() / maybe_reflect()

注意: 对话流程的编排不在本模块（见 conversation.py），
本模块只提供"单个 NPC 的原子能力"，方便单元测试与未来迁入 LangGraph 节点。
"""

import uuid
from contextlib import nullcontext
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.config import LLM as LLM_CFG
from backend.config import ACTIVITY, MEMORY
from backend.llm import LLMClient
from backend.logger import get_logger
from backend.memory import MemoryStore
from backend.persona import PersonaCard
from backend.world import AREAS, World

log = get_logger(__name__)


# ----------------------------------------------------------------------
# LLM 结构化输出的 schema
# ----------------------------------------------------------------------
class MemoryAssessment(BaseModel):
    """对话结束后，让 LLM 以某角色视角评定这段经历的记忆。"""

    content: str = Field(description="以第一人称写下的记忆内容，20~60字")
    importance: float = Field(description="重要性 0.0~1.0，越重要越高")
    emotion: str = Field(description="我的主导情绪: positive/neutral/negative")


class InsightSchema(BaseModel):
    """反思压缩的输出: 把一批琐碎记忆提炼成一条洞察。"""

    insight: str = Field(description="一条第一人称的洞察，25~60字")
    importance: float = Field(description="洞察的重要性 0.0~1.0（通常较高）")


# ----------------------------------------------------------------------
def memory_context(memory, agent, relationships):
    active = [m.content for m in memory.get_active(agent, 8) if m.type != 'reflection'][:6]
    insights = [m.content for m in memory.get_reflections(agent, 3)]
    return (f"【对相关人物的好感】{relationships or '无特定人物'}\n"
            f"【活跃记忆】{active}\n【反思洞察】{insights}")


def remember_experience(memory, agent, persona, evidence, *, perspective, kind, game_time,
                        location, importance, relationships='', participants=None, tags=None,
                        memory_id=None, fallback=None, commit=nullcontext):
    """生成角色自己的回忆，原始经历由调用方记录。"""
    if memory_id:
        existing = memory.get_by_id(memory_id)
        if existing:
            return existing
    assessment = memory.llm.chat_json(
        persona,
        f"{memory_context(memory, agent, relationships)}\n"
        f"【经历方式】{perspective}\n【当时接触到的内容】\n{evidence}\n"
        "按自己的性格、好感、记忆和洞察，用第一人称写下你会记得的内容，并给出重要性和情绪。"
        "可以选择性记忆、误听、改写细节，或补上你当时以为的解释。"
        "把推测自然写成自己的印象，别把旁听写成亲历。",
        MemoryAssessment, timeout=ACTIVITY.model_timeout_seconds,
    )
    return memory.add(
        agent=agent, content=assessment.content if assessment else (evidence if fallback is None else fallback),
        type=kind, importance=assessment.importance if assessment else importance,
        emotion=assessment.emotion if assessment else 'neutral', game_time=game_time, location=location,
        participants=participants, tags=tags, memory_id=memory_id, commit=commit,
    )


class NPCAgent:
    """一个可说话、可记忆、可反思的校园角色。"""

    def __init__(
        self, card: PersonaCard, world: World, llm: LLMClient, memory: MemoryStore, relations
    ):
        self.card = card
        self.world = world
        self.llm = llm
        self.memory = memory
        self.relations = relations
        self.name = card.name

    # ---------------- 上下文构建 ----------------
    def situation_text(self) -> str:
        """当前处境（时间/位置），拼进说话提示词。"""
        area_id = self.world.area_of(self.name) or "j3_lab"
        return f"现在是{self.world.clock.fmt()}，你在{AREAS[area_id]['name']}"

    def recall_text(self, query: str, now: float) -> str:
        """把记忆检索结果渲染成提示词片段。"""
        items = self.memory.recall(self.name, query, now)
        if not items:
            return "【你想起的记忆】（暂时没想起什么）"
        return "【你想起的相关记忆】\n" + "\n".join(f"- {m.content}" for m in items)

    def build_prompt(self, task_hint: str, memory_query: str, extra: str = "") -> str:
        """组装一次发言的完整 user 提示词。"""
        parts = [
            self.situation_text(),
            self.card.render_secret_hint(),
            self.recall_text(memory_query, self.world.clock.now),
        ]
        if extra:
            parts.append(extra)
        parts.append(f"【当前要求】{task_hint}")
        parts.append("直接输出你要说的话，不要任何前缀、引号或解释。")
        return "\n\n".join(parts)

    # ---------------- 说话 ----------------
    def opener(self, trigger: str) -> str:
        """开启一场对话的第一句话。"""
        extra = f"【对话缘由】{trigger}"
        text = self.llm.chat(
            self.card.render_system_prompt(),
            self.build_prompt("主动开启对话，说出第一句话", trigger, extra),
            model=LLM_CFG.chat_model,
        )
        return text or self.card.fallback_line()

    def speak(self, history: List[dict], trigger: str) -> str:
        """根据对话历史说下一句话。history: [{"speaker","text"},...]"""
        transcript = "\n".join(f"{t['speaker']}：{t['text']}" for t in history)
        last = history[-1]["text"] if history else ""
        extra = f"【对话缘由】{trigger}\n【目前的对话】\n{transcript}"
        text = self.llm.chat(
            self.card.render_system_prompt(),
            self.build_prompt(
                "接话：自然地回应对方最后一句话，可反问/可共情/可抬杠，贴合你的性格",
                f"{trigger} {last}",
                extra,
            ),
            model=LLM_CFG.chat_model,
        )
        return text or self.card.fallback_line()

    # ---------------- 记忆写入 ----------------
    def remember(self, evidence, *, participants=None, **experience):
        relationships = ', '.join(f'{name} {self.relations.get(self.name, name):.0f}/100'
                                  for name in participants or ())
        return remember_experience(
            self.memory, self.name, self.card.render_system_prompt(), evidence,
            relationships=relationships, participants=participants, **experience,
        )

    def save_dialogue_memory(
        self, transcript: List[dict], other: str, game_time: float, location: str, memory_id=None, commit=nullcontext
    ):
        """对话结束：以我的视角评定并保存这条对话记忆（LLM 失败则降级兜底）。"""
        transcript_text = "\n".join(f"{t['speaker']}：{t['text']}" for t in transcript)
        tail = transcript[-1]["text"][:20] if transcript else ""
        return self.remember(
            transcript_text, perspective=f'你刚和{other}交谈', kind='dialogue',
            game_time=game_time, location=location, participants=[other], memory_id=memory_id,
            importance=0.4, fallback=f'我和{other}聊了几句（{tail}...）', commit=commit,
        )

    # ---------------- 日常观察（低重要性记忆，遗忘演示素材） ----------------
    def observe(self, content: str, importance: float = 0.25, *, commit=nullcontext):
        return self.remember(
            content, perspective='你亲自观察到一件事', kind='observation', importance=importance,
            game_time=self.world.clock.now,
            location=self.world.area_of(self.name) or "j3_lab",
            commit=commit,
        )

    # ---------------- 反思（记忆压缩） ----------------
    def maybe_reflect(self,commit=nullcontext) -> bool:
        """未反思的原始记忆攒够 reflect_every 条 → LLM 压缩成一条洞察。

        原始记忆 importance 减半（淡出），洞察以较高重要性入库。
        返回是否发生了反思。
        """
        pending = self.memory.count_unreflected(self.name)
        if pending < MEMORY.reflect_every:
            return False
        batch = self.memory.get_unreflected(self.name, limit=MEMORY.reflect_every)
        # M3: 反思写入后进程中断时，用来源记忆集合定位已有洞察，恢复只补标记。
        reflection_id = uuid.uuid5(uuid.NAMESPACE_URL,
            self.name + ':' + ','.join(sorted(m.id for m in batch))).hex
        if self.memory.get_by_id(reflection_id):
            with commit():
                self.memory.mark_reflected(self.name, [m.id for m in batch])
            return True
        batch_text = "\n".join(f"- {m.content}" for m in batch)
        people = sorted({person for memory in batch for person in memory.participants})
        relationships = ', '.join(f'{name} {self.relations.get(self.name, name):.0f}/100' for name in people)
        insight = self.llm.chat_json(
            self.card.render_system_prompt(),
            f"{memory_context(self.memory, self.name, relationships)}\n"
            "请根据自己的性格和看法，把以下回忆提炼成一条第一人称洞察，"
            f"可以写主观联想或推测，并评定重要性。\n{batch_text}",
            InsightSchema,
        )
        if insight is None:
            log.warning("[%s] 反思 LLM 失败，本轮跳过", self.name)
            return False
        self.memory.add(
            agent=self.name,
            content=f"（洞察）{insight.insight}",
            type="reflection",
            memory_id=reflection_id,
            importance=insight.importance,
            emotion="neutral",
            game_time=self.world.clock.now,
            location=self.world.area_of(self.name) or "",
            tags=["reflection"],
            commit=commit,
        )
        with commit():
            self.memory.mark_reflected(self.name, [m.id for m in batch])
        log.info("🧠 [%s] 反思完成: %s", self.name, insight.insight[:40])
        return True



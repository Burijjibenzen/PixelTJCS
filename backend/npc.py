"""NPC Agent —— 人格 + 记忆 + 关系的载体

一个 NPCAgent 实例 = 一张人格卡 + 一个共享的 MemoryStore 句柄 + 世界状态引用。
它负责回答两个问题:
    "我现在该怎么说话?"    → speak() / opener()
    "我该怎么记住这场对话?" → save_dialogue_memory() / observe() / maybe_reflect()

注意: 对话流程的编排不在本模块（见 conversation.py），
本模块只提供"单个 NPC 的原子能力"，方便单元测试与未来迁入 LangGraph 节点。
"""

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.config import LLM as LLM_CFG
from backend.config import MEMORY
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
class NPCAgent:
    """一个可说话、可记忆、可反思的校园角色。"""

    def __init__(
        self, card: PersonaCard, world: World, llm: LLMClient, memory: MemoryStore
    ):
        self.card = card
        self.world = world
        self.llm = llm
        self.memory = memory
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
    def save_dialogue_memory(
        self, transcript: List[dict], other: str, game_time: float, location: str, memory_id=None
    ):
        """对话结束：以我的视角评定并保存这条对话记忆（LLM 失败则降级兜底）。"""
        # M3: 恢复图节点时复用已经提交的记忆，避免重复调用模型和写库。
        if memory_id:
            existing = self.memory.get_by_id(memory_id)
            if existing:
                return existing
        transcript_text = "\n".join(f"{t['speaker']}：{t['text']}" for t in transcript)
        assess = self.llm.chat_json(
            f"你是校园角色「{self.name}」。刚刚你和「{other}」谈了一次话。",
            f"请以「{self.name}」的第一人称视角总结这段对话给你留下的记忆，"
            f"并评定重要性与你的情绪:\n{transcript_text}",
            MemoryAssessment,
        )
        if assess is None:
            # 离线兜底: 保存模板记忆，保证记忆链路不中断
            tail = transcript[-1]["text"][:20] if transcript else ""
            assess = MemoryAssessment(
                content=f"我和{other}聊了几句（{tail}...）",
                importance=0.4, emotion="neutral",
            )
        return self.memory.add(
            agent=self.name,
            content=assess.content,
            type="dialogue",
            memory_id=memory_id,
            importance=assess.importance,
            emotion=assess.emotion,
            game_time=game_time,
            location=location,
            participants=[other],
        )

    # ---------------- 日常观察（低重要性记忆，遗忘演示素材） ----------------
    def observe(self, content: str, importance: float = 0.25):
        """记下一件身边小事，直接入库（不走 LLM 评定）。

        琐事的重要性天然偏低，是"时间衰减遗忘"机制的主力演示素材:
        三五天后别人还记得要紧事，这些琐事已经进了遗忘档案。
        """
        return self.memory.add(
            agent=self.name,
            content=content,
            type="observation",
            importance=importance,
            emotion="neutral",
            game_time=self.world.clock.now,
            location=self.world.area_of(self.name) or "j3_lab",
        )

    # ---------------- 反思（记忆压缩） ----------------
    def maybe_reflect(self) -> bool:
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
            self.memory.mark_reflected(self.name, [m.id for m in batch])
            return True
        batch_text = "\n".join(f"- {m.content}" for m in batch)
        insight = self.llm.chat_json(
            f"你是校园角色「{self.name}」。以下是你最近的一段零散记忆。",
            f"请把这些零散记忆提炼成一条更抽象的第一人称洞察"
            f"（规律/感受/结论），并评定其重要性:\n{batch_text}",
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
        )
        self.memory.mark_reflected(self.name, [m.id for m in batch])
        log.info("🧠 [%s] 反思完成: %s", self.name, insight.insight[:40])
        return True



"""NPC↔NPC 对话引擎 —— 系统的灵魂模块

这解决了基座项目（Helloagents-AI-Town）"假对话"的问题:
基座只做批量独白气泡，NPC 之间互不感知；这里实现真正的
    发起 → 多轮往返 → 双方各自沉淀记忆 → 关系更新 → 反思触发

一场对话的完整流程（run 函数）:
    1. 校验（存在性/冷却），必要时把对方"调度"到同区域
    2. 发起方 opener（带触发缘由）
    3. max_rounds 个回合，双方轮流接话
    4. 双方各自以第一人称评定并写入记忆（视角不同，内容不同）
    5. LLM 评估对话对关系的影响 → apply_delta（带原因）
    6. 记录冷却时间；反思由 api/演示层在时间推进后统一触发

离线兜底: LLM 不可用时用预设台词跑通流程，记忆/关系照常写入，
保证答辩演示永不中断。
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.config import LLM as LLM_CFG
from backend.config import WORLD
from backend.llm import LLMClient
from backend.logger import get_logger
from backend.memory import MemoryStore
from backend.npc import NPCAgent
from backend.relations import RelationGraph
from backend.world import AREAS, World

log = get_logger(__name__)


class RelationImpact(BaseModel):
    """对话对两人关系的影响评估。"""

    delta: float = Field(description="好感度变化量，范围 -6 到 +6")
    reason: str = Field(description="变化原因，15字以内")


# ----------------------------------------------------------------------
class ConversationEngine:
    """驱动一场 NPC↔NPC 对话的全流程。"""

    def __init__(
        self,
        world: World,
        llm: LLMClient,
        memory: MemoryStore,
        npcs: Dict[str, NPCAgent],
        relations: RelationGraph,
    ):
        self.world = world
        self.llm = llm
        self.memory = memory
        self.npcs = npcs
        self.relations = relations

    # ------------------------------------------------------------------
    def run(self, a_name: str, b_name: str, trigger: str) -> Optional[dict]:
        """跑完一整场对话，返回结构化结果；校验失败返回 None。"""
        npc_a, npc_b = self.npcs.get(a_name), self.npcs.get(b_name)
        if not npc_a or not npc_b:
            log.error("角色不存在: %s / %s", a_name, b_name)
            return None
        ok, why = self.world.can_chat(a_name, b_name)
        if not ok:
            log.warning("对话被冷却拦截: %s", why)
            return None

        # ---- 步骤 1: 调度到同区域（M1 的简化处理，未来由移动系统驱动）----
        area_a = self.world.area_of(a_name) or "j3_lab"
        area_b = self.world.area_of(b_name) or "j3_lab"
        if area_a != area_b:
            self.world.set_area(b_name, area_a)
            log.info("调度: %s 前往 %s（与 %s 会面）", b_name,
                     AREAS[area_a]["name"], a_name)
        location = area_a

        log.info("💬 对话开始: %s × %s @ %s（%s）",
                 a_name, b_name, AREAS[location]["name"], self.world.clock.fmt())

        # ---- 步骤 2+3: opener 与多轮接话 ----
        transcript: List[dict] = []
        opener_text = npc_a.opener(trigger)
        transcript.append({"speaker": a_name, "text": opener_text})
        log.info("  %s: %s", a_name, opener_text)

        for _ in range(WORLD.max_rounds):
            reply = npc_b.speak(transcript, trigger)
            transcript.append({"speaker": b_name, "text": reply})
            log.info("  %s: %s", b_name, reply)

            closing = npc_a.speak(transcript, trigger)
            transcript.append({"speaker": a_name, "text": closing})
            log.info("  %s: %s", a_name, closing)

        game_time = self.world.clock.now

        # ---- 步骤 4: 双方各自沉淀记忆（视角独立）----
        mem_a = npc_a.save_dialogue_memory(transcript, b_name, game_time, location)
        mem_b = npc_b.save_dialogue_memory(transcript, a_name, game_time, location)

        # ---- 步骤 5: 关系更新（一次 LLM 评估）----
        impact = self._assess_relation(transcript, a_name, b_name)
        new_score = self.relations.apply_delta(a_name, b_name, impact.delta, impact.reason)

        # ---- 步骤 6: 记录冷却 ----
        self.world.mark_chatted(a_name, b_name)

        result = {
            "area": location,
            "area_name": AREAS[location]["name"],
            "game_time": self.world.clock.fmt(),
            "trigger": trigger,
            "transcript": transcript,
            "memories": {
                a_name: mem_a.to_dict() if mem_a else None,
                b_name: mem_b.to_dict() if mem_b else None,
            },
            "relation": {
                "score": new_score,
                "delta": impact.delta,
                "reason": impact.reason,
            },
        }
        log.info("✅ 对话结束（%d 句），关系 %.0f（%+.1f %s）",
                 len(transcript), new_score, impact.delta, impact.reason)
        return result

    # ------------------------------------------------------------------
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


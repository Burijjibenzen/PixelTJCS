"""GameService —— 装配根：把所有组件串成一个可运行的服务

api.py（HTTP 接口）和 scripts/run_demo.py（命令行演示）共用本类，
保证两条入口的行为完全一致。新增组件时在这里装配一次即可。
"""

import os
from typing import Dict, Optional, Tuple

from backend.config import MEMORY
from backend.conversation import ConversationEngine
from backend.llm import LLMClient
from backend.logger import get_logger
from backend.memory import MemoryStore
from backend.npc import NPCAgent
from backend.persona import PersonaCard, load_all_personas
from backend.relations import RelationGraph
from backend.world import World

log = get_logger(__name__)


class GameService:
    """持有并初始化全部子系统；对外提供高层动作（对话/时间推进/查询）。"""

    def __init__(self, fresh_db: bool = True):
        # 1) 清空旧数据库（每次冷启动从零演示；改为 False 可续跑）
        if fresh_db and os.path.exists(MEMORY.db_path):
            os.remove(MEMORY.db_path)
            log.info("已清空旧数据库: %s", MEMORY.db_path)

        # 2) 装配各子系统（依赖方向: service → 各模块，禁止反向）
        self.llm = LLMClient()
        self.memory = MemoryStore(self.llm)
        self.relations = RelationGraph()
        self.world = World()
        self.cards: Dict[str, PersonaCard] = load_all_personas()
        self.world.register(self.cards)

        # 3) 人格卡里的 initial_relations 写入关系矩阵
        for card in self.cards.values():
            for other, score in card.initial_relations.items():
                if other in self.cards:
                    self.relations.set_initial(card.name, other, score)

        # 4) 构造 NPC Agent 与对话引擎
        self.npcs: Dict[str, NPCAgent] = {
            name: NPCAgent(card, self.world, self.llm, self.memory)
            for name, card in self.cards.items()
        }
        self.engine = ConversationEngine(
            self.world, self.llm, self.memory, self.npcs, self.relations
        )
        log.info("🌍 GameService 就绪（%d 个角色）", len(self.npcs))

    # ------------------------------------------------------------------
    # 高层动作
    # ------------------------------------------------------------------
    def run_conversation(self, a: str, b: str, trigger: str) -> Optional[dict]:
        """触发一场 NPC↔NPC 对话。"""
        return self.engine.run(a, b, trigger)

    def observe(self, npc_name: str, content: str, importance: float = 0.25) -> bool:
        """让角色记下一件低重要性的琐事（遗忘演示素材）。"""
        npc = self.npcs.get(npc_name)
        if not npc:
            return False
        return npc.observe(content, importance) is not None

    def advance_time(self, minutes: float) -> dict:
        """时间推进 = 时钟前进 + 全员反思 + 遗忘检查（演示"遗忘"的核心入口）。

        顺序说明: 先反思后衰减。反思会把待压缩记忆的 importance 减半，
        这些被"降权"的旧记忆紧接着在衰减检查中自然落入遗忘档案——
        一次时间推进即可完整演示"压缩 + 淡忘"两条遗忘路径。
        """
        self.world.clock.advance(minutes)
        log.info("⏩ 时间推进 %.0f 游戏分钟 → %s", minutes, self.world.clock.fmt())
        reflected = [n for n in self.npcs if self.npcs[n].maybe_reflect()]
        forget_stats = self.memory.full_forget_pass(self.world.clock.now)
        return {
            "now": self.world.clock.fmt(),
            "forget": forget_stats,
            "reflected": reflected,
        }

    def goto(self, npc_name: str, area_id: str) -> bool:
        """指派角色前往某区域（M2.2）：

        - set_area: 立即就位（M1 文字演示的语义不变，prompt 位置立刻正确）
        - assign_override: 记录覆盖指令，Godot 前端轮询到后会寻路跟随；
          日程翻段后自动失效
        """
        ok = self.world.set_area(npc_name, area_id)
        if ok:
            self.world.assign_override(npc_name, area_id)
        return ok

    def report_arrival(self, npc_name: str, grid_id: str) -> Tuple[bool, str]:
        """前端到达上报（M2.2）：只更新位置+日志，不写记忆（见 world.report_arrival）。"""
        return self.world.report_arrival(npc_name, grid_id)

    def chat_with_npc(self, npc_name: str, message: str) -> Optional[dict]:
        """玩家 ↔ NPC 单轮对话（玩家模式的最小实现，M4 扩展）。"""
        npc = self.npcs.get(npc_name)
        if not npc:
            return None
        history = [{"speaker": "玩家", "text": message}]
        reply = npc.speak(history, f"玩家主动找你说话: {message}")
        # 玩家说的话也值得被 NPC 记住
        mem = npc.save_dialogue_memory(
            history, "玩家", self.world.clock.now, self.world.area_of(npc_name) or "j3_lab"
        )
        return {"reply": reply, "memory": mem.to_dict() if mem else None}

    # ------------------------------------------------------------------
    # 查询（供 API / 前端）
    # ------------------------------------------------------------------
    def npc_list(self) -> list:
        return [
            {
                "name": c.name, "role": c.role, "title": c.title,
                "mbti": c.mbti, "traits": c.traits,
                "area": self.world.area_of(c.name),
            }
            for c in self.cards.values()
        ]

    def memory_panel(self, npc_name: str) -> Optional[dict]:
        """个人记忆面板：活跃 / 已遗忘 / 洞察 三视图。"""
        if npc_name not in self.cards:
            return None
        return {
            "npc": npc_name,
            "active": [m.to_dict() for m in self.memory.get_active(npc_name, 50)],
            "forgotten": [m.to_dict() for m in self.memory.get_forgotten(npc_name, 50)],
            "reflections": [m.to_dict() for m in self.memory.get_reflections(npc_name, 10)],
        }

    def relations_snapshot(self) -> dict:
        return self.relations.matrix()

    def world_snapshot(self) -> dict:
        return self.world.snapshot()

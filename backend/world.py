"""游戏世界 —— 时钟、区域地图、NPC 所在位置、事件总线

M1 范围说明:
    NPC 自由移动暂不做（那是 Godot 前端的活），NPC 的"位置"是逻辑概念，
    由世界事件/调度显式设置（set_area）。co_located_pairs() 已实现
    "同区域判定"，是 M3 自动调度器的地基 —— 届时循环:
        tick → 移动意图 → 同区域相遇 → 触发对话。

时间加速: 游戏时间用 float 分钟表示；演示脚本一次快进 N 天来展示记忆衰减。
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.config import WORLD
from backend.logger import get_logger
from backend.persona import PersonaCard

log = get_logger(__name__)

# ----------------------------------------------------------------------
# 区域地图（覆盖纪事楼 3F/4F、智信馆 4F；新增区域直接往里加即可）
# kind: lab=实验室  office=办公室  public=公共区  facility=设施
# ----------------------------------------------------------------------
AREAS: Dict[str, dict] = {
    a["id"]: a
    for a in [
        {"id": "j3_lab", "name": "纪事楼3F·实验室", "building": "纪事楼", "floor": 3, "kind": "lab"},
        {"id": "j3_pantry", "name": "纪事楼3F·茶水间", "building": "纪事楼", "floor": 3, "kind": "public"},
        {"id": "j3_wc", "name": "纪事楼3F·洗手间", "building": "纪事楼", "floor": 3, "kind": "facility"},
        {"id": "j4_lab", "name": "纪事楼4F·实验室", "building": "纪事楼", "floor": 4, "kind": "lab"},
        {"id": "j4_meeting", "name": "纪事楼4F·会议室", "building": "纪事楼", "floor": 4, "kind": "public"},
        {"id": "j4_wc", "name": "纪事楼4F·洗手间", "building": "纪事楼", "floor": 4, "kind": "facility"},
        {"id": "zx4_yan", "name": "智信馆4F·严老师办公室", "building": "智信馆", "floor": 4, "kind": "office"},
        {"id": "zx4_wen", "name": "智信馆4F·温老师办公室", "building": "智信馆", "floor": 4, "kind": "office"},
        {"id": "zx4_qian", "name": "智信馆4F·教务办公室", "building": "智信馆", "floor": 4, "kind": "office"},
        {"id": "zx4_hall", "name": "智信馆4F·大厅", "building": "智信馆", "floor": 4, "kind": "public"},
    ]
}


@dataclass
class GameClock:
    """游戏时钟。内部只存"从开局起经过了多少游戏分钟"。"""

    minutes: float = WORLD.start_minutes
    start: float = WORLD.start_minutes

    def advance(self, minutes: float) -> None:
        self.minutes += minutes

    @property
    def now(self) -> float:
        return self.minutes

    def fmt(self) -> str:
        """格式化为「第 D 天 HH:MM」。"""
        total = self.minutes
        day = int(total // WORLD.minutes_per_day) + 1
        rem = total % WORLD.minutes_per_day
        return f"第{day}天 {int(rem // 60):02d}:{int(rem % 60):02d}"


# ----------------------------------------------------------------------
@dataclass
class World:
    """世界状态容器：时钟 + 每个角色的位置 + 事件流水。

    npc 的具体行为由 npc.py / conversation.py 驱动；本类只管"世界事实"。
    """

    clock: GameClock = field(default_factory=GameClock)
    personas: Dict[str, PersonaCard] = field(default_factory=dict)
    npc_area: Dict[str, str] = field(default_factory=dict)  # {角色名: 区域id}
    events: List[dict] = field(default_factory=list)  # 事件流水
    last_chat_at: Dict[Tuple[str, str], float] = field(default_factory=dict)

    # ---------------- 初始化 ----------------
    def register(self, cards: Dict[str, PersonaCard]) -> None:
        """注册全部角色，按人格卡的 schedule 设定初始位置。"""
        self.personas = cards
        for name, card in cards.items():
            area = self.schedule_area(card, self.clock.now)
            self.npc_area[name] = area
            log.info("📍 %s 初始位置: %s", name, AREAS.get(area, {}).get("name", area))

    # ---------------- 位置 ----------------
    def set_area(self, name: str, area_id: str) -> bool:
        """把角色放到某区域（M1 的"传送"，未来替换为 Godot 驱动的移动）。"""
        if area_id not in AREAS:
            log.error("未知区域: %s", area_id)
            return False
        old = self.npc_area.get(name, "（未注册）")
        self.npc_area[name] = area_id
        log.info("🚶 %s: %s → %s", name,
                 AREAS.get(old, {}).get("name", old), AREAS[area_id]["name"])
        return True

    def area_of(self, name: str) -> Optional[str]:
        return self.npc_area.get(name)

    def co_located_pairs(self) -> List[Tuple[str, str, str]]:
        """找出所有"同区域"的角色对 —— M3 自动调度器的相遇检测地基。"""
        by_area: Dict[str, List[str]] = {}
        for name, area in self.npc_area.items():
            by_area.setdefault(area, []).append(name)
        pairs = []
        for area, names in by_area.items():
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    pairs.append((names[i], names[j], area))
        return pairs

    # ---------------- 时间 / 日程 ----------------
    def schedule_area(self, card: PersonaCard, now: float) -> str:
        """按人格卡日程推断角色此刻应在的区域（"HH:MM" → 区域id）。"""
        if not card.schedule:
            return "j3_lab"  # 默认实验室
        minute_of_day = now % WORLD.minutes_per_day
        entries = []
        for hhmm, area in card.schedule.items():
            h, m = hhmm.split(":")
            entries.append((int(h) * 60 + int(m), area))
        entries.sort()
        chosen = entries[0][1]  # 早于第一项 → 当天第一站
        for t, area in entries:
            if t <= minute_of_day:
                chosen = area
            else:
                break
        return chosen if chosen in AREAS else "j3_lab"

    def apply_schedules(self) -> None:
        """让所有角色按日程就位（演示脚本在每个场景开始时调用）。"""
        for name, card in self.personas.items():
            self.npc_area[name] = self.schedule_area(card, self.clock.now)

    # ---------------- 事件 / 对话冷却 ----------------
    def inject_event(self, text: str, participants: List[str], area: str = "") -> dict:
        """注入世界事件（如"组会通知"），写入事件流水并返回事件对象。"""
        event = {
            "id": len(self.events) + 1,
            "time": self.clock.fmt(),
            "text": text,
            "participants": participants,
            "area": area,
        }
        self.events.append(event)
        log.info("📢 世界事件: %s（涉及: %s）", text, "、".join(participants))
        return event

    def can_chat(self, a: str, b: str) -> Tuple[bool, str]:
        """对话冷却判定：同一对角色冷却期内不能重复触发。"""
        key = (a, b) if a <= b else (b, a)
        last = self.last_chat_at.get(key, -1e9)
        gap = self.clock.now - last
        if gap < WORLD.cooldown_minutes:
            return False, f"冷却中（还需 {WORLD.cooldown_minutes - gap:.0f} 游戏分钟）"
        return True, "ok"

    def mark_chatted(self, a: str, b: str) -> None:
        key = (a, b) if a <= b else (b, a)
        self.last_chat_at[key] = self.clock.now

    # ---------------- 快照（给前端 / API） ----------------
    def snapshot(self) -> dict:
        return {
            "time": self.clock.fmt(),
            "npcs": {
                name: {"area": area, "area_name": AREAS[area]["name"]}
                for name, area in self.npc_area.items()
            },
            "events": self.events[-20:],  # 最近 20 条
        }


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

# ----------------------------------------------------------------------
# M2.2 语义区域 → 前端几何区域(R##) 映射表 ★后续同学只改这一张表★
#
# 背景: Godot 前端目前只有"纪事楼 3F"一张图（几何区域 R01~R13，见
#   frontend/assets/map/jishilou_floor3/rooms.json，房间名可随意改，
#   编号稳定）。而人格卡日程可能把角色派往 4F / 智信馆的语义区域。
# 作用: 把语义区域"投影"到 3F 的对应房间，让前端永远有路可走——
#   单楼层演示期跨楼日程照样生效；M4 接入真实多楼层地图后删除本表即可。
# 设计原则:
#   - 键 = AREAS 的语义 id（人格卡/日程/事件都用它，不许改）
#   - 值 = 前端 rooms.json 的区域编号 R##（改房间名不影响映射）
#   - 一对多允许（多个洗手间可投影到同一间），反查取表序第一个
# ----------------------------------------------------------------------
GRID_OF: Dict[str, str] = {
    "j3_lab": "R03",      # 3F 实验室     → R03 J3实验室（名字直接对应）
    "j3_pantry": "R07",   # 3F 茶水间     → R07 茶水间（名字直接对应）
    "j3_wc": "R12",       # 3F 洗手间     → R12 小隔间（图上无明确洗手间，借用）
    "j4_lab": "R05",      # 4F 实验室     → R05 机房C（另一间实验室）
    "j4_meeting": "R06",  # 4F 会议室     → R06 左下开放区（最大公共区）
    "j4_wc": "R12",       # 4F 洗手间     → R12 小隔间（与 3F 合并）
    "zx4_yan": "R09",     # 严老师办公室  → R09 中部小间（独立小间当办公室）
    "zx4_wen": "R10",     # 温老师办公室  → R10 楼道门厅（次独立空间）
    "zx4_qian": "R02",    # 教务办公室    → R02 机房B（借用）
    "zx4_hall": "R01",    # 智信馆大厅    → R01 走廊与开放区（完美对应）
}
DEFAULT_GRID = "R01"  # 映射缺项时的兜底投影（走廊，四通八达不会卡死）


def grid_of(area_id: str) -> str:
    """语义区域 id → 前端几何区域 id（R##）。缺项投影到 DEFAULT_GRID。"""
    return GRID_OF.get(area_id, DEFAULT_GRID)


def area_of_grid(grid_id: str) -> Optional[str]:
    """前端几何区域 id(R##) → 语义区域 id（多对一时取表序第一个）。

    返回 None 表示前端上报了映射表里没有的几何区域（非法/未投影）。
    """
    for aid, gid in GRID_OF.items():
        if gid == grid_id and aid in AREAS:
            return aid
    return None


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
    # M2.2: 显式指派(/world/goto)优先于日程；当前日程段翻篇后自动失效。
    #   override_slot 记录"指派时所处的日程段"，用于检测翻段。
    override_area: Dict[str, str] = field(default_factory=dict)
    override_slot: Dict[str, str] = field(default_factory=dict)
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

    # ---------------- 指派 / 目标计算（M2.2 前端联动核心） ----------------
    def assign_override(self, name: str, area_id: str) -> None:
        """记录显式指派（/world/goto 转发而来），优先于日程。

        失效规则: 当人格卡日程翻到下一个时间段（schedule_area 结果变化），
        指派自动作废、角色回归日程——避免"一次召见把人永久钉在办公室"。
        """
        if name not in self.personas or area_id not in AREAS:
            log.error("指派无效: %s → %s", name, area_id)
            return
        self.override_area[name] = area_id
        self.override_slot[name] = self.schedule_area(self.personas[name], self.clock.now)
        log.info("🎯 %s 被指派前往 %s（日程翻段后自动失效）",
                 name, AREAS[area_id]["name"])

    def target_area(self, name: str) -> str:
        """角色此刻应前往的区域：显式指派 > 人格卡日程（惰性计算，无后台线程）。

        前端每次轮询 /world 都会取该值并换算成几何区域下发寻路，
        因此后端不需要任何定时器——时钟推进/日程变化在下次轮询即生效。
        """
        ov = self.override_area.get(name)
        if ov is not None:
            card = self.personas[name]
            if self.schedule_area(card, self.clock.now) != self.override_slot.get(name):
                self.override_area.pop(name, None)
                self.override_slot.pop(name, None)
                log.info("⌛ %s 的指派过期，回归日程", name)
            else:
                return ov
        return self.schedule_area(self.personas[name], self.clock.now)

    def report_arrival(self, name: str, grid_id: str) -> Tuple[bool, str]:
        """前端到达上报（M2.2）：几何区域 id → 语义 id，只更新位置+日志。

        ⚠️ 决策（已确认）：到达**不写记忆**——10 人频繁移动会把记忆库
        刷成噪音；"和谁同处一室"的观察记忆留给 M2.3 相遇/视野事件时写。
        """
        if name not in self.npc_area:
            return False, f"未知角色: {name}"
        if grid_of(self.npc_area[name]) == grid_id:
            return True, "already"  # 投影相同 → 去抖，不重复刷日志
        area_id = area_of_grid(grid_id)
        if area_id is None:
            return False, f"未知几何区域（不在 GRID_OF 投影内）: {grid_id}"
        self.set_area(name, area_id)
        return True, "moved"

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
        """世界快照。M2.2 起新增 grid / target_grid 字段供 Godot 寻路：

        - grid:        角色当前逻辑位置投影到前端的几何区域（R##）
        - target_grid: 角色此刻应前往的几何区域（指派 > 日程，惰性计算）
        旧字段（area/area_name/events）保持不变，M1 调用方零影响。
        """
        return {
            "time": self.clock.fmt(),
            "npcs": {
                name: {
                    "area": area,
                    "area_name": AREAS[area]["name"] if area in AREAS else area,
                    "grid": grid_of(area),
                    "target_grid": grid_of(self.target_area(name)),
                }
                for name, area in self.npc_area.items()
            },
            "events": self.events[-20:],  # 最近 20 条
        }


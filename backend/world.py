"""校园世界的时钟、实际位置、日程目标和事件。"""

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import WORLD
from backend.logger import get_logger
from backend.persona import PersonaCard

log = get_logger(__name__)

J3 = "jishilou_floor3"
J4 = "jishilou_floor4"
ZX2 = "zhixinguan_floor2"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# 人格卡、旧演示指令和已保存的记忆沿用这些地点名。最后一列只给单层旧客户端。
# 展示名、当前位置与新客户端的寻路目标一律取真实楼层清单。
LEGACY_LOCATIONS = {
    "j3_lab": (J3, "R03", "R03"),
    "j3_pantry": (J3, "R07", "R07"),
    "j3_wc": (J3, "R09", "R12"),
    "j4_lab": (J4, "R03", "R05"),
    "j4_meeting": (J4, "R05", "R06"),
    "j4_wc": (J4, "R09", "R12"),
    "zx4_hall": (ZX2, "zx2_corridor", "R01"),
}

# 教师和教务的原日程继续引用办公室别名。实际落点和 Godot 出生位置共用角色名册。
def load_offices() -> Dict[str, Tuple[str, str, str]]:
    old_grids = {"zx4_yan": "R09", "zx4_wen": "R10", "zx4_qian": "R02"}
    roster = json.loads((FRONTEND_DIR / "data/npc_roster.json").read_text(encoding="utf-8-sig"))
    offices = {}
    for person in roster["npcs"]:
        alias = person.get("office_alias")
        if alias:
            offices[alias] = (person.get("home_map", J3), person["home"], old_grids[alias])
    if offices.keys() != old_grids.keys():
        raise ValueError("npc_roster.json 中的教师、教务办公室别名与原日程不一致")
    return offices


LEGACY_LOCATIONS.update(load_offices())


def load_rooms() -> Dict[Tuple[str, str], dict]:
    map_dir = FRONTEND_DIR / "assets/map"
    rooms = {}
    for map_id, building, floor in ((J3, "济事楼", 3), (J4, "济事楼", 4), (ZX2, "智信馆", 2)):
        manifest = json.loads((map_dir / map_id / "manifest.json").read_text(encoding="utf-8-sig"))
        for room in manifest["rooms"]:
            rid = room["id"]
            rooms[map_id, rid] = {
                "id": f"{map_id}/{rid}", "map_id": map_id, "room_id": rid,
                "building": building, "floor": floor, "kind": room["type"],
                "name": f"{manifest['name']}·{room['name']}", "aliases": [],
            }
    for alias, (map_id, rid, _) in LEGACY_LOCATIONS.items():
        rooms[map_id, rid]["aliases"].append(alias)
    return rooms


ROOMS = load_rooms()
AREAS = {area["id"]: area for area in ROOMS.values()}
for alias, (map_id, rid, _) in LEGACY_LOCATIONS.items():
    AREAS[alias] = ROOMS[map_id, rid]


def grid_of(area_id: str) -> str:
    """单层旧客户端的投影；多层客户端直接使用 map_id 和 room_id。"""
    if area_id in LEGACY_LOCATIONS:
        return LEGACY_LOCATIONS[area_id][2]
    area = AREAS[area_id]
    aliases = area["aliases"]
    if aliases:
        return LEGACY_LOCATIONS[aliases[0]][2]
    return area["room_id"] if area["map_id"] == J3 else "R01"


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
    activity_targets: Dict[str, str] = field(default_factory=dict)  # 社交调度器锁定的行程
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
        if name not in self.personas or area_id not in AREAS:
            log.error("未知角色或区域: %s, %s", name, area_id)
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
            by_area.setdefault(AREAS[area]["id"], []).append(name)
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
    def assign_override(self, name: str, area_id: str) -> bool:
        """记录显式指派（/world/goto 转发而来），优先于日程。

        失效规则: 当人格卡日程翻到下一个时间段（schedule_area 结果变化），
        指派自动作废、角色回归日程——避免"一次召见把人永久钉在办公室"。
        """
        if name not in self.personas or area_id not in AREAS:
            log.error("指派无效: %s → %s", name, area_id)
            return False
        self.override_area[name] = area_id
        self.override_slot[name] = self.schedule_area(self.personas[name], self.clock.now)
        log.info("🎯 %s 被指派前往 %s（日程翻段后自动失效）",
                 name, AREAS[area_id]["name"])
        return True

    def target_area(self, name: str) -> str:
        """兼容旧指派，其次取动态调度器锁定的行程，最后用人格卡兜底。

        World只保存目标事实；动态生命周期由daily_planner驱动，Godot负责实际行走。
        CLI不装配动态调度器时继续按原固定日程运行。
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
        return self.activity_targets.get(name) or self.schedule_area(self.personas[name], self.clock.now)

    def report_arrival(self, name: str, grid_id: Optional[str] = None,
                       map_id: Optional[str] = None, room_id: Optional[str] = None) -> Tuple[bool, str]:
        """到达时只更新实际位置；经过楼梯或走廊不会覆盖日程或目的地。"""
        if name not in self.npc_area:
            return False, f"未知角色: {name}"
        current = self.npc_area[name]
        target = self.target_area(name)
        if map_id is not None or room_id is not None:
            room = ROOMS.get((map_id, room_id))
            if room is None:
                return False, f"未知楼层或房间: {map_id}/{room_id}"
            area_id = target if AREAS[target]["id"] == room["id"] else room["id"]
            if AREAS[current]["id"] == room["id"]:
                return True, "already"
        else:
            # 只有旧版前端才上报裸格号。先匹配目标，避免两个楼层同号时误选。
            if not grid_id:
                return False, "需要 map_id 和 room_id，或旧版 grid"
            if grid_of(target) == grid_id:
                area_id = target
            elif grid_of(current) == grid_id:
                return True, "already"
            else:
                area_id = next((a for a, (_, _, g) in LEGACY_LOCATIONS.items() if g == grid_id), None)
                if area_id is None:
                    room = ROOMS.get((J3, grid_id))
                    if room is None:
                        return False, f"未知旧版区域: {grid_id}"
                    area_id = room["id"]
            if AREAS[current]["id"] == AREAS[area_id]["id"]:
                return True, "already"
        self.set_area(name, area_id)
        return True, "moved"

    # ---------------- 事件 / 对话冷却 ----------------
    def inject_event(self, text: str, participants: List[str], area: str = "") -> dict:
        """注入世界事件（如"组会通知"），写入事件流水并返回事件对象。"""
        event = {
            "id": len(self.events) + 1,
            "key": uuid.uuid4().hex,
            "game_time": self.clock.now,
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
        """每名角色分别给出实际地点和日程/指派目的地。"""
        npcs = {}
        for name, area_id in self.npc_area.items():
            target_id = self.target_area(name)
            current, target = AREAS[area_id], AREAS[target_id]
            npcs[name] = {
                "area": area_id, "area_name": current["name"],
                "map_id": current["map_id"], "room_id": current["room_id"],
                "target_map_id": target["map_id"], "target_room_id": target["room_id"],
                "grid": grid_of(area_id), "target_grid": grid_of(target_id),
            }
        return {"time": self.clock.fmt(), "minutes": self.clock.now, "npcs": npcs, "events": self.events[-20:]}

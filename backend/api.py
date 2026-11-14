"""FastAPI 接口层 —— 供 Godot 前端 / 演示脚本调用的 HTTP API

启动:
    cd Roamchestra
    uvicorn backend.api:app --reload --port 8000

交互式文档（自动生成）:
    http://127.0.0.1:8000/docs
"""

from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.logger import get_logger
# M3: HTTP 使用独立社交服务；原 GameService 保留给命令行演示。
from backend.social import SocialService as GameService, install
from backend.world import ROOMS
from backend.config import SOCIAL

log = get_logger(__name__)

# 全局单例: 进程内只装配一次世界
_service: Optional[GameService] = None
_service_lock = Lock()


def get_service() -> GameService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = GameService(fresh_db=False)  # M3: 重启保留记忆、八卦和关系
    return _service


app = FastAPI(title="Roamchestra 校园多智能体交互系统", version="0.1.0")
# 允许 Godot/浏览器跨域调试
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class ConversationReq(BaseModel):
    a: str  # 发起方角色名
    b: str  # 接收方角色名
    trigger: str = "在走廊偶遇，闲聊几句"


class AdvanceReq(BaseModel):
    minutes: float = Field(gt=0, le=43200, allow_inf_nan=False)
    speed: float = Field(default=SOCIAL.fast_forward_speed, gt=0, le=60, allow_inf_nan=False)


class GotoReq(BaseModel):
    npc: str
    area: Optional[str] = None  # /areas 中的 id，或旧版日程地点
    map_id: Optional[str] = None
    room_id: Optional[str] = None


class ReportReq(BaseModel):
    """新客户端上报实际楼层和房间；旧客户端可继续发送 grid。"""
    npc: str
    map_id: Optional[str] = None
    room_id: Optional[str] = None
    grid: Optional[str] = None
    arrived: Optional[bool] = None  # False 表示经过此房间，仍在行走
    activity_id: Optional[str] = None
    x: Optional[float] = Field(default=None,ge=-1000000,le=1000000,allow_inf_nan=False)
    y: Optional[float] = Field(default=None,ge=-1000000,le=1000000,allow_inf_nan=False)


class ChatReq(BaseModel):
    npc: str
    message: str


class PublicLocation(BaseModel):
    area: Optional[str] = None
    name: str = "未知地点"
    map_id: Optional[str] = None
    room_id: Optional[str] = None


class PublicGoals(BaseModel):
    short_term: str = ""
    long_term: str = ""


class PublicScheduleEntry(BaseModel):
    time: str
    location: PublicLocation


class PublicNPCProfile(BaseModel):
    """响应也使用白名单；未来内部新增字段不会自动流到前端。"""
    name: str
    role: str
    title: str
    expertise: str = ""
    mbti: str = ""
    traits: list[str] = Field(default_factory=list)
    tone: str = ""
    catchphrase: str = ""
    goals: PublicGoals
    schedule: list[PublicScheduleEntry] = Field(default_factory=list)
    location: PublicLocation
    destination: PublicLocation
    game_time: str


# ----------------------------------------------------------------------
# 只读接口
# ----------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "roamchestra", "version": "0.1.0"}


@app.get("/world")
def world_snapshot():
    """世界快照: 当前时间 + 每个角色位置 + 最近事件。"""
    return get_service().world_snapshot()


@app.get("/npcs")
def npc_list():
    """角色名单（含身份/性格/当前位置）。"""
    return get_service().npc_list()


@app.get("/npcs/{name}/profile", response_model=PublicNPCProfile)
def npc_public_profile(name: str):
    profile = get_service().public_npc_profile(name)
    if profile is None:
        raise HTTPException(404, f"角色不存在: {name}")
    return profile


@app.get("/npcs/{name}/memory")
def memory_panel(name: str, view: Optional[str] = None, snapshot_id: Optional[int] = None):
    """个人记忆面板: 活跃记忆 / 遗忘档案 / 洞察（遗忘可视化的数据源）。"""
    panel = get_service().memory_panel(name, snapshot_id=snapshot_id)
    if panel is None:
        raise HTTPException(404, f"角色不存在: {name}")
    # M3: view 查询按 DESIGN §8；不带 view 时兼容原三组面板。
    if view is not None:
        section = {'active': 'active', 'forgotten': 'forgotten', 'reflection': 'reflections'}.get(view)
        if section is None:
            raise HTTPException(422, 'view 必须是 active / forgotten / reflection')
        return dict(npc=name, items=panel[section], as_of=panel.get('as_of'))
    return panel


@app.get("/relations")
def relations():
    """全员关系矩阵（0~100，None=陌生人）。"""
    return get_service().relations_snapshot()


@app.get("/areas")
def areas():
    """每个真实房间一条；原有地点保留旧版 id，新客户端使用 map_id 和 room_id。"""
    return [dict(room, id=room["aliases"][0]) if room["aliases"] else room
            for room in ROOMS.values()]


# ----------------------------------------------------------------------
# 动作接口
# ----------------------------------------------------------------------
@app.post("/conversation")
def run_conversation(req: ConversationReq):
    """触发一场 NPC↔NPC 对话（核心接口）。"""
    result = get_service().run_conversation(req.a, req.b, req.trigger)
    if result is None:
        raise HTTPException(400, "对话未能开始（角色不存在或处于冷却期）")
    return result


@app.post("/world/advance")
def advance_time(req: AdvanceReq):
    """连续加速指定游戏分钟数；到达目标后恢复原倍速，不跳变时钟。"""
    return get_service().fast_forward(req.minutes, req.speed)


@app.post("/world/goto")
def goto(req: GotoReq):
    """设置目的地。实际位置由前端行走和到达上报更新。"""
    area = req.area
    if req.map_id is not None or req.room_id is not None:
        room = ROOMS.get((req.map_id, req.room_id))
        if room is None or area is not None:
            raise HTTPException(400, "请提供有效的 map_id 和 room_id，或只提供 area")
        area = room["id"]
    if not area or not get_service().assign_destination(req.npc, area):
        raise HTTPException(400, "npc 或 area 无效")
    return {"ok": True, "npc": req.npc, "area": area}


@app.post("/world/report")
def report_arrival(req: ReportReq):
    """前端到达上报（M2.2）：只更新世界事实中的位置 + 日志。

    ⚠️ 决策（已确认）：不写记忆——防 10 人移动噪音；
    "同处一室"观察记忆由 M2.3 相遇/视野事件写入。
    """
    ok, msg = get_service().report_arrival(req.npc, req.grid, req.map_id, req.room_id, req.arrived, req.activity_id, req.x, req.y)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "npc": req.npc, "grid": req.grid, "map_id": req.map_id,
            "room_id": req.room_id, "detail": msg}


@app.post("/chat")
def chat_with_npc(req: ChatReq):
    """玩家 ↔ NPC 单轮对话（玩家模式最小实现）。"""
    result = get_service().chat_with_npc(req.npc, req.message)
    if result is None:
        raise HTTPException(404, f"角色不存在: {req.npc}")
    return result

# M3: 注册社交事件/控制接口并随应用启动和关闭调度器。
install(app, get_service)

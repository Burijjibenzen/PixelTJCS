"""FastAPI 接口层 —— 供 Godot 前端 / 演示脚本调用的 HTTP API

启动:
    cd Roamchestra
    uvicorn backend.api:app --reload --port 8000

交互式文档（自动生成）:
    http://127.0.0.1:8000/docs
"""

import asyncio
from threading import Lock
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.logger import get_logger
# M3: HTTP 使用独立社交服务；原 GameService 保留给命令行演示。
from backend.social import SocialService as GameService, install
from backend.world import ROOMS
from backend.config import SOCIAL
from backend.config import PLAYER
from backend.player_mode import PlayerError

log = get_logger(__name__)

# 全局单例: 进程内只装配一次世界
_service: Optional[GameService] = None
_service_lock = Lock()
_resetting = False
_active_requests = 0


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
    CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"], allow_headers=["*"]
)

@app.middleware("http")
async def guard_world_reset(request,call_next):
    global _active_requests
    if request.url.path=="/world/reset":
        return await call_next(request)
    if _resetting:
        return JSONResponse({'detail':'世界正在重置'},status_code=503)
    _active_requests+=1
    try:
        return await call_next(request)
    finally:
        _active_requests-=1


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
    encounter_wait: Optional[str] = None
    cast_version: Optional[int] = None
    x: Optional[float] = Field(default=None,ge=-1000000,le=1000000,allow_inf_nan=False)
    y: Optional[float] = Field(default=None,ge=-1000000,le=1000000,allow_inf_nan=False)
    world_id: Optional[str] = None


class ResetReq(BaseModel):
    confirm: Literal[True]
    world_id: str


class ChatReq(BaseModel):
    npc: str
    message: str = Field(min_length=1, max_length=PLAYER.message_max_chars)
    session_id: Optional[str] = Field(default=None, max_length=100)
    request_id: Optional[str] = Field(default=None, max_length=100)


class PlayerJoinReq(BaseModel):
    name: str = Field(default='玩家', min_length=1, max_length=PLAYER.name_max_chars)
    session_id: Optional[str] = Field(default=None, max_length=100)
    player_token: Optional[str] = Field(default=None, max_length=128)


class PlayerSessionReq(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


class PlayerChatReq(PlayerSessionReq):
    npc: str
    message: str = Field(min_length=1, max_length=PLAYER.message_max_chars)
    request_id: str = Field(min_length=1, max_length=100)


class PlayerEngageReq(PlayerSessionReq):
    npc: str


class PlayerPositionReq(PlayerSessionReq):
    map_id: str
    room_id: str
    x: float = Field(ge=-1000000, le=1000000, allow_inf_nan=False)
    y: float = Field(ge=-1000000, le=1000000, allow_inf_nan=False)


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
    activity: str = "日常安排"


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


def initialize_world(previous):
    with previous.database() as db:
        db.execute('BEGIN IMMEDIATE')
        tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for name, in tables:
            db.execute('DELETE FROM "'+name.replace('"','""')+'"')
    return GameService(fresh_db=False)


@app.post("/world/reset")
async def reset_world(req: ResetReq):
    global _resetting
    if _resetting:
        raise HTTPException(409,'世界正在重置')
    previous=get_service()
    if req.world_id!=previous.world_id:
        raise HTTPException(409,'世界已经更新，请刷新后再试')
    _resetting=True
    return await asyncio.shield(replace_world(previous))


async def replace_world(previous):
    global _resetting,_service
    try:
        # 唤醒暂停中等待的请求，并等待本轮请求和后台工作全部退出再清库。
        await asyncio.to_thread(previous.stop)
        while _active_requests:
            await asyncio.sleep(0.025)
        replacement=await asyncio.to_thread(initialize_world,previous)
        world=replacement.world_snapshot()
        _service=replacement
        previous.bus.notify_world_reset(world)
        replacement.start()
        return {'ok':True,'world':world}
    finally:
        _resetting=False


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
    s=get_service()
    if req.world_id is not None and req.world_id!=s.world_id:
        raise HTTPException(409,'忽略来自旧世界的位置上报')
    ok, msg = s.report_arrival(req.npc, req.grid, req.map_id, req.room_id, req.arrived,
                               req.activity_id, req.x, req.y, req.encounter_wait, req.cast_version)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "npc": req.npc, "grid": req.grid, "map_id": req.map_id,
            "room_id": req.room_id, "detail": msg}


@app.post("/chat")
def chat_with_npc(req: ChatReq):
    """兼容单轮同步入口；同样要求玩家已加入。Godot使用/player/chat异步入口。"""
    return player_action(lambda: get_service().chat_with_npc(req.npc, req.message, req.session_id, req.request_id))


def player_action(action):
    try:return action()
    except PlayerError as error:raise HTTPException(error.status, str(error))


@app.get('/player')
def player_state(x_player_session: Optional[str] = Header(default=None)):
    return get_service().player.state(x_player_session)


@app.post('/player/join')
def player_join(req: PlayerJoinReq):
    return player_action(lambda: get_service().player.join(req.name, req.session_id, req.player_token))


@app.post('/player/leave')
def player_leave(req: PlayerSessionReq):
    return player_action(lambda: get_service().player.leave(req.session_id))


@app.post('/player/engage')
def player_engage(req: PlayerEngageReq):
    return player_action(lambda: get_service().player.engage(req.session_id, req.npc))


@app.post('/player/position')
def player_position(req: PlayerPositionReq):
    return player_action(lambda: get_service().player.report_position(
        req.session_id, req.map_id, req.room_id, req.x, req.y))


@app.post('/player/disengage')
def player_disengage(req: PlayerSessionReq):
    return player_action(lambda: get_service().player.disengage(req.session_id))


@app.post('/player/chat', status_code=202)
def player_chat(req: PlayerChatReq):
    return player_action(lambda: get_service().player.submit(req.session_id, req.npc, req.message, req.request_id))


@app.get('/player/turns/{turn_id}')
def player_turn(turn_id: str, session_id: str):
    return player_action(lambda: get_service().player.turn(session_id, turn_id))


@app.get('/player/history')
def player_history(session_id: str, npc: Optional[str] = None):
    return player_action(lambda: dict(turns=get_service().player.history(session_id, npc)))


@app.get('/player/memory')
def player_memory(session_id: str):
    return player_action(lambda: get_service().player.memories(session_id))

# M3: 注册社交事件/控制接口并随应用启动和关闭调度器。
install(app, get_service)

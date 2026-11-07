"""FastAPI 接口层 —— 供 Godot 前端 / 演示脚本调用的 HTTP API

启动:
    cd Roamchestra
    uvicorn backend.api:app --reload --port 8000

交互式文档（自动生成）:
    http://127.0.0.1:8000/docs
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.logger import get_logger
from backend.service import GameService
from backend.world import AREAS

log = get_logger(__name__)

# 全局单例: 进程内只装配一次世界
_service: Optional[GameService] = None


def get_service() -> GameService:
    global _service
    if _service is None:
        _service = GameService(fresh_db=True)
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
    minutes: float  # 推进多少游戏分钟（演示遗忘: 一次推 3 天 = 4320）


class GotoReq(BaseModel):
    npc: str
    area: str  # 区域 id，见 /areas


class ChatReq(BaseModel):
    npc: str
    message: str


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


@app.get("/npcs/{name}/memory")
def memory_panel(name: str):
    """个人记忆面板: 活跃记忆 / 遗忘档案 / 洞察（遗忘可视化的数据源）。"""
    panel = get_service().memory_panel(name)
    if panel is None:
        raise HTTPException(404, f"角色不存在: {name}")
    return panel


@app.get("/relations")
def relations():
    """全员关系矩阵（0~100，None=陌生人）。"""
    return get_service().relations_snapshot()


@app.get("/areas")
def areas():
    """区域地图。"""
    return list(AREAS.values())


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
    """推进游戏时间，触发遗忘检查与反思（时间加速演示入口）。"""
    return get_service().advance_time(req.minutes)


@app.post("/world/goto")
def goto(req: GotoReq):
    """把角色放到某区域（M1 传送占位，未来由前端移动事件驱动）。"""
    ok = get_service().goto(req.npc, req.area)
    if not ok:
        raise HTTPException(400, "npc 或 area 无效")
    return {"ok": True, "npc": req.npc, "area": req.area}


@app.post("/chat")
def chat_with_npc(req: ChatReq):
    """玩家 ↔ NPC 单轮对话（玩家模式最小实现）。"""
    result = get_service().chat_with_npc(req.npc, req.message)
    if result is None:
        raise HTTPException(404, f"角色不存在: {req.npc}")
    return result

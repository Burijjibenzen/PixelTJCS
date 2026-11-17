"""全局配置 —— Roamstra 所有可调参数的唯一入口

运行时优先读取项目根目录 .env 中的环境变量，未设置时使用下面的默认值。
改参数只需要动这一个文件（或 .env），不要散落在各模块里。
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 项目根目录: <repo>/backend/config.py 的上一级
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env（key、模型名等敏感/环境相关信息，不入 git）
load_dotenv(os.path.join(BASE_DIR, ".env"))


@dataclass
class LLMConfig:
    """大模型接入配置（同济 AGI 网关，OpenAI 兼容协议）"""

    api_key: str = os.getenv("LLM_API_KEY", "")
    base_url: str = os.getenv("LLM_BASE_URL", "https://agi.tongji.edu.cn/v1")
    # 日常对话模型：Flash 便宜，NPC 互相聊天全用它
    chat_model: str = os.getenv("LLM_CHAT_MODEL", "GLM-5.3-Flash")
    # 深度任务模型：反思压缩、记忆重要性评定、关系变化评估
    think_model: str = os.getenv("LLM_THINK_MODEL", "GLM-5.3")
    # 记忆语义检索的向量模型（GET /v1/models 可查看套餐内全部模型）
    embed_model: str = os.getenv("LLM_EMBED_MODEL", "Qwen3-Embedding")
    # ⚠️ 网关上的 GLM 是"推理模型"（先输出思考过程再给正文），
    # max_tokens 给小了正文会是 null —— 必须留足思考空间
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "1024"))
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.8"))


@dataclass
class MemoryConfig:
    """记忆系统参数（"遗忘可视化"演示的核心旋钮）"""

    db_path: str = os.path.join(BASE_DIR, "data", "memory.db")

    # ---- 遗忘机制 1: 容量淘汰 ----
    capacity: int = 60  # 单个 NPC 活跃记忆上限，超出淘汰强度最低者
    # ---- 遗忘机制 2: 时间衰减 ----
    half_life_minutes: float = 300.0  # 新近度半衰期（游戏分钟，300 = 5 游戏小时）
    forget_threshold: float = 0.30  # 强度低于此值 → 进入"遗忘档案"
    #  注: 强度 = 0.6·新近度 + 0.4·重要性。3 天后新近度≈0，
    #  重要性 0.75 以上的记忆才守得住 0.30 阈值——"要紧事忘得慢"符合直觉
    # ---- 检索打分权重 ----
    # score = w_recency * 新近度 + w_importance * 重要性 + w_relevance * 相关度
    w_recency: float = 0.20
    w_importance: float = 0.30
    w_relevance: float = 0.50
    retrieval_top_k: int = 6  # 每次回忆最多取几条
    # ---- 遗忘机制 3: 反思压缩 ----
    reflect_every: int = 4  # 每累计 4 条未反思的新记忆触发一次反思


@dataclass
class WorldConfig:
    """世界与时间参数"""

    start_minutes: float = 8 * 60  # 开局: 第 1 天 08:00
    minutes_per_day: float = 24 * 60
    # 一场对话最多几个回合（1 回合 = 双方各说一句）；M1 固定轮次，后续可加"是否继续"判断
    max_rounds: int = 2
    # 同一对 NPC 的对话冷却（游戏分钟），防止无限连聊
    cooldown_minutes: float = 120.0


# 全局单例，其他模块统一 `from backend.config import LLM, MEMORY, WORLD` 引用
LLM = LLMConfig()
MEMORY = MemoryConfig()
WORLD = WorldConfig()


@dataclass
class SocialConfig:
    """M3: 文档中的世界循环/关系加权/事件成本参数集中于配置层。"""
    tick_seconds: float = 0.5
    speed: float = 10.0  # 恢复原版后端：1 现实秒 = 10 游戏分钟。
    fast_forward_speed: float = 60.0  # 定时快进：3 游戏天连续运行约 72 现实秒。
    decision_interval: float = 15.0
    base_chat_probability: float = 0.20
    max_conversations: int = 2
    presence_seconds: float = 15.0
    encounter_radius: float = 96.0  # 人物脚下坐标的地图像素距离，约6个16px地砖。
    position_report_seconds: float = 0.25
    position_max_age_seconds: float = 2.0  # 心跳不能延长坐标有效期。
    meeting_timeout: float = 300.0
    meeting_wait_timeout: float = 300.0
    maintenance_minutes: float = 30.0
    bubble_seconds: float = 10.0  # 恢复原版：新台词立即替换，每段最多显示 10 现实秒。
    bubble_segment_chars: int = 60
    bubble_exit_hold_seconds: float = 3.0  # 最后一句后至多停留片刻；完整台词在日志里。
    visit_relation: float = 80.0
    gossip_max_hops: int = 3
    websocket_queue: int = 256
    event_history_limit: int = 5000
    memory_history_limit: int = 200

SOCIAL = SocialConfig()


@dataclass
class ActivityConfig:
    """动态活动参数；时间点/停留用游戏分钟，行走预算用原速下的现实秒。"""
    enabled: bool = True
    daily_optional_count: int = 2
    dwell_minutes: float = 180.0
    min_stay_seconds: float = 12.0
    travel_timeout_seconds: float = 300.0
    pending_expiry_minutes: float = 720.0
    appointment_lead_minutes: float = 180.0
    context_scan_seconds: float = 5.0
    memory_reactions_per_day: int = 1
    event_reactions_per_day: int = 4
    friend_threshold: float = 70.0
    model_enabled: bool = True
    model_interval_seconds: float = 8.0
    model_timeout_seconds: float = 15.0
    model_result_deadline_seconds: float = 20.0
    history_days: int = 3


ACTIVITY = ActivityConfig()


@dataclass
class NoticeBoardConfig:
    read_radius: float = 64.0
    discovery_radius: float = 192.0
    approach_timeout_seconds: float = 30.0
    read_seconds: float = 2.5  # 现实秒，快进不省略阅读过程。
    reader_cooldown_seconds: float = 10.0
    max_readers: int = 3
    max_notices_per_board: int = 100
    importance: float = 0.45
    retry_seconds: float = 30.0
    queue_timeout_seconds: float = 300.0


NOTICEBOARD = NoticeBoardConfig()


@dataclass
class PlayerConfig:
    name_max_chars: int = 24
    message_max_chars: int = 500
    context_turns: int = 6
    history_limit: int = 50
    memory_importance: float = 0.45
    relation_max_delta: float = 6.0
    engage_lease_seconds: float = 45.0
    chat_radius: float = 48.0


PLAYER = PlayerConfig()

"""自研记忆服务 —— 本系统最重要的模块（课程核心卖点：遗忘可视化）

为什么不直接用框架内置记忆？—— 遗忘的内部过程（衰减公式、淘汰、压缩）
正是我们要"展示"的东西，黑盒组件无法演示。本模块全部白盒实现。

一条记忆的生命周期:
    写入(算向量) → 检索(三因子打分) → 遗忘(三机制) → 反思(压缩淡出)

┌─ 检索打分公式（Generative Agents 风格）──────────────────────┐
│ score = w_recency·新近度 + w_importance·重要性 + w_relevance·相关度 │
│ 新近度 = 0.5^(Δ游戏分钟 / 半衰期)   —— 指数衰减                    │
│ 相关度 = cosine(查询向量, 记忆向量)  —— Qwen3-Embedding             │
└──────────────────────────────────────────────────────────┘

遗忘三机制（全部留痕，不物理删除，供前端"遗忘档案"展示）:
  1) 时间衰减: 强度 = 0.6·新近度 + 0.4·重要性，低于阈值 → forgotten
  2) 容量淘汰: 活跃记忆超过 capacity → 淘汰强度最低者
  3) 反思压缩: 每 N 条新记忆，LLM 压缩成一条"洞察"，原始记忆 importance
     减半并打 reflected 标记（淡出，而非直接删除）
"""

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.config import MEMORY
from backend.llm import LLMClient
from backend.logger import get_logger

log = get_logger(__name__)


# ----------------------------------------------------------------------
# 记忆数据结构
# ----------------------------------------------------------------------
@dataclass
class MemoryItem:
    """一条记忆。字段与 SQLite 表 memories 一一对应。"""

    id: str
    agent: str  # 属于哪个角色
    content: str  # 记忆内容（第一人称视角）
    type: str  # dialogue / observation / event / reflection
    importance: float  # 重要性 0~1（LLM 评定）
    emotion: str  # 情绪标签
    game_time: float  # 发生时刻（游戏分钟）
    location: str  # 发生地点（区域 id）
    participants: List[str] = field(default_factory=list)  # 涉及的其他角色
    embedding: Optional[List[float]] = None  # 内容向量（语义检索用）
    tags: List[str] = field(default_factory=list)  # 附加标记: reflected / gossip...
    forgotten: bool = False  # 是否已遗忘
    forgotten_at: float = 0.0  # 遗忘时刻（游戏分钟）
    forget_reason: str = ""  # 遗忘原因: strength_decay / capacity_evict
    reflected: bool = False  # 是否已被反思压缩过（淡出标记）

    def to_dict(self) -> dict:
        """转 JSON 友好 dict（向量不序列化，体积太大）。"""
        return {
            "id": self.id, "agent": self.agent, "content": self.content,
            "type": self.type, "importance": round(self.importance, 3),
            "emotion": self.emotion, "game_time": self.game_time,
            "location": self.location, "participants": self.participants,
            "tags": self.tags, "forgotten": self.forgotten,
            "forgotten_at": self.forgotten_at, "forget_reason": self.forget_reason,
            "reflected": self.reflected,
        }


# ----------------------------------------------------------------------
# 记忆仓库
# ----------------------------------------------------------------------
class MemoryStore:
    """SQLite 持久化的记忆仓库（所有角色共用一个库文件，按 agent 列区分）。"""

    # 行 → 对象 的列顺序（SELECT * 用）
    COLUMNS = [
        "id", "agent", "content", "type", "importance", "emotion", "game_time",
        "location", "participants", "embedding", "tags", "forgotten",
        "forgotten_at", "forget_reason", "reflected",
    ]

    def __init__(self, llm: LLMClient, cfg=MEMORY):
        self.llm = llm
        self.cfg = cfg
        import os

        os.makedirs(os.path.dirname(cfg.db_path), exist_ok=True)
        self._init_db()

    # ---------------- 基础设施 ----------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cfg.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                       id TEXT PRIMARY KEY,
                       agent TEXT NOT NULL,
                       content TEXT NOT NULL,
                       type TEXT NOT NULL,
                       importance REAL NOT NULL,
                       emotion TEXT DEFAULT 'neutral',
                       game_time REAL NOT NULL,
                       location TEXT DEFAULT '',
                       participants TEXT DEFAULT '[]',
                       embedding TEXT,
                       tags TEXT DEFAULT '[]',
                       forgotten INTEGER DEFAULT 0,
                       forgotten_at REAL DEFAULT 0,
                       forget_reason TEXT DEFAULT '',
                       reflected INTEGER DEFAULT 0
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_forgotten ON memories(forgotten)"
            )

    def get_by_id(self, memory_id):
        """M3: 原子步骤重试前读取稳定 ID。"""
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
        return self._row_to_item(row) if row else None

    # ---------------- 写入 ----------------
    def add(
        self,
        agent: str,
        content: str,
        type: str,  # noqa: A002 与表字段同名，保持直观
        importance: float,
        game_time: float,
        location: str = "",
        emotion: str = "neutral",
        participants: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        memory_id: Optional[str] = None,  # M3: 检查点恢复使用稳定 ID。
    ) -> MemoryItem:
        """写入一条记忆；自动计算内容向量（失败则 None，检索降级为关键词）。"""
        item = MemoryItem(
            id=memory_id or uuid.uuid4().hex[:12],
            agent=agent,
            content=content,
            type=type,
            importance=max(0.0, min(1.0, importance)),
            emotion=emotion or "neutral",
            game_time=game_time,
            location=location,
            participants=participants or [],
            tags=tags or [],
        )
        vecs = self.llm.embed([content])
        item.embedding = vecs[0] if vecs else None

        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO memories (id, agent, content, type, importance,
                       emotion, game_time, location, participants, embedding,
                       tags, forgotten, forgotten_at, forget_reason, reflected)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,'',0)""",
                (
                    item.id, item.agent, item.content, item.type, item.importance,
                    item.emotion, item.game_time, item.location,
                    json.dumps(item.participants, ensure_ascii=False),
                    json.dumps(item.embedding) if item.embedding else None,
                    json.dumps(item.tags, ensure_ascii=False),
                ),
            )
        log.info("💾 [%s] 写入%s记忆: %s", agent, type, content[:30])
        return item

    # ---------------- 遗忘三机制 ----------------
    def _recency(self, game_time: float, now: float) -> float:
        """新近度: 指数衰减，半衰期可配（时间加速后遗忘效果自然放大）。"""
        delta = max(0.0, now - game_time)
        return 0.5 ** (delta / self.cfg.half_life_minutes)

    def _strength(self, game_time: float, importance: float, now: float) -> float:
        """记忆当前强度 = 0.6·新近度 + 0.4·重要性（与检索公式解耦）。"""
        return 0.6 * self._recency(game_time, now) + 0.4 * importance

    def decay_pass(self, now_minutes: float) -> Dict[str, int]:
        """对全体角色的记忆执行一次"时间衰减检查"。

        强度低于 forget_threshold → 标记遗忘（原因: strength_decay）。
        返回统计 {"forgotten_decay": n}，供 API/演示展示。
        """
        stats = {"forgotten_decay": 0}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, agent, game_time, importance FROM memories "
                "WHERE forgotten = 0"
            ).fetchall()
            for mid, agent, game_time, importance in rows:
                strength = self._strength(game_time, importance, now_minutes)
                if strength < self.cfg.forget_threshold:
                    conn.execute(
                        "UPDATE memories SET forgotten=1, forgotten_at=?, "
                        "forget_reason='strength_decay' WHERE id=?",
                        (now_minutes, mid),
                    )
                    stats["forgotten_decay"] += 1
                    log.info("🫥 [%s] 记忆淡忘(强度%.3f): %s", agent, strength, mid)
        return stats

    def _evict_capacity(self, agent: str, now_minutes: float) -> int:
        """容量淘汰：活跃记忆超过 capacity，强度最低者被挤进遗忘档案。"""
        evicted = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, game_time, importance FROM memories "
                "WHERE agent=? AND forgotten=0",
                (agent,),
            ).fetchall()
            excess = len(rows) - self.cfg.capacity
            if excess <= 0:
                return 0
            scored = sorted(
                rows, key=lambda r: self._strength(r[1], r[2], now_minutes)
            )
            for mid, _, _ in scored[:excess]:
                conn.execute(
                    "UPDATE memories SET forgotten=1, forgotten_at=?, "
                    "forget_reason='capacity_evict' WHERE id=?",
                    (now_minutes, mid),
                )
                evicted += 1
                log.info("🗄️ [%s] 容量淘汰记忆: %s", agent, mid)
        return evicted

    def full_forget_pass(self, now_minutes: float) -> Dict[str, int]:
        """完整遗忘流程 = 衰减检查 + 全员容量淘汰。由世界时钟推进时调用。"""
        stats = self.decay_pass(now_minutes)
        total_evict = sum(
            self._evict_capacity(agent, now_minutes) for agent in self.list_agents()
        )
        stats["forgotten_evict"] = total_evict
        return stats

    # ---------------- 检索 ----------------
    def recall(
        self, agent: str, query: str, now_minutes: float, top_k: Optional[int] = None
    ) -> List[MemoryItem]:
        """回忆：按三因子打分取 top_k 条活跃记忆（已遗忘的不参与）。"""
        top_k = top_k or self.cfg.retrieval_top_k
        items = self._load_active(agent)
        if not items:
            return []
        query_vec = (self.llm.embed([query]) or [None])[0]
        scored = []
        for item in items:
            relevance = self._relevance(query_vec, item, query)
            score = (
                self.cfg.w_recency * self._recency(item.game_time, now_minutes)
                + self.cfg.w_importance * item.importance
                + self.cfg.w_relevance * relevance
            )
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def _relevance(
        self, query_vec: Optional[List[float]], item: MemoryItem, query: str
    ) -> float:
        """相关度: 优先向量余弦；向量不可用时降级为字符二元组重叠。"""
        if query_vec and item.embedding:
            return self._cosine(query_vec, item.embedding)
        return self._keyword_overlap(query, item.content)

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        """纯 Python 余弦相似度（记忆条目规模小，无需 numpy）。"""
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(y * y for y in b)) or 1e-9
        return max(0.0, dot / (na * nb))

    @staticmethod
    def _keyword_overlap(q: str, content: str) -> float:
        """兜底相关度: 中文 2-gram Jaccard。"""

        def bigrams(s: str) -> set:
            s = "".join(ch for ch in s if ch.isalnum())
            return {s[i: i + 2] for i in range(max(0, len(s) - 1))}

        qb, cb = bigrams(q), bigrams(content)
        if not qb or not cb:
            return 0.0
        return len(qb & cb) / len(qb | cb)

    # ---------------- 反思（压缩） ----------------
    def count_unreflected(self, agent: str) -> int:
        """统计尚未被反思覆盖的原始记忆条数（触发反思的依据）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE agent=? AND forgotten=0 AND reflected=0 AND type != 'reflection'",
                (agent,),
            ).fetchone()
            return row[0]

    def get_unreflected(self, agent: str, limit: int = 20) -> List[MemoryItem]:
        """取一批待反思的原始记忆（供 LLM 压缩）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories "
                "WHERE agent=? AND forgotten=0 AND reflected=0 AND type != 'reflection' "
                "ORDER BY game_time DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def mark_reflected(self, agent: str, ids: List[str]) -> None:
        """把一批原始记忆标记为"已被反思"（importance 减半 → 自然淡出）。"""
        with self._connect() as conn:
            for mid in ids:
                conn.execute(
                    "UPDATE memories SET reflected=1, "
                    "importance = importance * 0.5 WHERE id=? AND agent=?",
                    (mid, agent),
                )

    # ---------------- 查询（API / 前端面板用） ----------------
    def list_agents(self) -> List[str]:
        with self._connect() as conn:
            return [
                r[0]
                for r in conn.execute("SELECT DISTINCT agent FROM memories").fetchall()
            ]

    def get_active(self, agent: str, limit: int = 20) -> List[MemoryItem]:
        return self._query(agent, forgotten=False, limit=limit)

    def get_forgotten(self, agent: str, limit: int = 20) -> List[MemoryItem]:
        return self._query(agent, forgotten=True, limit=limit)

    def get_reflections(self, agent: str, limit: int = 10) -> List[MemoryItem]:
        """洞察记忆（按时间倒序的最新若干条）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE agent=? AND forgotten=0 AND type='reflection' "
                "ORDER BY game_time DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def _query(self, agent: str, forgotten: bool, limit: int) -> List[MemoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE agent=? AND forgotten=? "
                "ORDER BY game_time DESC LIMIT ?",
                (agent, int(forgotten), limit),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    # ---------------- 工具 ----------------
    def _row_to_item(self, r) -> MemoryItem:
        d = dict(zip(self.COLUMNS, r))
        return MemoryItem(
            id=d["id"], agent=d["agent"], content=d["content"], type=d["type"],
            importance=d["importance"], emotion=d["emotion"], game_time=d["game_time"],
            location=d["location"],
            participants=json.loads(d["participants"] or "[]"),
            embedding=json.loads(d["embedding"]) if d["embedding"] else None,
            tags=json.loads(d["tags"] or "[]"),
            forgotten=bool(d["forgotten"]), forgotten_at=d["forgotten_at"],
            forget_reason=d["forget_reason"], reflected=bool(d["reflected"]),
        )

    def _load_active(self, agent: str) -> List[MemoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE agent=? AND forgotten=0", (agent,)
            ).fetchall()
        return [self._row_to_item(r) for r in rows]




"""关系矩阵 —— NPC↔NPC（及 NPC↔玩家）的好感度网络

数据: relations[a][b] ∈ [0,100]，对称存储（M1 简化；真实的单方向好感
留作扩展点，见 CODE_GUIDE §9）。

更新方式: 每场对话结束后由 LLM 评估谈话对双方关系的影响（delta ∈ [-6,+6]），
带原因留痕（last_reason），供前端关系面板展示"为什么变了"。
"""

import json
import sqlite3
from typing import Dict, Optional, Tuple

from backend.config import MEMORY
from backend.logger import get_logger

log = get_logger(__name__)

DEFAULT_SCORE = 50.0  # 初识好感度


class RelationGraph:
    """持久化的对称关系矩阵（SQLite 存储）。"""

    def __init__(self, db_path: str = MEMORY.db_path):
        self.db_path = db_path
        # 内存缓存，写穿到 SQLite
        self._cache: Dict[Tuple[str, str], float] = {}
        self._reason: Dict[Tuple[str, str], str] = {}
        self._init_db()
        self._load_all()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS relations (
                       a TEXT NOT NULL,
                       b TEXT NOT NULL,
                       score REAL NOT NULL,
                       last_reason TEXT DEFAULT '',
                       PRIMARY KEY (a, b)
                   )"""
            )
            conn.execute('CREATE TABLE IF NOT EXISTS relation_effects(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def _load_all(self) -> None:
        with self._connect() as conn:
            for a, b, score, reason in conn.execute(
                "SELECT a, b, score, last_reason FROM relations"
            ):
                self._cache[(a, b)] = score
                self._reason[(a, b)] = reason

    # ------------------------------------------------------------------
    @staticmethod
    def _key(a: str, b: str) -> Tuple[str, str]:
        """对称键：把 (a,b)/(b,a) 归一化到同一存储键。"""
        return (a, b) if a <= b else (b, a)

    def get(self, a: str, b: str) -> float:
        """查询好感度；陌生人默认 50。"""
        return self._cache.get(self._key(a, b), DEFAULT_SCORE)

    def level(self, score: float) -> str:
        """好感度 → 关系等级文案（前端展示用）。"""
        if score >= 80:
            return "莫逆之交"
        if score >= 65:
            return "好朋友"
        if score >= 45:
            return "普通同学"
        if score >= 25:
            return "有点尴尬"
        return "互相看不顺眼"

    def set_initial(self, a: str, b: str, score: float) -> None:
        """写入初始关系（仅当尚无记录时生效，不覆盖运行中数据）。"""
        key = self._key(a, b)
        if key not in self._cache:
            self.apply_delta(a, b, score - DEFAULT_SCORE, "初识印象")

    def apply_delta(self, a: str, b: str, delta: float, reason: str) -> float:
        """关系变化 ±delta（带原因留痕），裁剪到 [0,100] 并持久化。"""
        key = self._key(a, b)
        old = self._cache.get(key, DEFAULT_SCORE)
        new = max(0.0, min(100.0, old + delta))
        self._cache[key] = new
        self._reason[key] = reason
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO relations (a, b, score, last_reason)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(a, b) DO UPDATE
                   SET score = excluded.score, last_reason = excluded.last_reason""",
                (key[0], key[1], new, reason),
            )
        log.info("关系变化: %s ↔ %s %.0f → %.0f（%s）", a, b, old, new, reason)
        return new

    # ------------------------------------------------------------------
    def all_for(self, name: str) -> Dict[str, float]:
        """某人与所有人的关系 → {对方: 分数}（前端个人面板用）。"""
        return {
            other: score
            for (x, y), score in self._cache.items()
            if name in (x, y)
            for other in [(y if x == name else x)]
        }

    def apply_delta_once(self, a: str, b: str, delta: float, reason: str, effect_id: str) -> dict:
        """单轮玩家对话的关系与幂等标记在同一事务提交。"""
        key = self._key(a, b)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT payload FROM relation_effects WHERE id=?', (effect_id,)).fetchone()
            if previous:
                result = json.loads(previous[0])
            else:
                row = conn.execute('SELECT score FROM relations WHERE a=? AND b=?', key).fetchone()
                old = row[0] if row else DEFAULT_SCORE
                score = max(0.0, min(100.0, old + delta))
                result = dict(score=score, delta=score-old, reason=reason)
                conn.execute('INSERT OR REPLACE INTO relations VALUES(?,?,?,?)', (*key, score, reason))
                conn.execute('INSERT INTO relation_effects VALUES(?,?)', (effect_id, json.dumps(result, ensure_ascii=False)))
        self._cache[key], self._reason[key] = result['score'], result['reason']
        return result

    def matrix(self) -> Dict[str, Dict[str, Optional[float]]]:
        """全量矩阵（缺省项为 None 表示"陌生人"），供 /relations 接口。"""
        names = set()
        for a, b in self._cache:
            names.update([a, b])
        return {
            n: {
                m: (self._cache.get(self._key(n, m)) if m != n else None)
                for m in sorted(names)
            }
            for n in sorted(names)
        }

    def last_reason(self, a: str, b: str) -> str:
        return self._reason.get(self._key(a, b), "")

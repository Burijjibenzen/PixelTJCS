"""Back up SQLite and replace saved player data with a clean demo character."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import uuid

from backend.config import MEMORY
from backend.player_mode import DEMO_PLAYER_NAME


def require_no_active_player(db):
    if db.execute("SELECT 1 FROM player_sessions WHERE state='active' LIMIT 1").fetchone():
        raise RuntimeError("仍有活跃玩家会话。请先正常停止 PixelTJCS 后端。")


def reset_demo_player(database: Path) -> Path:
    if not database.is_file():
        raise FileNotFoundError(database)
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=1)) as db:
        # In WAL mode the first read acquires and holds the exclusive file lock.
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        try:
            require_no_active_player(db)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                raise RuntimeError("数据库正在使用。请先正常停止 PixelTJCS 后端。") from error
            raise
        with tempfile.NamedTemporaryFile(
            dir=database.parent, prefix="before-demo-player-reset-", suffix=".sqlite3", delete=False,
        ) as backup_file:
            backup = Path(backup_file.name)
        with closing(sqlite3.connect(backup)) as copy:
            db.backup(copy)
        print(f"备份已保存到 {backup}", flush=True)

        with db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            require_no_active_player(db)
            db.execute("CREATE TEMP TABLE old_profiles AS SELECT id FROM player_profiles")
            db.execute("CREATE TEMP TABLE old_turns AS SELECT id FROM player_turns")
            db.execute("""CREATE TEMP TABLE old_events AS SELECT id FROM game_events
                WHERE kind='player_conversation' OR actor IN (SELECT id FROM old_profiles)
                   OR target_id IN (SELECT id FROM old_profiles)""")
            db.execute("""INSERT INTO old_events SELECT id FROM game_events
                WHERE kind='conversation_overheard' AND source_event_id IN (SELECT id FROM old_events)""")
            db.execute("""CREATE TEMP TABLE old_memories AS SELECT id FROM memories
                WHERE agent IN (SELECT id FROM old_profiles)
                   OR EXISTS (SELECT 1 FROM json_each(memories.participants)
                       WHERE value IN (SELECT id FROM old_profiles))
                   OR id LIKE 'player-chat:%'
                   OR id IN (SELECT memory_id FROM event_knowledge WHERE event_id IN (SELECT id FROM old_events))""")

            db.execute("""DELETE FROM m3_events
                WHERE json_extract(payload,'$.activity')='player_chat'
                   OR json_extract(payload,'$.conversation_id') IN (SELECT id FROM old_turns)
                   OR json_extract(payload,'$.game_event_id') IN (SELECT id FROM old_events)
                   OR json_extract(payload,'$.a') IN (SELECT id FROM old_profiles)
                   OR json_extract(payload,'$.b') IN (SELECT id FROM old_profiles)
                   OR (kind='world' AND EXISTS (SELECT 1 FROM json_tree(m3_events.payload)
                       WHERE key IN ('id','target_id') AND value IN (SELECT id FROM old_events)))""")
            db.execute("""DELETE FROM m3_memory_history
                WHERE EXISTS (SELECT 1 FROM json_tree(m3_memory_history.payload)
                    WHERE key='id' AND value IN (SELECT id FROM old_memories))""")
            db.execute("DELETE FROM gossip WHERE memory_id IN (SELECT id FROM old_memories)")
            db.execute("DELETE FROM memories WHERE id IN (SELECT id FROM old_memories)")
            for table in ("relations", "social_changes"):
                db.execute(f"DELETE FROM {table} WHERE a IN (SELECT id FROM old_profiles) OR b IN (SELECT id FROM old_profiles)")
            db.execute("DELETE FROM relation_effects WHERE id IN (SELECT id FROM old_turns)")
            for table in ("event_reactions", "event_assessments", "event_knowledge", "npc_event_spans", "npc_event_focus"):
                db.execute(f"DELETE FROM {table} WHERE event_id IN (SELECT id FROM old_events) OR actor IN (SELECT id FROM old_profiles)")
            db.execute("UPDATE event_knowledge SET memory_id='' WHERE memory_id IN (SELECT id FROM old_memories)")
            db.execute("""UPDATE npc_event_focus SET suspended=(SELECT json_group_array(value)
                FROM json_each(npc_event_focus.suspended) WHERE value NOT IN (SELECT id FROM old_events))""")
            db.execute("UPDATE game_events SET source_event_id=NULL WHERE source_event_id IN (SELECT id FROM old_events)")
            db.execute("DELETE FROM game_events WHERE id IN (SELECT id FROM old_events)")
            for table in ("player_turns", "player_sessions", "player_profiles"):
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT INTO player_profiles(id,name,token) VALUES(?,?,NULL)",
                       ("player:" + uuid.uuid4().hex, DEMO_PLAYER_NAME))
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(MEMORY.db_path))
    args = parser.parse_args()
    try:
        reset_demo_player(args.db.resolve())
    except (OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f"未完成玩家数据重置：{error}\n")
    print(f"旧玩家数据已清理。当前只有角色「{DEMO_PLAYER_NAME}」。")


if __name__ == "__main__":
    main()

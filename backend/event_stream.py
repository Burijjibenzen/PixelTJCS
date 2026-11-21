"""M3: 持久事件总线；工作线程发布，async WebSocket 订阅，慢客户端独立断开。"""
import asyncio
import json
import sqlite3
import threading
import time
from contextlib import closing


class EventStream:
    def __init__(self, db_path, queue_size=256, history_limit=5000):
        self.db_path, self.queue_size = db_path, queue_size
        self.history_limit = history_limit
        self.lock = threading.RLock()
        self.clients = {}
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS m3_events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT, created REAL, event_key TEXT UNIQUE)")
            self._prune(db)
            db.commit()

    def _prune(self, db):
        db.execute('DELETE FROM m3_events WHERE id <= (SELECT COALESCE(MAX(id),0)-? FROM m3_events)',
                   (self.history_limit,))

    def db(self):
        return closing(sqlite3.connect(self.db_path, timeout=10))

    def publish(self, kind, event_key=None, **payload):
        with self.lock, self.db() as db:
            if event_key:
                row = db.execute("SELECT id,kind,payload,created FROM m3_events WHERE event_key=?", (event_key,)).fetchone()
                if row:
                    return self.decode(row)
            created = time.time()
            cursor = db.execute("INSERT INTO m3_events(kind,payload,created,event_key) VALUES(?,?,?,?)",
                                (kind, json.dumps(payload, ensure_ascii=False), created, event_key))
            if cursor.lastrowid % 100 == 0:
                self._prune(db)
            db.commit()
            event = dict(id=cursor.lastrowid, kind=kind, created=created, **payload)
            for queue, loop in list(self.clients.items()):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self.deliver, queue, event)
            return event

    @staticmethod
    def decode(row):
        return dict(id=row[0], kind=row[1], created=row[3], **json.loads(row[2]))

    @staticmethod
    def deliver(queue, event):
        if queue.full():
            # 不阻塞其他观察者或 LLM。断开后客户端用 last_id 补取持久事件。
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({'kind': 'overflow'})
        else:
            queue.put_nowait(event)

    def subscribe(self, after):
        with self.lock, self.db() as db:
            queue = asyncio.Queue(self.queue_size)
            self.clients[queue] = asyncio.get_running_loop()
            latest = db.execute('SELECT COALESCE(MAX(id),0) FROM m3_events').fetchone()[0]
            # 首连只恢复活跃对话；重连补最近窗口，完整历史通过查询接口回放。
            replay = [] if after is None else [self.decode(r) for r in db.execute(
                'SELECT id,kind,payload,created FROM m3_events WHERE id>? ORDER BY id LIMIT 512', (after,))]
            return queue, latest, replay

    def unsubscribe(self, queue):
        with self.lock:
            self.clients.pop(queue, None)

    def notify_world_reset(self,snapshot):
        with self.lock:
            for queue,loop in self.clients.items():
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self.deliver,queue,{'kind':'world_reset','world':snapshot})

    def history(self, after=0, limit=200):
        with self.db() as db:
            return [self.decode(r) for r in db.execute(
                'SELECT id,kind,payload,created FROM m3_events WHERE id>? ORDER BY id LIMIT ?', (after, limit))]

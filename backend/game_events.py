"""游戏事件、角色获知和处理结果独立于供前端同步的短期事件流。"""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class GameEvent:
    id: str
    kind: str
    game_time: float
    actor: str | None
    participants: tuple[str, ...]
    map_id: str
    room_id: str
    target_type: str
    target_id: str
    content: dict
    result: dict
    source_event_id: str | None = None


class GameEvents:
    def __init__(self, database):
        self.database = database
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS game_events (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, game_time REAL NOT NULL,
                    actor TEXT, participants TEXT NOT NULL, map_id TEXT NOT NULL, room_id TEXT NOT NULL,
                    target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                    source_event_id TEXT REFERENCES game_events(id),
                    content TEXT NOT NULL, result TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_knowledge (
                    actor TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES game_events(id),
                    learned_at REAL NOT NULL, memory_id TEXT NOT NULL,
                    PRIMARY KEY(actor, event_id)
                );
                CREATE INDEX IF NOT EXISTS event_knowledge_timeline
                    ON event_knowledge(actor, learned_at DESC, event_id DESC);
                CREATE TABLE IF NOT EXISTS event_reactions (
                    actor TEXT NOT NULL, event_id TEXT NOT NULL, decided_at REAL,
                    action TEXT NOT NULL, reason TEXT NOT NULL, activity_ids TEXT NOT NULL,
                    PRIMARY KEY(actor, event_id),
                    FOREIGN KEY(actor, event_id) REFERENCES event_knowledge(actor, event_id)
                );
                CREATE TABLE IF NOT EXISTS npc_event_focus (
                    actor TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES game_events(id),
                    suspended TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS npc_event_spans (
                    actor TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES game_events(id),
                    started_at REAL NOT NULL, ended_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS npc_open_event_span
                    ON npc_event_spans(actor) WHERE ended_at IS NULL;
                CREATE TABLE IF NOT EXISTS event_assessments (
                    event_id TEXT NOT NULL REFERENCES game_events(id), actor TEXT NOT NULL,
                    mode TEXT NOT NULL, status TEXT NOT NULL, requested_at REAL NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(event_id,actor)
                );
                CREATE INDEX IF NOT EXISTS event_assessments_pending
                    ON event_assessments(requested_at) WHERE status='pending';
            """)
            columns = {row[1] for row in db.execute('PRAGMA table_info(game_events)')}
            if 'phase' not in columns:
                db.execute("ALTER TABLE game_events ADD COLUMN phase TEXT NOT NULL DEFAULT 'finished'")
            if 'ended_at' not in columns:
                db.execute('ALTER TABLE game_events ADD COLUMN ended_at REAL')
                db.execute('UPDATE game_events SET ended_at=game_time')

    def record(self, db, event: GameEvent) -> bool:
        inserted = db.execute("""
            INSERT INTO game_events (id,kind,game_time,actor,participants,map_id,room_id,
                target_type,target_id,source_event_id,content,result,phase,ended_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO NOTHING
        """, (event.id, event.kind, event.game_time, event.actor,
              json.dumps(event.participants, ensure_ascii=False), event.map_id, event.room_id,
              event.target_type, event.target_id, event.source_event_id,
              json.dumps(event.content, ensure_ascii=False),
              json.dumps(dict(event.result, id=event.id + ':result'), ensure_ascii=False),
              'finished',event.game_time))
        return inserted.rowcount > 0

    def begin(self, db, event, phase):
        if not self.record(db,event):
            return False
        db.execute('UPDATE game_events SET phase=?,ended_at=NULL,result=? WHERE id=?',
                   (phase,'{}',event.id))
        return True

    def conclude(self, db, event, ended_at):
        """既有事件保留原开始时间和 ID；结果只写入一次。"""
        result = json.dumps(dict(event.result,id=event.id+':result'),ensure_ascii=False)
        return bool(db.execute('''UPDATE game_events SET kind=?,participants=?,map_id=?,room_id=?,
            content=?,result=?,source_event_id=COALESCE(source_event_id,?),phase='finished',ended_at=?
            WHERE id=? AND ended_at IS NULL''',
            (event.kind,json.dumps(event.participants,ensure_ascii=False),event.map_id,event.room_id,
             json.dumps(event.content,ensure_ascii=False),result,event.source_event_id,ended_at,event.id)).rowcount)

    def focus(self, db, actor):
        row = db.execute('SELECT event_id,suspended FROM npc_event_focus WHERE actor=?',(actor,)).fetchone()
        return (row[0],json.loads(row[1])) if row else (None,[])

    def set_focus(self, db, actor, event_id, suspended, at):
        db.execute('UPDATE npc_event_spans SET ended_at=? WHERE actor=? AND ended_at IS NULL',(at,actor))
        db.execute('INSERT INTO npc_event_spans VALUES (?,?,?,NULL)',(actor,event_id,at))
        db.execute('''INSERT INTO npc_event_focus VALUES (?,?,?) ON CONFLICT(actor) DO UPDATE SET
                      event_id=excluded.event_id,suspended=excluded.suspended''',
                   (actor,event_id,json.dumps(suspended)))

    def current(self):
        with self.database() as db:
            rows = db.execute('''SELECT f.actor,e.id,e.kind,e.phase,e.game_time,e.target_id,e.content,e.participants,e.actor
                FROM npc_event_focus f JOIN game_events e ON e.id=f.event_id''').fetchall()
        current={}
        for actor,identity,kind,phase,at,target,content,participants,initiator in rows:
            data=self.visible_content(json.loads(content),kind,actor,initiator)
            notes=data.get('schedule_notes',[])
            current[actor]=dict(id=identity,kind=kind,phase=phase,game_time=at,target_id=target,
                                title=data.get('title',''),participants=json.loads(participants),
                                tier=data.get('tier'),
                                schedule_reason=notes[-1]['text'] if notes else data.get('decision_reason',''))
        return current

    def write_assessment(self,db,event_id,actor,status,payload):
        raw = db.execute('SELECT result FROM game_events WHERE id=?',(event_id,)).fetchone()[0]
        result = json.loads(raw)
        result.setdefault('perspectives',{})[actor] = dict(payload,status=status)
        db.execute('UPDATE game_events SET result=? WHERE id=?',
                   (json.dumps(result,ensure_ascii=False),event_id))
        db.execute("UPDATE event_assessments SET status=?,payload='{}' WHERE event_id=? AND actor=?",
                   (status,event_id,actor))

    @staticmethod
    def public_consent(result,actor):
        consent=result.get('consent')
        if consent and consent.get('decision_by',actor)!=actor:
            result['consent']={key:value for key,value in consent.items() if key not in ('reason','requested_decision')}

    @staticmethod
    def visible_content(content,kind,actor,initiator):
        if 'schedule_notes' in content:
            content['schedule_notes']=[note for note in content['schedule_notes'] if note['actor']==actor]
        if kind in ('conversation','conversation_completed') and actor!=initiator:
            content.pop('purpose',None)
            content.pop('trigger',None)
            content.pop('decision_reason',None)
            content['title']=f'收到{initiator}的邀约'
        return content

    def learn(self, db, actor, event_id, at, memory_id):
        db.execute("""
            INSERT INTO event_knowledge VALUES (?,?,?,?)
            ON CONFLICT(actor, event_id) DO NOTHING
        """, (actor, event_id, at, memory_id))

    def record_reaction(self, db, actor, event_id, at, decision):
        db.execute("""
            INSERT INTO event_reactions VALUES (?,?,?,?,?,?)
            ON CONFLICT(actor, event_id) DO NOTHING
        """, (actor, event_id, at, decision['action'], decision['reason'], json.dumps(decision['activity_ids'])))

    def known_events(self, actor, since, limit=30):
        with self.database() as db:
            rows = db.execute("""
                SELECT k.event_id,k.learned_at,e.kind,CASE
                    WHEN m.content IS NULL THEN json_extract(p.value,'$.outcome')
                    WHEN json_extract(p.value,'$.outcome') IS NULL THEN m.content
                    ELSE m.content || char(10) || '本人的判断：' || json_extract(p.value,'$.outcome')
                END FROM event_knowledge k
                JOIN game_events e ON e.id=k.event_id
                LEFT JOIN memories m ON m.id=k.memory_id AND m.agent=k.actor AND m.forgotten=0
                LEFT JOIN json_each(e.result,'$.perspectives') p ON p.key=k.actor
                WHERE k.actor=? AND k.learned_at>=? AND (m.id IS NOT NULL OR
                    (k.memory_id='' AND json_extract(p.value,'$.outcome') IS NOT NULL))
                ORDER BY k.learned_at DESC,k.event_id DESC LIMIT ?
            """, (actor, since, limit)).fetchall()
        return [dict(event_id=event_id, game_time=at, kind=kind, text=text)
                for event_id, at, kind, text in rows]

    def timeline(self, actor, cursor=None, limit=30):
        with self.database() as db:
            before = None
            if cursor is not None:
                before = db.execute('SELECT learned_at,rowid FROM event_knowledge WHERE actor=? AND event_id=?',
                                    (actor, cursor)).fetchone()
                if before is None:
                    raise ValueError('时间线分页位置无效')
            where = 'AND (k.learned_at,k.rowid) < (?,?)' if before is not None else ''
            args = (actor, *before, limit + 1) if before is not None else (actor, limit + 1)
            rows = db.execute(f"""
                SELECT e.id,e.kind,e.game_time,e.actor,e.participants,e.map_id,e.room_id,e.target_type,e.target_id,e.source_event_id,
                       e.content,e.result,p.content,k.learned_at,r.decided_at,r.action,r.reason,r.activity_ids,
                       e.ended_at,a.status,e.phase
                FROM event_knowledge k JOIN game_events e ON e.id=k.event_id
                LEFT JOIN game_events p ON p.id=e.source_event_id AND (
                    p.kind='notice_published' OR EXISTS (
                        SELECT 1 FROM event_knowledge visible WHERE visible.actor=k.actor AND visible.event_id=p.id
                    )
                )
                LEFT JOIN event_reactions r ON r.actor=k.actor AND r.event_id=k.event_id
                LEFT JOIN event_assessments a ON a.actor=k.actor AND a.event_id=e.id
                WHERE k.actor=? {where}
                ORDER BY k.learned_at DESC,k.rowid DESC LIMIT ?
            """, args).fetchall()
        events = []
        for row in rows[:limit]:
            (event_id, kind, at, initiator, participants, map_id, room_id, target_type, target_id, source_id,
             content, result, source, learned_at, decided_at, action, reason, activity_ids,
             ended_at, assessment_status, phase) = row
            result=json.loads(result)
            assessment=result.pop('perspectives',{}).get(actor)
            self.public_consent(result,actor)
            if assessment is None and assessment_status:
                assessment={'status':assessment_status}
            source_content=json.loads(source) if source else None
            if kind=='notice_read' and source_content:
                source_content.pop('title',None)
            events.append(dict(
                id=event_id, kind=kind, game_time=at, actor=initiator, participants=json.loads(participants),
                learned_at=learned_at, ended_at=ended_at, phase=phase,
                location={'map_id': map_id, 'room_id': room_id},
                target={'type': target_type, 'id': target_id}, source_event_id=source_id,
                content=self.visible_content(json.loads(content),kind,actor,initiator), result=result, assessment=assessment,
                source=source_content,
                reaction=dict(action=action, reason=reason, game_time=decided_at,
                              activity_ids=json.loads(activity_ids)) if action else None))
        return dict(actor=actor, events=events, next_cursor=events[-1]['id'] if len(rows) > limit else None)

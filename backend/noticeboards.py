"""公开发布不等于角色知情；位置合法且完成阅读后才写记忆。"""
import json
import math
import time
import uuid
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from backend.config import NOTICEBOARD, SOCIAL
from backend.game_events import GameEvent
from backend.world import AREAS, CastChanged, FRONTEND_DIR, J3, J4, ZX2


NAV_GRIDS={}


def load_boards():
    result={}
    for map_id in (J3,J4,ZX2):
        directory=FRONTEND_DIR/'assets/map'/map_id
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8-sig'))
        grid=json.loads((directory/'grid.json').read_text(encoding='utf-8-sig'))
        NAV_GRIDS[map_id]=(grid,{r['id']:grid['room_grid'][r['spawn'][1]][r['spawn'][0]] for r in manifest['rooms']})
        for obj in manifest['objects']:
            if obj['type']!='notice':continue
            key=f"{map_id}:{obj['id']}"
            result[key]=dict(id=key,object_id=obj['id'],name=obj['name'],map_id=map_id,room_id=obj['room'],
                area=f"{map_id}/{obj['room']}",location=AREAS[f"{map_id}/{obj['room']}"]['name'],
                x=(obj['cell'][0]+grid['origin'][0]+.5)*manifest['tile_size'],
                y=(obj['cell'][1]+grid['origin'][1]+.5)*manifest['tile_size'])
    return result


BOARDS=load_boards()


class NoticeBoards:
    def __init__(self,service):
        self.s=service
        self.sessions={};self.requests={};self.approaches={};self.cooldown={};self.jobs={};self.next_retry=0
        self.suspended={}
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='notice-memory')
        with service.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS board_notices(id TEXT PRIMARY KEY,board TEXT,content TEXT,published_at REAL);
                CREATE TABLE IF NOT EXISTS board_reads(npc TEXT,notice TEXT,read_at REAL,status TEXT,PRIMARY KEY(npc,notice));
                CREATE TABLE IF NOT EXISTS board_read_events(npc TEXT,notice TEXT,event_id TEXT NOT NULL,PRIMARY KEY(npc,notice));
            ''')
            if 'deleted' not in {row[1] for row in db.execute('PRAGMA table_info(board_notices)')}:
                db.execute('ALTER TABLE board_notices ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

    def _publication_event(self, notice, board):
        return GameEvent(id='notice:'+notice['id'],kind='notice_published',
            game_time=notice['published_at'],actor=None,participants=(),
            map_id=board['map_id'],room_id=board['room_id'],target_type='object',target_id=board['id'],
            content={'text':notice['content']},result={'kind':'notice_available'})

    def _reading_event(self, name, notice_id, board, at, result):
        return GameEvent(id=f'notice:{notice_id}:{name}',kind='notice_read',
            game_time=at,actor=name,participants=(name,),
            map_id=board['map_id'],room_id=board['room_id'],target_type='object',target_id=board['id'],
            content={'notice_id':notice_id},result=result,source_event_id='notice:'+notice_id)

    def backfill_events(self):
        events=self.s.game_events
        with self.s.database() as db:
            for notice_id, board_id, content, at in db.execute(
                'SELECT id,board,content,published_at FROM board_notices'
            ):
                event=self._publication_event(dict(id=notice_id,content=content,published_at=at),BOARDS[board_id])
                events.record(db,event)
            for name, notice_id, at, board_id in db.execute("""
                SELECT r.npc,r.notice,r.read_at,n.board FROM board_reads r
                JOIN board_notices n ON n.id=r.notice
                LEFT JOIN board_read_events m ON m.npc=r.npc AND m.notice=r.notice
                WHERE r.status='completed'
                    AND NOT EXISTS (SELECT 1 FROM game_events e WHERE e.id=COALESCE(m.event_id,'notice:' || r.notice || ':' || r.npc))
            """):
                event=self._reading_event(name,notice_id,BOARDS[board_id],at,{'kind':'notice_learned'})
                events.record(db,event)
                events.learn(db,name,event.id,at,event.id)
                events.record_reaction(db,name,event.id,None,dict(
                    action='historical',reason='从原有已读记录恢复，历史决定未记录',activity_ids=[]))

    def stop(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.s.reading_waiting.clear()
        self.s.reading.clear()
        self.s.notice_approaching.clear()

    def listing(self,board_id):
        if board_id not in BOARDS:raise KeyError('公告栏不存在')
        with self.s.database() as db:
            rows=db.execute('SELECT id,content,published_at FROM board_notices WHERE board=? AND deleted=0 ORDER BY published_at DESC,rowid DESC',(board_id,)).fetchall()
        readers=[dict(npc=n,state='waiting',detail=self.wait_reason(n)) for n,r in self.requests.items() if r['board_id']==board_id]
        readers += [dict(npc=n,state='reading',detail='正在阅读公告') for n,r in self.sessions.items() if r['board_id']==board_id]
        readers += [dict(npc=n,state='approaching',detail='正在走近公告栏') for n,r in self.approaches.items() if r['board_id']==board_id]
        return dict(board=dict(BOARDS[board_id]),notices=[dict(id=r[0],content=r[1],published_at=r[2]) for r in rows],readers=readers)

    def reading_allowed(self,name):
        planner=self.s.planner
        current=planner.current_item(name)
        return (self.s.onstage(name) and name not in self.suspended and name not in planner.encounter_waits
                and not (current and current['tier']=='high')
                and not any(item['tier']=='high' for item in planner.due(name)))

    def wait_reason(self,name):
        if name in self.s.busy:return '已排队，当前对话结束后阅读'
        if name in self.s.reflecting:return '已排队，整理完思绪后阅读'
        return '已排队，等待空余阅读位置'

    def request_read(self,name,board_id,now=None):
        now=time.monotonic() if now is None else now
        if name not in self.s.npcs or board_id not in BOARDS:raise KeyError('未知角色或公告栏')
        if name in self.sessions:
            self.start_read(name,board_id,now)
            return dict(state='reading',detail='角色正在阅读公告')
        if self.approaches.get(name,{}).get('board_id')==board_id:
            return dict(state='approaching',detail='角色正走向公告栏')
        if self.s.paused:raise ValueError('世界暂停中，继续世界后再阅读')
        # 先检查真实位置，忙碌不能掩盖“角色不在公告栏附近”的实际原因。
        if not self.in_range(name,board_id,now,NOTICEBOARD.discovery_radius):raise ValueError('角色需要实际进入公告栏所在走廊，才能发现公告')
        if not self.reading_allowed(name):raise ValueError('角色有已开始的交互或更优先的活动，稍后可以阅读')
        if not self.unread(name,board_id):raise ValueError('没有尚未阅读的公告')
        created=name not in self.requests
        if created:
            if self.approaches.get(name,{}).get('board_id')!=board_id:
                self._drop_approach(name)
            self.requests[name]=dict(board_id=board_id,requested_at=now)
            self.s.reading_waiting.add(name)
        self._try_requested(name,now)
        if name in self.sessions:return dict(state='reading',detail='角色开始阅读，读完后继续原来的安排')
        if name in self.approaches:return dict(state='approaching',detail='角色正走向公告栏')
        if created:self.s.emit('world',snapshot=self.s.world_snapshot())
        return dict(state='waiting',detail=self.wait_reason(name))

    def _try_requested(self,name,now):
        request=self.requests[name]
        board_id=request['board_id']
        if not self._can_approach(name) or not self._start_or_approach(name,board_id,now):return
        self.requests.pop(name,None);self.s.reading_waiting.discard(name)

    def cancel(self,name,now=None):
        suspended=self.suspended.pop(name,None)
        if suspended and 'event_id' in suspended[2]:
            self.s.event_runtime.end_external(suspended[2]['event_id'],'interrupted','公告阅读中断')
        self._drop_approach(name)
        self.requests.pop(name,None);self.s.reading_waiting.discard(name)
        self._release(name,time.monotonic() if now is None else now)

    def suspend_for_encounter(self,name,now):
        for group,occupied in ((self.sessions,self.s.reading),(self.approaches,self.s.notice_approaching),
                               (self.requests,self.s.reading_waiting)):
            entry=group.pop(name,None)
            if entry is not None:
                occupied.discard(name)
                self.suspended[name]=(group,occupied,entry,now)
                return

    def publish(self,board_id,content):
        if board_id not in BOARDS:raise KeyError('公告栏不存在')
        content=content.strip()
        if not content or len(content)>500:raise ValueError('请填写公告正文，最多500字')
        item=dict(id=uuid.uuid4().hex,content=content,published_at=self.s.world.clock.now)
        with self.s.database() as db:
            count=db.execute('SELECT COUNT(*) FROM board_notices WHERE board=? AND deleted=0',(board_id,)).fetchone()[0]
            if count>=NOTICEBOARD.max_notices_per_board:raise ValueError('此公告栏已达到公告上限')
            db.execute('INSERT INTO board_notices(id,board,content,published_at) VALUES(?,?,?,?)',
                       (item['id'],board_id,content,item['published_at']))
            self.s.store_game_event(db,self._publication_event(item,BOARDS[board_id]),{})
        self.s.emit('notice_published',board_id=board_id,notice_id=item['id'])
        return item

    def delete(self,board_id,notice_id):
        with self.s.database() as db:
            updated=db.execute('UPDATE board_notices SET deleted=1 WHERE board=? AND id=? AND deleted=0',
                               (board_id,notice_id))
            if not updated.rowcount:raise KeyError('公告不存在或已删除')
        for group in (self.approaches,self.sessions):
            for name,entry in list(group.items()):
                if entry['notice_id']==notice_id:
                    self.cancel(name)
        for name,(_,_,entry,_) in list(self.suspended.items()):
            if entry.get('notice_id')==notice_id:
                self.cancel(name)
        self.s.emit('notice_deleted',board_id=board_id,notice_id=notice_id)

    def in_range(self,name,board_id,now,radius):
        board=BOARDS[board_id];sample=self.s.positions.get(name)
        if (not self.s.onstage(name) or not sample or sample['area']!=board['area']
                or AREAS[self.s.world.area_of(name)]['id']!=board['area']
                or not 0<=now-sample['at']<=SOCIAL.position_max_age_seconds
                or math.hypot(sample['x']-board['x'],sample['y']-board['y'])>radius):
            return False
        grid,codes=NAV_GRIDS[board['map_id']]
        x=math.floor(sample['x']/grid['tile_size'])-grid['origin'][0]
        y=math.floor(sample['y']/grid['tile_size'])-grid['origin'][1]
        return (0<=x<grid['size'][0] and 0<=y<grid['size'][1] and grid['obstacle'][y][x]=='0'
                and grid['room_grid'][y][x]==codes[board['room_id']])

    def near(self,name,board_id,now):
        return self.in_range(name,board_id,now,NOTICEBOARD.read_radius)

    def unread(self,name,board_id):
        with self.s.database() as db:
            return [r[0] for r in db.execute('''SELECT n.id FROM board_notices n WHERE n.board=? AND n.deleted=0
                AND NOT EXISTS(SELECT 1 FROM board_reads r WHERE r.npc=? AND r.notice=n.id)
                ORDER BY n.published_at DESC,n.rowid DESC''',(board_id,name))]

    def start_read(self,name,board_id,now=None):
        now=time.monotonic() if now is None else now
        if name not in self.s.npcs or board_id not in BOARDS:raise KeyError('未知角色或公告栏')
        if name in self.sessions:
            if self.sessions[name]['board_id']==board_id:return dict(self.sessions[name])
            raise ValueError('角色正在阅读另一处公告栏')
        if self.s.paused:raise ValueError('世界暂停中，继续世界后再阅读')
        if name in self.s.busy|self.s.reflecting:
            raise ValueError('角色正在处理其它任务，请稍后阅读')
        if not self.reading_allowed(name):raise ValueError('角色有已开始的交互或更优先的活动，稍后可以阅读')
        paused_readers=sum(group is self.sessions for group,_,_,_ in self.suspended.values())
        if len(self.sessions)+paused_readers>=NOTICEBOARD.max_readers:raise ValueError('附近阅读中的角色较多，请稍后再试')
        if not self.near(name,board_id,now) or self.s.positions[name]['travelling']:
            raise ValueError('角色需要在公告栏前实际到达并站定，才能阅读')
        unread=self.unread(name,board_id)
        if not unread:raise ValueError('没有尚未阅读的公告')
        trip=self.approaches.get(name)
        notice_id=trip['notice_id'] if trip else unread[0]
        if trip:
            position=self.s.positions[name]
            if notice_id not in unread or math.hypot(position['x']-trip['x'],position['y']-trip['y'])>4:
                raise ValueError('尚未到达此次阅读的目标站位')
            identity=trip['event_id']
            self._drop_approach(name,complete=True)
            with self.s.database() as db:
                db.execute("""UPDATE game_events SET phase='active',
                    content=json_set(content,'$.reading_started_at',?) WHERE id=?""",
                    (self.s.world.clock.now,identity))
        else:
            identity=self._begin_read_event(name,board_id,notice_id,'active')
        session=dict(board_id=board_id,notice_id=notice_id,event_id=identity,remaining=NOTICEBOARD.read_seconds)
        self.sessions[name]=session;self.s.reading.add(name)
        self.s.self_talk(name,identity,'notice:'+notice_id,'过来看看公告写了什么。')
        self.s.emit('world',snapshot=self.s.world_snapshot())
        return dict(session)

    def approach_point(self,name,board_id,now):
        """从真实采样格做有界BFS，落点须在同区可走地板且位于板前。"""
        board=BOARDS[board_id];p=self.s.positions.get(name)
        if not self.in_range(name,board_id,now,NOTICEBOARD.discovery_radius):return None
        grid,codes=NAV_GRIDS[board['map_id']];tile=grid['tile_size'];origin=grid['origin']
        start=(math.floor(p['x']/tile)-origin[0],math.floor(p['y']/tile)-origin[1])
        code=codes[board['room_id']]
        def walkable(x,y):
            return 0<=x<grid['size'][0] and 0<=y<grid['size'][1] and grid['obstacle'][y][x]=='0' and grid['room_grid'][y][x]==code
        if not walkable(*start):return None
        queue=deque([(*start,0)]);seen={start}
        while queue:
            x,y,steps=queue.popleft()
            px,py=(x+origin[0]+.5)*tile,(y+origin[1]+.5)*tile
            if math.hypot(px-board['x'],py-board['y'])<=NOTICEBOARD.read_radius:return dict(x=px,y=py)
            if steps>=math.ceil(NOTICEBOARD.discovery_radius*2/tile):continue
            for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if (nx,ny) not in seen and walkable(nx,ny):
                    seen.add((nx,ny));queue.append((nx,ny,steps+1))
        return None

    def _begin_read_event(self,name,board_id,notice_id,phase):
        event=self._reading_event(name,notice_id,BOARDS[board_id],self.s.world.clock.now,{})
        with self.s.database() as db:
            if db.execute('SELECT 1 FROM game_events WHERE id=?',(event.id,)).fetchone():
                event=replace(event,id=event.id+':'+uuid.uuid4().hex)
            content=dict(event.content,title='阅读公告',tier='medium',decision_reason='在走廊发现附近的未读公告')
            if phase=='active':
                content['reading_started_at']=event.game_time
            event=replace(event,content=content)
            self.s.event_runtime.enter(db,event,phase)
        return event.id

    def _drop_approach(self,name,complete=False):
        trip=self.approaches.pop(name,None)
        self.s.notice_approaching.discard(name)
        if trip and not complete:
            self.s.event_runtime.end_external(trip['event_id'],'interrupted','未到达公告栏，阅读中断')

    def _can_approach(self,name):
        return self.reading_allowed(name) and name not in self.s.busy|self.s.reflecting

    def _start_or_approach(self,name,board_id,now):
        paused_readers=sum(group is not self.requests for group,_,_,_ in self.suspended.values())
        if len(self.sessions)+len(self.approaches)+paused_readers>=NOTICEBOARD.max_readers:
            return False
        unread=self.unread(name,board_id)
        if not unread:
            return False
        if self.near(name,board_id,now) and not self.s.positions[name]['travelling']:
            self.start_read(name,board_id,now)
            return True
        point=self.approach_point(name,board_id,now)
        if not point:
            return False
        identity=self._begin_read_event(name,board_id,unread[0],'travelling')
        self.approaches[name]=dict(board_id=board_id,started_at=now,event_id=identity,notice_id=unread[0],**point)
        self.s.notice_approaching.add(name)
        self.s.emit('world',snapshot=self.s.world_snapshot())
        return True

    def tick(self,now,elapsed):
        self.jobs={key:job for key,job in self.jobs.items() if not job.done()}
        for name,(group,occupied,entry,paused_at) in list(self.suspended.items()):
            if not self.s.available(name,name) or name in self.s.planner.encounter_waits:
                continue
            if 'event_id' in entry:
                with self.s.database() as db:
                    focus,_=self.s.game_events.focus(db,name)
                if focus!=entry['event_id']:
                    continue
            for field in ('started_at','requested_at'):
                if field in entry:
                    entry[field]+=now-paused_at
            group[name]=entry
            occupied.add(name)
            del self.suspended[name]
        for name,session in list(self.sessions.items()):
            if not self.near(name,session['board_id'],now) or not self._can_approach(name):
                self._release(name,now);continue
            session['remaining']-=max(0,elapsed)
            if session['remaining']>0:continue
            with self.s.database() as db:
                db.execute('INSERT OR IGNORE INTO board_reads VALUES(?,?,?,?)',
                           (name,session['notice_id'],self.s.world.clock.now,'pending'))
                db.execute('INSERT OR IGNORE INTO board_read_events VALUES(?,?,?)',
                           (name,session['notice_id'],session['event_id']))
                self.s.event_runtime.release(db,session['event_id'])
            self._queue(name,session['notice_id'])
            self._release(name,now,complete=True)
        # 已经占用角色的交谈或阅读先继续，阅读排队项不能挡住更优先的安排。
        for name,request in list(self.requests.items()):
            if (not self.reading_allowed(name) or now-request['requested_at']>NOTICEBOARD.queue_timeout_seconds
                    or not self.in_range(name,request['board_id'],now,NOTICEBOARD.discovery_radius)
                    or not self.unread(name,request['board_id'])):
                self.cancel(name,now);continue
            self._try_requested(name,now)
        for name,trip in list(self.approaches.items()):
            p=self.s.positions.get(name)
            if (now-trip['started_at']>NOTICEBOARD.approach_timeout_seconds or not self._can_approach(name)
                or not p or p['area']!=BOARDS[trip['board_id']]['area'] or now-p['at']>SOCIAL.position_max_age_seconds
                or trip['notice_id'] not in self.unread(name,trip['board_id'])):
                self._drop_approach(name);self.cooldown[name]=now+NOTICEBOARD.reader_cooldown_seconds
            elif (not p['travelling'] and self.near(name,trip['board_id'],now)
                    and math.hypot(p['x']-trip['x'],p['y']-trip['y'])<=4):
                self.start_read(name,trip['board_id'],now)
        for name in self.s.npcs:
            if not self.s.onstage(name):continue
            if name in self.sessions or name in self.requests or name in self.approaches or now<self.cooldown.get(name,0):continue
            for board_id in BOARDS:
                if (self.reading_allowed(name) and self.in_range(name,board_id,now,NOTICEBOARD.discovery_radius)
                        and self.unread(name,board_id)):
                    self.request_read(name,board_id,now)
                    break
        if now>=self.next_retry:
            self.next_retry=now+NOTICEBOARD.retry_seconds
            with self.s.database() as db:pending=db.execute("SELECT npc,notice FROM board_reads WHERE status='pending'").fetchall()
            for name,notice_id in pending:self._queue(name,notice_id)

    def _release(self,name,now,complete=False):
        session=self.sessions.pop(name,None)
        self.s.reading.discard(name)
        if session and not complete:
            self.s.event_runtime.end_external(session['event_id'],'interrupted','阅读中断，未取得公告内容')
        self.cooldown[name]=now+NOTICEBOARD.reader_cooldown_seconds

    def _queue(self,name,notice_id):
        if not self.s.onstage(name):
            return
        key=(name,notice_id)
        if key in self.jobs and not self.jobs[key].done():return
        self.jobs[key]=self.pool.submit(self._write_for_cast,name,notice_id,self.s.cast_stamp(name))

    def _write_for_cast(self,name,notice_id,stamp):
        try:
            with self.s.cast_task(stamp):
                self._write(name,notice_id)
        except CastChanged:
            pass

    def _write(self,name,notice_id):
        with self.s.database() as db:
            row=db.execute('''SELECT n.board,n.content,r.read_at,r.status,m.event_id FROM board_notices n
                JOIN board_reads r ON r.notice=n.id LEFT JOIN board_read_events m ON m.npc=r.npc AND m.notice=r.notice
                WHERE n.id=? AND r.npc=?''',(notice_id,name)).fetchone()
        if not row or row[3]=='completed':return
        board_id,text,read_at,_,event_id=row
        board=BOARDS[board_id]
        identity=f'notice:{notice_id}:{name}'
        memory=self.s.npcs[name].remember(text,perspective='你亲自读完了公告栏上的一则公告',
            kind='observation',importance=NOTICEBOARD.importance,game_time=read_at,location=board['area'],
            tags=['notice',notice_id],memory_id=identity,commit=self.s.cast_step)
        with self.s.cast_step():
            result=dict(kind='notice_learned',status='completed',detail='已根据公告形成记忆')
            event=self._reading_event(name,notice_id,board,read_at,result)
            event=replace(event,id=event_id or event.id,content=dict(event.content,text=text))
            known_by={name:identity}
            with self.s.database() as db:
                updated=db.execute("UPDATE board_reads SET status='completed' WHERE npc=? AND notice=? AND status='pending'",
                                   (name,notice_id))
                if not updated.rowcount:return
                changes=self.s.store_game_event(db,event,known_by)
                db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',
                           ('notice:'+notice_id,name,'公告栏',memory.content,0,identity))
            self.s.apply_game_event(event,known_by,changes)
            self.s.emit('memory_changed',event_key=identity,npc=name)
            self.s.emit('notice_read',npc=name,notice_id=notice_id,board_id=board_id,game_event_id=event.id)

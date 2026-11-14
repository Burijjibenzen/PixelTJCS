"""公开发布不等于角色知情；位置合法且完成阅读后才写记忆。"""
import json
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from backend.config import NOTICEBOARD, SOCIAL
from backend.world import AREAS, FRONTEND_DIR, J3, J4, ZX2


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
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='notice-memory')
        with service.database() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS board_notices(id TEXT PRIMARY KEY,board TEXT,title TEXT,content TEXT,published_at REAL);
                CREATE TABLE IF NOT EXISTS board_reads(npc TEXT,notice TEXT,read_at REAL,status TEXT,PRIMARY KEY(npc,notice));
                CREATE TABLE IF NOT EXISTS notice_comments(npc TEXT,notice TEXT,text TEXT,created REAL,delivered INTEGER DEFAULT 0,PRIMARY KEY(npc,notice));
            ''')

    def stop(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.s.reading_waiting.clear()
        self.s.reading.clear()
        self.s.notice_approaching.clear()

    def listing(self,board_id):
        if board_id not in BOARDS:raise KeyError('公告栏不存在')
        with self.s.database() as db:
            rows=db.execute('SELECT id,title,content,published_at FROM board_notices WHERE board=? ORDER BY published_at DESC,rowid DESC',(board_id,)).fetchall()
        readers=[dict(npc=n,state='waiting',detail=self.wait_reason(n)) for n,r in self.requests.items() if r['board_id']==board_id]
        readers += [dict(npc=n,state='reading',detail='正在阅读公告') for n,r in self.sessions.items() if r['board_id']==board_id]
        readers += [dict(npc=n,state='approaching',detail='正在走近公告栏') for n,r in self.approaches.items() if r['board_id']==board_id]
        return dict(board=dict(BOARDS[board_id]),notices=[dict(id=r[0],title=r[1],content=r[2],published_at=r[3]) for r in rows],readers=readers)

    def manual_ready(self,name):
        if not self.s.planner.manual(name):return True
        item=self.s.planner.current_item(name)
        return bool(item and item['source']=='manual' and item['phase']=='active' and item['area']==AREAS[self.s.world.area_of(name)]['id'])

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
        if self.s.paused:raise ValueError('世界暂停中，继续世界后再阅读')
        # 先检查真实位置，忙碌不能掩盖“角色不在公告栏附近”的实际原因。
        if not self.near(name,board_id,now):raise ValueError('角色需要实际走到公告栏附近，才能阅读')
        if not self.manual_ready(name):raise ValueError('角色仍在执行手动行程，请抵达或取消行程后再阅读')
        if not self.unread(name,board_id):raise ValueError('没有尚未阅读的公告')
        created=name not in self.requests
        if created:
            self._drop_approach(name)
            self.requests[name]=dict(board_id=board_id,requested_at=now)
            self.s.reading_waiting.add(name)
        self._try_requested(name,now)
        if name in self.sessions:return dict(state='reading',detail='角色开始阅读，读完后继续原来的安排')
        if created:self.s.emit('world',snapshot=self.s.world_snapshot())
        return dict(state='waiting',detail=self.wait_reason(name))

    def _try_requested(self,name,now):
        request=self.requests[name]
        try:self.start_read(name,request['board_id'],now)
        except ValueError:return
        self.requests.pop(name,None);self.s.reading_waiting.discard(name)

    def cancel(self,name,now=None):
        self._drop_approach(name)
        self.requests.pop(name,None);self.s.reading_waiting.discard(name)
        self._release(name,time.monotonic() if now is None else now)

    def publish(self,board_id,title,content):
        if board_id not in BOARDS:raise KeyError('公告栏不存在')
        title,content=title.strip(),content.strip()
        if not title or not content or len(title)>80 or len(content)>500:raise ValueError('请填写标题和正文，标题最多80字，正文最多500字')
        item=dict(id=uuid.uuid4().hex,title=title,content=content,published_at=self.s.world.clock.now)
        with self.s.database() as db:
            count=db.execute('SELECT COUNT(*) FROM board_notices WHERE board=?',(board_id,)).fetchone()[0]
            if count>=NOTICEBOARD.max_notices_per_board:raise ValueError('此公告栏已达到公告上限')
            db.execute('INSERT INTO board_notices VALUES(?,?,?,?,?)',(item['id'],board_id,title,content,item['published_at']))
        self.s.emit('notice_published',board_id=board_id,notice_id=item['id'])
        return item

    def near(self,name,board_id,now):
        board=BOARDS[board_id];sample=self.s.positions.get(name)
        return bool(sample and sample['area']==board['area'] and
            AREAS[self.s.world.area_of(name)]['id']==board['area'] and
            0<=now-sample['at']<=SOCIAL.position_max_age_seconds and
            math.hypot(sample['x']-board['x'],sample['y']-board['y'])<=NOTICEBOARD.read_radius)

    def unread(self,name,board_id):
        with self.s.database() as db:
            return [r[0] for r in db.execute('''SELECT n.id FROM board_notices n WHERE n.board=?
                AND NOT EXISTS(SELECT 1 FROM board_reads r WHERE r.npc=? AND r.notice=n.id)
                ORDER BY n.published_at DESC,n.rowid DESC LIMIT 1''',(board_id,name))]

    def start_read(self,name,board_id,now=None):
        now=time.monotonic() if now is None else now
        if name not in self.s.npcs or board_id not in BOARDS:raise KeyError('未知角色或公告栏')
        if name in self.sessions:
            if self.sessions[name]['board_id']==board_id:return dict(self.sessions[name])
            raise ValueError('角色正在阅读另一处公告栏')
        if self.s.paused:raise ValueError('世界暂停中，继续世界后再阅读')
        if name in self.s.busy|self.s.reflecting:
            raise ValueError('角色正在处理其它任务，请稍后阅读')
        if not self.manual_ready(name):raise ValueError('角色仍在执行手动行程，请抵达或取消行程后再阅读')
        if len(self.sessions)>=NOTICEBOARD.max_readers:raise ValueError('附近阅读中的角色较多，请稍后再试')
        if not self.near(name,board_id,now):raise ValueError('角色需要实际走到公告栏附近并停下后，才能阅读')
        unread=self.unread(name,board_id)
        if not unread:raise ValueError('没有尚未阅读的公告')
        session=dict(board_id=board_id,notice_id=unread[0],remaining=NOTICEBOARD.read_seconds)
        self._drop_approach(name)
        self.sessions[name]=session;self.s.reading.add(name)
        self.s.emit('world',snapshot=self.s.world_snapshot())
        return dict(session)

    def approach_point(self,name,board_id,now):
        """从真实采样格做有界BFS，落点须在同区可走地板且位于板前。"""
        board=BOARDS[board_id];p=self.s.positions.get(name)
        if (not p or p['area']!=board['area'] or AREAS[self.s.world.area_of(name)]['id']!=board['area']
            or not 0<=now-p['at']<=SOCIAL.position_max_age_seconds):return None
        if math.hypot(p['x']-board['x'],p['y']-board['y'])>NOTICEBOARD.discovery_radius:return None
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
            if math.hypot(px-board['x'],py-board['y'])<=NOTICEBOARD.read_radius/2:return dict(x=px,y=py)
            if steps>=math.ceil(NOTICEBOARD.discovery_radius*2/tile):continue
            for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if (nx,ny) not in seen and walkable(nx,ny):
                    seen.add((nx,ny));queue.append((nx,ny,steps+1))
        return None

    def _drop_approach(self,name):
        self.approaches.pop(name,None);self.s.notice_approaching.discard(name)

    def _can_approach(self,name):
        if name in self.s.busy|self.s.reflecting or not self.manual_ready(name):return False
        if (self.s.planner.current_item(name) or {}).get('priority',0)>=70:return False
        # 新到的高优先级安排也应能结束顺路停靠，不必先等它成为current。
        return not any(a['npc']==name and a['state']=='scheduled' and a['priority']>=70
            and a['starts_at']<=self.s.world.clock.now for a in self.s.planner.records.values())

    def tick(self,now,elapsed):
        self.jobs={key:job for key,job in self.jobs.items() if not job.done()}
        for name,session in list(self.sessions.items()):
            if not self.near(name,session['board_id'],now) or name in self.s.busy|self.s.reflecting:
                self._release(name,now);continue
            session['remaining']-=max(0,elapsed)
            if session['remaining']>0:continue
            with self.s.database() as db:
                db.execute('INSERT OR IGNORE INTO board_reads VALUES(?,?,?,?)',(name,session['notice_id'],self.s.world.clock.now,'pending'))
            self._queue(name,session['notice_id'])
            self._release(name,now)
        # 已发现的阅读机会先于新会面、自然社交、反思和普通日程获得空闲角色。
        for name,request in list(self.requests.items()):
            if now-request['requested_at']>NOTICEBOARD.queue_timeout_seconds or not self.near(name,request['board_id'],now) or not self.unread(name,request['board_id']):
                self.cancel(name,now);continue
            self._try_requested(name,now)
        for name,trip in list(self.approaches.items()):
            p=self.s.positions.get(name)
            if (now-trip['started_at']>NOTICEBOARD.approach_timeout_seconds or not self._can_approach(name)
                or not p or p['area']!=BOARDS[trip['board_id']]['area'] or now-p['at']>SOCIAL.position_max_age_seconds
                or not self.unread(name,trip['board_id'])):
                self._drop_approach(name);self.cooldown[name]=now+NOTICEBOARD.reader_cooldown_seconds
            elif self.near(name,trip['board_id'],now):
                self.request_read(name,trip['board_id'],now)
        for name in self.s.npcs:
            if name in self.sessions or name in self.requests or name in self.approaches or now<self.cooldown.get(name,0):continue
            for board_id in BOARDS:
                if self.near(name,board_id,now):
                    try:self.request_read(name,board_id,now)
                    except ValueError:pass
                    break
                if not self._can_approach(name) or len(self.approaches)+len(self.sessions)+len(self.requests)>=NOTICEBOARD.max_readers:continue
                point=self.approach_point(name,board_id,now)
                if point and self.unread(name,board_id):
                    self.approaches[name]=dict(board_id=board_id,started_at=now,**point)
                    self.s.notice_approaching.add(name)
                    self.s.emit('world',snapshot=self.s.world_snapshot())
                    break
        if now>=self.next_retry:
            self.next_retry=now+NOTICEBOARD.retry_seconds
            with self.s.database() as db:pending=db.execute("SELECT npc,notice FROM board_reads WHERE status='pending'").fetchall()
            for name,notice_id in pending:self._queue(name,notice_id)
        self._comments()

    def _remark(self,name,title,content):
        text=title+' '+content
        if any(word in text for word in ('取消','延期')):return '（自言自语）安排有变，得留意后续通知。'
        kind=self.s.planner.notice_kind(text)
        if kind=='seminar':
            return '（自言自语）'+('有组会通知，得提前理一理要讨论的问题。' if '组会' in title+content else '这场学术交流得留意一下，提前安排好时间。')
        words={'library':'这条资料通知记下了，下次查资料时可以用上。',
               'class':'课程安排有更新，得记清楚时间和地点。',
               'exercise':'有空可以去活动一下，换换脑子。'}
        return '（自言自语）'+words.get(kind,f'这条“{title[:18]}”我记下了，之后留意一下。')

    def _comments(self):
        with self.s.database() as db:
            rows=db.execute('SELECT npc,notice,text,created FROM notice_comments WHERE delivered=0').fetchall()
        for name,notice_id,text,created in rows:
            if name in self.s.busy|self.s.reflecting|self.s.reading:continue
            key=f'notice:{notice_id}:{name}:comment'
            with self.s.database() as db:
                already=db.execute('SELECT 1 FROM m3_events WHERE event_key=?',(key,)).fetchone()
            if not already and time.time()-created<=NOTICEBOARD.queue_timeout_seconds:
                self.s.emit('dialogue_line',event_key=key,conversation_id=key,sequence=1,
                    participants=[name],speaker=name,text=text,activity='notice_comment')
            with self.s.database() as db:
                db.execute('UPDATE notice_comments SET delivered=1 WHERE npc=? AND notice=?',(name,notice_id))

    def _release(self,name,now):
        self.sessions.pop(name,None);self.s.reading.discard(name)
        self.cooldown[name]=now+NOTICEBOARD.reader_cooldown_seconds

    def _queue(self,name,notice_id):
        key=(name,notice_id)
        if key in self.jobs and not self.jobs[key].done():return
        self.jobs[key]=self.pool.submit(self._write,name,notice_id)

    def _write(self,name,notice_id):
        with self.s.database() as db:
            row=db.execute('''SELECT n.board,n.title,n.content,r.read_at,r.status FROM board_notices n
                JOIN board_reads r ON r.notice=n.id WHERE n.id=? AND r.npc=?''',(notice_id,name)).fetchone()
        if not row or row[4]=='completed':return
        board_id,title,content,read_at,_=row
        text=f'公告《{title}》：{content}'
        identity=f'notice:{notice_id}:{name}'
        if self.s.memory.get_by_id(identity) is None:
            self.s.memory.add(agent=name,content=text,type='observation',importance=NOTICEBOARD.importance,
                game_time=read_at,location=BOARDS[board_id]['area'],tags=['notice',notice_id],memory_id=identity)
        with self.s.lock:
            with self.s.database() as db:
                db.execute('INSERT OR IGNORE INTO gossip VALUES(?,?,?,?,?,?)',('notice:'+notice_id,name,'公告栏',text,0,identity))
            event=self.s.world.inject_event(text,[name],BOARDS[board_id]['area']);event['key']=identity
            self.s.planner.notice(text,[name],identity,read_at)
            with self.s.database() as db:
                db.execute("UPDATE board_reads SET status='completed' WHERE npc=? AND notice=?",(name,notice_id))
                db.execute('INSERT OR IGNORE INTO notice_comments(npc,notice,text,created) VALUES(?,?,?,?)',
                    (name,notice_id,self._remark(name,title,content),time.time()))
        self.s.emit('memory_changed',event_key=identity,npc=name)
        self.s.emit('notice_read',npc=name,notice_id=notice_id,board_id=board_id)

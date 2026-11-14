"""任务04：公告发布、位置合法阅读、幂等记忆与知情范围。"""
import time
import unittest
from unittest.mock import patch
import test_social as fixtures
from backend.config import NOTICEBOARD, SOCIAL
from backend.noticeboards import BOARDS, NAV_GRIDS, NoticeBoards


class NoticeBoardTests(unittest.TestCase):
    setUp=fixtures.SocialTests.setUp
    tearDown=fixtures.SocialTests.tearDown
    profile_client=fixtures.SocialTests.profile_client

    def board(self):return next(iter(BOARDS.values()))

    def locate(self,name=None,board=None,offset=0,travelling=False):
        board=board or self.board()
        self.s.report_arrival(name or self.a,map_id=board['map_id'],room_id=board['room_id'],
            x=board['x']+offset,y=board['y'],arrived=not travelling)

    def publish(self):return self.s.boards.publish(self.board()['id'],'查阅资料','去图书分馆查阅研究文献')

    def complete(self):
        self.s.boards.start_read(self.a,self.board()['id'])
        self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        for future in list(self.s.boards.jobs.values()):future.result(timeout=5)

    def test_publish_and_user_view_never_broadcast_knowledge(self):
        before={n:len(self.s.memory.get_active(n,50)) for n in self.s.npcs}
        notice=self.publish()
        with self.profile_client() as client:
            boards=client.get('/noticeboards').json()['boards']
            self.assertEqual(3,len(boards))
            data=client.get('/noticeboards/'+self.board()['id']+'/notices').json()
            self.assertEqual(notice['id'],data['notices'][0]['id'])
            self.assertEqual(self.board()['location'],data['board']['location'])
            self.assertEqual(self.s.world.clock.now,data['notices'][0]['published_at'])
        self.assertFalse(self.s.world.events)
        self.assertEqual(before,{n:len(self.s.memory.get_active(n,50)) for n in self.s.npcs})

    def test_completed_read_writes_once_and_only_reader_knows(self):
        notice=self.publish();self.locate();self.complete()
        identity=f"notice:{notice['id']}:{self.a}"
        self.assertIsNotNone(self.s.memory.get_by_id(identity))
        self.assertFalse(self.s.reading)
        self.assertTrue(any(a['npc']==self.a and a['source']=='event' for a in self.s.planner.records.values()))
        self.assertFalse(self.s.planner.context(self.b)['known_events'])
        self.assertFalse(any('查阅研究文献' in m.content for m in self.s.memory.get_active(self.b,50)))
        self.assertFalse(self.s.boards.unread(self.a,self.board()['id']))
        self.s.boards._write(self.a,notice['id'])
        with self.s.database() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM memories WHERE id=?',(identity,)).fetchone()[0])
            db.execute('UPDATE memories SET forgotten=1 WHERE id=?',(identity,))
        self.assertFalse(self.s.boards.unread(self.a,self.board()['id']))
        self.assertIsNone(self.s.gossip_candidate(self.a,self.b))

    def test_read_requires_fresh_same_map_room_and_nearby(self):
        self.publish();key=self.board()['id']
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)
        self.locate(offset=NOTICEBOARD.read_radius+1)
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)
        self.locate(travelling=True)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading)  # 普通在途行程可以短暂停下阅读，路线仍保留。
        self.s.boards.cancel(self.a)
        other=list(BOARDS.values())[1];self.locate(board=other)
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)
        self.locate();self.s.positions[self.a]['at']-=SOCIAL.position_max_age_seconds+1
        self.s.heartbeat()
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)

    def test_interrupted_or_busy_read_gives_no_memory(self):
        notice=self.publish();self.locate()
        self.s.busy.add(self.a)
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,self.board()['id'])
        self.s.busy.clear();self.s.boards.start_read(self.a,self.board()['id'])
        self.assertFalse(self.s.assign_destination(self.a,'missing-area'))
        self.assertIn(self.a,self.s.reading)
        self.assertTrue(self.s.world_snapshot()['npcs'][self.a]['busy'])
        self.assertEqual('阅读公告',self.s.world_snapshot()['npcs'][self.a]['activity']['title'])
        self.assertFalse(self.s.available(self.a,self.b))
        self.locate(offset=1000);self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        self.assertFalse(self.s.reading)
        self.assertIsNone(self.s.memory.get_by_id(f"notice:{notice['id']}:{self.a}"))

    def test_notice_can_enter_existing_gossip_and_dialogue_context(self):
        self.publish();self.locate();self.complete()
        self.locate(self.b,offset=16)
        self.assertIsNotNone(self.s.gossip_candidate(self.a,self.b))
        result=self.s.run_conversation(self.a,self.b,'交流公告')
        self.assertIsNotNone(result)
        self.assertTrue(result['transcript'][0]['text'].startswith('听说'))
        self.assertTrue(any(m.type=='gossip' for m in self.s.memory.get_active(self.b,50)))

    def test_restart_recovers_approved_pending_read_without_duplicate_memory(self):
        notice=self.publish();self.locate()
        with patch.object(self.s.boards.pool,'submit'):
            self.s.boards.start_read(self.a,self.board()['id'])
            self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        self.s.boards.stop();self.s.boards=NoticeBoards(self.s)
        self.s.boards.tick(time.monotonic(),0)
        for future in list(self.s.boards.jobs.values()):future.result(timeout=5)
        self.assertIsNotNone(self.s.memory.get_by_id(f"notice:{notice['id']}:{self.a}"))
        self.assertEqual(1,len(self.s.boards.listing(self.board()['id'])['notices']))

    def test_api_publish_read_validation_and_no_instant_knowledge(self):
        key=self.board()['id']
        with self.profile_client() as client:
            self.assertEqual(404,client.get('/noticeboards/missing/notices').status_code)
            self.assertEqual(400,client.post('/noticeboards/'+key+'/notices',json={'title':' ','content':' '}).status_code)
            self.assertEqual(422,client.post('/noticeboards/'+key+'/notices',json={'title':'标题','content':'字'*501}).status_code)
            self.assertEqual(200,client.post('/noticeboards/'+key+'/notices',json={'title':'组会','content':'明天10:00组会'}).status_code)
            self.assertEqual(409,client.post('/noticeboards/'+key+'/read',json={'npc':self.a}).status_code)
            self.locate()
            self.assertEqual(200,client.post('/noticeboards/'+key+'/read',json={'npc':self.a}).status_code)
            self.assertFalse(self.s.world.events)

    def test_nearby_busy_npc_automatically_reserves_reading_before_more_tasks(self):
        self.publish();self.locate();self.s.busy.add(self.a)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading_waiting)
        self.assertFalse(self.s.available(self.a,self.b))
        self.assertFalse(self.s.world.events)
        self.assertEqual('等待阅读',self.s.world_snapshot()['npcs'][self.a]['activity']['state_label'])
        self.s.busy.clear()
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.reading_waiting)
        self.assertIn(self.a,self.s.reading)

    def test_queued_meeting_does_not_block_automatic_reading(self):
        self.publish();self.locate()
        meeting=self.s.request_meeting(self.a,self.b,self.board()['area'])
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading)
        self.s.scheduler.meetings(time.monotonic(),0)
        self.assertEqual('waiting',meeting['state'])
        self.assertFalse(self.s.busy)

    def test_arrived_manual_activity_can_read_but_in_flight_manual_cannot(self):
        self.publish();self.locate()
        self.s.assign_destination(self.a,self.board()['area'])
        self.s.planner.tick(time.monotonic(),0)
        with self.assertRaises(ValueError):self.s.boards.request_read(self.a,self.board()['id'])
        self.locate()  # 真实到达确认使手动行程进入停留阶段。
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading)
        self.assertTrue(self.s.assign_destination(self.a,'j3_lab'))
        self.assertNotIn(self.a,self.s.reading)
        self.assertNotIn(self.a,self.s.reading_waiting)

    def test_waiting_read_cancels_on_departure_or_timeout_without_knowledge(self):
        self.publish();self.locate();self.s.reflecting.add(self.a)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading_waiting)
        self.locate(offset=1000);self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.reading_waiting)
        self.s.boards.cooldown.clear();self.locate()
        self.s.boards.tick(time.monotonic(),0)
        self.s.boards.requests[self.a]['requested_at']-=NOTICEBOARD.queue_timeout_seconds+1
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.reading_waiting)
        self.assertFalse(self.s.world.events)

    def test_read_comment_is_once_self_only_nonblocking_and_waits_for_dialogue(self):
        self.assertIn('安排有变',self.s.boards._remark(self.a,'组会取消','无需到场'))
        self.publish();self.locate();self.complete()
        self.s.busy.add(self.a)
        self.s.boards.tick(time.monotonic(),0)
        self.assertFalse(any(e.get('activity')=='notice_comment' for e in self.s.bus.history()))
        self.s.busy.clear();self.s.boards.tick(time.monotonic(),0)
        comments=[e for e in self.s.bus.history() if e.get('activity')=='notice_comment']
        self.assertEqual(1,len(comments))
        self.assertEqual([self.a],comments[0]['participants'])
        self.assertIn('自言自语',comments[0]['text'])
        self.assertIn('资料',comments[0]['text'])
        self.assertFalse(self.s.busy)
        self.assertFalse(self.s.reading)
        self.s.boards.tick(time.monotonic(),0)
        self.assertEqual(1,len([e for e in self.s.bus.history() if e.get('activity')=='notice_comment']))

    def test_all_three_boards_have_reachable_floor_targets(self):
        for board in BOARDS.values():
            # 板中心是墙体/装饰；从同一走廊的真实地板出发，不能把板中心当目的地。
            cell=(28,30) if board['map_id'].startswith('jishilou') else (60,106)
            self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],
                x=(cell[0]+.5)*16,y=(cell[1]+.5)*16,arrived=False)
            now=time.monotonic()
            self.assertFalse(self.s.boards.near(self.a,board['id'],now))
            point=self.s.boards.approach_point(self.a,board['id'],now)
            self.assertIsNotNone(point,board['id'])
            grid,codes=NAV_GRIDS[board['map_id']]
            x,y=int(point['x']//16),int(point['y']//16)
            self.assertEqual('0',grid['obstacle'][y][x])
            self.assertEqual(codes[board['room_id']],grid['room_grid'][y][x])
            self.assertLessEqual((point['x']-board['x'])**2+(point['y']-board['y'])**2,(NOTICEBOARD.read_radius/2)**2)
        self.assertEqual('zx2_corridor',list(BOARDS.values())[2]['room_id'])

    def test_nearby_approach_targets_real_point_then_reads_and_restores_schedule(self):
        self.publish();board=self.board()
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],x=456,y=488,arrived=False)
        before=self.s.world_snapshot()['npcs'][self.a]
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        state=self.s.world_snapshot()['npcs'][self.a]
        self.assertFalse(state['busy'])
        self.assertEqual('走近公告栏',state['activity']['title'])
        self.assertFalse(self.s.available(self.a,self.b))
        self.assertFalse(self.s.world.events)
        point=state['target_position']
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],**point,arrived=True)
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.assertIn(self.a,self.s.reading)
        self.assertNotIn('target_position',self.s.world_snapshot()['npcs'][self.a])
        self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        for future in list(self.s.boards.jobs.values()):future.result(timeout=5)
        self.assertNotIn(self.a,self.s.reading)
        self.assertEqual(before['target_room_id'],self.s.world_snapshot()['npcs'][self.a]['target_room_id'])

    def test_approach_does_not_cross_walls_or_steal_manual_tasks_and_times_out(self):
        self.publish();board=self.board();key=board['id']
        self.locate(offset=1000)
        self.assertIsNone(self.s.boards.approach_point(self.a,key,time.monotonic()))
        self.locate()  # 板中心是实心墙，不是可达落点。
        self.assertIsNone(self.s.boards.approach_point(self.a,key,time.monotonic()))
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],x=456,y=488,arrived=False)
        self.s.busy.add(self.a);self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.s.busy.clear();self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        self.s.boards.approaches[self.a]['started_at']-=NOTICEBOARD.approach_timeout_seconds+1
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.assertFalse(self.s.world.events)
        self.s.boards.cooldown.clear();self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        self.assertTrue(self.s.assign_destination(self.a,'j3_lab'))
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)

    def test_new_urgent_event_interrupts_approach_before_becoming_current(self):
        self.publish();board=self.board()
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],x=456,y=488,arrived=False)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        self.s.planner._make(self.a,'baseline','j3_lab',self.s.world.clock.now,'event','紧急通知',priority=70)
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.assertFalse(self.s.reading)
        self.assertFalse(self.s.world.events)


if __name__=='__main__':unittest.main()

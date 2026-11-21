"""任务04：公告发布、位置合法阅读、幂等记忆与知情范围。"""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_social as fixtures
from backend.config import NOTICEBOARD, SOCIAL
from backend.noticeboards import BOARDS, NAV_GRIDS, NoticeBoards
from backend.world import AREAS


class NoticeBoardTests(unittest.TestCase):
    setUp=fixtures.SocialTests.setUp
    tearDown=fixtures.SocialTests.tearDown
    profile_client=fixtures.SocialTests.profile_client

    def board(self):return next(iter(BOARDS.values()))

    def locate(self,name=None,board=None,offset=0,travelling=False):
        board=board or self.board()
        self.s.report_arrival(name or self.a,map_id=board['map_id'],room_id=board['room_id'],
            x=board['x']+offset,y=board['y']+32,arrived=not travelling)

    def publish(self):return self.s.boards.publish(self.board()['id'],'去图书分馆查阅研究文献')

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

    def test_nearby_encounter_pauses_notice_reading_and_restores_the_same_session(self):
        board_id=self.board()['id']
        self.publish()
        self.locate()
        self.locate(self.b,offset=64,travelling=True)
        session=self.s.boards.start_read(self.a,board_id)
        before=NOTICEBOARD.read_seconds/2
        self.s.boards.sessions[self.a]['remaining']=before
        planner=self.s.planner
        visit=planner.queue_social_visit(self.b,self.a,'稍等一下')
        planner.tick(time.monotonic(),0)
        self.assertEqual(visit['id'],planner.encounter_waits[self.a])
        self.assertNotIn(self.a,self.s.reading)
        self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        self.assertIn(self.a,self.s.boards.suspended)
        self.assertEqual([],list(self.s.boards.jobs))
        planner.cancel(self.b,visit['id'])
        self.s.boards.tick(time.monotonic(),0)
        restored=self.s.boards.sessions[self.a]
        self.assertEqual((session['event_id'],before),(restored['event_id'],restored['remaining']))

    def test_completed_read_writes_once_and_only_reader_knows(self):
        notice=self.publish();self.locate();self.complete()
        identity=f"notice:{notice['id']}:{self.a}"
        memory=self.s.memory.get_by_id(identity)
        self.assertEqual((notice['content'],'observation'),(memory.content,memory.type))
        self.assertFalse(self.s.reading)
        self.assertFalse(self.s.planner.records)
        self.assertFalse(self.s.planner.context(self.b)['known_events'])
        self.assertFalse(any('查阅研究文献' in m.content for m in self.s.memory.get_active(self.b,50)))
        self.assertFalse(self.s.boards.unread(self.a,self.board()['id']))
        self.s.boards._write(self.a,notice['id'])
        with self.s.database() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM memories WHERE id=?',(identity,)).fetchone()[0])
            db.execute('UPDATE memories SET forgotten=1 WHERE id=?',(identity,))
        self.assertFalse(self.s.boards.unread(self.a,self.board()['id']))
        self.assertIsNone(self.s.gossip_candidate(self.a,self.b))
        self.assertFalse(self.s.planner.context(self.a)['known_events'])
        self.assertEqual(identity,self.s.game_events.timeline(self.a)['events'][0]['id'])

    def test_timeline_links_to_the_original_notice_and_memory_after_restart(self):
        self.locate()
        notice=self.s.boards.publish(self.board()['id'],'明天10:00去图书分馆查阅研究文献')
        self.complete()
        identity=f"notice:{notice['id']}:{self.a}"
        timeline=self.s.game_events.timeline(self.a)['events']
        self.assertFalse(self.s.planner.records)
        self.assertEqual([identity],[e['event_id'] for e in self.s.planner.context(self.a)['known_events']])
        self.s.planner.tick(time.monotonic(),0)
        self.assertFalse(any(a['source'] in {'event','memory'} for a in self.s.planner.records.values()))

        self.s.stop()
        self.s=type(self.s)()
        self.s.boards._write(self.a,notice['id'])
        self.assertEqual(timeline,self.s.game_events.timeline(self.a)['events'])
        self.assertEqual(notice['content'],self.s.memory.get_by_id(identity).content)
        self.assertEqual([identity],[e['event_id'] for e in self.s.planner.context(self.a)['known_events']])

        self.assertIn(self.a,self.s.planner.routine_turns)
        self.resume_routine()
        self.locate()
        self.s.boards.publish(self.board()['id'],'今天正常开放')
        self.complete()
        with self.profile_client() as client:
            first=client.get(f'/npcs/{self.a}/timeline',params={'limit':1}).json()
            cursor=first['next_cursor']
            second=client.get(f'/npcs/{self.a}/timeline',params={'before':cursor,'limit':1}).json()
            combined=first['events']+second['events']
            read=next(event for event in combined if event['id']==identity)
            self.assertEqual({'map_id':self.board()['map_id'],'room_id':self.board()['room_id']},read['location'])
            self.assertEqual({'type':'object','id':self.board()['id']},read['target'])
            self.assertEqual('notice:'+notice['id'],read['source_event_id'])
            self.assertEqual('notice_learned',read['result']['kind'])
            self.assertEqual('已根据公告形成记忆',read['result']['detail'])
            self.assertEqual(notice['content'],read['content']['text'])
            self.assertTrue(all(event['reaction'] is None and event['assessment'] is None for event in combined))
            self.assertIsNone(second['next_cursor'])
            self.assertEqual([],client.get(f'/npcs/{self.b}/timeline').json()['events'])
            self.assertEqual(400,client.get(f'/npcs/{self.b}/timeline',params={'before':cursor}).status_code)

    def test_timed_notice_text_is_remembered_without_creating_an_appointment(self):
        notice=self.s.boards.publish(self.board()['id'],'明天10:00到学术讲座教室参加组会')
        self.s.world.clock.minutes=1440+9*60
        self.locate()
        self.complete()
        item,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual(notice['content'],item['content']['text'])
        self.assertIsNone(item['reaction'])
        self.assertFalse(self.s.planner.records)
        self.assertEqual('notice:'+notice['id'],item['source_event_id'])

    def test_notice_memory_can_differ_from_the_recorded_board_text(self):
        from backend.npc import MemoryAssessment
        notice=self.publish()
        self.locate()
        assessment=MemoryAssessment(content='我以为公告说要去核对借阅记录。',importance=0.7,emotion='neutral')
        with patch.object(self.s.llm,'chat_json',return_value=assessment):
            self.complete()
        event,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual(notice['content'],event['source']['text'])
        self.assertEqual(assessment.content,self.s.memory.get_by_id(event['id']).content)
        self.assertEqual(assessment.content,self.s.gossip_candidate(self.a,self.b)['content'])
        self.assertFalse(self.s.planner.records)

    def test_explicit_notice_enters_memory_without_launching_a_model_decision(self):
        self.s.llm.cfg=SimpleNamespace(api_key='mock')
        self.locate()
        self.locate(self.b)
        board_id=self.board()['id']
        notice=self.s.boards.publish(board_id,'看到的人前往图书馆')
        self.s.boards.start_read(self.b,board_id)
        self.complete()
        planner=self.s.planner
        self.assertFalse(planner.records)

        with patch.object(self.s.engine.agents[self.a],'assess_event') as assess:
            self.s.event_runtime.poll(time.monotonic())
            self.assertFalse(self.s.event_runtime.jobs)
            assess.assert_not_called()
        for name in (self.a,self.b):
            self.assertIn(notice['content'],[memory['content'] for memory in planner.context(name)['memories']])
            item,=self.s.game_events.timeline(name)['events']
            self.assertIsNone(item['assessment'])
        self.assertFalse(planner.context(self.c)['known_events'])

    def test_restart_retires_an_old_pending_notice_assessment(self):
        notice=self.publish();self.locate();self.complete()
        identity=f"notice:{notice['id']}:{self.a}"
        with self.s.database() as db:
            db.execute('INSERT INTO event_assessments VALUES(?,?,?,?,?,?)',
                       (identity,self.a,'fact','pending',self.s.world.clock.now,'{}'))
        self.s.stop()
        self.s=type(self.s)()
        item,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual('remembered',item['assessment']['status'])
        self.assertEqual(notice['content'],self.s.memory.get_by_id(identity).content)
        self.assertFalse(self.s.planner.records)

    def test_failed_event_write_keeps_pending_read_retryable(self):
        notice=self.publish()
        at=self.s.world.clock.now
        with self.s.database() as db:
            db.execute('INSERT INTO board_reads VALUES(?,?,?,?)',(self.a,notice['id'],at,'pending'))
        with patch.object(self.s.game_events,'learn',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):
                self.s.boards._write(self.a,notice['id'])
        self.assertFalse(self.s.game_events.timeline(self.a)['events'])
        with self.s.database() as db:
            self.assertEqual('pending',db.execute('SELECT status FROM board_reads').fetchone()[0])
        self.s.boards._write(self.a,notice['id'])
        self.assertEqual(1,len(self.s.game_events.timeline(self.a)['events']))
        self.assertFalse(self.s.planner.records)

    def test_old_reads_are_visible_without_creating_new_activities(self):
        with self.s.database() as db:
            db.execute('ALTER TABLE board_notices ADD COLUMN title TEXT')
            db.execute('INSERT INTO board_notices(id,board,title,content,published_at) VALUES(?,?,?,?,?)',
                       ('legacy',self.board()['id'],'旧组会','15:30学术讲座教室组会',480))
            db.execute('INSERT INTO board_reads VALUES(?,?,?,?)',(self.a,'legacy',481,'completed'))
        self.s.stop()
        self.s=type(self.s)()
        row,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual(481,row['learned_at'])
        self.assertEqual('15:30学术讲座教室组会',row['source']['text'])
        self.assertNotIn('title',self.s.boards.listing(self.board()['id'])['notices'][0])
        self.assertEqual('去图书分馆查阅研究文献',self.publish()['content'])
        self.assertEqual('historical',row['reaction']['action'])
        self.assertIsNone(row['reaction']['game_time'])
        self.assertFalse(self.s.planner.records)
        self.assertFalse(self.s.planner.context(self.a)['known_events'])

    def test_map_perception_filters_nearby_objects_and_people_by_actual_location(self):
        self.locate()
        self.locate(self.b,offset=16)
        self.locate(self.c,board=list(BOARDS.values())[1])
        self.locate(self.d,offset=24)
        self.s.positions[self.d]['at']-=SOCIAL.position_max_age_seconds+1
        context=self.s.planner.context(self.a)['perception']
        self.assertEqual(self.board()['map_id'],context['location']['map_id'])
        self.assertTrue(context['location']['fresh'])
        self.assertEqual([self.b],[item['name'] for item in context['nearby_characters']])
        self.assertEqual([self.board()['id']],[item['id'] for item in context['nearby_objects']])
        self.assertFalse(self.s.perception(self.d)['nearby_characters'])
        prompt=self.s.engine.agents[self.a].perceive({})['context']
        self.assertIn(self.board()['id'],prompt)

    def test_read_requires_fresh_same_map_room_and_nearby(self):
        self.publish();key=self.board()['id']
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)
        self.locate(offset=NOTICEBOARD.read_radius+1)
        with self.assertRaises(ValueError):self.s.boards.start_read(self.a,key)
        self.locate(travelling=True)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        self.assertNotIn(self.a,self.s.reading)
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
        notice=self.publish();self.locate();self.complete()
        self.locate(self.b,offset=16)
        self.assertIsNotNone(self.s.gossip_candidate(self.a,self.b))
        result=self.s.run_conversation(self.a,self.b,'交流公告')
        self.assertIsNotNone(result)
        self.assertTrue(result['transcript'][0]['text'].startswith('听说'))
        self.assertTrue(any(m.type=='gossip' for m in self.s.memory.get_active(self.b,50)))
        speaker_events=self.s.game_events.timeline(self.a)['events']
        self.assertEqual({'notice_read','conversation_completed'},{event['kind'] for event in speaker_events})
        heard,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual('conversation_completed',heard['kind'])
        self.assertEqual('notice:'+notice['id'],heard['source_event_id'])

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
            self.assertEqual(400,client.post('/noticeboards/'+key+'/notices',json={'content':' '}).status_code)
            self.assertEqual(422,client.post('/noticeboards/'+key+'/notices',json={'content':'字'*501}).status_code)
            published=client.post('/noticeboards/'+key+'/notices',json={'content':'明天10:00组会'})
            self.assertEqual(200,published.status_code)
            self.assertNotIn('title',client.get('/noticeboards/'+key+'/notices').json()['notices'][0])
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

    def test_manual_activity_blocks_reading_before_and_after_arrival(self):
        self.publish();self.locate()
        self.s.assign_destination(self.a,self.board()['area'])
        self.s.planner.tick(time.monotonic(),0)
        with self.assertRaises(ValueError):self.s.boards.request_read(self.a,self.board()['id'])
        self.locate()  # 真实到达确认使手动行程进入停留阶段。
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.reading)
        self.assertNotIn(self.a,self.s.reading_waiting)
        self.assertTrue(self.s.assign_destination(self.a,'j3_lab'))
        self.assertNotIn(self.a,self.s.reading)
        self.assertNotIn(self.a,self.s.reading_waiting)

    def test_wider_pass_by_starts_reading_before_ordinary_activities(self):
        self.publish()
        board=self.board()
        # 西侧实验室门内 [29,27] 不触发。紧邻门外的走廊 [29,28] 会触发。
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id='R03',x=472,y=440,arrived=False)
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],x=472,y=456,arrived=False)
        self.s.planner.tick(time.monotonic(),0)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        event=self.s.game_events.current()[self.a]
        self.assertEqual('notice_read',event['kind'])
        self.assertEqual('medium',event['tier'])
        visit=self.s.planner.queue_social_visit(self.b,self.a,self.board()['area'])
        self.assertEqual('medium',visit['tier'])
        self.s.planner._make(self.a,'library','j3_lab',self.s.world.clock.now,'plan','有既定活动')
        self.s.planner.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)

    def test_notice_reading_suspends_an_existing_medium_activity(self):
        board=self.board()
        self.publish();self.locate()
        planner=self.s.planner
        item=planner._make(self.a,'research',board['area'],self.s.world.clock.now,'event','进行科研')
        planner._start(item)
        self.locate()
        item['dwell_minutes']=23
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.reading)
        self.assertEqual('notice_read',self.s.game_events.current()[self.a]['kind'])
        planner.tick(time.monotonic(),1)
        self.assertEqual(23,item['dwell_minutes'])
        self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        self.assertEqual(item['id'],self.s.game_events.current()[self.a]['id'])
        self.assertIn('恢复推进科研',self.s.game_events.current()[self.a]['schedule_reason'])

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

    def test_read_emits_one_bubble_linked_to_the_event(self):
        self.publish();self.locate();self.complete()
        self.s.boards.tick(time.monotonic(),0)
        line,=[e for e in self.s.bus.history() if e['kind']=='dialogue_line']
        event=self.s.game_events.timeline(self.a)['events'][0]
        self.assertEqual(event['id'],line['game_event_id'])
        self.assertTrue(line['text'].startswith('（自言自语）'))
        self.assertTrue(any(e['kind']=='notice_read' for e in self.s.bus.history()))

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
            self.assertLessEqual((point['x']-board['x'])**2+(point['y']-board['y'])**2,NOTICEBOARD.read_radius**2)
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
        event_id=state['current_event']['id']
        self.assertEqual('travelling',state['current_event']['phase'])
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],**point,arrived=False)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.a,self.s.notice_approaching)
        self.assertFalse(any(e['kind']=='dialogue_line' for e in self.s.bus.history()))
        self.s.world.clock.minutes+=6
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],**point,arrived=True)
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.assertIn(self.a,self.s.reading)
        self.assertEqual(event_id,self.s.game_events.current()[self.a]['id'])
        self.assertNotIn('target_position',self.s.world_snapshot()['npcs'][self.a])
        self.s.boards.tick(time.monotonic(),NOTICEBOARD.read_seconds)
        for future in list(self.s.boards.jobs.values()):future.result(timeout=5)
        self.assertNotIn(self.a,self.s.reading)
        event=self.s.game_events.timeline(self.a)['events'][0]
        self.assertEqual(event_id,event['id'])
        self.assertEqual(event['game_time']+6,event['content']['reading_started_at'])
        self.assertEqual(before['target_room_id'],self.s.world_snapshot()['npcs'][self.a]['target_room_id'])

    def test_approach_does_not_cross_walls_or_steal_manual_tasks_and_times_out(self):
        self.publish();board=self.board();key=board['id']
        self.locate(offset=1000)
        self.assertIsNone(self.s.boards.approach_point(self.a,key,time.monotonic()))
        self.s.report_arrival(self.a,map_id=board['map_id'],room_id=board['room_id'],x=board['x'],y=board['y'],arrived=True)
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
        self.s.planner._make(self.a,'manual','j3_lab',self.s.world.clock.now,'manual','紧急指派')
        self.s.boards.tick(time.monotonic(),0)
        self.assertNotIn(self.a,self.s.notice_approaching)
        self.assertFalse(self.s.reading)
        self.assertFalse(self.s.world.events)

    def test_delete_preserves_learned_content_and_cancels_unfinished_readers(self):
        notice=self.publish()
        self.locate()
        self.complete()
        self.locate(self.b,offset=96,travelling=True)
        self.s.boards.tick(time.monotonic(),0)
        self.assertIn(self.b,self.s.notice_approaching)
        path=f"/noticeboards/{self.board()['id']}/notices/{notice['id']}"
        with self.profile_client() as client:
            deleted=client.delete(path)
            self.assertEqual(200,deleted.status_code)
            self.assertEqual([],deleted.json()['notices'])
            self.assertEqual(404,client.delete(path).status_code)
        self.assertNotIn(self.b,self.s.notice_approaching)
        self.assertFalse(self.s.boards.unread(self.b,self.board()['id']))
        event,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual(notice['content'],event['content']['text'])
        self.assertIsNotNone(self.s.memory.get_by_id(event['id']))

    def resume_routine(self):
        self.s.planner.tick(time.monotonic(),0)
        routine=self.s.planner.current_item(self.a)
        area=AREAS[routine['area']]
        self.s.report_arrival(self.a,map_id=area['map_id'],room_id=area['room_id'],arrived=True,
                              activity_id=routine['id'])
        self.s.planner.tick(time.monotonic(),1)
        self.assertNotIn(self.a,self.s.planner.routine_turns)


if __name__=='__main__':unittest.main()

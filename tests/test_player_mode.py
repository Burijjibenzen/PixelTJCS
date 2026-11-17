"""任务09：真实SQLite与HTTP，模型替身；不接触开发中的身份和记忆。"""
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_social as fixtures
from backend.player_mode import PlayerError, PlayerMode, PlayerRelationImpact
from backend.config import PLAYER, SOCIAL
from backend.world import AREAS


class PlayerModeTests(unittest.TestCase):
    tearDown = fixtures.SocialTests.tearDown
    profile_client = fixtures.SocialTests.profile_client

    def setUp(self):
        fixtures.SocialTests.setUp(self)
        room=AREAS[self.area]
        for npc in self.s.npcs:
            self.s.report_arrival(npc,map_id=room['map_id'],room_id=room['room_id'],x=0,y=0)

    def join(self, name='访客甲'):
        if not hasattr(self,'tokens'):self.tokens={}
        result=self.s.player.join(name,player_token=self.tokens.get(name))
        self.tokens[name]=result['player_token']
        room=AREAS[self.area]
        self.s.player.report_position(result['session_id'],room['map_id'],room['room_id'],0,0)
        return result['session_id']

    def send(self, message='一起讨论研究计划吧', npc=None, session=None, request='first'):
        session = session or self.s.player.state()['session_id']
        item = self.s.player.submit(session, npc or self.a, message, request)
        future = self.s.player.jobs.get(item['id'])
        if future:future.result(timeout=10)
        return self.s.player.turn(session, item['id'])

    def test_default_off_join_leave_idempotency_and_npc_roster_unchanged(self):
        self.assertFalse(self.s.player.state()['enabled'])
        with self.assertRaises(PlayerError):self.s.chat_with_npc(self.a, '你好')
        original = dict(self.s.world.npc_area)
        session = self.join()
        self.assertEqual(session, self.s.player.join('访客甲', session)['session_id'])
        self.assertEqual(session,self.join())
        with self.assertRaises(PlayerError):self.s.player.join('访客甲')
        self.assertEqual(10, len(self.s.npcs))
        self.assertEqual(original, self.s.world.npc_area)
        self.assertEqual(10, len(self.s.player.state()['relations']))
        with self.assertRaises(PlayerError):self.join('访客乙')
        self.assertFalse(self.s.player.leave(session)['enabled'])
        self.assertFalse(self.s.player.leave(session)['enabled'])
        with self.assertRaises(PlayerError):self.join(self.a)
        with self.assertRaises(PlayerError):self.join(' \n ')

    def test_engagement_holds_npc_through_reply_and_releases_on_close(self):
        session=self.join()
        state=self.s.player.engage(session,self.a)
        self.assertEqual(self.a,state['engaged_npc'])
        self.assertFalse(self.s.available(self.a,self.b))
        self.assertTrue(self.s.world_snapshot()['npcs'][self.a]['busy'])
        self.assertEqual('与玩家交谈',self.s.world_snapshot()['npcs'][self.a]['activity']['title'])
        with patch.object(self.s.npcs[self.a],'speak',return_value='你好，我停下来聊聊。'):
            turn=self.send(session=session)
            self.assertEqual('completed',turn['status'])
        self.assertFalse(self.s.available(self.a,self.b))
        self.assertFalse(any(e['kind']=='dialogue_finished' and e['conversation_id']==turn['id']
                             for e in self.s.bus.history()))
        self.s.player.disengage(session)
        self.assertTrue(any(e['kind']=='dialogue_finished' and e['conversation_id']==turn['id']
                            for e in self.s.bus.history()))
        self.assertTrue(self.s.available(self.a,self.b))

    def test_engagement_expires_after_abandoned_window(self):
        session=self.join()
        self.s.player.engage(session,self.a)
        self.s.player.expire_engagement(time.monotonic()+PLAYER.engage_lease_seconds+1)
        self.assertIsNone(self.s.player.state()['engaged_npc'])
        self.assertTrue(self.s.available(self.a,self.b))

    def test_server_checks_fresh_player_and_npc_distance_for_engage_and_chat(self):
        session=self.join()
        room=AREAS[self.area]
        self.s.player.report_position(session,room['map_id'],room['room_id'],1000,1000)
        with self.assertRaises(PlayerError):self.s.player.engage(session,self.a)
        with self.assertRaises(PlayerError):self.s.player.submit(session,self.a,'远处消息','far')
        self.s.player.report_position(session,room['map_id'],room['room_id'],0,0)
        self.s.positions[self.a]['at']=time.monotonic()-SOCIAL.position_max_age_seconds-1
        with self.assertRaises(PlayerError):self.s.player.engage(session,self.a)
        self.s.report_arrival(self.a,map_id=room['map_id'],room_id=room['room_id'],x=0,y=0)
        self.s.player.engage(session,self.a)
        self.s.player.position['at']=time.monotonic()-SOCIAL.position_max_age_seconds-1
        with self.assertRaises(PlayerError):self.s.player.submit(session,self.a,'过期消息','stale')

    def test_rejected_chat_job_keeps_existing_engagement(self):
        session=self.join()
        self.s.player.engage(session,self.a)
        with patch.object(self.s.player.pool,'submit',side_effect=RuntimeError('closed')):
            with self.assertRaises(PlayerError) as error:
                self.s.player.submit(session,self.a,'你好','rejected')
        self.assertEqual(503,error.exception.status)
        self.assertEqual(self.a,self.s.player.state(session)['engaged_npc'])
        self.assertIn(self.a,self.s.busy)
        self.assertFalse(self.s.available(self.a,self.b))
        self.s.player.disengage(session)
        self.assertTrue(self.s.available(self.a,self.b))

    def test_player_engagement_waits_existing_npc_meeting_then_resumes(self):
        session=self.join()
        destination=next(area for area in AREAS if AREAS[area]['id'] != AREAS[self.area]['id'])
        meeting=self.s.request_meeting(self.a,self.b,destination)
        self.s.scheduler.meetings(time.monotonic(),1)
        self.assertEqual('moving',meeting['state'])
        target=meeting['area']
        self.s.player.engage(session,self.a)
        elapsed=meeting['travel_seconds']
        self.s.scheduler.meetings(time.monotonic(),10)
        self.assertEqual('waiting',meeting['state'])
        self.assertEqual(target,meeting['area'])
        self.assertEqual(elapsed,meeting['travel_seconds'])
        self.s.player.disengage(session)
        self.s.scheduler.meetings(time.monotonic(),1)
        self.assertEqual('moving',meeting['state'])

    def test_waiting_meeting_expires_instead_of_blocking_queue_forever(self):
        session=self.join()
        meeting=self.s.request_meeting(self.a,self.b)
        self.s.player.engage(session,self.a)
        self.s.scheduler.meetings(time.monotonic(),SOCIAL.meeting_wait_timeout-1)
        self.assertEqual('waiting',meeting['state'])
        self.s.scheduler.meetings(time.monotonic(),2)
        self.assertEqual('failed',meeting['state'])
        self.assertNotIn(meeting,self.s.active_meetings())
        self.assertIn(self.a,self.s.busy)

    def test_full_transcript_in_both_personal_memories_and_only_participants_know(self):
        session = self.join();player = self.s.player.state()['player']
        with patch.object(self.s.llm, 'chat', return_value='我会带着实验结果来一起讨论。'):
            item = self.send(session=session)
        self.assertEqual('completed', item['status'])
        self.assertEqual(2, len(item['transcript']))
        self.assertEqual(item['reply'], item['transcript'][1]['text'])
        self.assertEqual({self.a, player['id']}, set(item['memories']))
        for agent, memory_id in item['memories'].items():
            memory = self.s.memory.get_by_id(memory_id)
            self.assertEqual(agent, memory.agent)
            self.assertIn(item['message'], memory.content)
            self.assertIn(item['reply'], memory.content)
        self.assertFalse(self.s.world.events)
        self.assertFalse(self.s.memory.get_active(self.b, 50))
        self.assertEqual(1, len(self.s.player.memories(session)['active']))
        self.assertEqual(0, item['relation']['delta'])
        self.assertIn('保持原好感度', item['relation']['reason'])

    def test_real_context_contains_previous_reply_without_other_npc_dialogue(self):
        session = self.join()
        calls = []
        def reply(system, user, **kw):
            calls.append(user)
            return '第一位角色的独有答复' if len(calls) == 1 else '继续讨论'
        with patch.object(self.s.llm, 'chat', side_effect=reply):
            self.send(message='仅告诉甲的研究线索', session=session, request='one')
            self.send(message='刚才你说的是什么意思', session=session, request='two')
            self.send(message='另一位你好', npc=self.b, session=session, request='three')
        self.assertIn('第一位角色的独有答复', calls[1])
        self.assertIn('仅告诉甲的研究线索', calls[1])
        self.assertNotIn('第一位角色的独有答复', calls[2])
        self.assertNotIn('仅告诉甲的研究线索', calls[2])

    def test_duplicate_requests_and_relation_effect_are_exactly_once(self):
        session = self.join();player = self.s.player.state()['player']['id']
        impact = PlayerRelationImpact(delta=3, reason='认真回应了研究建议')
        with patch.object(self.s.llm, 'chat_json', return_value=impact), patch.object(self.s.npcs[self.a], 'speak', return_value='好建议') as speak:
            first = self.send(session=session)
            room=AREAS[self.area]
            self.s.player.report_position(session,room['map_id'],room['room_id'],1000,1000)
            second = self.send(session=session)
            self.assertEqual(first, second)
            self.assertEqual(1, speak.call_count)
        self.assertEqual(53, self.s.relations.get(self.a, player))
        self.s.relations.apply_delta_once(self.a, player, 3, impact.reason, first['id'])
        self.assertEqual(53, self.s.relations.get(self.a, player))
        with self.s.database() as db:
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM memories WHERE id LIKE 'player-chat:%'").fetchone()[0])
        with self.assertRaises(PlayerError):self.send(message='不同内容', session=session)

    def test_in_flight_dedup_and_busy_do_not_block_world_or_other_social_pair(self):
        session = self.join();entered = threading.Event();release = threading.Event()
        def slow_reply(*a, **kw):
            entered.set();release.wait(10);return '刚刚认真想了想'
        try:
            with patch.object(self.s.npcs[self.a], 'speak', side_effect=slow_reply) as speak:
                item = self.s.player.submit(session, self.a, '你好', 'concurrent')
                self.assertTrue(entered.wait(3))
                with ThreadPoolExecutor(max_workers=6) as executor:
                    results = list(executor.map(lambda _:self.s.player.submit(session,self.a,'你好','concurrent'), range(6)))
                self.assertTrue(all(r['id'] == item['id'] for r in results))
                self.assertEqual(1, speak.call_count)
                self.assertFalse(self.s.available(self.a,self.b))
                self.assertTrue(self.s.available(self.c,self.d))
                before = self.s.world.clock.now
                with patch.object(self.s.scheduler, 'natural'):
                    self.s.scheduler.tick(time.monotonic(), 1)
                self.assertGreater(self.s.world.clock.now,before)
                self.assertEqual('与玩家交谈',self.s.world_snapshot()['npcs'][self.a]['activity']['title'])
                with self.assertRaises(PlayerError):self.s.player.submit(session,self.b,'另一轮','new')
                release.set();self.s.player.jobs[item['id']].result(timeout=10)
        finally:release.set()
        self.assertNotIn(self.a,self.s.busy)

    def test_leave_during_reply_preserves_accepted_turn_and_rejoin_identity(self):
        session = self.join();player = self.s.player.state()['player'];release = threading.Event()
        try:
            with patch.object(self.s.npcs[self.a], 'speak', side_effect=lambda *a:release.wait(10) and '回复已完成'):
                item = self.s.player.submit(session,self.a,'离开前打个招呼','leave')
                self.assertFalse(self.s.player.leave(session)['enabled'])
                self.assertIn(self.a,self.s.busy)
                with self.assertRaises(PlayerError):self.join()
                self.assertEqual(item['id'],self.s.player.submit(session,self.a,'离开前打个招呼','leave')['id'])
                with self.assertRaises(PlayerError):self.s.player.submit(session,self.a,'已经离开','different')
                release.set();self.s.player.jobs[item['id']].result(timeout=10)
        finally:release.set()
        self.assertEqual('completed',self.s.player.turn(session,item['id'])['status'])
        new_session = self.join()
        self.assertNotEqual(session,new_session)
        self.assertEqual(player,self.s.player.state()['player'])
        with self.assertRaises(PlayerError):self.s.player.leave(session)
        self.assertTrue(self.s.player.state()['enabled'])
        self.assertEqual(1,len(self.s.player.history(new_session,self.a)))

    def test_busy_reading_reflecting_approaching_paused_and_unknown_npc(self):
        session = self.join()
        for group in (self.s.busy,self.s.reading,self.s.reading_waiting,self.s.reflecting,self.s.notice_approaching):
            group.add(self.a)
            try:
                with self.assertRaises(PlayerError):self.s.player.submit(session,self.a,'你好','blocked')
            finally:group.discard(self.a)
        self.s.paused = True
        with self.assertRaises(PlayerError):self.s.player.submit(session,self.a,'你好','paused')
        self.s.paused = False
        with self.assertRaises(PlayerError) as error:self.s.player.submit(session,'不存在','你好','missing')
        self.assertEqual(404,error.exception.status)

    def test_failure_releases_actor_and_preserves_generated_reply_on_storage_failure(self):
        session = self.join()
        with patch.object(self.s.npcs[self.a],'speak',side_effect=RuntimeError('INTERNAL_SECRET')):
            item = self.send(session=session,request='failed')
        self.assertEqual('failed',item['status'])
        self.assertNotIn('INTERNAL_SECRET',json.dumps(item))
        self.assertNotIn(self.a,self.s.busy)
        self.assertIsNone(self.s.player.pending)
        with patch.object(self.s.memory,'add',side_effect=RuntimeError('disk')):
            item = self.send(session=session,request='storage')
        self.assertEqual('failed',item['status'])
        self.assertTrue(item['reply'])
        self.assertEqual(2,len(item['transcript']))
        self.assertFalse(item['memories'])
        self.assertIsNone(item['relation'])
        self.assertNotIn(self.a,self.s.busy)

    def test_invalid_relation_assessment_uses_explicit_neutral_fallback(self):
        session = self.join()
        with patch.object(self.s.llm,'chat_json',return_value={'delta':float('nan'),'reason':'无效'}):
            item = self.send(session=session)
        self.assertEqual('completed',item['status'])
        self.assertEqual(0,item['relation']['delta'])
        self.assertEqual(50,item['relation']['score'])

    def test_model_unavailable_still_saves_both_sides_without_inventing_relation_change(self):
        session = self.join()
        with patch.object(self.s.llm,'chat',return_value=None),patch.object(self.s.llm,'chat_json',return_value=None):
            item = self.send(session=session)
        self.assertEqual('completed',item['status'])
        self.assertTrue(item['reply'])
        self.assertEqual(2,len(item['transcript']))
        self.assertEqual(2,len(item['memories']))
        self.assertEqual(0,item['relation']['delta'])

    def test_long_npc_reply_is_preserved_in_history_and_both_memories(self):
        session = self.join()
        reply = '研究细节。'*300+'最后一个结论也必须保存。'
        with patch.object(self.s.npcs[self.a],'speak',return_value=reply):
            item = self.send(session=session)
        self.assertEqual(reply,item['reply'])
        self.assertEqual(reply,item['transcript'][1]['text'])
        for memory_id in item['memories'].values():
            self.assertIn(reply,self.s.memory.get_by_id(memory_id).content)

    def test_restart_defaults_off_preserves_completed_history_and_marks_interrupted(self):
        session = self.join();item = self.send(session=session)
        player = self.s.player.state()['player']
        interrupted = dict(item,id='interrupted',request_id='crash',status='processing',reply=None,transcript=[],memories={},relation=None)
        with self.s.database() as db:
            db.execute('INSERT INTO player_turns VALUES(?,?,?,?,?,?)',('interrupted',session,'crash',self.a,item['message'],json.dumps(interrupted)))
        self.s.player.stop();self.s.player = PlayerMode(self.s)
        self.assertFalse(self.s.player.state()['enabled'])
        self.assertEqual('failed',self.s.player.turn(session,'interrupted')['status'])
        self.assertEqual('completed',self.s.player.turn(session,item['id'])['status'])
        new_session = self.join()
        self.assertEqual(player,self.s.player.state()['player'])
        self.assertEqual(1,len(self.s.player.memories(new_session)['active']))

    def test_history_memory_and_turns_are_separated_between_players(self):
        alice = self.join();item = self.send(session=alice)
        self.s.player.leave(alice);bob = self.join('访客乙')
        self.assertFalse(self.s.player.history(bob,self.a))
        self.assertFalse(self.s.player.memories(bob)['active'])
        with self.assertRaises(PlayerError):self.s.player.turn(bob,item['id'])
        self.assertEqual(1,len(self.s.player.history(alice,self.a)))

    def test_http_contract_validation_and_async_responsiveness(self):
        release = threading.Event()
        try:
            with self.profile_client() as client:
                self.assertFalse(client.get('/player').json()['enabled'])
                self.assertEqual(409,client.post('/chat',json={'npc':self.a,'message':'你好'}).status_code)
                self.assertEqual(400,client.post('/player/join',json={'name':self.a}).status_code)
                self.assertEqual(422,client.post('/player/join',json={'name':'名'*25}).status_code)
                joined = client.post('/player/join',json={'name':'测试访客'}).json()
                session = joined['session_id']
                room=AREAS[self.area]
                position=dict(session_id=session,map_id=room['map_id'],room_id=room['room_id'],x=0,y=0)
                self.assertEqual(409,client.post('/player/engage',json={'session_id':session,'npc':self.a}).status_code)
                self.assertEqual(409,client.post('/player/chat',json=dict(session_id=session,npc=self.a,
                    message='没有位置',request_id='no-position')).status_code)
                self.assertEqual(400,client.post('/player/position',json=dict(position,room_id='不存在')).status_code)
                self.assertEqual(200,client.post('/player/position',json=position).status_code)
                self.assertIsNone(client.get('/player').json()['session_id'])
                self.assertIsNone(client.get('/player').json()['player'])
                self.assertEqual(session, client.get('/player',headers={'X-Player-Session':session}).json()['session_id'])
                self.assertEqual(409,client.post('/player/join',json={'name':'测试访客'}).status_code)
                self.assertEqual(session,client.post('/player/join',json={'name':'测试访客',
                    'player_token':joined['player_token']}).json()['session_id'])
                self.assertEqual(self.a,client.post('/player/engage',json={'session_id':session,
                    'npc':self.a}).json()['engaged_npc'])
                payload = dict(session_id=session,npc=self.a,message='接口你好',request_id='http')
                self.assertEqual(422,client.post('/player/chat',json=dict(payload,message='字'*501)).status_code)
                self.assertEqual(400,client.post('/player/chat',json=dict(payload,message=' ')).status_code)
                with patch.object(self.s.npcs[self.a],'speak',side_effect=lambda *a:release.wait(10) and '异步回复'):
                    started = time.monotonic()
                    response = client.post('/player/chat',json=payload)
                    self.assertEqual(202,response.status_code)
                    item = response.json()
                    self.assertLess(time.monotonic()-started,1)
                    self.assertEqual(200,client.get('/world').status_code)
                    self.assertEqual(200,client.get('/player/turns/'+item['id'],params={'session_id':session}).status_code)
                    release.set();self.s.player.jobs[item['id']].result(timeout=10)
                history = client.get('/player/history',params={'session_id':session,'npc':self.a}).json()['turns']
                self.assertEqual(2,len(history[0]['transcript']))
                self.assertEqual(1,len(client.get('/player/memory',params={'session_id':session}).json()['active']))
                self.assertEqual(404,client.get('/player/history',params={'session_id':'unknown'}).status_code)
                legacy = client.post('/chat',json=dict(payload,request_id='legacy')).json()
                self.assertEqual('completed',legacy['status'])
                self.assertTrue(legacy['reply'])
                self.assertIsNone(client.post('/player/disengage',json={'session_id':session}).json()['engaged_npc'])
                self.assertFalse(client.post('/player/leave',json={'session_id':session}).json()['enabled'])
                self.assertEqual(403,client.post('/player/join',json={'name':'测试访客'}).status_code)
                self.assertEqual(200,client.post('/player/join',json={'name':'测试访客',
                    'player_token':joined['player_token']}).status_code)
        finally:release.set()


if __name__ == '__main__':unittest.main()

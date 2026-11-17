"""M3: 真实 LangGraph/SQLite/WebSocket 集成，模型为可控替身，不消耗 API。"""
import gc
import logging
import tempfile
import threading
import time
import unittest
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.config import MEMORY, SOCIAL
from backend.memory import MemoryStore
from backend.relations import RelationGraph
from backend.social import SocialService, install
from backend.agent_runtime import SocialDecision
from backend.world import AREAS, ROOMS
logging.disable(logging.CRITICAL)

class FakeLLM:
    def chat(self,*a,**kw):return '今天实验有了新进展。'
    def chat_json(self,*a,**kw):return None
    def embed(self,*a,**kw):return None

class SocialTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[2]/'.cache'/'tests';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base)
        cfg=replace(MEMORY,db_path=str(Path(self.tmp.name)/'memory.db'))
        self.patches=[patch('backend.service.LLMClient',FakeLLM),
            patch('backend.service.MemoryStore',lambda llm:MemoryStore(llm,cfg)),
            patch('backend.service.RelationGraph',lambda:RelationGraph(cfg.db_path))]
        for p in self.patches:p.start()
        self.s=SocialService();self.a,self.b,self.c,self.d=list(self.s.npcs)[:4]
        self.area=next(iter(AREAS))
        for n in self.s.npcs:self.s.world.set_area(n,self.area)
        self.s.seen={n:time.monotonic() for n in self.s.npcs}
    def tearDown(self):
        self.s.stop()
        for p in reversed(self.patches):p.stop()
        gc.collect();self.tmp.cleanup()
    def release(self):
        self.s.busy.clear();self.s.live_lines.clear()
    def tick(self,elapsed=0):
        with patch.object(SOCIAL,'base_chat_probability',0):self.s.scheduler.tick(time.monotonic(),elapsed)
    def finish(self):
        for future in list(self.s.scheduler.jobs.values()):future.result(timeout=20)
    def report_positions(self,names=None):
        for name in names or self.s.npcs:
            room=AREAS[self.s.world.area_of(name)]
            self.s.report_arrival(name,map_id=room['map_id'],room_id=room['room_id'],x=0,y=0)
    def client(self):
        app=FastAPI();install(app,lambda:self.s)
        app.get('/health')(lambda:{'status':'ok'})
        return TestClient(app)

    @contextmanager
    def profile_client(self):
        from backend import api
        # 使用真实API路由，但不启动调度器或接触开发数据库。
        with patch.object(api,'_service',self.s):
            client=TestClient(api.app)
            try:yield client
            finally:client.close()

    def test_public_profiles_exclude_private_fields_without_model_calls(self):
        before=[]
        for card in self.s.cards.values():
            card.secrets.append('PRIVATE_SENTINEL_'+card.name)
            card.internal_note='FUTURE_PRIVATE_FIELD'
        with self.s.database() as db:before=db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
        expected={'name','role','title','expertise','mbti','traits','tone','catchphrase','goals','schedule','location','destination','game_time'}
        with self.profile_client() as client,patch.object(self.s.llm,'chat',side_effect=AssertionError('must not call model')),patch.object(self.s.llm,'chat_json',side_effect=AssertionError('must not call model')),patch.object(self.s.llm,'embed',side_effect=AssertionError('must not call model')):
            for name,card in self.s.cards.items():
                response=client.get('/npcs/'+name+'/profile')
                self.assertEqual(200,response.status_code)
                data=response.json()
                self.assertEqual(expected,set(data))
                self.assertEqual(name,data['name'])
                for secret in card.secrets:self.assertNotIn(secret,response.text)
                self.assertNotIn('FUTURE_PRIVATE_FIELD',response.text)
            self.assertEqual(404,client.get('/npcs/不存在的角色/profile').status_code)
        with self.s.database() as db:self.assertEqual(before,db.execute('SELECT COUNT(*) FROM memories').fetchone()[0])

    def test_public_profile_projection_does_not_change_private_prompt(self):
        card=self.s.cards[self.a]
        hint,system=card.render_secret_hint(),card.render_system_prompt()
        result=card.public_profile()
        result['traits'].append('外部修改')
        result['schedule']['23:59']='j3_pantry'
        result['goals']['short_term']='外部目标'
        self.assertNotIn('外部修改',card.traits)
        self.assertNotIn('23:59',card.schedule)
        self.assertNotEqual('外部目标',card.goals_short)
        self.assertEqual(hint,card.render_secret_hint())
        self.assertEqual(system,card.render_system_prompt())
        self.assertNotIn('secrets',result)
        self.assertNotIn('initial_relations',result)
        self.assertNotIn('example_lines',result)

    def test_public_profile_defaults_and_unknown_schedule_location(self):
        from backend.persona import PersonaCard
        path=Path(self.tmp.name)/'minimal.json'
        path.write_text(json.dumps(dict(name=self.a,role='student',title='最小资料'),ensure_ascii=False),encoding='utf-8')
        card=PersonaCard.from_file(str(path))
        self.s.cards[self.a]=card
        with self.profile_client() as client:
            data=client.get('/npcs/'+self.a+'/profile').json()
            self.assertEqual([],data['traits'])
            self.assertEqual([],data['schedule'])
            self.assertEqual({'short_term':'','long_term':''},data['goals'])
            card.schedule={'09:00':'not-a-room','08:00':'j3_lab'}
            response=client.get('/npcs/'+self.a+'/profile')
            self.assertEqual(200,response.status_code)
            rows=response.json()['schedule']
            self.assertEqual(['08:00','09:00'],[row['time'] for row in rows])
            self.assertEqual('未知地点',rows[1]['location']['name'])

    def test_public_profile_uses_actual_location_and_current_destination(self):
        actual=next(room for room in ROOMS.values() if room['map_id']=='zhixinguan_floor2')
        target=next(room for room in ROOMS.values() if room['map_id']=='jishilou_floor4')
        self.s.world.set_area(self.a,actual['id'])
        self.s.world.assign_override(self.a,target['id'])
        with self.profile_client() as client:
            data=client.get('/npcs/'+self.a+'/profile').json()
            self.assertEqual(actual['map_id'],data['location']['map_id'])
            self.assertEqual(actual['room_id'],data['location']['room_id'])
            self.assertEqual(target['room_id'],data['destination']['room_id'])
            self.assertEqual(target['map_id'],data['destination']['map_id'])
            self.s.world.set_area(self.a,target['id'])
            self.assertEqual(target['room_id'],client.get('/npcs/'+self.a+'/profile').json()['location']['room_id'])

    def test_public_profile_response_schema_filters_future_internal_fields(self):
        data=self.s.public_npc_profile(self.a)
        data['secrets']=['PRIVATE_ROOT']
        data['goals']['secrets']='PRIVATE_GOAL'
        data['location']['raw_prompt']='PRIVATE_LOCATION'
        with self.profile_client() as client,patch.object(self.s,'public_npc_profile',return_value=data):
            response=client.get('/npcs/'+self.a+'/profile')
            self.assertEqual(200,response.status_code)
            for marker in ('PRIVATE_ROOT','PRIVATE_GOAL','PRIVATE_LOCATION'):
                self.assertNotIn(marker,response.text)

    def test_graph_nodes_real_execution_and_cooldown(self):
        before=self.s.relations.get(self.a,self.b)
        result=self.s.run_conversation(self.a,self.b,'测试相遇')
        self.assertIsNotNone(result)
        self.assertEqual(6,len(result['transcript']))
        self.assertEqual(before+1,self.s.relations.get(self.a,self.b))
        nodes=self.s.engine.graph.get_graph().nodes
        self.assertTrue({'opener','reply_a','reply_b','memory_a','assess','reflect_a'}<=set(nodes))
        config={'configurable':{'thread_id':result['conversation_id']}}
        history=list(self.s.engine.graph.get_state_history(config))
        self.assertGreater(len(history),10)
        self.assertEqual((),self.s.engine.graph.get_state(config).next)
        self.release()
        self.assertIsNone(self.s.run_conversation(self.a,self.b,'冷却'))

    def test_scheduler_calls_service_then_graph(self):
        self.report_positions()
        with patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0),patch.object(self.s,'run_conversation',wraps=self.s.run_conversation) as entry:
            self.s.scheduler.tick(time.monotonic(),0.5);self.finish()
        self.assertEqual(2,entry.call_count)
        self.assertFalse(self.s.busy)
        self.assertEqual(485,self.s.world.clock.now)

    def test_model_can_decline_natural_chat(self):
        self.report_positions()
        with patch.object(self.s.llm,'chat_json',return_value=SocialDecision(action='continue_schedule')),patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0):
            self.s.scheduler.tick(time.monotonic());self.finish()
        self.assertFalse(self.s.busy)
        self.assertFalse(any(e['kind']=='dialogue_line' for e in self.s.bus.history()))

    def test_spatial_encounter_requires_same_map_room_radius_and_fresh_coordinates(self):
        self.report_positions([self.a,self.b])
        room=AREAS[self.area]
        self.assertTrue(self.s.nearby(self.a,self.b))
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=SOCIAL.encounter_radius,y=0)
        self.assertTrue(self.s.nearby(self.a,self.b))
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=SOCIAL.encounter_radius+.1,y=0)
        self.assertFalse(self.s.nearby(self.a,self.b))
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=SOCIAL.encounter_radius*.8,y=SOCIAL.encounter_radius*.8)
        self.assertFalse(self.s.nearby(self.a,self.b))
        other=next(r for r in ROOMS.values() if r['map_id']==room['map_id'] and r['id']!=room['id'])
        self.s.report_arrival(self.b,map_id=other['map_id'],room_id=other['room_id'],x=0,y=0)
        self.assertFalse(self.s.nearby(self.a,self.b))
        other_floor=next(r for r in ROOMS.values() if r['map_id']!=room['map_id'] and r['room_id']==room['room_id'])
        self.s.report_arrival(self.b,map_id=other_floor['map_id'],room_id=other_floor['room_id'],x=0,y=0)
        self.assertFalse(self.s.nearby(self.a,self.b))
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=0,y=0)
        self.s.positions[self.a]['at']-=SOCIAL.position_max_age_seconds+1
        self.s.heartbeat()
        self.assertFalse(self.s.nearby(self.a,self.b))

    def test_corridor_far_pair_is_not_a_natural_candidate_but_near_pair_is(self):
        self.report_positions([self.a,self.b]);room=AREAS[self.area]
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=1000,y=0)
        before=len(self.s.memory.get_active(self.a,50))
        with patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0),patch.object(self.s.scheduler,'submit') as submit:
            self.s.scheduler.natural(time.monotonic())
            submit.assert_not_called()
            self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=32,y=0)
            self.s.scheduler.natural(time.monotonic())
            submit.assert_called_once()
        self.assertEqual(before,len(self.s.memory.get_active(self.a,50)))

    def test_natural_encounter_without_coordinates_does_not_fall_back_to_whole_room(self):
        self.assertFalse(self.s.nearby(self.a,self.b))
        with patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0),patch.object(self.s.scheduler,'submit') as submit:
            self.s.scheduler.natural(time.monotonic())
            submit.assert_not_called()
        # 双方均为旧客户端时，显式请求保留原区域兼容，不自动发起自然对话。
        self.assertTrue(self.s.valid_pair(self.a,self.b))

    def test_movement_during_model_decision_prevents_remote_chat_and_gossip(self):
        self.report_positions([self.a,self.b]);room=AREAS[self.area]
        before=len(self.s.memory.get_active(self.a,50))
        original=self.s.llm.chat_json
        def move_before_answer(*args,**kwargs):
            if args[2] is SocialDecision:
                self.assertIn(self.b,self.s.busy)
                self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=1000,y=0)
                return SocialDecision(action='chat',share_gossip=True)
            return original(*args,**kwargs)
        with patch.object(self.s.llm,'chat_json',side_effect=move_before_answer):
            self.s.scheduler.submit(self.a,self.b,'自然相遇')
            self.finish()
        self.assertFalse(self.s.busy)
        self.assertEqual(1000,self.s.positions[self.b]['x'])
        self.assertEqual(before,len(self.s.memory.get_active(self.a,50)))
        self.assertFalse(any(e['kind'] in ('dialogue_line','conversation_started') for e in self.s.bus.history()))

    def test_meeting_in_corridor_waits_until_reported_positions_are_near(self):
        self.report_positions([self.a,self.b]);room=AREAS[self.area]
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=1000,y=0)
        meeting=self.s.request_meeting(self.a,self.b)
        self.s.scheduler.meetings(time.monotonic(),0)
        self.assertEqual('moving',meeting['state'])
        self.assertFalse(self.s.busy)
        self.s.report_arrival(self.b,map_id=room['map_id'],room_id=room['room_id'],x=16,y=0)
        self.s.scheduler.meetings(time.monotonic(),0);self.finish()
        self.assertEqual('completed',meeting['state'])

    def test_position_report_api_rejects_partial_and_nonfinite_coordinates(self):
        room=AREAS[self.area]
        body=dict(npc=self.a,map_id=room['map_id'],room_id=room['room_id'],x=8,y=12)
        with self.profile_client() as client:
            self.assertEqual(200,client.post('/world/report',json=body).status_code)
            self.assertEqual(8,self.s.positions[self.a]['x'])
            partial=dict(body);partial.pop('y')
            self.assertEqual(400,client.post('/world/report',json=partial).status_code)
            raw=json.dumps(dict(body,x='NaN'))
            self.assertEqual(422,client.post('/world/report',content=raw,headers={'content-type':'application/json'}).status_code)
            self.assertEqual(8,self.s.positions[self.a]['x'])

    def test_relationship_changes_probability(self):
        with patch.object(self.s.relations,'get',return_value=0):low=self.s.scheduler.probability(self.a,self.b)
        with patch.object(self.s.relations,'get',return_value=100):high=self.s.scheduler.probability(self.a,self.b)
        self.assertGreater(high,low)

    def test_decision_contains_schedule_goals_relation(self):
        with patch.object(self.s.llm,'chat_json',return_value=None) as call:
            self.s.engine.agents[self.a].graph.invoke(dict(task='choose',other=self.b,co_located=True,forced=False))
        prompt=call.call_args.args[1]
        self.assertIn(self.s.cards[self.a].goals_short,prompt)
        self.assertIn('日程目标',prompt);self.assertIn('好感度',prompt)
        self.assertNotIn(self.s.cards[self.b].secrets[0] if self.s.cards[self.b].secrets else 'IMPOSSIBLE',prompt)

    def test_no_teleport_between_maps(self):
        other=next(x for x in ROOMS.values() if x['map_id']!=AREAS[self.area]['map_id'])
        self.s.world.set_area(self.b,other['id'])
        self.assertIsNone(self.s.run_conversation(self.a,self.b,'不能传送'))
        self.assertEqual(other['id'],self.s.world.area_of(self.b))

    def test_meeting_waits_busy_and_cooldown(self):
        self.s.world.mark_chatted(self.a,self.b);self.s.busy.add(self.b)
        m=self.s.request_meeting(self.a,self.b)
        self.assertIs(m,self.s.request_meeting(self.b,self.a))
        self.tick();self.assertEqual('waiting',m['state'])
        self.release();self.tick();self.assertEqual('cooldown',m['state'])
        self.s.advance_time(121,maintenance=False);self.tick();self.finish()
        self.assertEqual('completed',m['state'])

    def test_meeting_target_survives_schedule_cancel_timeout(self):
        other=next(x for x in AREAS if AREAS[x]['id']!=self.area)
        self.s.world.set_area(self.b,other)
        m=self.s.request_meeting(self.a,self.b);self.tick()
        self.s.advance_time(500,maintenance=False)
        self.assertEqual(AREAS[self.area]['room_id'],self.s.world_snapshot()['npcs'][self.b]['target_room_id'])
        self.s.cancel_meeting(m['id']);self.assertEqual('cancelled',m['state'])
        m=self.s.request_meeting(self.a,self.b);self.tick(301)
        self.assertEqual('failed',m['state'])

    def test_two_workers_no_shared_actor_and_third_queued(self):
        names=list(self.s.npcs)[:6]
        meetings=[self.s.request_meeting(*names[i:i+2]) for i in (0,2,4)]
        barrier=threading.Barrier(2);original=self.s.engine.run
        def concurrent(*a,**kw):barrier.wait(timeout=10);return original(*a,**kw)
        with patch.object(self.s.engine,'run',side_effect=concurrent):self.tick();self.finish()
        self.assertEqual(['completed','completed','ready'],[m['state'] for m in meetings])
        self.assertFalse(self.s.busy)
        self.tick();self.finish()
        self.assertEqual('completed',meetings[2]['state'])

    def test_gossip_actual_transcript_source_dedup_and_hop_limit(self):
        self.s.run_conversation(self.a,self.b,'实验结果');self.release()
        result=self.s.run_conversation(self.b,self.c,'转述')
        self.assertTrue(result['transcript'][0]['text'].startswith('听说'))
        self.assertEqual(self.c,result['gossip']['recipient'])
        self.assertIsNone(self.s.gossip_candidate(self.b,self.c))
        self.assertTrue(any(m.type=='gossip' for m in self.s.memory.get_active(self.c,50)))
        with self.s.database() as db:db.execute('UPDATE gossip SET hops=3 WHERE holder=?',(self.c,))
        self.assertIsNone(self.s.gossip_candidate(self.c,self.d))

    def test_declining_gossip_keeps_information_private(self):
        self.s.run_conversation(self.a,self.b,'实验结果');self.release()
        original=self.s.llm.chat_json
        def decide(*a,**kw):
            if a[2] is SocialDecision:return SocialDecision(action='chat',share_gossip=False)
            return original(*a,**kw)
        with patch.object(self.s.llm,'chat_json',side_effect=decide):result=self.s.run_conversation(self.b,self.c,'普通聊天')
        self.assertFalse(result['gossip'])
        self.assertFalse(any(m.type=='gossip' for m in self.s.memory.get_active(self.c,50)))

    def test_checkpoint_resume_without_duplicate_memory_or_relation(self):
        with patch.object(self.s.engine,'_assess_relation',side_effect=RuntimeError('offline')):
            self.assertIsNone(self.s.run_conversation(self.a,self.b,'中断'))
        self.assertFalse(self.s.busy)
        with self.s.database() as db:run_id=db.execute('SELECT id FROM m3_runs').fetchone()[0]
        before_count=len(self.s.memory.get_active(self.a,50))
        self.s.stop();self.s=SocialService()
        self.s.world.set_area(self.a,self.area);self.s.world.set_area(self.b,self.area)
        before=self.s.relations.get(self.a,self.b)
        result=self.s.resume(run_id)
        self.assertTrue(result)
        self.assertEqual(before_count,len(self.s.memory.get_active(self.a,50)))
        self.assertEqual(before+1,self.s.relations.get(self.a,self.b))
        self.release();self.s.resume(run_id)
        self.assertEqual(before+1,self.s.relations.get(self.a,self.b))

    def test_memory_timeline_forget_and_restart(self):
        self.s.run_conversation(self.a,self.b,'测试');self.release()
        with self.s.database() as db:
            old=db.execute('SELECT MAX(id) FROM m3_memory_history WHERE npc=?',(self.a,)).fetchone()[0]
        self.s.advance_time(4320);self.s.future.result(timeout=20)
        self.assertTrue(self.s.memory_panel(self.a)['forgotten'])
        self.assertTrue(self.s.memory_panel(self.a,old)['active'])

    def test_ws_push_order_reconnect_and_no_http_poll_endpoint(self):
        client=self.client()
        self.assertEqual(404,client.get('/social/events').status_code)
        with client.websocket_connect('/ws/events') as ws:
            snap=ws.receive_json();self.assertEqual('snapshot',snap['kind'])
            first=self.s.emit('movement',npc=self.a,area=self.area)
            self.assertEqual(first['id'],ws.receive_json()['id'])
        second=self.s.emit('memory_changed',npc=self.a)
        with client.websocket_connect('/ws/events?after='+str(first['id'])) as ws:
            ws.receive_json();replayed=ws.receive_json()
            self.assertTrue(replayed['replay']);self.assertEqual(second['id'],replayed['id'])
        client.close()

    def test_ws_lines_arrive_before_memory_and_http_remains_responsive(self):
        client=self.client();entered=threading.Event();release=threading.Event()
        original=self.s.npcs[self.a].save_dialogue_memory
        def blocked(*a,**kw):entered.set();release.wait(timeout=10);return original(*a,**kw)
        with client.websocket_connect('/ws/events') as ws,patch.object(self.s.npcs[self.a],'save_dialogue_memory',side_effect=blocked):
            ws.receive_json()
            worker=threading.Thread(target=lambda:self.s.run_conversation(self.a,self.b,'实时'))
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                kinds=[]
                for _ in range(9):kinds.append(ws.receive_json()['kind'])
                self.assertEqual(6,kinds.count('dialogue_line'))
                self.assertEqual(200,client.get('/health').status_code)
            finally:release.set();worker.join(timeout=15)
        client.close()

    def test_debug_controls_and_history(self):
        client=self.client()
        self.assertEqual(400,client.post('/debug/force_meet',json={'a':self.a,'b':self.a}).status_code)
        self.assertEqual(422,client.post('/world/speed',json={'speed':-1}).status_code)
        response=client.post('/debug/force_meet',json={'a':self.a,'b':self.b,'area':self.area})
        key=response.json()['meeting']['id']
        self.assertEqual(200,client.post('/social/meet/'+key+'/cancel').status_code)
        self.assertTrue(client.get('/npcs/'+self.a+'/memory/history').json())
        self.assertEqual(45,len(client.get('/social/relations').json()))
        client.close()

    def test_reflection_retry_reuses_insight_after_interrupted_mark(self):
        from backend.npc import InsightSchema
        for i in range(4):
            self.s.memory.add(agent=self.a,content=f'实验观察{i}',type='observation',importance=0.2,game_time=480)
        insight=InsightSchema(insight='需要先核对实验条件',importance=0.8)
        with patch.object(self.s.llm,'chat_json',return_value=insight),patch.object(self.s.memory,'mark_reflected',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.s.engine.agents[self.a].graph.invoke({'task':'reflect'})
        self.assertEqual(1,len(self.s.memory.get_reflections(self.a,10)))
        with patch.object(self.s.llm,'chat_json',side_effect=AssertionError('must not regenerate')):
            result=self.s.engine.agents[self.a].graph.invoke({'task':'reflect'})
        self.assertTrue(result['reflected'])
        self.assertEqual(1,len(self.s.memory.get_reflections(self.a,10)))
        self.assertEqual(0,self.s.memory.count_unreflected(self.a))

    def test_clock_rates_pause_and_partial_control(self):
        self.assertEqual(10,self.s.speed)
        self.s.step_clock(2)
        self.assertEqual(500,self.s.world.clock.now)
        client=self.client()
        try:
            self.assertEqual(200,client.post('/world/speed',json={'speed':40}).status_code)
            self.s.step_clock(2)
            self.assertEqual(580,self.s.world.clock.now)
            client.post('/social/control',json={'paused':True}).raise_for_status()
            self.assertEqual(40,self.s.speed)
            self.s.step_clock(100)
            self.assertEqual(580,self.s.world.clock.now)
            client.post('/world/speed',json={'speed':20}).raise_for_status()
            self.assertTrue(self.s.paused)
            client.post('/social/control',json={'paused':False}).raise_for_status()
            self.s.step_clock(0.5)
            self.assertEqual(590,self.s.world_snapshot()['minutes'])
        finally:client.close()

    def test_http_fast_forward_is_continuous_and_restores_speed(self):
        from backend import api
        client=TestClient(api.app)
        try:
            with patch.object(api,'get_service',return_value=self.s):
                response=client.post('/world/advance',json={'minutes':10,'speed':4})
                response.raise_for_status()
                self.assertEqual(480,self.s.world.clock.now)
                self.assertEqual(490,response.json()['target_minutes'])
                self.s.step_clock(2)
                self.assertEqual(488,self.s.world.clock.now)
                self.s.step_clock(1)
                self.assertEqual(495,self.s.world.clock.now)
                self.assertEqual(10,self.s.speed)
                self.assertIsNone(self.s.fast_forward_target)
                self.assertEqual(422,client.post('/world/advance',json={'minutes':10,'speed':0}).status_code)
        finally:client.close()

    def test_fast_forward_pause_and_manual_rate_cancel_target(self):
        self.s.set_control(speed=2)
        self.s.fast_forward(20,8)
        self.s.step_clock(1)
        self.assertEqual(488,self.s.world.clock.now)
        self.s.set_control(paused=True)
        self.s.step_clock(30)
        self.assertEqual(488,self.s.world.clock.now)
        self.assertEqual(500,self.s.fast_forward_target)
        self.s.set_control(paused=False,speed=4)
        self.assertIsNone(self.s.fast_forward_target)
        self.s.step_clock(10)
        self.assertEqual(528,self.s.world.clock.now)

    def test_rate_switch_settles_old_elapsed_time_before_using_new_rate(self):
        self.s.clock_sample=100.0
        try:
            with patch('backend.social.time.monotonic',return_value=100.1):
                self.s.set_control(speed=8)
            self.assertAlmostEqual(481,self.s.world.clock.now)
            self.s.sync_clock(100.5)
            self.assertAlmostEqual(484.2,self.s.world.clock.now)
            self.s.sync_clock(100.4)
            self.assertEqual(100.5,self.s.clock_sample)
            with patch('backend.social.time.monotonic',return_value=100.6):
                self.s.set_control(paused=True)
            self.assertAlmostEqual(485,self.s.world.clock.now)
            with patch('backend.social.time.monotonic',return_value=110.0):
                self.s.set_control(paused=False,speed=1)
            self.s.sync_clock(111.0)
            self.assertAlmostEqual(486,self.s.world.clock.now)
        finally:self.s.clock_sample=None

    def test_burst_dialogue_clears_final_bubble_before_releasing_actors(self):
        result=self.s.run_conversation(self.a,self.b,'恢复原版显示节奏')
        run_id=result['conversation_id']
        latest=self.s.live_lines[run_id]['data']
        self.assertEqual(6,latest['sequence'])
        self.assertEqual([10],[part['seconds'] for part in latest['segments']])
        self.assertTrue(self.s.live_lines[run_id]['finished'])
        self.assertFalse(any(line['conversation_id']==run_id for line in self.s.live_dialogue()))
        self.assertEqual(1,len([e for e in self.s.bus.history() if e['kind']=='dialogue_finished' and e['conversation_id']==run_id]))
        self.assertFalse(self.s.busy)
        # 地图气泡已结束；完整台词仍可从历史查询，同一人物可以参与下一场。
        self.assertIsNotNone(self.s.run_conversation(self.a,self.c,'继续原有社交行为'))
        self.assertFalse(self.s.busy)

    def test_final_bubble_hold_is_bounded_and_only_for_observers(self):
        key='visual-hold-test'
        self.s.live_lines[key]=dict(start=time.monotonic(),end=time.monotonic()+30)
        with patch('backend.social.time.sleep') as sleep:
            self.s._hold_final_bubble(key)
            sleep.assert_not_called()
            subscriber=object()
            self.s.bus.clients[subscriber]=None
            try:
                self.s._hold_final_bubble(key)
                self.assertEqual(1,sleep.call_count)
                self.assertGreater(sleep.call_args.args[0],0)
                self.assertLessEqual(sleep.call_args.args[0],SOCIAL.bubble_exit_hold_seconds)
            finally:self.s.bus.clients.pop(subscriber,None)

    def test_visual_end_event_is_published_before_actor_release(self):
        original=self.s.emit
        busy_at_end=[]
        def observe(kind,**data):
            if kind=='dialogue_finished':busy_at_end.append(set(self.s.busy))
            return original(kind,**data)
        with patch.object(self.s,'emit',side_effect=observe):
            self.s.run_conversation(self.a,self.b,'验证气泡先于离场清除')
        self.assertEqual(1,len(busy_at_end))
        self.assertTrue({self.a,self.b}.issubset(busy_at_end[0]))
        self.assertFalse(self.s.busy)

    def test_live_dialogue_reconnect_restores_latest_line_and_deduplicates(self):
        payload=dict(conversation_id='read-test',participants=[self.a,self.b],speaker=self.a,text='先读完这句话')
        first=self.s.emit('dialogue_line',event_key='read-test:1',sequence=1,**payload)
        self.s.emit('dialogue_line',event_key='read-test:2',sequence=2,**payload)
        duplicate=self.s.emit('dialogue_line',event_key='read-test:1',sequence=1,**payload)
        self.assertEqual(first['id'],duplicate['id'])
        stored=self.s.live_lines['read-test']
        self.assertEqual(2,stored['data']['sequence'])
        with patch('backend.social.time.monotonic',return_value=stored['start']+1):
            client=self.client()
            try:
                with client.websocket_connect('/ws/events') as ws:
                    live=ws.receive_json()['live']
                    self.assertEqual([2],[line['sequence'] for line in live])
                    self.assertEqual(1,live[0]['playback_elapsed'])
            finally:client.close()

    def test_original_ten_second_segments_do_not_scale_with_world_speed(self):
        from backend.playback import dialogue_segments
        self.assertEqual(10,dialogue_segments('你好')[0]['seconds'])
        self.assertEqual(10,dialogue_segments('长'*60)[0]['seconds'])
        parts=dialogue_segments('长'*125)
        self.assertEqual([60,60,5],[len(part['text']) for part in parts])
        self.assertEqual(30,sum(part['seconds'] for part in parts))
        self.s.set_control(speed=8)
        self.assertEqual(parts,dialogue_segments('长'*125))

    def test_three_day_fast_forward_restores_speed_and_runs_final_forgetting(self):
        from concurrent.futures import Future
        self.s.observe(self.a,'快进前记下的一件琐事',0.2)
        pending=Future()
        self.s.future=pending
        result=self.s.fast_forward(4320)
        self.assertEqual(480,self.s.world.clock.now)
        self.assertEqual(480+4320,result['target_minutes'])
        self.s.step_clock(12)
        self.assertEqual(480+720,self.s.world.clock.now)
        self.s.set_control(paused=True)
        self.s.step_clock(100)
        self.assertEqual(480+720,self.s.world.clock.now)
        self.s.set_control(paused=False)
        self.s.step_clock(60)
        self.assertEqual(480+4320,self.s.world.clock.now)
        self.assertEqual(10,self.s.speed)
        self.assertIsNone(self.s.fast_forward_target)
        self.tick()
        self.assertTrue(self.s.maintenance_due)
        pending.set_result(None)
        self.tick();self.s.future.result(timeout=20)
        self.assertFalse(self.s.maintenance_due)
        self.assertTrue(self.s.memory.get_forgotten(self.a,50))

if __name__=='__main__':unittest.main()

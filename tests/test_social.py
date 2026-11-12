"""M3: 真实 LangGraph/SQLite/WebSocket 集成，模型为可控替身，不消耗 API。"""
import gc
import logging
import tempfile
import threading
import time
import unittest
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
        self.s.busy.clear();self.s.release_at.clear();self.s.live_lines.clear()
    def tick(self,elapsed=0):
        with patch.object(SOCIAL,'base_chat_probability',0):self.s.scheduler.tick(time.monotonic(),elapsed)
    def finish(self):
        for future in list(self.s.scheduler.jobs.values()):future.result(timeout=20)
    def client(self):
        app=FastAPI();install(app,lambda:self.s)
        app.get('/health')(lambda:{'status':'ok'})
        return TestClient(app)

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
        with patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0),patch.object(self.s,'run_conversation',wraps=self.s.run_conversation) as entry:
            self.s.scheduler.tick(time.monotonic(),0.5);self.finish()
        self.assertEqual(2,entry.call_count)
        self.assertEqual(4,len(self.s.busy))
        self.assertEqual(485,self.s.world.clock.now)

    def test_model_can_decline_natural_chat(self):
        with patch.object(self.s.llm,'chat_json',return_value=SocialDecision(action='continue_schedule')),patch.object(SOCIAL,'base_chat_probability',1),patch('backend.scheduler.random.random',return_value=0):
            self.s.scheduler.tick(time.monotonic());self.finish()
        self.assertFalse(self.s.busy)
        self.assertFalse(any(e['kind']=='dialogue_line' for e in self.s.bus.history()))

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
        self.assertEqual('playing',m['state'])

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
        self.assertEqual(['playing','playing','ready'],[m['state'] for m in meetings])
        self.s.release_at[tuple(sorted(names[:2]))]=0;self.s.paused=True;self.tick()
        self.assertFalse(set(names[:2])&self.s.busy);self.assertTrue(set(names[2:4])<=self.s.busy)

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

if __name__=='__main__':unittest.main()

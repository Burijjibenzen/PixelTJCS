"""动态日程使用真实服务/SQLite，模型替身；不访问开发数据库或网关。"""
import json
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch
import test_social as fixtures
from backend.config import ACTIVITY, SOCIAL
from backend.daily_planner import DynamicSchedule
from backend.world import AREAS, J3, J4, ZX2


class DynamicScheduleTests(unittest.TestCase):
    setUp = fixtures.SocialTests.setUp
    tearDown = fixtures.SocialTests.tearDown
    profile_client = fixtures.SocialTests.profile_client

    def plan_tick(self, elapsed=0):
        self.s.planner.tick(time.monotonic(),elapsed)

    def event(self,text,names=None):
        return self.s.world.inject_event(text,names or [self.a])

    def item(self,name=None):
        return self.s.planner.current_item(name or self.a)

    def test_offline_daily_variation_has_meaningful_three_map_opportunities(self):
        p=self.s.planner
        signatures=[];maps=set();kinds=set()
        with patch.object(self.s.llm,'chat_json',side_effect=AssertionError('offline must not call model')):
            for day in range(7):
                self.s.world.clock.minutes=day*1440+480
                for name in self.s.npcs:p.ensure_day(name)
                tasks=[a for a in p.records.values() if a['day']==day]
                signatures.append([(a['npc'],a['kind'],a['area'],a['starts_at']%1440) for a in tasks])
                maps.update(AREAS[a['area']]['map_id'] for a in tasks)
                kinds.update(a['kind'] for a in tasks)
                p._request_model(time.monotonic())
        self.assertNotEqual(signatures[0],signatures[1])
        self.assertEqual({J3,J4,ZX2},maps)
        self.assertTrue({'library','class','exercise'}.issubset(kinds))
        self.assertIsNone(p.job)
        self.assertTrue(all(a['reason'] and a['area'] in AREAS for a in p.records.values()))

    def test_personality_and_role_change_activity_choices(self):
        p=self.s.planner
        student=next(n for n,c in self.s.cards.items() if c.role=='student')
        staff=next(n for n,c in self.s.cards.items() if c.role=='staff')
        for day in range(30):
            self.s.world.clock.minutes=day*1440+480
            p.ensure_day(student);p.ensure_day(staff)
        student_admin=sum(a['npc']==student and a['kind']=='admin' for a in p.records.values())
        staff_admin=sum(a['npc']==staff and a['kind']=='admin' for a in p.records.values())
        self.assertEqual(0,student_admin)
        self.assertGreater(staff_admin,10)

    def test_known_event_only_redirects_recipients_and_keeps_real_position(self):
        before=dict(self.s.world.npc_area)
        self.event('现在前往图书分馆查资料',[self.a])
        self.plan_tick()
        task=self.item()
        self.assertEqual('event',task['source'])
        self.assertEqual(f'{ZX2}/zx2_lib',task['area'])
        self.assertEqual('travelling',task['state'])
        self.assertEqual(before,self.s.world.npc_area)
        self.assertFalse(any(a['npc']==self.b and a['source']=='event' for a in self.s.planner.records.values()))

    def test_target_latches_across_slots_days_and_fast_forward_until_actual_arrival(self):
        self.event('去智信馆图书分馆查资料')
        self.plan_tick();task=self.item();target=task['area']
        self.s.world.clock.advance(1500)
        self.s.speed=60
        self.plan_tick(1)
        self.assertEqual(task['id'],self.item()['id'])
        self.assertEqual(target,self.s.world.target_area(self.a))
        self.assertIsNone(task['arrived_at'])
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_corridor',arrived=False)
        self.assertEqual('travelling',task['phase'])
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',arrived=False)
        self.assertEqual('travelling',task['phase'])
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',arrived=True)
        self.assertEqual('active',task['state'])
        self.plan_tick(1)
        self.assertEqual('active',task['state'])
        self.plan_tick(3)
        self.assertEqual('completed',task['state'])

    def test_event_appointment_prepares_travel_and_cancellation_is_recipient_scoped(self):
        self.event('明天10:00到智信馆n219教室上课',[self.a,self.b])
        self.plan_tick()
        tasks=[a for a in self.s.planner.records.values() if a['source']=='event']
        self.assertEqual(2,len(tasks))
        self.assertEqual(1440+600-ACTIVITY.appointment_lead_minutes,tasks[0]['starts_at'])
        self.assertEqual(tasks[0]['area'],tasks[1]['area'])
        self.assertEqual(f'{ZX2}/zx2_n219',tasks[0]['area'])
        self.assertNotEqual('event',self.item()['source'])
        self.event('取消明天的上课安排',[self.a]);self.s.planner.scan_at=0;self.plan_tick()
        self.assertEqual('cancelled',next(a for a in tasks if a['npc']==self.a)['state'])
        self.assertEqual('scheduled',next(a for a in tasks if a['npc']==self.b)['state'])

    def test_memories_and_model_context_do_not_share_private_knowledge(self):
        self.s.observe(self.a,'讨论研究问题后需要去图书分馆查资料',.8)
        self.s.observe(self.b,'PRIVATE_OTHER_MEMORY：去健身',.8)
        self.event('PRIVATE_OTHER_EVENT：明天组会',[self.b])
        context=self.s.planner.context(self.a)
        self.assertNotIn('PRIVATE_OTHER',json.dumps(context,ensure_ascii=False))
        self.plan_tick()
        self.assertEqual('memory',self.item()['source'])
        self.assertEqual('library',self.item()['kind'])
        public=json.dumps(self.s.world_snapshot()['npcs'][self.a]['activity'],ensure_ascii=False)
        self.assertNotIn('讨论研究问题后',public)
        self.assertNotIn('memories',public)
        self.s.planner.scan_at=0;self.plan_tick()
        self.assertEqual(1,sum(a['npc']==self.a and a['source']=='memory' for a in self.s.planner.records.values()))

    def test_busy_and_manual_assignment_coordinate_with_queued_meeting(self):
        self.event('去图书分馆查资料');self.plan_tick();original=self.item()
        self.s.busy.update((self.a,self.b))
        meeting=self.s.request_meeting(self.a,self.b,f'{J4}/R05')
        self.s.assign_destination(self.a,f'{ZX2}/zx2_gym')
        self.plan_tick(10)
        self.assertEqual(original['id'],self.item()['id'])
        self.assertEqual('waiting',self.item()['state'])
        self.s.busy.clear();self.plan_tick()
        manual=self.item()
        self.assertEqual('manual',manual['source'])
        self.s.scheduler.meetings(time.monotonic(),0)
        self.assertEqual('waiting',meeting['state'])
        snapshot=self.s.world_snapshot()['npcs'][self.a]
        self.assertEqual('zx2_gym',snapshot['target_room_id'])
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_gym',arrived=True)
        self.plan_tick(12)
        self.assertEqual('completed',manual['state'])
        self.s.scheduler.meetings(time.monotonic(),0)
        self.assertEqual('moving',meeting['state'])
        self.assertEqual('R05',self.s.world_snapshot()['npcs'][self.a]['target_room_id'])

    def test_meeting_interrupts_dwell_then_activity_requires_return_arrival(self):
        self.event('去图书分馆查资料');self.plan_tick();task=self.item()
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',arrived=True)
        self.plan_tick(3);dwell=task['dwell_minutes']
        m=self.s.request_meeting(self.a,self.b,f'{J4}/R05')
        self.plan_tick(10)
        self.assertEqual(dwell,task['dwell_minutes'])
        self.s.report_arrival(self.a,map_id=J4,room_id='R05',arrived=True)
        m['state']='completed';self.plan_tick()
        self.assertEqual('travelling',task['phase'])
        self.assertEqual(f'{ZX2}/zx2_lib',self.s.world.target_area(self.a))
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',arrived=True)
        self.assertEqual('active',task['state'])

    def test_relationship_visit_uses_existing_meeting_queue(self):
        p=self.s.planner
        task=p._make(self.a,'visit',f'{ZX2}/zx2_lib',self.s.world.clock.now,
                     'relationship','约熟悉的同学讨论项目',partner=self.b)
        self.plan_tick()
        self.assertTrue(task['meeting_id'])
        m=self.s.meeting(task['meeting_id'])
        self.assertEqual({self.a,self.b},{m['a'],m['b']})
        self.assertNotEqual(f'{ZX2}/zx2_lib',AREAS[self.s.world.area_of(self.a)]['id'])
        m.update(state='completed',detail='会面完成');self.plan_tick()
        self.assertEqual('completed',task['state'])

    def test_offline_presence_pauses_budget_and_online_route_timeout_is_visible(self):
        self.event('去图书分馆查资料');self.plan_tick();task=self.item()
        self.s.seen.clear();self.plan_tick(400)
        self.assertEqual('waiting',task['state'])
        self.assertEqual(0,task['travel_elapsed'])
        self.s.seen[self.a]=time.monotonic();self.plan_tick(301)
        self.assertEqual('timed_out',task['state'])

    def test_model_plan_validation_is_atomic_and_checks_rooms_times_roles_and_partners(self):
        p=self.s.planner;p.ensure_day(self.a);context=p.context(self.a)
        original=[a['id'] for a in p.records.values() if a['npc']==self.a and a['source']=='rule']
        good=dict(kind='library',area=f'{ZX2}/zx2_lib',start_minute=900,duration=180)
        for bad in [dict(good,area=f'{ZX2}/not_a_room'),dict(good,start_minute=20),
                    dict(good,start_minute=1500),dict(good,duration=float('nan')),
                    dict(good,kind='admin',area=f'{J3}/R08'),dict(good,partner=self.b),
                    dict(good,basis='event'),dict(good,kind='visit',partner='不存在')]:
            self.assertFalse(p.apply_model(self.a,p.day,{'activities':[good,bad]},context),bad)
        self.assertTrue(all(p.records[key]['state']=='scheduled' for key in original))
        self.assertFalse(p.apply_model(self.a,p.day,{'activities':[good,dict(good,start_minute=901)]},context))
        self.assertTrue(p.apply_model(self.a,p.day,{'activities':[good]},context))
        self.assertTrue(all(p.records[key]['state']=='cancelled' for key in original))
        self.assertTrue(any(a['source']=='model' and a['area']==good['area'] for a in p.records.values()))

    def test_model_timeout_is_nonblocking_discards_late_result_and_keeps_rules(self):
        p=self.s.planner;p.ensure_day(self.a)
        future=Future();context=p.context(self.a)
        p.job=dict(future=future,npc=self.a,day=p.day,start=0,expired=False,context=context)
        p._poll_model(20)
        self.assertTrue(p.job['expired'])
        future.set_result({'activities':[dict(kind='library',area=f'{ZX2}/zx2_lib',start_minute=900)]})
        p._poll_model(21)
        self.assertIsNone(p.job)
        self.assertFalse(any(a['source']=='model' for a in p.records.values()))
        self.assertTrue(any(a['source']=='rule' for a in p.records.values()))

    def test_event_between_context_scans_still_reacts_and_overdue_model_result_is_discarded(self):
        self.plan_tick()
        self.event('前往图书分馆查资料')
        self.s.world.clock.advance(50)
        self.s.planner.scan_at=0;self.plan_tick()
        self.assertEqual('event',self.item()['source'])
        p=self.s.planner;future=Future()
        future.set_result({'activities':[dict(kind='library',area=f'{ZX2}/zx2_lib',start_minute=1000)]})
        p.job=dict(future=future,npc=self.a,day=p.day,start=0,expired=False,context=p.context(self.a))
        p._poll_model(20)
        self.assertFalse(any(a['source']=='model' for a in p.records.values()))

    def test_model_frequency_once_per_npc_day_and_one_global_request(self):
        p=self.s.planner
        for name in self.s.npcs:p.ensure_day(name)
        self.s.llm.cfg=SimpleNamespace(api_key='test-placeholder')
        held=Future()
        with patch.object(p.pool,'submit',return_value=held) as submit:
            p._request_model(100);p._request_model(200)
            self.assertEqual(1,submit.call_count)
            first=p.job['npc'];held.set_result(None);p._poll_model(101)
            p._request_model(102)
            self.assertEqual(1,submit.call_count)
            p._request_model(109)
            self.assertEqual(2,submit.call_count)
            self.assertNotEqual(first,p.job['npc'])
        p.job=None

    def test_background_model_runs_through_agent_and_shared_gateway_with_private_context(self):
        from backend.activity_catalog import DailyPlan
        p=self.s.planner
        for name in self.s.npcs:p.ensure_day(name)
        self.s.llm.cfg=SimpleNamespace(api_key='test-placeholder')
        self.s.observe(self.b,'PRIVATE_OTHER_CONTEXT',.8)
        result=DailyPlan.model_validate({'activities':[dict(kind='library',area=f'{ZX2}/zx2_lib',start_minute=1000)]})
        with patch.object(self.s.llm,'chat_json',return_value=result) as gateway:
            p._request_model(100)
            p.job['future'].result(timeout=3)
            p._poll_model(101)
        self.assertEqual(1,gateway.call_count)
        args,kwargs=gateway.call_args
        self.assertIs(args[2],DailyPlan)
        self.assertEqual(ACTIVITY.model_timeout_seconds,kwargs['timeout'])
        self.assertNotIn('PRIVATE_OTHER_CONTEXT',args[0]+args[1])
        self.assertTrue(any(a['npc']==self.a and a['source']=='model' for a in p.records.values()))

    def test_http_schedule_fields_manual_command_cancel_and_arrival_confirmation(self):
        with self.profile_client() as client:
            self.assertEqual(200,client.post('/world/goto',json=dict(npc=self.a,map_id=ZX2,room_id='zx2_lib')).status_code)
            self.plan_tick()
            data=client.get('/npcs/'+self.a+'/activities').json()
            self.assertEqual('manual',data['current']['source'])
            self.assertEqual('正在前往',data['current']['state_label'])
            response=client.post('/world/report',json=dict(npc=self.a,map_id=ZX2,room_id='zx2_lib',arrived=False))
            self.assertEqual(200,response.status_code)
            self.assertEqual('travelling',self.item()['phase'])
            client.post('/world/report',json=dict(npc=self.a,map_id=ZX2,room_id='zx2_lib',arrived=True)).raise_for_status()
            identity=self.item()['id']
            self.assertEqual('active',self.item()['state'])
            self.assertEqual(200,client.post(f'/npcs/{self.a}/activities/{identity}/cancel').status_code)
            self.assertEqual(404,client.get('/npcs/无人/activities').status_code)

    def test_persistence_preserves_current_target_and_daily_model_attempt(self):
        self.event('去图书分馆查资料');self.plan_tick();task=self.item()
        self.s.planner.model_attempted.add((self.a,0))
        with self.s.database() as db:db.execute("UPDATE npc_plan_days SET model_status='invalid' WHERE npc=? AND day=0",(self.a,))
        self.s.planner.stop()
        self.s.planner=DynamicSchedule(self.s)
        self.plan_tick()
        self.assertEqual(task['id'],self.item()['id'])
        self.assertEqual(task['area'],self.s.world.target_area(self.a))
        self.assertIn((self.a,0),self.s.planner.model_attempted)

    def test_disabled_dynamic_schedule_preserves_legacy_manual_destination(self):
        with patch.object(ACTIVITY,'enabled',False):
            self.assertTrue(self.s.assign_destination(self.a,f'{ZX2}/zx2_lib'))
            self.plan_tick()
            self.assertEqual(f'{ZX2}/zx2_lib',self.s.world.target_area(self.a))
            self.assertEqual('baseline',self.s.planner.describe(self.a)['kind'])

    def test_same_room_new_activity_waits_for_fresh_arrival_and_rejects_stale_confirmation(self):
        self.s.assign_destination(self.a,self.area);self.plan_tick();task=self.item()
        self.assertEqual('travelling',task['phase'])
        room=AREAS[self.area]
        self.s.report_arrival(self.a,map_id=room['map_id'],room_id=room['room_id'],arrived=True,activity_id='old-plan')
        self.assertEqual('travelling',task['phase'])
        self.s.report_arrival(self.a,map_id=room['map_id'],room_id=room['room_id'],arrived=False,activity_id=task['id'])
        self.assertEqual('travelling',task['phase'])
        self.s.report_arrival(self.a,map_id=room['map_id'],room_id=room['room_id'],arrived=True,activity_id=task['id'])
        self.assertEqual('active',task['state'])

    def test_restart_requeues_unfinished_relationship_visit(self):
        task=self.s.planner._make(self.a,'visit',f'{ZX2}/zx2_lib',self.s.world.clock.now,
                                 'relationship','约同学交流',partner=self.b)
        self.plan_tick();previous=task['meeting_id']
        self.s.planner.stop();self.s.meetings=[]
        self.s.planner=DynamicSchedule(self.s)
        self.plan_tick()
        restored=self.item()
        self.assertNotEqual(previous,restored['meeting_id'])
        self.assertIsNotNone(self.s.meeting(restored['meeting_id']))


if __name__=='__main__':unittest.main()

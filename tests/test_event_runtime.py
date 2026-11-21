import json
from threading import Event
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_social as fixtures
from backend.agent_runtime import ConversationConsent, EventAssessment, WhisperAction
from backend.social import SocialService
from backend.world import AREAS, J3, J4, ZX2


class EventRuntimeTests(unittest.TestCase):
    setUp = fixtures.SocialTests.setUp
    tearDown = fixtures.SocialTests.tearDown
    report_positions = fixtures.SocialTests.report_positions

    def focus(self, name=None):
        return self.s.game_events.current()[name or self.a]

    def start_nearby_invitation(self,item):
        planner=self.s.planner
        seeker,partner=item['npc'],item['partner']
        with self.s.lock:
            planner._reserve_encounters({seeker:item},time.monotonic())
            self.assertEqual(item['id'],planner.encounter_waits[partner])
            sample=self.s.positions[partner]
            self.s.report_arrival(partner,map_id=sample['map_id'],room_id=AREAS[sample['area']]['room_id'],
                                  x=sample['x'],y=sample['y'],arrived=False,encounter_wait=item['id'])
            planner._talk(item,time.monotonic(),0)

    def library(self):
        self.a=next(name for name,card in self.s.cards.items() if card.role=='student' and name!=self.b)
        self.s.llm.cfg=SimpleNamespace(api_key='mock')
        planner=self.s.planner
        item=planner._make(self.a,'library',f'{ZX2}/zx2_lib',self.s.world.clock.now,'rule','查阅资料')
        planner._start(item)
        self.assertEqual(item['id'],self.focus()['id'])
        self.assertEqual('travelling',self.focus()['phase'])
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',arrived=True,activity_id=item['id'])
        self.assertEqual(item['id'],self.focus()['id'])
        item['dwell_minutes']=75
        planner._checkpoint(item)
        self.assertEqual(item['id'],self.focus()['id'])
        self.assertEqual('evaluating',self.focus()['phase'])
        return item

    def decide(self, action, bubble=""):
        with patch.object(self.s.llm,'chat_json',return_value=EventAssessment(
                outcome='看懂了一半，仍有一项疑问',reason='需要核对方法',action=action,bubble=bubble)) as assess:
            runtime=self.s.event_runtime
            runtime.poll(time.monotonic())
            future,_,_=next(job for key,job in runtime.jobs.items() if key[1]==self.a)
            future.result(timeout=5)
            runtime.poll(time.monotonic())
            self.assertIs(EventAssessment,assess.call_args.args[2])
            context=json.loads(assess.call_args.args[1].split('\n')[-1])
            self.assertEqual('completed',context['event']['facts']['status'])

    def assert_coverage(self):
        with self.s.database() as db:
            spans=db.execute('SELECT actor,event_id,started_at,ended_at FROM npc_event_spans ORDER BY rowid').fetchall()
        current=self.s.game_events.current()
        self.assertEqual(set(self.s.npcs),set(current))
        for name in self.s.npcs:
            person=[(identity,start,end) for actor,identity,start,end in spans if actor==name]
            self.assertEqual((current[name]['id'],None),(person[-1][0],person[-1][2]))
            for previous,next_span in zip(person,person[1:]):
                self.assertEqual(previous[2],next_span[1])

    def test_extend_creates_same_location_event_and_preserves_identity_and_progress(self):
        item=self.library()
        self.s.world.clock.advance(75)
        self.decide(dict(type='extend',minutes=30))
        next_item=self.s.planner.current_item(self.a)
        self.assertNotEqual(item['id'],next_item['id'])
        self.assertEqual((item['id'],75,'active'),(next_item['source_event_id'],
            next_item['prior_progress_minutes'],next_item['state']))
        entry=self.s.game_events.timeline(self.a)['events'][0]
        self.assertEqual(item['id'],entry['id'])
        self.assertEqual(next_item['id'],entry['assessment']['next_event_id'])
        self.assertEqual('看懂了一半，仍有一项疑问',entry['assessment']['outcome'])
        with self.s.database() as db:
            result=json.loads(db.execute('SELECT result FROM game_events WHERE id=?',(item['id'],)).fetchone()[0])
        self.assertEqual(entry['assessment'],result['perspectives'][self.a])
        self.assert_coverage()

    def test_routine_resumes_and_records_why_before_another_middle_tier_action(self):
        planner=self.s.planner
        now=self.s.world.clock.now
        room=f'{J3}/R03'
        routine=planner._make(self.a,'research',room,now,'rule','科研日程')
        planner._start(routine)
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',arrived=True,activity_id=routine['id'])
        routine['dwell_minutes']=5
        first=planner._make(self.a,'research',room,now,'event','实验遇到问题')
        second=planner._make(self.a,'research',room,now,'event','复核结果')
        planner.tick(time.monotonic(),0)
        self.assertEqual(first['id'],self.focus()['id'])
        self.assertEqual('medium',self.focus()['tier'])
        self.assertIn('实验遇到问题，暂挂推进科研',self.focus()['schedule_reason'])
        self.assertEqual('suspended',routine['state'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',arrived=True,activity_id=first['id'])
        planner._checkpoint(first)
        self.assertEqual(routine['id'],self.focus()['id'])
        self.assertIn('结束，恢复',self.focus()['schedule_reason'])
        self.assertIn(self.a,planner.routine_turns)
        planner.tick(time.monotonic(),0)
        self.assertEqual('scheduled',second['state'])
        planner.tick(time.monotonic(),1)
        self.assertGreater(routine['dwell_minutes'],5)
        planner.tick(time.monotonic(),0)
        self.assertEqual(second['id'],self.focus()['id'])
        saved=next(row for row in self.s.game_events.timeline(self.a)['events'] if row['id']==first['id'])
        self.assertEqual('medium',saved['content']['tier'])
        self.assertTrue(any('恢复推进科研' in note['text'] for note in saved['content']['schedule_notes']))

    def test_queued_actions_use_deadline_then_enqueue_order_and_keep_running_same_tier(self):
        planner=self.s.planner
        now=self.s.world.clock.now
        def add(caption,deadline):
            item=planner._make(self.a,'research','j3_lab',now,'event',caption)
            item['appointment_at']=now+deadline
            return item
        late=add('晚一些',30)
        first=add('先进入',15)
        tied=add('后进入',15)
        planner.tick(time.monotonic(),0)
        self.assertIs(first,planner.current_item(self.a))
        urgent=add('同档更早到期',5)
        planner.tick(time.monotonic(),0)
        self.assertIs(first,planner.current_item(self.a))
        self.assertEqual(['scheduled']*3,[row['state'] for row in (late,tied,urgent)])
        command=planner.whisper(self.a,WhisperAction(type='stay'))
        planner.tick(time.monotonic(),0)
        self.assertIs(command,planner.current_item(self.a))
        self.assertEqual('high',self.focus()['tier'])
        self.assertEqual('cancelled',first['state'])

    def test_go_to_lab_requires_arrival_and_survives_restart(self):
        item=self.library()
        self.decide(dict(type='perform_at',kind='research',area=f'{J3}/R03',purpose='验证方法',minutes=90))
        followup=self.s.planner.current_item(self.a)
        self.assertEqual('travelling',self.focus()['phase'])
        self.assertEqual('zx2_lib',AREAS[self.s.world.area_of(self.a)]['room_id'])
        self.s.stop()
        self.s=SocialService()
        self.assertEqual(followup['id'],self.focus()['id'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',arrived=True,activity_id=item['id'])
        self.assertEqual('travelling',self.focus()['phase'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',arrived=True,activity_id=followup['id'])
        self.assertEqual('active',self.focus()['phase'])
        self.assert_coverage()

    def test_model_can_keep_schedule_or_propose_invalid_action_without_fabricating_run(self):
        item=self.library()
        self.decide(dict(type='perform_at',kind='research',area='missing',purpose='验证',minutes=30))
        entry=self.s.game_events.timeline(self.a)['events'][0]
        self.assertEqual('rejected',entry['assessment']['status'])
        self.assertNotIn('next_event_id',entry['assessment'])
        self.assertEqual('idle',self.focus()['kind'])
        item=self.library()
        self.decide(None)
        entry=next(event for event in self.s.game_events.timeline(self.a)['events'] if event['id']==item['id'])
        self.assertEqual((item['id'],None),(entry['id'],entry['assessment']['action']))
        self.assert_coverage()

    def test_self_talk_uses_model_summary_without_replacing_the_saved_result(self):
        item=self.library()
        self.decide(dict(type='talk_to',npc=self.b,topic='请教如何验证方法'),bubble='还差一步，找人核对下。')
        entry=self.s.game_events.timeline(self.a)['events'][0]
        self.assertEqual(item['id'],entry['id'])
        self.assertEqual('看懂了一半，仍有一项疑问',entry['assessment']['outcome'])
        lines=[e for e in self.s.bus.history() if e.get('speech')=='event_self_talk']
        self.assertEqual(['（自言自语）还差一步，找人核对下。'],[e['text'] for e in lines])

    def test_whisper_waits_for_running_high_tier_action_and_uses_actual_position(self):
        planner=self.s.planner
        self.assertTrue(planner.command(self.a,'j3_lab'))
        planner.tick(time.monotonic(),0)
        manual=planner.current_item(self.a)
        self.assertEqual('manual',manual['source'])
        actual=AREAS[self.s.world.area_of(self.a)]['id']
        action=WhisperAction(type='stay',minutes=20)
        with fixtures.SocialTests.profile_client(self) as client, \
                patch.object(self.s.llm,'chat_json',return_value=action) as classify, \
                patch.object(self.s.engine.agents[self.a],'consent_to_event',
                             side_effect=AssertionError('assigned NPC cannot refuse')):
            response=client.post('/world/whisper',json={'npc':self.a,'text':'在当前位置停留二十分钟'})
            self.assertEqual(200,response.status_code,response.text)
            row,=client.get('/world/whisper/history').json()['history']
            self.assertEqual((self.a,'在当前位置停留二十分钟','原地停留'),
                             (row['npc'],row['text'],row['action']))
            self.assertEqual(response.json()['activity']['id'],row['activity_id'])
            self.assertIs(WhisperAction,classify.call_args.args[2])
            planner.tick(time.monotonic(),0)
            self.assertIs(manual,planner.current_item(self.a))
            self.assertEqual('scheduled',planner.records[row['activity_id']]['state'])
        planner._checkpoint(manual)
        planner.tick(time.monotonic(),0)
        active=planner.current_item(self.a)
        self.assertEqual('completed',manual['state'])
        self.assertEqual('high',active['tier'])
        self.assertEqual(('whisper',actual,'active',20),(active['source'],active['area'],active['state'],active['duration']))
        self.assertEqual(active['id'],self.focus()['id'])
        self.assertTrue(self.s.world_snapshot()['npcs'][self.a]['hold_position'])
        planner.tick(time.monotonic(),19/self.s.speed)
        self.assertEqual('active',active['state'])
        planner.tick(time.monotonic(),1/self.s.speed)
        self.assertEqual('completed',active['state'])

    def test_nearby_target_holds_its_existing_route_until_invitation_is_cancelled(self):
        planner=self.s.planner
        item=planner.whisper(self.a,WhisperAction(type='talk_to',npc=self.b))
        self.s.report_arrival(self.a,map_id=J4,room_id='R01',x=296,y=488,arrived=False)
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=360,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        self.assertNotIn(self.b,planner.encounter_waits)
        self.assertNotEqual('encounter_wait',self.focus(self.b)['kind'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=250,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        self.assertNotIn(self.b,planner.encounter_waits)
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=296,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        self.assertEqual({self.b:item['id']},planner.encounter_waits)
        waiting=self.focus(self.b)
        self.assertEqual(('encounter_wait','medium',self.a),(waiting['kind'],waiting['tier'],waiting['target_id']))
        npcs=self.s.world_snapshot()['npcs']
        self.assertEqual(self.a,npcs[self.b]['encounter_wait_for'])
        self.assertNotIn('encounter_wait',npcs[self.a])
        with fixtures.SocialTests.profile_client(self) as client:
            response=client.post('/world/report',json=dict(npc=self.b,map_id=J3,room_id='R01',x=358,y=488,
                arrived=False,encounter_wait=item['id']))
            self.assertEqual(200,response.status_code,response.text)
        planner.tick(time.monotonic(),0)
        self.assertFalse(self.s.positions[self.b]['travelling'])
        self.assertEqual(item['id'],self.s.positions[self.b]['encounter_wait'])
        self.assertNotIn(item['id'],self.s.scheduler.event_talks)
        self.assertTrue(planner.cancel(self.a,item['id']))
        self.assertNotIn('encounter_wait',self.s.world_snapshot()['npcs'][self.b])
        saved,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual((waiting['id'],'cancelled','conversation:'+item['id']),
                         (saved['id'],saved['result']['status'],saved['source_event_id']))

    def test_pursuit_tracks_across_floors_then_preempts_medium_activity_only_at_range(self):
        planner=self.s.planner
        target=planner._make(self.b,'research',f'{J4}/R03',self.s.world.clock.now,'event','正在科研')
        planner._start(target)
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',x=0,y=0,arrived=True)
        self.s.report_arrival(self.b,map_id=J3,room_id='R03',x=200,y=0,arrived=True)
        visit=planner.queue_social_visit(self.a,self.b,'请教方法')
        planner.tick(time.monotonic(),0)
        self.assertIs(visit,planner.current_item(self.a))

        self.s.report_arrival(self.b,map_id=J4,room_id='R03',x=200,y=0,arrived=True,activity_id=target['id'])
        planner.tick(time.monotonic(),0)
        npc=self.s.world_snapshot()['npcs'][self.a]
        self.assertEqual((J4,'R03','travelling'),(npc['target_map_id'],npc['target_room_id'],self.focus()['phase']))
        self.assertEqual(target['id'],self.focus(self.b)['id'])
        self.assertFalse(self.s.game_events.timeline(self.b)['events'])

        target['dwell_minutes']=23
        self.s.report_arrival(self.a,map_id=J4,room_id='R03',x=180,y=0,arrived=True)
        planner.tick(time.monotonic(),0)
        self.assertEqual(visit['id'],planner.encounter_waits[self.b])
        self.assertFalse(self.s.scheduler.event_talks)
        event,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual(('encounter_wait','waiting','medium'),(event['kind'],event['phase'],event['content']['tier']))
        planner.tick(time.monotonic(),1)
        self.assertEqual(23,target['dwell_minutes'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',x=180,y=0,arrived=False)
        planner.tick(time.monotonic(),0)
        ended,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual((event['id'],'finished','interrupted'),(ended['id'],ended['phase'],ended['result']['status']))
        self.assertEqual(target['id'],self.focus(self.b)['id'])
        self.assertIn('恢复推进科研',self.focus(self.b)['schedule_reason'])
        planner.tick(time.monotonic(),1)
        self.assertGreater(target['dwell_minutes'],23)

    def test_nearby_encounter_queues_while_recipient_is_already_speaking(self):
        planner=self.s.planner
        self.report_positions([self.a,self.b,self.c])
        first=planner.queue_social_visit(self.a,self.b,'核对方法')
        planner._start(first)
        spoken,finish=Event(),Event()

        def reply(_):
            spoken.set()
            finish.wait(5)
            return ConversationConsent(decision='decline',reason='稍后再说',reply='我现在有事。')

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',side_effect=reply):
            self.start_nearby_invitation(first)
            future=self.s.scheduler.event_talks[first['id']]
            try:
                self.assertTrue(spoken.wait(5))
                second=planner.queue_social_visit(self.c,self.b,'讨论结果')
                with self.s.lock:
                    planner.tick(time.monotonic(),0)
                self.assertEqual(first['id'],planner.encounter_waits[self.b])
                pending=next(row for row in self.s.game_events.timeline(self.b)['events']
                             if row['source_event_id']==self.s.event_runtime.activity_id(second))
                self.assertEqual('scheduled',pending['phase'])
            finally:
                finish.set()
            future.result(timeout=5)
        planner.tick(time.monotonic(),0)
        self.assertEqual(pending['id'],self.focus(self.b)['id'])

    def test_encounter_retains_an_assessment_returned_during_the_pause(self):
        item=self.library()
        planner=self.s.planner
        runtime=self.s.event_runtime
        self.s.world.set_area(self.b,self.s.world.area_of(self.a))
        self.report_positions([self.a,self.b])
        answer=EventAssessment(outcome='已经核对过原文',reason='资料找到了',action=None)
        with patch.object(self.s.engine.agents[self.a],'assess_event',return_value=answer) as assess:
            runtime.poll(time.monotonic())
            runtime.jobs[(item['id'],self.a)][0].result(timeout=5)
            visit=planner.queue_social_visit(self.b,self.a,'想问一个问题')
            planner.tick(time.monotonic(),0)
            self.assertEqual('encounter_wait',self.focus()['kind'])
            self.assertIn((item['id'],self.a),runtime.deferred_assessments)
            planner.cancel(self.b,visit['id'])
            runtime.poll(time.monotonic())
            assess.assert_called_once()
        assessed=next(row for row in self.s.game_events.timeline(self.a)['events'] if row['id']==item['id'])
        self.assertEqual(answer.outcome,assessed['assessment']['outcome'])

    def test_nearby_encounter_is_queued_once_during_a_high_directive_then_starts(self):
        planner=self.s.planner
        for name,x in ((self.a,296),(self.b,360)):
            self.s.report_arrival(name,map_id=J3,room_id='R01',x=x,y=488,arrived=True)
        command=planner.whisper(self.b,WhisperAction(type='stay',minutes=1))
        planner._start(command)
        item=planner.queue_social_visit(self.a,self.b,'核对方法')
        planner.tick(time.monotonic(),0)
        self.assertEqual(command['id'],self.focus(self.b)['id'])
        entry,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual(('scheduled','medium'),(entry['phase'],entry['content']['tier']))
        planner.tick(time.monotonic(),0)
        self.assertEqual([entry['id']],[e['id'] for e in self.s.game_events.timeline(self.b)['events']])
        planner.tick(time.monotonic(),1/self.s.speed)
        current=self.focus(self.b)
        self.assertEqual((entry['id'],'waiting','medium'),(current['id'],current['phase'],current['tier']))
        self.assertEqual(item['id'],planner.encounter_waits[self.b])

    def test_high_directive_preempts_encounter_wait_without_cancelling_the_pursuit(self):
        planner=self.s.planner
        item=planner.queue_social_visit(self.a,self.b,'请教方法')
        for name,x in ((self.a,296),(self.b,360)):
            self.s.report_arrival(name,map_id=J3,room_id='R01',x=x,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        hold_id=self.focus(self.b)['id']
        self.assertEqual('encounter_wait',self.focus(self.b)['kind'])
        command=planner.whisper(self.b,WhisperAction(type='stay'))
        planner.tick(time.monotonic(),0)
        self.assertIs(item,planner.current_item(self.a))
        self.assertIs(command,planner.current_item(self.b))
        self.assertNotIn(self.b,planner.encounter_waits)
        stopped=next(e for e in self.s.game_events.timeline(self.b)['events'] if e['id']==hold_id)
        self.assertEqual('interrupted',stopped['result']['status'])

    def test_encounter_wait_ends_when_seeker_leaves_the_radius(self):
        planner=self.s.planner
        routine=planner._make(self.b,'baseline',f'{J3}/R01',self.s.world.clock.now,'baseline','基础作息')
        planner._start(routine)
        planner.queue_social_visit(self.a,self.b,'核对方法')
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=360,y=488,arrived=True,activity_id=routine['id'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=264,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        wait=self.focus(self.b)
        self.assertEqual('encounter_wait',wait['kind'])

        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=263,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        self.assertEqual(routine['id'],self.focus(self.b)['id'])
        self.assertNotIn(self.b,planner.encounter_waits)
        history=self.s.game_events.timeline(self.b)['events']
        self.assertEqual((wait['id'],'interrupted'),(history[0]['id'],history[0]['result']['status']))

    def test_mutual_pursuit_chooses_one_waiter_until_a_single_invitation_ends(self):
        planner=self.s.planner
        first=planner.whisper(self.a,WhisperAction(type='talk_to',npc=self.b))
        second=planner.whisper(self.b,WhisperAction(type='talk_to',npc=self.a))
        for name,x in ((self.a,296),(self.b,360)):
            self.s.report_arrival(name,map_id=J3,room_id='R01',x=x,y=488,arrived=False)
        with patch('backend.daily_planner.random.choice',return_value=second) as choose:
            planner.tick(time.monotonic(),0)
            self.assertEqual({self.a:second['id']},planner.encounter_waits)
            planner.tick(time.monotonic(),0)
            choose.assert_called_once_with((first,second))
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=310,y=488,arrived=True)
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=296,y=488,arrived=True)
        planner.tick(time.monotonic(),0)
        self.assertFalse(self.s.scheduler.event_talks, 'Wait for the actual stop acknowledgement')
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=296,y=488,arrived=False,
                              encounter_wait=second['id'])
        no=ConversationConsent(decision='decline',reason='刚好有事',reply='现在不方便。')
        with patch.object(self.s.engine.agents[self.a],'consent_to_event',return_value=no) as gate:
            with self.s.lock:
                planner.tick(time.monotonic(),0)
            self.s.scheduler.event_talks[second['id']].result(timeout=10)
            gate.assert_called_once()
        self.assertEqual(('declined','declined'),(first['state'],second['state']))
        self.assertFalse(planner.encounter_waits)
        merged=next(event for event in self.s.game_events.timeline(self.a)['events']
                    if event['id']==self.s.event_runtime.activity_id(first))
        self.assertEqual(self.s.event_runtime.activity_id(second),merged['result']['shared_event_id'])
        lines=[row for row in self.s.bus.history() if row.get('speech')=='npc_dialogue']
        self.assertEqual({second['id']},{row['conversation_id'] for row in lines})

    def test_cast_exit_takes_priority_over_whisper_classification(self):
        def parse(*_,**__):
            self.s.set_cast(self.a,False)
            self.s.set_cast(self.a,True)
            return WhisperAction(type='go_to',area='j3_lab')

        with fixtures.SocialTests.profile_client(self) as client,patch.object(self.s.llm,'chat_json',side_effect=parse):
            response=client.post('/world/whisper',json={'npc':self.a,'text':'去实验室'})
        self.assertEqual(409,response.status_code)
        self.assertTrue(self.s.onstage(self.a))
        self.assertFalse(any(item['source']=='whisper' for item in self.s.planner.records.values()))
        self.assertEqual(['stage_enter','stage_exit'],
            [item['kind'] for item in self.s.game_events.timeline(self.a)['events']])

    def test_cast_exit_invalidates_an_invitation_even_after_immediate_return(self):
        planner=self.s.planner
        item=planner.whisper(self.a,WhisperAction(type='talk_to',npc=self.b))
        planner._start(item)
        self.report_positions([self.a,self.b])
        called,return_answer=Event(),Event()

        def reply(_):
            called.set()
            if not return_answer.wait(5):
                raise AssertionError('test did not release model')
            return ConversationConsent(decision='accept',reason='可以聊',reply='这句答复已经过期')

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',side_effect=reply):
            with self.s.lock:
                self.start_nearby_invitation(item)
                future=self.s.scheduler.event_talks[item['id']]
            try:
                self.assertTrue(called.wait(5))
                self.s.set_control(paused=True)
                self.s.set_cast(self.b,False)
                self.assertEqual('cancelled',item['state'])
                self.assertFalse({self.a,self.b}&self.s.busy)
                self.s.set_cast(self.b,True)
                self.s.busy.add(self.b)  # 新行动已占用角色，旧任务不得解除它。
            finally:
                return_answer.set()
            future.result(timeout=5)
        self.assertIn(self.b,self.s.busy)
        identity=self.s.event_runtime.activity_id(item)
        event=next(row for row in self.s.game_events.timeline(self.a)['events'] if row['id']==identity)
        self.assertEqual('cancelled',event['result']['status'])
        self.assertEqual([self.a],[line['speaker'] for line in event['content']['transcript']])

    def test_talk_target_rejects_and_resumes_the_original_activity(self):
        item=self.library()
        planner=self.s.planner
        routine=planner._make(self.b,'baseline',self.s.world.area_of(self.b),self.s.world.clock.now,
                              'baseline','基础作息')
        planner._start(routine)
        other=routine['id']
        self.decide(dict(type='talk_to',npc=self.b,topic='请教如何验证方法'))
        followup=self.s.planner.current_item(self.a)
        bubbles=[event for event in self.s.bus.history() if event.get('speech')=='event_self_talk']
        self.assertEqual(1,len(bubbles))
        self.assertEqual('conversation:'+followup['id'],bubbles[0]['game_event_id'])
        self.assertEqual(item['id'],bubbles[0]['source_event_id'])
        self.assertIn('（自言自语）资料查完了',bubbles[0]['text'])
        self.assertEqual(other,self.focus(self.b)['id'])
        self.s.world.set_area(self.b,self.s.world.area_of(self.a))
        self.report_positions([self.a,self.b])
        with patch.object(self.s.engine.agents[self.b],'consent_to_event',
                          return_value=ConversationConsent(decision='decline',reason='正在准备组会',
                                                           reply='我正在准备组会，今天就先不聊了。')):
            self.start_nearby_invitation(followup)
            self.s.scheduler.event_talks[followup['id']].result(timeout=10)
        identity='conversation:'+followup['id']
        entry=next(event for event in self.s.game_events.timeline(self.a)['events'] if event['id']==identity)
        self.assertEqual((identity,item['id'],'declined'),(
                         entry['id'],entry['source_event_id'],entry['result']['status']))
        self.assertEqual(other,self.focus(self.b)['id'])
        self.assertEqual([self.a,self.b],entry['participants'])
        self.assertEqual('我正在准备组会，今天就先不聊了。',entry['content']['transcript'][-1]['text'])
        self.assertNotIn('reason',entry['result']['consent'])
        recipient_event=next(event for event in self.s.game_events.timeline(self.b)['events'] if event['id']==identity)
        self.assertEqual('正在准备组会',recipient_event['result']['consent']['reason'])
        self.assertNotIn('purpose',recipient_event['content'])
        self.assertIsNone(recipient_event['source'])
        lines=[e for e in self.s.bus.history() if e['kind']=='dialogue_line' and e['conversation_id']==followup['id']]
        self.assertEqual([(1,self.a),(2,self.b)],[(e['sequence'],e['speaker']) for e in lines])
        self.assertTrue(all(line['ordered'] for line in lines))
        self.assertFalse(self.s.world.can_chat(self.a,self.b)[0])
        self.assert_coverage()

    def test_accepted_talk_shares_running_event_and_restores_partner_activity(self):
        item=self.library()
        partner=self.s.planner._make(self.b,'baseline',self.s.world.area_of(self.b),
                                  self.s.world.clock.now,'baseline','基础作息')
        self.s.planner._start(partner)
        self.decide(dict(type='talk_to',npc=self.b,topic='请教如何验证方法'))
        followup=self.s.planner.current_item(self.a)
        identity='conversation:'+followup['id']
        self.s.world.set_area(self.b,self.s.world.area_of(self.a))
        self.report_positions([self.a,self.b])
        opener=self.s.npcs[self.a].opener
        seen=[]

        def check_pair(*args,**kwargs):
            seen.append((self.focus()['id'],self.focus(self.b)['id']))
            return opener(*args,**kwargs)

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',
                          return_value=ConversationConsent(decision='brief',reason='可以聊几句',
                                                           reply='可以，先聊一个问题。')):
            with patch.object(self.s.npcs[self.a],'opener',side_effect=check_pair):
                self.start_nearby_invitation(followup)
                self.s.scheduler.event_talks[followup['id']].result(timeout=20)
        self.assertEqual([(identity,identity)],seen)
        self.assertEqual(partner['id'],self.focus(self.b)['id'])
        entry=next(event for event in self.s.game_events.timeline(self.a)['events'] if event['id']==identity)
        self.assertEqual((identity,item['id'],'conversation_completed'),(
                         entry['id'],entry['source_event_id'],entry['kind']))
        self.assertTrue(entry['result']['consent']['accept'])
        self.assertEqual('brief',entry['result']['consent']['decision'])
        self.assertEqual([self.a,self.b,self.a,self.b],[line['speaker'] for line in entry['content']['transcript']])
        self.assert_coverage()

    def test_whisper_takes_over_active_autonomous_dialogue_without_late_lines(self):
        planner=self.s.planner
        self.report_positions([self.a,self.b])
        invite=planner.queue_social_visit(self.a,self.b,'核对方法')
        planner._start(invite)
        speaking,respond=Event(),Event()

        def opener(*args,**kwargs):
            speaking.set()
            if not respond.wait(5):
                raise AssertionError('test did not release model')
            return '不应出现的台词'

        consent=ConversationConsent(decision='accept',reason='有空',reply='好，我们聊聊。')
        with patch.object(self.s.engine.agents[self.b],'consent_to_event',return_value=consent), \
             patch.object(self.s.npcs[self.a],'opener',side_effect=opener):
            with self.s.lock:
                self.start_nearby_invitation(invite)
                future=self.s.scheduler.event_talks[invite['id']]
            try:
                self.assertTrue(speaking.wait(5))
                self.assertEqual('active',self.focus()['phase'])
                with self.s.lock:
                    command=planner.whisper(self.b,WhisperAction(type='stay'))
                    planner.tick(time.monotonic(),0)
                self.assertIs(command,planner.current_item(self.b))
                self.assertIn('接管，结束交谈',self.focus(self.b)['schedule_reason'])
                self.assertEqual('cancelled',invite['state'])
                self.assertFalse({self.a,self.b}&self.s.busy)
                self.s.busy.add(self.a)
            finally:
                respond.set()
            future.result(timeout=5)
        self.assertIn(self.a,self.s.busy)
        entry=next(row for row in self.s.game_events.timeline(self.b)['events']
                   if row['id']==self.s.event_runtime.activity_id(invite))
        self.assertEqual('interrupted',entry['result']['status'])
        self.assertFalse(any(line.get('text')=='不应出现的台词' for line in self.s.bus.history()))

    def test_pending_checkpoint_recovers_and_records_gateway_failure(self):
        item=self.library()
        self.s.stop()
        self.s=SocialService()
        self.s.llm.cfg=SimpleNamespace(api_key='mock')
        self.assertEqual('evaluating',self.focus()['phase'])
        with patch.object(self.s.llm,'chat_json',return_value=None):
            runtime=self.s.event_runtime
            runtime.poll(time.monotonic())
            future,_,_=runtime.jobs[(item['id'],self.a)]
            future.result(timeout=5)
            runtime.poll(time.monotonic())
        event,=self.s.game_events.timeline(self.a)['events']
        self.assertEqual(item['id'],event['id'])
        self.assertEqual({'status':'unavailable'},event['assessment'])
        self.assertEqual('completed',event['result']['status'])
        self.assertEqual('idle',self.focus()['kind'])
        self.assert_coverage()

    def test_scheduled_meeting_clamps_accepted_invitation_to_defer_when_deadline_is_near(self):
        self.s.llm.cfg=SimpleNamespace(api_key='mock')
        planner=self.s.planner
        now=self.s.world.clock.now
        area=AREAS[self.s.world.area_of(self.b)]
        routine=planner._make(self.b,'baseline',area['id'],now,'baseline','基础作息')
        planner._start(routine)
        self.report_positions([self.a,self.b])
        self.assertIn('accept',planner.conversation_window(self.b)['allowed'])
        planner._make(self.b,'seminar',area['id'],now+2,'event','即将开始组会')
        meeting=self.s.request_meeting(self.a,self.b,area['id'])
        def accept_after_deadline(_context):
            self.s.world.clock.advance(3)
            return ConversationConsent(decision='accept',reason='现在有空',reply='我现在有空。')

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',side_effect=accept_after_deadline) as decide:
            self.s.scheduler.meetings(time.monotonic(),0)
            for future in self.s.scheduler.jobs.values():
                future.result(timeout=10)
        self.assertEqual(['defer','decline'],decide.call_args.args[0]['gate']['allowed'])
        self.assertEqual('cancelled',meeting['state'])
        entry,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual('deferred',entry['result']['status'])
        self.assertEqual('accept',entry['result']['consent']['requested_decision'])
        self.assertIn('忙完了再聊',entry['content']['transcript'][-1]['text'])
        self.assertEqual(routine['id'],self.focus(self.b)['id'])
        self.assert_coverage()

    def test_directed_visit_only_changes_the_initiator_and_emits_no_monologue(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.social import install
        app=FastAPI()
        install(app,lambda:self.s)
        other_focus=self.focus(self.b)['id']
        self.s.set_control(paused=True)
        with TestClient(app) as client:
            response=client.post('/social/visit',json={'a':self.a,'b':self.b})
            self.assertEqual(200,response.status_code)
            identity=response.json()['activity']['id']
            self.assertEqual(self.b,self.s.planner.records[identity]['partner'])
            self.assertEqual(other_focus,self.focus(self.b)['id'])
            self.assertFalse(any(e.get('speech')=='event_self_talk' for e in self.s.bus.history()))

    def test_directed_visit_waits_for_actual_arrival_at_the_approach_point(self):
        self.s.report_arrival(self.a,map_id=ZX2,room_id='zx2_lib',x=0,y=0,arrived=True)
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=296,y=488,arrived=True)
        item=self.s.planner.queue_social_visit(self.a,self.b,'验证抵达',directed=True)
        self.s.planner._start(item)
        self.s.planner._talk(item,time.monotonic(),0)
        point=item['interaction_point']
        self.assertEqual(point,self.s.world_snapshot()['npcs'][self.a]['target_position'])
        self.assertFalse(self.s.scheduler.event_talks)
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=376,y=488,arrived=True)
        self.s.planner._talk(item,time.monotonic(),0)
        self.assertEqual(point,item['interaction_point'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=point['x'],y=point['y'],arrived=False)
        self.s.planner._talk(item,time.monotonic(),0)
        self.assertFalse(self.s.scheduler.event_talks)
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=point['x'],y=point['y'],arrived=True)
        self.s.planner._talk(item,time.monotonic(),0)
        new_point=item['interaction_point']
        self.assertNotEqual(point,new_point)
        self.assertFalse(self.s.scheduler.event_talks)
        snapshots=[]

        def capture_pair():
            npcs=self.s.world_snapshot()['npcs']
            snapshots.append((npcs[self.a]['current_event']['phase'],
                              npcs[self.a].get('target_position'),npcs[self.a]['busy'],npcs[self.b]['busy']))

        def consent(_context):
            capture_pair()
            return ConversationConsent(decision='brief',reason='现在能聊一会儿',reply='可以。')

        opener=self.s.npcs[self.a].opener
        def open_conversation(*args,**kwargs):
            capture_pair()
            return opener(*args,**kwargs)

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',side_effect=consent), \
             patch.object(self.s.npcs[self.a],'opener',side_effect=open_conversation):
            self.s.report_arrival(self.a,map_id=J3,room_id='R01',**new_point,arrived=True)
            self.start_nearby_invitation(item)
            self.s.scheduler.event_talks[item['id']].result(timeout=15)
        self.assertEqual([('waiting',new_point,True,True),('active',new_point,True,True)],snapshots)
        self.assertEqual('completed',item['state'])
        self.assertNotIn('target_position',self.s.world_snapshot()['npcs'][self.a])

    def test_when_participants_move_apart_only_invitation_is_spoken(self):
        self.library()
        self.decide(dict(type='talk_to',npc=self.b,topic='想核对方法'))
        item=self.s.planner.current_item(self.a)
        self.s.world.set_area(self.b,self.s.world.area_of(self.a))
        self.report_positions([self.a,self.b])

        def move_away(_context):
            self.s.positions[self.b]['x']+=1000
            return ConversationConsent(decision='accept',reason='可以帮忙',reply='可以。')

        with patch.object(self.s.engine.agents[self.b],'consent_to_event',side_effect=move_away):
            self.start_nearby_invitation(item)
            self.s.scheduler.event_talks[item['id']].result(timeout=10)
        entry=next(row for row in self.s.game_events.timeline(self.b)['events'] if row['kind']=='conversation')
        self.assertEqual('cancelled',entry['result']['status'])
        self.assertNotIn('consent',entry['result'])
        self.assertEqual([self.a],[line['speaker'] for line in entry['content']['transcript']])

    def test_seeker_stops_close_enough_without_walk_to_the_exact_point(self):
        planner=self.s.planner
        item=planner.queue_social_visit(self.a,self.b,'请教方法')
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=296,y=488,arrived=False)
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=360,y=488,arrived=True)
        planner.tick(time.monotonic(),0)
        self.assertNotEqual(dict(x=360,y=488),item['interaction_point'])
        self.s.report_arrival(self.b,map_id=J3,room_id='R01',x=360,y=488,arrived=False,encounter_wait=item['id'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=342,y=488,arrived=False)
        planner.tick(time.monotonic(),0)
        self.assertNotEqual(dict(x=342,y=488),item['interaction_point'])
        self.assertTrue(self.s.world_snapshot()['npcs'][self.a]['hold_position'])
        self.assertFalse(self.s.scheduler.event_talks)
        self.s.report_arrival(self.a,map_id=J3,room_id='R01',x=342,y=488,arrived=True)
        with patch.object(self.s.engine.agents[self.b],'consent_to_event',return_value=ConversationConsent(
                decision='decline',reason='稍后',reply='等一下再聊。')):
            with self.s.lock:
                planner.tick(time.monotonic(),0)
            self.s.scheduler.event_talks[item['id']].result(timeout=5)
        self.assertEqual('declined',item['state'])

    def test_restart_during_invitation_preserves_spoken_line_and_releases_event(self):
        self.library()
        self.decide(dict(type='talk_to',npc=self.b,topic='想核对方法'))
        item=self.s.planner.current_item(self.a)
        identity='conversation:'+item['id']
        self.s.world.set_area(self.b,self.s.world.area_of(self.a))
        self.s.invitation_line(item['id'],self.a,self.b,'想核对方法',
                               [dict(speaker=self.a,text='方便核对一下方法吗？')])
        self.s.stop()
        self.s=SocialService()
        entry,=self.s.game_events.timeline(self.b)['events']
        self.assertEqual((identity,'cancelled'),(entry['id'],entry['result']['status']))
        self.assertEqual(['方便核对一下方法吗？'],[line['text'] for line in entry['content']['transcript']])
        self.assertNotEqual(identity,self.focus()['id'])
        self.assert_coverage()

    def test_conversation_outcome_suspends_and_then_resumes_existing_activity(self):
        self.a=next(name for name,card in self.s.cards.items() if card.role=='student' and name!=self.b)
        self.s.llm.cfg=SimpleNamespace(api_key='mock')
        planner=self.s.planner
        area=AREAS[self.s.world.area_of(self.a)]
        original=planner._make(self.a,'baseline',area['id'],self.s.world.clock.now,'baseline','基础作息')
        planner._start(original)
        self.s.report_arrival(self.a,map_id=area['map_id'],room_id=area['room_id'],arrived=True)
        original['dwell_minutes']=45
        self.report_positions([self.a,self.b])
        conversation=self.s.run_conversation(self.a,self.b,'交流查阅中发现的问题')
        identity='conversation:'+conversation['conversation_id']
        self.assertEqual(original['id'],self.focus()['id'])
        choice=dict(type='perform_at',kind='research',area=f'{J3}/R03',purpose='验证对话中提出的方法',minutes=60)
        runtime=self.s.event_runtime
        with patch.object(self.s.engine.agents[self.a],'assess_event',return_value=EventAssessment(
                outcome='想到一个可验证的方法',reason='对话解决了原来的疑问',action=choice)):
            with patch.object(self.s.engine.agents[self.b],'assess_event',return_value=EventAssessment(
                    outcome='提了一个建议',reason='继续原计划',action=None)):
                runtime.poll(time.monotonic())
                for future,_,_ in runtime.jobs.values():
                    future.result(timeout=5)
                runtime.poll(time.monotonic())
        first=self.s.game_events.timeline(self.a)['events'][0]
        second=self.s.game_events.timeline(self.b)['events'][0]
        self.assertEqual(identity,first['id'])
        self.assertEqual(identity,second['id'])
        self.assertNotEqual(first['assessment']['outcome'],second['assessment']['outcome'])
        self.assertIsNone(second['assessment']['action'])
        self.s.llm.cfg.api_key=''
        planner.tick(time.monotonic(),0)
        followup=planner.current_item(self.a)
        self.assertEqual(first['assessment']['next_event_id'],followup['id'])
        self.assertEqual('suspended',original['state'])
        self.s.report_arrival(self.a,map_id=J3,room_id='R03',arrived=True,activity_id=followup['id'])
        planner._checkpoint(followup)
        self.assertEqual(original['id'],self.focus()['id'])
        planner.tick(time.monotonic(),0)
        self.assertEqual(original['id'],planner.current_item(self.a)['id'])
        self.assertEqual(45,original['dwell_minutes'])
        self.assert_coverage()

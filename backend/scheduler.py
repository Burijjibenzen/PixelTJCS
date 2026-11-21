"""M3: 世界循环只做计时/候选检测；感知→决策→行动→对话由 LangGraph 编排。"""
import random
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from backend.config import SOCIAL
from backend.world import AREAS, CastChanged


class ScheduleState(TypedDict, total=False):
    a: str
    b: str
    trigger: str
    meeting_id: str
    forced: bool
    ask_consent: bool
    colocated: bool
    candidate: dict
    decision: dict
    conversation_id: str
    result: dict
    tier: str
    valid: bool


class Scheduler:
    def __init__(self, service):
        self.s = service
        self.stop_event = threading.Event()
        self.thread = None
        self.pool = ThreadPoolExecutor(max_workers=SOCIAL.max_conversations, thread_name_prefix='M3-graph')
        self.maintenance = ThreadPoolExecutor(max_workers=1,thread_name_prefix='M3-maintenance')
        self.jobs = {}
        self.event_talks = {}
        self.next_decision = {}
        self.last_maintenance = service.world.clock.now
        self.last_snapshot = 0
        graph = StateGraph(ScheduleState)
        for name in ('perceive','decide','act','dialogue'):
            graph.add_node(name,getattr(self,name))
        graph.add_edge(START,'perceive')
        graph.add_conditional_edges('perceive', lambda s: 'decide' if s['valid'] else END)
        graph.add_edge('decide','act')
        graph.add_conditional_edges('act', lambda s: 'dialogue' if s['decision']['action']=='chat' and s['valid'] else END)
        graph.add_edge('dialogue',END)
        self.graph = graph.compile(checkpointer=service.engine.checkpointer)

    def start(self):
        with self.s.lock:self.s.clock_sample=time.monotonic()
        self.thread = threading.Thread(target=self.loop,daemon=True,name='M3-clock')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.s.lock:
            self.s.sync_clock()
            self.s.clock_sample=None
        if self.thread:
            self.thread.join(timeout=3)
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.maintenance.shutdown(wait=True,cancel_futures=True)

    def loop(self):
        last=time.monotonic()
        while not self.stop_event.wait(SOCIAL.tick_seconds):
            now=time.monotonic()
            elapsed,last=now-last,now
            try:
                self.tick(now,elapsed)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('M3 tick failed')

    def tick(self,now,elapsed=0):
        s=self.s
        encounter_changed=False
        with s.lock:
            s.player.expire_engagement(now)
            if s.clock_sample is None:s.step_clock(elapsed)
            else:s.sync_clock(now)
            # 只清理过期显示数据，气泡生命周期不参与角色占用。
            if not s.paused:
                for key,playback in list(s.live_lines.items()):
                    if not playback.get('finished'):
                        s.capture_playing_lines(playback,now)
                    if playback['end']<=now or playback.get('finished'):
                        del s.live_lines[key]
                        s.emit('conversation_released',conversation_id=key,participants=playback['participants'])
            self.jobs={p:f for p,f in self.jobs.items() if not f.done()}
            self.event_talks={key:future for key,future in self.event_talks.items() if not future.done()}
            if not s.paused:
                s.boards.tick(now,elapsed)
                encounter_changed=s.planner.tick(now,elapsed)
                self.meetings(now,elapsed)
                self.natural(now)
                if (s.maintenance_due or s.world.clock.now-self.last_maintenance>=SOCIAL.maintenance_minutes) and (s.future is None or s.future.done()):
                    self.last_maintenance=s.world.clock.now
                    s.maintenance_due=False
                    s.future=self.maintenance.submit(s.maintain)
        # 快照推送不能等待模型；也不在 async WebSocket 线程内执行数据库操作。
        if not s.paused and (encounter_changed or now-self.last_snapshot>=1):
            self.last_snapshot=now
            s.emit('world',snapshot=s.world_snapshot())

    def meetings(self,now,elapsed):
        s=self.s;claimed=set()
        for m in s.active_meetings():
            if m['state'] not in ('talking','deciding') and not s.planner.meeting_eligible(m):
                self._wait_meeting(m,elapsed,'等待更优先的活动完成');continue
            pair={m['a'],m['b']};conflict=bool(pair&claimed);claimed.update(pair)
            if m['state'] in ('talking','deciding'):
                continue
            if conflict or pair&s.planner.encounter_participants() or not s.available(*pair):
                self._wait_meeting(m,elapsed,'等待前序会面或角色空闲');continue
            m['wait_seconds']=0.0
            if m['area'] is None:
                m['area']=AREAS[s.world.area_of(m['a'])]['id']
            online=all(now-s.seen.get(n,-100)<SOCIAL.presence_seconds for n in pair)
            arrived=all(AREAS[s.world.area_of(n)]['id']==m['area'] for n in pair) and s.ready_pair(m['a'],m['b'])
            if not online or not arrived:
                m.update(state='moving',detail='等待客户端位置上报' if not online else '前往会面，等待双方抵达')
                m['travel_seconds']+=elapsed
                if m['travel_seconds']>=SOCIAL.meeting_timeout:
                    m.update(state='failed',detail='会面超时，请检查路线后重试')
                continue
            if not s.world.can_chat(m['a'],m['b'])[0]:
                m.update(state='cooldown',detail=s.world.can_chat(m['a'],m['b'])[1]);continue
            if len(s.busy)>=SOCIAL.max_conversations*2:
                m.update(state='ready',detail='等待对话名额');continue
            self.submit(m['a'],m['b'],m['trigger'],m)

    def _wait_meeting(self, meeting, elapsed, detail):
        waiting = meeting.get('wait_seconds', 0.0) + max(0.0, elapsed)
        meeting['wait_seconds'] = waiting
        if waiting >= SOCIAL.meeting_wait_timeout:
            meeting.update(state='failed', detail='等待对方空闲超时，已取消本次会面')
        else:
            meeting.update(state='waiting', detail=detail)

    def probability(self,a,b):
        # 好感度0/50/100对应基础概率的0.5/1/1.5倍；规则筛选后模型仍可拒聊。
        return min(1,SOCIAL.base_chat_probability*(0.5+self.s.relations.get(a,b)/100))

    def natural(self,now):
        s=self.s
        reserved={n for m in s.active_meetings() if not m.get('activity_id')
                  for n in (m['a'],m['b'])} | s.planner.encounter_participants()
        candidates=[(a,b,True) for a,b,_ in s.world.co_located_pairs() if s.valid_pair(a,b)]
        # 高关系允许主动找上门，仍由模型决定 move；动作只下发目的地。
        names=[name for name in s.npcs if s.onstage(name)]
        candidates += [(a,b,False) for i,a in enumerate(names) for b in names[i+1:]
                       if s.relations.get(a,b)>=SOCIAL.visit_relation
                       and AREAS[s.world.area_of(a)]['id']!=AREAS[s.world.area_of(b)]['id']]
        random.shuffle(candidates)
        for a,b,colocated in candidates:
            if len(s.busy)>=SOCIAL.max_conversations*2:
                break
            pair=tuple(sorted((a,b)))
            if s.planner.blocks_interaction(a) or s.planner.blocks_interaction(b):
                continue
            if not colocated and any((s.planner.current_item(n) or {}).get('phase')=='travelling' for n in pair):
                continue  # 已上路时不再临时起意找远处好友；保留实际相遇和显式会面。
            if set(pair)&reserved or not s.available(a,b) or not s.world.can_chat(a,b)[0]:
                continue
            if not all(now-s.seen.get(n,-100)<SOCIAL.presence_seconds for n in pair) or now<self.next_decision.get(pair,0):
                continue
            self.next_decision[pair]=now+SOCIAL.decision_interval
            if random.random()<self.probability(a,b):
                self.submit(a,b,'实际相遇，聊聊近况' if colocated else '想找熟悉的同学聊聊',tier='medium')

    def submit(self,a,b,trigger,meeting=None,*,tier=None):
        s=self.s;pair=tuple(sorted((a,b)))
        s.busy.update(pair)
        if meeting:
            meeting.update(state='deciding',detail='准备对话')
        state=dict(a=a,b=b,trigger=trigger,meeting_id=meeting['id'] if meeting else '',
                   forced=meeting is not None,ask_consent=True,
                   conversation_id=uuid.uuid4().hex)
        if meeting:
            tier=meeting.get('tier') or ('low' if meeting.get('activity_id') else tier)
        if tier:
            state['tier']=tier
        s.cast_duos[state['conversation_id']]=(pair,tier or 'medium',state['meeting_id'])
        self.jobs[pair]=self.pool.submit(self.execute,state,s.cast_stamp(*pair))

    def submit_event_talk(self,item,reverse=None):
        s=self.s
        a,b=item['npc'],item['partner']
        key=item['id']
        s.busy.update((a,b))
        s.cast_duos[key]=((a,b),item['tier'],'')
        self.event_talks[key]=self.pool.submit(self.event_talk,item,reverse,s.cast_stamp(a,b))

    def invite(self,a,b,topic,run_id,candidate,meeting_id='',*,tier=None,reason=''):
        s=self.s
        with s.conversation_step():
            if not s.valid_pair(a,b):
                return 'cancelled','双方未能当面发起邀约',None
            gate=s.planner.conversation_window(b)
            evidence=s.planner.context(b)
            context=dict(inviter=a,topic=topic,perception=s.perception(b),gate=gate,
                         relationship=s.relations.get(b,a),memories=evidence['memories'],
                         insights=evidence['insights'])
            transcript=[dict(speaker=a,text=f'{b}，现在方便聊一下吗？{topic}')]
            s.invitation_line(run_id,a,b,topic,transcript,tier=tier,reason=reason)

        decision=s.engine.agents[b].consent_to_event(context)
        with s.conversation_step():
            consent=None
            if not s.valid_pair(a,b):
                status,detail='cancelled','双方情况变化，邀约中断'
            elif decision is None:
                status,detail='unavailable','未能取得对方的交谈决定'
            else:
                consent=decision.model_dump()
                gate=s.planner.conversation_window(b)
                if consent['decision'] not in gate['allowed']:
                    choice='brief' if 'brief' in gate['allowed'] else 'defer'
                    consent.update(requested_decision=consent['decision'],decision=choice,reason=gate['reason'],
                                   reply='我还有事要做，可以先聊一个问题。' if choice=='brief'
                                   else '我现在走不开，等忙完了再聊吧。')
                consent.update(accept=consent['decision'] in ('accept','brief'),decision_by=b)
                transcript.append(dict(speaker=b,text=consent['reply']))
                s.invitation_line(run_id,a,b,topic,transcript,consent)
                status='completed' if consent['accept'] else 'deferred' if consent['decision']=='defer' else 'declined'
                detail={'completed':'双方已完成交谈','deferred':'对方提议稍后再聊','declined':'对方婉拒了邀约'}[status]
            if status!='completed':
                s.finish_invitation(run_id,a,b,status,detail,consent,meeting_id)
                return status,detail,None
        result=s.run_conversation(a,b,topic,scheduled=dict(run_id=run_id,candidate=candidate,
            meeting_id=meeting_id,require_near=True,invitation=dict(transcript=transcript,consent=consent)))
        return status,detail,result

    def event_talk(self,item,reverse,stamp):
        s=self.s
        a,b=item['npc'],item['partner']
        try:
            with s.cast_task(stamp,conversation=item['id']):
                status,detail,_=self.invite(a,b,item['purpose'],item['id'],{})
                with s.conversation_step():
                    s.planner.finish_event_talk(item,status,detail)
                    if reverse and s.planner.current_item(b) is reverse:
                        s.planner.finish_event_talk(reverse,status,'双方互相寻找，合并为同一次邀约。'+detail,
                                                   shared_event_id=s.event_runtime.activity_id(item))
        except CastChanged:
            return
        except Exception:
            with s.lock:
                if item['id'] in s.cast_duos and s.cast_matches(stamp):
                    logging.getLogger(__name__).exception('Event conversation failed: %s',item['id'])
                    s.fail(item['id'],a,b,'EventConversationError')
                    s.planner.finish_event_talk(item,'cancelled','对话未完成')
        finally:
            with s.lock:
                if s.cast_duos.pop(item['id'],None) is not None:
                    s.busy.difference_update((a,b))

    def execute(self,state,stamp):
        s=self.s
        pair=(state['a'],state['b'])
        run=state['conversation_id']
        try:
            with s.cast_task(stamp,conversation=run):
                result=self.graph.invoke(state,{'configurable':{'thread_id':'schedule:'+run},'recursion_limit':30})
                return result.get('result')
        except CastChanged:
            return None
        except Exception as exc:
            with s.lock:
                if run in s.cast_duos and s.cast_matches(stamp):
                    s.fail(run,*pair,type(exc).__name__,state.get('meeting_id'))
        finally:
            with s.lock:
                if s.cast_duos.pop(run,None) is not None:
                    s.busy.difference_update(pair)
                    m=s.meeting(state.get('meeting_id',''))
                    if m and m['state']=='deciding':
                        m.update(state='waiting',detail='条件变化，等待下一次调度')

    def perceive(self,state):
        s=self.s;a,b=state['a'],state['b']
        with s.conversation_step():
            valid=s.world.can_chat(a,b)[0]
            colocated=s.valid_pair(a,b)
            if not state['forced'] and AREAS[s.world.area_of(a)]['id']==AREAS[s.world.area_of(b)]['id'] and not colocated:
                valid=False
        return {'valid':valid,'colocated':colocated,'candidate':s.gossip_candidate(a,b) or {}}

    def decide(self,state):
        a,b=state['a'],state['b']
        with self.s.conversation_step():
            pass
        decision=self.s.engine.agents[a].graph.invoke(dict(task='choose',other=b,
            trigger=state['trigger'],co_located=state['colocated'],forced=state['forced'],
            candidate=state['candidate'].get('content','')))['decision']
        return {'decision':decision}

    def act(self,state):
        s=self.s;a,b=state['a'],state['b'];action=state['decision']['action']
        with s.conversation_step():
            valid=s.world.can_chat(a,b)[0]
            if action=='move' and valid:
                if s.event_runtime.model_ready:
                    s.planner.queue_social_visit(a,b,state['decision']['intent'],reason=state['decision'].get('reason',''))
                else:
                    s.request_meeting(b,a,trigger=state['decision']['intent'],tier='medium')
            if action=='chat':
                valid=valid and s.valid_pair(a,b) and (state['forced'] or s.nearby(a,b))
            if state['meeting_id'] and (not valid or action!='chat'):
                m=s.meeting(state['meeting_id'])
                m.update(state='waiting',detail='条件变化，等待下一次调度')
        return {'valid':valid}

    def dialogue(self,state):
        s=self.s
        candidate=state['candidate'] if state['decision']['share_gossip'] else {}
        reason=state['decision'].get('reason') or state['trigger']
        if state.get('ask_consent') and s.event_runtime.model_ready:
            _,_,result=self.invite(state['a'],state['b'],state['decision']['intent'],
                                   state['conversation_id'],candidate,state['meeting_id'],tier=state.get('tier'),reason=reason)
            return {'result':result}
        result=s.run_conversation(state['a'],state['b'],state['decision']['intent'],
                         scheduled=dict(run_id=state['conversation_id'],candidate=candidate,meeting_id=state['meeting_id'],
                                        require_near=not state['forced'],tier=state.get('tier'),reason=reason))
        return {'result':result}

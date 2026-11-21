"""运行中的增长边界和服务重启后的世界事实。"""
import time
import unittest
from unittest.mock import patch

import test_social as fixtures
from backend.event_stream import EventStream
from backend.social import SocialService
from backend.world import AREAS


class RuntimePersistenceTests(unittest.TestCase):
    setUp = fixtures.SocialTests.setUp
    tearDown = fixtures.SocialTests.tearDown

    def test_recall_mixed_embedding_dimensions_keeps_keyword_relevance(self):
        with patch.object(self.s.llm, 'embed', side_effect=[[[1.0, 0.0]], [[0.0, 0.8, 0.6]], [[0.0, 1.0, 0.0]]]):
            now = self.s.world.clock.now
            original = self.s.memory.add(self.a, '研究计划', 'observation', 0.5, now)
            self.s.memory.add(self.a, '关于气象的闲聊', 'observation', 0.5, now)
            matches = self.s.memory.recall(self.a, '研究计划', now)
        self.assertEqual(original.id, matches[0].id)

    def test_restart_restores_actual_room_and_chat_cooldown(self):
        other = next(area for area in AREAS if AREAS[area]['id'] != AREAS[self.area]['id'])
        self.s.world.set_area(self.a, other)
        self.s.world.clock.minutes = 900
        self.s.world.mark_chatted(self.a, self.b)
        self.s.save_clock()
        self.s.stop()
        self.s = SocialService()
        self.assertEqual(900, self.s.world.clock.now)
        self.assertEqual(other, self.s.world.area_of(self.a))
        self.assertFalse(self.s.world.can_chat(self.a, self.b)[0])

    def test_unchanged_memory_and_paused_world_do_not_add_history(self):
        with self.s.database() as db:
            before = db.execute('SELECT COUNT(*) FROM m3_memory_history WHERE npc=?',(self.a,)).fetchone()[0]
        self.s.world.clock.advance(60)
        self.s.emit('memory_changed',npc=self.a)
        with self.s.database() as db:
            after = db.execute('SELECT COUNT(*) FROM m3_memory_history WHERE npc=?',(self.a,)).fetchone()[0]
        self.assertEqual(before, after)
        self.s.set_control(paused=True)
        with self.s.database() as db:
            events = db.execute('SELECT COUNT(*) FROM m3_events').fetchone()[0]
        self.s.scheduler.tick(time.monotonic()+2)
        with self.s.database() as db:
            self.assertEqual(events, db.execute('SELECT COUNT(*) FROM m3_events').fetchone()[0])

    def test_event_replay_is_bounded_after_restart(self):
        stream = EventStream(self.s.memory.cfg.db_path, history_limit=5)
        for index in range(12):
            stream.publish('probe',value=index)
        stream = EventStream(self.s.memory.cfg.db_path, history_limit=5)
        self.assertEqual([7,8,9,10,11], [event['value'] for event in stream.history()
                                              if event['kind']=='probe'])


if __name__ == '__main__':
    unittest.main()

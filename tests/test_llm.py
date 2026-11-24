import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from backend.config import LLM
from backend.llm import LLMClient, prioritize_player_reply


class LLMConcurrencyTests(unittest.TestCase):
    def test_player_uses_gateway_and_falls_back_for_rest_of_turn(self):
        class Assessment(BaseModel):
            delta: int

        cfg = replace(LLM, api_key='local', chat_model='Qwen', think_model='Qwen',
                      player_api_key='fake-gateway', player_api_model='Flash',
                      max_concurrent_requests=1)
        calls = []

        def client(provider):
            def create(*, model, messages, **_):
                prompt = messages[-1]['content']
                calls.append((provider, model, prompt))
                if provider == 'api' and prompt == 'failed reply':
                    raise TimeoutError()
                text = '{"delta": 2}' if prompt == 'assessment' else f'{provider} reply'
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

            sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            sdk.with_options = lambda **_: sdk
            return sdk

        with patch('backend.llm.OpenAI', side_effect=[client('api'), client('local')]), patch('backend.llm.httpx.Client'):
            llm = LLMClient(cfg)
        self.assertEqual('local reply', llm.chat('system', 'npc only'))
        with llm.player_turn(), llm._slot(None):
            self.assertEqual('api reply', llm.chat('system', 'player first', model='Qwen'))
            self.assertEqual(2, llm.chat_json('system', 'assessment', Assessment).delta)
        with llm.player_turn():
            self.assertEqual('local reply', llm.chat('system', 'failed reply'))
            self.assertEqual(2, llm.chat_json('system', 'assessment', Assessment).delta)
        with llm.player_turn():
            self.assertEqual('api reply', llm.chat('system', 'next turn'))
        self.assertEqual([
            ('local', 'Qwen', 'npc only'), ('api', 'Flash', 'player first'),
            ('api', 'Flash', 'assessment'), ('api', 'Flash', 'failed reply'),
            ('local', 'Qwen', 'failed reply'), ('local', 'Qwen', 'assessment'),
            ('api', 'Flash', 'next turn'),
        ], calls)

    def test_chat_and_embeddings_share_slots_and_expired_work_is_not_sent(self):
        full = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        active = peak = calls = 0

        def request(response):
            nonlocal active, peak, calls
            with lock:
                calls += 1
                active += 1
                peak = max(peak, active)
                if active == 3:
                    full.set()
            try:
                if not release.wait(5):
                    raise AssertionError('test did not release the pending requests')
                return response
            finally:
                with lock:
                    active -= 1

        chat = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='hello'))])
        embedding = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.5])])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: request(chat))),
            embeddings=SimpleNamespace(create=lambda **_: request(embedding)),
        )
        client.with_options = lambda **_: client
        with patch('backend.llm.OpenAI', return_value=client), patch('backend.llm.httpx.Client'):
            llm = LLMClient(replace(LLM, api_key='test-only', max_concurrent_requests=3))
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(llm.chat, 'system', 'user'), pool.submit(llm.embed, ['text']),
                       pool.submit(llm.chat, 'system', 'user')]
            try:
                self.assertTrue(full.wait(5))
                self.assertIsNone(pool.submit(llm.chat, 'system', 'late', timeout=0.05).result(2))
                self.assertEqual(3, calls)
            finally:
                release.set()
            self.assertEqual(['hello', [[0.5]], 'hello'], [task.result(5) for task in pending])
        self.assertEqual('hello', llm.chat('system', 'another request'))
        self.assertEqual(3, peak)

    def test_waiting_player_takes_next_free_slot_without_reserving_an_idle_slot(self):
        first_started = threading.Event()
        release_first = threading.Event()
        background_queued = threading.Event()
        player_queued = threading.Event()
        calls = []

        def complete(*, messages, **_):
            name = messages[-1]['content']
            calls.append(name)
            if name == 'in flight':
                first_started.set()
                if not release_first.wait(5):
                    raise AssertionError('test did not release the in-flight request')
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=name))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
        with patch('backend.llm.OpenAI', return_value=client), patch('backend.llm.httpx.Client'):
            llm = LLMClient(replace(LLM, api_key='test-only', max_concurrent_requests=1))

        original_wait = llm._slot_condition.wait_for

        def wait_for_slot(predicate, timeout=None):
            if not predicate():
                (player_queued if llm._foreground_waiters else background_queued).set()
            return original_wait(predicate, timeout)

        def player_reply():
            with prioritize_player_reply():
                return llm.chat('system', 'player')

        with patch.object(llm._slot_condition, 'wait_for', side_effect=wait_for_slot):
            with ThreadPoolExecutor(max_workers=3) as pool:
                first = pool.submit(llm.chat, 'system', 'in flight')
                try:
                    self.assertTrue(first_started.wait(2))
                    background = pool.submit(llm.chat, 'system', 'background')
                    self.assertTrue(background_queued.wait(2))
                    player = pool.submit(player_reply)
                    self.assertTrue(player_queued.wait(2))
                    self.assertEqual(['in flight'], calls)
                finally:
                    release_first.set()
                self.assertEqual('in flight', first.result(2))
                self.assertEqual('player', player.result(2))
                self.assertEqual('background', background.result(2))
        self.assertEqual(['in flight', 'player', 'background'], calls)

    def test_player_turn_keeps_local_slot_between_reply_and_postprocessing(self):
        llm = LLMClient(replace(LLM, api_key='', max_concurrent_requests=1))
        background_waiting = threading.Event()
        calls = []
        original_wait = llm._slot_condition.wait_for

        def wait_for_slot(predicate, timeout=None):
            if not predicate():
                background_waiting.set()
            return original_wait(predicate, timeout)

        def background():
            with llm._slot(None) as acquired:
                self.assertTrue(acquired)
                calls.append('npc background')

        with patch.object(llm._slot_condition, 'wait_for', side_effect=wait_for_slot):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with llm.reserve_player_slot(), prioritize_player_reply():
                    with llm._slot(None) as acquired:
                        self.assertTrue(acquired)
                        calls.append('reply')
                    queued = pool.submit(background)
                    self.assertTrue(background_waiting.wait(2))
                    with llm._slot(None) as acquired:
                        self.assertTrue(acquired)
                        calls.append('save turn')
                    self.assertEqual(['reply', 'save turn'], calls)
                queued.result(2)
        self.assertEqual(['reply', 'save turn', 'npc background'], calls)

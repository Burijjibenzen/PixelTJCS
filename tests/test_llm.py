import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from backend.config import LLM
from backend.llm import LLMClient


class LLMConcurrencyTests(unittest.TestCase):
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

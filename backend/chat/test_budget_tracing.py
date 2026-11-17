"""Tracing must not change budgets, repeat LLM calls or leak to other agents."""
import os
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from django.test import override_settings
from .llm import budget_tracing as tracing


class BudgetTracingTest(SimpleTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'BUDGET_ADJUST_LANGSMITH': 'true', 'LANGSMITH_TRACING': 'true',
                                    'LANGSMITH_API_KEY': 'synthetic-not-a-real-key'}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.root = Mock()
        start = patch.object(tracing, '_start', return_value=self.root)
        start.start()
        self.addCleanup(start.stop)

    def test_records_question_raw_reading_and_final_reply_once(self):
        client = Mock(model='synthetic-model')
        client.complete.return_value = '{"mine":true}'
        reply = {'status': 'replied', 'reply': {'lines': ['식비 예산을 얼마 늘릴까요?']}}
        def call():
            self.assertEqual(tracing.trace_llm(client, 'prompt', 'synthetic input', 9), '{"mine":true}')
            return reply
        with patch('chat.llm.live.schema_for', return_value=('budget_adjust_dialogue', {})):
            self.assertIs(tracing.trace_turn({'text': '식비 늘려줘'}, 'synthetic-conversation', call), reply)
        client.complete.assert_called_once_with('prompt', 'synthetic input', 9)
        self.root.create_child.assert_called_once()
        child = self.root.create_child.return_value
        child.end.assert_called_once_with(outputs={'output': '{"mine":true}'}, error=None)
        self.root.end.assert_called_once_with(outputs=reply, error=None)
        self.assertIsNone(tracing._active.get())

    def test_outside_budget_turn_does_not_trace_other_agents(self):
        client = Mock()
        tracing.trace_llm(client, 'spending prompt', 'input', None)
        client.complete.assert_called_once()
        self.root.create_child.assert_not_called()

    def test_upload_error_does_not_change_response_or_retry_llm(self):
        self.root.create_child.side_effect = RuntimeError('synthetic-secret')
        self.root.patch.side_effect = RuntimeError('synthetic-secret')
        client = Mock(model='synthetic-model')
        client.complete.return_value = '{}'
        with patch('chat.llm.live.schema_for', return_value=('budget_adjust_plan', {})), self.assertLogs(tracing.log, level='WARNING') as logs:
            reply = tracing.trace_turn({'text': '합성'}, 'talk', lambda: {
                'status': 'replied', 'reply': tracing.trace_llm(client, 'p', 'u', None)})
        self.assertEqual(reply['reply'], '{}')
        client.complete.assert_called_once()
        self.assertNotIn('synthetic-secret', '\n'.join(logs.output))
        self.assertIsNone(tracing._active.get())

    def test_llm_error_propagates_and_context_is_reset(self):
        error = ValueError('synthetic-secret')
        client = Mock(model='synthetic-model')
        client.complete.side_effect = error
        with patch('chat.llm.live.schema_for', return_value=('budget_adjust_feedback', {})):
            with self.assertRaises(ValueError) as caught:
                tracing.trace_turn({'text': '합성'}, 'talk', lambda: tracing.trace_llm(client, 'p', 'u', None))
        self.assertIs(caught.exception, error)
        self.root.end.assert_called_once_with(outputs={}, error='ValueError')
        self.assertIsNone(tracing._active.get())

    def test_both_flags_and_key_required(self):
        for changed in ({'BUDGET_ADJUST_LANGSMITH': 'false'}, {'LANGSMITH_TRACING': 'false'}, {'LANGSMITH_API_KEY': ''}):
            with self.subTest(changed=changed), patch.dict(os.environ, changed), patch.object(tracing, '_client') as client:
                # Call the real starter; setUp patches its public name.
                result = REAL_START('synthetic question', 'talk')
                self.assertIsNone(result)
                client.assert_not_called()

    @override_settings(CHAT_LLM='live')
    def test_consent_denial_happens_before_tracing(self):
        from budgets.ai_consent import ConsentRequired
        from .budget_adjustment import handler
        start = tracing._start
        with patch('users.privacy.require_bank_not_revoked'), patch('budgets.ai_consent.require_consent', side_effect=ConsentRequired()):
            result = handler(Mock(), Mock(), Mock(), Mock(), 'talk')({'text': '식비 늘려줘'})
        self.assertEqual(result['error'], 'budget_adjust_consent_required')
        start.assert_not_called()

    def test_parallel_turns_have_separate_contexts(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        roots = [Mock(), Mock()]
        def turn(i):
            def call():
                current = tracing._active.get()
                barrier.wait(timeout=5)
                self.assertIs(tracing._active.get(), current)
                return {'status': 'replied', 'reply': {}}
            tracing.trace_turn({'text': '합성'}, str(i), call)
            self.assertIsNone(tracing._active.get())
        with patch.object(tracing, '_start', side_effect=roots):
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(turn, range(2)))
        for root in roots:
            root.end.assert_called_once()


REAL_START = tracing._start

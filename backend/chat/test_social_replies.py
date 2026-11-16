from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from . import engine, replies


class SocialReplyTest(SimpleTestCase):
    def test_thanks_only_returns_a_farewell_without_calling_agent_or_changing_state(self):
        for said in ('고마워', '감사합니다', '땡큐', ' 고마워요! ', '고맙습니다.', 'Thank you~'):
            with self.subTest(said=said), patch.object(engine, 'reading') as reading:
                store = Mock()
                bot, route = engine.answer(None, store, text=said)
                self.assertEqual(bot['lines'], ['감사합니다! 앞으로도 궁금한 지출이나 조정이 필요한 예산이 있다면 언제든 편하게 말씀해 주세요.'])
                self.assertEqual(bot['choices'], [])
                self.assertFalse(route['fallback'])
                reading.assert_not_called()
                self.assertEqual(store.mock_calls, [])

    def test_thanks_with_a_request_is_not_swallowed(self):
        for said in ('고마워 식비도 알려줘', '감사합니다. 예산 확정해줘', '땡큐 취소할게', '안녕', None):
            with self.subTest(said=said):
                self.assertEqual(replies.thanks_reply(said), '')
        with patch.object(engine, 'reading', side_effect=RuntimeError('normal routing')):
            with self.assertRaisesMessage(RuntimeError, 'normal routing'):
                engine.answer(None, Mock(), text='고마워 식비도 알려줘')

    def test_greeting_only_returns_requested_message_without_changing_state(self):
        expected = '안녕하세요! 쿠숑이에요. 소비나 예산 관리에 고민이 있으신가요? 궁금한 점이나 조정이 필요한 부분을 말씀해 주시면 함께 살펴보겠습니다.'
        for said in ('안녕', '안녕하세요!', '하이~'):
            with self.subTest(said=said), patch.object(engine, 'reading') as reading:
                store = Mock()
                bot, _ = engine.answer(None, store, text=said)
                self.assertEqual(bot['lines'], [expected])
                reading.assert_not_called()
                self.assertEqual(store.mock_calls, [])
        self.assertEqual(replies.greeting_reply('안녕하세요 식비 알려줘'), '')

    def test_leading_thanks_is_removed_without_changing_the_request(self):
        for said, expected in [
            ('고마워, 근데 식비는 얼마썼어?', '식비는 얼마썼어?'),
            ('감사합니다! 그리고 저축은 건드리지 말고 식비 2만원 늘려줘', '저축은 건드리지 말고 식비 2만원 늘려줘'),
            ('땡큐 취소할게', '취소할게'),
            ('고마워 식비도 알려줘', '식비도 알려줘'),
            ('고마워도 예산은 못 바꾸겠어', '고마워도 예산은 못 바꾸겠어'),
            ('식비 알려줘, 고마워', '식비 알려줘, 고마워'),
        ]:
            with self.subTest(said=said):
                self.assertEqual(replies.without_thanks(said), expected)
                with patch.object(engine, 'reading'), patch.object(engine, 'run_turn', side_effect=RuntimeError('route probe')) as route:
                    with self.assertRaisesMessage(RuntimeError, 'route probe'):
                        engine.answer(None, Mock(), text=said, budget_adapter=Mock())
                self.assertEqual(route.call_args.kwargs['text'], expected)

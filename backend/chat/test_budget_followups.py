"""Next questions are optional, state-aware and cannot select or approve an adjustment."""
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from budgets.adjustment_core import followups
from budgets.models import Budget
from chat.llm.fake import FakeReading
from chat.llm.schemas import schema_for
from chat.test_agent_collab import CollabFixture, SETTINGS


class BudgetFollowupTest(SimpleTestCase):
    def setUp(self):
        self.state = {'requests': [{'category': 'FOOD', 'amount': 50000}], 'excluded': ['SAVINGS'],
                      'pending': {'kind': 'unlock', 'line': '잠금을 해제할까요?',
                                  'choices': [('keep', '잠금 유지'), ('unlock', '승인 시 잠금 해제')],
                                  'choice_descriptions': {'keep': '이 항목의 금액을 유지해요.',
                                                          'unlock': '최종 승인할 때 잠금을 풀어요.'}}}
        self.reply = {'lines': ['식비 조정 예시예요.'], 'notices': ['내역 확인이 필요해요.'],
                      'request_summary': '식비 50,000원 늘리기', 'choices': [{'value': 'keep', 'label': '잠금 유지'}],
                      'explanation': {'why': '잠긴 항목은 동의 없이 줄이지 않아요.'},
                      'budget_adjustment': {'status': 'awaiting_input'}}

    def test_model_selects_questions_from_answer_and_conditions_without_mutation(self):
        before = deepcopy((self.reply, self.state))
        complete = Mock(return_value=json.dumps({'questions': ['choices', 'reason']}))
        selected = followups.suggest(self.reply, self.state, '어떻게 할까?', complete)
        self.assertEqual([row['id'] for row in selected], ['choices', 'reason'])
        self.assertEqual((self.reply, self.state), before)
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs, {'timeout': 5})
        data = json.loads(complete.call_args.args[1])
        self.assertEqual(data['answer']['lines'], self.reply['lines'])
        self.assertEqual(data['state']['excluded'], ['SAVINGS'])
        self.assertEqual(data['phase'], 'unlock')
        self.assertFalse({'approve', 'unlock', 'cancel', 'SAVINGS'} & {row['id'] for row in data['candidates']})

    def test_invented_actions_and_malformed_output_never_become_buttons(self):
        invalid = ['not json', 'null', '[]', '{}', json.dumps({'questions': 'reason'}),
                   json.dumps({'questions': ['reason'], 'extra': 'approve'}),
                   *[json.dumps({'questions': qs}) for qs in [['approve'], ['unlock'], ['SAVINGS'],
                       ['저축에서 전부 가져와줘'], ['reason', 'reason'], ['reason', 'choices', 'request'], [None], [{}]]]]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(followups.suggest(self.reply, self.state, '', Mock(return_value=value)), [])

    def test_timeout_and_empty_selection_leave_verified_reply_unchanged(self):
        before = deepcopy(self.reply)
        for complete in (Mock(side_effect=TimeoutError()), Mock(side_effect=RuntimeError('call_limit')),
                         Mock(return_value='{"questions": []}')):
            self.assertEqual(followups.suggest(self.reply, self.state, '', complete), [])
            self.assertEqual(self.reply, before)
            complete.assert_called_once()

    def test_ended_and_failed_states_never_call_model(self):
        complete = Mock()
        for status in ('ended', 'failed', 'unsupported', 'not_mine'):
            self.reply['budget_adjustment']['status'] = status
            self.assertEqual(followups.suggest(self.reply, self.state, '', complete), [])
        self.reply['budget_adjustment']['status'] = 'replied'
        for status in followups.TERMINAL:
            self.reply['proposal'] = {'state': status}
            self.assertEqual(followups.suggest(self.reply, self.state, '', complete), [])
        complete.assert_not_called()

    def test_verified_preview_and_next_cycle_are_question_material_not_new_money(self):
        self.reply.update(proposal={'changes': [{'category': 'FOOD', 'before': 400000, 'after': 450000},
            {'category': 'SAVINGS', 'before': 500000, 'after': 470000},
            {'category': 'SPARE', 'before': 200000, 'after': 180000}],
            'shortfalls': [{'category': 'FOOD', 'requested_amount': 80000, 'applied_amount': 50000, 'missing_amount': 30000}]})
        self.reply['budget_adjustment']['planning'] = {'effective_on': '2026-12-01'}
        candidates = {row['id']: row for row in followups.candidates(self.reply, self.state)}
        self.assertIn('식비: 400,000원 → 450,000원', candidates['changes']['lines'])
        self.assertIn('2026-12-01', candidates['period']['lines'][0])
        self.assertEqual(candidates['changes_shortfall']['lines'], ['식비: 요청 80,000원 중 50,000원 조정 가능, 30,000원 부족해요.'])
        self.state['followups'] = [candidates['changes']]
        before = deepcopy(self.state)
        reply = followups.answer(self.state, candidates['changes']['question'])
        self.assertEqual(self.state, before)
        self.assertEqual(reply['reply']['choices'][0]['value'], 'keep')
        self.assertTrue(reply['reply']['detail'])
        self.assertIsNone(followups.answer(self.state, '잠금 풀고 승인해줘'))

    def test_opening_offers_real_input_examples_instead_of_meta_questions(self):
        state = {'pending': {'kind': 'request'}, 'requests': []}
        rows = followups.candidates(self.reply, state)
        self.assertEqual([row['question'] for row in rows],
                         ['식비 예산을 늘리고 싶어', '쇼핑 예산을 줄이고 싶어', '저축을 늘리고 싶어'])
        for row in rows:
            self.assertEqual(row['kind'], 'input')
            self.assertIsNone(followups.answer({**state, 'followups': [row]}, row['question']))

    def test_clear_input_steps_do_not_get_generic_explanation_suggestions(self):
        for kind in ('amount', 'direction', 'category', 'scope', 'clarification'):
            state = {'pending': {'kind': kind}, 'requests': self.state['requests']}
            self.assertEqual(followups.candidates(self.reply, state), [])

    def test_comparison_offers_verified_recommendation_reasons(self):
        self.reply.update(comparison={'recommendation': {'plan_id': 'living', 'reasons': ['저축을 줄이지 않아요.']},
                                     'plans': [{'id': 'living', 'result': {}}]})
        rows = followups.candidates(self.reply, {'pending': {'kind': 'comparison'}, 'requests': self.state['requests']})
        self.assertEqual(rows, [{'id': 'recommendation', 'question': '어떤 안이 더 나은지 설명해줘',
                                 'lines': ['저축을 줄이지 않아요.']}])

    def test_simple_preview_does_not_repeat_already_visible_amounts(self):
        self.reply.update(proposal={'changes': [{'category': 'FOOD', 'before': 400000, 'after': 450000},
                                               {'category': 'SPARE', 'before': 200000, 'after': 150000}]})
        self.assertEqual(followups.candidates(self.reply, {'requests': self.state['requests']}), [])

    def test_live_schema_and_fake_adapter_use_same_role(self):
        self.assertEqual(schema_for(followups.PROMPT), ('budget_followups', followups.SCHEMA))
        fake = FakeReading()
        self.assertTrue(followups.suggest(self.reply, self.state, '', fake))
        self.assertEqual(fake.calls[-1]['role'], 'fake_budget_followups')


@override_settings(**SETTINGS)
class BudgetFollowupApiTest(CollabFixture, TestCase):
    def test_opening_examples_enter_normal_adjustment_without_applying_budget(self):
        for index, category, direction in ((0, 'FOOD', 'up'), (1, 'FASHION_SHOPPING', 'down')):
            self.cid = None
            _, opening = self.say(choice='budget')
            self.assertEqual(opening['examples'], ['식비 예산을 늘리고 싶어', '쇼핑 예산을 줄이고 싶어'])
            self.assertNotIn('확인하는 이유', str(opening['examples']))
            data, reply = self.say(opening['examples'][index])
            self.assertEqual(data['agent'], 'budget')
            self.assertIn('budgetAdjustment', reply)
            item = self.budget_state()['requests'][0]
            self.assertEqual((item['category'], item['direction']), (category, direction))
            self.base.refresh_from_db()
            self.assertEqual(self.base.status, 'approved')

    def test_expired_preview_does_not_replay_old_suggestion(self):
        self.ctx['minimums'].update({code: amount - 20000 for code, amount in self.ctx['allocations'].items() if code != 'FOOD'})
        self.say(choice='budget')
        _, reply = self.say('식비를 5만 원 늘려줘')
        _, preview = self.pick(reply, 'plan_living')
        Budget.objects.filter(pk=preview['budgetAdjustment']['proposal']['proposalId']).update(status='expired')
        _, ended = self.say(preview['examples'][0])
        self.assertNotIn('examples', ended)
        self.assertFalse(ended.get('budgetAdjustment', {}).get('canApprove'))

    def test_amount_input_does_not_overwrite_followup_answer(self):
        from budgets.adjustment_core.agent import _ask
        from chat.budget_adjustment import handler
        from chat.router_core import MemoryStateStore
        from budgets.test_adjustment_core import context
        store = MemoryStateStore()
        state = {'requests': [{'category': 'FOOD', 'direction': 'up', 'amount': None}]}
        _ask(store, 'test', state, 'amount', '얼마 늘릴까요?', explanation={
            'why': '추가로 필요한 지출을 기준으로 금액을 정해 주세요.'})
        state = store.get_agent_state('test', 'budget')
        state['followups'] = [{'id': 'reason', 'question': '금액은 어떻게 정할까?',
                              'lines': ['추가로 필요한 지출을 기준으로 금액을 정해 주세요.']}]
        store.save_agent_state('test', 'budget', state)
        adapter = Mock(reference_context=None, get_context=Mock(return_value=context()))
        complete = Mock(side_effect=AssertionError('추천 질문은 읽기 전용으로 처리한다'))
        result = handler(self.user, store, adapter, complete, 'test')({'mode': 'continue', 'text': '금액은 어떻게 정할까?'})
        self.assertEqual(result['reply']['lines'], ['추가로 필요한 지출을 기준으로 금액을 정해 주세요.'])
        self.assertEqual(result['reply']['budget_adjustment']['amount_input']['category'], 'FOOD')
        complete.assert_not_called()

    def test_question_click_preserves_pending_then_existing_selection_and_approval(self):
        self.ctx['minimums'].update({code: amount - 20000 for code, amount in self.ctx['allocations'].items() if code != 'FOOD'})
        self.say(choice='budget')
        _, reply = self.say('식비를 5만 원 늘려줘')
        self.assertTrue(reply['examples'])
        original = self.budget_state()
        count = Budget.objects.count()
        _, explained = self.say(reply['examples'][0])
        current = self.budget_state()
        for key in ('requests', 'sources', 'excluded', 'pending', 'planning', 'unlock_categories', 'target_changes'):
            self.assertEqual(current.get(key), original.get(key), key)
        self.assertEqual(Budget.objects.count(), count)
        self.assertEqual(explained['budgetAdjustment']['comparison'], reply['budgetAdjustment']['comparison'])
        self.assertFalse(self.llm.calls)
        _, preview = self.pick(explained, 'plan_living')
        self.assertTrue(preview['budgetAdjustment']['canApprove'])
        proposal = preview['budgetAdjustment']['proposal']
        question = preview['examples'][0]
        _, detail = self.say(question)
        self.assertEqual(detail['budgetAdjustment']['proposal'], proposal)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        approved = self.client.post(f"/api/budgets/proposals/{proposal['proposalId']}/approve/",
                                   {'keepForNextCycle': False}, format='json', HTTP_IDEMPOTENCY_KEY='followup-approval')
        self.assertEqual(approved.status_code, 200, approved.content)

    def test_optional_generation_failure_keeps_normal_budget_response(self):
        self.say(choice='budget')
        with patch.object(followups, 'suggest', return_value=[]):
            _, baseline = self.say('식비를 1만 5천 원 늘려줘')
        self.assertNotIn('examples', baseline)
        self.assertTrue(baseline['budgetAdjustment']['comparison']['plans'])
        self.assertTrue(baseline['choices'])

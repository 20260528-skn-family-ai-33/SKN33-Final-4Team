"""Conversation transitions, independent of a particular user phrase or LLM provider."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import Mock, patch

from .adjustment_core import financial_conversation as dialogue, interpretation
from .adjustment_core.explanations import result_explanation, status_explanation, step_explanation
from .adjustment_core.funding import simulate_funding
from .adjustment_core.conversation import _merge
from .adjustment_core.responses import guidance
from .test_adjustment_core import Store, allocation, context, envelope, item, reading


class AdjustmentDialogueTest(unittest.TestCase):
    def test_new_topic_preserves_new_amount_and_asks_about_future_timing(self):
        self.turn(reading(requests=[item('SAVINGS', 300000)]))
        self.store.state.update(financial_offer={'old': True}, financial_overrides={'remaining_days': 1},
                                spare_minimum=20000, excluded=['FOOD'])
        result = self.turn(reading(requests=[item('EDUCATION', 500000)], reference='new',
                                  unclear=['다음 달부터 교육비가 50만 원 더 필요하시군요. 이번 예산도 미리 조정할까요?']))
        self.assertEqual(self.store.state['requests'], [item('EDUCATION', 500000)])
        self.assertEqual(self.store.state['pending']['kind'], 'clarification')
        self.assertIn('다음 달부터 교육비', result['reply']['lines'][0])
        self.assertFalse(self.store.state.get('financial_offer'))
        self.assertFalse(self.store.state.get('financial_overrides'))
        self.assertFalse(self.store.state.get('excluded'))
        self.assertNotIn('spare_minimum', self.store.state)
        self.assertFalse(self.proposals)

    def test_savings_help_explains_real_transfer_and_shortfall_without_using_itself(self):
        self.context['bucket_available']['CUSHION_MONTHLY'] = 185000
        self.context['locks'] = dict(self.context['allocations'])
        for action in ('inspect', 'explain', 'help_decide'):
            with self.subTest(action=action):
                self.store.state = {}
                self.turn(reading(requests=[item('SAVINGS', 300000)]))
                result = self.turn(reading(action=action))
                lines = ' '.join(result['reply']['lines'])
                self.assertIn('이번 달 적립할 예산을 줄여 저축에 최대 185,000원', lines)
                self.assertIn('115,000원이 부족', lines)
                self.assertIn('남길 여윳돈 정하기', lines)
                self.assertNotIn('저축 ·', lines)
                self.assertNotIn('SAVINGS', [c['value'] for c in result['reply']['choices']])
                self.assertFalse(self.proposals)

    def test_combined_card_can_be_selected_and_keeps_its_protected_spare(self):
        self.turn(reading(requests=[item('SAVINGS', 300000)]))
        self.store.state['spare_minimum'] = 200000
        result = self.turn(reading(action='compare'))
        self.assertIn('plan_combined', [c['value'] for c in result['reply']['choices']])
        result = self.turn(reading(selected_choice='plan_combined'))
        self.assertIn('proposal', result['reply'], result)
        proposal = result['reply']['proposal']
        self.assertEqual(proposal['buckets']['SAVINGS'], 900000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 200000)

    def test_decision_help_cannot_become_an_approval_or_unlock(self):
        parsed = interpretation.parse(reading(action='help_decide', selected_choice='unlock', approval=True, unlock=True))
        self.assertIsNone(parsed['selected_choice'])
        self.assertIsNone(parsed['approval'])
        self.assertIsNone(parsed['unlock'])

    def test_uncertainty_uses_interpreted_context_instead_of_a_phrase_shortcut(self):
        for action in ['help_decide', 'explain', None]:
            with self.subTest(action=action):
                self.setUp()
                self.turn(reading(requests=[item('LIVING', 50000)]))
                self.complete.reset_mock()
                self.complete.side_effect = lambda system, payload: json.dumps(reading(action=action))
                result = dialogue.handle({'conversation_id': 'talk', 'text': '모르겠어'}, **self.ports)
                self.assertEqual(self.complete.call_count, 1)
                self.assertNotIn('comparison', result['reply'])
                self.assertFalse(self.store.state.get('sources'))
                self.assertFalse(self.proposals)
                if action == 'help_decide':
                    self.assertIn('유지할 예산은 그대로', ' '.join(result['reply']['lines']))
                elif action == 'explain':
                    self.assertIn('이번에 저축할 예산', ' '.join(result['reply']['lines']))

    def test_decision_help_preserves_choice_stage_and_never_grants_consent(self):
        for kind, field, expected in [('destinations', None, '평소 돈이 모자라는'),
                                     ('scope', None, '이번 달에만'), ('unlock', None, '잠금 유지'),
                                     ('financial_data', 'spare_minimum', '병원비'),
                                     ('financial_data', 'planned', '확실히 없을 때만'),
                                     ('one_off_review', None, '기억나지 않으면'),
                                     ('direction', None, '덜 쓸 계획이면'),
                                     ('amount_mode', None, '지금 예산에')]:
            with self.subTest(kind=kind, field=field):
                self.setUp()
                self.question(kind, field)
                before = deepcopy(self.store.state)
                response = self.turn(reading(action='help_decide'))
                self.assertIn(expected, ' '.join(response['reply']['lines']), response)
                self.assertEqual(self.store.state['pending'], before['pending'])
                self.assertEqual(self.store.state['requests'], before['requests'])
                self.assertFalse(self.proposals)
                self.assertFalse(self.store.state.get('unlock_categories'))

    def test_amount_decision_help_shows_calculated_examples_and_then_recommendation(self):
        from .adjustment_core.financial_planner import PROMPT
        from .test_financial_planner import draft, review
        self.context['spending_status'] = 'complete'
        self.question('amount')
        self.store.state['requests'] = [item(amount=None)]
        self.store.state['sources'] = []
        def complete(system, payload):
            if system != PROMPT:
                return json.dumps(reading(action='help_decide'))
            payload = json.loads(payload)
            return json.dumps(draft(payload['balanced_funding']) if payload['phase'] == 'draft' else review())
        self.complete.side_effect = complete
        response = dialogue.handle({'conversation_id': 'talk', 'text': '얼마가 좋을지 모르겠어'}, **self.ports)
        comparison = response['reply']['comparison']
        self.assertGreater(comparison['basis_amount'], 0)
        self.assertTrue(any(p.get('result', {}).get('changes') for p in comparison['plans'] if p.get('result')))
        response = self.turn(reading(action='help_decide'))
        self.assertIn('recommendation_advice', response['reply'])
        self.assertFalse(self.proposals)

    def test_unknown_funding_choice_asks_destination_then_compares_without_losing_goal(self):
        self.turn(reading(requests=[item('LIVING', 1000000)]))
        self.turn(reading(action='inspect'))
        response = self.turn(reading(action='compare', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'destinations', response)
        self.assertNotIn('돈을 찾지 못했어요', str(response))
        self.assertNotIn('추천에서 제외', str(response))
        response = self.turn(reading(selected_choice='FOOD', reference='previous'))
        self.assertIn('comparison', response['reply'], response)
        self.assertEqual(self.store.state['requests'], [item('LIVING', 1000000)])
        self.assertEqual(self.store.state['destinations'], ['FOOD'])
        comparison = response['reply']['comparison']
        self.assertTrue(any(p['amount'] > 0 for p in comparison['plans']))
        self.assertTrue(next(p for p in comparison['plans'] if p['id'] == 'spare')['needs_spare_minimum'])
        self.assertFalse(self.proposals)

    def test_source_question_explains_checked_options_without_selecting_or_repeating(self):
        for category in ['LIVING', 'FOOD']:
            with self.subTest(category=category):
                self.setUp()
                self.turn(reading(requests=[item(category, 50000)]))
                before = deepcopy(self.store.state['requests'])
                response = self.turn(reading(action='inspect', reference='previous'))
                lines = ' '.join(response['reply']['lines'])
                self.assertIn('저축 · 이번에 저축할 예산을 줄여', lines)
                self.assertIn('150,000원', lines)
                self.assertIn('최대 100,000원을 더해요', lines)
                self.assertIn('여윳돈 · 먼저 남길 금액을 정하면', lines)
                self.assertEqual(guidance(response, self.store.state)['message'].splitlines(), response['reply']['lines'])
                self.assertNotIn('600,000원', lines)
                self.assertNotIn('어디에서 가져올까요', lines)
                self.assertEqual(self.store.state['requests'], before)
                self.assertFalse(self.store.state.get('sources'))
                self.assertFalse(self.proposals)
                selected = self.turn(reading(selected_choice='SAVINGS', reference='previous'))
                self.assertEqual(self.store.state['sources'], ['SAVINGS'], selected)

    def test_source_question_explains_unknown_excluded_and_empty_sources(self):
        self.turn(reading(requests=[item('LIVING', 50000)]))
        self.store.state['excluded'] = ['SAVINGS']
        self.context['bucket_available']['CUSHION_MONTHLY'] = 0
        self.context['bucket_status']['SPARE'] = 'unknown'
        response = self.turn(reading(action='inspect'))
        lines = ' '.join(response['reply']['lines'])
        for text in ['유지하기로', '가져올 수 있는 금액 0원', '조정 가능한 예산 확인 필요']:
            self.assertIn(text, lines)
        self.assertFalse(response['reply']['choices'])

    def test_source_summary_lists_verified_money_first_and_keeps_unknown_amounts_unknown(self):
        self.context['bucket_status']['SAVINGS'] = 'unknown'
        self.context['bucket_available']['CUSHION_MONTHLY'] = 185000
        self.turn(reading(requests=[item('LIVING', 1000000)]))
        response = self.turn(reading(action='inspect'))
        lines = response['reply']['lines']
        self.assertIn('최대 185,000원', lines[0])
        self.assertIn('저축 · 조정 가능한 예산 확인 필요', lines)
        self.assertIn('815,000원이 부족', ' '.join(lines))
        self.assertNotIn('저축 · 이번에 저축할 예산을 줄여', ' '.join(lines))
        self.assertEqual([choice['value'] for choice in response['reply']['choices']],
                         ['CUSHION_MONTHLY', 'keep_spare'])

    def test_source_question_preserves_spare_minimum_and_asks_before_using_spare(self):
        self.turn(reading(requests=[item('LIVING', 50000)]))
        self.turn(reading(action='inspect'))
        response = self.turn(reading(selected_choice='keep_spare', reference='previous'))
        self.assertEqual(self.store.state['pending']['field'], 'spare_minimum', response)
        self.complete.side_effect = lambda system, payload: json.dumps({'value': 350000})
        response = dialogue.handle({'conversation_id': 'talk', 'text': '35만원은 남길래'}, **self.ports)
        self.assertIn('최대 50,000원', ' '.join(response['reply']['lines']))
        self.assertEqual(self.store.state['spare_minimum'], 350000)
        self.assertEqual(self.store.state['pending']['kind'], 'sources')
        self.assertFalse(self.store.state.get('sources'))
        selected = self.turn(reading(selected_choice='SPARE', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'destinations', selected)

    def test_impossible_increases_ask_amount_before_funding_or_comparison(self):
        for category in ['LIVING', 'FOOD', 'SAVINGS']:
            for mode, amount in [('by', 10000000), ('to', 10000000), ('percent_by', 10000), ('remaining', 10000000)]:
                for action in [None, 'compare']:
                    with self.subTest(category=category, mode=mode, action=action):
                        self.store.state = {}
                        self.complete.reset_mock()
                        result = self.turn(reading(requests=[item(category, amount, 'up', mode)], action=action))
                        self.assertEqual(self.store.state['pending']['kind'], 'amount', result)
                        self.assertIn('다른 예산의 총액', result['reply']['explanation']['brief'])
                        self.assertFalse(result['reply']['choices'])
                        self.assertNotIn('comparison', result['reply'])
                        self.assertFalse(self.proposals)
                        self.assertEqual(self.complete.call_count, 1)
                        self.assertIsNone(self.store.state['requests'][0]['amount'])

    def setUp(self):
        self.store, self.context = Store(), context()
        self.proposals = []
        self.complete = Mock()
        self.ports = dict(store=self.store, complete=self.complete, get_context=lambda: deepcopy(self.context),
            simulate=lambda ctx, request: simulate_funding(ctx, request, allocate_group_reduction=allocation),
            propose=self.propose, task_status=Mock(), cancel=Mock(), allocator=allocation)

    def propose(self, ctx, result, **settings):
        card = {**result, **settings, 'proposal_id': 'preview', 'task_ref': 'task'}
        self.proposals.append(card)
        return card

    def question(self, kind, field=None):
        self.store.state = {'requests': [item(amount=50000)], 'sources': ['SPARE'],
                            'history': [{'requests': [item(amount=20000)], 'sources': ['SPARE']}]}
        if kind == 'one_off_review':
            self.store.state['review_candidates'] = [{'id': 'synthetic', 'category': 'FOOD', 'amount': 30000}]
        return dialogue._ask(self.store, 'talk', self.store.state, kind, '현재 질문',
            [('manual_living', '생활비 직접 조정'), ('cancel', '취소')], field=field)

    def turn(self, parsed):
        def complete(system, payload):
            if system == dialogue.ANSWER_PROMPT:
                return json.dumps({'value': None})
            if system == dialogue.ONE_OFF_PROMPT:
                return json.dumps({'decisions': [{'id': 'synthetic', 'decision': 'unknown'}]})
            if system in {interpretation.PROMPT, dialogue.DIALOGUE_PROMPT}:
                return json.dumps(parsed, ensure_ascii=False)
            raise AssertionError('Unexpected extra model call')
        self.complete.side_effect = complete
        return dialogue.handle({'conversation_id': 'talk', 'text': '다른 표현으로 보낸 후속 요청'}, **self.ports)

    def test_question_does_not_swallow_global_intents_or_reinterpret_twice(self):
        for kind, field in [('financial_data', 'planned'), ('financial_data', 'period'),
                            ('financial_data', 'amount'), ('one_off_review', None)]:
            for action in ['explain', 'back', 'cancel', 'modify', 'handoff']:
                with self.subTest(kind=kind, field=field, action=action):
                    first = self.question(kind, field)
                    self.proposals.clear()
                    self.complete.reset_mock()
                    before = deepcopy(self.store.state)
                    parsed = {
                        'explain': reading(action='explain'), 'back': reading(action='back'),
                        'cancel': reading(cancel=True),
                        'modify': reading(requests=[item(amount=30000)], sources=['SPARE'], reference='previous'),
                        'handoff': reading(mine=False, handoff='spending', handoff_category='FOOD'),
                    }[action]
                    response = self.turn(parsed)
                    self.assertEqual(self.complete.call_count, 2)
                    if action == 'explain':
                        self.assertTrue(response['reply']['detail'])
                        self.assertEqual(response['reply']['explanation'], first['reply']['explanation'])
                        self.assertEqual(self.store.state['requests'], before['requests'])
                        self.assertEqual(self.store.state['pending'], before['pending'])
                        self.assertFalse(self.proposals)
                    elif action == 'handoff':
                        self.assertEqual(response['status'], 'not_mine')
                        self.assertEqual(self.store.state, before)
                    elif action == 'cancel':
                        self.assertEqual(response['status'], 'ended')
                        self.assertIsNone(self.store.state)
                    else:
                        self.assertNotEqual((self.store.state.get('pending') or {}).get('kind'), kind)
                        self.assertEqual(self.proposals[-1]['allocations']['FOOD'],
                                         self.context['allocations']['FOOD'] + (30000 if action == 'modify' else 20000))
                    self.ports['cancel'].assert_not_called()

    def test_question_context_has_field_and_confirmed_conditions(self):
        self.question('financial_data', 'planned')
        self.turn(reading(action='explain'))
        payloads = [json.loads(call.args[1]) for call in self.complete.call_args_list]
        for payload in payloads:
            self.assertEqual(payload['pending']['field'], 'planned')
            self.assertEqual(payload['previous']['requests'], [item(amount=50000)])
            self.assertEqual(payload['previous']['sources'], ['SPARE'])

    def test_living_shortage_asks_where_before_asking_about_reduced_money(self):
        for action in [None, 'compare']:
            with self.subTest(action=action):
                self.store.state = None
                response = self.turn(reading(requests=[item('LIVING', None, 'up', None)], action=action))
                self.assertEqual(self.store.state['pending']['kind'], 'category')
                self.assertIn('어느 지출에서 돈이 부족', response['reply']['lines'][0])
                self.assertNotIn('줄인', json.dumps(response, ensure_ascii=False))
                self.assertFalse(self.proposals)

    def test_short_answer_selects_manual_adjustment_without_repeating_the_question(self):
        for kind in ['financial_data', 'financial_selection']:
            with self.subTest(kind=kind):
                self.question(kind, 'planned')
                self.store.state['requests'] = [item('LIVING', None, 'up', None)]
                if kind == 'financial_selection':
                    self.store.state['financial_offer'] = {'answer': {'plans': [], 'recommended_index': None}, 'comparison': {'plans': []}}
                response = self.turn(reading(selected_choice='manual_living', reference='previous'))
                self.assertEqual(self.store.state['pending']['kind'], 'category')
                self.assertIn('부족하다고', response['reply']['lines'][0])
                self.assertFalse(self.proposals)

    def test_total_living_followup_keeps_increase_and_asks_amount_then_source(self):
        self.turn(reading(requests=[item('LIVING', None, 'up', None)]))
        self.turn(reading(selected_choice='LIVING', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        response = self.turn(reading(requests=[item('LIVING', 50000, 'up')], reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'sources')
        self.assertNotIn('줄인', json.dumps(response, ensure_ascii=False))
        response = self.turn(reading(selected_choice='SPARE', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'destinations')
        self.assertIn('생활비에 더할 돈', response['reply']['lines'][0])
        self.assertNotIn('줄인 돈', json.dumps(response, ensure_ascii=False))
        self.assertFalse(self.proposals)

    def test_category_answer_replaces_unspecified_living_target_and_keeps_direction(self):
        self.turn(reading(requests=[item('LIVING', None, 'up', None)]))
        with patch.object(dialogue, 'comparison_reply', return_value={'status': 'awaiting_input', 'reply': {}}) as compare:
            self.turn(reading(selected_choice='FOOD', reference='previous'))
        self.assertEqual(compare.call_args.args[2]['requests'], [item('FOOD', None, 'up', None)])
        self.assertFalse(self.proposals)

    def test_old_destination_flow_resolves_the_amount_question_to_selected_category(self):
        self.store.state = {'requests': [item('LIVING', None, 'up', None)],
            'pending': {'kind': 'destinations', 'index': 0, 'line': '어느 생활비 항목에 배분할까요?',
                        'choices': [('TRANSPORT_CAR', '교통·차량')]}}
        response = self.turn(reading(selected_choice='TRANSPORT_CAR', reference='previous'))
        self.assertEqual(self.store.state['requests'], [item('TRANSPORT_CAR', None, 'up', None)])
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertEqual(response['reply']['explanation'], {})
        response = self.turn(reading(requests=[item('LIVING', 50000, 'up')], reference='previous'))
        self.assertEqual(self.store.state['requests'], [item('TRANSPORT_CAR', 50000, 'up')])
        self.assertNotEqual(self.store.state['pending']['kind'], 'amount')
        self.assertFalse(self.proposals)

    def test_existing_duplicate_living_placeholder_is_removed_without_losing_amount_or_source(self):
        state = {'requests': [item('LIVING', None), item('TRANSPORT_CAR', 50000)],
                 'destinations': ['TRANSPORT_CAR'], 'sources': ['SPARE'], 'excluded': ['SAVINGS']}
        _merge(state, reading(reference='previous'), {'kind': 'amount', 'index': 0})
        self.assertEqual(state['requests'], [item('TRANSPORT_CAR', 50000)])
        self.assertEqual(state['sources'], ['SPARE'])
        self.assertEqual(state['excluded'], ['SAVINGS'])
        self.assertEqual(state['destinations'], [])
        explicit = {'requests': [item('LIVING', 50000)], 'destinations': ['TRANSPORT_CAR']}
        _merge(explicit, reading(reference='previous'), {})
        self.assertEqual(explicit['requests'], [item('LIVING', 50000)])

    def test_buttons_and_natural_choices_have_the_same_transition_across_question_types(self):
        cases = [
            ('category', 'TRANSPORT_CAR', item(None, 50000), ['SPARE']),
            ('direction', 'up', item('FOOD', 50000, None), ['SPARE']),
            ('amount_mode', 'by', item('FOOD', 50000, 'up', None), ['SPARE']),
            ('sources', 'SPARE', item('FOOD', 50000), []),
            ('destinations', 'TRANSPORT_CAR', item('FOOD', 50000, 'down'), []),
            ('funding_scope', 'spare', item('FOOD', 50000), []),
            ('scope', 'ongoing', item('FOOD', 50000), ['SPARE']),
        ]
        for kind, value, request, sources in cases:
            outcomes = []
            for natural in [False, True]:
                with self.subTest(kind=kind, natural=natural):
                    self.store.state = {'requests': [deepcopy(request)], 'sources': sources,
                        'pending': {'kind': kind, 'index': 0, 'field': 'sources', 'line': '질문', 'choices': [(value, '선택지')]}}
                    self.proposals.clear()
                    if natural:
                        result = self.turn(reading(selected_choice=value, reference='previous'))
                    else:
                        self.complete.side_effect = AssertionError('A button must not call the interpreter')
                        result = dialogue.handle({'conversation_id': 'talk', 'text': value}, **self.ports)
                    outcomes.append((result['status'], result['reply'].get('proposal'), self.store.state.get('requests')))
            self.assertEqual(outcomes[0], outcomes[1], kind)

    def test_replacement_and_addition_preserve_different_requests(self):
        for operation, expected in [('merge', ['FOOD', 'TRANSPORT_CAR']), ('replace', ['TRANSPORT_CAR'])]:
            with self.subTest(operation=operation):
                state = {'requests': [item('FOOD', 50000)], 'sources': ['SPARE'], 'excluded': ['SAVINGS'],
                         'unlock_categories': ['FOOD'], 'unlock_consents': {'FOOD': {'amount': 400000}}}
                _merge(state, reading(requests=[item('TRANSPORT_CAR', 30000)], request_update=operation,
                                      reference='previous'), {'kind': 'amount', 'index': 0})
                self.assertEqual([row['category'] for row in state['requests']], expected)
                self.assertEqual(state['sources'], ['SPARE'])
                self.assertEqual(state['excluded'], ['SAVINGS'])
                if operation == 'replace':
                    self.assertEqual(state['unlock_consents'], {})

    def test_partial_withdrawal_keeps_consent_for_retained_requests(self):
        consent = {'FOOD': {'amount': 400000}}
        state = {'requests': [item('FOOD', 10000), item('TRANSPORT_CAR', 20000)],
                 'unlock_categories': ['FOOD'], 'unlock_consents': deepcopy(consent)}
        _merge(state, reading(requests=[item('FOOD', 10000)], request_update='replace'), {})
        self.assertEqual(state['unlock_consents'], consent)
        self.assertEqual(state['unlock_categories'], ['FOOD'])


    def test_complete_transfer_does_not_ask_for_the_already_named_recipient(self):
        for action in [None, 'distribute']:
            with self.subTest(action=action):
                self.store.state = {}
                result = self.turn(reading(requests=[item('FASHION_SHOPPING', 30000, 'down'),
                    item('EDUCATION', 20000, 'down')], destinations=['TRANSPORT_CAR'], action=action))
                proposal = result['reply']['proposal']
                self.assertEqual(proposal['allocations']['TRANSPORT_CAR'], 150000)
                self.assertEqual(proposal['allocations']['FASHION_SHOPPING'], 170000)
                self.assertEqual(proposal['allocations']['EDUCATION'], 30000)

    def test_old_spare_card_requires_a_minimum_before_reuse(self):
        for category, automatic in [('HOUSEHOLD', True), ('SAVINGS', False)]:
            with self.subTest(category=category):
                request = envelope({**item(category, 95000), 'automatic': automatic}, sources=['SPARE'])
                self.store.state = {'requests': request['items'], 'comparison': {'context': deepcopy(self.context),
                    'comparison': {'plans': [{'id': 'spare', 'status': 'ready', 'envelope': request}]}},
                    'pending': {'kind': 'comparison', 'choices': [('plan_spare', '여윳돈 활용')]}}
                result = dialogue.handle({'conversation_id': 'talk', 'text': 'plan_spare'}, **self.ports)
                self.assertEqual(self.store.state['pending']['field'], 'spare_minimum', result)
                self.assertFalse(self.proposals)
                self.complete.assert_not_called()

    def test_selection_does_not_trap_unsupported_or_ambiguous_requests(self):
        for parsed, expected in [(reading(action='fixed_expense'), '고정지출'),
                                 (reading(unclear=['현재 금액을 바꿀까요, 다음 달 목표를 바꿀까요?']), '다음 달 목표'),
                                 (reading(action='undo'), '이미 확정한 변경')]:
            with self.subTest(parsed=parsed):
                self.question('financial_selection')
                self.store.state['financial_offer'] = {'answer': {'plans': [], 'recommended_index': None},
                                                      'comparison': {'plans': []}}
                response = self.turn(parsed)
                self.assertIn(expected, ' '.join(response['reply']['lines']))
                self.assertNotEqual(self.store.state['pending']['kind'], 'financial_selection')
                self.assertFalse(self.proposals)

    def test_recommend_and_compare_both_open_examples_before_a_plan_exists(self):
        for action in ['recommend', 'compare']:
            with self.subTest(action=action):
                self.question('funding_scope')
                with patch.object(dialogue, 'comparison_reply', return_value={'status': 'awaiting_input', 'reply': {}}) as compare:
                    self.turn(reading(action=action))
                compare.assert_called_once()
                self.assertFalse(self.proposals)

    def test_unknown_data_offers_manual_path_without_inventing_zero(self):
        for field in ['planned', 'period', 'amount']:
            with self.subTest(field=field):
                self.question('financial_data', field)
                response = self.turn(reading(action='compare'))
                self.assertIn('생활비 직접 조정', ' '.join(response['reply']['lines']))
                self.assertNotIn('financial_overrides', self.store.state)
                self.assertFalse(self.proposals)
                self.assertIn('manual_living', [row['value'] for row in response['reply']['choices']])

    def test_numeric_answer_uses_only_one_interpretation_and_keeps_zero(self):
        self.question('financial_data', 'planned')
        self.complete.return_value = json.dumps({'value': 0})
        with patch.object(dialogue, 'comparison_reply', return_value={'status': 'replied', 'reply': {}}) as compare:
            dialogue.handle({'conversation_id': 'talk', 'text': '없어요'}, **self.ports)
        self.complete.assert_called_once()
        state = compare.call_args.args[2]
        self.assertEqual(state['financial_overrides']['planned_spare']['amount'], 0)

    def test_all_financial_questions_store_the_explanation_they_display(self):
        for kind in ['financial_data', 'financial_selection', 'financial_risk', 'financial_partial', 'one_off_review']:
            with self.subTest(kind=kind):
                response = self.question(kind, 'planned')
                self.assertEqual(response['reply']['explanation'], self.store.state['pending']['explanation'])
                self.assertEqual({row['value']: row['description'] for row in response['reply']['choices']},
                                 self.store.state['pending']['choice_descriptions'])

    def test_interpretation_failure_is_not_reported_as_an_unanswered_question(self):
        self.question('financial_data', 'planned')
        before = deepcopy(self.store.state)
        self.complete.side_effect = interpretation.InterpretationFailed('call_limit')
        response = dialogue.handle({'conversation_id': 'talk', 'text': '질문'}, **self.ports)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error'], 'call_limit')
        self.assertIn('횟수', response['reply']['explanation']['why'])
        self.assertEqual(self.store.state, before)

    def test_no_recommendation_only_offers_reserve_change_when_it_can_help(self):
        for allowed, limit, expected in [(['SPARE'], 0, False), (['SPARE'], 400000, True), ([], 0, False)]:
            with self.subTest(allowed=allowed, limit=limit):
                state = {'requests': [item(amount=50000)], 'sources': ['SPARE']}
                self.context['bucket_available']['SPARE'] = limit
                answer = {'plans': [], 'diagnosis': {'status': 'ready', 'target': 'FOOD',
                    'allowed': allowed, 'excluded': [], 'capacities': {'SPARE': 0} if allowed else {}}}
                with patch.object(dialogue, 'recommend', return_value=answer):
                    response = dialogue.comparison_reply(self.store, 'talk', state, self.context, self.context, self.complete)
                self.assertEqual('reserve_override' in [row['value'] for row in response['reply']['choices']], expected)
                self.assertIn('직접 정한 금액', response['reply']['explanation']['effect'])
                self.assertIn('추천안이 없어', response['reply']['lines'][0])

    def test_calculator_failure_does_not_ask_user_for_another_category(self):
        state = {'requests': [item(amount=50000)], 'sources': ['SPARE']}
        with patch.object(dialogue, 'recommend', side_effect=ValueError('invalid_result')):
            response = dialogue.comparison_reply(self.store, 'talk', state, self.context, self.context, self.complete)
        self.assertEqual(response['status'], 'awaiting_input')
        self.assertEqual(response['error'], 'planner_unavailable')
        self.assertIn('계산하는 중', response['reply']['explanation']['why'])
        self.assertEqual(self.store.state['requests'], state['requests'])

    def test_preview_edits_explain_only_the_constraint_that_actually_failed(self):
        for target, locked, expected in [(0, False, '10,000원 아래'), (250000, False, '조정 전 예산 200,000원'),
                                         (175001, False, '5,000원 단위'), (200000, False, '현재 미리보기 금액'),
                                         (180000, True, '잠금을 풀기 전')]:
            with self.subTest(target=target, locked=locked):
                self.context = context()
                original = simulate_funding(self.context, envelope(item(amount=50000), sources=['SPARE']),
                                            allocate_group_reduction=allocation)
                old = {**original, 'task_ref': 'task', 'proposal_id': 'preview'}
                state = {'requests': [item(amount=50000)], 'sources': ['SPARE'], 'proposal': old, 'work_ref': 'task'}
                if locked:
                    self.context['locks']['FASHION_SHOPPING'] = 200000
                self.ports['task_status'].return_value = {'state': 'awaiting_approval'}
                self.complete.return_value = json.dumps({'action': 'refine_more', 'category': 'FASHION_SHOPPING',
                    'destination': None, 'amount': target, 'amount_mode': 'to', 'suggested_target': None})
                response = dialogue.feedback({'conversation_id': 'talk', 'text': f'쇼핑을 {target}원으로 줄여줘'}, state, self.ports)
                self.assertIn(expected, response['reply']['lines'][0])
                self.assertNotIn('잠금·하한·우선순위·금액 단위', response['reply']['lines'][0])
                self.assertEqual(response['reply']['proposal'], old)
                self.assertFalse(self.proposals)

    def test_every_calculation_blocker_has_a_specific_reason_and_next_step(self):
        codes = ['bucket_unverified', 'bucket_disabled', 'unit_alignment_required', 'spending_unverified',
            'ml_reference_unavailable', 'destination_required', 'lock_mismatch', 'lock_below_minimum',
            'excluded_requested_category', 'locked_category', 'excluded_destination',
            'living_decrease_requires_upper_destination', 'source_required', 'living_increase_requires_upper_source',
            'allocation_policy_required', 'protected_amount_not_reached', 'insufficient_funds', 'maximum_not_verifiable']
        for code in codes:
            with self.subTest(code=code):
                explanation = result_explanation(self.context, {'conflicts': [{'code': code, 'category': 'FOOD'}]})
                self.assertNotIn('현재 정보로는', explanation['why'])
                self.assertNotEqual(explanation['next'], '현재 예산과 변경 조건을 다시 확인한 뒤 시도해 주세요.')
                self.assertTrue(explanation['next'])

    def test_unknown_failure_does_not_blame_user_or_invent_a_question(self):
        self.assertEqual(step_explanation('unrecognized_phase'), {})
        self.assertEqual(status_explanation('awaiting_input'), {})
        explanation = result_explanation({}, {'conflicts': [{'code': 'unrecognized'}]})
        self.assertNotIn('금액을 줄이', explanation['next'])
        for error in ['call_limit', 'invalid_category', 'invalid_amount', 'feedback_unavailable',
                      'invalid_available_result', 'replay_mismatch', 'spending_unverified', 'unexpected_failure']:
            with self.subTest(error=error):
                value = status_explanation('failed', error=error)
                self.assertTrue(value['why'])
                self.assertTrue(value['next'])
                self.assertNotIn('바꾸고 싶은 항목부터', value['why'])


class ResponseContractTest(unittest.TestCase):
    def test_every_phase_uses_one_contract_and_routine_questions_have_no_extra_explanations(self):
        for phase in ['category', 'direction', 'amount', 'amount_mode', 'sources', 'destinations', 'scope',
                      'comparison', 'financial_selection', 'distribution', 'manual_distribution']:
            with self.subTest(phase=phase):
                state = {'requests': [item('TRANSPORT_CAR', 50000)], 'pending': {'kind': phase, 'index': 0}}
                reply = {'lines': ['다음 질문 하나예요?'], 'explanation': step_explanation(phase, state)}
                view = guidance({'status': 'awaiting_input', 'reply': reply}, state)
                self.assertEqual(set(view), {'kind', 'context', 'message', 'reason', 'next', 'details'})
                self.assertEqual(view['message'], '다음 질문 하나예요?')
                self.assertEqual(view['details'], [])
                self.assertEqual(view['reason'], '')
                self.assertIn('교통·차량', view['context'])

    def test_constraints_and_errors_explain_the_actual_blocker_without_another_question(self):
        response = {'status': 'awaiting_input', 'reply': {'lines': ['잠금을 풀까요?'],
            'explanation': step_explanation('unlock')}}
        view = guidance(response, {'pending': {'kind': 'unlock'}})
        self.assertIn('잠금', view['reason'])
        self.assertIn('다음 예산', view['next'])
        failed = guidance({'status': 'failed', 'error': 'simulation_failed', 'reply': {'lines': ['다시 항목을 알려 주세요.']}}, {})
        self.assertEqual(failed['kind'], 'error')
        self.assertIn('계산하는 중', failed['message'])
        self.assertNotIn('항목', failed['message'])
        self.assertTrue(failed['next'])

    def test_omitted_recommendations_and_explicit_explanations_keep_their_reasons(self):
        reason = '최근 지출을 다 확인하지 못했어요.'
        response = {'status': 'awaiting_input', 'reply': {'lines': ['직접 조정할 항목을 골라 주세요.'],
            'explanation': {'brief': '자동 추천을 제외했어요.', 'why': reason, 'next': '내역을 조회할 수도 있어요.'}}}
        view = guidance(response, {'pending': {'kind': 'funding_scope'}})
        self.assertIn('추천', view['reason'])
        self.assertIn(reason, view['details'])
        response['reply']['detail'] = True
        explained = guidance(response, {'pending': {'kind': 'funding_scope'}})
        self.assertEqual(explained['kind'], 'explanation')
        self.assertEqual(explained['details'].count(reason), 1)



@unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'Opt-in LLM; synthetic dialogue only')
class LiveAdjustmentIntentTest(unittest.TestCase):
    def setUp(self):
        from chat.llm.live import LiveReading
        self.live = LiveReading(30, None)

    def test_same_uncertainty_has_different_meanings_from_previous_question(self):
        cases = [
            ('sources', '어디에서 가져올까요?', '잘 모르겠어', 'help_decide'),
            ('sources', '어디에서 가져올까요?', '뭘 골라야 할지 감이 안 와', 'help_decide'),
            ('discussion', '방금 설명한 내용이 이해되셨나요?', '잘 모르겠어', 'explain'),
            ('one_off_review', '이 지출은 이번에만 쓴 돈인가요?', '잘 모르겠어', None),
            ('destinations', '어느 항목에 넣을까요?', '어떻게 정하면 좋을지 도와줘', 'help_decide'),
            ('unlock', '잠금을 풀까요?', '어느 쪽을 골라야 할지 모르겠네', 'help_decide'),
        ]
        for kind, line, text, expected in cases:
            with self.subTest(kind=kind, text=text):
                result = interpretation.read(text, self.live, pending={'kind': kind, 'line': line, 'choices': []},
                    previous={'requests': [item('LIVING', 50000)], 'sources': [], 'destinations': []})
                self.assertEqual(result['action'], expected, result)
                self.assertIsNone(result.get('selected_choice'))
                self.assertIsNone(result.get('approval'))
                self.assertIsNone(result.get('unlock'))

    def test_life_change_switches_topic_and_keeps_next_cycle_scope(self):
        for text in ['다음달부터 교육비에 50만원 더 나감', '이제 학원 다니느라 교육비가 매달 50만원 더 필요해']:
            with self.subTest(text=text):
                result = interpretation.read(text, self.live,
                    pending={'kind': 'sources', 'line': '저축에 더 넣을 돈을 어디에서 가져올까요?'},
                    previous={'requests': [item('SAVINGS', 300000)], 'sources': []})
                self.assertEqual(result['requests'], [item('EDUCATION', 500000)], result)
                self.assertEqual(result['reference'], 'new', result)
                self.assertIsNone(result.get('approval'))
                if text.startswith('다음달'):
                    self.assertEqual(result['scope'], 'next_cycle')
                    self.assertFalse(result['unclear'])

    def test_savings_guidance_and_topic_switch_follow_live_natural_language(self):
        fixture = AdjustmentDialogueTest()
        fixture.setUp()
        fixture.context.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        fixture.context['bucket_available']['CUSHION_MONTHLY'] = 185000
        fixture.context['locks'] = dict(fixture.context['allocations'])
        fixture.ports['complete'] = self.live
        for text in ['저축 30만원 더 늘리고 싶어', '어디서 가져올 수 있는데', '어케할수있는데', '뭘 고를지 모르겠어']:
            result = dialogue.handle({'conversation_id': 'talk', 'text': text}, **fixture.ports)
            if text != '저축 30만원 더 늘리고 싶어':
                lines = ' '.join(result['reply']['lines'])
                self.assertIn('185,000원', lines, result['status'])
                self.assertIn('115,000원이 부족', lines)
                self.assertNotIn('저축 ·', lines)
                self.assertEqual(fixture.store.state['requests'], [item('SAVINGS', 300000)])
        result = dialogue.handle({'conversation_id': 'talk', 'text': '다음달부터 교육비에 50만원 더 나감'}, **fixture.ports)
        self.assertEqual(fixture.store.state['requests'], [item('EDUCATION', 500000)])
        self.assertIn('교육', ' '.join(result['reply']['lines']))
        self.assertFalse(fixture.proposals)

    def test_spare_concern_asks_minimum_and_recalculates_without_spending_it_all(self):
        ctx, store = context(), Store()
        ctx['spending_status'] = 'complete'
        ctx['funding_amount'] += 98176 - ctx['buckets']['SPARE']
        ctx['buckets']['SPARE'] = ctx['bucket_available']['SPARE'] = 98176
        request = envelope(item('HOUSEHOLD', 95000), sources=['SPARE'])
        preview = simulate_funding(ctx, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        for text, ask in [('여유돈이 너무 없어지잖아', True),
                          ('이렇게 하면 여윳돈이 거의 바닥나는데 더 남겨야겠어', True),
                          ('여윳돈은 적어도 3만원 남기고 싶어', False)]:
            with self.subTest(text=text):
                store.state = {'requests': request['items'], 'sources': ['SPARE'], 'proposal': deepcopy(preview), 'work_ref': 'old'}
                tasks = {'old': {'state': 'awaiting_approval', 'task_ref': 'old'}}

                def cancel(ref):
                    tasks[ref]['state'] = 'canceled'

                ports = dict(store=store, complete=self.live, get_context=lambda: deepcopy(ctx),
                    simulate=simulate_funding, propose=Mock(), task_status=lambda ref: tasks[ref], cancel=cancel, allocator=allocation)
                result = dialogue.handle({'conversation_id': 'talk', 'text': text}, **ports)
                if ask:
                    self.assertEqual(store.state.get('pending', {}).get('field'), 'spare_minimum', result)
                    self.assertIn('최소 얼마', result['reply']['lines'][0])
                    result = dialogue.handle({'conversation_id': 'talk', 'text': '적어도 3만원은 남겨줘'}, **ports)
                self.assertEqual(store.state.get('spare_minimum'), 30000, result)
                self.assertIn('comparison', result['reply'], result)
                plans = result['reply']['comparison']['plans']
                self.assertTrue(plans)
                for plan in plans:
                    self.assertGreaterEqual(plan['result']['buckets']['SPARE'], 30000)
                    self.assertEqual(plan['result']['requests']['spare_minimum'], 30000)
                self.assertEqual(tasks['old']['state'], 'canceled')
                ports['propose'].assert_not_called()

    def test_complete_dialogues_correct_add_withdraw_and_cancel_without_saving(self):
        for turns in [
            [('여윳돈에서 식비에 5만원 보태줘', {'FOOD': 450000}),
             ('식비 말고 교통비였어. 나머지 조건은 그대로', {'FOOD': 400000, 'TRANSPORT_CAR': 150000}),
             ('거기 2만원만 늘리는 걸로 바꿀게', {'TRANSPORT_CAR': 120000}),
             ('그런데 교육에도 1만원 더 넣고 싶어. 여윳돈에서', {'TRANSPORT_CAR': 120000, 'EDUCATION': 60000}),
             ('교육 추가는 빼고 아까 교통비만 유지해줘', {'TRANSPORT_CAR': 120000, 'EDUCATION': 50000})],
            [('쇼핑에서 3만원, 교육에서 2만원 빼서 전부 교통비에 넣어줘',
              {'FASHION_SHOPPING': 170000, 'EDUCATION': 30000, 'TRANSPORT_CAR': 150000}),
             ('교육 줄이는 건 빼자. 쇼핑에서 줄인 돈만 교통비로',
              {'FASHION_SHOPPING': 170000, 'EDUCATION': 50000, 'TRANSPORT_CAR': 130000})],
        ]:
            store, ctx, tasks = Store(), context(), {}
            before = deepcopy(ctx)

            def propose(context, result, **settings):
                ref = f'task-{len(tasks)}'
                # Match AdjustmentAdapter.propose: a new preview replaces the previous one.
                for row in tasks.values():
                    if row['state'] == 'awaiting_approval':
                        row['state'] = 'canceled'
                tasks[ref] = {'state': 'awaiting_approval', 'task_ref': ref}
                return {**result, **settings, **tasks[ref], 'proposal_id': ref}

            def cancel(ref):
                tasks[ref]['state'] = 'canceled'
                return tasks[ref]

            ports = dict(store=store, complete=self.live, get_context=lambda: deepcopy(ctx),
                simulate=lambda current, request: simulate_funding(current, request, allocate_group_reduction=allocation),
                propose=propose, cancel=cancel, task_status=lambda ref: deepcopy(tasks[ref]), allocator=allocation)
            for text, expected in turns:
                with self.subTest(text=text):
                    result = dialogue.handle({'conversation_id': 'synthetic', 'text': text}, **ports)
                    self.assertNotIn('error', result, result)
                    proposal = result['reply'].get('proposal')
                    self.assertIsNotNone(proposal, result)
                    self.assertEqual({key: proposal['allocations'][key] for key in expected}, expected)
                    self.assertEqual(sum(row['state'] == 'awaiting_approval' for row in tasks.values()), 1)
                    self.assertEqual(ctx, before)
            result = dialogue.handle({'conversation_id': 'synthetic', 'text': '이번 조정은 전부 그만둘게'}, **ports)
            self.assertEqual(result['status'], 'ended')
            self.assertTrue(all(row['state'] == 'canceled' for row in tasks.values()))
            self.assertEqual(ctx, before)

    def interpret(self, text, state=None):
        def complete(system, payload):
            if (state or {}).get('pending', {}).get('kind') in {'financial_selection', 'financial_data', 'one_off_review'}:
                return self.live(system, payload)
            result = json.loads(self.live(dialogue.DIALOGUE_PROMPT, payload))
            return json.dumps(result['reading'], ensure_ascii=False)
        return interpretation.read(text, complete, categories=context()['category_names'],
                                   **interpretation.dialogue_context(state or {}))

    def test_living_shortage_and_short_answers_follow_the_current_question(self):
        from chat.router_core.reading import read as route_read
        for text in ['생활비가 부족해', '생활비 늘려줘', '한 달 쓰는 돈이 빠듯해서 좀 늘리고 싶어']:
            with self.subTest(text=text):
                routed = route_read(text, None, self.live)
                self.assertIn('budget_adjust', routed.intents)
                data = self.interpret(text)
                self.assertTrue(data['mine'])
                self.assertEqual([(row['category'], row['direction']) for row in data['requests']], [('LIVING', 'up')])
                self.assertFalse(data['unclear'])
        state = {'requests': [item('LIVING', None, 'up', None)], 'pending': {'kind': 'financial_data',
            'line': '생활비를 직접 조정할까요, 아니면 취소할까요?',
            'choices': [('manual_living', '생활비 직접 조정'), ('cancel', '취소')]}}
        for text in ['조정해', '조정하라고', '계속 진행해줘']:
            with self.subTest(text=text):
                data = self.interpret(text, state)
                self.assertEqual(data['selected_choice'], 'manual_living')
                self.assertFalse(data['requests'])
                self.assertFalse(data['cancel'])

    def test_amount_answer_keeps_the_category_selected_in_the_previous_turn(self):
        state = {'requests': [item('TRANSPORT_CAR', None, 'up', None)], 'pending': {
            'kind': 'amount', 'index': 0, 'line': '교통·차량 예산을 얼마 늘릴까요?', 'choices': []}}
        for text in ['5만원 늘려줘', '생활비를 5만원 늘리고 싶어요', '교통비에 5만원 더 넣을래']:
            with self.subTest(text=text):
                data = self.interpret(text, state)
                self.assertEqual(len(data['requests']), 1)
                self.assertIn(data['requests'][0]['category'], [None, 'TRANSPORT_CAR'])
                self.assertEqual(data['requests'][0]['amount'], 50000)
                self.assertFalse(data['unclear'])

    def test_equivalent_intents_across_categories_and_wording(self):
        for text, category, direction in [
            ('병원비 쪽 예산을 좀 덜 잡고 싶어', 'MEDICAL_HEALTH', 'down'),
            ('식비로 잡힌 돈이 모자라서 더 배정하고 싶어', 'FOOD', 'up'),
            ('옷 사는 데 잡힌 예산이 과한 것 같아 줄여줘', 'FASHION_SHOPPING', 'down'),
            ('교육에 좀 더 쓰고 싶은데 얼마가 좋을지는 모르겠어', 'EDUCATION', 'up'),
            ('미용 예산을 낮추고 싶은데 적당한 액수는 모르겠네', 'BEAUTY', 'down'),
        ]:
            with self.subTest(text=text):
                data = self.interpret(text)
                self.assertTrue(data['mine'])
                self.assertEqual([(row['category'], row['direction'], row['amount']) for row in data['requests']],
                                 [(category, direction, None)])
                self.assertFalse(data['unclear'])

    def test_same_words_have_different_meaning_by_phase(self):
        for phase, expected in [('funding_scope', 'compare'), ('comparison', 'recommend'), ('financial_selection', 'recommend')]:
            for text in ['어느 쪽이 내 상황에 맞을까?', '도무지 감이 안 와. 골라줄 수 있어?']:
                with self.subTest(phase=phase, text=text):
                    choices = {
                        'funding_scope': [('spare', '여윳돈 가져오기'), ('living', '생활비 내에서')],
                        'comparison': [('plan_spare', '여윳돈에서 가져오기'), ('plan_living', '생활비에서 가져오기')],
                        'financial_selection': [('financial_0', '1안 여윳돈'), ('financial_1', '2안 생활비')],
                    }[phase]
                    state = {'requests': [item(amount=50000)], 'pending': {'kind': phase, 'choices': choices,
                        'line': '더 넣을 돈을 어디에서 가져올까요?' if phase == 'funding_scope' else '변경안을 볼 방법을 골라 주세요.'}}
                    data = self.interpret(text, state)
                    self.assertIn(data['action'], {'compare', 'recommend', 'help_decide'} if phase == 'funding_scope' else {expected})
                    self.assertFalse(data.get('selected_choice'))
                    self.assertFalse(data['approval'])

    def test_explanation_navigation_and_changes_during_a_question(self):
        state = {'requests': [item(amount=50000)], 'sources': ['SPARE'],
                 'pending': {'kind': 'financial_data', 'field': 'planned', 'line': '여윳돈으로 추가 지출할 예정이 있나요?'}}
        for text, action in [('그걸 알아야 하는 까닭이 뭐야?', 'explain'),
                             ('앞에서 고르던 화면으로 돌아가고 싶어', 'back')]:
            with self.subTest(text=text):
                data = self.interpret(text, state)
                self.assertEqual(data['action'], action)
                self.assertFalse(data['requests'])
        data = self.interpret('아니, 식비는 3만원만 더 늘리는 걸로 바꿀게', state)
        self.assertEqual(data['requests'], [item(amount=30000)])
        self.assertEqual(data['reference'], 'previous')
        self.assertFalse(data['unclear'])
        data = self.interpret('이번 조정은 그만두자', state)
        self.assertTrue(data['cancel'])
        data = self.interpret('그런데 식비는 이번 달에 얼마 썼어?', state)
        self.assertFalse(data['mine'])
        self.assertEqual(data['handoff'], 'spending')

    def test_amount_meaning_and_multiple_sources(self):
        for text, expected in [('교통비를 18만원으로 맞춰줘', item('TRANSPORT_CAR', 180000, None, 'to')),
                               ('교통비를 3만원만큼 더 늘려줘', item('TRANSPORT_CAR', 30000))]:
            with self.subTest(text=text):
                data = self.interpret(text)
                row = data['requests'][0]
                self.assertEqual((row['category'], row['amount'], row['amount_mode']),
                                 (expected['category'], expected['amount'], expected['amount_mode']))
        data = self.interpret('쇼핑에서 3만원, 교육에서 2만원 빼서 전부 교통비에 넣어줘')
        self.assertEqual({(row['category'], row['amount'], row['direction']) for row in data['requests']},
                         {('FASHION_SHOPPING', 30000, 'down'), ('EDUCATION', 20000, 'down')})
        self.assertEqual(data['destinations'], ['TRANSPORT_CAR'])

    def test_specialized_number_reader_does_not_consume_a_correction(self):
        state = {'requests': [item(amount=50000)], 'pending': {'kind': 'financial_data', 'field': 'planned',
                 'line': '여윳돈으로 추가 지출할 예정이 있나요?'}}
        for text, expected in [('추가로 쓸 돈은 없어', 0), ('따로 2만원 쓸 예정이야', 20000),
                               ('아까 식비 늘리기로 한 돈을 2만원으로 바꿀래', None), ('그걸 왜 물어봐?', None)]:
            with self.subTest(text=text):
                data = json.loads(self.live(dialogue.ANSWER_PROMPT, json.dumps(
                    {'text': text, 'field': 'planned', **interpretation.dialogue_context(state)}, ensure_ascii=False)))
                self.assertEqual(data['value'], expected)

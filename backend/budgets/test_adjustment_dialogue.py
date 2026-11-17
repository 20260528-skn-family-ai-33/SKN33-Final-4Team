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


class AdjustmentInspectionInterpretationTest(unittest.TestCase):
    def test_inspection_focus_is_optional_for_existing_readings_and_validated(self):
        legacy = reading(action='inspect')
        self.assertNotIn('inspection', interpretation.parse(legacy))
        for focus in ('capacity', 'sources', None):
            with self.subTest(focus=focus):
                parsed = interpretation.parse({**legacy, 'inspection': focus})
                self.assertEqual(parsed['inspection'], focus)
        for focus in ('all', True, 0, [], {}):
            with self.subTest(focus=focus):
                with self.assertRaisesRegex(interpretation.InterpretationFailed, 'invalid_inspection'):
                    interpretation.parse({**legacy, 'inspection': focus})

    def test_live_schema_lookup_includes_inspection_for_both_interpreters(self):
        from chat.llm.schemas import schema_for
        for prompt in (interpretation.PROMPT, dialogue.DIALOGUE_PROMPT):
            with self.subTest(prompt=prompt[:30]):
                _, schema = schema_for(prompt)
                if prompt == dialogue.DIALOGUE_PROMPT:
                    schema = schema['properties']['reading']
                self.assertEqual(schema['properties']['inspection']['enum'], ['capacity', 'sources', None])
                self.assertIn('inspection', schema['required'])

    def test_inspection_focus_does_not_replace_or_persist_budget_conditions(self):
        original = {'requests': [item('FOOD', 50000)], 'sources': ['HOUSEHOLD'], 'excluded': ['SAVINGS'],
                    'scope': 'cycle', 'clear_targets': ['FOOD'], 'spare_minimum': 200000}
        for focus in ('capacity', 'sources'):
            with self.subTest(focus=focus):
                state = deepcopy(original)
                _merge(state, interpretation.parse(reading(action='inspect', inspection=focus)), {})
                for field, value in original.items():
                    self.assertEqual(state[field], value)
                self.assertNotIn('inspection', state)


class AdjustmentDialogueTest(unittest.TestCase):

    def test_amount_edit_choice_names_the_requested_category_and_direction(self):
        for direction, label in [('up', '식비 늘릴 금액 바꾸기'), ('down', '식비 줄일 금액 바꾸기')]:
            self.store.state = {}
            response = self.turn(reading(requests=[item('FOOD', 50000, direction)], action='compare'))
            choice = next(row for row in response['reply']['choices'] if row['value'] == 'manual')
            self.assertEqual(choice['label'], label)

    def test_category_capacity_inquiry_keeps_target_and_lists_verified_living_donors(self):
        self.context.update(spending_status='partial', budget_preview=True,
                            observed_spending={'transaction_count': 3, 'through': '2026-11-09'})
        response = self.turn(reading(requests=[item('FOOD', None)], action='inspect', inspection='sources'))
        lines = response['reply']['lines']
        self.assertTrue(lines[0].startswith('다른 생활비 · 최대'), lines)
        self.assertFalse(any('확인된 증액 한도' in line for line in lines))
        self.assertIn('· 생활용품 5만 원', lines)
        self.assertIn('· 의료·건강 5만 원', lines)
        self.assertTrue(any('2026-11-09' in line and '참고 금액' in line for line in lines))
        self.assertFalse(any('계산하지 못했' in line for line in lines))
        self.assertIsNone(self.store.state['requests'][0]['amount'])
        self.assertFalse(self.proposals)
        # Subsequent maximum inquiry must preserve an explicitly selected source.
        self.context['priorities'].pop('FASHION_SHOPPING')
        self.store.state['sources'] = ['FASHION_SHOPPING']
        response = self.turn(reading(action='inspect', reference='previous'))
        self.assertIn('패션·쇼핑 · 최대 19만 원', response['reply']['lines'])
        self.assertFalse(any('저축 ·' in line for line in response['reply']['lines']))
        self.store.state = {}
        response = self.turn(reading(action='inspect', requests=[item('FOOD', None)], sources=['FASHION_SHOPPING']))
        self.assertIn('패션·쇼핑 · 최대 19만 원', response['reply']['lines'])
        self.assertNotIn('comparison', response['reply'])

    def test_capacity_breakdown_matches_calculated_total_and_deduplicates_sources(self):
        from .adjustment_core.advisor import capacities, source_capacity_lines
        found = capacities(self.context, 'FOOD', sources=['LIVING', 'HOUSEHOLD'], allocator=allocation)
        self.assertEqual(set(found), {'LIVING'})
        lines = source_capacity_lines(self.context, 'FOOD', found, sources=['LIVING', 'HOUSEHOLD'], allocator=allocation)
        self.assertEqual(lines[0], '다른 생활비 · 최대 20만 원')
        self.assertCountEqual(lines[1:], ['· 생활용품 5만 원', '· 의료·건강 5만 원',
                                         '· 뷰티·미용 5만 원', '· 문화·여가 5만 원'])

    def test_unknown_source_is_not_reported_as_zero(self):
        self.context['bucket_status']['SPARE'] = 'unknown'
        response = self.turn(reading(requests=[item('FOOD', None)], action='inspect'))
        self.assertIn('여윳돈 · 금액 확인 필요', response['reply']['lines'])
        self.assertNotIn('여윳돈 · 최대 0원', response['reply']['lines'])
        self.store.state['sources'] = ['SPARE']
        response = self.turn(reading(action='inspect', reference='previous'))
        self.assertEqual(response['reply']['lines'][0], '여윳돈 · 금액 확인 필요')
        self.assertFalse(any('0원' in line for line in response['reply']['lines']))

    def test_capacity_summary_and_source_details_preserve_request_and_conditions(self):
        self.store.state = {'requests': [item('FOOD', None)], 'excluded': ['SAVINGS'],
                            'pending': {'kind': 'amount', 'index': 0}}
        summary = self.turn(reading(action='inspect', inspection='capacity'))['reply']['lines']
        self.assertEqual(summary[0], '다른 생활비에서 식비에 최대 20만 원을 더할 수 있어요.')
        self.assertFalse(any('· 생활용품' in line or '저축' in line for line in summary))
        details = self.turn(reading(action='inspect', inspection='sources'))['reply']['lines']
        self.assertIn('· 생활용품 5만 원', details)
        self.assertNotEqual(summary, details)
        self.assertEqual(self.store.state['requests'], [item('FOOD', None)])
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertFalse(self.proposals)

    def test_capacity_inquiry_does_not_replace_selected_amount_or_preview(self):
        self.turn(reading(requests=[item('FOOD', 50000)], action='compare'))
        comparison = deepcopy(self.store.state['comparison'])
        response = self.turn(reading(action='inspect', inspection='capacity'))
        self.assertIn('최대 20만 원', response['reply']['lines'][0])
        self.assertEqual(self.store.state['requests'], [item('FOOD', 50000)])
        self.assertEqual(self.store.state['comparison'], comparison)
        self.assertNotIn('comparison', response['reply'])
        self.assertFalse(self.proposals)

    def test_capacity_inquiry_for_total_living_does_not_use_internal_transfers(self):
        response = self.turn(reading(requests=[item('LIVING', None)], action='inspect', inspection='capacity'))
        lines = response['reply']['lines']
        self.assertTrue(any('여윳돈' in line for line in lines))
        self.assertFalse(any('다른 생활비' in line for line in lines))
        self.assertFalse(self.proposals)

    def test_reduction_capacity_inquiry_keeps_direction_and_destination(self):
        self.store.state = {'requests': [item('FOOD', 50000, 'down')], 'destinations': ['EDUCATION'],
                            'excluded': ['SAVINGS'], 'pending': {'kind': 'comparison'}}
        before = deepcopy(self.store.state)
        response = self.turn(reading(action='inspect', inspection='capacity'))
        self.assertIn('줄일 수', ' '.join(response['reply']['lines']))
        self.assertNotIn('더할 수', ' '.join(response['reply']['lines']))
        for key in ('requests', 'destinations', 'excluded'):
            self.assertEqual(self.store.state[key], before[key])
        self.assertFalse(self.proposals)

    def test_living_distribution_choice_opens_editor_without_picking_a_recipient(self):
        response = self.turn(reading(requests=[item('FOOD', 100000, 'down')], action='compare'))
        living = next(p for p in response['reply']['comparison']['plans'] if p['id'] == 'living')
        self.assertEqual(living['status'], 'needs_destination')
        self.assertIsNone(living['result'])
        self.assertIn('plan_living', {c['value'] for c in response['reply']['choices']})
        response = self.turn(reading(selected_choice='plan_living', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'distribution')
        self.assertEqual(response['reply']['distribution_input']['amount'], 100000)
        self.assertNotIn('proposal', response['reply'])
        self.assertFalse(self.proposals)
        self.assertIn('임의로 배분하지', response['reply']['explanation']['brief'])

    def test_distribution_guidance_uses_verified_shortfalls_not_priority(self):
        from .adjustment_core.financial_planner import synthetic_diagnostics
        self.context['diagnostic_inputs'] = synthetic_diagnostics(self.context['allocations'])
        self.context['today'] = self.context['diagnostic_inputs']['as_of']
        for sample in self.context['diagnostic_inputs']['history']['samples']:
            sample['HOUSEHOLD'] = 10000
            sample['MEDICAL_HEALTH'] = 7000
        response = self.turn(reading(requests=[item('FOOD', 100000, 'down')], destinations=['LIVING']))
        evidence = response['reply']['explanation']['brief']
        self.assertIn('생활용품 약 50,000원', evidence)
        self.assertIn('의료·건강 약 20,000원', evidence)
        self.assertNotIn('교통·차량', evidence)
        self.assertIn('예상', evidence)
        self.assertFalse(self.proposals)
        self.store.state = {}
        self.context['spending_status'] = 'incomplete'
        self.context['budget_preview'] = True
        response = self.turn(reading(requests=[item('FOOD', 100000, 'down')], destinations=['LIVING']))
        self.assertNotIn('50,000', response['reply']['explanation']['brief'])
        self.assertIn('임의로 배분하지', response['reply']['explanation']['brief'])

    def test_unspecified_amount_asks_before_calculating_in_either_direction(self):
        for direction in ('up', 'down'):
            for action in (None, 'compare'):
                with self.subTest(direction=direction, action=action):
                    self.store.state = {}
                    result = self.turn(reading(requests=[item('FOOD', None, direction)], action=action))
                    self.assertEqual(self.store.state['pending']['kind'], 'amount')
                    self.assertIsNone(self.store.state['requests'][0]['amount'])
                    self.assertNotIn('comparison', result['reply'])
                    self.assertNotIn('proposal', result['reply'])
                    self.assertFalse(self.proposals)

    def test_explicit_capacity_examples_require_amount_after_method_selection(self):
        for direction in ('up', 'down'):
            with self.subTest(direction=direction):
                self.store.state = {}
                self.proposals.clear()
                original = deepcopy(self.context)
                code = 'FOOD' if direction == 'up' else 'HOUSEHOLD'
                response = self.turn(reading(requests=[item(code, None, direction)],
                    action='compare', use_available=True, excluded=['SAVINGS']))
                comparison = response['reply']['comparison']
                self.assertFalse(comparison['amount_was_given'])
                plan = next(p for p in comparison['plans'] if p['status'] == 'ready')
                response = self.turn(reading(selected_choice='plan_' + plan['id'], reference='previous'))
                self.assertEqual(self.store.state['pending']['kind'], 'amount')
                self.assertIsNone(self.store.state['requests'][0]['amount'])
                self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
                self.assertFalse(self.proposals)
                self.assertNotIn('proposal', response['reply'])
                response = self.turn(reading(requests=[item(code, 20000, direction)], reference='previous'))
                after = response['reply']['proposal']['allocations'][code]
                self.assertEqual(after, original['allocations'][code] + (20000 if direction == 'up' else -20000))
                self.assertEqual(self.context, original)

    def test_financial_example_selection_does_not_create_a_proposal(self):
        self.store.state = {'requests': [item('FOOD', None)], 'excluded': ['SAVINGS'],
            'scope': 'next_cycle', 'clear_targets': ['FOOD'],
            'over_budget': {'category': 'FOOD', 'over_amount': 12000},
            'financial_offer': {'context': deepcopy(self.context), 'comparison': {'amount_was_given': False},
                                'answer': {'plans': [{'funding': [{'source': 'LIVING', 'amount': 100000}]}]}}}
        response = dialogue.selected_preview(self.store, 'talk', self.store.state, 0, self.ports)
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertIsNone(self.store.state['requests'][0]['amount'])
        self.assertEqual(self.store.state['sources'], ['LIVING'])
        self.assertEqual(self.store.state['scope'], 'next_cycle')
        self.assertEqual(self.store.state['clear_targets'], ['FOOD'])
        self.assertNotIn('proposal', response['reply'])
        self.assertFalse(self.proposals)

    def test_ambiguous_transfer_keeps_amount_until_direction_is_confirmed(self):
        result = self.turn(reading(requests=[item('FOOD', 50000, None)]))
        self.assertEqual(self.store.state['pending']['kind'], 'direction', result)
        self.assertEqual(self.store.state['requests'], [item('FOOD', 50000, None)])
        self.assertFalse(self.proposals)
        result = self.turn(reading(requests=[item('FOOD', None, 'down', None)], reference='previous'))
        self.assertEqual(self.store.state['requests'], [item('FOOD', 50000, 'down')])
        ready = [p for p in result['reply']['comparison']['plans'] if p['status'] == 'ready']
        self.assertTrue(ready)
        self.assertTrue(all(p['result']['allocations']['FOOD'] == 350000 for p in ready))

    def test_preview_destination_change_preserves_reduction_and_original_conditions(self):
        request = envelope(item('FOOD', 100000, 'down'), destinations=['CULTURE_LEISURE'], excluded=['SAVINGS'])
        preview = simulate_funding(self.context, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        self.store.state = {'requests': request['items'], 'destinations': ['CULTURE_LEISURE'],
                            'excluded': ['SAVINGS'], 'proposal': deepcopy(preview), 'work_ref': 'old',
                            'scope': 'cycle', 'clear_targets': ['FOOD']}
        self.ports['task_status'].return_value = {'state': 'awaiting_approval'}
        self.ports['cancel'].side_effect = lambda ref: self.ports['task_status'].configure_mock(
            return_value={'state': 'canceled', 'task_ref': ref})
        self.complete.side_effect = [
            json.dumps({'action': 'revise', 'category': None, 'destination': None, 'amount': None,
                        'amount_mode': None, 'suggested_target': None}),
            json.dumps(reading(destinations=['MEDICAL_HEALTH'], reference='previous')),
        ]
        original = deepcopy(self.context)
        result = dialogue.handle({'conversation_id': 'talk', 'text': '문화여가 말고 병원비에 옮겨줘'}, **self.ports)
        self.assertNotIn('error', result)
        self.assertIn('proposal', result['reply'], result)
        after = result['reply']['proposal']['allocations']
        self.assertEqual({k: after[k] for k in ['FOOD', 'CULTURE_LEISURE', 'MEDICAL_HEALTH']},
                         {'FOOD': 300000, 'CULTURE_LEISURE': 50000, 'MEDICAL_HEALTH': 150000})
        payload = json.loads(self.complete.call_args_list[0].args[1])
        self.assertEqual(payload['previous']['requests'], request['items'])
        self.assertEqual(payload['previous']['destinations'], ['CULTURE_LEISURE'])
        self.assertEqual(self.store.state['requests'], request['items'])
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertEqual(self.store.state['scope'], 'cycle')
        self.assertEqual(self.store.state['clear_targets'], ['FOOD'])
        self.assertEqual(self.context, original)

    def test_oversized_decrease_keeps_request_and_recovers_to_examples(self):
        self.context['allocations']['EDUCATION'] = 30000
        self.context['buckets']['SPARE'] += 20000
        for mode, amount in [('by', 50000), ('percent_by', 200)]:
            with self.subTest(mode=mode):
                self.store.state = {}
                request = item('EDUCATION', amount, 'down', mode)
                result = self.turn(reading(requests=[request], action='compare', excluded=['SAVINGS']))
                self.assertEqual(self.store.state['pending']['kind'], 'amount')
                self.assertIn('30,000원', result['reply']['lines'][0])
                self.assertIn('현재 예산보다', result['reply']['lines'][0])
                self.assertEqual(self.store.state['requests'], [request])
                result = self.turn(reading(requests=[item('EDUCATION', 10000, 'down')], reference='previous'))
                self.assertIn('comparison', result['reply'], result)
                ready = [p for p in result['reply']['comparison']['plans'] if p['status'] == 'ready']
                self.assertTrue(ready)
                self.assertTrue(all(p['result']['allocations']['EDUCATION'] == 20000 for p in ready))
                self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertFalse(self.proposals)

    def test_valid_reduction_to_zero_and_absolute_target_still_show_examples(self):
        for mode, amount, target in [('by', 50000, 0), ('to', 10000, 10000)]:
            with self.subTest(mode=mode):
                self.store.state = {}
                result = self.turn(reading(requests=[item('EDUCATION', amount, 'down', mode)], action='compare'))
                ready = [p for p in result['reply']['comparison']['plans'] if p['status'] == 'ready']
                self.assertTrue(ready)
                self.assertTrue(all(p['result']['allocations']['EDUCATION'] == target for p in ready))

    def test_excessive_reduction_keeps_other_requests_and_constraints(self):
        requests = [item('FOOD', 10000), item('EDUCATION', 60000, 'down')]
        result = self.turn(reading(requests=requests, destinations=['TRANSPORT_CAR'], excluded=['SAVINGS']))
        self.assertEqual(self.store.state['pending']['kind'], 'amount', result)
        self.assertEqual(self.store.state['pending']['index'], 1)
        self.assertEqual(self.store.state['requests'], requests)
        self.assertEqual(self.store.state['destinations'], ['TRANSPORT_CAR'])
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertFalse(self.proposals)

    def test_zero_budget_reduction_asks_for_another_category(self):
        self.context['allocations']['EDUCATION'] = 0
        self.context['buckets']['SPARE'] += 50000
        for mode, amount in [('by', 50000), ('to', 0), ('percent_by', 100), (None, None)]:
            with self.subTest(mode=mode):
                self.store.state = {}
                result = self.turn(reading(requests=[item('EDUCATION', amount, 'down', mode)]))
                self.assertEqual(self.store.state['pending']['kind'], 'category')
                self.assertIn('교육의 현재 예산은 0원', result['reply']['lines'][0])
                self.assertTrue(result['reply']['choices'])
                self.assertNotIn('EDUCATION', [row['value'] for row in result['reply']['choices']])

    def test_direct_distribution_uses_same_zero_budget_reason_and_keeps_recipient(self):
        self.context['allocations']['EDUCATION'] = 0
        self.context['buckets']['SPARE'] += 50000
        result = self.turn(reading(requests=[item('EDUCATION', 50000, 'down'), item('FOOD', 50000)], action='distribute'))
        self.assertIn('교육의 현재 예산은 0원', result['reply']['lines'][0])
        self.assertEqual(self.store.state['requests'], [item('EDUCATION', 50000, 'down'), item('FOOD', 50000)])
        self.assertNotIn('FOOD', [row['value'] for row in result['reply']['choices']])
        self.assertFalse(self.proposals)

    def test_comparison_failure_explains_known_cause_and_preserves_request(self):
        from .adjustment_core.conversation import _show_comparison
        for error, status, reason in [('funding_total_mismatch', 'failed', '합계'),
                                      ('direction_conflict', 'awaiting_input', '최종 금액'),
                                      ('unexpected_error', 'failed', '계산하는 중')]:
            with self.subTest(error=error):
                self.store.state = {'requests': [item('EDUCATION', 10000, 'down')]}
                requests = deepcopy(self.store.state['requests'])
                with patch('budgets.adjustment_core.comparison.compare_adjustments', side_effect=ValueError(error)):
                    result = _show_comparison(self.store, 'talk', self.store.state, self.context, self.context, allocation)
                self.assertEqual(result['status'], status)
                self.assertIn(reason, ' '.join(result['reply']['lines']))
                self.assertEqual(self.store.state['requests'], requests)
                self.assertNotIn('방법별 비교를 할 수', ' '.join(result['reply']['lines']))

    def test_amount_context_preserves_category_but_allows_explicit_topic_change(self):
        pending = {'kind': 'amount', 'index': 0}
        previous = {'requests': [item('TRANSPORT_CAR', None, 'up', None)]}
        for reference, update, expected in [('previous', 'merge', 'TRANSPORT_CAR'),
                                           ('new', 'merge', 'LIVING'), ('previous', 'replace', 'LIVING')]:
            answer = reading(requests=[item('LIVING', 50000)], reference=reference, request_update=update)
            data = interpretation.read('금액 답변', lambda *_: json.dumps(answer),
                                       pending=pending, previous=previous)
            self.assertEqual(data['requests'][0]['category'], expected)

    def test_unclear_reason_and_help_do_not_remove_large_increase_warning(self):
        self.context['monthly_income'] = 3000000
        self.turn(reading(requests=[item('LIVING', 1000000)]))
        for answer in [reading(unclear=['그래도 계속할까요?']),
                       reading(action='help_decide', reference='previous')]:
            response = self.turn(answer)
            self.assertIn('30% 이상', ' '.join(response['reply']['lines']))
            self.assertEqual(self.store.state['pending']['kind'], 'increase_review')
            self.assertFalse(self.proposals)

    def test_funding_inquiry_shows_protected_capacity_and_preserves_pending_category(self):
        from .adjustment_core.funding import available_bucket_source
        self.turn(reading(requests=[item('LIVING', 50000)]))
        before = deepcopy(self.store.state['requests'])
        response = self.turn(reading(action='inspect', reference='previous'))
        self.assertEqual(guidance(response, self.store.state)['kind'], 'explanation')
        maximum = available_bucket_source(self.context, 'SAVINGS')['maximum']
        self.assertIn(f'저축 · 최대 {maximum:,}원', response['reply']['lines'])
        self.assertEqual(self.store.state['requests'], before)
        self.assertFalse(self.proposals)
        self.context['bucket_status']['SPARE'] = 'unknown'
        self.store.state['excluded'] = ['SAVINGS']
        response = self.turn(reading(action='inspect', reference='previous'))
        self.assertIn('여윳돈 · 사용 가능액 확인 필요', response['reply']['lines'])
        self.assertFalse(any(line.startswith('저축 ·') for line in response['reply']['lines']))

    def test_living_category_step_explanation_includes_funding_meaning(self):
        self.turn(reading(requests=[item('LIVING', 50000)]))
        before = deepcopy(self.store.state['requests'])
        response = self.turn(reading(action='explain', reference='previous'))
        message = guidance(response, self.store.state)['message']
        self.assertIn('저축·월 머니쿠션', message)
        self.assertIn('이미 모은 돈을 인출하는 것은 아니에요', message)
        self.assertEqual(self.store.state['requests'], before)
        self.assertFalse(self.proposals)

    def test_multi_request_missing_amount_does_not_erase_the_known_amount(self):
        requests = [item('FOOD', 50000), item('TRANSPORT_CAR', None, 'up', None)]
        self.turn(reading(requests=requests))
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertEqual(self.store.state['pending']['index'], 1)
        self.assertEqual(self.store.state['requests'][0], requests[0])
        response = self.turn(reading(requests=[item('TRANSPORT_CAR', 30000)], reference='previous'))
        # 두 금액이 모두 정해지면 재원 이름 대신 계산한 추천안을 보여 준다.
        self.assertIn('plan_living', {c['value'] for c in response['reply']['choices']})
        self.assertEqual(response['reply']['comparison']['basis_amount'], 80000)
        self.assertEqual(self.store.state['requests'], [item('FOOD', 50000), item('TRANSPORT_CAR', 30000)])
        self.assertFalse(self.proposals)

    def test_multi_request_source_help_preserves_amounts_and_protections(self):
        requests = [item('FOOD', 50000), item('TRANSPORT_CAR', 30000)]
        self.context['spending_status'] = 'complete'
        self.context['bucket_status']['CUSHION_MONTHLY'] = 'unknown'
        self.turn(reading(requests=requests, excluded=['SAVINGS']))
        for action in ('inspect', 'compare', 'help_decide'):
            response = self.turn(reading(action=action, reference='previous'))
            values = {c['value'] for c in response['reply']['choices']}
            self.assertIn('plan_living', values)
            self.assertNotIn('manual_living', values)
            plans = self.store.state['comparison']['comparison']['plans']
            used = {t['from'] for plan in plans for t in plan['result']['transfers']}
            self.assertFalse(used & {'SAVINGS', 'CUSHION_MONTHLY'})
            self.assertEqual(self.store.state['requests'], requests)
            self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
            self.assertFalse(self.proposals)
        selected = self.turn(reading(selected_choice='plan_living', reference='previous'))
        proposal = selected['reply']['proposal']
        self.assertEqual(proposal['allocations']['FOOD'], self.context['allocations']['FOOD'] + 50000)
        self.assertEqual(proposal['allocations']['TRANSPORT_CAR'], self.context['allocations']['TRANSPORT_CAR'] + 30000)
        self.assertEqual(proposal['buckets']['SAVINGS'], self.context['buckets']['SAVINGS'])

    def test_interpreter_sees_the_same_verified_examples_in_both_comparison_flows(self):
        comparison = {'recommendation': {'plan_id': 'living'}, 'plans': [
            {'id': 'living', 'label': '생활비 내 재배분', 'status': 'ready', 'amount': 50000,
             'result': {'transfers': [{'from': 'BEAUTY', 'to': 'FOOD', 'amount': 50000}]}}]}
        for field, kind in [('comparison', 'comparison'), ('financial_offer', 'financial_selection')]:
            state = {'requests': [item(amount=50000)], 'pending': {'kind': kind},
                     field: {'comparison': deepcopy(comparison)}}
            before = deepcopy(state)
            result = interpretation.dialogue_context(state)
            self.assertEqual(result['previous']['comparison']['recommendation'], comparison['recommendation'])
            self.assertEqual(result['previous']['comparison']['plans'][0]['amount'], 50000)
            self.assertEqual(state, before)

    def test_feedback_role_conflict_is_reinterpreted_before_changing_money(self):
        request = envelope(item('HOUSEHOLD', 50000), sources=['SPARE'])
        preview = simulate_funding(self.context, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        self.store.state = {'requests': request['items'], 'sources': ['SPARE'], 'proposal': deepcopy(preview), 'work_ref': 'old'}
        self.ports['task_status'] = Mock(return_value={'state': 'awaiting_approval'})
        wrong = {'action': 'refine_less', 'category': 'HOUSEHOLD', 'amount': None, 'amount_mode': None, 'suggested_target': None}
        correct = {**wrong, 'category': 'SPARE'}
        self.complete.side_effect = [json.dumps(wrong), json.dumps(correct)]
        result = dialogue.handle({'conversation_id': 'talk', 'text': '여윳돈이 너무 없어지잖아'}, **self.ports)
        self.assertEqual(self.store.state['pending']['field'], 'spare_minimum')
        self.assertIn('최소', result['reply']['lines'][0])
        repair = json.loads(self.complete.call_args_list[1].args[1])
        self.assertEqual(repair['recipient'], 'HOUSEHOLD')
        self.assertEqual(repair['funding_sources'], ['SPARE'])
        self.assertEqual(repair['interpretation_error']['code'], 'recipient_is_not_funding_source')
        self.assertEqual(self.proposals, [])
        self.ports['cancel'].assert_not_called()

    def test_feedback_failure_keeps_the_verified_preview_and_task(self):
        request = envelope(item('HOUSEHOLD', 50000), sources=['SPARE'])
        preview = simulate_funding(self.context, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        before = {'requests': request['items'], 'sources': ['SPARE'], 'proposal': deepcopy(preview), 'work_ref': 'old'}
        self.store.state = deepcopy(before)
        self.ports['task_status'] = Mock(return_value={'state': 'awaiting_approval'})
        self.complete.side_effect = RuntimeError('temporary LLM error')
        result = dialogue.handle({'conversation_id': 'talk', 'text': '돈을 더 남기고 싶어'}, **self.ports)
        self.assertEqual(result['status'], 'awaiting_input')
        self.assertEqual(result['reply']['proposal'], preview)
        self.assertEqual(result['work_ref'], 'old')
        for key, value in before.items():
            self.assertEqual(self.store.state[key], value)
        self.assertEqual(self.proposals, [])
        self.ports['cancel'].assert_not_called()

    def test_partial_withdrawal_read_failure_keeps_preview_and_can_resume(self):
        from chat.llm.openai_rest import CapReached, ResponseError

        for failure, code in [(ResponseError('network'), 'call_failed'),
                              (ResponseError('incomplete'), 'call_failed'),
                              (CapReached(), 'call_limit')]:
            with self.subTest(failure=str(failure)):
                self.setUp()
                request = envelope(item('FASHION_SHOPPING', 30000, 'down'),
                                   item('EDUCATION', 20000, 'down'), destinations=['TRANSPORT_CAR'])
                preview = simulate_funding(self.context, request)
                preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
                before = {'requests': request['items'], 'destinations': ['TRANSPORT_CAR'],
                          'proposal': deepcopy(preview), 'work_ref': 'old', 'excluded': ['SAVINGS']}
                self.store.state = deepcopy(before)
                original = deepcopy(self.context)
                self.ports['task_status'] = Mock(return_value={'state': 'awaiting_approval'})
                feedback = json.dumps({'action': 'revise', 'category': None, 'amount': None,
                                       'amount_mode': None, 'suggested_target': None})
                self.complete.side_effect = [feedback, failure]
                result = dialogue.handle({'conversation_id': 'talk', 'text': '교육 감액은 취소할게'}, **self.ports)
                self.assertEqual(result['error'], code)
                self.assertEqual(result['status'], 'awaiting_input')
                self.assertEqual(result['reply']['proposal'], preview)
                self.assertEqual(result['work_ref'], 'old')
                self.assertEqual({key: self.store.state[key] for key in before}, before)
                self.assertEqual(self.proposals, [])
                self.ports['cancel'].assert_not_called()

                def cancel(ref):
                    self.ports['task_status'].return_value = {'state': 'canceled', 'task_ref': ref}
                    return self.ports['task_status'].return_value

                self.ports['cancel'].side_effect = cancel
                self.complete.side_effect = [feedback, json.dumps(reading(
                    requests=[item('FASHION_SHOPPING', 30000, 'down')], destinations=['TRANSPORT_CAR'],
                    request_update='replace', reference='previous'))]
                result = dialogue.handle({'conversation_id': 'talk', 'text': '쇼핑에서 줄인 돈만 옮겨줘'}, **self.ports)
                self.assertNotIn('error', result)
                self.assertIn('proposal', result['reply'], result)
                after = result['reply']['proposal']['allocations']
                self.assertEqual({key: after[key] for key in ['FASHION_SHOPPING', 'EDUCATION', 'TRANSPORT_CAR']},
                                 {'FASHION_SHOPPING': 170000, 'EDUCATION': 50000, 'TRANSPORT_CAR': 130000})
                self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
                self.assertEqual(self.context, original)

    def test_source_decrease_encoding_keeps_the_actual_recipient_for_feedback(self):
        self.context['funding_amount'] += 98176 - self.context['buckets']['SPARE']
        self.context['buckets']['SPARE'] = self.context['bucket_available']['SPARE'] = 98176
        request = envelope(item('SPARE', 95000, 'down'), destinations=['HOUSEHOLD'])
        preview = simulate_funding(self.context, request)
        self.assertEqual(preview['status'], 'partial')
        preview.pop('requested_targets')
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        self.store.state = {'requests': request['items'], 'sources': ['SPARE'], 'destinations': ['HOUSEHOLD'],
                            'proposal': deepcopy(preview), 'proposal_request': deepcopy(request), 'work_ref': 'old'}
        self.ports['task_status'] = Mock(return_value={'state': 'awaiting_approval'})
        self.complete.return_value = json.dumps({'action': 'refine_less', 'category': 'SPARE',
            'amount': None, 'amount_mode': None, 'suggested_target': None})
        result = dialogue.handle({'conversation_id': 'talk', 'text': '여윳돈이 너무 없어지잖아'}, **self.ports)
        self.assertEqual(self.store.state['pending']['field'], 'spare_minimum')
        self.assertEqual(self.store.state['primary_request']['items'], [item('HOUSEHOLD', 95000)])
        self.assertIn('최소', result['reply']['lines'][0])
        self.assertEqual(self.proposals, [])

    def test_explicit_transfer_amount_omitted_by_model_is_reinterpreted(self):
        empty = reading(requests=[], sources=['SPARE'], destinations=['HOUSEHOLD'])
        filled = reading(requests=[item('HOUSEHOLD', 95000)], sources=['SPARE'])
        self.complete.side_effect = [json.dumps({'reading': empty, 'reply': ''}), json.dumps({'reading': filled, 'reply': ''})]
        result = dialogue.handle({'conversation_id': 'talk', 'text': '여윳돈에서 생활용품에 95000원 옮겨줘',
            'hints': {'categories': ['HOUSEHOLD'], 'direction': 'up', 'amount': 95000, 'amount_mode': 'by'}}, **self.ports)
        self.assertEqual(result['reply']['proposal']['allocations']['HOUSEHOLD'], 145000)
        self.assertEqual(result['reply']['proposal']['buckets']['SPARE'], 305000)
        self.assertIn('interpretation_error', json.loads(self.complete.call_args_list[1].args[1]))
        self.assertEqual(len(self.proposals), 1)

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
                comparison = result['reply'].get('comparison') or self.store.state['comparison']['comparison']
                plans = [p for p in comparison['plans'] if p['status'] in {'ready', 'partial'}]
                self.assertTrue(plans)
                for plan in plans:
                    # 생활비가 모두 잠겨 여윳돈(30% 남김)을 먼저 쓰고, 부족분만 월 머니쿠션에서 가져온다.
                    self.assertEqual(plan['amount'], 300000)
                    self.assertEqual(plan['status'], 'ready')
                    self.assertEqual(plan['result']['buckets']['SAVINGS'], 900000)
                    self.assertEqual({t['from']: t['amount'] for t in plan['result']['transfers']},
                                     {'SPARE': 280000, 'CUSHION_MONTHLY': 20000})
                self.assertIn('keep_spare', [c['value'] for c in result['reply']['choices']])
                self.assertFalse(self.proposals)

    def test_combined_card_can_be_selected_and_keeps_its_protected_spare(self):
        self.turn(reading(requests=[item('SAVINGS', 300000)]))
        self.store.state['spare_minimum'] = 200000
        result = self.turn(reading(action='compare'))
        # 생활비로 부족한 만큼만 여윳돈에서 채우는 안이 추천된다.
        self.assertEqual(result['reply']['comparison']['recommendation']['plan_id'], 'spare')
        result = self.turn(reading(selected_choice='plan_spare'))
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
                    self.assertIn('평소 돈이 모자라는 항목', ' '.join(result['reply']['lines']))
                elif action == 'explain':
                    self.assertEqual(self.store.state['pending']['kind'], 'destinations')

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
        self.assertFalse(self.proposals)

    def test_source_question_shows_calculated_examples_without_selecting_or_applying(self):
        for category in ['FOOD', 'TRANSPORT_CAR', 'SAVINGS']:
            for action in ['inspect', 'help_decide', 'explain']:
                with self.subTest(category=category, action=action):
                    self.setUp()
                    request = item(category, 50000)
                    first = self.turn(reading(requests=[request]))
                    self.assertIn('comparison', first['reply'])
                    # Conversations opened before this change must also reach the same cards.
                    self.store.state['pending'] = {'kind': 'sources', 'index': 0, 'choices': [], 'line': ''}
                    response = self.turn(reading(action=action, reference='previous'))
                    comparison = response['reply']['comparison']
                    self.assertEqual(comparison['basis_amount'], 50000)
                    plans = [p for p in comparison['plans'] if p['status'] in {'ready', 'partial'}]
                    self.assertTrue(plans)
                    for plan in plans:
                        result = plan['result']
                        self.assertTrue(result['changes'])
                        self.assertEqual(sum(t['amount'] for t in result['transfers']), plan['amount'])
                        self.assertTrue(all(t['to'] == category for t in result['transfers']))
                    self.assertLessEqual(len(response['reply']['lines']), 1)
                    self.assertEqual(self.store.state['requests'], [request])
                    self.assertFalse(self.store.state.get('sources'))
                    self.assertFalse(self.proposals)
                    self.assertEqual(guidance(response, self.store.state)['message'].splitlines(), response['reply']['lines'])

    def test_first_request_shows_examples_regardless_of_interpreted_action(self):
        for action in [None, 'compare', 'inspect', 'help_decide', 'recommend']:
            with self.subTest(action=action):
                self.setUp()
                self.context['spending_status'] = 'complete'
                response = self.turn(reading(requests=[item('TRANSPORT_CAR', 180000, 'up', 'to')], action=action))
                comparison = response['reply']['comparison']
                self.assertEqual(comparison['basis_amount'], 80000)
                self.assertFalse(guidance(response, self.store.state)['reason'])
                ready = [p for p in comparison['plans'] if p['status'] == 'ready']
                self.assertTrue(ready)
                for plan in ready:
                    result = plan['result']
                    self.assertEqual(result['allocations']['TRANSPORT_CAR'], 180000)
                    self.assertEqual(result['buckets'], self.context['buckets'])
                    self.assertEqual(sum(t['amount'] for t in result['transfers']), 80000)
                self.assertEqual(self.store.state['requests'], [item('TRANSPORT_CAR', 180000, 'up', 'to')])
                self.assertFalse(self.proposals)

    def test_absolute_amount_without_direction_uses_current_budget_for_examples(self):
        response = self.turn(reading(requests=[item('TRANSPORT_CAR', 180000, None, 'to')]))
        self.assertEqual(response['reply']['comparison']['basis_amount'], 80000)
        self.assertEqual(self.store.state['requests'], [item('TRANSPORT_CAR', 180000, 'up', 'to')])
        self.assertFalse(self.proposals)

    def test_examples_keep_explicit_sources_exclusions_and_only_supplement_shortfall(self):
        response = self.turn(reading(requests=[item('FOOD', 250000)], excluded=['SAVINGS']))
        comparison = response['reply']['comparison']
        combined = next(p for p in comparison['plans'] if p['id'] == 'spare')
        transfers = combined['result']['transfers']
        self.assertEqual(sum(t['amount'] for t in transfers if t['from'] in self.context['allocations']), 200000)
        self.assertEqual(sum(t['amount'] for t in transfers if t['from'] in self.context['buckets']), 50000)
        self.assertNotIn('SAVINGS', [t['from'] for t in transfers])
        self.assertFalse(self.proposals)
        self.setUp()
        response = self.turn(reading(requests=[item('FOOD', 50000)], sources=['SAVINGS']))
        self.assertEqual(response['reply']['proposal']['transfers'],
                         [{'from': 'SAVINGS', 'to': 'FOOD', 'amount': 50000, 'direct': False}])
        self.assertEqual(len(self.proposals), 1)

    def test_funding_question_applies_new_exclusions_before_recalculating(self):
        self.context['spending_status'] = 'complete'
        self.turn(reading(requests=[item('FOOD', 250000)]))
        response = self.turn(reading(action='inspect', excluded=['SAVINGS', 'SPARE'], reference='previous'))
        self.assertEqual(self.store.state['requests'], [item('FOOD', 250000)])
        self.assertEqual(set(self.store.state['excluded']), {'SAVINGS', 'SPARE'})
        plans = response['reply']['comparison']['plans']
        self.assertTrue(plans)
        for plan in plans:
            self.assertFalse({'SAVINGS', 'SPARE'} & {t['from'] for t in (plan.get('result') or {}).get('transfers', [])})
        self.assertFalse(self.proposals)

    def test_source_question_never_invents_unknown_or_excluded_funding(self):
        self.context['locks'] = dict(self.context['allocations'])
        self.context['bucket_available']['CUSHION_MONTHLY'] = 0
        self.context['bucket_status']['SPARE'] = 'unknown'
        response = self.turn(reading(requests=[item('SAVINGS', 50000)], excluded=['SPARE']))
        comparison = response['reply'].get('comparison') or {'plans': []}
        self.assertFalse(any(p['status'] in {'ready', 'partial'} for p in comparison['plans']))
        self.assertFalse(any(c['value'].startswith('plan_') for c in response['reply']['choices']))
        self.assertEqual(self.store.state['excluded'], ['SPARE'])
        self.assertFalse(self.proposals)

    def test_source_examples_keep_unknown_amounts_unknown_and_show_shortfall(self):
        self.context['bucket_status']['SAVINGS'] = 'unknown'
        self.context['bucket_available']['CUSHION_MONTHLY'] = 185000
        response = self.turn(reading(requests=[item('LIVING', 1000000)], destinations=['FOOD']))
        comparison = response['reply']['comparison']
        plans = [p for p in comparison['plans'] if p['status'] in {'ready', 'partial'}]
        self.assertTrue(plans)
        for plan in plans:
            self.assertEqual(plan['status'], 'partial')
            self.assertNotIn('SAVINGS', {t['from'] for t in plan['result']['transfers']})
        best = max(plans, key=lambda p: p['amount'])
        # 여윳돈(30% 남김) → 월 머니쿠션 순서로 부족분만 채운다. 확인 안 된 저축은 쓰지 않는다.
        self.assertEqual(best['result']['transfers'], [{'from': 'SPARE', 'to': 'FOOD', 'amount': 280000, 'direct': False},
                                                       {'from': 'CUSHION_MONTHLY', 'to': 'FOOD', 'amount': 140000, 'direct': False}])
        self.assertEqual(comparison['basis_amount'] - best['amount'], 580000)
        self.assertFalse(self.proposals)

    def test_source_question_preserves_spare_minimum_and_asks_before_using_spare(self):
        self.context['locks'] = dict(self.context['allocations'])
        self.turn(reading(requests=[item('SAVINGS', 50000)]))
        self.turn(reading(action='inspect'))
        response = self.turn(reading(selected_choice='keep_spare', reference='previous'))
        self.assertEqual(self.store.state['pending']['field'], 'spare_minimum', response)
        self.complete.side_effect = lambda system, payload: json.dumps({'value': 350000})
        response = dialogue.handle({'conversation_id': 'talk', 'text': '35만원은 남길래'}, **self.ports)
        self.assertEqual(self.store.state['spare_minimum'], 350000)
        self.assertEqual(self.store.state['pending']['kind'], 'comparison')
        plan = next(p for p in response['reply']['comparison']['plans'] if p['id'] == 'spare')
        self.assertEqual(plan['amount'], 50000)
        self.assertEqual(plan['result']['buckets']['SPARE'], 350000)
        self.assertFalse(self.store.state.get('sources'))
        self.assertFalse(self.proposals)
        selected = self.turn(reading(selected_choice='plan_spare', reference='previous'))
        self.assertEqual(selected['reply']['proposal']['buckets']['SPARE'], 350000)
        self.assertEqual(len(self.proposals), 1)

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
                # 가져올 수 있는 곳(여윳돈 등)을 먼저 알려 주고 받을 항목을 묻는다.
                self.assertIn('여윳돈', response['reply']['lines'][0])
                self.assertIn('어느 항목이 부족한가요', response['reply']['lines'][0])
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
                self.assertIn('어느 항목이 부족한가요', response['reply']['lines'][0])
                self.assertFalse(self.proposals)

    def test_total_living_followup_keeps_increase_and_asks_destination_before_examples(self):
        self.turn(reading(requests=[item('LIVING', None, 'up', None)]))
        self.turn(reading(selected_choice='LIVING', reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        response = self.turn(reading(requests=[item('LIVING', 50000, 'up')], reference='previous'))
        self.assertEqual(self.store.state['pending']['kind'], 'destinations')
        self.assertNotIn('줄인', json.dumps(response, ensure_ascii=False))
        self.assertEqual(self.store.state['requests'], [item('LIVING', 50000)])
        self.assertFalse(self.proposals)

    def test_category_answer_replaces_unspecified_living_target_and_keeps_direction(self):
        self.turn(reading(requests=[item('LIVING', None, 'up', None)]))
        with patch.object(dialogue, 'comparison_reply', return_value={'status': 'awaiting_input', 'reply': {}}) as compare:
            self.turn(reading(selected_choice='FOOD', reference='previous'))
        compare.assert_not_called()
        self.assertEqual(self.store.state['requests'], [item('FOOD', None, 'up', None)])
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertFalse(self.proposals)

    def test_old_destination_flow_resolves_the_amount_question_to_selected_category(self):
        self.store.state = {'requests': [item('LIVING', None, 'up', None)],
            'pending': {'kind': 'destinations', 'index': 0, 'line': '어느 생활비 항목에 배분할까요?',
                        'choices': [('TRANSPORT_CAR', '교통·차량')]}}
        response = self.turn(reading(selected_choice='TRANSPORT_CAR', reference='previous'))
        self.assertEqual(self.store.state['requests'], [item('TRANSPORT_CAR', None, 'up', None)])
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertNotIn('comparison', response['reply'])
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

    def test_old_spare_card_keeps_the_default_reserve_without_another_question(self):
        for category, automatic in [('HOUSEHOLD', True), ('SAVINGS', False)]:
            with self.subTest(category=category):
                self.proposals.clear()
                request = envelope({**item(category, 95000), 'automatic': automatic}, sources=['SPARE'])
                self.store.state = {'requests': request['items'], 'comparison': {'context': deepcopy(self.context),
                    'comparison': {'plans': [{'id': 'spare', 'status': 'ready', 'envelope': request}]}},
                    'pending': {'kind': 'comparison', 'choices': [('plan_spare', '여윳돈 활용')]}}
                result = dialogue.handle({'conversation_id': 'talk', 'text': 'plan_spare'}, **self.ports)
                self.assertIn('proposal', result['reply'], result)
                # 따로 정하지 않아도 여윳돈은 30% 이상 남긴다.
                self.assertGreaterEqual(result['reply']['proposal']['buckets']['SPARE'], 120000)
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
                self.store.state['sources'] = []
                self.context['spending_status'] = 'complete'
                response = self.turn(reading(action=action))
                self.assertIn('comparison', response['reply'])
                self.assertTrue(any((p.get('result') or {}).get('changes') for p in response['reply']['comparison']['plans']))
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

    def test_chosen_source_without_money_explains_and_offers_direct_choice(self):
        for limit, ready in [(0, False), (400000, True)]:
            with self.subTest(limit=limit):
                state = {'requests': [item(amount=50000)], 'sources': ['SPARE']}
                self.context['bucket_available']['SPARE'] = limit
                response = dialogue.comparison_reply(self.store, 'talk', state, self.context, self.context, self.complete)
                comparison = response['reply'].get('comparison') or {'status': 'unavailable'}
                self.assertEqual(comparison['status'] == 'ready', ready)
                values = [row['value'] for row in response['reply']['choices']]
                self.assertIn('manual_living', values)
                if ready:
                    self.assertEqual({t['from'] for t in comparison['plans'][0]['result']['transfers']}, {'SPARE'})
                else:
                    self.assertIn('자동으로 추천할 방법이 없어요', response['reply']['lines'][0])
                self.assertFalse(self.proposals)

    def test_calculator_failure_does_not_ask_user_for_another_category(self):
        state = {'requests': [item(amount=50000)], 'sources': ['SPARE']}
        with patch('budgets.adjustment_core.advisor.plan_increase', side_effect=ValueError('invalid_result')):
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

    def test_preview_capacity_feedback_uses_strict_schema_and_preserves_inquiry_focus(self):
        ctx = context()
        state = {'requests': [item('FOOD', 50000)], 'sources': ['HOUSEHOLD']}
        for text, focus in [('얼마나 늘릴 수 있어?', 'capacity'), ('어디서 얼마나 가져올 수 있어?', 'sources')]:
            with self.subTest(text=text):
                payload = {'text': text, 'codes': [*ctx['allocations'], *ctx['buckets']],
                           'category_names': ctx['category_names'],
                           'primary_request': envelope(item('FOOD', 50000), sources=['HOUSEHOLD']),
                           'previous': state}
                data = json.loads(self.live(dialogue.FEEDBACK_PROMPT, json.dumps(payload, ensure_ascii=False)))
                self.assertEqual(data['action'], 'maxima', data)
                self.assertEqual(data['inspection'], focus, data)
        self.assertEqual(self.live.usage()['failed'], 0)

    def test_capacity_question_keeps_food_and_followup_source(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                def interpret(text, state=None):
                    if direct:
                        return interpretation.read(text, self.live, categories=context()['category_names'],
                            **interpretation.dialogue_context(state or {}))
                    return self.interpret(text, state)
                data = interpret('식비 어디까지 늘릴 수 있어?')
                self.assertEqual(data['requests'], [item('FOOD', None, 'up', data['requests'][0]['amount_mode'])], data)
                self.assertEqual(data['action'], 'inspect', data)
                self.assertEqual(data['inspection'], 'capacity', data)
                state = {'requests': [item('FOOD', None)], 'sources': ['HOUSEHOLD'],
                         'pending': {'kind': 'amount', 'question': '식비 예산을 얼마 늘릴까요?'}}
                for text, focus in [('얼마나 늘릴 수 있어?', 'capacity'),
                                    ('어디서 얼마나 가져올 수 있는데?', 'sources')]:
                    data = interpret(text, state)
                    _merge(state, data, state['pending'])
                    self.assertEqual(state['requests'][0]['category'], 'FOOD', data)
                    self.assertEqual(state['sources'], ['HOUSEHOLD'], data)
                    self.assertEqual(data['action'], 'inspect', data)
                    self.assertEqual(data['inspection'], focus, data)
                    self.assertNotIn('inspection', state)

    def test_transfer_direction_and_unspecified_reduction_amount(self):
        for text in ['식비 5만 원 옮겨줘', '교통비 3만원 이동해줘']:
            with self.subTest(text=text):
                data = self.interpret(text)
                self.assertEqual(len(data['requests']), 1, data)
                self.assertIsNone(data['requests'][0]['direction'], data)
                self.assertIsNone(data['action'], data)
        previous = {'requests': [item('FOOD', 50000)],
                    'pending': {'kind': 'sources', 'question': '식비로 옮길 돈은 어디서 가져올까요?'}}
        data = self.interpret('아니 식비에서', previous)
        state = deepcopy(previous)
        _merge(state, data, previous['pending'])
        self.assertEqual(state['requests'], [item('FOOD', 50000, 'down')], data)
        for text in ['패션줄여서 교육에다 분배', '패션 돈 줄여서 교육에다가 분배하고 싶다니까']:
            with self.subTest(text=text):
                state = {'requests': [item('FASHION_SHOPPING', None, 'down', None)],
                         'pending': {'kind': 'request', 'question': '어느 항목에서 얼마를 줄여 나눌까요?'}}
                data = self.interpret(text, state)
                _merge(state, data, state['pending'])
                self.assertEqual(state['requests'][0]['category'], 'FASHION_SHOPPING', data)
                self.assertEqual(state['requests'][0]['direction'], 'down', data)
                self.assertIsNone(state['requests'][0]['amount'], data)
                self.assertEqual(state['destinations'], ['EDUCATION'], data)
                self.assertNotEqual(data['action'], 'distribute', data)

    def test_live_reduction_destination_change_rebuilds_preview(self):
        store, ctx = Store(), context()
        request = envelope(item('FOOD', 100000, 'down'), destinations=['CULTURE_LEISURE'], excluded=['SAVINGS'])
        preview = simulate_funding(ctx, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        store.state = {'requests': request['items'], 'destinations': ['CULTURE_LEISURE'], 'excluded': ['SAVINGS'],
                       'proposal': deepcopy(preview), 'work_ref': 'old'}
        tasks = {'old': {'state': 'awaiting_approval'}}
        ports = dict(store=store, complete=self.live, get_context=lambda: deepcopy(ctx),
            simulate=lambda current, req: simulate_funding(current, req, allocate_group_reduction=allocation),
            propose=lambda current, result, **settings: {**result, **settings, 'task_ref': 'new', 'proposal_id': 'new'},
            cancel=lambda ref: tasks[ref].update(state='canceled'),
            task_status=lambda ref: deepcopy(tasks[ref]), allocator=allocation)
        before = deepcopy(ctx)
        result = dialogue.handle({'conversation_id': 'synthetic', 'text': '문화여가 말고 병원비에 옮겨줘'}, **ports)
        self.assertNotIn('error', result)
        self.assertIn('proposal', result['reply'], result)
        after = result['reply']['proposal']['allocations']
        self.assertEqual({k: after[k] for k in ['FOOD', 'CULTURE_LEISURE', 'MEDICAL_HEALTH']},
                         {'FOOD': 300000, 'CULTURE_LEISURE': 50000, 'MEDICAL_HEALTH': 150000})
        self.assertEqual(store.state['requests'], request['items'])
        self.assertEqual(store.state['excluded'], ['SAVINGS'])
        self.assertEqual(ctx, before)

    def test_live_transfer_without_amount_keeps_route_through_amount_answer(self):
        fixture = AdjustmentDialogueTest()
        fixture.setUp()
        fixture.ports['complete'] = self.live
        before = deepcopy(fixture.context)
        result = dialogue.handle({'conversation_id': 'synthetic', 'text': '패션 돈 줄여서 교육에다가 분배하고 싶어'},
                                 **fixture.ports)
        state = fixture.store.state
        self.assertEqual(state['requests'][0]['category'], 'FASHION_SHOPPING', result)
        self.assertEqual(state['requests'][0]['direction'], 'down', result)
        self.assertEqual(state['destinations'], ['EDUCATION'], result)
        self.assertNotIn('어느 항목에서', ' '.join(result['reply']['lines']))
        self.assertNotIn('요청한 금액보다 적어요', ' '.join(result['reply']['lines']))
        result = dialogue.handle({'conversation_id': 'synthetic', 'text': '그럼 3만원만 옮길게'}, **fixture.ports)
        if result['reply'].get('comparison'):
            plan = next(p for p in result['reply']['comparison']['plans'] if p['status'] == 'ready')
            result = dialogue.handle({'conversation_id': 'synthetic', 'text': 'plan_' + plan['id']}, **fixture.ports)
        self.assertIn('proposal', result['reply'], result)
        after = result['reply']['proposal']['allocations']
        self.assertEqual(after['FASHION_SHOPPING'], 170000)
        self.assertEqual(after['EDUCATION'], 80000)
        self.assertEqual(fixture.context, before)

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
            comparison = result['reply'].get('comparison') or fixture.store.state.get('comparison', {}).get('comparison')
            self.assertIsNotNone(comparison, result)
            plans = [p for p in comparison['plans'] if p['status'] in {'ready', 'partial'}]
            self.assertTrue(plans)
            self.assertEqual(max(p['amount'] for p in plans), 300000)
            for plan in plans:
                self.assertEqual(plan['result']['allocations'], fixture.context['allocations'])
                for code, before in fixture.context['buckets'].items():
                    self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)
            self.assertEqual(comparison['basis_amount'], 300000)
            self.assertTrue(all(t['from'] != 'SAVINGS' for p in plans for t in p['result']['transfers']))
            self.assertEqual(fixture.store.state['requests'], [item('SAVINGS', 300000)])
        result = dialogue.handle({'conversation_id': 'talk', 'text': '다음달부터 교육비에 50만원 더 나감'}, **fixture.ports)
        self.assertEqual(fixture.store.state['requests'], [item('EDUCATION', 500000)])
        self.assertIn('교육', guidance(result, fixture.store.state)['context'])
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
                    self.assertNotIn('error', result, (result, self.live.usage(), self.live.client.attempts[-1:]))
                    proposal = result['reply'].get('proposal')
                    self.assertIsNotNone(proposal, result)
                    self.assertEqual({key: proposal['allocations'][key] for key in expected}, expected)
                    self.assertEqual(sum(row['state'] == 'awaiting_approval' for row in tasks.values()), 1)
                    self.assertEqual(ctx, before)
            result = dialogue.handle({'conversation_id': 'synthetic', 'text': '이번 조정은 전부 그만둘게'}, **ports)
            self.assertEqual(result['status'], 'ended', (result, self.live.usage(), self.live.client.attempts[-1:]))
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


class SalaryChangeDialogueTest(unittest.TestCase):
    def setUp(self):
        self.store, self.context = Store(), context()
        self.context.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        self.proposals = []
        self.ports = dict(store=self.store, complete=Mock(), get_context=lambda: deepcopy(self.context),
            simulate=lambda ctx, request: simulate_funding(ctx, request, allocate_group_reduction=allocation),
            propose=self.propose, task_status=Mock(return_value={'state': 'expired'}), cancel=Mock(), allocator=allocation)

    def propose(self, ctx, result, **settings):
        self.proposals.append(result)
        return {**result, **settings, 'proposal_id': 'plan', 'task_ref': 'task'}

    def say(self, text, parsed=None):
        if parsed is not None:
            self.ports['complete'].side_effect = lambda system, payload: json.dumps(parsed, ensure_ascii=False)
        return dialogue.handle({'conversation_id': 'talk', 'text': text}, **self.ports)

    def handoff(self, **changes):
        return self.say('월급 수정할래', reading(action='salary_change', **changes))

    def test_salary_without_amount_goes_to_mypage_without_asking_again(self):
        response = self.handoff()
        self.assertEqual(self.store.state['pending']['kind'], 'salary_handoff')
        self.assertIn('마이페이지', response['reply']['lines'][0])
        self.assertEqual([c['value'] for c in response['reply']['choices']], ['salary_resume'])
        self.assertNotIn('planning', self.store.state)
        self.assertFalse(self.proposals)

    def test_known_salary_amount_is_not_applied_or_used_for_calculation(self):
        before = deepcopy(self.context)
        self.say('월급 50만원 올랐어', reading(income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'}))
        self.assertEqual(self.store.state['pending']['kind'], 'salary_handoff')
        self.assertNotIn('salary', self.store.state['pending'])
        self.assertNotIn('income_change', self.store.state.get('planning', {}))
        self.assertEqual(self.context, before)
        self.assertFalse(self.proposals)

    def test_future_salary_does_not_replace_existing_expense_period_or_source(self):
        self.store.state = {'requests': [item('EDUCATION', 500000)], 'scope': 'next_cycle',
            'planning': {'scope': 'next_cycle'}, 'sources': ['SAVINGS'], 'excluded': ['SPARE'],
            'clear_targets': ['EDUCATION']}
        response = self.say('다음달 월급 50만원 오르니까 그걸 써', reading(
            income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'},
            scope='next_cycle', reference='previous'))
        state = self.store.state
        self.assertEqual(response['reply']['lines'][0],
            '월급 변경은 실제 변경 시점에 마이페이지에서 하실 수 있어요. 진행 중인 예산 조정은 그대로 유지해 둘게요.')
        self.assertEqual(state['requests'], [item('EDUCATION', 500000)])
        self.assertEqual(state['planning'], {'scope': 'next_cycle'})
        self.assertEqual(state['sources'], ['SAVINGS'])
        self.assertEqual(state['excluded'], ['SPARE'])
        self.assertEqual(state['clear_targets'], ['EDUCATION'])
        self.assertFalse(self.proposals)

    def test_salary_and_expense_in_one_turn_keeps_the_expense(self):
        self.handoff(income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'},
            requests=[item('EDUCATION', 100000)], scope='next_cycle', excluded=['SAVINGS'])
        self.assertEqual(self.store.state['requests'], [item('EDUCATION', 100000)])
        self.assertEqual(self.store.state['planning'], {'scope': 'next_cycle'})
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertNotIn('income_change', self.store.state['planning'])

    def test_canceling_salary_edit_restores_previous_question_without_llm(self):
        pending = {'kind': 'amount', 'line': '식비를 얼마나 늘릴까요?', 'choices': [], 'index': 0}
        self.store.state = {'requests': [item('FOOD')], 'pending': deepcopy(pending)}
        self.handoff()
        self.ports['complete'].reset_mock(side_effect=True)
        response = self.say('salary_resume')
        self.assertEqual(self.store.state['pending'], pending)
        self.assertEqual(response['reply']['lines'], [pending['line']])
        self.ports['complete'].assert_not_called()

    def test_preview_survives_salary_handoff_and_no_change_return(self):
        request = envelope(item('FOOD', 50000), sources=['SPARE'])
        preview = simulate_funding(self.context, request)
        preview.update(task_ref='old', proposal_id='old', state='awaiting_approval')
        self.store.state = {'requests': request['items'], 'sources': ['SPARE'], 'proposal': deepcopy(preview), 'work_ref': 'old'}
        self.ports['task_status'].return_value = {'state': 'awaiting_approval'}
        self.ports['complete'].side_effect = [json.dumps({'action': 'revise', 'category': None, 'amount': None,
            'amount_mode': None, 'suggested_target': None}), json.dumps(reading(action='salary_change'))]
        response = self.say('월급을 수정할래')
        self.assertEqual(self.store.state['requests'], request['items'])
        self.assertEqual(self.store.state['proposal'], preview)
        self.assertEqual(self.store.state['work_ref'], 'old')
        self.assertNotIn('proposal', response['reply'])
        response = self.say('salary_resume')
        self.assertEqual(response['reply']['proposal'], preview)
        self.ports['cancel'].assert_not_called()
        self.assertFalse(self.proposals)

    def test_fulfilled_target_in_new_budget_is_not_added_again(self):
        self.store.state = {'requests': [item('FOOD', 50000)], 'sources': ['SPARE']}
        self.handoff()
        self.store.state['work_ref'] = 'old'
        self.context['base_budget_id'] = 'new-budget'
        self.context['allocations']['FOOD'] += 50000
        self.context['buckets']['SPARE'] -= 50000
        response = self.say('salary_resume')
        self.assertIn('이미 반영', response['reply']['lines'][0])
        self.assertEqual(self.store.state['requests'], [])
        self.assertNotIn('work_ref', self.store.state)
        self.assertFalse(self.proposals)

    def test_partial_target_in_new_budget_only_calculates_remaining_amount(self):
        self.store.state = {'requests': [item('FOOD', 50000)], 'sources': ['SPARE'], 'excluded': ['SAVINGS'],
            'clear_targets': ['FOOD'], 'scope': 'cycle'}
        self.handoff()
        self.context['base_budget_id'] = 'new-budget'
        self.context['allocations']['FOOD'] += 30000
        self.context['buckets']['SPARE'] -= 30000
        response = self.say('salary_resume')
        self.assertEqual(self.store.state['requests'], [item('FOOD', 450000, 'up', 'to')])
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertEqual(self.store.state['clear_targets'], ['FOOD'])
        self.assertEqual(self.store.state['scope'], 'cycle')
        proposal = response['reply']['proposal']
        self.assertEqual(proposal['allocations']['FOOD'], 450000)
        self.assertEqual(sum(row['amount'] for row in proposal['transfers']), 20000)

    def test_changed_lock_revision_requires_new_consent(self):
        from .adjustment_core.agent import _lock_snapshot
        self.context['locks'] = {'EDUCATION': 50000}
        self.context['lock_revisions'] = {'EDUCATION': 'old-revision'}
        self.store.state = {'requests': [item('EDUCATION', 10000, 'down')], 'destinations': ['FOOD'],
            'unlock_categories': ['EDUCATION'], 'unlock_consents': _lock_snapshot(self.context, ['EDUCATION'])}
        self.handoff()
        self.context['lock_revisions']['EDUCATION'] = 'new-revision'
        self.say('salary_resume')
        self.assertEqual(self.store.state['unlock_categories'], [])
        self.assertEqual(self.store.state['pending']['kind'], 'unlock')
        self.assertFalse(self.proposals)

    def test_legacy_salary_plan_button_only_hands_off(self):
        self.store.state = {'pending': {'kind': 'salary_change', 'choices': [('salary_plan', '늘어난 돈 미리 나누기')],
            'salary': {'before': 3000000, 'after': 3500000}}}
        self.say('salary_plan')
        self.assertEqual(self.store.state['pending']['kind'], 'salary_handoff')
        self.assertNotIn('planning', self.store.state)
        self.assertFalse(self.proposals)

    def test_salary_handoff_works_before_a_budget_is_available(self):
        self.ports['get_context'] = Mock(side_effect=ValueError('no_budget'))
        response = self.handoff()
        self.assertEqual(self.store.state['pending']['kind'], 'salary_handoff')
        self.assertIn('마이페이지', response['reply']['lines'][0])
        response = self.say('salary_resume')
        self.assertEqual(response['status'], 'awaiting_input')
        self.assertEqual(response['reply']['choices'][0]['value'], 'salary_resume')
        self.assertIn('salary_handoff', self.store.state)

    def test_target_release_is_preserved_when_amount_is_already_applied(self):
        self.context['category_targets'] = {'FOOD': 400000}
        self.store.state = {'requests': [item('FOOD', 50000)], 'clear_targets': ['FOOD'], 'scope': 'cycle'}
        self.handoff()
        self.context['allocations']['FOOD'] += 50000
        self.context['buckets']['SPARE'] -= 50000
        response = self.say('salary_resume')
        self.assertEqual(response['reply']['proposal']['target_changes'], {'FOOD': None})
        self.assertEqual(response['reply']['proposal']['allocations']['FOOD'], 450000)

    def test_new_expense_while_handoff_pending_updates_the_return_target(self):
        self.store.state = {'requests': [item('FOOD', 50000)], 'sources': ['SPARE'], 'scope': 'cycle',
                            'primary_request': {'items': [item('FOOD', 50000)]}}
        self.handoff()
        self.handoff(requests=[item('FOOD', 70000)], reference='previous')
        response = self.say('salary_resume')
        self.assertEqual(response['reply']['proposal']['allocations']['FOOD'], 470000)

    def test_one_off_return_keeps_absolute_target_after_partial_reallocation(self):
        self.store.state = {'requests': [item('FOOD', 100000)], 'sources': ['SPARE'], 'scope': 'next_cycle',
            'planning': {'scope': 'next_cycle', 'one_off': True}, 'one_off': True,
            'expense_request': item('FOOD', 100000), 'set_aside_amount': 0}
        self.handoff()
        self.context['allocations']['FOOD'] += 30000
        self.context['buckets']['SPARE'] -= 30000
        response = self.say('salary_resume')
        proposal = response['reply']['proposal']
        self.assertEqual(proposal['allocations']['FOOD'], 500000)
        self.assertEqual(sum(row['amount'] for row in proposal['transfers']), 70000)
        self.assertEqual(self.store.state['scope'], 'next_cycle')
        self.assertTrue(self.store.state['planning']['one_off'])

    def test_salary_followup_keeps_explicit_sources_and_exclusions(self):
        self.store.state = {'requests': [item('FOOD', 50000)], 'scope': 'cycle'}
        self.handoff(scope='next_cycle', sources=['SPARE'], excluded=['SAVINGS', 'CUSHION_MONTHLY'])
        self.assertEqual(self.store.state['sources'], ['SPARE'])
        self.assertEqual(self.store.state['excluded'], ['CUSHION_MONTHLY', 'SAVINGS'])
        self.assertEqual(self.store.state['scope'], 'cycle')
        response = self.say('salary_resume')
        self.assertEqual({row['from'] for row in response['reply']['proposal']['transfers']}, {'SPARE'})

    def test_explicit_donor_amount_survives_return_but_automatic_donor_does_not(self):
        self.store.state = {'requests': [item('FOOD', 50000), item('SAVINGS', 50000, 'down')],
            'primary_request': {'items': [item('FOOD', 50000)]}, 'sources': ['SAVINGS'], 'scope': 'cycle',
            'refinement': {'donors': {'SAVINGS': {'target': 550000, 'explicit': True},
                                      'CULTURE_LEISURE': {'target': 40000, 'explicit': False}}},
            'spare_minimum': 250000}
        self.handoff()
        self.context['monthly_income'] += 500000
        response = self.say('salary_resume')
        card = response['reply']['proposal']
        self.assertEqual(card['allocations']['FOOD'], 450000)
        self.assertEqual(card['buckets']['SAVINGS'], 550000)
        self.assertEqual(card['allocations']['CULTURE_LEISURE'], self.context['allocations']['CULTURE_LEISURE'])
        self.assertEqual(self.store.state['spare_minimum'], 250000)
        self.assertEqual({row['category'] for row in self.store.state['requests']}, {'FOOD', 'SAVINGS'})

    def test_confirmed_planned_spare_is_kept_only_within_the_same_period(self):
        for changed_cycle in (False, True):
            with self.subTest(changed_cycle=changed_cycle):
                self.setUp()
                planned = {'status': 'user_confirmed', 'scope': 'unbudgeted_only', 'amount': 200000}
                self.store.state = {'requests': [item('FOOD', 250000)], 'sources': ['SPARE'], 'scope': 'cycle',
                                    'financial_overrides': {'planned_spare': deepcopy(planned)}}
                self.handoff()
                self.context['monthly_income'] += 500000
                if changed_cycle:
                    self.context['cycle_id'] = 'next-cycle'
                response = self.say('salary_resume')
                if changed_cycle:
                    self.assertEqual(self.store.state['pending']['field'], 'planned')
                    self.assertNotIn('financial_overrides', self.store.state)
                    self.assertFalse(self.proposals)
                else:
                    self.assertEqual(self.store.state['financial_overrides']['planned_spare'], planned)
                    results = [plan['result'] for plan in response['reply']['comparison']['plans'] if plan.get('result')]
                    self.assertTrue(results)
                    self.assertTrue(all(result['buckets']['SPARE'] >= 200000 for result in results))

    def test_completed_donor_cut_is_not_reused_for_the_remaining_increase(self):
        for sources in (['SAVINGS'], ['SAVINGS', 'SPARE']):
            with self.subTest(sources=sources):
                self.setUp()
                calculated = []
                def simulate(ctx, request):
                    result = simulate_funding(ctx, request, allocate_group_reduction=allocation)
                    calculated.append(result)
                    return result
                self.ports['simulate'] = simulate
                self.store.state = {'requests': [item('FOOD', 100000), item('SAVINGS', 50000, 'down')],
                    'primary_request': {'items': [item('FOOD', 100000)]}, 'sources': sources, 'scope': 'cycle',
                    'refinement': {'donors': {'SAVINGS': {'target': 550000, 'explicit': True}}}}
                self.handoff()
                self.context['allocations']['FOOD'] = 450000
                self.context['buckets']['SAVINGS'] = 550000
                self.say('salary_resume')
                self.assertTrue(calculated)
                result = calculated[-1]
                self.assertEqual(result['buckets']['SAVINGS'], 550000)
                self.assertNotIn('SAVINGS', {row['from'] for row in result['transfers']})
                if 'SPARE' in sources:
                    self.assertEqual(result['allocations']['FOOD'], 500000)
                    self.assertEqual(result['buckets']['SPARE'], 350000)
                else:
                    self.assertEqual(result['allocations']['FOOD'], 450000)
                    self.assertEqual(result['shortfalls'][0]['missing_amount'], 50000)
                    self.assertFalse(self.proposals)


class DirectDistributionContinuationTest(unittest.TestCase):
    setUp = AdjustmentDialogueTest.setUp
    propose = AdjustmentDialogueTest.propose
    turn = AdjustmentDialogueTest.turn

    def test_known_transfer_preserves_both_categories_until_amount_answer(self):
        for destination_as_request in (False, True):
            for action in (None, 'distribute'):
                with self.subTest(destination_as_request=destination_as_request, action=action):
                    self.setUp()
                    donor = item('FASHION_SHOPPING', None, 'down', None)
                    self.store.state = {'clear_targets': ['FASHION_SHOPPING'], 'spare_minimum': 200000}
                    parsed = reading(requests=[donor, item('EDUCATION', None)] if destination_as_request else [donor],
                        destinations=[] if destination_as_request else ['EDUCATION'],
                        action='distribute', excluded=['SAVINGS'], scope='cycle')
                    for _ in range(2):
                        response = self.turn(parsed)
                        self.assertEqual(self.store.state['pending']['kind'], 'amount', response)
                        self.assertEqual(self.store.state['requests'], [donor])
                        self.assertEqual(self.store.state['destinations'], ['EDUCATION'])
                        self.assertEqual(response['reply']['lines'], ['패션·쇼핑에서 얼마를 줄여 교육에 옮길까요?'])
                        self.assertFalse(self.proposals)
                    response = self.turn(reading(requests=[item('FASHION_SHOPPING', 50000, 'down')],
                                                 action=action, reference='previous'))
                    proposal = response['reply']['proposal']
                    self.assertEqual(proposal['allocations']['FASHION_SHOPPING'], 150000)
                    self.assertEqual(proposal['allocations']['EDUCATION'], 100000)
                    self.assertEqual(proposal['buckets'], self.context['buckets'])
                    self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
                    self.assertEqual(self.store.state['clear_targets'], ['FASHION_SHOPPING'])
                    self.assertEqual(self.store.state['spare_minimum'], 200000)

    def test_unknown_transfer_amount_keeps_zero_budget_reason(self):
        from .adjustment_core.conversation import distribute
        self.store.state = {}
        self.context['allocations']['FASHION_SHOPPING'] = 0
        response = distribute({'conversation_id': 'talk'}, self.store.state, reading(
            requests=[item('FASHION_SHOPPING', None, 'down'), item('EDUCATION', None)], action='distribute'), **self.ports)
        self.assertEqual(self.store.state['pending']['kind'], 'category')
        self.assertIn('패션·쇼핑의 현재 예산은 0원', response['reply']['lines'][0])
        self.assertEqual(self.store.state['destinations'], ['EDUCATION'])
        self.assertFalse(self.proposals)

    def test_known_destination_cannot_bypass_verified_spending(self):
        from .adjustment_core.conversation import distribute
        self.context['spent']['FASHION_SHOPPING'] = 190000
        self.store.state = {'requests': [item('FASHION_SHOPPING', None, 'down')],
                            'destinations': ['EDUCATION'], 'pending': {'kind': 'amount', 'index': 0}}
        response = distribute({'conversation_id': 'talk'}, self.store.state, reading(
            requests=[item('FASHION_SHOPPING', 50000, 'down')], action='distribute'), **self.ports)
        self.assertEqual(self.store.state['pending']['kind'], 'distribution', response)
        self.assertIn('이미 쓴 돈', response['reply']['lines'][0])
        self.assertFalse(self.proposals)

    def test_generic_distribution_after_preview_reopens_recipient_picker(self):
        from .adjustment_core.conversation import distribute
        donor = item('EDUCATION', 50000, 'down')
        result = simulate_funding(self.context, envelope(donor, destinations=['FOOD']),
                                  allocate_group_reduction=allocation)
        old = {**result, 'proposal_id': 'old-preview', 'task_ref': 'old-task'}
        self.store.state = {'requests': [donor], 'destinations': ['FOOD'], 'proposal': old}
        response = distribute({'conversation_id': 'talk'}, self.store.state,
                              reading(action='distribute'), **self.ports)
        self.assertIn('distribution_input', response['reply'])
        self.assertEqual(self.store.state['pending']['kind'], 'distribution')
        self.assertEqual(self.store.state['proposal'], old)
        self.assertFalse(self.proposals)

"""Synthetic-only financial diagnosis, constrained LLM proposals and approval boundaries."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from budgets.adjustment_core.financial_planner import diagnose, simulate_plan, recommend, synthetic_diagnostics, recommendation_context
from budgets.test_adjustment_core import context, envelope, item


def financial_context():
    inp = context()
    inp.update(today="2026-11-10", spending_status="complete", data_origin="synthetic",
               system_floors={"TRANSPORT_CAR": 60000})
    inp["diagnostic_inputs"] = synthetic_diagnostics(inp["allocations"])
    return inp


def funding(**amounts):
    return [{"source": c, "amount": n} for c, n in amounts.items()]


def draft(*plans):
    return {"plans": [{"funding": p} for p in plans], "recommendation_index": None, "reason_fact_ids": []}


def review(index=0, ids=None):
    return {"plans": [], "recommendation_index": index,
            "reason_fact_ids": ids if ids is not None else ["reserve", f"plan_{index}.reserve_after"]}


class FinancialPlannerTest(unittest.TestCase):
    def setUp(self):
        self.inp = financial_context()
        self.inp['priorities'].pop('FASHION_SHOPPING')
        self.request = envelope(item(amount=200000))
        self.diag = diagnose(self.inp, self.request)

    def test_upper_budgets_keep_thirty_percent_through_replay(self):
        from budgets.adjustment_core.simulation import simulate
        inp = deepcopy(self.inp)
        inp['diagnostic_inputs'] = {}
        inp['bucket_floors'] = {}
        inp['bucket_available'] = dict(inp['buckets'])
        request = envelope(item(amount=900000), sources=['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'])
        diag = diagnose(inp, request)
        self.assertEqual(diag['capacities'], {'SAVINGS': 420000, 'CUSHION_MONTHLY': 140000, 'SPARE': 280000})
        plan = simulate_plan(inp, diag, funding(**diag['capacities']))
        self.assertEqual(plan['result']['buckets'], {'SAVINGS': 180000, 'CUSHION_MONTHLY': 60000, 'SPARE': 120000})
        self.assertEqual(plan['result']['status'], 'partial')
        replay = deepcopy(plan['result']['requests'])
        replay['analysis'] = {'request': request, 'overrides': {}}
        self.assertEqual(simulate(inp, replay)['buckets'], plan['result']['buckets'])

    def test_thirty_percent_rounds_up_and_preserves_higher_protection(self):
        self.inp['diagnostic_inputs'] = {}
        self.inp['buckets']['SPARE'] = 98176
        protected = recommendation_context(self.inp, {})
        self.assertEqual(protected['bucket_floors']['SPARE'], 29453)
        self.assertEqual(protected['bucket_floors']['SAVINGS'], 300000)
        self.assertNotIn('FASHION_SHOPPING', protected['minimums'])
        self.assertEqual(protected['priorities'], self.inp['priorities'])
        protected = recommendation_context(self.inp, {'spare_minimum': 50000})
        self.assertEqual(protected['bucket_floors']['SPARE'], 50000)

    def test_forecast_uses_remaining_period_and_observed_spending(self):
        self.assertEqual(self.diag["reserve_before"], 330000)
        self.assertEqual(self.diag["forecast"]["FOOD"], 400000)
        inferred = diagnose(self.inp, envelope(item(amount=None, mode=None)))
        self.assertEqual(inferred["requested_amount"], 120000)
        self.assertNotEqual(inferred["requested_amount"], inferred["capacities"]["LIVING"])

    def test_sparse_history_uses_verified_budget_for_llm_combined_examples(self):
        cases = [("remaining_days", None, "period"), ("planned_spare", {}, "planned"), ("history", {}, "history"), ("as_of", "yesterday", "stale")]
        for key, value, expected in cases:
            with self.subTest(field=key):
                inp = deepcopy(self.inp); inp["diagnostic_inputs"][key] = value
                def complete(system, payload):
                    payload = json.loads(payload)
                    return draft(payload['balanced_funding']) if payload['phase'] == 'draft' else review()
                answer = recommend(inp, self.request, complete)
                self.assertEqual(answer['diagnosis']['basis'], 'budget')
                self.assertEqual(answer['recommended_index'], 0, answer)
                self.assertEqual(answer['plans'][0]['funding'], funding(LIVING=answer['diagnosis']['planning_amount']))
                self.assertNotIn('forecast', answer['diagnosis'])

    def test_unknown_spare_does_not_become_zero_available(self):
        self.inp["bucket_status"]["SPARE"] = "unknown"
        diag = diagnose(self.inp, self.request)
        self.assertEqual(diag['capacities']['SPARE'], 0)
        self.assertGreater(diag['capacities']['SAVINGS'], 0)

    def test_history_with_missing_categories_or_fixed_cost_scope_is_not_used(self):
        for change in ("missing", "fixed"):
            inp = deepcopy(self.inp)
            if change == "missing": inp["diagnostic_inputs"]["history"]["samples"][0].pop("FOOD")
            else: inp["diagnostic_inputs"]["history"]["scope"] = "all_transactions"
            diag = diagnose(inp, self.request)
            self.assertEqual(diag['basis'], 'budget')
            self.assertNotIn('forecast', diag)

    def test_combined_fifty_man_keeps_money_in_all_four_sources_and_replays(self):
        from budgets.adjustment_core.simulation import simulate
        inp = deepcopy(self.inp)
        inp['diagnostic_inputs'] = {}
        inp['buckets'].update(SAVINGS=300000, CUSHION_MONTHLY=400000, SPARE=500000)
        inp['bucket_floors'] = {}
        inp['bucket_available'] = dict(inp['buckets'])
        request = envelope(item(amount=500000))
        seen = []
        def complete(system, payload):
            payload = json.loads(payload)
            seen.append(payload)
            return draft(payload['balanced_funding']) if payload['phase'] == 'draft' else review()
        answer = recommend(inp, request, complete)
        plan = answer['plans'][0]
        self.assertEqual(plan['result']['status'], 'proposed')
        self.assertEqual({row['source'] for row in plan['funding']}, {'LIVING', 'SPARE'})
        self.assertEqual(sum(row['amount'] for row in plan['funding']), 500000)
        for code, before in inp['buckets'].items():
            self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)
        for code in inp['priorities']:
            if code != 'FOOD':
                self.assertEqual(plan['result']['allocations'][code], inp['allocations'][code])
        replay = deepcopy(plan['result']['requests'])
        replay['analysis'] = {'request': request, 'overrides': {}}
        result = simulate(inp, replay)
        self.assertEqual(result['transfers'], plan['result']['transfers'])
        self.assertEqual(sum(result['allocations'].values()) + sum(result['buckets'].values()) + inp['fixed_amount'], inp['funding_amount'])

    def test_savings_increase_can_use_other_sources_without_history(self):
        inp = deepcopy(self.inp)
        inp['diagnostic_inputs'] = {}
        diag = diagnose(inp, envelope(item('SAVINGS', 300000)))
        self.assertNotIn('SAVINGS', diag['allowed'])
        plan = simulate_plan(inp, diag, funding(SPARE=150000, CUSHION_MONTHLY=100000, LIVING=50000))
        self.assertEqual(plan['result']['buckets']['SAVINGS'], 900000)

    def test_selected_source_shortfall_does_not_authorize_other_sources(self):
        answer = recommend(self.inp, envelope(item(amount=200000), sources=['SPARE']), Mock(side_effect=RuntimeError('unavailable')))
        plan = answer['plans'][0]
        self.assertEqual(plan['result']['status'], 'partial')
        self.assertEqual(plan['funding'], funding(SPARE=70000))
        self.assertEqual(plan['result']['shortfalls'][0]['missing_amount'], 130000)

    def test_unknown_amount_is_a_budget_example_but_missing_spending_is_not_invented(self):
        self.inp['diagnostic_inputs'] = {}
        diagnosis = diagnose(self.inp, envelope(item()))
        self.assertEqual(diagnosis['status'], 'ready')
        self.assertTrue(diagnosis['amount_is_example'])
        self.assertNotIn('forecast', diagnosis)
        self.inp['spending_status'] = 'partial'
        self.assertNotEqual(diagnose(self.inp, self.request)['status'], 'ready')

    def test_no_amount_sparse_example_uses_llm_and_retains_money_in_combined_sources(self):
        self.inp['diagnostic_inputs'] = {}
        self.inp['bucket_available'] = dict(self.inp['buckets'])
        phases = []
        def complete(system, payload):
            payload = json.loads(payload)
            phases.append(payload['phase'])
            return draft(payload['balanced_funding']) if payload['phase'] == 'draft' else review()
        answer = recommend(self.inp, envelope(item()), complete)
        self.assertEqual(phases, ['review'])
        self.assertTrue(answer['diagnosis']['amount_is_example'])
        self.assertEqual(answer['plans'][0]['funding'], funding(LIVING=answer['diagnosis']['planning_amount']))
        for code, before in self.inp['buckets'].items():
            self.assertGreaterEqual(answer['plans'][0]['result']['buckets'][code], (before * 3 + 9) // 10)

    def test_missing_initial_reference_does_not_block_protected_unranked_cuts(self):
        from budgets.adjustment_core.simulation import simulate
        self.inp['diagnostic_inputs'] = {}
        self.inp.pop('ml_reference')
        self.inp['priorities']['FASHION_SHOPPING'] = 5
        self.inp['locks']['HOUSEHOLD'] = 50000
        self.inp['minimums']['BEAUTY'] = 40000
        diag = diagnose(self.inp, self.request)
        self.assertGreater(diag['capacities']['LIVING'], 0)
        plan = simulate_plan(self.inp, diag, funding(LIVING=25000, SAVINGS=75000, CUSHION_MONTHLY=50000, SPARE=50000))
        for code in self.inp['priorities']:
            if code != 'FOOD':
                self.assertEqual(plan['result']['allocations'][code], self.inp['allocations'][code])
        self.assertEqual(plan['result']['allocations']['HOUSEHOLD'], 50000)
        self.assertGreaterEqual(plan['result']['allocations']['BEAUTY'], 40000)
        replay = deepcopy(plan['result']['requests'])
        replay['analysis'] = {'request': self.request, 'overrides': {}}
        self.assertEqual(simulate(self.inp, replay)['transfers'], plan['result']['transfers'])

    def test_protected_money_shortfall_is_partial_not_a_source_exhaustion(self):
        self.inp['diagnostic_inputs'] = {}
        request = envelope(item(amount=900000), sources=['SAVINGS', 'CUSHION_MONTHLY', 'SPARE'])
        diag = diagnose(self.inp, request)
        self.assertLess(diag['planning_amount'], 900000)
        plan = simulate_plan(self.inp, diag, funding(**{s: n for s, n in diag['capacities'].items() if n}))
        self.assertEqual(plan['result']['status'], 'partial')
        for code, before in self.inp['buckets'].items():
            self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)

    def test_valid_hybrid_conserves_money_and_preserves_reserve(self):
        before = deepcopy(self.inp)
        plan = simulate_plan(self.inp, self.diag, funding(LIVING=80000, SPARE=70000, SAVINGS=50000))
        self.assertEqual(plan["result"]["allocations"]["FOOD"], 600000)
        self.assertEqual(plan["result"]["buckets"]["SPARE"], 330000)
        self.assertEqual(plan["spare_available_after"], plan["reserve_after"])
        self.assertEqual(self.inp, before)

    def test_spare_overuse_is_rejected_even_when_engine_balance_is_valid(self):
        with self.assertRaisesRegex(ValueError, "funding_limit|spare_reserve_shortfall"):
            simulate_plan(self.inp, self.diag, funding(SPARE=200000))

    def test_verified_savings_can_join_living_and_spare(self):
        plan = simulate_plan(self.inp, self.diag, funding(LIVING=50000, SPARE=50000, SAVINGS=100000))
        self.assertEqual(plan["result"]["buckets"]["SAVINGS"], 500000)
        self.assertIn("goal_SAVINGS", plan["facts"])

    def test_unverified_savings_is_not_a_candidate(self):
        self.inp["bucket_status"]["SAVINGS"] = "unknown"
        diag = diagnose(self.inp, self.request)
        self.assertEqual(diag["capacities"]["SAVINGS"], 0)
        with self.assertRaisesRegex(ValueError, "funding_limit"):
            simulate_plan(self.inp, diag, funding(SAVINGS=100000, LIVING=100000))

    def test_amounts_duplicates_exclusions_and_totals_are_validated(self):
        for bad in (funding(LIVING=True), funding(LIVING=-5000), funding(LIVING=12345),
                    funding(LIVING=100000), funding(LIVING=100000) * 2):
            with self.subTest(funding=bad), self.assertRaises(ValueError):
                simulate_plan(self.inp, self.diag, bad)
        request = envelope(item(amount=200000), excluded=["SAVINGS"])
        diag = diagnose(self.inp, request)
        with self.assertRaises(ValueError): simulate_plan(self.inp, diag, funding(LIVING=100000, SAVINGS=100000))

    def test_no_inference_if_recent_forecast_already_fits(self):
        for sample in self.inp["diagnostic_inputs"]["history"]["samples"]: sample["FOOD"] = 10000
        diagnosis = diagnose(self.inp, envelope(item(mode=None)))
        self.assertTrue(diagnosis['amount_is_example'])
        self.assertEqual(diagnosis['basis'], 'budget')
        self.assertNotIn('forecast', diagnosis)

    def test_priority_lock_and_user_minimum_are_still_enforced(self):
        self.inp["locks"] = {"FASHION_SHOPPING": 200000}
        self.inp["minimums"]["HOUSEHOLD"] = 50000
        plan = simulate_plan(self.inp, self.diag, funding(LIVING=75000, SAVINGS=125000))
        self.assertEqual(plan["result"]["allocations"]["FASHION_SHOPPING"], 200000)
        self.assertEqual(plan["result"]["allocations"]["HOUSEHOLD"], 50000)
        self.assertEqual(plan["result"]["allocations"]["EDUCATION"], 50000)
        self.inp["locks"]["FOOD"] = 400000
        self.assertEqual(diagnose(self.inp, self.request)["status"], "unsupported")

    def test_llm_only_explains_server_verified_plan(self):
        complete = Mock(return_value=review())
        answer = recommend(self.inp, self.request, complete)
        self.assertEqual(answer['recommended_index'], 0)
        payload = json.loads(complete.call_args.args[1])
        self.assertEqual(payload['phase'], 'review')
        self.assertEqual(payload['verified_plans'][0]['status'], 'proposed')
        self.assertEqual(answer['plans'][0]['funding'], funding(LIVING=200000))

    def test_llm_cannot_replace_living_only_plan_with_savings(self):
        complete = Mock(return_value=draft(funding(SAVINGS=200000)))
        answer = recommend(self.inp, self.request, complete)
        self.assertEqual(answer['plans'][0]['funding'], funding(LIVING=200000))
        self.assertEqual(answer['plans'][0]['result']['buckets'], self.inp['buckets'])

    def test_llm_failure_does_not_apply_or_discard_plan(self):
        before = deepcopy(self.inp)
        answer = recommend(self.inp, self.request, Mock(side_effect=RuntimeError('unavailable')))
        self.assertEqual(self.inp, before)
        self.assertEqual(answer['plans'][0]['result']['status'], 'proposed')
        self.assertEqual(answer['recommended_index'], 0)

    def test_invented_explanation_preserves_verified_plan_and_server_reason(self):
        for bad in (review(999), review(ids=['invented_fact']), {'invalid': True}):
            with self.subTest(answer=bad):
                answer = recommend(self.inp, self.request, Mock(return_value=bad))
                self.assertEqual(answer['recommended_index'], 0)
                self.assertEqual(answer['plans'][0]['funding'], funding(LIVING=200000))
                self.assertFalse(any('invented_fact' in reason for reason in answer['reasons']))

    def test_payload_contains_aggregates_not_personal_inputs(self):
        self.inp.update(api_key="do-not-send", account_number="do-not-send", raw_transactions=[{"secret": 1}])
        complete = Mock(side_effect=[draft(funding(LIVING=80000, SPARE=70000, SAVINGS=50000)), review()])
        recommend(self.inp, self.request, complete)
        for call in complete.call_args_list:
            self.assertNotIn("do-not-send", call.args[1])
            self.assertNotIn("raw_transactions", call.args[1])


class LivingFirstPolicyTest(unittest.TestCase):
    def setUp(self):
        self.ctx = financial_context()
        self.ctx['allocations'] = {'FOOD': 100000, 'HOUSEHOLD': 60000, 'BEAUTY': 40000, 'EDUCATION': 100000}
        self.ctx['spent'] = {'HOUSEHOLD': 20000}
        self.ctx['minimums'] = {}
        self.ctx['system_floors'] = {}
        self.ctx['priorities'] = {'EDUCATION': 1, 'HOUSEHOLD': 7, 'BEAUTY': 8}
        self.ctx['buckets'] = {'SPARE': 100000, 'SAVINGS': 200000, 'CUSHION_MONTHLY': 100000}
        self.ctx['bucket_available'] = dict(self.ctx['buckets'])
        self.ctx['bucket_targets'] = dict(self.ctx['buckets'])
        self.ctx['reserve_anchors'] = dict(self.ctx['buckets'])
        self.ctx['bucket_floors'] = {}
        self.ctx['funding_amount'] = self.ctx['fixed_amount'] + 700000
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['ml_reference'] = {}
        self.fail_llm = Mock(side_effect=RuntimeError('LLM unavailable'))

    def plan(self, amount=100000, **kwargs):
        answer = recommend(self.ctx, envelope(item('FOOD', amount), **kwargs), self.fail_llm)
        self.assertTrue(answer['plans'], answer)
        return answer['plans'][0]

    def test_living_only_uses_multiple_categories_without_upper_money(self):
        result = self.plan(80000)['result']
        self.assertEqual(result['buckets'], self.ctx['buckets'])
        self.assertEqual({t['from'] for t in result['transfers']}, {'HOUSEHOLD', 'BEAUTY'})
        self.assertEqual(result['allocations']['EDUCATION'], 100000)
        self.assertEqual(result['allocations']['HOUSEHOLD'], 20000)
        self.assertEqual(result['allocations']['BEAUTY'], 0)

    def test_standard_comparison_uses_the_same_living_first_moves(self):
        from .adjustment_core.comparison import compare_adjustments
        from .adjustment_core.calculation import proportional_group_reduction
        for amount in (80000, 100000):
            comparison = compare_adjustments(self.ctx, envelope(item('FOOD', amount)), allocate_group_reduction=proportional_group_reduction)
            full = [p for p in comparison['plans'] if p['status'] == 'ready']
            self.assertTrue(full)
            for plan in full:
                transferred = plan['result']['transfers']
                self.assertEqual(sum(t['amount'] for t in transferred if t['from'] in self.ctx['allocations']), min(amount, 80000))
                self.assertEqual(sum(t['amount'] for t in transferred if t['from'] not in self.ctx['allocations']), max(amount - 80000, 0))
                cautions = ' '.join(plan['assessment']['cautions'])
                self.assertIn('사용할 예산이 남지 않아요', cautions)
                self.assertNotIn('이보다 더 줄이려면', cautions)

    def test_only_shortfall_is_taken_from_spare(self):
        result = self.plan()['result']
        self.assertEqual(result['buckets'], {'SPARE': 80000, 'SAVINGS': 200000, 'CUSHION_MONTHLY': 100000})
        self.assertEqual(sum(t['amount'] for t in result['transfers'] if t['from'] in self.ctx['allocations']), 80000)

    def test_all_top_six_are_automatically_preserved_but_explicit_cut_is_allowed(self):
        from .adjustment_core.funding import simulate_funding
        for rank in range(1, 7):
            self.ctx['priorities'] = {'EDUCATION': rank}
            result = self.plan(50000)['result']
            self.assertEqual(result['allocations']['EDUCATION'], 100000)
        direct = simulate_funding(self.ctx, envelope(item('EDUCATION', 30000, 'down'), item('FOOD', 30000)))
        self.assertEqual(direct['allocations']['EDUCATION'], 70000)

    def test_locks_minimums_spending_and_confirmed_expenses_remain_protected(self):
        self.ctx['locks'] = {'BEAUTY': 40000}
        self.ctx['minimums'] = {'HOUSEHOLD': 30000}
        self.ctx['required_expenses'] = {'HOUSEHOLD': 50000}
        result = self.plan()['result']
        self.assertEqual(result['allocations']['BEAUTY'], 40000)
        self.assertEqual(result['allocations']['HOUSEHOLD'], 50000)
        self.assertGreaterEqual(result['buckets']['SPARE'], 30000)
        self.assertGreaterEqual(result['buckets']['SAVINGS'], 60000)

    def test_user_selected_or_excluded_sources_are_not_expanded(self):
        selected = self.plan(50000, sources=['SAVINGS'])['result']
        self.assertEqual({t['from'] for t in selected['transfers']}, {'SAVINGS'})
        excluded = self.plan(100000, excluded=['SPARE'])['result']
        self.assertNotIn('SPARE', {t['from'] for t in excluded['transfers']})
        self.assertEqual(sum(t['amount'] for t in excluded['transfers'] if t['from'] == 'SAVINGS'), 20000)

    def test_living_total_increase_cannot_use_living_itself(self):
        answer = recommend(self.ctx, envelope(item('LIVING', 50000), destinations=['FOOD']), self.fail_llm)
        self.assertTrue(answer['plans'], answer)
        self.assertFalse(any(t['from'] in self.ctx['allocations'] for t in answer['plans'][0]['result']['transfers']))

    def test_partial_amount_retains_precise_shortfall(self):
        plan = self.plan(500000)
        result = plan['result']
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(sum(t['amount'] for t in result['transfers']), 360000)
        self.assertEqual(result['shortfalls'][0]['missing_amount'], 140000)
        self.assertIn('140,000원이 부족해요', plan['facts']['funding_order'])
        self.assertIn('다른 생활비에서 80,000원', plan['facts']['funding_order'])

    def test_quotes_replay_and_repeated_adjustments_keep_same_reserve_anchor(self):
        from .adjustment_core.funding import available_adjustment, simulate_funding
        from .adjustment_core.simulation import simulate
        quote = available_adjustment(self.ctx, 'FOOD', 'up', sources=['LIVING'], allocate_group_reduction=__import__('budgets.adjustment_core.calculation', fromlist=['proportional_group_reduction']).proportional_group_reduction)
        self.assertEqual(quote['maximum'], 80000)
        result = self.plan()['result']
        self.assertEqual(simulate(self.ctx, result['requests']), result)
        self.ctx['allocations'] = result['allocations']
        self.ctx['buckets'] = result['buckets']
        self.ctx['bucket_available'] = dict(result['buckets'])
        self.ctx['bucket_targets'] = dict(result['buckets'])
        next_result = self.plan(150000)['result']
        self.assertGreaterEqual(next_result['buckets']['SPARE'], 30000)
        self.assertGreaterEqual(next_result['buckets']['SAVINGS'], 60000)

    def test_missing_current_spending_is_only_an_unverified_preview(self):
        self.ctx.update(spending_status='partial', budget_preview=True)
        result = self.plan()['result']
        self.assertFalse(result['spending_verified'])
        self.assertEqual(result['allocations']['HOUSEHOLD'], 20000)
        self.ctx.pop('budget_preview')
        answer = recommend(self.ctx, envelope(item('FOOD', 100000)), self.fail_llm)
        self.assertEqual(answer['plans'], [])

    def test_future_cycle_does_not_reuse_current_spending(self):
        from .adjustment_schedule import planning_context
        self.ctx.update(monthly_income=self.ctx['funding_amount'], next_cycle_starts_on='2026-12-01')
        planned = planning_context(self.ctx, {'scope': 'next_cycle'})
        self.assertEqual(planned['spent'], {})
        self.assertEqual(planned['required_expenses'], {})
        self.assertFalse(planned['budget_preview'])

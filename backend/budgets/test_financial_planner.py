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
        self.inp['buckets']['SPARE'] = 98176
        protected = recommendation_context(self.inp, {})
        self.assertEqual(protected['bucket_floors']['SPARE'], 29453)
        self.assertEqual(protected['bucket_floors']['SAVINGS'], 300000)
        self.assertEqual(protected['minimums']['FASHION_SHOPPING'], 100000)
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
                self.assertGreater(len(answer['plans'][0]['funding']), 1)
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
        self.assertEqual({row['source'] for row in plan['funding']}, {'LIVING', 'SAVINGS', 'CUSHION_MONTHLY', 'SPARE'})
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

    def test_insufficient_selected_source_offers_combination_but_respects_exclusions(self):
        self.inp['diagnostic_inputs'] = {}
        def complete(system, payload):
            payload = json.loads(payload)
            return draft(payload['balanced_funding']) if payload['phase'] == 'draft' else review(index=None, ids=[])
        for excluded in ([], ['LIVING', 'SPARE', 'CUSHION_MONTHLY']):
            request = envelope(item('EDUCATION', 300000), sources=['SAVINGS'], excluded=excluded)
            answer = recommend(self.inp, request, complete)
            plan = answer['plans'][0]
            self.assertEqual(plan['result']['status'], 'partial' if excluded else 'proposed')
            self.assertEqual(len(plan['funding']) > 1, not bool(excluded))
            for row in plan['funding']:
                self.assertNotIn(row['source'], request['excluded'])

    def test_missing_current_spending_or_unknown_request_amount_is_not_invented(self):
        self.inp['diagnostic_inputs'] = {}
        self.assertNotEqual(diagnose(self.inp, envelope(item()))['status'], 'ready')
        self.inp['spending_status'] = 'partial'
        self.assertNotEqual(diagnose(self.inp, self.request)['status'], 'ready')

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
        self.assertEqual(diagnose(self.inp, envelope(item(mode=None)))["field"], "amount")

    def test_priority_lock_and_user_minimum_are_still_enforced(self):
        self.inp["locks"] = {"FASHION_SHOPPING": 200000}
        self.inp["minimums"]["HOUSEHOLD"] = 50000
        plan = simulate_plan(self.inp, self.diag, funding(LIVING=75000, SAVINGS=125000))
        self.assertEqual(plan["result"]["allocations"]["FASHION_SHOPPING"], 200000)
        self.assertEqual(plan["result"]["allocations"]["HOUSEHOLD"], 50000)
        self.assertEqual(plan["result"]["allocations"]["EDUCATION"], 50000)
        self.inp["locks"]["FOOD"] = 400000
        self.assertEqual(diagnose(self.inp, self.request)["status"], "unsupported")

    def test_llm_drafts_then_selects_only_verified_plan_and_facts(self):
        complete = Mock(side_effect=[draft(funding(LIVING=80000, SPARE=70000, SAVINGS=50000)), review()])
        answer = recommend(self.inp, self.request, complete)
        self.assertEqual(answer["recommended_index"], 0)
        second = json.loads(complete.call_args_list[1].args[1])
        self.assertEqual(second["phase"], "review")
        self.assertEqual(second["verified_plans"][0]["status"], "proposed")
        self.assertEqual(len(answer["reasons"]), 2)

    def test_single_source_draft_also_gets_a_verified_combined_alternative(self):
        self.inp['bucket_available']['SAVINGS'] = 250000
        complete = Mock(side_effect=[draft(funding(SAVINGS=200000)), review(index=1)])
        answer = recommend(self.inp, self.request, complete)
        self.assertEqual(answer['recommended_index'], 1, answer)
        combined = answer['plans'][1]
        self.assertGreater(len(combined['funding']), 1)
        self.assertEqual(sum(row['amount'] for row in combined['funding']), 200000)
        self.assertGreaterEqual(combined['spare_available_after'], combined['reserve_after'])
        reviewed = json.loads(complete.call_args_list[1].args[1])['verified_plans']
        self.assertEqual(len(reviewed), 2)

    def test_failed_draft_can_be_replanned_without_applying_anything(self):
        before = deepcopy(self.inp)
        complete = Mock(side_effect=[draft(funding(SPARE=200000)), draft(funding(LIVING=80000, SPARE=70000, SAVINGS=50000)), review()])
        answer = recommend(self.inp, self.request, complete)
        self.assertEqual(answer["recommended_index"], 0)
        self.assertEqual(self.inp, before)
        self.assertEqual(json.loads(complete.call_args_list[1].args[1])["error"]["code"], "funding_limit")

    def test_invented_fact_or_plan_cannot_be_recommended(self):
        for bad in (review(999), review(ids=["invented_fact"]), review(ids=["reserve"])):
            with self.subTest(answer=bad):
                complete = Mock(side_effect=[draft(funding(LIVING=80000, SPARE=70000, SAVINGS=50000)), bad, bad])
                answer = recommend(self.inp, self.request, complete)
                self.assertEqual(answer["error"], "planner_not_validated")
                self.assertFalse(answer["plans"])

    def test_payload_contains_aggregates_not_personal_inputs(self):
        self.inp.update(api_key="do-not-send", account_number="do-not-send", raw_transactions=[{"secret": 1}])
        complete = Mock(side_effect=[draft(funding(LIVING=80000, SPARE=70000, SAVINGS=50000)), review()])
        recommend(self.inp, self.request, complete)
        for call in complete.call_args_list:
            self.assertNotIn("do-not-send", call.args[1])
            self.assertNotIn("raw_transactions", call.args[1])

"""예산 조정 순수 계산·대화 계약. DB·네트워크·개인 작업 경로를 사용하지 않는다."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from budgets.adjustment_core.calculation import AdjustmentError
from budgets.adjustment_core.comparison import compare_adjustments
from budgets.adjustment_core.conversation import handle_expanded, _merge
from budgets.adjustment_core.funding import available_adjustment, available_bucket_source, large_increase_warning, simulate_funding, validate_funding
from budgets.adjustment_core.explanations import CATEGORY_NAMES
from budgets.adjustment_core.interpretation import InterpretationFailed, parse, read


def context():
    amounts = {
        "FOOD": 400000, "TRANSPORT_CAR": 100000, "HOUSEHOLD": 50000,
        "FASHION_SHOPPING": 200000, "MEDICAL_HEALTH": 50000, "BEAUTY": 50000,
        "EDUCATION": 50000, "CULTURE_LEISURE": 50000, "ETC": 50000,
    }
    return {
        "base_budget_id": "base", "cycle_id": "cycle", "mode": "NORMAL_ADJUSTMENT",
        "source_status": "approved", "policy_version": "test-mixed-unit",
        "automatic_unit": 10000, "fallback_unit": 5000,
        "allocations": amounts, "category_names": {code: CATEGORY_NAMES[code] for code in amounts},
        "spent": {"FOOD": 120000, "FASHION_SHOPPING": 10000},
        "minimums": {"FOOD": 200000}, "system_floors": {"TRANSPORT_CAR": 55000},
        "locks": {}, "lock_revisions": {}, "category_targets": {},
        "priorities": {"FOOD": 1, "TRANSPORT_CAR": 2, "EDUCATION": 3, "FASHION_SHOPPING": 5},
        "ml_reference": {code: amount * 3 // 4 for code, amount in amounts.items() if code != "ETC"},
        "funding_amount": 3000000, "fixed_amount": 800000,
        "buckets": {"SAVINGS": 600000, "CUSHION_MONTHLY": 200000, "SPARE": 400000},
        "bucket_floors": {"SAVINGS": 300000, "CUSHION_MONTHLY": 1},
        "bucket_available": {"SAVINGS": 150000, "CUSHION_MONTHLY": 100000, "SPARE": 400000},
        "bucket_status": {code: "ready" for code in ("SAVINGS", "CUSHION_MONTHLY", "SPARE")},
        "bucket_targets": {"LIVING": 1000000, "SAVINGS": 600000, "CUSHION_MONTHLY": 200000},
    }


def item(code="FOOD", amount=None, direction="up", mode="by"):
    return {"category": code, "amount_mode": mode, "amount": amount, "direction": direction}


def envelope(*items, sources=(), destinations=(), excluded=()):
    return {"items": list(items), "sources": list(sources), "destinations": list(destinations), "excluded": list(excluded)}


def allocation(group, required, capacities):
    """테스트 전용 순서 배분. 서비스의 A/B 그룹 내부 정책을 선택하지 않는다."""
    cuts = {code: 0 for code in group}
    for unit in (10000, 5000):
        for code in group:
            amount = min(required // unit, (capacities[code] - cuts[code]) // unit) * unit
            cuts[code] += amount
            required -= amount
    return {"reductions": cuts, "remaining": required}


def reading(**changes):
    return {
        "mine": True, "requests": [], "scope": None, "clear_targets": [], "unlock": None,
        "approval": None, "cancel": False, "reason": None, "unclear": [],
        "sources": [], "destinations": [], "excluded": [], "funding_scope": None,
        "action": None, "use_available": None, "reference": None, **changes,
    }


class AdjustmentCalculationTest(unittest.TestCase):
    def test_excluding_living_funding_allows_named_increase_but_never_a_living_cut(self):
        inp = context()
        result = simulate_funding(inp, envelope(item(amount=50000), sources=['SPARE', 'LIVING'], excluded=['LIVING']),
                                  allocate_group_reduction=allocation)
        validate_funding(inp, result['requests'], result)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual({row['from'] for row in result['transfers']}, {'SPARE'})
        blocked = simulate_funding(inp, envelope(item(amount=50000, direction='down'), destinations=['SPARE'], excluded=['LIVING']),
                                   allocate_group_reduction=allocation)
        self.assertEqual(blocked['status'], 'infeasible')

    def test_salary_warning_uses_increase_not_target_and_includes_thirty_percent_boundary(self):
        ctx = context()
        ctx['monthly_income'] = 3000000
        for request, warns in [([item('LIVING', 899999)], False), ([item('LIVING', 900000)], True),
                ([item('FOOD', 900000, 'up', 'to')], False), ([item('FOOD', 1300000, 'up', 'to')], True),
                ([item('FOOD', 900000, 'down')], False), ([item('FOOD', 225, 'up', 'percent_by')], True),
                ([item('FOOD', 500000), item('EDUCATION', 400000)], True),
                ([item('LIVING', 500000), item('FOOD', 500000)], False)]:
            with self.subTest(request=request):
                self.assertEqual(bool(large_increase_warning(ctx, request)), warns)
        for income in [None, 0, -1]:
            ctx['monthly_income'] = income
            self.assertIsNone(large_increase_warning(ctx, [item('LIVING', 900000)]))

    def test_source_only_limits_keep_floors_spare_and_unknown_data(self):
        ctx = context()
        ctx['bucket_floors'] = {'SAVINGS': 500000}
        self.assertEqual(available_bucket_source(ctx, 'SAVINGS')['maximum'], 100000)
        self.assertEqual(available_bucket_source(ctx, 'SAVINGS', excluded=['SAVINGS'])['maximum'], 0)
        self.assertIsNone(available_bucket_source(ctx, 'SPARE')['maximum'])
        self.assertEqual(available_bucket_source(ctx, 'SPARE', spare_minimum=350000)['maximum'], 50000)
        ctx['bucket_status']['CUSHION_MONTHLY'] = 'unknown'
        self.assertEqual(available_bucket_source(ctx, 'CUSHION_MONTHLY')['status'], 'needs_data')
        ctx['spending_status'] = 'unknown'
        self.assertEqual(available_bucket_source(ctx, 'SAVINGS')['status'], 'needs_data')

    def test_other_is_never_a_budget_donor_or_recipient_but_legacy_amount_is_preserved(self):
        from budgets.adjustment_core.conversation import distribution_fields
        inp = context()
        self.assertNotIn('ETC', distribution_fields(inp, [item('SPARE', 50000, 'down')])['categories'])
        for request in [envelope(item('ETC', 5000), sources=['SPARE']),
                        envelope(item(amount=5000), sources=['ETC']),
                        envelope(item('SPARE', 5000, 'down'), destinations=['ETC'])]:
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, 'unsupported_category'):
                self.simulate(inp, request)
        result = self.simulate(inp, envelope(item(amount=200000), sources=['LIVING']))
        self.assertEqual(result['allocations']['ETC'], inp['allocations']['ETC'])
        self.assertTrue(all('ETC' not in (row['from'], row['to']) for row in result['transfers']))

    def test_spare_maximum_is_not_an_automatic_recommendation_and_confirmed_minimum_is_replayed(self):
        inp = context()
        inp['funding_amount'] += 98176 - inp['buckets']['SPARE']
        inp['buckets']['SPARE'] = inp['bucket_available']['SPARE'] = 98176
        request = envelope(item('HOUSEHOLD', None, 'up', None), sources=['SPARE'])
        compared = compare_adjustments(inp, request, allocate_group_reduction=allocation, respect_selected=True)
        self.assertFalse(any(row['status'] == 'ready' for row in compared['plans']))
        self.assertTrue(any(row.get('needs_spare_minimum') for row in compared['plans']))
        reduced = compare_adjustments(inp, envelope(item('SPARE', None, 'down', None)), allocate_group_reduction=allocation)
        self.assertFalse(any(row['status'] == 'ready' for row in reduced['plans']))
        self.assertTrue(any(row.get('needs_spare_minimum') for row in reduced['plans']))
        quote = available_adjustment(inp, 'HOUSEHOLD', 'up', sources=['SPARE'], spare_minimum=30000)
        self.assertEqual(quote['maximum'], 65000)
        self.assertEqual(quote['envelope']['spare_minimum'], 30000)
        result = self.simulate(inp, quote['envelope'])
        self.assertEqual(result['buckets']['SPARE'], 33176)
        self.assertEqual(result['requests']['spare_minimum'], 30000)

    def test_explanations_omit_internal_units_and_give_specific_next_action(self):
        from budgets.adjustment_core.explanations import result_explanation
        inp = context()
        proposed = self.simulate(inp, envelope(item(amount=10000), sources=['SPARE']))
        explanation = result_explanation(inp, proposed)
        self.assertEqual(explanation['title'], '예산 확정 전에 확인할 내용')
        self.assertNotIn('단위', str(explanation))
        self.assertNotIn('검수', str(explanation))
        inp['spending_status'] = 'partial'
        blocked = self.simulate(inp, envelope(item(amount=10000), sources=['SPARE']))
        explanation = result_explanation(inp, blocked)
        self.assertIn('최근 지출', explanation['why'])
        self.assertIn('계좌·카드 연결 관리', explanation['next'])

    def test_released_living_budget_uses_priority_and_preserves_locks_and_exclusions(self):
        for locked, excluded, recipient in ((False, [], 'FOOD'), (True, [], 'TRANSPORT_CAR'),
                                             (False, ['FOOD'], 'TRANSPORT_CAR')):
            with self.subTest(locked=locked, excluded=excluded):
                inp = context()
                if locked:
                    inp['locks'] = {'FOOD': inp['allocations']['FOOD']}
                before = deepcopy(inp)
                request = envelope(item('MEDICAL_HEALTH', 10000, 'down', 'to'),
                                   destinations=['LIVING'], excluded=excluded)
                result = simulate_funding(inp, request)
                self.assertEqual(result['status'], 'proposed')
                self.assertEqual(result['allocations']['MEDICAL_HEALTH'], 10000)
                self.assertEqual(result['allocations'][recipient], before['allocations'][recipient] + 40000)
                self.assertEqual(sum(result['allocations'].values()), sum(inp['allocations'].values()))
                self.assertEqual(inp, before)
                validate_funding(inp, request, result)

    def simulate(self, inp, request):
        before = deepcopy((inp, request))
        result = simulate_funding(inp, request, allocate_group_reduction=allocation)
        self.assertEqual((inp, request), before)
        self.assertEqual(sum(result["allocations"].values()) + sum(result["buckets"].values()) + inp["fixed_amount"], inp["funding_amount"])
        validate_funding(inp, request, result)
        return result

    def test_real_budget_amounts_use_five_thousand_fallback_without_changing_total(self):
        inp = context()
        inp['ml_reference']['MEDICAL_HEALTH'] = 35000
        result = self.simulate(inp, envelope(item(amount=200000), sources=["LIVING"]))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["allocations"]["FOOD"], 445000)
        self.assertEqual(result["allocations"]["TRANSPORT_CAR"], 100000)
        self.assertEqual(result["shortfalls"][0]["missing_amount"], 155000)
        self.assertTrue(all(value % 5000 == 0 for value in result["allocations"].values()))

    def test_explicit_won_request_has_no_precision_exception(self):
        result = self.simulate(context(), envelope(item(amount=35555), item("SAVINGS", 35555, "down")))
        self.assertEqual(result["allocations"]["FOOD"], 435000)
        self.assertEqual(result["buckets"]["SAVINGS"], 565000)
        self.assertTrue(all(row["missing_amount"] == 555 for row in result["shortfalls"]))

    def test_direct_donor_waives_ml_only_and_preserves_spent_floor(self):
        inp = context()
        inp["spent"]["FASHION_SHOPPING"] = 125000
        result = self.simulate(inp, envelope(item(amount=100000), item("FASHION_SHOPPING", 100000, "down")))
        self.assertEqual(result["allocations"]["FASHION_SHOPPING"], 125000)
        self.assertEqual(result["allocations"]["FOOD"], 475000)
        self.assertEqual(result["shortfalls"][0]["missing_amount"], 25000)

    def test_floor_boundary_never_rounds_below_minimum(self):
        for floor, expected in ((55000, 55000), (55001, 60000)):
            with self.subTest(floor=floor):
                inp = context()
                inp["minimums"]["TRANSPORT_CAR"] = floor
                request = envelope(item(amount=100000), item("TRANSPORT_CAR", 100000, "down"))
                result = self.simulate(inp, request)
                self.assertEqual(result["allocations"]["TRANSPORT_CAR"], expected)

    def test_locked_won_amount_is_kept_exact(self):
        inp = context()
        inp["allocations"]["HOUSEHOLD"] = 37777
        inp["locks"] = {"HOUSEHOLD": 37777}
        inp["fixed_amount"] += 12223
        result = self.simulate(inp, envelope(item(amount=15000), sources=["SPARE"]))
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"]["HOUSEHOLD"], 37777)
        changed = self.simulate(inp, envelope(item("HOUSEHOLD", 10000), sources=["SPARE"]))
        self.assertEqual(changed["status"], "infeasible")
        self.assertEqual(changed["allocations"], inp["allocations"])

    def test_unknown_bucket_is_not_spendable_zero(self):
        inp = context()
        inp["bucket_status"]["SAVINGS"] = "unknown"
        result = self.simulate(inp, envelope(item(amount=10000), sources=["SAVINGS"]))
        self.assertEqual(result["status"], "needs_data")
        self.assertEqual(result["allocations"], inp["allocations"])

    def test_conflicting_lock_cannot_be_hidden_by_using_spare(self):
        for locked, minimum, code in ((100000, 110000, "lock_below_minimum"), (90000, 0, "lock_mismatch")):
            with self.subTest(code=code):
                inp = context()
                inp["locks"] = {"TRANSPORT_CAR": locked}
                inp["minimums"]["TRANSPORT_CAR"] = minimum
                before = deepcopy(inp)
                result = self.simulate(inp, envelope(item(amount=10000), sources=["SPARE"]))
                self.assertEqual(result["status"], "infeasible")
                self.assertEqual(result["conflicts"][0]["code"], code)
                self.assertEqual(result["transfers"], [])
                self.assertEqual(inp, before)
                from budgets.adjustment_core.explanations import result_explanation
                reason = result_explanation(inp, result)["brief"]
                self.assertIn("설정", reason)
                if code == "lock_below_minimum":
                    self.assertIn("110,000원", reason)
                    self.assertIn("100,000원", reason)

    def test_incomplete_spending_blocks_simulation_quotes_and_forged_success(self):
        inp = context()
        request = envelope(item(amount=10000), sources=["SPARE"])
        valid = self.simulate(inp, request)
        for state in ("partial", "none", "error"):
            with self.subTest(state=state):
                inp["spending_status"] = state
                result = self.simulate(inp, request)
                self.assertEqual(result["status"], "needs_data")
                self.assertEqual(result["conflicts"][0]["code"], "spending_unverified")
                quote = available_adjustment(inp, "FOOD", "up", sources=["SPARE"])
                self.assertEqual(quote["status"], "needs_data")
                with self.assertRaisesRegex(AdjustmentError, "unverified_funding_result"):
                    validate_funding(inp, request, valid)

    def test_automatic_donor_cannot_be_higher_priority_than_request(self):
        inp = context()
        result = self.simulate(inp, envelope(item("FASHION_SHOPPING", 200000), sources=["LIVING"]))
        for code in ("FOOD", "TRANSPORT_CAR", "EDUCATION"):
            self.assertEqual(result["allocations"][code], inp["allocations"][code])
        self.assertEqual(result["status"], "partial")

    def test_every_assigned_rank_is_protected_until_a_donor_is_named(self):
        inp = context()
        inp['priorities'] = dict(zip(['FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'MEDICAL_HEALTH',
                                     'FASHION_SHOPPING', 'EDUCATION', 'BEAUTY', 'CULTURE_LEISURE'], range(1, 9)))
        inp['spent']['ETC'] = inp['allocations']['ETC']
        original = deepcopy(inp)
        result = self.simulate(inp, envelope(item(amount=10000), sources=['LIVING']))
        self.assertEqual(result['allocations'], inp['allocations'])
        self.assertEqual(result['transfers'], [])
        self.assertEqual(available_adjustment(inp, 'FOOD', 'up', sources=['LIVING'],
            allocate_group_reduction=allocation)['maximum'], 0)
        for donor in ('FASHION_SHOPPING', 'EDUCATION', 'BEAUTY', 'CULTURE_LEISURE'):
            with self.subTest(rank=inp['priorities'][donor]):
                consented = self.simulate(inp, envelope(item(amount=10000), sources=[donor]))
                self.assertEqual(consented['status'], 'proposed')
                self.assertEqual(consented['allocations'][donor], inp['allocations'][donor] - 10000)
        self.assertEqual(inp, original)

    def test_ranked_automatic_transfer_without_source_consent_is_rejected(self):
        inp = context()
        request = envelope(item(amount=10000), sources=['LIVING'],
                           excluded=[c for c in inp['allocations'] if c not in {'FOOD', 'FASHION_SHOPPING'}])
        without_rank = deepcopy(inp)
        without_rank['priorities'].pop('FASHION_SHOPPING')
        forged = self.simulate(without_rank, request)
        self.assertEqual(forged['status'], 'proposed')
        with self.assertRaises(AdjustmentError):
            validate_funding(inp, request, forged)

    def test_missing_ml_reference_blocks_automatic_cut_but_not_direct_donor(self):
        inp = context()
        inp["ml_reference"] = {}
        automatic = self.simulate(inp, envelope(item(amount=10000), sources=["FASHION_SHOPPING"]))
        self.assertEqual(automatic["status"], "needs_data")
        direct = self.simulate(inp, envelope(item(amount=10000), item("FASHION_SHOPPING", 10000, "down")))
        self.assertEqual(direct["status"], "proposed")

    def test_quote_is_same_amount_as_verified_simulation(self):
        inp = context()
        quote = available_adjustment(inp, "FOOD", "up", sources=["LIVING"], allocate_group_reduction=allocation)
        self.assertEqual(quote["maximum"], 40000)
        result = self.simulate(inp, quote["envelope"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"]["FOOD"], 440000)

    def test_no_automatic_group_policy_is_silently_selected(self):
        inp = context()
        result = simulate_funding(inp, envelope(item(amount=10000), sources=["LIVING"]))
        self.assertEqual(result["status"], "needs_policy")
        self.assertEqual(result["allocations"], inp["allocations"])

    def test_unlocked_off_grid_legacy_budget_requires_confirmation(self):
        inp = context()
        inp["allocations"]["HOUSEHOLD"] += 1
        inp["funding_amount"] += 1
        result = self.simulate(inp, envelope(item(amount=10000), sources=["SPARE"]))
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["conflicts"][0]["code"], "unit_alignment_required")

    def test_forged_non_grid_balances_fail_even_when_sum_still_matches(self):
        inp = context()
        request = envelope(item(amount=15000), sources=["SPARE"])
        result = self.simulate(inp, request)
        result["allocations"]["FOOD"] += 1
        result["buckets"]["SPARE"] -= 1
        with self.assertRaisesRegex(AdjustmentError, "invalid_result_budget_unit"):
            validate_funding(inp, request, result)

    def test_unallocated_spare_residual_stays_exact_without_relaxing_budget_units(self):
        inp = context()
        inp["buckets"]["SPARE"] = 35555
        inp["bucket_available"]["SPARE"] = 35555
        inp["funding_amount"] -= 400000 - 35555
        result = self.simulate(inp, envelope(item(amount=35000), sources=["SPARE"]))
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"]["FOOD"], 435000)
        self.assertEqual(result["buckets"]["SPARE"], 555)
        self.assertTrue(all(value % 5000 == 0 for value in result["allocations"].values()))
        self.assertTrue(all(row["amount"] % 5000 == 0 for row in result["transfers"]))

    def test_comparison_preserves_original_amount_and_independent_sources(self):
        inp = context()
        before = deepcopy(inp)
        result = compare_adjustments(inp, {**envelope(item(amount=200000)), 'spare_minimum': 100000}, allocate_group_reduction=allocation)
        self.assertEqual(inp, before)
        self.assertEqual(result["basis_amount"], 200000)
        self.assertEqual([plan["amount"] for plan in result["plans"]], [40000, 200000, 200000, 200000])
        self.assertEqual(result["recommendation"]["plan_id"], "spare")
        self.assertEqual(result["plans"][1]["result"]["buckets"]["SAVINGS"], 600000)

    def test_combined_savings_increase_uses_three_sources_and_preserves_spare(self):
        inp = context()
        request = {**envelope(item('SAVINGS', 300000)), 'spare_minimum': 200000}
        comparison = compare_adjustments(inp, request, allocate_group_reduction=allocation)
        combined = next(p for p in comparison['plans'] if p['id'] == 'combined')
        self.assertEqual(combined['status'], 'ready')
        self.assertEqual(combined['amount'], 300000)
        sources = {t['from'] for t in combined['result']['transfers']}
        self.assertIn('SPARE', sources)
        self.assertIn('CUSHION_MONTHLY', sources)
        self.assertTrue(sources.intersection(inp['allocations']))
        self.assertNotIn('SAVINGS', sources)
        self.assertGreaterEqual(combined['result']['buckets']['SPARE'], 200000)
        self.assertEqual(comparison['recommendation']['plan_id'], 'combined')
        validate_funding(inp, combined['envelope'], combined['result'])

    def test_savings_comparison_never_spends_spare_without_confirmed_minimum(self):
        inp = context()
        for minimum in (None, 400000):
            with self.subTest(minimum=minimum):
                request = envelope(item('SAVINGS', 300000))
                if minimum is not None:
                    request['spare_minimum'] = minimum
                comparison = compare_adjustments(inp, request, allocate_group_reduction=allocation)
                for plan in comparison['plans']:
                    if plan.get('result'):
                        self.assertEqual(plan['result']['buckets']['SPARE'], 400000)
                if minimum is None:
                    self.assertTrue(next(p for p in comparison['plans'] if p['id'] == 'spare')['needs_spare_minimum'])


class Store:
    def __init__(self):
        self.state = None

    def get_agent_state(self, cid, agent):
        return deepcopy(self.state)

    def save_agent_state(self, cid, agent, state):
        self.state = deepcopy(state)


class AdjustmentConversationTest(unittest.TestCase):
    def test_merge_removes_only_completely_empty_requests_and_preserves_input(self):
        blank = dict.fromkeys(("category", "amount", "amount_mode", "direction"))
        state = {"requests": [deepcopy(blank)]}
        data = reading(requests=[item(amount=0, mode="to", direction=None), item(code=None, amount=20000), blank])
        before = deepcopy(data)
        _merge(state, data, {})
        self.assertEqual(state["requests"], data["requests"][:2])
        self.assertEqual(data, before)
        empty = {"requests": [deepcopy(blank)]}
        _merge(empty, reading(requests=[blank]), {})
        self.assertEqual(empty["requests"], [])

    def setUp(self):
        self.context, self.store = context(), Store()
        self.complete = Mock()
        self.cards, self.tasks = [], {}

    def propose(self, original, result, **settings):
        number = len(self.cards) + 1
        card = {**deepcopy(result), **deepcopy(settings), "proposal_id": f"proposal-{number}", "task_ref": f"task-{number}"}
        self.cards.append(card)
        self.tasks[card["task_ref"]] = {"task_ref": card["task_ref"], "state": "awaiting_approval"}
        return card

    def cancel(self, ref):
        self.tasks[ref]["state"] = "canceled"

    def turn(self, text, parsed=None):
        self.complete.side_effect = None if parsed is not None else AssertionError("known answer must not call LLM")
        if parsed is not None:
            self.complete.return_value = parsed
        return handle_expanded({"conversation_id": "talk", "text": text}, store=self.store,
            complete=self.complete, get_context=lambda: deepcopy(self.context),
            simulate=lambda inp, request: simulate_funding(inp, request, allocate_group_reduction=allocation),
            propose=self.propose, task_status=lambda ref: deepcopy(self.tasks[ref]), cancel=self.cancel, allocator=allocation)

    def test_natural_scope_and_partial_selection_do_not_repeat_question_or_save(self):
        before = deepcopy(self.context)
        self.turn("식비를 20만원 늘려줘", reading(requests=[item(amount=200000)]))
        response = self.turn("생활비 내에서 해결하고 싶어", reading(funding_scope="living", reference="previous"))
        card = response["reply"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 440000)
        self.assertEqual(response["reply"]["alternatives"]["basis_amount"], 200000)
        count = self.complete.call_count
        response = self.turn("그냥 이렇게 해줘")
        self.assertEqual(response["reply"]["proposal"]["proposal_id"], card["proposal_id"])
        self.assertNotIn("pending", self.store.state)
        self.assertEqual(self.complete.call_count, count)
        self.assertEqual(len(self.cards), 1)
        self.assertEqual(self.context, before)
        self.assertEqual(self.tasks[card["task_ref"]]["state"], "awaiting_approval")

    def test_comparison_and_details_do_not_create_or_select_a_proposal(self):
        self.turn("식비가 부족해", reading(requests=[item(mode=None)]))
        response = self.turn("잘 모르겠어요", reading(action='help_decide'))
        self.assertEqual(response["reply"]["comparison"]["basis_amount"], 40000)
        self.assertEqual(self.cards, [])
        before = deepcopy(self.store.state)
        self.turn("2안 예산 보여줘")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.cards, [])
        self.assertNotIn('plan_spare', dict(self.store.state['pending']['choices']))
        card = self.turn("plan_living")["reply"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 440000)
        self.assertEqual(card["buckets"]["SPARE"], 400000)
        self.assertEqual(self.context["allocations"]["FOOD"], 400000)

    def test_saved_message_needs_verified_saved_reference(self):
        self.turn("식비 1만5천원 여윳돈에서", reading(requests=[item(amount=15000)], sources=["SPARE"]))
        self.tasks["task-1"]["state"] = "saved"
        response = self.turn("완료됐어?")
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"], "invalid_saved_ref")

    def test_cancel_uses_task_status_and_keeps_budget(self):
        before = deepcopy(self.context)
        self.turn("식비 1만5천원 여윳돈에서", reading(requests=[item(amount=15000)], sources=["SPARE"]))
        response = self.turn("취소")
        self.assertEqual(response["status"], "ended")
        self.assertEqual(self.tasks["task-1"]["state"], "canceled")
        self.assertIsNone(self.store.state)
        self.assertEqual(self.context, before)


class AdjustmentInterpretationTest(unittest.TestCase):
    def test_schema_keeps_new_intent_amount_and_pending_without_calculating(self):
        payload = reading(requests=[item(amount=35555)], sources=["SPARE"])
        complete = Mock(return_value=json.dumps(payload, ensure_ascii=False))
        pending = {"kind": "amount", "choices": [("available", "가능한 금액 확인")]}
        result = read("35555원 늘릴게", complete, categories={"FOOD": "식비"}, pending=pending)
        self.assertEqual(result["requests"][0]["amount"], 35555)
        self.assertEqual(json.loads(complete.call_args.args[1])["pending"], {"kind": "amount", "choices": [["available", "가능한 금액 확인"]]})

    def test_invalid_llm_money_and_extra_fields_are_rejected(self):
        for payload in (reading(requests=[{**item(amount=10000), "amount": True}]), {**reading(), "saved": True}):
            with self.subTest(payload=payload), self.assertRaises(InterpretationFailed):
                parse(payload)


if __name__ == "__main__":
    unittest.main()

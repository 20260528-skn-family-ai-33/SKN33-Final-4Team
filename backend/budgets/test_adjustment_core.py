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
        # 계산 규칙 테스트용: 확인된 평소 소비가 0원인 상태(자동 감액 하한 = 이미 쓴 돈·최소금액).
        # 실제 보호금액 규칙은 AutomaticProtectionTest 에서 따로 확인한다.
        "usage_floors": {code: 0 for code in amounts},
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
    def test_spare_minimum_protects_available_balance_after_spending_and_reservations(self):
        for available in (250000, 230000):
            with self.subTest(available=available):
                ctx = context()
                ctx['bucket_available']['SPARE'] = available
                request = {**envelope(item('FOOD', 100000), sources=['SPARE']), 'spare_minimum': 200000}
                quote = available_bucket_source(ctx, 'SPARE', spare_minimum=200000)
                self.assertEqual(quote['maximum'], available - 200000)
                result = simulate_funding(ctx, request)
                moved = ctx['buckets']['SPARE'] - result['buckets']['SPARE']
                self.assertEqual(moved, available - 200000)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(available - moved, 200000)
                validate_funding(ctx, request, result)


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

    def test_large_request_warning_asks_reason_but_does_not_treat_claim_as_verified(self):
        ctx = context()
        ctx['monthly_income'] = 3000000
        request = [item('LIVING', 1000000)]
        self.assertIn('어떤 지출에 얼마', large_increase_warning(ctx, request))
        for reason in ['그냥 많이 쓰고 싶어', '꼭 필요하니까 무조건 늘려줘']:
            self.assertIn('꼭 필요한지는 확인하기 어려워요', large_increase_warning(ctx, request, reason=reason))
        ctx.update(income_before=2000000, planning={'scope': 'next_cycle', 'income_change': {'amount': 1000000}})
        self.assertIn('늘어난 월급으로 충당', large_increase_warning(ctx, request))
        ctx['planning'] = {'scope': 'next_cycle', 'expense': {'total': 1700000, 'set_aside': 700000, 'additional': 1000000}}
        self.assertIn('따로 마련한 돈을 제외한 필요액', large_increase_warning(ctx, request))

    def test_source_only_limits_keep_floors_spare_and_unknown_data(self):
        ctx = context()
        ctx['bucket_floors'] = {'SAVINGS': 500000}
        self.assertEqual(available_bucket_source(ctx, 'SAVINGS')['maximum'], 100000)
        self.assertEqual(available_bucket_source(ctx, 'SAVINGS', excluded=['SAVINGS'])['maximum'], 0)
        # 여윳돈은 따로 정하지 않아도 30%(400,000원 중 120,000원)를 남기고 계산한다.
        self.assertEqual(available_bucket_source(ctx, 'SPARE')['maximum'], 280000)
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

    def test_released_budget_requires_a_named_destination_regardless_of_priority(self):
        for locked, excluded in ((False, []), (True, []), (False, ['FOOD'])):
            with self.subTest(locked=locked, excluded=excluded):
                inp = context()
                if locked:
                    inp['locks'] = {'FOOD': inp['allocations']['FOOD']}
                before = deepcopy(inp)
                request = envelope(item('MEDICAL_HEALTH', 10000, 'down', 'to'),
                                   destinations=['LIVING'], excluded=excluded)
                result = simulate_funding(inp, request)
                self.assertEqual(result['status'], 'needs_destination')
                self.assertEqual(result['allocations'], before['allocations'])
                self.assertEqual(result['buckets'], before['buckets'])
                self.assertEqual(result['transfers'], [])
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
        result = self.simulate(context(), envelope(item(amount=200000), sources=['LIVING']))
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['allocations']['FOOD'], 600000)
        self.assertEqual(result['allocations']['TRANSPORT_CAR'], 100000)
        self.assertTrue(all(value % 5000 == 0 for value in result['allocations'].values()))

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
        self.assertEqual(result["status"], "proposed")


    def test_every_assigned_rank_is_protected_until_a_donor_is_named(self):
        inp = context()
        inp['priorities'] = dict(zip(['FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'MEDICAL_HEALTH', 'FASHION_SHOPPING', 'EDUCATION', 'BEAUTY', 'CULTURE_LEISURE'], range(1, 9)))
        result = self.simulate(inp, envelope(item(amount=10000), sources=['LIVING']))
        self.assertEqual(result['status'], 'proposed')
        self.assertTrue({t['from'] for t in result['transfers']} <= {'BEAUTY', 'CULTURE_LEISURE'})
        for code, rank in inp['priorities'].items():
            if rank <= 6 and code != 'FOOD':
                self.assertEqual(result['allocations'][code], inp['allocations'][code])
        direct = self.simulate(inp, envelope(item('EDUCATION', 10000, 'down'), item(amount=10000)))
        self.assertEqual(direct['allocations']['EDUCATION'], 40000)

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
        self.assertEqual(automatic["status"], "proposed")
        direct = self.simulate(inp, envelope(item(amount=10000), item("FASHION_SHOPPING", 10000, "down")))
        self.assertEqual(direct["status"], "proposed")

    def test_quote_is_same_amount_as_verified_simulation(self):
        inp = context()
        quote = available_adjustment(inp, "FOOD", "up", sources=["LIVING"], allocate_group_reduction=allocation)
        self.assertEqual(quote["maximum"], 200000)
        result = self.simulate(inp, quote["envelope"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"]["FOOD"], 600000)

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
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["allocations"]["FOOD"], 420000)
        self.assertEqual(result["buckets"]["SPARE"], 15555)
        self.assertTrue(all(value % 5000 == 0 for value in result["allocations"].values()))
        self.assertTrue(all(row["amount"] % 5000 == 0 for row in result["transfers"]))


    def test_comparison_preserves_original_amount_and_independent_sources(self):
        inp = context()
        before = deepcopy(inp)
        result = compare_adjustments(inp, {**envelope(item(amount=200000)), 'spare_minimum': 100000}, allocate_group_reduction=allocation)
        self.assertEqual(inp, before)
        self.assertEqual(result['basis_amount'], 200000)
        self.assertTrue(all(plan['amount'] == 200000 for plan in result['plans']))
        self.assertEqual(result['recommendation']['plan_id'], 'living')
        selected = next(p for p in result['plans'] if p['id'] == 'living')
        self.assertEqual(selected['result']['buckets'], inp['buckets'])

    def test_category_reduction_without_priorities_compares_all_destination_types(self):
        inp = context()
        inp['priorities'] = {}
        before = deepcopy(inp)
        result = compare_adjustments(inp, envelope(item('FOOD', 100000, 'down')), allocate_group_reduction=allocation)
        plans = {plan['id']: plan for plan in result['plans'] if plan['status'] == 'ready'}
        self.assertEqual(set(plans), {'spare', 'savings', 'cushion'})
        for plan in plans.values():
            self.assertEqual(plan['result']['allocations']['FOOD'], 300000)
            self.assertEqual(plan['amount'], 100000)
            validate_funding(inp, plan['envelope'], plan['result'])
        self.assertEqual(plans['savings']['result']['buckets']['SAVINGS'], 700000)
        self.assertEqual(plans['cushion']['result']['buckets']['CUSHION_MONTHLY'], 300000)
        living = next(plan for plan in result['plans'] if plan['id'] == 'living')
        self.assertEqual(living['status'], 'needs_destination')
        self.assertIsNone(living['result'])
        self.assertEqual(living['envelope']['items'], [item('FOOD', 100000, 'down', 'by') | {'automatic': False}])
        self.assertEqual(living['envelope']['destinations'], ['LIVING'])
        self.assertEqual(inp, before)

    def test_unnamed_destination_cannot_validate_an_older_automatic_allocation(self):
        inp = context()
        explicit = envelope(item('FOOD', 100000, 'down'), destinations=['TRANSPORT_CAR'])
        result = self.simulate(inp, explicit)
        unnamed = envelope(item('FOOD', 100000, 'down'), destinations=['LIVING'])
        result['requests']['destinations'] = ['LIVING']
        with self.assertRaises(ValueError):
            validate_funding(inp, unnamed, result)

    def test_reduction_explicit_destination_and_exclusions_are_not_expanded(self):
        inp = context()
        result = compare_adjustments(inp, envelope(item('FOOD', 100000, 'down'),
            destinations=['MEDICAL_HEALTH'], excluded=['SAVINGS']),
            allocate_group_reduction=allocation, respect_selected=True)
        plans = [plan for plan in result['plans'] if plan['status'] == 'ready']
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['result']['allocations']['MEDICAL_HEALTH'], 150000)
        self.assertEqual(plans[0]['result']['buckets'], inp['buckets'])
        excluded = compare_adjustments(inp, envelope(item('FOOD', 100000, 'down'),
            excluded=['SAVINGS', 'CUSHION_MONTHLY']), allocate_group_reduction=allocation)
        self.assertEqual({plan['id'] for plan in excluded['plans'] if plan['status'] == 'ready'}, {'spare'})

    def test_receiving_bucket_does_not_require_withdrawal_availability(self):
        inp = context()
        inp['priorities'] = {}
        inp['bucket_status'] = dict.fromkeys(inp['buckets'], 'unknown')
        inp['bucket_available'] = {}
        before = deepcopy(inp)
        for code in ('SPARE', 'SAVINGS', 'CUSHION_MONTHLY'):
            with self.subTest(destination=code):
                request = envelope(item('FOOD', 100000, 'down'), destinations=[code])
                result = simulate_funding(inp, request, allocate_group_reduction=allocation)
                self.assertEqual(result['status'], 'proposed')
                self.assertEqual(result['allocations']['FOOD'], 300000)
                self.assertEqual(result['buckets'][code], inp['buckets'][code] + 100000)
                validate_funding(inp, request, result)
                quote = available_adjustment(inp, 'FOOD', 'down', destinations=[code],
                                             allocate_group_reduction=allocation)
                self.assertEqual((quote['status'], quote['maximum']), ('ready', 200000))
                outgoing = simulate_funding(inp, envelope(item('FOOD', 10000), sources=[code]),
                                            allocate_group_reduction=allocation)
                self.assertEqual(outgoing['status'], 'needs_data')
                self.assertEqual(outgoing['conflicts'][0]['code'], 'bucket_unverified')
                self.assertEqual(outgoing['transfers'], [])
                self.assertEqual(outgoing['allocations'], inp['allocations'])
        comparison = compare_adjustments(inp, envelope(item('FOOD', 100000, 'down')),
                                         allocate_group_reduction=allocation)
        self.assertEqual({p['id'] for p in comparison['plans'] if p['status'] == 'ready'},
                         {'spare', 'savings', 'cushion'})
        self.assertEqual(inp, before)

    def test_receiving_bucket_still_requires_budget_and_enabled_feature(self):
        inp = context()
        inp['bucket_status']['CUSHION_MONTHLY'] = 'disabled'
        request = envelope(item('FOOD', 100000, 'down'), destinations=['CUSHION_MONTHLY'])
        result = simulate_funding(inp, request, allocate_group_reduction=allocation)
        self.assertEqual(result['status'], 'infeasible')
        self.assertEqual(result['conflicts'][0]['code'], 'bucket_disabled')
        self.assertEqual(result['transfers'], [])
        inp['buckets'].pop('CUSHION_MONTHLY')
        with self.assertRaisesRegex(ValueError, 'missing_buckets'):
            simulate_funding(inp, request, allocate_group_reduction=allocation)

    def test_combined_savings_increase_uses_three_sources_and_preserves_spare(self):
        inp = context()
        request = {**envelope(item('SAVINGS', 300000)), 'spare_minimum': 200000}
        comparison = compare_adjustments(inp, request, allocate_group_reduction=allocation)
        combined = next(p for p in comparison['plans'] if p['id'] == 'combined')
        self.assertEqual(combined['status'], 'ready')
        self.assertEqual(combined['amount'], 300000)
        sources = {t['from'] for t in combined['result']['transfers']}
        self.assertIn('SPARE', sources)
        self.assertNotIn('CUSHION_MONTHLY', sources)
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
        self.turn("식비를 30만원 늘려줘", reading(requests=[item(amount=300000)]))
        response = self.turn("생활비 내에서 해결하고 싶어", reading(funding_scope="living", reference="previous"))
        card = response["reply"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 600000)
        self.assertEqual(response["reply"]["alternatives"]["basis_amount"], 300000)
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
        self.assertEqual(response["reply"]["comparison"]["basis_amount"], 200000)
        self.assertEqual(self.cards, [])
        before = deepcopy(self.store.state)
        self.turn("2안 예산 보여줘")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.cards, [])
        self.assertNotIn('plan_spare', dict(self.store.state['pending']['choices']))
        self.turn("plan_living")
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertIsNone(self.store.state['requests'][0]['amount'])
        self.assertEqual(self.cards, [])
        card = self.turn('5만원만 늘릴게', reading(requests=[item(amount=50000)], reference='previous'))['reply']['proposal']
        self.assertEqual(card["allocations"]["FOOD"], 450000)
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


class AutomaticProtectionTest(unittest.TestCase):
    """자동 감액 보호금액과 재원 순서(생활비 → 여윳돈 → 월 머니쿠션 → 저축)."""

    def history_context(self, food=40000, other=1000, days=10):
        from budgets.adjustment_core.financial_planner import synthetic_diagnostics
        ctx = context()
        ctx.pop('usage_floors')
        ctx['today'] = '2026-11-10'
        ctx['diagnostic_inputs'] = synthetic_diagnostics(ctx['allocations'])
        ctx['diagnostic_inputs']['remaining_days'] = days
        ctx['diagnostic_inputs']['planned_spare']['amount'] = 0
        for sample in ctx['diagnostic_inputs']['history']['samples']:
            sample.update({code: food if code == 'FOOD' else 0 if code == 'ETC' else other for code in sample})
        return ctx

    def plans(self, ctx, *items, **envelope_fields):
        from budgets.adjustment_core.advisor import plan_increase
        return plan_increase(ctx, envelope(*items, **envelope_fields), allocation)

    def test_without_verified_history_living_categories_are_not_cut_automatically(self):
        from budgets.adjustment_core.protection import usage_floors
        ctx = context()
        ctx.pop('usage_floors')
        self.assertIsNone(usage_floors(ctx))
        comparison = self.plans(ctx, item(amount=50000))
        recommended = comparison['plans'][0]
        # 생활비는 그대로 두고 여윳돈에서만 채운다. 이유도 알려 준다.
        self.assertEqual({t['from'] for t in recommended['result']['transfers']}, {'SPARE'})
        self.assertTrue(any('남은 기간을 확인하지 못해' in note for note in comparison['notes']))
        direct = simulate_funding(ctx, envelope(item(amount=30000), item('BEAUTY', 30000, 'down')), allocate_group_reduction=allocation)
        self.assertEqual(direct['status'], 'proposed')  # 사용자가 직접 정한 감액은 그대로 가능하다.

    def test_without_history_living_moves_first_by_budget_pace_not_savings(self):
        """기록이 부족해도 남은 날을 알면 '이미 쓴 돈 + 배정액÷30×남은 날'을 남기고 생활비 안에서 먼저 옮긴다 (11/14 저축 1순위 버그)"""
        from budgets.adjustment_core.protection import usage_floors
        ctx = context()
        ctx.pop('usage_floors')
        ctx.update(today='2026-11-10', next_cycle_starts_on='2026-11-21')  # 남은 10일
        self.assertEqual(usage_floors(ctx)['BEAUTY'], 20000)  # 0 + ceil(50,000÷30×10)=16,667 → 5천 원 올림
        recommended = self.plans(ctx, item(amount=50000))['plans'][0]
        self.assertTrue(recommended['recommended'])
        self.assertTrue({t['from'] for t in recommended['result']['transfers']} <= set(ctx['allocations']))
        self.assertEqual(recommended['sources']['SAVINGS'], 0)

    def test_usage_floor_keeps_what_the_user_usually_spends_until_payday(self):
        from budgets.adjustment_core.protection import usage_floors
        ctx = self.history_context(other=2000, days=10)
        floors = usage_floors(ctx)
        self.assertEqual(floors['BEAUTY'], 20000)
        self.assertEqual(floors['FASHION_SHOPPING'], 30000)  # 이미 쓴 1만 원 + 평소 2만 원
        result = self.plans(ctx, item(amount=100000))['plans'][0]['result']
        for code in ('BEAUTY', 'CULTURE_LEISURE', 'HOUSEHOLD', 'MEDICAL_HEALTH'):
            self.assertGreaterEqual(result['allocations'][code], floors[code])
        for code, rank in ctx['priorities'].items():
            if code != 'FOOD':
                self.assertEqual(result['allocations'][code], ctx['allocations'][code])

    def test_living_first_then_only_the_shortfall_from_spare_cushion_and_savings_last(self):
        ctx = self.history_context()
        comparison = self.plans(ctx, item(amount=100000))
        self.assertEqual(comparison['recommendation']['plan_id'], 'living')
        self.assertEqual(comparison['plans'][0]['result']['buckets'], ctx['buckets'])
        comparison = self.plans(ctx, item(amount=250000))
        plan = comparison['plans'][0]
        self.assertEqual(plan['id'], 'spare')
        used = plan['sources']
        self.assertEqual(used['LIVING'], 160000)
        self.assertEqual(used['SPARE'], 90000)
        self.assertEqual(used['CUSHION_MONTHLY'] + used['SAVINGS'], 0)
        comparison = self.plans(ctx, item(amount=600000))
        recommended = next(p for p in comparison['plans'] if p['recommended'])
        self.assertEqual(recommended['sources']['SAVINGS'], 0)
        self.assertGreater(recommended['sources']['CUSHION_MONTHLY'], 0)
        self.assertGreaterEqual(recommended['result']['buckets']['SPARE'], 120000)  # 여윳돈 30% 유지
        alternative = next(p for p in comparison['plans'] if p['sources']['SAVINGS'])
        self.assertIn('저축까지 줄여야', alternative['assessment']['cautions'][0])

    def test_large_increase_warning_explains_what_else_must_shrink(self):
        ctx = self.history_context()
        ctx['monthly_income'] = 3000000
        warning = large_increase_warning(ctx, [item(amount=1000000)])
        self.assertIn('약 33%', warning)
        self.assertIn('부족해요', warning)
        self.assertIn('어떤 지출에 얼마', warning)
        ctx['monthly_income'] = 2000000
        warning = large_increase_warning(ctx, [item(amount=600000)])
        self.assertIn('다른 생활비에서는 최대 16만 원만', warning)

    def test_next_cycle_keeps_a_month_of_usual_spending(self):
        from budgets.adjustment_schedule import planning_context
        ctx = self.history_context(other=2000)
        ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        planned = planning_context(ctx, {'scope': 'next_cycle'})
        self.assertEqual(planned['usage_floors']['BEAUTY'], 60000)  # 하루 2천 원 × 30일

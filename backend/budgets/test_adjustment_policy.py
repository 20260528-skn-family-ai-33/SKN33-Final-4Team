"""Same-group proportional cuts: units, caps and stable rounding without DB or LLM."""
from copy import deepcopy
from fractions import Fraction
import json
from random import Random
import unittest

from budgets.adjustment_core.calculation import AdjustmentError, proportional_group_reduction
from budgets.adjustment_core.funding import simulate_funding, validate_funding
from budgets.test_adjustment_core import context, envelope, item


class ProportionalGroupTest(unittest.TestCase):
    def test_json_storage_key_order_keeps_the_verified_proposal_identical(self):
        inp = context()
        request = envelope(item(amount=30000), sources=["LIVING"])
        result = simulate_funding(inp, request, allocate_group_reduction=proportional_group_reduction)
        stored = json.loads(json.dumps(result, sort_keys=True))
        validate_funding(inp, request, stored)
        restored_context = json.loads(json.dumps(inp, sort_keys=True))
        self.assertEqual(simulate_funding(restored_context, request,
            allocate_group_reduction=proportional_group_reduction), stored)

    def test_adjustment_has_no_initial_design_twenty_percent_cap(self):
        inp = context()
        inp["ml_reference"] = {c: 0 for c in inp["allocations"] if c != "ETC"}
        result = simulate_funding(inp, envelope(item(amount=300000), sources=["LIVING"]),
                                  allocate_group_reduction=proportional_group_reduction)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"]["FOOD"], 700000)
        self.assertEqual(sum(row["amount"] for row in result["transfers"]), 300000)

    def cut(self, capacities, required, group=None):
        original = deepcopy(capacities)
        group = list(capacities) if group is None else group
        result = proportional_group_reduction(group, required, capacities)
        self.assertEqual(capacities, original)
        self.assertEqual(set(result["reductions"]), set(capacities))
        self.assertEqual(sum(result["reductions"].values()) + result["remaining"], required)
        for code, amount in result["reductions"].items():
            self.assertEqual(amount % 5000, 0)
            self.assertLessEqual(amount, capacities[code])
        return result

    def test_capacity_ratio_not_category_order_drives_the_split(self):
        capacities = {"HOUSEHOLD": 60000, "CULTURE_LEISURE": 30000}
        self.assertEqual(self.cut(capacities, 30000)["reductions"],
                         {"HOUSEHOLD": 20000, "CULTURE_LEISURE": 10000})

    def test_whole_share_units_are_preserved_and_half_units_keep_proportions(self):
        self.assertEqual(self.cut({"A": 10000, "B": 5000}, 10000)["reductions"], {"A": 5000, "B": 5000})
        self.assertEqual(self.cut({"A": 15000, "B": 5000}, 15000)["reductions"], {"A": 10000, "B": 5000})
        self.assertEqual(self.cut({"A": 5000, "B": 5000}, 10000)["reductions"], {"A": 5000, "B": 5000})

    def test_small_capacities_are_not_excluded_to_fill_one_larger_category(self):
        capacities = {"A": 100000, **{code: 5000 for code in "BCDEFGHIJ"}}
        result = self.cut(capacities, 100000)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(sum(result["reductions"].values()), 100000)
        self.assertEqual(result["reductions"]["A"], 70000)
        self.assertEqual(sum(result["reductions"][c] for c in "BCDEFGHIJ"), 30000)
        self.assertEqual(result, self.cut(capacities, 100000, list(reversed(capacities))))

    def test_rounding_ties_are_independent_of_input_order(self):
        capacities = {"C": 30000, "A": 30000, "B": 30000}
        for required in (5000, 10000, 15000, 35000, 85000, 100000):
            with self.subTest(required=required):
                forward = self.cut(capacities, required)
                reverse = self.cut(dict(reversed(capacities.items())), required)
                self.assertEqual(forward, reverse)

    def test_partial_capacity_and_subunit_remainder_stay_unfilled(self):
        self.assertEqual(self.cut({"A": 6000, "B": 4000}, 10000),
                         {"reductions": {"A": 5000, "B": 0}, "remaining": 5000})
        self.assertEqual(self.cut({"A": 15555}, 35555),
                         {"reductions": {"A": 15000}, "remaining": 20555})
        self.assertEqual(self.cut({}, 10000), {"reductions": {}, "remaining": 10000})
        self.assertEqual(self.cut({"A": 0}, 0), {"reductions": {"A": 0}, "remaining": 0})

    def test_large_integer_capacity_keeps_exact_proportions(self):
        unit = 10 ** 20
        result = self.cut({"A": 6 * unit, "B": 3 * unit}, 3 * unit)
        self.assertEqual(result["reductions"], {"A": 2 * unit, "B": unit})

    def test_invalid_money_or_group_is_rejected(self):
        cases = [(["A"], True, {"A": 5000}), (["A"], 5000.0, {"A": 5000}),
                 (["A"], -1, {"A": 5000}), (["A"], 5000, {"A": True}),
                 (["A"], 5000, {"A": Fraction(5000)}), (["A"], 5000, {"A": -1}),
                 (["A"], 5000, {}), (["A"], 5000, {"A": 5000, "B": 5000}),
                 (["A", "A"], 5000, {"A": 5000}), ([None], 5000, {None: 5000})]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(AdjustmentError):
                proportional_group_reduction(*args)

    def test_random_caps_conserve_money_and_fill_every_available_unit(self):
        rng = Random(20261106)
        for _ in range(400):
            capacities = {str(c): rng.randrange(200000) for c in range(rng.randrange(1, 10))}
            required = rng.randrange(1000000)
            result = self.cut(capacities, required)
            expected = min(required // 5000, sum(v // 5000 for v in capacities.values())) * 5000
            self.assertEqual(sum(result["reductions"].values()), expected)
            self.assertEqual(result, self.cut(capacities, required, list(reversed(capacities))))

    def test_current_group_half_units_are_spent_before_the_next_rank(self):
        inp = context()
        inp["allocations"] = {"FOOD": 100000, "HOUSEHOLD": 15000, "CULTURE_LEISURE": 5000,
                              "EDUCATION": 100000}
        inp["funding_amount"] = inp["fixed_amount"] + sum(inp["allocations"].values()) + sum(inp["buckets"].values())
        inp["spent"], inp["minimums"], inp["system_floors"] = {}, {}, {}
        inp["priorities"] = {"FOOD": 1, "EDUCATION": 3}
        inp["ml_reference"] = {c: 0 for c in inp["allocations"]}
        result = simulate_funding(inp, envelope(item(amount=15000), sources=["LIVING"]),
                                  allocate_group_reduction=proportional_group_reduction)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["allocations"], {"FOOD": 115000, "HOUSEHOLD": 5000,
                                                 "CULTURE_LEISURE": 0, "EDUCATION": 100000})


if __name__ == "__main__":
    unittest.main()

"""Input-to-money and GUI smoke checks for the local budget simulator."""
import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import time
import queue
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "budget_design" / "simulator.py"
spec = importlib.util.spec_from_file_location("budget_simulator", SCRIPT)
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def action_message(category, action):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'test_call', 'type': 'function', 'function': {
            'name': 'adjust_budget', 'arguments': json.dumps({'category': category, 'action': action})}}]}


class SimulatorTests(unittest.TestCase):
    def test_transaction_list_parser_privacy_and_totals(self):
        rows=sim.transaction_list('10000, 170000@병원A',month='2026-09',category='MEDICAL_HEALTH')
        self.assertEqual(sum(r['net_amount'] for r in rows),180000)
        self.assertEqual(rows[1]['merchant_key'],'병원a')
        self.assertEqual(sim.transaction_list('',month='2026-09',category='FOOD'),[])
        for text in ('secret@@clinic','10000@','10000,,20000','-1','NaN','1.5'):
            with self.subTest(text=text),self.assertRaises(ValueError) as error:
                sim.transaction_list(text,month='2026-09',category='FOOD')
            self.assertNotIn('secret',str(error.exception))

    def test_llm_selection_changes_final_money_to_selected_candidate_without_prose(self):
        form = sim.example()
        form['profile']['car'] = '있음'
        normal = sim.calculate(form)
        with patch('budgets.design_selection._request_action',
                   side_effect=lambda messages, category, key, timeout: action_message(category, 'finish')), \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key'}):
            result = sim.calculate(form, use_llm=True)
        self.assertEqual(result['engine'], normal['engine'])
        self.assertNotEqual(result['selected']['allocations'], normal['selected']['allocations'])
        self.assertEqual(result['selected']['allocations'], result['engine']['candidates'][0]['result']['allocations'])
        self.assertEqual(result['selected']['selection_source'], 'llm_tools')
        from budgets.test_design_explain import assert_ledger
        assert_ledger(self, result['selected']['explanations'], result['selected']['allocations'])
        self.assertTrue(all(not e['text'] or e['text'] in sim.describe(result) for e in result['selected']['explanations']))
        self.assertNotIn('reasons', result['selected'])
        self.assertNotIn('summary', result['selected'])
        self.assertNotIn('옮겼어요', sim.describe(result))

    def test_llm_runs_with_system_floor_and_keeps_transport(self):
        form = sim.example()
        form['rows']['TRANSPORT_CAR']['draft'] = '0'
        form['rows']['FOOD']['draft'] = '400000'
        with patch('budgets.design_selection._request_action',
                   side_effect=lambda messages, category, key, timeout: action_message(category, 'increase')) as request, \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key', 'LANGSMITH_TRACING': 'false'}):
            result = sim.calculate(form, use_llm=True)
        self.assertTrue(request.called)
        self.assertEqual(result['selected']['selection_source'], 'llm_tools')
        from budgets.test_design_explain import assert_ledger
        assert_ledger(self, result['selected']['explanations'], result['selected']['allocations'])
        self.assertTrue(all(not e['text'] or e['text'] in sim.describe(result) for e in result['selected']['explanations']))
        self.assertEqual(result['selected']['allocations']['TRANSPORT_CAR'], 55000)
        self.assertEqual(result['selected']['system_floors'], {'TRANSPORT_CAR': 55000})
        self.assertIsNone(result['selected']['fallback'])

    def test_system_floor_display_and_unaffordable(self):
        form = sim.example()
        form['rows']['TRANSPORT_CAR']['draft']='0'
        form['rows']['FOOD']['draft']='400000'
        out=sim.calculate(form)
        self.assertEqual(out['engine']['system_floors'], {'TRANSPORT_CAR':55000})
        self.assertIn('TRANSPORT_CAR',out['engine']['floor_applied'])
        self.assertIn('시스템 하한',sim.describe(out))
        form['living']='100000'
        form['etc_minimum']='50000'
        for row in form['rows'].values():row['draft']='0'
        form['rows']['FOOD']['draft']='50000'
        out=sim.calculate(form)
        self.assertEqual(out['engine']['reason'],'system_floor_unaffordable')
        self.assertIn('5,000원',sim.describe(out))

    def test_llm_skipped_on_conflict_and_disabled_by_default(self):
        sim.calculate(sim.example())
        with patch('budgets.design_selection._request_action') as request:
            sim.calculate(sim.example())
            form = sim.example()
            for code in ('FOOD', 'EDUCATION'):
                form['rows'][code]['minimum'] = '600000'
            self.assertIsNone(sim.calculate(form, use_llm=True)['selected'])
        request.assert_not_called()

    def test_window_llm_checkbox_runs_in_background_and_shows_fallback(self):
        try:
            root = sim.tk.Tk()
        except sim.tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            app = sim.Simulator(root)
            app.profile['car'].set('있음')
            self.assertFalse(app.use_llm.get())
            app.use_llm.set(True)
            sim.calculate(sim.example())
            with patch('budgets.design_selection._request_action', side_effect=TimeoutError), \
                    patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key'}):
                app.run()
                deadline = time.monotonic() + 10
                while app.pending is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
            self.assertIsNone(app.pending)
            self.assertEqual(len(app.table.get_children()), 11)
            self.assertIn('adjustment_failed:TimeoutError', app.details.get('1.0', 'end'))
            self.assertIn('rule_fallback', app.details.get('1.0', 'end'))
            self.assertEqual(app.table.item(app.table.get_children()[0], 'values')[-1], '360,000')
            # Final recommendation must follow LLM base selection, not the last candidate.
            with patch('budgets.design_selection._request_action', side_effect=lambda messages, category, key, timeout: action_message(category, 'finish')), \
                    patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key'}):
                app.run()
                deadline = time.monotonic() + 10
                while app.pending is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
            self.assertIsNone(app.pending)
            self.assertEqual(app.table.item(app.table.get_children()[0], 'values')[-1], '300,000')
            self.assertEqual(app.table.item(app.table.get_children()[-1], 'values')[-1], '1,050,000')
            self.assertNotIn('옮겼어요', app.details.get('1.0', 'end'))
        finally:
            root.destroy()

    def ml_result(self, target=1000000):
        # Fractional draft values must not be rounded before percentage targets.
        amounts = dict.fromkeys(sim.CATEGORIES.values(), 0.0)
        amounts.update({'식비':12499.5,'생활용품':target-12499.5})
        return {'model_name':'catboost_shallow', 'artifact_id':'20261026T074827Z_53769d64',
                'living_budget_amount':target, 'draft_amounts':amounts,
                'ratios':{k:v/target for k,v in amounts.items()},
                'used_fallback':False, 'negative_clipped':False}

    def test_ml_uses_target_mapping_and_preserves_fractional_draft(self):
        from django.db.backends.base.base import BaseDatabaseWrapper
        form = sim.example()
        form['rows']['FOOD']['draft'] = 'ignored in ML mode'
        with patch.object(BaseDatabaseWrapper, 'connect', side_effect=AssertionError('No DB allowed')):
            with patch.object(sim,'predict_ml',return_value=self.ml_result()) as predict:
                result = sim.calculate(form,use_ml=True)
        predict.assert_called_once_with({'age':27,'sex':1,'has_vehicle':0,
                                        'disposable_income':3000000,'fixed_expense_amount':700000,
                                        'living_budget_amount':1000000})
        self.assertEqual(result['draft']['FOOD'],12499.5)
        self.assertEqual(result['engine']['increase_targets']['FOOD'],2000)
        self.assertEqual(result['source'],'ml')
        self.assertEqual(sum(result['engine']['candidates'][-1]['result']['allocations'].values()),1000000)

    def test_ml_errors_timeout_and_bad_response_have_no_fabricated_result(self):
        self.enterContext(patch.object(sim.Path, 'is_file', return_value=True))
        mapped = {'living_budget_amount':1000000}
        with patch.object(sim.subprocess,'run',side_effect=subprocess.TimeoutExpired('worker',30)):
            with self.assertRaisesRegex(ValueError,'30초'): sim.predict_ml(mapped)
        with patch.object(sim.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','private')):
            with self.assertRaisesRegex(ValueError,'실패'): sim.predict_ml(mapped)
        with patch.object(sim.Path,'is_file',return_value=False):
            with self.assertRaisesRegex(ValueError,'실행 환경이 없습니다'): sim.predict_ml(mapped)
        valid = self.ml_result()
        bad = [None, {**valid,'artifact_id':'wrong'}, {**valid,'ratios':{}},
               {**valid,'draft_amounts':{**valid['draft_amounts'],'식비':float('nan')}},
               {**valid,'draft_amounts':{**valid['draft_amounts'],'식비':12499.0}}]
        for value in bad:
            with self.subTest(value=value),patch.object(sim.subprocess,'run',return_value=
                    subprocess.CompletedProcess([],0,json.dumps(value),'')):
                with self.assertRaisesRegex(ValueError,'ML 응답'): sim.predict_ml(mapped)

    def test_ml_skipped_for_zero_target_conflicts_or_all_locks(self):
        zero=sim.example();zero['living']=zero['etc_minimum']='0'
        conflict=sim.example()
        for code in ('FOOD','EDUCATION'): conflict['rows'][code]['minimum']='600000'
        locked=sim.example()
        locked['living']='850000'
        for code, row in locked['rows'].items():
            if code not in ('TRAVEL_LODGING', 'HOUSING_TELECOM'): row['lock']='100000'
        with patch.object(sim,'predict_ml',side_effect=AssertionError('Must not call ML')):
            for form in (zero,conflict,locked):
                self.assertEqual(sim.calculate(form,use_ml=True)['source'],'ml_skipped')

    def test_unsupported_profile_or_travel_priority_rejected_before_ml(self):
        with patch.object(sim,'predict_ml') as predict:
            for field,value in (('age','35'),('sex',''),('car',''),('income','0')):
                form=sim.example();form['profile'][field]=value
                with self.subTest(field=field),self.assertRaises(ValueError):
                    sim.calculate(form,use_ml=True)
            form=sim.example();form['rows']['TRAVEL_LODGING']['rank']='4'
            with self.assertRaisesRegex(ValueError,'여행'): sim.calculate(form,use_ml=True)
        predict.assert_not_called()

    def test_travel_minimum_and_lock_rejected_before_ml(self):
        with patch.object(sim, 'predict_ml') as predict:
            for field, value in (('minimum', '50000'), ('lock', '80000'), ('lock', '0')):
                form = sim.example()
                form['rows']['TRAVEL_LODGING'][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '여행'):
                    sim.calculate(form, use_ml=True)
            predict.assert_not_called()

    def test_housing_minimum_and_lock_rejected_before_ml(self):
        with patch.object(sim, 'predict_ml') as predict:
            for field, value in (('minimum', '50000'), ('lock', '80000'), ('lock', '0')):
                form = sim.example()
                form['rows']['HOUSING_TELECOM'][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '주거'):
                    sim.calculate(form, use_ml=True)
            predict.assert_not_called()

    def test_window_runs_real_ml_and_rejects_stale_results(self):
        import os
        python = Path(os.getenv('BUDGET_INFERENCE_PYTHON', str(sim.ROOT / 'local_workspace/.venv-budget-inference/Scripts/python.exe')))
        model = Path(os.getenv('BUDGET_MODEL_PATH', str(sim.ROOT / 'local_workspace/models/living_expense_prediction/models/shallow/catboost_allocation/budget_allocation.joblib')))
        if not python.is_file() or not model.is_file():
            self.skipTest('Actual ML smoke test requires the separately shared model and inference environment')
        from django.db.backends.base.base import BaseDatabaseWrapper
        try:
            root=sim.tk.Tk()
        except sim.tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            app=sim.Simulator(root)
            with patch.object(BaseDatabaseWrapper,'connect',side_effect=AssertionError('No DB allowed')):
                app.run_ml()
                deadline=time.monotonic()+45
                while app.pending is not None and time.monotonic()<deadline:
                    root.update()
                    time.sleep(0.02)
            self.assertIsNone(app.pending, 'ML window did not finish')
            self.assertEqual(len(app.table.get_children()),11,app.details.get('1.0','end'))
            self.assertIn('실제 ML',app.status.get())
            self.assertIn('20261026T074827Z_53769d64',app.details.get('1.0','end'))
            self.assertEqual(app.table.item(app.table.get_children()[-1],'values')[2:4],('1,050,000','1,050,000'))
            # Changed inputs cannot receive an old async result, even if valid.
            old=app.form()
            ready=sim.calculate(old)
            app.clear()
            app.pending=queue.Queue();app.pending.put((ready,None))
            app.top['living'].set('2000000')
            app.poll_ml(old)
            self.assertEqual(len(app.table.get_children()),0)
            self.assertIn('폐기',app.status.get())
            app.pending=queue.Queue();app.pending.put((None,'ML 실행에 실패했습니다.'))
            app.poll_ml(app.form())
            self.assertEqual(len(app.table.get_children()),0)
            self.assertIn('실패',app.details.get('1.0','end'))
        finally:
            root.destroy()

    def test_example_money_flows_through_real_engine_without_db(self):
        from django.db.backends.base.base import BaseDatabaseWrapper
        with patch.object(BaseDatabaseWrapper, "connect", side_effect=AssertionError("No DB allowed")):
            out = sim.calculate(sim.example())
        base, adjusted = [c["result"]["allocations"] for c in out["engine"]["candidates"]]
        self.assertEqual(base["FOOD"], 300000)
        self.assertEqual(adjusted["FOOD"], 360000)
        self.assertEqual(adjusted["EDUCATION"], 120000)
        self.assertEqual(adjusted["MEDICAL_HEALTH"], 120000)
        for code in ('FASHION_SHOPPING','BEAUTY'):
            self.assertEqual(adjusted[code], 50000)
        self.assertEqual(adjusted['CULTURE_LEISURE'],100000)
        self.assertEqual(adjusted['TRAVEL_LODGING'],0)
        self.assertEqual(sum(adjusted.values()) + out["etc"], 1050000)
        self.assertIn("100,000", sim.describe(out))
        self.assertEqual(out['engine']['increase_targets'],{'FOOD':60000,'EDUCATION':20000,'MEDICAL_HEALTH':20000})

    def test_nonzero_travel_draft_is_redistributed_and_not_reused_as_donor(self):
        form = sim.example()
        form['rows']['FOOD']['draft'] = '200000'
        form['rows']['TRAVEL_LODGING']['draft'] = '100000'
        for row in form['rows'].values():
            row['rank'] = ''
        for code, rank in (('FASHION_SHOPPING','1'), ('BEAUTY','2'), ('TRANSPORT_CAR','3')):
            form['rows'][code]['rank'] = rank
        form['rows']['TRANSPORT_CAR']['minimum'] = '200000'
        form['rows']['TRANSPORT_CAR']['lock'] = '200000'
        out = sim.calculate(form)
        self.assertNotIn('TRAVEL_LODGING',out['engine']['active_categories'])
        for candidate in out['engine']['candidates']:
            self.assertEqual(candidate['result']['allocations']['TRAVEL_LODGING'],0)
            self.assertEqual(sum(candidate['result']['allocations'].values()),1000000)
        self.assertTrue(all(t['from'] != 'TRAVEL_LODGING' for t in out['engine']['transfers']))

    def test_beauty_warning_input_is_forwarded(self):
        form = sim.example()
        form['rows']['BEAUTY']['minimum'] = '300000'
        form['rows']['BEAUTY']['warning'] = '200000'
        out = sim.calculate(form)
        self.assertTrue(any(w.get('category') == 'BEAUTY' and w['code'] == 'high_minimum'
                            for w in out['engine']['warnings']))
        self.assertIn('뷰티·미용 최소 요청액이 경고선',sim.describe(out))

    def test_percentage_input_preserves_exact_rounding_and_rejects_bad_rates(self):
        form = sim.example()
        for row in form['rows'].values():
            row['draft'] = '0'
        form['rows']['FOOD']['draft'] = '12500'
        form['rows']['HOUSEHOLD']['draft'] = '987500'
        form['rows']['FOOD']['rate'] = '20.0'
        out = sim.calculate(form)
        self.assertEqual(out['engine']['increase_targets']['FOOD'],3000)
        for value in ('NaN','Infinity','-1','잘못된 값',''):
            form['rows']['FOOD']['rate'] = value
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'증액률'):
                sim.calculate(form)

    def test_total_mismatch_and_bad_priority_are_explained(self):
        form = sim.example()
        form["rows"]["FOOD"]["draft"] = "1"
        with self.assertRaisesRegex(ValueError, "초안 합계"):
            sim.calculate(form)
        form = sim.example()
        form["rows"]["FOOD"]["rank"] = "2"
        with self.assertRaisesRegex(ValueError, "순위"):
            sim.calculate(form)

    def test_etc_minimum_and_lock_conflicts(self):
        form = sim.example()
        form["etc_lock"] = "40000"
        with self.assertRaisesRegex(ValueError, "기타 잠금"):
            sim.calculate(form)
        form["etc_lock"] = "2000000"
        with self.assertRaisesRegex(ValueError, "총생활비"):
            sim.calculate(form)

    def test_minimum_conflict_displays_shortfall(self):
        form = sim.example()
        for code in ("FOOD", "EDUCATION"):
            form["rows"][code]["minimum"] = "600000"
        out = sim.calculate(form)
        self.assertEqual(out["engine"]["candidates"], [])
        self.assertIn("200,000원", sim.describe(out))

    def test_missing_limit_does_not_invent_a_value(self):
        form = sim.example()
        form["rows"]["EDUCATION"]["increase"] = ""
        with self.assertRaisesRegex(ValueError, "증액 한도"):
            sim.calculate(form)

    def test_zero_budget_and_comma_amounts(self):
        self.assertEqual(sim.money(" 1,000 ", "금액"), 1000)
        for text in ("-1", "1.5", "만원", ""):
            with self.assertRaises(ValueError):
                sim.money(text, "금액")
        form = sim.example()
        form["living"] = form["etc_minimum"] = "0"
        form["profile"]["car"] = "있음"
        for row in form["rows"].values():
            row["draft"] = "0"
        out = sim.calculate(form)
        self.assertEqual(sum(out["engine"]["candidates"][0]["result"]["allocations"].values()), 0)

    def test_window_calculates_and_clears_stale_results_on_error(self):
        try:
            root = sim.tk.Tk()
        except sim.tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            app = sim.Simulator(root)
            app.run()
            self.assertEqual(len(app.table.get_children()), 11)
            travel_row = app.table.item(app.table.get_children()[9], 'values')
            self.assertEqual(travel_row[0], '여행·숙박 (제외)')
            self.assertEqual(travel_row[2:4], ('0', '0'))
            self.assertIn("100,000", app.details.get("1.0", "end"))
            app.rows["FOOD"]["draft"].set("bad")
            app.run()
            self.assertEqual(len(app.table.get_children()), 0)
            self.assertIn("정수", app.details.get("1.0", "end"))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

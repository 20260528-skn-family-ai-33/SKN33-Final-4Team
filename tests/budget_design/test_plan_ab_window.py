"""Editable synthetic GUI input and rendering tests; no real LLM or database."""
from copy import deepcopy
import os
from pathlib import Path
import queue
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
import compare_window as gui
from test_plan_ab_comparison import ml_response


class FormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def test_spare_is_explicit_nonnegative_and_not_inferred_from_income(self):
        form = gui.example_form()
        self.assertEqual(gui.parse_form(form)['available_spare'], 0)
        form['spare'] = '150000'
        self.assertEqual(gui.parse_form(form)['available_spare'], 150000)
        for value in ('-1', '1.5', '3000000'):
            form['spare'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                gui.parse_form(form)

    def test_round_trip_values_and_unchanged_form(self):
        form = gui.example_form()
        original = deepcopy(form)
        case = gui.parse_form(form)
        self.assertEqual(form, original)
        self.assertEqual(case['input'], gui.test_input()['input'])
        for month, data in case['months'].items():
            self.assertEqual({c:v for c,v in data['monthly_totals'].items() if v},
                             {i['category']: i['net_amount'] for i in gui.test_input()['months'][month]['items']})
        self.assertEqual(case['increase_threshold'], 0.1)
        self.assertEqual(case['profile']['has_vehicle'], False)

    def test_forecast_fields_explicit_band_and_independent_enable(self):
        form = gui.example_form()
        self.assertIsNone(gui.parse_form(form)['forecast_band'])
        form.update(target_month='12', forecast_down='20', forecast_up='30',
                    user_note='다음 달 이사해요', forecast_enabled=True, forecast_text_limit='80')
        case = gui.parse_form(form)
        self.assertEqual(case['forecast_band'], {'down_percent': 20, 'up_percent': 30})
        self.assertEqual(case['target_month'], 12)
        self.assertEqual(case['forecast_text_limit'], 80)
        self.assertEqual(case['user_note'], '다음 달 이사해요')
        with patch('compare_plans.predict_ml', side_effect=ml_response), \
                patch('compare_plans.run_a', return_value={'status':'proposed'}), \
                patch('compare_plans.run_b', return_value={'status':'proposed'}) as rule, \
                patch('compare_plans.run_forecast', return_value={'status':'proposed'}) as forecast:
            result = gui.compare(case, use_llm=True)
        self.assertIn('b_forecast', result)
        self.assertFalse(rule.call_args.args[1])
        self.assertEqual(forecast.call_args.args[0]['forecast_band'], case['forecast_band'])
        for update in ({'target_month':'13'}, {'forecast_down':'101'}, {'forecast_up':''}, {'forecast_text_limit':'0'}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                gui.parse_form({**form, **update})

    def test_typed_money_history_settings_and_policy_are_used(self):
        form = gui.example_form()
        form.update(living='1,200,000', etc='50,000', threshold='20')
        form['rows']['BEAUTY'].update(m0='12,345', m1='23,456', m2='34,567', lock='0', cut='25')
        form['rows']['FOOD'].update(minimum='410000', rate='15', increase='25000', ceiling='700000')
        case = gui.parse_form(form)
        self.assertEqual(case['input']['target'], 1150000)
        self.assertEqual(case['input']['locks']['BEAUTY'], 0)
        self.assertEqual(case['input']['minimums']['FOOD'], 410000)
        self.assertEqual(case['a_policy']['increase_rates']['FOOD'], 15)
        self.assertEqual(case['b_cut_rates']['BEAUTY'], 25)
        self.assertEqual(case['increase_threshold'], .2)
        amounts = [m['monthly_totals']['BEAUTY'] for m in case['months'].values()]
        self.assertEqual(amounts, [12345, 23456, 34567])

    def test_invalid_inputs_rejected_before_model_calls(self):
        for field, value in (('living', '-1'), ('age', '35'), ('income', '0'),
                             ('threshold', 'NaN'), ('threshold', 'Infinity'), ('threshold', '-1')):
            form = gui.example_form()
            form[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                gui.parse_form(form)
        for category, field, value in (('FOOD', 'rank', '2'), ('FOOD', 'm0', ''),
                                      ('BEAUTY', 'cut', '0'), ('BEAUTY', 'cut', '101'),
                                      ('TRAVEL_LODGING', 'rank', '4'),
                                      ('HOUSING_TELECOM', 'lock', '0'), ('TRAVEL_LODGING', 'minimum', '1')):
            form = gui.example_form()
            form['rows'][category][field] = value
            with self.subTest(category=category, field=field), self.assertRaises(ValueError):
                gui.parse_form(form)

    def test_real_engines_receive_edited_input(self):
        form = gui.example_form()
        form['rows']['FOOD']['minimum'] = '450000'
        with patch('compare_plans.predict_ml', side_effect=ml_response), \
                patch('socket.socket', side_effect=AssertionError('No network')), \
                patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')):
            result = gui.compare(gui.parse_form(form))
        for plan in ('a', 'b'):
            self.assertEqual(result[plan]['status'], 'proposed')
            self.assertGreaterEqual(result[plan]['allocations']['FOOD'], 450000)
            self.assertEqual(sum(result[plan]['allocations'].values()), 1000000)

    def test_transaction_mode_uses_lists_not_stale_totals(self):
        form = gui.example_form()
        form['input_mode']='transactions'
        form['rows']['MEDICAL_HEALTH'].update(t0='10000',t1='10000, 170000@병원A',t2='',m1='999999')
        form['rows']['CULTURE_LEISURE'].update(t0='10000',t1='10000, 183000',t2='23000')
        form['rows']['FASHION_SHOPPING'].update(t0='30000',t1='40000, 50000, 300000',t2='')
        form['rows']['BEAUTY'].update(t0='40000',t1='20000, 400000',t2='')
        case=gui.parse_form(form)
        from plan_b_history import summarize_history
        out=summarize_history(case['months'],as_of=case['as_of'])
        self.assertEqual(out['categories']['MEDICAL_HEALTH']['monthly_amounts'],[10000,180000,0])
        candidate=next(c for c in out['one_off_candidates'] if c['category']=='MEDICAL_HEALTH')
        self.assertEqual(candidate['amount'],170000)
        self.assertEqual(candidate['detail']['merchant_check'],'available')
        culture=next(c for c in out['one_off_candidates'] if c['category']=='CULTURE_LEISURE')
        self.assertEqual((culture['amount'],culture['method'],culture['excluded']),(183000,'dominance',True))
        for code in ('FASHION_SHOPPING','BEAUTY'):
            self.assertFalse(any(c['category']==code for c in out['one_off_candidates']))
            self.assertEqual(out['categories'][code]['baseline_amount'],out['categories'][code]['average'])
        self.assertNotIn('병원A',str(out))


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FormTests.setUpClass()

    # Run this test in its own process: Tk objects must be finalized on the main thread.
    def test_window_comparison_stale_and_failure_states(self):
        root = gui.tk.Tk()
        root.withdraw()
        try:
            app = gui.ComparisonWindow(root)
            with patch('compare_plans.predict_ml', side_effect=ml_response):
                app.run()
                deadline = time.monotonic() + 10
                while app.pending is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
            self.assertIsNone(app.pending)
            self.assertEqual(len(app.table.get_children()), 13)
            totals = app.table.item(app.table.get_children()[-3], 'values')
            self.assertEqual(totals[4:6], ('1,000,000', '1,000,000'))
            self.assertIn('A: 계산 완료', app.status.get())
            self.assertIn('forecast', app.table['columns'])
            self.assertIn('예측 하향·상향 범위 입력 필요', app.status.get())
            self.assertNotIn('LLM 미실행', app.status.get())
            snapshot = app.form()
            app.clear()
            app.pending = queue.Queue()
            app.pending.put(({}, None))
            app.fields['living'].set('999999')
            app.poll(snapshot)
            self.assertIn('폐기', app.status.get())
            self.assertEqual(len(app.table.get_children()), 0)
            app.rows['FOOD']['m0'].set('bad')
            with patch.object(gui, 'compare') as compare:
                app.run()
            compare.assert_not_called()
            self.assertIn('정수', app.status.get())
            app.presets.current(1)
            app.load_example()
            with patch('compare_plans.predict_ml', side_effect=ml_response):
                out = gui.compare(gui.parse_form(app.form()))
            app.show_result(out)
            self.assertIn('B 규칙: 계산 완료', app.status.get())
            free = app.table.item(app.table.get_children()[-1], 'values')
            self.assertEqual(free[0], '남는 여윳돈')
            self.assertEqual(free[5], f"{out['b']['unallocated']:,}")
            self.assertIn('여윳돈으로 남겼어요', app.details.get('1.0', 'end'))
            app.fields['forecast_down'].set('20')
            app.fields['forecast_up'].set('20')
            app.forecast_enabled.set(True)
            from test_plan_b_forecast import ForecastTests
            with patch('compare_plans.predict_ml', side_effect=ml_response), \
                    patch('plan_b_forecast._request_completion', side_effect=ForecastTests.response):
                predicted = gui.compare(gui.parse_form(app.form()))
            app.show_result(predicted)
            self.assertEqual(predicted['b_forecast']['llm_status'], 'completed')
            self.assertTrue(all(amount % 5000 == 0 for code, amount in predicted['b_forecast']['allocations'].items()
                                if code not in gui.parse_form(app.form())['input']['locks']))
            food = app.table.item(app.table.get_children()[0], 'values')
            self.assertEqual(food[7], f"{predicted['b_forecast']['allocations']['FOOD']:,}")
            self.assertIn('B 예측: 시도 1', app.details.get('1.0', 'end'))
            self.assertIn('B LLM 예측: 호출 1회 · 검수 통과', app.status.get())
            blocked=deepcopy(predicted)
            blocked['b_forecast'].update(llm_status='fallback',reason='forecast_band_infeasible',
                forecast_required=1050000,forecast_shortfall=50000,attempts=0)
            app.show_result(blocked)
            self.assertIn('1,050,000', app.status.get())
            self.assertIn('50,000', app.status.get())
            self.assertIn('호출 안 됨', app.table.heading('forecast','text'))
            self.assertEqual(app.table.item(app.table.get_children()[0],'values')[7],'—')
            blocked['b_forecast'].update(reason='openai_key_missing',llm_status='not_run')
            app.show_result(blocked)
            self.assertIn('OPENAI_API_KEY 설정 없음', app.status.get())
            blocked['b_forecast'].update(attempts=2,reason=None,llm_status='fallback',violation_types=['sum_exceeded'])
            app.show_result(blocked)
            self.assertIn('호출 2회 · 결과 사용 불가 (sum_exceeded)', app.status.get())
            app.fields['living'].set('500000')
            app.fields['etc'].set('0')
            app.fields['spare'].set('200000')
            active = [c for c in gui.CATEGORIES if c not in gui.EXCLUDED]
            for code, row in app.rows.items():
                for key in ('m0', 'm1', 'm2'):
                    row[key].set('100000' if code in active else '0')
                row['minimum'].set('')
                row['lock'].set('')
                row['rank'].set(str(active.index(code) + 1) if code in active[:3] else '')
                if code in active:
                    row['cut'].set('25')
            with patch('compare_plans.predict_ml', side_effect=ml_response), \
                    patch('plan_b_forecast._request_completion', side_effect=ForecastTests.response):
                funded = gui.compare(gui.parse_form(app.form()))
            app.show_result(funded)
            self.assertEqual(funded['b']['spare_transfer'], 100000)
            self.assertEqual(funded['b_forecast']['spare_transfer'], 140000)
            transfer = app.table.item(app.table.get_children()[-2], 'values')
            free = app.table.item(app.table.get_children()[-1], 'values')
            self.assertEqual(transfer[5], '100,000')
            self.assertEqual(free[5], '100,000')
            self.assertEqual(free[7], '60,000')
            self.assertIn('기존 여윳돈 200,000원 / 생활비 충당 100,000원', app.details.get('1.0', 'end'))
        finally:
            root.destroy()

    def test_transaction_totals_candidates_and_reinclude(self):
        root=gui.tk.Tk(); root.withdraw()
        try:
            app=gui.ComparisonWindow(root)
            app.input_mode.set('transactions')
            app.change_mode()
            app.rows['MEDICAL_HEALTH']['t0'].set('10000')
            app.rows['MEDICAL_HEALTH']['t1'].set('10000, 170000@병원A')
            app.rows['MEDICAL_HEALTH']['t2'].set('')
            self.assertEqual(app.rows['MEDICAL_HEALTH']['m1'].get(),'180000')
            snapshot=app.form()
            with patch('compare_plans.predict_ml',side_effect=ml_response):
                result=gui.compare(gui.parse_form(snapshot))
            app.candidate_source=app.history_source(snapshot)
            app.show_result(result)
            candidate=next(c for c in result['b']['one_off_candidates'] if c['category']=='MEDICAL_HEALTH')
            self.assertIn(candidate['id'],app.reincluded)
            app.reincluded[candidate['id']].set(True)
            with patch('compare_plans.predict_ml',side_effect=ml_response):
                restored=gui.compare(gui.parse_form(app.form()))
            row=next(c for c in restored['b']['one_off_candidates'] if c['id']==candidate['id'])
            self.assertFalse(row['excluded'])
            app.show_result(restored)
            self.assertTrue(app.reincluded[candidate['id']].get())
        finally:
            root.destroy()

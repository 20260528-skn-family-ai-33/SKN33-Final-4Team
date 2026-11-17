from copy import deepcopy
from datetime import date, timedelta
import json
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .price_reference import PRICE_GUIDANCE, price_context, review_prices


def reference(**changes):
    return {'category': 'BEAUTY', 'item_group': 'haircut', 'unit': '1회',
            'reference_price': 35000, 'scope': 'local', 'region_code': '11110',
            'tier': 'everyday', 'sample_kind': 'general', 'as_of': date.today().isoformat(), **changes}


def snapshot():
    return {'version': 1, 'snapshot': 'synthetic-test-only', 'regions': {'11110': '테스트 지역'},
            'references': [reference()]}


class PriceReferenceTests(SimpleTestCase):
    def test_planner_schema_accepts_only_the_registered_price_guidance(self):
        from budgets.adjustment_core import financial_planner as planner
        from chat.llm.live import schema_for

        for prompt in (planner.PROMPT, planner.PROMPT + PRICE_GUIDANCE):
            self.assertEqual(schema_for(prompt), ('budget_adjust_plan', planner.SCHEMA))
        with self.assertRaises(KeyError):
            schema_for(planner.PROMPT + '\nUnregistered instructions')

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key', 'LLM_MODEL': 'synthetic-test-model',
                              'BUDGET_ADJUST_LANGSMITH': 'false'})
    def test_price_planner_uses_live_schema_and_preserves_calculations_on_explanation_failure(self):
        from budgets.adjustment_core import financial_planner as planner
        from budgets.test_adjustment_core import envelope, item
        from budgets.test_financial_planner import financial_context, review
        from chat.llm.live import LiveReading

        context = financial_context()
        context['priorities'].pop('FASHION_SHOPPING')
        context['price_reference'] = {'status': 'ready', 'snapshot': 'synthetic-test-only',
            'region_code': '11110', 'references': [reference(category='FOOD', item_group='meal',
                reference_price=1000, as_of='2026-11-01')]}
        request = envelope(item(amount=200000))
        baseline = planner.recommend(context, request, lambda *args: review(ids=[]))
        self.assertTrue(baseline['plans'])

        for outcome in ('verified', 'invalid_reason', 'timeout'):
            with self.subTest(outcome=outcome):
                def respond(http_request, **kwargs):
                    if outcome == 'timeout':
                        raise TimeoutError('synthetic timeout')
                    answer = review(ids=['plan_0.preserved'] if outcome == 'verified' else ['invented_fact'])
                    return BytesIO(json.dumps({'status': 'completed', 'output': [{'type': 'message',
                        'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}]}).encode())

                live = LiveReading(1, None)
                live.client.opener = Mock(side_effect=respond)
                with patch('socket.socket', side_effect=AssertionError('No network')):
                    result = planner.recommend(context, request, live)
                live.client.opener.assert_called_once()
                body = json.loads(live.client.opener.call_args.args[0].data)
                self.assertEqual(body['instructions'], planner.PROMPT + PRICE_GUIDANCE)
                self.assertEqual(body['text']['format'], {'type': 'json_schema', 'name': 'budget_adjust_plan',
                    'schema': planner.SCHEMA, 'strict': True})
                payload = json.loads(body['input'])
                self.assertEqual(payload['price_reference'], context['price_reference'])
                self.assertTrue(payload['verified_plans'])
                self.assertEqual(result['plans'], baseline['plans'])
                self.assertEqual(result['recommended_index'], baseline['recommended_index'])
                self.assertEqual(len(live.client.attempts), 1)
                if outcome == 'verified':
                    self.assertIn(result['plans'][0]['facts']['preserved'], result['reasons'])
                    self.assertEqual(live.client.attempts[0]['kind'], 'ok')
                else:
                    self.assertEqual(result['reasons'], baseline['reasons'])

    def context_from(self, document):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'references.json'
            path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8-sig')
            with patch.dict('os.environ', {'BUDGET_PRICE_REFERENCE_PATH': str(path)}), override_settings(
                    BUDGET_PRICE_PROFILE_PROVIDER=lambda user: {'region_code': '11110'}):
                return price_context(object())

    @patch.dict('os.environ', {'BUDGET_PRICE_REFERENCE_PATH': ''})
    def test_unconfigured_preserves_existing_behavior(self):
        provider = Mock(side_effect=AssertionError('disabled feature must not load a profile'))
        with override_settings(BUDGET_PRICE_PROFILE_PROVIDER=provider), patch.object(
                Path, 'read_text', side_effect=AssertionError('disabled feature must not read a snapshot')):
            self.assertEqual(price_context(object()), {'status': 'disabled'})
        self.assertEqual(review_prices({}, {}, {}, {})['issues'], [])

    def test_existing_other_category_does_not_break_price_validation(self):
        reference = {'category': 'BEAUTY', 'item_group': 'haircut', 'unit': '1회',
                     'reference_price': 35000, 'scope': 'local', 'region_code': '11110',
                     'tier': 'everyday', 'sample_kind': 'general', 'as_of': date.today().isoformat()}
        context = {'status': 'ready', 'snapshot': 'test-only', 'region_code': '11110',
                   'references': [reference]}
        review = review_prices(context, {'BEAUTY': 11667, 'ETC': 0},
            {'BEAUTY': 11667, 'ETC': 0}, {'BEAUTY': 35000, 'ETC': 0})
        self.assertEqual(review['issues'][0]['suggested_amount'], 35000)
        self.assertEqual(review['snapshot'], 'test-only')

    def test_valid_snapshot_reviews_without_changing_allocations(self):
        context = self.context_from(snapshot())
        before, proposed, history = {'BEAUTY': 50000}, {'BEAUTY': 20000}, {'BEAUTY': 100000}
        originals = deepcopy((context, before, proposed, history))
        result = review_prices(context, before, proposed, history, spent={'BEAUTY': 40000})
        self.assertEqual(result['status'], 'needs_reallocation')
        self.assertEqual(result['snapshot'], 'synthetic-test-only')
        self.assertEqual(result['issues'][0]['suggested_amount'], 40000)
        self.assertEqual(result['issues'][0]['additional_funding_needed'], 20000)
        self.assertTrue(result['issues'][0]['requires_reallocation'])
        self.assertEqual((context, before, proposed, history), originals)

    def test_explicit_amounts_locks_and_unused_zero_remain_untouched(self):
        context = self.context_from(snapshot())
        for protections in ({'user_direct': ['BEAUTY']}, {'locks': {'BEAUTY': 10000}}):
            with self.subTest(protections=protections):
                result = review_prices(context, {'BEAUTY': 50000}, {'BEAUTY': 10000},
                                       {'BEAUTY': 100000}, **protections)
                self.assertEqual(result['issues'], [])
        self.assertEqual(review_prices(context, {'BEAUTY': 0}, {'BEAUTY': 0}, {})['issues'], [])
        self.assertEqual(review_prices(context, {'HOUSING_TELECOM': 100000, 'TRAVEL_LODGING': 100000},
            {'HOUSING_TELECOM': 0, 'TRAVEL_LODGING': 0},
            {'HOUSING_TELECOM': 100000, 'TRAVEL_LODGING': 100000})['issues'], [])

    def test_only_current_general_prices_for_the_users_region_are_used(self):
        document = snapshot()
        document['regions']['11140'] = '다른 테스트 지역'
        document['references'] += [
            reference(scope='national', region_code='', reference_price=50000),
            reference(region_code='11140', reference_price=10000),
            reference(sample_kind='selected_low_price', reference_price=1000),
            reference(tier='premium', reference_price=200000),
            reference(as_of=(date.today() - timedelta(days=181)).isoformat(), reference_price=5000),
        ]
        context = self.context_from(document)
        self.assertEqual(len(context['references']), 4)
        result = review_prices(context, {'BEAUTY': 20000}, {'BEAUTY': 20000}, {'BEAUTY': 100000})
        self.assertEqual(result['issues'][0]['reference'], reference())
        self.assertEqual(result['issues'][0]['suggested_amount'], 35000)

    def test_known_item_group_overrides_provisional_default(self):
        document = snapshot()
        document['references'].append(reference(item_group='nails', reference_price=60000))
        context = self.context_from(document)
        context['item_groups'] = {'BEAUTY': 'nails'}
        result = review_prices(context, {'BEAUTY': 40000}, {'BEAUTY': 40000}, {'BEAUTY': 100000})
        self.assertEqual(result['issues'][0]['suggested_amount'], 60000)
        self.assertFalse(result['issues'][0]['provisional_item_group'])

    def test_missing_price_is_not_invented(self):
        context = self.context_from({**snapshot(), 'references': []})
        result = review_prices(context, {'BEAUTY': 20000}, {'BEAUTY': 20000}, {'BEAUTY': 100000})
        self.assertEqual(result['status'], 'insufficient_price_data')
        self.assertEqual(result['issues'], [])

    def test_malformed_snapshot_does_not_crash_budget_review(self):
        documents = [None, [], {}, {**snapshot(), 'version': 2}, {**snapshot(), 'version': True},
                     {**snapshot(), 'snapshot': ''}, {**snapshot(), 'regions': []},
                     {**snapshot(), 'regions': {'bad': '지역'}}, {**snapshot(), 'references': {}},
                     {**snapshot(), 'references': [None]}]
        for field, value in [('category', 'ETC'), ('category', 'UNCLASSIFIED'), ('reference_price', True),
                             ('reference_price', 0), ('reference_price', -1), ('reference_price', '35000'),
                             ('scope', 'unknown'), ('region_code', '99999'), ('tier', 'unknown'),
                             ('unit', None), ('item_group', ''), ('as_of', 'not-a-date'),
                             ('as_of', (date.today() + timedelta(days=1)).isoformat())]:
            documents.append({**snapshot(), 'references': [reference(**{field: value})]})
        for field in reference():
            row = reference()
            row.pop(field)
            documents.append({**snapshot(), 'references': [row]})
        for document in documents:
            with self.subTest(document=document):
                context = self.context_from(document)
                self.assertEqual(context['status'], 'invalid_snapshot')
                self.assertEqual(review_prices(context, {}, {}, {})['issues'], [])

    def test_unreadable_or_non_json_snapshot_is_reported(self):
        with TemporaryDirectory() as directory, override_settings(
                BUDGET_PRICE_PROFILE_PROVIDER=lambda user: {'region_code': '11110'}):
            path = Path(directory) / 'references.json'
            with patch.dict('os.environ', {'BUDGET_PRICE_REFERENCE_PATH': str(path)}):
                self.assertEqual(price_context(None)['status'], 'invalid_snapshot')
                for raw in (b'{invalid', b'\xff'):
                    path.write_bytes(raw)
                    self.assertEqual(price_context(None)['status'], 'invalid_snapshot')

    @patch.dict('os.environ', {'BUDGET_PRICE_REFERENCE_PATH': 'not-read.json'})
    def test_region_is_not_guessed_and_provider_must_be_configured(self):
        with override_settings(BUDGET_PRICE_PROFILE_PROVIDER=None):
            self.assertEqual(price_context(None)['status'], 'needs_region_provider')
        for profile in ({}, {'region_code': 11110}, {'region_code': '1111010100'}):
            with self.subTest(profile=profile), override_settings(BUDGET_PRICE_PROFILE_PROVIDER=lambda user: profile):
                self.assertEqual(price_context(None)['status'], 'needs_region')
        self.assertEqual(self.context_from({**snapshot(), 'regions': {'11140': '다른 지역'},
            'references': [reference(region_code='11140')]})['status'], 'unknown_region')

    def test_team_modules_run_without_personal_workspace(self):
        # Isolate the deployed modules: no project root or personal files are on this import path.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'budgets'
            package.mkdir()
            (package / '__init__.py').write_text('', encoding='utf-8')
            for name in ('price_reference.py', 'price_review.py'):
                shutil.copyfile(Path(__file__).with_name(name), package / name)
            (root / 'references.json').write_text(json.dumps(snapshot()), encoding='utf-8')
            script = '''
import os, sys
from pathlib import Path
from django.conf import settings
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
settings.configure(BUDGET_PRICE_PROFILE_PROVIDER=lambda user: {'region_code': '11110'})
from budgets.price_reference import price_context, review_prices
os.environ['BUDGET_PRICE_REFERENCE_PATH'] = ''
assert price_context(None) == {'status': 'disabled'}
os.environ['BUDGET_PRICE_REFERENCE_PATH'] = str(root / 'references.json')
context = price_context(None)
assert context['status'] == 'ready', context
result = review_prices(context, {'BEAUTY': 50000}, {'BEAUTY': 20000}, {'BEAUTY': 100000})
assert result['status'] == 'needs_reallocation', result
assert result['issues'][0]['suggested_amount'] == 35000, result
'''
            result = subprocess.run([sys.executable, '-I', '-c', script, str(root)], cwd=root,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

import json
from unittest.mock import patch
from django.test import SimpleTestCase
from .codef_privacy import delete_registered_accounts
from .codef import CodefError


def response(data, code='CF-00000'):
    return json.dumps({'result': {'code': code}, 'data': data})


class CodefPrivacyTests(SimpleTestCase):
    @patch('finance.codef._client')
    def test_scoped_deletion_maps_organization_code_without_credentials(self, client):
        bank = {'countryCode': 'KR', 'businessType': 'BK', 'clientType': 'P', 'loginType': '1', 'organizationCode': '0004'}
        card = {**bank, 'businessType': 'CD', 'organizationCode': '0302'}
        client.return_value.get_account_list.side_effect = [response({'accountList': [bank, card]}), response({'accountList': [bank]})]
        client.return_value.delete_account.return_value = response({'successList': [card]})
        delete_registered_accounts('synthetic', {'CD'})
        body = client.return_value.delete_account.call_args.args[1]
        self.assertEqual(body, {'connectedId': 'synthetic', 'accountList': [{
            'countryCode': 'KR', 'businessType': 'CD', 'clientType': 'P', 'loginType': '1', 'organization': '0302'}]})

    @patch('finance.codef._client')
    def test_already_removed_is_noop(self, client):
        client.return_value.get_account_list.return_value = response({'accountList': []})
        delete_registered_accounts('synthetic')
        client.return_value.delete_account.assert_not_called()

    @patch('finance.codef._client')
    def test_no_registered_accounts_code_is_noop_for_withdrawal_and_scope_revoke(self, client):
        client.return_value.get_account_list.return_value = response({'accountList': []}, 'CF-04014')
        for scope in (None, {'CD'}, {'BK'}):
            delete_registered_accounts('synthetic', scope)
        client.return_value.delete_account.assert_not_called()

    @patch('finance.codef._client')
    def test_last_account_deleted_then_empty_code_verifies_success(self, client):
        row = {'countryCode': 'KR', 'businessType': 'CD', 'clientType': 'P', 'loginType': '1', 'organizationCode': '0302'}
        client.return_value.get_account_list.side_effect = [
            response({'accountList': [row]}), response({'accountList': []}, 'CF-04014'),
        ]
        client.return_value.delete_account.return_value = response({'successList': [row]})
        delete_registered_accounts('synthetic')
        client.return_value.delete_account.assert_called_once()

    @patch('finance.codef._client')
    def test_empty_code_needs_explicit_complete_empty_list(self, client):
        for data in ({}, None, {'accountList': [{}]}, {'accountList': [], 'hasNext': True},
                     {'accountList': [], 'errorList': [{'code': 'failure'}]}):
            with self.subTest(data=data):
                client.return_value.get_account_list.return_value = response(data, 'CF-04014')
                with self.assertRaises(CodefError):
                    delete_registered_accounts('synthetic')
        client.return_value.delete_account.assert_not_called()

    @patch('finance.codef._client')
    def test_other_errors_with_empty_list_are_still_failures(self, client):
        for code in ('CF-04000', 'CF-00401', 'CF-09999'):
            with self.subTest(code=code):
                client.return_value.get_account_list.return_value = response({'accountList': []}, code)
                with self.assertRaises(CodefError):
                    delete_registered_accounts('synthetic')
        client.return_value.delete_account.assert_not_called()

    @patch('finance.codef._client')
    def test_partial_and_incomplete_results_are_not_success(self, client):
        for data in ({}, {'accountList': [], 'hasNext': True}, {'accountList': [{}]}):
            client.return_value.get_account_list.return_value = response(data)
            with self.assertRaises(ValueError):
                delete_registered_accounts('synthetic')
        row = {'countryCode': 'KR', 'businessType': 'CD', 'clientType': 'P', 'loginType': '1', 'organizationCode': '0302'}
        client.return_value.get_account_list.return_value = response({'accountList': [row]})
        client.return_value.delete_account.return_value = response({'errorList': [{'code': 'failure'}]})
        with self.assertRaises(ValueError):
            delete_registered_accounts('synthetic')
        client.return_value.delete_account.return_value = response({'successList': [row]})
        with self.assertRaises(ValueError):
            delete_registered_accounts('synthetic')

import json
from unittest.mock import patch
from django.test import SimpleTestCase
from .codef_privacy import delete_registered_accounts


def response(data):
    return json.dumps({'result': {'code': 'CF-00000'}, 'data': data})


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

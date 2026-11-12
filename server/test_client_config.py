import copy
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from server.client_config import default_config, load_client_config, next_prediction_status, validate_config


class ClientConfigTests(unittest.TestCase):
    def test_defaults_are_disabled_and_free(self):
        value = load_client_config({})
        self.assertFalse(value['ads']['enabled'])
        self.assertEqual(len(value['ads']['placements']), 4)
        self.assertTrue(all(not v['enabled'] for v in value['ads']['placements'].values()))
        self.assertTrue(all(v['mode'] == 'free' for v in value['featureGates'].values()))

    def test_valid_rewarded_configuration(self):
        value = default_config()
        value['ads']['enabled'] = True
        for slot in value['ads']['rewarded'].values():
            slot.update(enabled=True, unitId='adunit-test001')
        for gate in value['featureGates'].values():
            gate['mode'] = 'rewarded'
        self.assertEqual(load_client_config({'CLIENT_ADS_CONFIG_JSON': json.dumps(value)}), value)

    def test_rejects_malformed_configuration_without_echoing_values(self):
        cases = [None, [], {}, {'secret': 'private-example'}]
        for path, bad in [
            (('ads', 'enabled'), 'true'), (('ads', 'cacheTtlSeconds'), True),
            (('ads', 'cacheTtlSeconds'), 3600), (('ads', 'allowedVersions'), ['latest']),
            (('ads', 'placements', 'home_history_below'), {'enabled': True, 'type': 'ad-custom', 'unitId': ''}),
            (('featureGates', 'simulation_10d'), {'mode': 'rewarded'}),
        ]:
            value = default_config()
            node = value
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = bad
            cases.append(value)
        for value in cases:
            with self.subTest(value_type=type(value)):
                with self.assertRaisesRegex(ValueError, '^广告配置无效$'):
                    validate_config(value)
        with self.assertRaisesRegex(ValueError, '^广告配置无效$'):
            load_client_config({'CLIENT_ADS_CONFIG_JSON': 'private-invalid-json'})

    def test_independent_slots_and_no_unknown_fields(self):
        value = default_config()
        value['ads']['placements']['unexpected'] = {}
        with self.assertRaises(ValueError):
            validate_config(value)
        value = default_config()
        value['ads']['placements']['monitor_footer']['unitId'] = 'https://example.test/secret'
        with self.assertRaises(ValueError):
            validate_config(value)

    def test_metadata_does_not_disclose_items_and_allows_empty_ready_pool(self):
        payload = {'nextDayAvailable': True, 'dataUnavailable': False, 'datasetStatus': 'ready',
                   'datasetQuoteComplete': True, 'targetTradeDate': '20261113',
                   'dataStage': 'initial', 'items': [{'ts_code': '000001.SZ'}]}
        for stage in ('initial', 'formal'):
            for items in ([], payload['items']):
                value = dict(payload, dataStage=stage, items=items)
                result = next_prediction_status(value)
                self.assertTrue(result['available'])
                self.assertEqual(set(result), {'available', 'targetTradeDate', 'dataStage', 'reason'})
        for key, value in [('datasetStatus', 'failed'), ('dataUnavailable', True),
                           ('datasetQuoteComplete', False), ('nextDayAvailable', False),
                           ('targetTradeDate', ''), ('dataStage', 'realtime')]:
            with self.subTest(key=key):
                self.assertFalse(next_prediction_status(dict(payload, **{key: value}))['available'])


class ClientConfigHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server.app import ApiHandler
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), ApiHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def test_configuration_http_never_reads_business_resources(self):
        with patch('server.app.SERVICE', new=Mock()) as service, patch.dict('os.environ', {'CLIENT_ADS_CONFIG_JSON': ''}):
            with urlopen(self.base + '/api/client-config', timeout=3) as response:
                self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                body = json.load(response)
            self.assertEqual(body['code'], 0)
            self.assertEqual(service.mock_calls, [])

    def test_invalid_configuration_http_is_sanitized(self):
        with patch.dict('os.environ', {'CLIENT_ADS_CONFIG_JSON': 'private-marker'}):
            with self.assertRaises(HTTPError) as result:
                urlopen(self.base + '/api/client-config', timeout=3)
            self.assertEqual(result.exception.code, 503)
            body = result.exception.read().decode()
            self.assertNotIn('private-marker', body)

    def test_next_metadata_http_uses_existing_read_only_snapshot(self):
        payload = {'nextDayAvailable': True, 'datasetQuoteComplete': True, 'datasetStatus': 'ready',
                   'targetTradeDate': '20261113', 'dataStage': 'initial',
                   'items': [{'name': 'must-not-leak'}]}
        with patch('server.app.SERVICE', new=Mock()) as service:
            service.predictions.return_value = payload
            with urlopen(self.base + '/api/predictions/next-status', timeout=3) as response:
                body = json.load(response)
            self.assertTrue(body['data']['available'])
            self.assertNotIn('must-not-leak', json.dumps(body))
            service.predictions.assert_called_once_with('next_day')
            self.assertEqual(len(service.mock_calls), 1)

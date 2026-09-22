"""Runtime acceptance with synthetic plans/API data; no live App or HTTP calls."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANAGE_SPEC = importlib.util.spec_from_file_location('verify_test_manage', ROOT / 'scripts/manage.py')
manage = importlib.util.module_from_spec(MANAGE_SPEC)
MANAGE_SPEC.loader.exec_module(manage)
SPEC = importlib.util.spec_from_file_location('clash_project_verify', ROOT / 'scripts/verify.py')
verify = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {'manage': manage}):
    SPEC.loader.exec_module(verify)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        self.plan_file = self.root / 'plan.json'
        self.pinned = 'Fixture physical node'
        self.dns = {'enable': True, 'nameserver-policy': {'+.claude.ai': ['https://1.1.1.1/dns-query#Claude']}}
        self.runtime = {'dns': copy.deepcopy(self.dns), 'external-controller': '', 'rules': []}
        self.configs = {'mode': 'rule', 'allow-lan': False, 'mixed-port': 17890,
                        'port': 0, 'socks-port': 0, 'ipv6': False, 'tun': {'enable': False}}
        deployed = b'// synthetic managed overlay\n'
        (self.app / 'overlay.js').write_bytes(deployed)
        self.plan = {'version': 2, 'state': 'applied', 'pinned_proxy': self.pinned,
                     'app_version': '2.5.2',
                     'profiles': [{'uid': 'active', 'expected_dns': copy.deepcopy(self.dns)}],
                     'before_runtime': copy.deepcopy(self.configs),
                     'before_runtime_file': {'external-controller': ''},
                     'files': [{'path': 'overlay.js', 'after_sha256': hashlib.sha256(deployed).hexdigest()}]}
        self.responses = {
            '/configs': self.configs,
            '/proxies': {'proxies': {'Claude': {'all': [self.pinned], 'now': self.pinned},
                                     self.pinned: {'type': 'Shadowsocks'}}},
            '/rules': {'rules': [{'payload': domain, 'type': 'DomainSuffix', 'proxy': 'Claude'}
                                  for domain in verify.DOMAINS]},
            '/version': {'version': '1.19.29'},
            '/dns/query?name=api.anthropic.com&type=A': {'Status': 0, 'Answer': [{'data': '192.0.2.1'}]},
        }
        self.write_fixture()
        patcher = mock.patch.multiple(verify, APP=self.app, PLAN=self.plan_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        api_patch = mock.patch.object(verify, 'api', side_effect=lambda endpoint: self.responses[endpoint])
        self.api = api_patch.start()
        self.addCleanup(api_patch.stop)
        process_patch = mock.patch.object(verify.subprocess, 'Popen', side_effect=AssertionError('No real HTTP in tests'))
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def write_fixture(self):
        self.plan_file.write_text(json.dumps(self.plan))
        (self.app / 'clash-verge.yaml').write_text(yaml.safe_dump(self.runtime))
        (self.app / 'profiles.yaml').write_text('current: active\n')

    def successful_probe(self, url, proxy_url, method='HEAD'):
        return {'url': url, 'method': method, 'curl_exit': 0, 'http_status': 200,
                'http_connect': 200, 'tls_verify_result': 0, 'connected_to_local_proxy': True,
                'observed_connections': [{'chains': ['Claude', verify.alias(self.pinned)]}]}

    def test_valid_v2_plan_preserves_disabled_tun_and_uses_no_network_probe(self):
        with mock.patch.object(verify, 'probe', side_effect=AssertionError('No optional network probe')):
            result = verify.verify()
        self.assertTrue(all(result['checks'].values()), result['checks'])
        self.assertFalse(result['entry_state']['tun_enabled'])
        self.assertNotIn('probes', result)
        self.assertNotIn('/dns/query?name=api.anthropic.com&type=A', [call.args[0] for call in self.api.call_args_list])
        self.assertNotIn(self.pinned, json.dumps(result))

    def test_missing_pinned_node_reject_group_fails_acceptance(self):
        self.responses['/proxies']['proxies'] = {'Claude': {'all': ['REJECT'], 'now': 'REJECT'}}
        self.assertFalse(verify.verify()['checks']['single_existing_node'])
        with mock.patch.object(sys, 'argv', ['verify.py']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main(), 1)

    def test_runtime_baseline_drift_is_detected(self):
        for key, value in [('mode', 'global'), ('mixed-port', 23456), ('allow-lan', True),
                           ('ipv6', True), ('tun', {'enable': True})]:
            with self.subTest(key=key):
                before = self.configs[key]
                self.configs[key] = value
                checks = verify.verify()['checks']
                field = 'unchanged_tun_enable' if key == 'tun' else 'unchanged_' + key
                self.assertFalse(checks[field])
                self.configs[key] = before

    def test_dns_rules_profile_and_deployed_file_mismatches_are_rejected(self):
        self.runtime['dns']['enable'] = False
        self.runtime['external-controller'] = '127.0.0.1:19090'
        self.write_fixture()
        (self.app / 'profiles.yaml').write_text('current: unmanaged\n')
        (self.app / 'overlay.js').write_bytes(b'changed after install\n')
        self.responses['/rules']['rules'].pop()
        checks = verify.verify()['checks']
        for field in ['active_profile_managed', 'dns_policy_effective', 'core_domains_routed',
                      'persistent_files_match_plan', 'unchanged_tcp_controller']:
            self.assertFalse(checks[field], field)

    def test_anonymous_probes_use_runtime_port_and_no_private_health_endpoint(self):
        with mock.patch.object(verify, 'probe', side_effect=self.successful_probe) as probe:
            result = verify.verify(network=True)
        self.assertTrue(all(result['transport_checks'].values()))
        self.assertEqual([call.args[0] for call in probe.call_args_list], [
            'https://api.anthropic.com/v1/models', 'https://claude.ai/', 'https://platform.claude.com/'])
        self.assertEqual({call.args[1] for call in probe.call_args_list}, {'http://127.0.0.1:17890'})
        self.assertNotIn('health_probe', result)

    def test_http_port_is_used_when_mixed_port_is_disabled(self):
        self.configs.update({'mixed-port': 0, 'port': 17891})
        with mock.patch.object(verify, 'probe', side_effect=self.successful_probe) as probe:
            verify.verify(network=True)
        self.assertEqual({call.args[1] for call in probe.call_args_list}, {'http://127.0.0.1:17891'})

    def test_network_requires_an_enabled_http_port(self):
        self.configs.update({'mixed-port': 0, 'port': 0})
        with self.assertRaisesRegex(ValueError, 'HTTP or mixed proxy port'):
            verify.verify(network=True)

    def test_optional_health_endpoint_is_explicit_and_uses_the_same_proxy(self):
        with mock.patch.object(verify, 'probe', side_effect=self.successful_probe) as probe:
            result = verify.verify(network=True, health_url='https://health.example.test/ready')
        self.assertTrue(result['transport_checks']['optional_health_ok'])
        self.assertEqual(probe.call_args.args, ('https://health.example.test/ready', 'http://127.0.0.1:17890', 'GET'))

    def test_invalid_health_urls_never_reach_probe(self):
        for url in ['https://user:secret@example.test/', 'https://example.test/?token=secret',
                    'file:///tmp/health', 'https://example.test/#fragment']:
            with self.subTest(url=url), mock.patch.object(verify, 'probe', side_effect=self.successful_probe) as probe:
                with self.assertRaisesRegex(ValueError, 'Health URL'):
                    verify.verify(network=True, health_url=url)
                self.assertNotIn(url, [call.args[0] for call in probe.call_args_list])

    def test_missing_observed_route_is_not_treated_as_verified_transport(self):
        def no_route(url, proxy_url, method='HEAD'):
            result = self.successful_probe(url, proxy_url, method)
            result['observed_connections'] = []
            return result
        with mock.patch.object(verify, 'probe', side_effect=no_route):
            report = verify.verify(network=True)
        self.assertTrue(report['transport_checks']['http_responses_received'])
        self.assertFalse(report['transport_checks']['claude_routes_observed'])

    def test_head_probe_uses_curl_head_semantics_and_redacts_observed_node(self):
        process = mock.Mock(returncode=0)
        process.poll.side_effect = [None, 0, 0]
        process.communicate.return_value = (json.dumps({
            'http_code': 403, 'http_connect': 200, 'ssl_verify_result': 0,
            'remote_ip': '127.0.0.1', 'time_total': 0.3}), '')
        self.responses['/connections'] = {'connections': [{
            'metadata': {'host': 'claude.ai'}, 'rule': 'DomainSuffix', 'rulePayload': 'claude.ai',
            'chains': ['Claude', self.pinned]}]}
        with mock.patch.object(verify.subprocess, 'Popen', return_value=process) as popen, \
                mock.patch.object(verify.time, 'sleep'):
            report = verify.probe('https://claude.ai/', 'http://127.0.0.1:17890')
        command = popen.call_args.args[0]
        self.assertIn('--head', command)
        self.assertNotIn('-X', command)
        self.assertEqual(command[command.index('--proxy') + 1], 'http://127.0.0.1:17890')
        self.assertEqual(command[command.index('--noproxy') + 1], '')
        self.assertEqual(report['http_status'], 403)
        self.assertEqual(report['observed_connections'][0]['chains'], ['Claude', verify.alias(self.pinned)])
        self.assertNotIn(self.pinned, json.dumps(report))


if __name__ == '__main__':
    unittest.main()

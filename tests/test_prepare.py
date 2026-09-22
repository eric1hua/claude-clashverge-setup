"""Prepare uses disposable profiles and mocked IPC/core: no App or network access."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/manage.py'
SPEC = importlib.util.spec_from_file_location('prepare_manage', MODULE_PATH)
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.app = Path(self.temp.name).resolve() / 'app'
        self.bundle = Path(self.temp.name).resolve() / 'Clash Verge.app'
        self.local = self.root / '.local'
        self.plan_file = self.local / 'plan.json'
        (self.app / 'profiles').mkdir(parents=True)
        (self.app / 'geosite.dat').write_bytes(b'fixture database; core process is mocked')
        (self.bundle / 'Contents').mkdir(parents=True)
        with (self.bundle / 'Contents/Info.plist').open('wb') as f:
            plistlib.dump({'CFBundleShortVersionString': '2.5.2'}, f)
        template = MODULE_PATH.parents[1] / 'config/overrides/claude-routing.js'
        (self.root / 'config/overrides').mkdir(parents=True)
        (self.root / 'config/overrides/claude-routing.js').write_text(template.read_text())
        patcher = mock.patch.multiple(manage, ROOT=self.root, APP=self.app,
                                      APP_BUNDLE=self.bundle, CORE=self.bundle / 'core',
                                      LOCAL=self.local, PLAN=self.plan_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        platform = mock.patch.object(manage.sys, 'platform', 'darwin')
        platform.start()
        self.addCleanup(platform.stop)
        self.dns = {'enable': True, 'nameserver': ['https://resolver.example/dns-query'],
                    'proxy-server-nameserver': ['https://bootstrap.example/dns-query'],
                    'fake-ip-filter': ['*.home.example'],
                    'nameserver-policy': {'+.internal.example': ['192.0.2.53']}}
        self.node = {'name': 'Exact subscription node', 'type': 'ss', 'server': 'node.example',
                     'port': 1234, 'cipher': 'aes-128-gcm', 'password': 'fixture-only-secret'}
        self.runtime = {'dns': copy.deepcopy(self.dns), 'proxies': [copy.deepcopy(self.node)],
                        'proxy-groups': [{'name': 'JMS', 'type': 'select', 'proxies': [self.node['name']]}],
                        'rules': ['MATCH,JMS'], 'external-controller': ''}
        self.configs = {'mode': 'rule', 'mixed-port': 7890, 'port': 0, 'socks-port': 0,
                        'allow-lan': False, 'tun': {'enable': False, 'stack': 'mixed'}}
        self.live_proxies = {'JMS': {'type': 'Selector', 'now': self.node['name']},
                             self.node['name']: {'type': 'Shadowsocks'}}
        self.meta = {'current': 'primary', 'items': []}
        self.add_profile('primary', 'My arbitrary subscription')
        self.add_profile('mirror', 'My different mirror')
        self.persist()
        api = mock.patch.object(manage, 'api', side_effect=self.api)
        api.start()
        self.addCleanup(api.stop)
        core = mock.patch.object(manage.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b''))
        self.core = core.start()
        self.addCleanup(core.stop)
        transform = mock.patch.object(manage, 'transform', side_effect=self.transform)
        transform.start()
        self.addCleanup(transform.stop)

    def add_profile(self, uid, name):
        profile = {'uid': uid, 'name': name, 'type': 'remote', 'file': uid + '.yaml', 'option': {}}
        for kind in ['script', 'merge', 'rules', 'groups', 'proxies']:
            ext_uid = uid + '-' + kind
            filename = ext_uid + ('.js' if kind == 'script' else '.yaml')
            self.meta['items'].append({'uid': ext_uid, 'type': kind, 'file': filename})
            profile['option'][kind] = ext_uid
            text = 'function main(config, profileName) { return config; }\n' if kind == 'script' else '{}\n'
            (self.app / 'profiles' / filename).write_text(text)
        self.meta['items'].append(profile)
        base = {'proxies': [copy.deepcopy(self.node)], 'proxy-groups': copy.deepcopy(self.runtime['proxy-groups']),
                'rules': ['DOMAIN-SUFFIX,keep.example,DIRECT', 'MATCH,JMS']}
        self.dump(self.app / 'profiles' / profile['file'], base)

    def dump(self, path, data):
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    def persist(self):
        self.dump(self.app / 'profiles.yaml', self.meta)
        self.dump(self.app / 'clash-verge.yaml', self.runtime)

    def profile(self, uid):
        return next(p for p in self.meta['items'] if p['uid'] == uid)

    def extension_path(self, uid, kind):
        return self.app / 'profiles' / self.profile(self.profile(uid)['option'][kind])['file']

    def api(self, endpoint):
        values = {'/version': {'version': 'v1.19.29'}, '/proxies': {'proxies': self.live_proxies}, '/configs': self.configs}
        return copy.deepcopy(values[endpoint])

    def transform(self, script, config, _name):
        self.last_candidate = copy.deepcopy(config)
        pin = manage.script_pin(script.read_text())
        config['proxy-groups'] = [g for g in config.get('proxy-groups', []) if g.get('name') != 'Claude']
        config['proxy-groups'].append({'name': 'Claude', 'type': 'select', 'proxies': [pin]})
        return config

    def prepare(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            manage.prepare(**kwargs)
        return json.loads(self.plan_file.read_text())

    def all_live_bytes(self):
        return {str(p.relative_to(self.app)): p.read_bytes() for p in self.app.rglob('*') if p.is_file()}

    def test_default_discovers_only_arbitrary_active_remote_and_never_writes_app(self):
        before = self.all_live_bytes()
        plan = self.prepare()
        self.assertEqual([p['name'] for p in plan['profiles']], ['My arbitrary subscription'])
        self.assertEqual(len(plan['files']), 2)
        self.assertEqual(self.all_live_bytes(), before)
        self.assertEqual(plan['before_runtime']['mixed-port'], 7890)
        self.assertFalse(plan['before_runtime']['tun']['enable'])
        self.assertIn('approximation', plan['validation'][0]['scope'])

    def test_multiple_selected_profiles_and_each_own_dns_are_preserved(self):
        mirror_dns = {'nameserver': ['https://mirror.example/dns-query'],
                      'proxy-server-nameserver': ['192.0.2.54'], 'ipv6': True}
        self.dump(self.extension_path('mirror', 'merge'), {'dns': mirror_dns, 'hosts': {'host.example': '192.0.2.5'}})
        plan = self.prepare(profile_names=['My arbitrary subscription', 'My different mirror'])
        self.assertEqual(len(plan['files']), 4)
        primary, mirror = [p['expected_dns'] for p in plan['profiles']]
        self.assertEqual(primary['nameserver'], self.dns['nameserver'])
        self.assertEqual(mirror['nameserver'], mirror_dns['nameserver'])
        self.assertEqual(mirror['proxy-server-nameserver'], ['192.0.2.54'])
        self.assertTrue(mirror['ipv6'])
        self.assertEqual(mirror['nameserver-policy']['+.internal.example'], ['192.0.2.53'])
        self.assertEqual(mirror['nameserver-policy']['+.claude.ai'], manage.CLAUDE_RESOLVERS)
        self.assertNotIn('respect-rules', mirror)
        staged = manage.read_yaml(self.local / 'staged/profiles/mirror-merge.yaml')
        self.assertEqual(staged['hosts'], {'host.example': '192.0.2.5'})

    def test_selection_must_include_active_profile(self):
        with self.assertRaisesRegex(ValueError, 'include the active'):
            self.prepare(profile_names=['My different mirror'])

    def test_missing_node_in_a_selected_mirror_is_rejected(self):
        path = self.app / 'profiles/mirror.yaml'
        base = manage.read_yaml(path)
        base['proxies'] = []
        self.dump(path, base)
        with self.assertRaisesRegex(ValueError, 'Pinned node absent'):
            self.prepare(profile_names=['My arbitrary subscription', 'My different mirror'])
        self.assertFalse(self.plan_file.exists())

    def test_previous_private_plan_pin_survives_source_group_selection_change(self):
        first = self.prepare()
        self.live_proxies['JMS']['now'] = 'some other node'
        second = self.prepare()
        self.assertEqual(first['pinned_proxy'], second['pinned_proxy'])

    def test_existing_managed_script_pin_is_retained_when_live_group_rejects(self):
        first = self.prepare()
        self.extension_path('primary', 'script').write_bytes((self.local / 'claude-routing.js').read_bytes())
        self.plan_file.unlink()
        self.live_proxies['Claude'] = {'type': 'Selector', 'all': ['REJECT'], 'now': 'REJECT'}
        self.live_proxies['JMS']['now'] = 'some other node'
        self.assertEqual(self.prepare()['pinned_proxy'], first['pinned_proxy'])

    def test_missing_old_pin_never_follows_source_group(self):
        self.prepare()
        self.runtime['proxies'] = []
        self.persist()
        with self.assertRaisesRegex(ValueError, 'missing pin never'):
            self.prepare()

    def test_urltest_source_group_is_not_implicitly_adopted(self):
        self.live_proxies['JMS']['type'] = 'URLTest'
        with self.assertRaisesRegex(ValueError, 'manual Selector'):
            self.prepare()
        self.assertEqual(self.prepare(node=self.node['name'])['pinned_proxy'], self.node['name'])

    def test_source_group_cannot_select_another_group(self):
        self.live_proxies['JMS']['now'] = 'Fastest'
        self.live_proxies['Fastest'] = {'type': 'URLTest', 'now': self.node['name']}
        with self.assertRaisesRegex(ValueError, 'physical proxy'):
            self.prepare()

    def test_unknown_nonempty_script_requires_explicit_adoption_and_backup(self):
        path = self.extension_path('primary', 'script')
        original = b'function main(config) { config.port = 9000; return config; }\n'
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'custom script needs review'):
            self.prepare()
        plan = self.prepare(adopt_existing_script=True)
        self.assertTrue(plan['profiles'][0]['adopted_custom_script'])
        self.assertEqual(path.read_bytes(), original)
        with contextlib.redirect_stdout(io.StringIO()):
            manage.apply()
        applied = json.loads(self.plan_file.read_text())
        snapshot = self.root / applied['snapshot']
        self.assertEqual((snapshot / 'before-change/profiles/primary-script.js').read_bytes(), original)
        with contextlib.redirect_stdout(io.StringIO()):
            manage.rollback(applied['snapshot'])
        self.assertEqual(path.read_bytes(), original)

    def test_selected_script_shared_with_unselected_profile_is_rejected(self):
        self.profile('mirror')['option']['script'] = self.profile('primary')['option']['script']
        self.persist()
        with self.assertRaisesRegex(ValueError, 'shared with an unselected'):
            self.prepare()

    def test_selected_merge_shared_by_different_uid_alias_is_rejected(self):
        self.profile('mirror-merge')['file'] = self.profile('primary-merge')['file']
        self.persist()
        with self.assertRaisesRegex(ValueError, 'shared with an unselected'):
            self.prepare()

    def test_nonempty_group_or_node_extensions_are_rejected(self):
        self.dump(self.extension_path('primary', 'groups'), {'prepend': [{'name': 'Custom'}]})
        with self.assertRaisesRegex(ValueError, 'Nonempty groups'):
            self.prepare()

    def test_missing_proxy_server_resolver_is_rejected(self):
        del self.runtime['dns']['proxy-server-nameserver']
        self.persist()
        with self.assertRaisesRegex(ValueError, 'proxy-server-nameserver is required'):
            self.prepare()

    def test_disabled_effective_dns_is_rejected(self):
        self.runtime['dns']['enable'] = False
        self.persist()
        with self.assertRaisesRegex(ValueError, 'DNS must already be enabled'):
            self.prepare()

    def test_mirror_explicitly_disabled_dns_is_rejected(self):
        self.dump(self.extension_path('mirror', 'merge'), {'dns': {'enable': False}})
        with self.assertRaisesRegex(ValueError, 'profile disables DNS'):
            self.prepare(profile_names=['My arbitrary subscription', 'My different mirror'])

    def test_candidate_keeps_subscription_rules_and_node_tls_properties(self):
        base_path = self.app / 'profiles/primary.yaml'
        base = manage.read_yaml(base_path)
        base['proxies'][0]['skip-cert-verify'] = False
        self.dump(base_path, base)
        self.dump(self.extension_path('primary', 'rules'), {'prepend': ['DOMAIN,first.example,DIRECT'], 'append': []})
        self.prepare()
        self.assertEqual(self.last_candidate['rules'], ['DOMAIN,first.example,DIRECT'] + base['rules'])
        self.assertFalse(self.last_candidate['proxies'][0]['skip-cert-verify'])

    def test_core_failure_leaves_app_untouched_and_does_not_create_plan(self):
        self.core.return_value = subprocess.CompletedProcess([], 1, b'private details', b'failure')
        before = self.all_live_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Core validation failed'):
            self.prepare()
        self.assertEqual(self.all_live_bytes(), before)
        self.assertFalse(self.plan_file.exists())

    def test_non_rule_mode_is_rejected_without_changes(self):
        self.configs['mode'] = 'global'
        before = self.all_live_bytes()
        with self.assertRaisesRegex(ValueError, 'must already be in rule mode'):
            self.prepare()
        self.assertEqual(self.all_live_bytes(), before)

    def test_existing_unknown_claude_group_is_not_adopted(self):
        self.live_proxies['Claude'] = {'type': 'Selector', 'all': [self.node['name']], 'now': self.node['name']}
        with self.assertRaisesRegex(ValueError, 'not managed by this project'):
            self.prepare(node=self.node['name'])

    def test_unknown_claude_group_in_mirror_is_not_overwritten(self):
        path = self.app / 'profiles/mirror.yaml'
        base = manage.read_yaml(path)
        base['proxy-groups'].append({'name': 'Claude', 'type': 'select', 'proxies': ['DIRECT']})
        self.dump(path, base)
        with self.assertRaisesRegex(ValueError, 'unmanaged Claude node/group'):
            self.prepare(profile_names=['My arbitrary subscription', 'My different mirror'])

    def test_explicit_node_reselection_updates_prepared_plan_only(self):
        self.prepare()
        other = dict(self.node, name='Explicit replacement node')
        self.runtime['proxies'].append(other)
        self.live_proxies[other['name']] = {'type': 'Shadowsocks'}
        path = self.app / 'profiles/primary.yaml'
        base = manage.read_yaml(path)
        base['proxies'].append(other)
        self.dump(path, base)
        self.persist()
        before = self.all_live_bytes()
        plan = self.prepare(node=other['name'])
        self.assertEqual(plan['pinned_proxy'], other['name'])
        self.assertEqual(self.all_live_bytes(), before)

    def test_core_checks_use_private_copies_of_existing_databases(self):
        self.prepare()
        private_copy = self.local / 'core-check/geosite.dat'
        self.assertEqual(private_copy.read_bytes(), (self.app / 'geosite.dat').read_bytes())
        self.assertFalse(private_copy.is_symlink())
        self.assertNotEqual(private_copy.stat().st_ino, (self.app / 'geosite.dat').stat().st_ino)
        command = self.core.call_args.args[0]
        self.assertEqual(command[command.index('-d') + 1], str(self.local / 'core-check'))

    def test_missing_geosite_cache_requires_app_preparation(self):
        (self.app / 'geosite.dat').unlink()
        with self.assertRaisesRegex(ValueError, 'Existing geosite.dat is required'):
            self.prepare()
        self.core.assert_not_called()

    def test_existing_respect_rules_false_is_preserved(self):
        self.runtime['dns']['respect-rules'] = False
        self.persist()
        self.assertFalse(self.prepare()['profiles'][0]['expected_dns']['respect-rules'])

    def test_provider_configuration_is_rejected_before_core_cache_downloads(self):
        self.runtime['rule-providers'] = {'external': {'type': 'http', 'url': 'https://fixture.example/rules.yaml', 'path': '/unwanted/path'}}
        self.persist()
        with self.assertRaisesRegex(ValueError, 'Provider-based configurations'):
            self.prepare()
        self.core.assert_not_called()

    def test_runtime_configuration_change_invalidates_prepared_dns_baseline(self):
        self.prepare()
        self.runtime['dns']['nameserver'] = ['192.0.2.60']
        self.persist()
        with self.assertRaisesRegex(ValueError, 'Profile changed since prepare'):
            manage.apply()

    def test_unsupported_versions_rejected_without_skip_option(self):
        with (self.bundle / 'Contents/Info.plist').open('wb') as f:
            plistlib.dump({'CFBundleShortVersionString': '2.6.0'}, f)
        with self.assertRaisesRegex(ValueError, 'Supported App/core'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()

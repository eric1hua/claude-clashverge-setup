"""Run the real installer against disposable scripts; never touch an App or network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.root.mkdir()
        shutil.copy2(ROOT / 'install.sh', self.root / 'install.sh')
        self.bin = self.root / 'mock-bin'
        self.bin.mkdir()
        self.events = self.root / 'events.jsonl'
        self.env = dict(os.environ, PATH=str(self.bin) + ':/usr/bin:/bin',
                        PYTHON_BIN=str(self.bin / 'python3'), EVENTS=str(self.events))
        for name, content in {
            'uname': 'printf "%s\\n" "${FAKE_OS:-Darwin}"',
            'id': 'printf "%s\\n" "${FAKE_UID:-501}"',
            'node': 'exit "${NODE_EXIT:-0}"',
            'python3': 'exit "${PYTHON_EXIT:-0}"',
            'sleep': 'exit 0',
            'sudo': 'printf \'["UNEXPECTED_SUDO"]\\n\' >> "$EVENTS"; exit 99',
        }.items():
            self.executable(self.bin / name, '#!/bin/sh\n' + content + '\n')
        self.executable(self.root / '.local/venv/bin/python', '#!' + sys.executable + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == '-c':
    raise SystemExit(int(os.environ.get('YAML_IMPORT_EXIT', '0')))
if args[:2] == ['-m', 'pip']:
    phase = 'pip'
elif Path(args[0]).name == 'manage.py':
    phase = args[1]
elif Path(args[0]).name == 'verify.py':
    phase = 'verify-network' if '--network' in args else 'verify'
else:
    raise SystemExit('Unexpected fake Python command')
with open(os.environ['EVENTS'], 'a') as log:
    log.write(json.dumps([phase, args]) + '\\n')
raise SystemExit(7 if os.environ.get('FAIL_PHASE') == phase else 0)
''')
        # Only the installer is copied. Even a mistaken Python dispatch cannot
        # reach the real manage.py, verify.py or reload script from this fixture.
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/manage.py').write_text('# disposable placeholder\n')
        (self.root / 'scripts/verify.py').write_text('# disposable placeholder\n')
        self.executable(self.root / 'scripts/reload-app.zsh', '''#!/bin/zsh
printf '["reload"]\\n' >> "$EVENTS"
[[ "${FAIL_PHASE:-}" != reload ]]
''')

    def executable(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o700)

    def run_installer(self, *args, **env):
        return subprocess.run(['/bin/bash', str(self.root / 'install.sh'), *args],
                              cwd=self.root, env=dict(self.env, **env), input='',
                              text=True, capture_output=True, timeout=15)

    def recorded(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def phases(self):
        return [event[0] for event in self.recorded()]

    def test_dry_run_only_prepares_and_preserves_argument_boundaries(self):
        result = self.run_installer('--yes', '--dry-run', '--profile', 'Primary subscription',
                                    '--profile', 'Backup subscription', '--source-group', 'My group',
                                    '--node', 'Node with spaces', '--adopt-existing-script')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(), ['prepare'])
        self.assertEqual(self.recorded()[0][1][1:], [
            'prepare', '--profiles', 'Primary subscription', '--profiles', 'Backup subscription',
            '--source-group', 'My group', '--node', 'Node with spaces', '--adopt-existing-script'])

    @unittest.skipUnless(Path('/bin/zsh').is_file(), 'installer reload requires macOS /bin/zsh')
    def test_yes_orders_prepare_apply_reload_and_both_acceptance_stages(self):
        result = self.run_installer('--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(), ['prepare', 'apply', 'reload', 'verify', 'verify-network'])
        self.assertNotIn('UNEXPECTED_SUDO', self.phases())

    def test_unknown_or_missing_arguments_fail_before_prepare(self):
        for args in [('--unknown',), ('--profile',), ('--node', '')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_installer(*args).returncode, 0)
                self.assertEqual(self.phases(), [])

    def test_noninteractive_run_requires_yes_after_prepare(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.phases(), ['prepare'])
        self.assertIn('No terminal for confirmation', result.stderr)

    def test_wrong_platform_or_root_never_prepares(self):
        for env in [{'FAKE_OS': 'Linux'}, {'FAKE_UID': '0'}]:
            with self.subTest(env=env):
                self.assertNotEqual(self.run_installer('--yes', **env).returncode, 0)
                self.assertEqual(self.phases(), [])

    def test_python_and_node_failures_stop_before_prepare(self):
        for env in [{'PYTHON_EXIT': '1'}, {'NODE_EXIT': '1'}]:
            with self.subTest(env=env):
                self.assertNotEqual(self.run_installer('--yes', **env).returncode, 0)
                self.assertEqual(self.phases(), [])

    def test_pinned_dependency_install_failure_never_prepares(self):
        result = self.run_installer('--yes', YAML_IMPORT_EXIT='1', FAIL_PHASE='pip')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases(), ['pip'])
        self.assertIn('--require-hashes', self.recorded()[0][1])
        self.assertIn('--only-binary=:all:', self.recorded()[0][1])

    def test_prepare_failure_never_applies(self):
        result = self.run_installer('--yes', FAIL_PHASE='prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases(), ['prepare'])

    def test_apply_failure_never_reloads(self):
        result = self.run_installer('--yes', FAIL_PHASE='apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases(), ['prepare', 'apply'])

    @unittest.skipUnless(Path('/bin/zsh').is_file(), 'installer reload requires macOS /bin/zsh')
    def test_reload_failure_never_verifies(self):
        result = self.run_installer('--yes', FAIL_PHASE='reload')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases(), ['prepare', 'apply', 'reload'])
        self.assertIn('reload failed', result.stderr)

    @unittest.skipUnless(Path('/bin/zsh').is_file(), 'installer reload requires macOS /bin/zsh')
    def test_persistent_runtime_verification_failure_is_not_hidden(self):
        result = self.run_installer('--yes', FAIL_PHASE='verify')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases()[:3], ['prepare', 'apply', 'reload'])
        self.assertTrue(self.phases()[3:])
        self.assertTrue(all(phase == 'verify' for phase in self.phases()[3:]))
        self.assertIn('Installation is not verified', result.stderr)

    @unittest.skipUnless(Path('/bin/zsh').is_file(), 'installer reload requires macOS /bin/zsh')
    def test_network_verification_failure_is_not_hidden(self):
        result = self.run_installer('--yes', FAIL_PHASE='verify-network')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.phases(), ['prepare', 'apply', 'reload', 'verify', 'verify-network'])
        self.assertIn('network acceptance is incomplete', result.stderr)
        self.assertNotIn('Installed and anonymous transport checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()

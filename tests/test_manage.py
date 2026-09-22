"""Exercise deployment/rollback against disposable local files, without the App."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/manage.py'
SPEC = importlib.util.spec_from_file_location('clash_project_manage', MODULE_PATH)
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.app = Path(self.temp.name).resolve() / 'app'
        self.local = self.root / '.local'
        self.plan_file = self.local / 'plan.json'
        self.app.mkdir(parents=True)
        self.local.mkdir(parents=True, mode=0o700)
        (self.root / 'backups').mkdir(mode=0o700)
        patcher = mock.patch.multiple(
            manage, ROOT=self.root, APP=self.app, LOCAL=self.local,
            PLAN=self.plan_file,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Every test fails immediately if a path unexpectedly makes an API call.
        api_patch = mock.patch.object(manage, 'api', side_effect=AssertionError('No network in file tests'))
        api_patch.start()
        self.addCleanup(api_patch.stop)
        self.relative = ['profiles/primary.js', 'profiles/mirror.js']
        self.old = [b'// primary original\n', b'// mirror original\n']
        self.new = [b'// primary managed\n', b'// mirror managed\n']
        (self.app / 'profiles').mkdir()
        meta = b'current: primary\nitems: []\n'
        (self.app / 'profiles.yaml').write_bytes(meta)
        entries = []
        sources = []
        for relative, before, after in zip(self.relative, self.old, self.new):
            (self.app / relative).write_bytes(before)
            manage.atomic(self.local / 'staged' / relative, after)
            entries.append({
                'path': relative, 'before_sha256': manage.sha(before),
                'after_sha256': manage.sha(after),
            })
            sources.append({'path': relative, 'sha256': manage.sha(before)})
        self.plan = {
            'version': 1, 'state': 'prepared', 'app_dir': str(self.app),
            'meta_sha256': manage.sha(meta), 'pinned_proxy': 'Existing node',
            'files': entries, 'sources': sources,
        }
        manage.write_json(self.plan_file, self.plan)

    def invoke(self, function, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(*args)

    def content(self):
        return [(self.app / relative).read_bytes() for relative in self.relative]

    def deploy(self):
        self.invoke(manage.apply)
        plan = json.loads(self.plan_file.read_text())
        return plan['snapshot'], self.root / plan['snapshot']

    def test_apply_backs_up_exact_bytes_and_rollback_restores_them(self):
        snapshot_arg, snapshot = self.deploy()
        self.assertEqual(self.content(), self.new)
        manifest = json.loads((snapshot / 'manifest.json').read_text())
        self.assertEqual(len(manifest['files']), 2)
        for entry, before in zip(manifest['files'], self.old):
            saved = snapshot / entry['backup']
            self.assertEqual(saved.read_bytes(), before)
            self.assertEqual(manage.sha(saved.read_bytes()), entry['before_sha256'])
            self.assertEqual(os.stat(saved).st_mode & 0o777, 0o600)
        self.invoke(manage.rollback, snapshot_arg)
        self.assertEqual(self.content(), self.old)
        self.assertEqual(json.loads(self.plan_file.read_text())['state'], 'rolled-back')

    def test_staged_tampering_is_rejected_before_any_live_write(self):
        (self.local / 'staged' / self.relative[1]).write_bytes(b'tampered\n')
        with self.assertRaisesRegex(ValueError, 'Staged content changed'):
            self.invoke(manage.apply)
        self.assertEqual(self.content(), self.old)

    def test_source_drift_is_rejected_without_overwriting_the_newer_edit(self):
        edited = b'// changed in Verge after prepare\n'
        (self.app / self.relative[1]).write_bytes(edited)
        with self.assertRaisesRegex(ValueError, 'Profile changed'):
            self.invoke(manage.apply)
        self.assertEqual(self.content(), [self.old[0], edited])

    def test_profile_metadata_drift_is_rejected(self):
        (self.app / 'profiles.yaml').write_bytes(b'current: another-profile\n')
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            self.invoke(manage.apply)
        self.assertEqual(self.content(), self.old)

    def test_failed_second_apply_write_restores_the_first(self):
        real_atomic = manage.atomic
        second = self.app / self.relative[1]

        def fail_second(path, data, mode=0o600):
            if path == second:
                raise OSError('simulated second-file write failure')
            return real_atomic(path, data, mode)

        with mock.patch.object(manage, 'atomic', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.invoke(manage.apply)
        self.assertEqual(self.content(), self.old)
        self.assertEqual(json.loads(self.plan_file.read_text())['state'], 'prepared')

    def test_rollback_refuses_later_edits_before_restoring_any_file(self):
        snapshot_arg, _ = self.deploy()
        edited = b'// legitimate edit after deployment\n'
        (self.app / self.relative[1]).write_bytes(edited)
        with self.assertRaisesRegex(ValueError, 'Newer edits'):
            self.invoke(manage.rollback, snapshot_arg)
        self.assertEqual(self.content(), [self.new[0], edited])

    def test_rollback_refuses_a_corrupt_backup(self):
        snapshot_arg, snapshot = self.deploy()
        manifest = json.loads((snapshot / 'manifest.json').read_text())
        (snapshot / manifest['files'][1]['backup']).write_bytes(b'corrupt\n')
        with self.assertRaisesRegex(ValueError, 'Backup checksum/path'):
            self.invoke(manage.rollback, snapshot_arg)
        self.assertEqual(self.content(), self.new)

    def test_rollback_refuses_an_outside_snapshot(self):
        with self.assertRaisesRegex(ValueError, 'Snapshot must be inside'):
            self.invoke(manage.rollback, '../outside-project')
        self.assertEqual(self.content(), self.old)

    def test_failed_second_rollback_write_restores_the_deployed_state(self):
        snapshot_arg, _ = self.deploy()
        real_atomic = manage.atomic
        second = self.app / self.relative[1]

        def fail_second_restore(path, data, mode=0o600):
            if path == second and data == self.old[1]:
                raise OSError('simulated rollback write failure')
            return real_atomic(path, data, mode)

        with mock.patch.object(manage, 'atomic', side_effect=fail_second_restore):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.invoke(manage.rollback, snapshot_arg)
        self.assertEqual(self.content(), self.new)
        self.assertEqual(json.loads(self.plan_file.read_text())['state'], 'applied')

    def test_yaml_errors_do_not_print_original_secret_lines(self):
        secret = 'fixture-secret-must-not-appear'
        malformed = self.app / 'malformed.yaml'
        malformed.write_text('password: "' + secret + '\n')
        try:
            manage.read_yaml(malformed)
        except Exception as error:
            self.assertNotIn(secret, str(error))
        else:
            self.fail('Expected malformed YAML rejection')


if __name__ == '__main__':
    unittest.main()

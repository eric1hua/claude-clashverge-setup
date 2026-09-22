#!/usr/bin/env python3
"""Stage, back up, deploy and restore Claude extensions for existing subscriptions.

macOS / Clash Verge Rev 2.5.2 / Mihomo 1.19.29 only. Prepare is read-only
for App files; apply never reloads the App or changes subscription metadata.
Subscription URLs, credentials and complete runtime files remain private.
"""
import argparse
import ast
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get('CLAUDE_CLASH_APP_DIR', str(Path.home() / 'Library/Application Support/io.github.clash-verge-rev.clash-verge-rev'))).expanduser().resolve()
APP_BUNDLE = Path(os.environ.get('CLAUDE_CLASH_APP_BUNDLE', '/Applications/Clash Verge.app')).expanduser().resolve()
CORE = APP_BUNDLE / 'Contents/MacOS/verge-mihomo'
SOCKET = os.environ.get('CLAUDE_CLASH_SOCKET', '/tmp/verge/verge-mihomo.sock')
LOCAL = ROOT / '.local'
PLAN = LOCAL / 'plan.json'
MANAGED_MARKERS = ('// claude-clashverge-setup managed v1',
                   '// Per-profile Clash Verge script for the verified JMS profiles.')
CLAUDE_DOMAINS = ('anthropic.com', 'claude.ai', 'claude.com', 'clau.de',
                  'claudemcpclient.com', 'claudemcpcontent.com', 'claudeusercontent.com')
CLAUDE_RESOLVERS = ['https://1.1.1.1/dns-query#Claude', 'https://8.8.8.8/dns-query#Claude']
GROUP_TYPES = {'Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Relay', 'Direct', 'Reject', 'Pass'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_yaml(path):
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        raise ValueError('Invalid YAML in ' + path.name + '; content withheld') from None


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        os.fchmod(f.fileno(), mode)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, data):
    atomic(path, (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode())


def api(endpoint):
    try:
        result = subprocess.run(
            ['curl', '--noproxy', '*', '--fail', '--silent', '--show-error',
             '--max-time', '10', '--unix-socket', SOCKET, 'http://localhost' + endpoint],
            capture_output=True, check=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        raise RuntimeError('Cannot read Mihomo IPC API; start the supported Clash Verge App and check its local socket') from None


def transform(script, config, name):
    result = subprocess.run(
        ['node', str(ROOT / 'scripts/run-overlay.cjs'), str(script)],
        input=json.dumps({'config': config, 'profileName': name}),
        text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('JavaScript validation failed; no live files changed')
    return json.loads(result.stdout)


def app_file(relative):
    raw_path = APP / relative
    path = raw_path.resolve()
    if not path.is_relative_to(APP.resolve()) or raw_path.is_symlink():
        raise ValueError('Unsafe App file path')
    return path


def deep_merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def managed_script(text):
    return text.lstrip().startswith(MANAGED_MARKERS)


def empty_script(text):
    # Recognize only the official no-op template; arbitrary JavaScript needs review.
    uncommented = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S).strip()
    if not uncommented:
        return True
    return bool(re.fullmatch(r'function\s+main\s*\(\s*config\s*(?:,\s*profileName\s*)?\)\s*\{\s*return\s+config\s*;?\s*\}\s*;?', uncommented))


def script_pin(text):
    if not managed_script(text):
        return None
    match = re.search(r'^const PINNED_PROXY = (.+);\s*$', text, re.M)
    if not match:
        raise ValueError('Managed script has no readable pin; review before replacing it')
    try:
        value = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        raise ValueError('Managed script pin cannot be read safely') from None
    if not isinstance(value, str) or not value or value == '__CLAUDE_PINNED_PROXY__':
        raise ValueError('Managed script requires an installed physical-node pin')
    return value


def extension(profile, items, kind, required=True):
    uid = profile.get('option', {}).get(kind)
    if not uid and not required:
        return None
    item = items.get(uid, {})
    if item.get('type') != kind or not item.get('file'):
        raise ValueError('Missing or mismatched ' + kind + ' extension; create it in Clash Verge first')
    return app_file('profiles/' + item['file'])


def selected_profiles(meta, profile_names):
    profiles = [p for p in meta.get('items', []) if p.get('type') == 'remote']
    active = next((p for p in profiles if p.get('uid') == meta.get('current')), None)
    if active is None:
        raise ValueError('Current profile must be an existing remote subscription')
    if not profile_names:
        return active, [active]
    selected = []
    for name in dict.fromkeys(profile_names):
        matches = [p for p in profiles if p.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Each --profiles name must identify exactly one remote subscription')
        selected.append(matches[0])
    if active['uid'] not in {p['uid'] for p in selected}:
        raise ValueError('Explicit --profiles must include the active subscription')
    return active, selected


def dns_overlay(merge, current_dns):
    if not isinstance(current_dns, dict) or current_dns.get('enable') is not True:
        raise ValueError('Effective DNS must already be enabled; review DNS in Clash Verge first')
    own = merge.get('dns', {})
    if not isinstance(own, dict):
        raise ValueError('Profile merge DNS must be a mapping')
    # Start with effective DNS, then retain every profile-specific override. This
    # also supplies a reviewed baseline for selected mirrors lacking a DNS block.
    dns = deep_merge(current_dns, own)
    if dns.get('enable') is False:
        raise ValueError('Selected profile disables DNS; review before installing')
    bootstrap = dns.get('proxy-server-nameserver')
    if not isinstance(bootstrap, list) or not bootstrap or not all(isinstance(x, str) and x for x in bootstrap):
        raise ValueError('Existing proxy-server-nameserver is required; review proxy-host resolution to avoid DNS loops')
    policy = dns.get('nameserver-policy', {})
    if not isinstance(policy, dict):
        raise ValueError('Existing nameserver-policy must be a mapping')
    dns['nameserver-policy'] = dict(policy)
    for domain in CLAUDE_DOMAINS:
        dns['nameserver-policy']['+.' + domain] = list(CLAUDE_RESOLVERS)
    dns['enable'] = True
    return dns


def prepare_core_data():
    """Copy existing databases so core syntax checks cannot update App caches."""
    geosite = app_file('geosite.dat')
    if not geosite.is_file():
        raise ValueError('Existing geosite.dat is required; let Clash Verge prepare its databases normally before retrying')
    directory = LOCAL / 'core-check'
    if directory.is_symlink():
        raise ValueError('Private core-check directory must not be a symlink')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    for name in ['geosite.dat', 'geoip.dat', 'Country.mmdb', 'ASN.mmdb']:
        source = app_file(name)
        if source.is_file():
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                temp = Path(stream.name)
            try:
                shutil.copyfile(source, temp)
                os.chmod(temp, 0o600)
                os.replace(temp, directory / name)
            finally:
                temp.unlink(missing_ok=True)
    return directory


def prepare(profile_names=None, source_group='JMS', node=None, adopt_existing_script=False):
    if sys.platform != 'darwin':
        raise ValueError('This installer supports macOS only')
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    os.chmod(LOCAL, 0o700)
    with open(APP_BUNDLE / 'Contents/Info.plist', 'rb') as f:
        version = plistlib.load(f)['CFBundleShortVersionString']
    if version != '2.5.2' or api('/version')['version'] != 'v1.19.29':
        raise ValueError('Supported App/core versions are 2.5.2 / v1.19.29; review official lifecycle before upgrading')
    meta_path = app_file('profiles.yaml')
    meta = read_yaml(meta_path)
    items = {x['uid']: x for x in meta['items']}
    active, selected = selected_profiles(meta, profile_names)
    selected_uids = {p['uid'] for p in selected}
    sources = {}
    def remember(path):
        sources[str(path.relative_to(APP.resolve()))] = sha(path.read_bytes())
    scripts = {}
    for profile in selected:
        for kind in ['script', 'merge']:
            path = extension(profile, items, kind)
            for other in meta['items']:
                if other.get('type') not in ('remote', 'local') or other['uid'] in selected_uids:
                    continue
                for other_kind in ['script', 'merge']:
                    other_path = extension(other, items, other_kind, required=False)
                    if other_path == path:
                        raise ValueError('Selected extension is shared with an unselected profile; select/review all affected profiles first')
            remember(path)
            if kind == 'script':
                text = path.read_text()
                if not (managed_script(text) or empty_script(text) or adopt_existing_script):
                    raise ValueError('Existing custom script needs review; --adopt-existing-script explicitly authorizes backed-up replacement')
                scripts[profile['uid']] = text
    proxies = api('/proxies')['proxies']
    current_runtime_path = app_file('clash-verge.yaml')
    current_runtime = read_yaml(current_runtime_path)
    remember(current_runtime_path)
    if current_runtime.get('proxy-providers') or current_runtime.get('rule-providers'):
        raise ValueError('Provider-based configurations require lifecycle review; this installer supports inline subscription nodes and rules only')
    configs = api('/configs')
    if configs.get('mode') != 'rule':
        raise ValueError('Clash Verge must already be in rule mode; select Rule in the App before preparing')
    previous = read_yaml(PLAN) if PLAN.exists() else {}
    if previous.get('app_dir') not in (None, str(APP)):
        previous = {}
    pins = {script_pin(text) for text in scripts.values() if managed_script(text)}
    pins.discard(None)
    if len(pins) > 1 and node is None:
        raise ValueError('Selected profiles have conflicting installed pins; review and explicitly select --node')
    installed_group = proxies.get('Claude', {})
    if installed_group and not managed_script(scripts[active['uid']]):
        raise ValueError('Existing Claude node/group is not managed by this project; review the naming conflict first')
    installed = installed_group.get('all', [])
    installed_pin = installed[0] if len(installed) == 1 and installed[0] != 'REJECT' else None
    pinned = node or previous.get('pinned_proxy') or next(iter(pins), None) or installed_pin
    if not pinned:
        source = proxies.get(source_group, {})
        if source.get('type') != 'Selector':
            raise ValueError('First install requires a manual Selector source group or explicit --node; automatic groups are not adopted')
        pinned = source.get('now')
    actual_names = {p['name'] for p in current_runtime.get('proxies', [])}
    physical = proxies.get(pinned, {})
    if not pinned or pinned not in actual_names or not physical.get('type') or physical['type'] in GROUP_TYPES or pinned in ['DIRECT', 'REJECT', 'PASS', 'Claude']:
        raise ValueError('An exact existing physical proxy is required; a missing pin never automatically falls back')
    template = (ROOT / 'config/overrides/claude-routing.js').read_text()
    placeholder = "'__CLAUDE_PINNED_PROXY__'"
    if template.count(placeholder) != 1:
        raise ValueError('Expected one pinned-proxy placeholder')
    overlay = template.replace(placeholder, json.dumps(pinned, ensure_ascii=False)).encode()
    staged_script = LOCAL / 'claude-routing.js'
    atomic(staged_script, overlay)
    core_data = prepare_core_data()
    changes = {}
    validations = []
    profile_plans = []
    for profile in selected:
        name = profile.get('name', profile['uid'])
        base_path = app_file('profiles/' + profile['file'])
        base = read_yaml(base_path)
        remember(base_path)
        if base.get('proxy-providers') or base.get('rule-providers'):
            raise ValueError('Provider-based configurations require lifecycle review; this installer supports inline subscription nodes and rules only')
        if (any(p.get('name') == 'Claude' for p in base.get('proxies', [])) or
                (any(g.get('name') == 'Claude' for g in base.get('proxy-groups', [])) and not managed_script(scripts[profile['uid']]))):
            raise ValueError('Selected subscription has an unmanaged Claude node/group; review the naming conflict first')
        if pinned not in {p['name'] for p in base.get('proxies', [])}:
            raise ValueError('Pinned node absent from a selected subscription; no App files changed')
        for kind in ['proxies', 'groups']:
            path = extension(profile, items, kind, required=False)
            if path:
                remember(path)
                value = read_yaml(path)
                if not isinstance(value, dict) or any(value.values()):
                    raise ValueError('Nonempty ' + kind + ' extension requires lifecycle review before candidate validation')
        script_path = extension(profile, items, 'script')
        merge_path = extension(profile, items, 'merge')
        merge = read_yaml(merge_path)
        if any(merge.get(key) for key in ['proxies', 'proxy-groups', 'proxy-providers', 'rule-providers', 'rules']):
            raise ValueError('Complex node/group/rule merge requires lifecycle review')
        dns = dns_overlay(merge, current_runtime.get('dns'))
        merge['dns'] = dns
        proposed = {
            script_path: overlay,
            merge_path: yaml.safe_dump(merge, allow_unicode=True, sort_keys=False).encode(),
        }
        for path, data in proposed.items():
            if path in changes and changes[path] != data:
                raise ValueError('Selected profiles require conflicting changes to a shared extension')
            changes[path] = data
        rules_path = extension(profile, items, 'rules', required=False)
        rules = read_yaml(rules_path) if rules_path else {}
        if rules_path:
            remember(rules_path)
        if rules.get('delete') or set(rules) - {'prepend', 'append', 'delete'}:
            raise ValueError('Complex rule extension requires lifecycle review')
        # This is a disposable syntax approximation, not Verge's official pipeline.
        candidate = deep_merge(current_runtime, merge)
        candidate['proxies'] = copy.deepcopy(base['proxies'])
        candidate['proxy-groups'] = copy.deepcopy(base.get('proxy-groups', []))
        candidate['rules'] = (rules.get('prepend') or []) + base.get('rules', []) + (rules.get('append') or [])
        candidate = transform(staged_script, candidate, name)
        groups = {g['name']: g for g in candidate['proxy-groups']}
        if groups.get('Claude', {}).get('proxies') != [pinned]:
            raise ValueError('Claude group did not pin expected physical node')
        file = LOCAL / (sha(str(profile['uid']).encode())[:16] + '-candidate.yaml')
        atomic(file, yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode())
        check = subprocess.run([str(CORE), '-t', '-d', str(core_data), '-f', str(file)], capture_output=True)
        if check.returncode:
            atomic(LOCAL / 'core-validation-error.log', check.stdout + check.stderr)
            raise RuntimeError('Core validation failed; see private .local/core-validation-error.log')
        validations.append({'profile': name, 'core_config_test': 'pass', 'scope': 'static approximation; official App reload and runtime verification required'})
        profile_plans.append({'uid': profile['uid'], 'name': name, 'expected_dns': dns,
                              'adopted_custom_script': not (managed_script(scripts[profile['uid']]) or empty_script(scripts[profile['uid']]))})
    staged = []
    for path, data in changes.items():
        relative = str(path.relative_to(APP.resolve()))
        atomic(LOCAL / 'staged' / relative, data)
        staged.append({'path': relative, 'before_sha256': sha(path.read_bytes()), 'after_sha256': sha(data)})
    plan = {'version': 2, 'state': 'prepared', 'prepared_at': dt.datetime.now().astimezone().isoformat(),
            'app_dir': str(APP), 'app_version': version, 'core_version': 'v1.19.29',
            'meta_sha256': sha(meta_path.read_bytes()), 'pinned_proxy': pinned, 'files': staged,
            'sources': [{'path': path, 'sha256': digest} for path, digest in sources.items()],
            'validation': validations, 'previous_snapshot': previous.get('snapshot'),
            'active_profile_uid': active['uid'], 'source_group': source_group, 'profiles': profile_plans,
            'before_runtime': {key: copy.deepcopy(configs.get(key)) for key in ['mode', 'allow-lan', 'mixed-port', 'port', 'socks-port', 'tun']},
            'before_runtime_file': {key: current_runtime.get(key) for key in ['external-controller', 'external-controller-tls', 'external-controller-unix']},
            'before_tailnet_rules': [r for r in current_runtime.get('rules', []) if isinstance(r, str) and r.startswith('IP-CIDR,100.64.0.0/10,DIRECT')]}
    write_json(PLAN, plan)
    print(json.dumps({'state': 'prepared', 'files': len(staged), 'validation': validations,
                      'pin': 'existing physical node; details kept private',
                      'scope': 'No App files changed; apply then official App reload are separate steps'}, ensure_ascii=False))

def apply():
    plan = json.loads(PLAN.read_text())
    if plan['state'] != 'prepared' or plan['app_dir'] != str(APP):
        raise ValueError('Run prepare first for this App directory')
    if sha((APP / 'profiles.yaml').read_bytes()) != plan['meta_sha256']:
        raise ValueError('Profile metadata changed since prepare')
    for source in plan['sources']:
        if sha(app_file(source['path']).read_bytes()) != source['sha256']:
            raise ValueError('Profile changed since prepare; re-prepare')
    for entry in plan['files']:
        if sha((LOCAL / 'staged' / entry['path']).read_bytes()) != entry['after_sha256']:
            raise ValueError('Staged content changed since validation')
    changed = [x for x in plan['files'] if x['before_sha256'] != x['after_sha256']]
    if not changed:
        plan['state'] = 'applied'
        plan['snapshot'] = plan.get('previous_snapshot')
        write_json(PLAN, plan)
        print('Already matches validated source; no App files changed')
        return
    now = dt.datetime.now().astimezone()
    snapshot = ROOT / 'backups' / now.strftime('%Y-%m-%d') / ('clash-verge-' + now.strftime('%H%M%S-%f'))
    before = snapshot / 'before-change'
    before.mkdir(parents=True, mode=0o700)
    manifest = {'version': 1, 'created_at': now.isoformat(), 'reason': 'Claude network hardening', 'files': []}
    for entry in changed:
        path = app_file(entry['path'])
        saved = before / entry['path']
        atomic(saved, path.read_bytes())
        manifest['files'].append(dict(entry, source=str(path), restore_target=str(path), backup=str(saved.relative_to(snapshot))))
    write_json(snapshot / 'manifest.json', manifest)
    index_path = ROOT / 'backups/manifest.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {'version': 1, 'snapshots': []}
    index['snapshots'].append({'path': str(snapshot.relative_to(ROOT)), 'created_at': now.isoformat(), 'file_count': len(changed)})
    write_json(index_path, index)
    written = []
    try:
        for entry in changed:
            path = app_file(entry['path'])
            if sha(path.read_bytes()) != entry['before_sha256']:
                raise ValueError('Concurrent configuration edit detected')
            atomic(path, (LOCAL / 'staged' / entry['path']).read_bytes())
            written.append(entry)
    except BaseException:
        for entry in reversed(written):
            path = app_file(entry['path'])
            if sha(path.read_bytes()) == entry['after_sha256']:
                atomic(path, (before / entry['path']).read_bytes())
        raise
    plan['state'] = 'applied'
    plan['snapshot'] = str(snapshot.relative_to(ROOT))
    write_json(PLAN, plan)
    print(json.dumps({'state': 'deployed; App reload still required', 'changed_files': len(changed), 'snapshot': plan['snapshot']}, ensure_ascii=False))


def rollback(snapshot_arg):
    snapshot = (ROOT / snapshot_arg).resolve()
    if not snapshot.is_relative_to((ROOT / 'backups').resolve()):
        raise ValueError('Snapshot must be inside this project backups directory')
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    originals = {}
    for entry in manifest['files']:
        saved = (snapshot / entry['backup']).resolve()
        if not saved.is_relative_to(snapshot) or sha(saved.read_bytes()) != entry['before_sha256']:
            raise ValueError('Backup checksum/path failed')
        if str(app_file(entry['path'])) != entry['restore_target']:
            raise ValueError('Restore target differs')
        current_sha = sha(app_file(entry['path']).read_bytes())
        if current_sha not in [entry['before_sha256'], entry['after_sha256']]:
            raise ValueError('Newer edits exist; refusing to overwrite them')
        originals[entry['path']] = app_file(entry['path']).read_bytes()
    written = []
    try:
        for entry in manifest['files']:
            target = app_file(entry['path'])
            if target.read_bytes() != originals[entry['path']]:
                raise ValueError('Concurrent edit detected during rollback')
            atomic(target, (snapshot / entry['backup']).read_bytes())
            written.append(entry)
    except BaseException:
        for entry in reversed(written):
            target = app_file(entry['path'])
            if sha(target.read_bytes()) == entry['before_sha256']:
                atomic(target, originals[entry['path']])
        raise
    if PLAN.exists():
        plan = json.loads(PLAN.read_text())
        plan['state'] = 'rolled-back'
        write_json(PLAN, plan)
    print('Restored verified snapshots. Reload Clash Verge to regenerate runtime.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply', 'rollback'])
    parser.add_argument('--snapshot', help='Project-relative backups snapshot for rollback')
    parser.add_argument('--profiles', action='append', help='Exact remote profile name; repeat for mirrors (default: active remote only)')
    parser.add_argument('--source-group', default='JMS', help='Existing manual Selector group used only when no previous pin exists')
    parser.add_argument('--node', help='Explicit exact physical-node name, present in every selected subscription')
    parser.add_argument('--adopt-existing-script', action='store_true', help='Authorize replacement of reviewed custom scripts; apply backs them up first')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.profiles, args.source_group, args.node, args.adopt_existing_script)
    elif args.action == 'apply':
        if args.profiles or args.node or args.adopt_existing_script or args.source_group != 'JMS':
            parser.error('Selection options apply only to prepare; re-prepare before apply')
        apply()
    elif not args.snapshot:
        parser.error('rollback requires --snapshot')
    else:
        rollback(args.snapshot)

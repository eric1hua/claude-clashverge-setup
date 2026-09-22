#!/usr/bin/env python3
"""Read-only runtime checks; --network makes anonymous HTTP and DNS probes."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from manage import APP, PLAN, ROOT, api, read_yaml, sha, write_json

DOMAINS = ['anthropic.com', 'claude.ai', 'claude.com', 'clau.de',
           'claudemcpclient.com', 'claudemcpcontent.com', 'claudeusercontent.com']


def alias(value):
    if value in ['Claude', 'DIRECT', 'REJECT']:
        return value
    return 'node-sha256:' + hashlib.sha256(value.encode()).hexdigest()[:12]


def probe(url, proxy_url, method='HEAD'):
    host = urlsplit(url).hostname
    process = subprocess.Popen(
        ['curl', '--noproxy', '', '--proxy', proxy_url, '--silent', '--show-error',
         *(['--head'] if method == 'HEAD' else []), '--max-time', '20',
         '--output', '/dev/null', '--write-out', '%{json}', url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    observed = []
    try:
        while process.poll() is None:
            for connection in api('/connections').get('connections', []):
                meta = connection.get('metadata', {})
                if meta.get('host') == host or meta.get('destinationIP') == host:
                    item = {'host': host, 'rule': connection.get('rule'),
                            'rule_payload': connection.get('rulePayload'),
                            'chains': [alias(x) for x in connection.get('chains', [])]}
                    if item not in observed:
                        observed.append(item)
            time.sleep(0.05)
        stdout, _ = process.communicate()
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
    try:
        stats = json.loads(stdout)
    except json.JSONDecodeError:
        stats = {}
    return {'url': url, 'method': method, 'curl_exit': process.returncode,
            'http_status': stats.get('http_code'), 'http_connect': stats.get('http_connect'),
            'tls_verify_result': stats.get('ssl_verify_result'),
            'connected_to_local_proxy': stats.get('remote_ip') in ['127.0.0.1', '::1'],
            'time_seconds': stats.get('time_total'), 'observed_connections': observed,
            'scope': 'Anonymous transport only; no login, API key, cookie, or model request'}


def verify(network=False, health_url=None):
    plan = json.loads(PLAN.read_text())
    runtime = read_yaml(APP / 'clash-verge.yaml')
    meta = read_yaml(APP / 'profiles.yaml')
    configs = api('/configs')
    proxies = api('/proxies')['proxies']
    rules = api('/rules')['rules']
    pinned = plan['pinned_proxy']
    group = proxies.get('Claude', {})
    current_uid = meta.get('current')
    profiles = plan.get('profiles', [])
    active = next((x for x in profiles if x['uid'] == current_uid), None)
    # A moved workspace may retain the original private deployment plan.
    # Read its private candidate instead of requiring old personal public files.
    if plan.get('version', 1) == 1:
        candidate = PLAN.parent / (str(current_uid) + '-candidate.yaml')
        expected_dns = read_yaml(candidate).get('dns') if candidate.exists() else None
        active = {'uid': current_uid} if candidate.exists() else None
    else:
        expected_dns = active.get('expected_dns') if active else None
    checks = {
        'installed_plan': plan.get('state') == 'applied',
        'active_profile_managed': active is not None,
        'rule_mode': configs.get('mode') == 'rule',
        'single_existing_node': group.get('all') == [pinned] and group.get('now') == pinned,
        'core_domains_routed': all(any(r.get('payload') == domain and r.get('type') == 'DomainSuffix' and r.get('proxy') == 'Claude' for r in rules) for domain in DOMAINS),
        'dns_policy_effective': expected_dns is not None and runtime.get('dns') == expected_dns,
        'persistent_files_match_plan': bool(plan['files']) and all(sha((APP / x['path']).read_bytes()) == x['after_sha256'] for x in plan['files']),
    }
    baseline = plan.get('before_runtime', {})
    for key in ['mode', 'allow-lan', 'mixed-port', 'port', 'socks-port', 'ipv6']:
        if key in baseline:
            checks['unchanged_' + key] = configs.get(key) == baseline[key]
    if 'tun' in baseline:
        checks['unchanged_tun_enable'] = configs.get('tun', {}).get('enable', False) == baseline['tun'].get('enable', False)
    controller = plan.get('before_runtime_file', {})
    if 'external-controller' in controller:
        checks['unchanged_tcp_controller'] = runtime.get('external-controller') == controller['external-controller']
    if plan.get('before_tailnet_rules'):
        checks['tailnet_no_resolve'] = any(r.startswith('IP-CIDR,100.64.0.0/10,DIRECT,') and 'no-resolve' in r.split(',')[3:] for r in runtime.get('rules', []))
    result = {'timestamp': dt.datetime.now().astimezone().isoformat(),
              'app_version': plan['app_version'], 'core_version': api('/version')['version'],
              'checks': checks, 'pinned_node': alias(pinned),
              'claude_rule_count': sum(x.get('proxy') == 'Claude' for x in rules),
              'entry_state': {'tun_enabled': configs.get('tun', {}).get('enable', False),
                              'allow_lan': configs.get('allow-lan'),
                              'tcp_controller_enabled': bool(runtime.get('external-controller'))},
              'limitations': ['Authenticated Desktop/TUI/Web sessions not tested',
                              'No claim about account safety or region eligibility',
                              'No exhaustive DNS/IPv6/UDP capture',
                              'DNS policy and lookup checked; upstream DoH path not packet-captured',
                              'Only the active profile is runtime-verified; subscription update not exercised']}
    if network:
        port = configs.get('mixed-port') or configs.get('port')
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('An enabled local HTTP or mixed proxy port is required for transport probes')
        proxy_url = 'http://127.0.0.1:' + str(port)
        result['probes'] = [probe('https://api.anthropic.com/v1/models', proxy_url, 'GET'),
                            probe('https://claude.ai/', proxy_url),
                            probe('https://platform.claude.com/', proxy_url)]
        query = api('/dns/query?name=api.anthropic.com&type=A')
        result['dns_query'] = {'host': 'api.anthropic.com', 'status': query.get('Status'),
                               'answer_count': len(query.get('Answer') or [])}
        result['transport_checks'] = {
            'http_responses_received': all(p['curl_exit'] == 0 and bool(p['http_status']) and p['connected_to_local_proxy'] for p in result['probes']),
            'https_connect_and_certificate': all(p['http_connect'] == 200 and p['tls_verify_result'] == 0 for p in result['probes']),
            'claude_routes_observed': all(any('Claude' in c['chains'] and alias(pinned) in c['chains'] for c in p['observed_connections']) for p in result['probes']),
            'dns_query_ok': query.get('Status') == 0 and bool(query.get('Answer')),
        }
        if health_url:
            parsed = urlsplit(health_url)
            if parsed.scheme not in ['http', 'https'] or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Health URL must be HTTP(S), without credentials, query or fragment')
            result['health_probe'] = probe(health_url, proxy_url, 'GET')
            h = result['health_probe']
            result['transport_checks']['optional_health_ok'] = h['curl_exit'] == 0 and h['http_status'] == 200 and h['connected_to_local_proxy']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--health-url', help='Optional own health endpoint; no private target is embedded')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.health_url and not args.network:
        parser.error('--health-url requires --network')
    report = verify(args.network, args.health_url)
    if args.output:
        write_json(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(report['checks'].values()) and all(report.get('transport_checks', {}).values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())

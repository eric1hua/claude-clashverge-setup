'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const template = fs.readFileSync(
  path.join(__dirname, '../config/overrides/claude-routing.js'), 'utf8'
);
const pinnedName = 'Existing US node';
const plain = (value) => JSON.parse(JSON.stringify(value));

function loadScript(nodeName = pinnedName) {
  const source = template.replace(
    "'__CLAUDE_PINNED_PROXY__'", JSON.stringify(nodeName)
  );
  const context = vm.createContext({});
  vm.runInContext(source, context, { timeout: 1000 });
  return (config) => plain(context.main(plain(config), 'JMS-Mihomo'));
}

function fixture() {
  return {
    'mixed-port': 7897,
    'allow-lan': false,
    'external-controller': '127.0.0.1:9097',
    secret: 'fixture-only-not-a-real-secret',
    dns: { enable: true, ipv6: false, nameserver: ['https://example.invalid/dns-query'] },
    tun: { enable: false },
    proxies: [
      { name: pinnedName, type: 'vless', server: 'example.invalid', port: 443,
        udp: false, 'packet-encoding': 'packetaddr', tls: true,
        'skip-cert-verify': false, 'client-fingerprint': 'fixture-unchanged' },
      { name: 'Other node', type: 'ss', server: 'other.invalid', udp: false }
    ],
    'proxy-groups': [
      { name: 'JMS', type: 'select', proxies: ['Other node', pinnedName] },
      { name: 'Other group', type: 'url-test', proxies: ['Other node'], interval: 300 }
    ],
    rules: [
      'DOMAIN,example.org,JMS',
      'IP-CIDR,100.64.0.0/10,DIRECT',
      'DOMAIN-SUFFIX,example.net,Other group',
      'MATCH,JMS'
    ]
  };
}

test('pins one existing node without changing the general JMS group or other configuration', () => {
  const input = fixture();
  input.dns['nameserver-policy'] = {
    '+.anthropic.com': ['https://1.1.1.1/dns-query#Claude', 'https://8.8.8.8/dns-query#Claude'],
    '+.claude.ai': 'https://1.1.1.1/dns-query#Claude',
    '+.example.org': 'https://8.8.8.8/dns-query#Other group'
  };
  const output = loadScript()(input);
  assert.deepEqual(output['proxy-groups'].slice(0, 2), input['proxy-groups']);
  assert.deepEqual(output['proxy-groups'][2], {
    name: 'Claude', type: 'select', proxies: [pinnedName]
  });
  for (const key of ['mixed-port', 'allow-lan', 'external-controller', 'secret', 'dns', 'tun']) {
    assert.deepEqual(output[key], input[key], key);
  }
  assert.deepEqual(output.proxies, input.proxies);
});

test('is idempotent and replaces a stale Claude group with the one-node group', () => {
  const run = loadScript();
  const input = fixture();
  input['proxy-groups'].push({ name: 'Claude', type: 'fallback', proxies: ['DIRECT', 'JMS'] });
  const once = run(input);
  assert.deepEqual(run(once), once);
  assert.equal(once['proxy-groups'].filter((group) => group.name === 'Claude').length, 1);
  assert.equal(new Set(once.rules).size, once.rules.length);
});

test('adds no-resolve to the existing tailnet rule before domain rules and preserves MATCH order', () => {
  const input = fixture();
  const output = loadScript()(input);
  assert.equal(output.rules[0], 'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve');
  assert.equal(output.rules[1], 'DOMAIN-SUFFIX,anthropic.com,Claude');
  assert.deepEqual(output.rules.slice(-3), [input.rules[0], input.rules[2], input.rules[3]]);
  assert.equal(output.rules.at(-1), 'MATCH,JMS');
});

test('keeps existing tailnet flags and does not duplicate no-resolve', () => {
  const run = loadScript();
  for (const flags of ['no-resolve', 'future-flag', 'future-flag,no-resolve', 'no-resolve,future-flag']) {
    const input = fixture();
    input.rules[1] += ',' + flags;
    const output = run(input);
    const expected = flags.includes('no-resolve')
      ? input.rules[1] : input.rules[1] + ',no-resolve';
    assert.equal(output.rules[0], expected);
    assert.equal(output.rules[0].split(',').filter((part) => part === 'no-resolve').length, 1);
    assert.deepEqual(run(output), output);
  }
});

test('does not add tailnet rules or move non-DIRECT routes for that range', () => {
  const input = fixture();
  input.rules[1] = 'IP-CIDR,100.64.0.0/10,JMS,no-resolve';
  const output = loadScript()(input);
  assert.equal(output.rules[0], 'DOMAIN-SUFFIX,anthropic.com,Claude');
  assert.deepEqual(output.rules.slice(-input.rules.length), input.rules);
});

test('missing pinned node yields a valid REJECT-only group even when JMS is absent', () => {
  const input = fixture();
  input.proxies = [input.proxies[1]];
  input['proxy-groups'] = [{ name: 'Other group', type: 'select', proxies: ['Other node'] }];
  input.rules = ['MATCH,Other group'];
  const output = loadScript()(input);
  assert.deepEqual(output['proxy-groups'].at(-1), { name: 'Claude', type: 'select', proxies: ['REJECT'] });
  assert.equal(output.rules[0], 'DOMAIN-SUFFIX,anthropic.com,Claude');
  assert.ok(!output['proxy-groups'].at(-1).proxies.includes('DIRECT'));
});

test('physical Claude node conflict blocks dedicated routes without duplicate node/group names', () => {
  const input = fixture();
  input.proxies.push({ name: 'Claude', type: 'ss', server: 'conflict.invalid' });
  input['proxy-groups'].push({ name: 'Claude', type: 'select', proxies: ['JMS'] });
  input.dns['nameserver-policy'] = {
    '+.anthropic.com': ['https://1.1.1.1/dns-query#Claude', 'https://8.8.8.8/dns-query#Claude'],
    '+.claude.ai': 'https://1.1.1.1/dns-query#Claude',
    '+.mixed.example.org': ['https://1.1.1.1/dns-query#Claude', 'https://8.8.8.8/dns-query#Other group'],
    '+.example.org': 'https://8.8.8.8/dns-query#Other group',
    '+.suffix.example.org': 'https://1.1.1.1/dns-query#ClaudeOther'
  };
  const run = loadScript();
  const output = run(input);
  assert.deepEqual(output.proxies.at(-1), input.proxies.at(-1));
  assert.equal(output['proxy-groups'].filter((group) => group.name === 'Claude').length, 0);
  assert.equal(output.rules[1], 'DOMAIN-SUFFIX,anthropic.com,REJECT');
  assert.equal(output.rules.filter((rule) => rule.endsWith(',REJECT')).length, 12);
  assert.deepEqual(output.dns, {
    ...input.dns,
    'nameserver-policy': {
      ...input.dns['nameserver-policy'],
      '+.anthropic.com': ['https://1.1.1.1/dns-query#REJECT', 'https://8.8.8.8/dns-query#REJECT'],
      '+.claude.ai': 'https://1.1.1.1/dns-query#REJECT',
      '+.mixed.example.org': ['https://1.1.1.1/dns-query#REJECT', 'https://8.8.8.8/dns-query#Other group']
    }
  });
  assert.deepEqual(run(output), output);
});

test('removes old managed REJECT rules when the conflict disappears without losing unrelated rules', () => {
  const run = loadScript();
  const input = fixture();
  input.rules.unshift('DOMAIN-SUFFIX,anthropic.com,REJECT');
  input.rules.unshift('DOMAIN-SUFFIX,anthropic.com,JMS');
  const output = run(input);
  assert.ok(!output.rules.includes('DOMAIN-SUFFIX,anthropic.com,REJECT'));
  assert.equal(output.rules.filter((rule) => rule === 'DOMAIN-SUFFIX,anthropic.com,Claude').length, 1);
  assert.ok(output.rules.includes('DOMAIN-SUFFIX,anthropic.com,JMS'));
});

test('adds only the specified dedicated destinations and no broad keyword, NTP, or IP rules', () => {
  const output = loadScript()({ proxies: fixture().proxies, rules: ['MATCH,DIRECT'] });
  const added = output.rules.slice(0, -1);
  assert.equal(added.length, 12);
  assert.deepEqual(added.filter((rule) => rule.startsWith('DOMAIN,')), [
    'DOMAIN,anthropic.auth0.com,Claude',
    'DOMAIN,servd-anthropic-website.b-cdn.net,Claude',
    'DOMAIN,anthropic.com.cdn.cloudflare.net,Claude',
    'DOMAIN,anthropic-com.ghost.io,Claude'
  ]);
  assert.equal(added.at(-1), 'GEOSITE,anthropic,Claude');
  assert.ok(added.every((rule) => !/DOMAIN-KEYWORD|sentry|datadog|sift|ntp|IP-CIDR/i.test(rule)));
});

test('deployment substitution preserves exact node names including quotes and backslashes', () => {
  const name = 'US "pinned" \\ route';
  const output = loadScript(name)({ proxies: [{ name, type: 'ss' }], rules: ['MATCH,DIRECT'] });
  assert.deepEqual(output['proxy-groups'][0].proxies, [name]);
});

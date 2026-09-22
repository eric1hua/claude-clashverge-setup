// claude-clashverge-setup managed v1
// Per-profile routing overlay; node transport settings remain subscription-owned.
// Deployment replaces this entire string literal with JSON.stringify(nodeName).
// The node must already exist: this script never adds or changes an exit node.
const PINNED_PROXY = '__CLAUDE_PINNED_PROXY__';

const CLAUDE_RULES = [
  ['DOMAIN-SUFFIX', 'anthropic.com'],
  ['DOMAIN-SUFFIX', 'claude.ai'],
  ['DOMAIN-SUFFIX', 'claude.com'],
  ['DOMAIN-SUFFIX', 'clau.de'],
  ['DOMAIN-SUFFIX', 'claudemcpclient.com'],
  ['DOMAIN-SUFFIX', 'claudemcpcontent.com'],
  ['DOMAIN-SUFFIX', 'claudeusercontent.com'],
  ['DOMAIN', 'anthropic.auth0.com'],
  ['DOMAIN', 'servd-anthropic-website.b-cdn.net'],
  ['DOMAIN', 'anthropic.com.cdn.cloudflare.net'],
  ['DOMAIN', 'anthropic-com.ghost.io'],
  ['GEOSITE', 'anthropic']
];

function ruleParts(rule) {
  return typeof rule === 'string'
    ? rule.split(',').map(function (part) { return part.trim(); })
    : [];
}

function isManagedClaudeRule(rule) {
  const parts = ruleParts(rule);
  if (parts[2] !== 'Claude' && parts[2] !== 'REJECT') return false;
  return CLAUDE_RULES.some(function (spec) {
    return parts[0] === spec[0] && parts[1] === spec[1];
  });
}

function isExistingTailnetRule(rule) {
  const parts = ruleParts(rule);
  return parts[0] === 'IP-CIDR' &&
    parts[1] === '100.64.0.0/10' && parts[2] === 'DIRECT';
}

function tailnetWithoutDnsResolution(rule) {
  const parts = ruleParts(rule);
  if (parts.slice(3).indexOf('no-resolve') !== -1) return rule;
  // This IP rule runs before domain rules. Without no-resolve, testing a public
  // hostname against the tailnet range can trigger DNS before Claude routing.
  // Preserve any existing flags; normalize only this already-present range.
  return rule.replace(/\s+$/, '') + ',no-resolve';
}

function rejectConflictingClaudeDnsRoute(value) {
  return typeof value === 'string' ? value.replace(/#Claude$/, '#REJECT') : value;
}

function main(config, profileName) {
  // profileName is supplied by Verge; attachment to approved profiles is managed
  // by deployment, not inferred from a potentially renamed profile here.
  const proxies = Array.isArray(config.proxies) ? config.proxies : [];
  const hasPinnedProxy = proxies.some(function (proxy) {
    return proxy && proxy.name === PINNED_PROXY;
  });
  const hasClaudeNode = proxies.some(function (proxy) {
    return proxy && proxy.name === 'Claude';
  });
  const groups = Array.isArray(config['proxy-groups'])
    ? config['proxy-groups'] : [];
  config['proxy-groups'] = groups.filter(function (group) {
    return !group || group.name !== 'Claude';
  });

  // A physical node named Claude cannot coexist with a Claude group. Preserve
  // that node and route Claude destinations directly to REJECT in this case.
  // Do not throw: a script failure can let the original profile keep running.
  const target = hasClaudeNode ? 'REJECT' : 'Claude';
  if (!hasClaudeNode) {
    config['proxy-groups'].push({
      name: 'Claude',
      type: 'select',
      proxies: [hasPinnedProxy ? PINNED_PROXY : 'REJECT']
    });
  } else {
    // The DNS merge can explicitly route resolvers through #Claude. With a
    // conflicting physical node, reject those exact policy routes as well;
    // otherwise DNS could still leave through that node despite REJECT rules.
    const policy = config.dns && config.dns['nameserver-policy'];
    if (policy && typeof policy === 'object' && !Array.isArray(policy)) {
      Object.keys(policy).forEach(function (domain) {
        const value = policy[domain];
        policy[domain] = Array.isArray(value)
          ? value.map(rejectConflictingClaudeDnsRoute)
          : rejectConflictingClaudeDnsRoute(value);
      });
    }
  }

  const originalRules = Array.isArray(config.rules) ? config.rules : [];
  const tailnetRules = [];
  const otherRules = [];
  originalRules.forEach(function (rule) {
    if (isExistingTailnetRule(rule)) tailnetRules.push(tailnetWithoutDnsResolution(rule));
    else if (!isManagedClaudeRule(rule)) otherRules.push(rule);
  });
  const claudeRules = CLAUDE_RULES.map(function (spec) {
    return spec[0] + ',' + spec[1] + ',' + target;
  });
  // Keep existing tailnet flags and unrelated rule order. Add no new tailnet
  // ranges, generic keywords, or NTP rules.
  config.rules = tailnetRules.concat(claudeRules, otherRules);
  return config;
}

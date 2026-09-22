#!/usr/bin/env python3
"""Check Git-tracked files for accidental local state and common secret patterns.

This guard complements manual review; it is not a comprehensive secret scanner.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOT = {'.gitignore', 'AGENTS.md', 'README.md', 'PROGRESS.md', 'install.sh', 'requirements.txt', 'LICENSE'}
ALLOWED_DIRS = {'config', 'scripts', 'tests', 'docs', '.github'}
ALLOWED_LOCAL_READMES = {'backups/README.md', 'diagnostics/README.md'}
RULES = [
    ('home directory path', re.compile(r'/Users/[A-Za-z0-9_.-]+/')),
    ('provider account or endpoint', re.compile(r'JMS-\d{4,}|portablesubmarines\.com')),
    ('private tailnet host', re.compile(r'\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b')),
    ('GitHub credential', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('Anthropic credential', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{16,}')),
    ('private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('credential in URL', re.compile(r'https?://(?P<user>[^\s/]+):(?P<password>[^\s/@]+)@(?P<host>[A-Za-z0-9.-]+)')),
]


def scan(files):
    problems = []
    for relative in files:
        path = Path(relative)
        if relative not in ALLOWED_LOCAL_READMES and not (len(path.parts) == 1 and relative in ALLOWED_ROOT) and not (path.parts and path.parts[0] in ALLOWED_DIRS):
            problems.append((relative, 'not on publication allowlist'))
            continue
        if any(x in path.parts for x in ['.local', '.learnings', '__pycache__', '.DS_Store']):
            problems.append((relative, 'private/generated file'))
            continue
        absolute = ROOT / path
        if absolute.is_symlink():
            problems.append((relative, 'symlink requires manual review'))
            continue
        try:
            text = absolute.read_text()
        except UnicodeError:
            problems.append((relative, 'unexpected binary'))
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for label, pattern in RULES:
                for match in pattern.finditer(line):
                    # The generic RFC 6598 range is a routing rule, not a host.
                    if label == 'private tailnet host' and match.group() == '100.64.0.0/10'.split('/')[0] and line[match.end():].startswith('/10'):
                        continue
                    # One exact dummy credential on the reserved test domain is
                    # used to verify that health probes reject credential URLs.
                    if (label == 'credential in URL' and path.parts[0] == 'tests' and
                            match.group('user') == 'user' and match.group('password') == 'secret' and
                            match.group('host') == 'example.test'):
                        continue
                    problems.append((relative + ':' + str(line_no), label))
    return problems


if __name__ == '__main__':
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    files = [x for x in result.stdout.decode().split('\0') if x]
    if not files:
        print('No tracked files; stage the publication allowlist before checking.', file=sys.stderr)
        raise SystemExit(1)
    errors = scan(files)
    for path, reason in errors:
        print(path + ': ' + reason, file=sys.stderr)
    print('Publication allowlist checked: ' + str(len(files)) + ' files, ' + str(len(errors)) + ' findings')
    raise SystemExit(bool(errors))

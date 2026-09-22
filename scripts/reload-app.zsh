#!/bin/zsh
set -eu

# Use the application's normal lifecycle, because Mihomo reload cannot execute
# Verge extensions. A short interruption affects all traffic through this proxy.
/usr/bin/osascript -e 'tell application "Clash Verge" to quit'
for attempt in {1..40}; do
  if ! /usr/bin/pgrep -x clash-verge >/dev/null; then
    break
  fi
  /bin/sleep 0.25
done
if /usr/bin/pgrep -x clash-verge >/dev/null; then
  print -u2 'Clash Verge did not exit normally; no forced termination attempted.'
  exit 1
fi
/usr/bin/open -a "${CLAUDE_CLASH_APP_BUNDLE:-/Applications/Clash Verge.app}"
print 'Clash Verge reopened. Run scripts/verify.py to confirm regenerated runtime.'

#!/bin/bash
# Compatible with the Bash 3.2 shipped by macOS.
set -euo pipefail
umask 077
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ASSUME_YES=0
DRY_RUN=0
PREPARE_ARGS=(prepare)
usage() {
  cat <<'HELP'
Clash Verge Claude network installer (macOS)
Usage: ./install.sh [options]
  --dry-run                  Validate candidates without changing the App
  --yes                      Apply and restart without an interactive prompt
  --profile NAME             Existing subscription; repeat to include more
  --source-group NAME        Selector to inherit on first install (default JMS)
  --node EXACT_NAME          Explicitly choose an existing physical node
  --adopt-existing-script    Replace reviewed custom profile scripts after backup
  -h, --help                 Show this help

Requires Python 3.9+, Node.js 18+, and running Clash Verge 2.5.2 / Mihomo 1.19.29.
Dependencies and candidates stay in .local/. Applying briefly interrupts proxy
connections through the official App restart. No subscription URL is requested.
HELP
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --profile|--source-group|--node)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "Missing value: $1" >&2; exit 2; }
      if [ "$1" = '--profile' ]; then PREPARE_ARGS+=(--profiles "$2"); else PREPARE_ARGS+=("$1" "$2"); fi
      shift 2 ;;
    --adopt-existing-script) PREPARE_ARGS+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[ "$(uname -s)" = Darwin ] || { echo 'This installer supports macOS only.' >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo 'Run as the desktop user; do not use sudo.' >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null || { echo 'Python 3.9+ is required.' >&2; exit 1; }
command -v node >/dev/null || { echo 'Node.js 18+ is required.' >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Python 3.9+ is required")'
node -e 'if (Number(process.versions.node.split(".")[0]) < 18) { console.error("Node.js 18+ is required"); process.exit(1); }'
mkdir -p "$PROJECT_ROOT/.local" "$PROJECT_ROOT/backups"
chmod 700 "$PROJECT_ROOT/.local" "$PROJECT_ROOT/backups"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$PROJECT_ROOT/.local/pycache"
if [ ! -x "$PROJECT_ROOT/.local/venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.local/venv"
fi
INSTALL_PYTHON="$PROJECT_ROOT/.local/venv/bin/python"
if ! "$INSTALL_PYTHON" -c 'import yaml; assert yaml.__version__ == "6.0.3"' >/dev/null 2>&1; then
  "$INSTALL_PYTHON" -m pip --disable-pip-version-check install --no-cache-dir --index-url https://pypi.org/simple --require-hashes --only-binary=:all: -r "$PROJECT_ROOT/requirements.txt"
fi
echo 'Preparing and validating; no live files changed yet.'
"$INSTALL_PYTHON" "$PROJECT_ROOT/scripts/manage.py" "${PREPARE_ARGS[@]}"
if [ "$DRY_RUN" -eq 1 ]; then
  echo 'Dry run passed. No App files changed or restart performed.'
  exit 0
fi
if [ "$ASSUME_YES" -ne 1 ]; then
  if [ ! -t 0 ]; then
    echo 'No terminal for confirmation. Inspect the dry run, then pass --yes if authorized.' >&2
    exit 2
  fi
  printf 'Apply the validated plan, back up originals, and restart Clash Verge? [y/N] '
  read -r ANSWER
  case "$ANSWER" in y|Y|yes|YES) ;; *) echo 'Cancelled; no live files changed.'; exit 0 ;; esac
fi
"$INSTALL_PYTHON" "$PROJECT_ROOT/scripts/manage.py" apply
if ! /bin/zsh "$PROJECT_ROOT/scripts/reload-app.zsh"; then
  echo 'Files deployed, but reload failed. Check .local/plan.json and the rollback runbook.' >&2
  exit 1
fi
READY=0
for ATTEMPT in $(seq 1 15); do
  if "$INSTALL_PYTHON" "$PROJECT_ROOT/scripts/verify.py" --output "$PROJECT_ROOT/.local/installation-report.json" >"$PROJECT_ROOT/.local/last-verify.log" 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo 'Runtime verification did not pass. Installation is not verified.' >&2
  echo 'Inspect .local/last-verify.log and docs/operations/runbook.md.' >&2
  exit 1
fi
echo 'Runtime verified. Running anonymous transport probes.'
if ! "$INSTALL_PYTHON" "$PROJECT_ROOT/scripts/verify.py" --network --output "$PROJECT_ROOT/.local/installation-report.json"; then
  echo 'Configuration applied, but network acceptance is incomplete; inspect the report.' >&2
  exit 1
fi
echo 'Installed and anonymous transport checks passed. Authenticated sessions were not tested.'

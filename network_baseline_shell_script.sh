#!/usr/bin/env bash
set -u

BASELINE="/central/network-baseline/baseline.json"
LOG="/central/network-baseline/network.log"
PYTHON="/usr/bin/python3"
PY_SCRIPT="/usr/local/bin/network_baseline.py"
SITE=""
PING_COUNT=2
SAVE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --site) SITE="$2"; shift 2 ;;
        --baseline-file) BASELINE="$2"; shift 2 ;;
        --log) LOG="$2"; shift 2 ;;
        --python) PYTHON="$2"; shift 2 ;;
        --python-script) PY_SCRIPT="$2"; shift 2 ;;
        --ping-count) PING_COUNT="$2"; shift 2 ;;
        --save-baseline) SAVE=1; shift ;;
        -h|--help)
            echo "Usage: $0 [--site SITE] [--save-baseline]"
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 20 ;;
    esac
done

SITE="${SITE:-$(hostname -s)}"

if [[ "$SITE" =~ ^SJC-NVIDIA-3200-50(0[1-9]|1[0-6])$ ]]; then
    N="${BASH_REMATCH[1]}"
    EXPECTED_IP="192.168.0.$((100 + 10#$N))"
else
    echo "ERROR: invalid site '$SITE'" >&2
    exit 20
fi

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log() {
    local ts
    ts="$(date '+%Y-%m-%dT%H:%M:%S%z')"
    echo "$ts [$SITE] $1" | tee -a "$LOG"
}

ping_eno1() {
    ping -c "$PING_COUNT" -W 2 "$EXPECTED_IP" >/dev/null 2>&1
}

python_check() {
    "$PYTHON" "$PY_SCRIPT" \
        --site "$SITE" \
        --baseline-file "$BASELINE" \
        --ip "$EXPECTED_IP" "$@"
    return $?
}

log "START eno1_expected=$EXPECTED_IP"

# 1. PRE-REBOOT: create the baseline.
if [[ "$SAVE" -eq 1 ]]; then
    if ping_eno1; then
        log "PRE_REBOOT_PING_OK $EXPECTED_IP"
    else
        log "PRE_REBOOT_PING_FAILED $EXPECTED_IP"
    fi

    python_check --save-baseline
    RC=$?
    [[ "$RC" -eq 0 ]] && log "STATUS=BASELINE_SAVED" ||
                           log "STATUS=ERROR exit=$RC"
    exit "$RC"
fi

# 2. POST-REBOOT: ping then compare against the saved baseline.
if ping_eno1; then
    log "POST_REBOOT_PING_OK $EXPECTED_IP"
else
    log "POST_REBOOT_PING_FAILED $EXPECTED_IP"
fi

python_check
RC=$?

if [[ "$RC" -eq 0 ]]; then
    log "STATUS=NO_CHANGE"
    exit 0
fi

# 3. Requirement: if changed, re-run the check.
if [[ "$RC" -eq 10 ]]; then
    log "STATUS=CHANGE_DETECTED RE-RUN"

    if ping_eno1; then
        log "RE_RUN_PING_OK $EXPECTED_IP"
    else
        log "RE_RUN_PING_FAILED $EXPECTED_IP"
    fi

    python_check
    RERUN_RC=$?

    if [[ "$RERUN_RC" -eq 0 ]]; then
        log "STATUS=CHANGE_CLEARED_ON_RERUN"
        exit 0
    elif [[ "$RERUN_RC" -eq 10 ]]; then
        log "STATUS=CHANGE_CONFIRMED"
        exit 10
    else
        log "STATUS=ERROR rerun_exit=$RERUN_RC"
        exit "$RERUN_RC"
    fi
fi

log "STATUS=ERROR verification_exit=$RC"
exit "$RC"

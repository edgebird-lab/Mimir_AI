#!/usr/bin/env bash
# Containment check for a running Mimir container (default: the inference service).
# For Zone A this asserts the sandbox goals hold; the full T1 suite for Zone S lives separately.
set -euo pipefail

SVC="${1:-inference}"
DC="${DC:-sudo docker compose}"   # Mimir runs on the native engine
dcx() { $DC exec -T "$SVC" sh -c "$1" 2>&1 || true; }
fail=0
check() { # desc, cmd-that-should-FAIL-or-be-empty
  local desc="$1" out; out="$(dcx "$2")"
  if [[ -z "$out" || "$out" == *"o such file"* || "$out" == *"ermission denied"* || "$out" == *"ead-only"* ]]; then
    echo "PASS: $desc"
  else echo "FAIL: $desc  -> $out"; fail=1; fi
}

echo "== containment: $SVC =="
echo -n "uid: "; dcx 'id'
check "no /home"                 'ls /home 2>&1; ls /home/* 2>&1'
check "no host .env reachable"   'find / -xdev -name ".env" 2>/dev/null | head'
check "/etc/shadow unreadable"   'cat /etc/shadow 2>&1 | head -c 40'
check "no /dev/kfd"              'ls /dev/kfd 2>&1'
check "no docker.sock"           'ls /var/run/docker.sock 2>&1'
check "rootfs read-only"         'touch /rootwrite-test 2>&1'
check "no secrets in pid1 env"   'cat /proc/1/environ 2>/dev/null | tr "\0" "\n" | grep -iE "key|secret|token|pass" | head'

echo "== non-root + no caps =="
dcx 'id | grep -q "uid=0" && echo "FAIL: running as root" || echo "PASS: non-root"'
dcx 'command -v capsh >/dev/null && capsh --print | grep -i "current:" || echo "(capsh not installed)"'

[[ "$fail" -eq 0 ]] && echo "== ALL CONTAINMENT CHECKS PASSED ==" || { echo "== CONTAINMENT FAILURES PRESENT =="; exit 1; }

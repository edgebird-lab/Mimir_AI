#!/usr/bin/env bash
# Regenerate proxy/filter from config/egress-allowlist.txt (host -> anchored regex).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/config/egress-allowlist.txt"
DST="$ROOT/proxy/filter"
{
  echo "# AUTO-GENERATED from config/egress-allowlist.txt — do not edit by hand."
  echo "# FilterDefaultDeny Yes => each line is an ALLOWED host. Empty == zero egress."
  grep -vE '^\s*#|^\s*$' "$SRC" 2>/dev/null | while read -r h; do
    # allow the host and its subdomains; escape dots
    printf '(^|\\.)%s$\n' "$(echo "$h" | sed 's/\./\\./g')"
  done
} > "$DST"
echo "wrote $DST:"; cat "$DST"

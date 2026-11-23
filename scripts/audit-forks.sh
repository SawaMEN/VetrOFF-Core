#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -eq 0 || ( $# -eq 1 && "$1" == "--strict" ) ]] || { echo "Usage: $0 [--strict]" >&2; exit 2; }
command -v git >/dev/null || { echo "Missing tool: git" >&2; exit 1; }
source "$ROOT/patches/fork-snapshots.lock"

entries=(
  "hiddify-core|https://github.com/SawaMEN/hiddify-core.git|$HIDDIFY_CORE_FORK_SHA"
  "hiddify-sing-box|https://github.com/SawaMEN/hiddify-sing-box.git|$SING_BOX_FORK_SHA"
  "ray2sing|https://github.com/SawaMEN/ray2sing.git|$RAY2SING_FORK_SHA"
)
drift=0
errors=0
summary="${GITHUB_STEP_SUMMARY:-}"
if [[ -n "$summary" ]]; then
  {
    echo "## Personal fork patch-snapshot audit"
    echo
    echo "| Fork | Audited commit | Current main | Result |"
    echo "| --- | --- | --- | --- |"
  } >> "$summary"
fi
for entry in "${entries[@]}"; do
  IFS="|" read -r name url expected <<< "$entry"
  if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid audited SHA for $name: $expected" >&2
    exit 1
  fi
  if ! remote="$(git ls-remote --exit-code "$url" refs/heads/main)"; then
    actual="unavailable"
    result="ERROR: cannot resolve fork main"
    errors=$((errors + 1))
  else
    actual="${remote%%$'\t'*}"
    if [[ ! "$actual" =~ ^[0-9a-f]{40}$ ]]; then
      result="ERROR: invalid SHA"
      errors=$((errors + 1))
    elif [[ "$actual" == "$expected" ]]; then
      result="OK"
    else
      result="DRIFT: regenerate and verify patches"
      drift=$((drift + 1))
    fi
  fi
  printf "%-18s audited=%s current=%s status=%s\n" "$name" "$expected" "$actual" "$result"
  if [[ -n "$summary" ]]; then
    printf '| %s | `%s` | `%s` | %s |\n' "$name" "${expected:0:12}" "${actual:0:12}" "$result" >> "$summary"
  fi
done
echo "Audited forks checked: 3; changed: $drift; lookup errors: $errors"
if (( errors > 0 )); then exit 1; fi
if (( drift > 0 )) && [[ "${1:-}" == "--strict" ]]; then
  echo "Personal forks have commits not yet represented by the audited patch snapshots." >&2
  exit 1
fi

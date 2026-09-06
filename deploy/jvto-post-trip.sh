#!/usr/bin/env bash
#
# Fire the trip-photo publisher at a time we actually choose.
#
# GitHub's own `schedule:` trigger is queued behind every other repo's and ran
# 2h18m to 3h49m late on each of the five days measured - 19:00 WIB asked for,
# 22:30 delivered. workflow_dispatch is not queued: every manual run in the
# history started in the same second it was requested. So the clock lives here
# and GitHub only does the work.
#
# The workflow keeps its own schedule as a fallback for a day this box is down.
# The two cannot double-post: the publisher gates on the sheet's own Uploaded
# At timestamps, so a trip posted at 19:00 blocks the late run that follows.
#
# Passes no inputs, so `force` stays false and the interval gate applies -
# exactly what the schedule trigger does.
#
#   jvto-post-trip.sh             dispatch
#   jvto-post-trip.sh --dry-run   check the token and workflow, dispatch nothing

set -euo pipefail

ENV_FILE=/var/www/sosmed-studio/.env
LOG=/var/log/jvto-post-trip.log
WORKFLOW=post-trip-photos.yml

log() { printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

dry_run=false
[[ ${1:-} == --dry-run ]] && dry_run=true

if [[ ! -r $ENV_FILE ]]; then
  log "FAILED: cannot read $ENV_FILE"
  exit 1
fi

# The studio panel already holds a token with dispatch rights on this repo.
# Reading it from there keeps one copy on the box to rotate instead of two.
value_of() { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'; }

TOKEN=$(value_of GITHUB_TOKEN)
REPO=$(value_of GITHUB_REPO)
REF=$(value_of GITHUB_REF)
REF=${REF:-main}

if [[ -z $TOKEN || -z $REPO ]]; then
  log "FAILED: GITHUB_TOKEN or GITHUB_REPO missing from $ENV_FILE"
  exit 1
fi

api="https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW"
out=$(mktemp) && trap 'rm -f "$out"' EXIT

if $dry_run; then
  code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 60 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$api")
  if [[ $code == 200 ]]; then
    log "dry run OK: $REPO@$REF, workflow $(jq -r .state <"$out"); nothing dispatched"
    exit 0
  fi
  log "dry run FAILED: GitHub answered $code - $(head -c 200 "$out" | tr -d '\n')"
  exit 1
fi

code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 60 -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/dispatches" \
  -d "{\"ref\":\"$REF\"}")

# A dispatch answers 204 with an empty body; anything else carries a reason.
if [[ $code == 204 ]]; then
  log "dispatched $WORKFLOW on $REPO@$REF"
  exit 0
fi

log "FAILED: GitHub answered $code - $(head -c 200 "$out" | tr -d '\n')"
exit 1

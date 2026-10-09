#!/usr/bin/env bash
# sync-github.sh — reconcile the local checkout with its GitHub upstream.
#
#   scripts/sync-github.sh           # fetch → ff/rebase → push, as needed
#   scripts/sync-github.sh --check   # report divergence only, change nothing
#   scripts/sync-github.sh --help
#
# Written for cron. The piwheels workflow commits stats/piwheels.json to
# master once a day (03:17 UTC), so local commits and origin/master
# diverge regularly; this replays local commits on top of upstream
# (rebase, not merge) and pushes. Quiet when already in sync; exits
# non-zero and logs when a human must intervene.
#
#   crontab -e:
#     */30 * * * * /home/x/nlp/prj.PhD/gnosislab/scripts/sync-github.sh
#
# Guarantees: never force-pushes, never commits or stashes uncommitted
# work, never switches branches. On a rebase conflict the local tree is
# left exactly as found.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/gnosislab"
LOG="$LOG_DIR/sync-github.log"
LOCK="$LOG_DIR/sync-github.lock"

CHECK=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "sync-github: unknown option '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# Cron supplies a minimal environment: gh provides the credential
# helper, and prompts must never block a background run.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"
export GIT_TERMINAL_PROMPT=0

mkdir -p "$LOG_DIR"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }
ok()  { log "$*"; }
err() { log "ERROR $*"; printf 'sync-github: %s\n' "$*" >&2; }

exec 9>"$LOCK"
flock -n 9 || { log "skipped (lock held by another run)"; exit 0; }

cd "$ROOT"

BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ -z "$BRANCH" ]]; then
  err "detached HEAD — refusing to sync"
  exit 1
fi

if ! git fetch origin --quiet 2>>"$LOG"; then
  err "git fetch failed (network/auth?) — left untouched"
  exit 1
fi

UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [[ -z "$UPSTREAM" ]]; then
  if git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
    UPSTREAM="origin/$BRANCH"
  else
    err "no upstream for $BRANCH — nothing to compare against"
    exit 1
  fi
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "$UPSTREAM")"
BASE="$(git merge-base HEAD "$UPSTREAM")"

if [[ "$LOCAL" == "$REMOTE" ]]; then
  ok "in sync ($BRANCH @ ${LOCAL:0:7})"
  exit 0
fi

AHEAD="$(git rev-list --count "$UPSTREAM"..HEAD)"
BEHIND="$(git rev-list --count HEAD.."$UPSTREAM")"
dirty() { [[ -n "$(git status --porcelain --untracked-files=no)" ]]; }

if [[ "$CHECK" -eq 1 ]]; then
  printf '%s is ahead %d, behind %d vs %s\n' "$BRANCH" "$AHEAD" "$BEHIND" "$UPSTREAM"
  dirty && printf 'note: uncommitted changes present\n'
  exit 0
fi

push() {
  git push origin "HEAD:refs/heads/$BRANCH" >>"$LOG" 2>&1
}

if [[ "$LOCAL" == "$BASE" ]]; then
  # strictly behind → fast-forward; git refuses if it would clobber
  # uncommitted changes, which is exactly what we want
  if git merge --ff-only "$UPSTREAM" >>"$LOG" 2>&1; then
    ok "fast-forwarded $BRANCH +$BEHIND → ${REMOTE:0:7}"
  else
    err "fast-forward of $BRANCH refused — likely uncommitted changes in updated files; commit or stash, then rerun"
    exit 1
  fi
elif [[ "$REMOTE" == "$BASE" ]]; then
  # strictly ahead → push (uncommitted state is irrelevant to push)
  if push; then
    ok "pushed $AHEAD commit(s) on $BRANCH → origin"
  else
    err "push of $BRANCH refused — check 'git push' manually"
    exit 1
  fi
else
  # diverged → replay local commits on upstream, then push
  if dirty; then
    err "$BRANCH diverged (ahead $AHEAD, behind $BEHIND) AND has uncommitted changes — resolve by hand: git pull --rebase && git push"
    exit 1
  fi
  if git rebase "$UPSTREAM" >>"$LOG" 2>&1; then
    if push; then
      ok "rebased $AHEAD local commit(s) onto $UPSTREAM (+$BEHIND) and pushed"
    else
      err "rebase succeeded but push refused — run 'git push' manually"
      exit 1
    fi
  else
    git rebase --abort >>"$LOG" 2>&1 || true
    err "$BRANCH diverged (ahead $AHEAD, behind $BEHIND) and rebase hit conflicts — local left untouched; resolve: git pull --rebase"
    exit 1
  fi
fi

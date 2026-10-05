#!/usr/bin/env bash
# bump-version.sh — bump the distribution version, commit, and tag.
#
#   scripts/bump-version.sh            # patch bump (0.1.17 → 0.1.18)
#   scripts/bump-version.sh minor      # 0.1.17 → 0.2.0
#   scripts/bump-version.sh major      # 0.1.17 → 1.0.0
#   scripts/bump-version.sh 0.3.0      # explicit version
#   scripts/bump-version.sh --no-git   # bump only, no commit/tag
#
# The repo is flat: pyproject.toml at the root IS the distribution
# metadata. Order is bump → lock → commit → tag so a v<ver> tag can
# never point at a tree whose pyproject disagrees (the publish
# workflow also refuses on a tag/version mismatch).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="$ROOT/pyproject.toml"

GIT=1
KIND="patch"
for arg in "$@"; do
  case "$arg" in
    --no-git) GIT=0 ;;
    patch|minor|major) KIND="$arg" ;;
    [0-9]*.[0-9]*.[0-9]*) KIND="explicit"; NEW="$arg" ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "bump: unknown option '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# ── preflight: clean + synced tree ────────────────────────────────
# The bump commit must contain ONLY the version change — refuse to
# run while work is staged, modified, untracked, or unpushed, since
# `git add` would otherwise sweep it silently into the release.
if [ "$GIT" -eq 1 ]; then
  dirty="$(git -C "$ROOT" status --porcelain)"
  if [ -n "$dirty" ]; then
    echo "bump: working tree is not clean — commit, stash, or ignore:" >&2
    echo "$dirty" >&2
    exit 1
  fi
  git -C "$ROOT" fetch -q origin
  if ! counts="$(git -C "$ROOT" rev-list --left-right --count HEAD...@{u} 2>/dev/null)"; then
    echo "bump: HEAD has no upstream — push it before bumping" >&2; exit 1
  fi
  ahead="${counts%%[[:space:]]*}"
  behind="${counts##*[[:space:]]}"
  if [ "$ahead" != "0" ] || [ "$behind" != "0" ]; then
    echo "bump: branch is ahead=$ahead behind=$behind vs upstream —" >&2
    echo "      push/pull to sync before bumping" >&2
    exit 1
  fi
fi

# ── compute new version ───────────────────────────────────────────
CUR="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$PYPROJECT" | head -1)"
[ -n "$CUR" ] || {
  echo "bump: can't read version from $PYPROJECT" >&2; exit 1; }

if [ "$KIND" != "explicit" ]; then
  IFS=. read -r major minor patch <<< "$CUR"
  case "$KIND" in
    patch) NEW="$major.$minor.$((patch + 1))" ;;
    minor) NEW="$major.$((minor + 1)).0" ;;
    major) NEW="$((major + 1)).0.0" ;;
  esac
fi

if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$NEW" >/dev/null 2>&1; then
  echo "bump: tag v$NEW already exists in $ROOT" >&2; exit 1
fi

# ── bump + lock ───────────────────────────────────────────────────
sed -i "0,/^version = \"$CUR\"$/s//version = \"$NEW\"/" "$PYPROJECT"
# Keep the lockfile's recorded project version in step — a stale
# [[package]] block trips 'uv sync --locked' and reads wrongly.
(cd "$ROOT" && uv lock --quiet)

grep -q "^version = \"$NEW\"$" "$PYPROJECT" || {
  echo "bump: pyproject did not pick up $NEW" >&2; exit 1; }

# ── commit + tag ──────────────────────────────────────────────────
if [ "$GIT" -eq 1 ]; then
  git -C "$ROOT" add pyproject.toml uv.lock
  git -C "$ROOT" commit -qm "gnosislab $NEW"
  git -C "$ROOT" push -q origin HEAD
  git -C "$ROOT" tag "v$NEW"
  git -C "$ROOT" push -q origin "v$NEW"
  echo "bump: committed + tagged v$NEW and pushed to origin"
fi

cat <<EOF
bump: $CUR -> $NEW
next: tag push triggers the publish workflow; or run
      $ROOT/scripts/publish-gnosislab.sh --ref v$NEW manually
EOF

#!/usr/bin/env bash
# publish-gnosislab.sh — clone cloudcell/gnosislab and publish to PyPI.
#
#   scripts/publish-gnosislab.sh              # clone, build, publish to PyPI
#   scripts/publish-gnosislab.sh --test       # same, but to TestPyPI
#   scripts/publish-gnosislab.sh --ref v0.1.0 # publish a tag/branch/commit
#
# Credentials:
#   ~/api-token-pypi.txt         PyPI API token (pypi-...)
#   ~/api-token-testpypi.txt     TestPyPI token — only needed for --test
#   (override with $PYPI_TOKEN_FILE / $TESTPYPI_TOKEN_FILE)
#
# The token is exported as UV_PUBLISH_TOKEN — it never appears in the
# command line, the process table, or any output.
set -euo pipefail

REPO="https://github.com/cloudcell/gnosislab.git"

TEST=0
REF=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test)    TEST=1; shift ;;
    --ref)     REF="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "publish: unknown option '$1' (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$TEST" -eq 1 ]; then
  TOKEN_FILE="${TESTPYPI_TOKEN_FILE:-$HOME/api-token-testpypi.txt}"
  PUBLISH_URL="https://test.pypi.org/legacy/"
  INDEX="https://test.pypi.org/simple"
else
  TOKEN_FILE="${PYPI_TOKEN_FILE:-$HOME/api-token-pypi.txt}"
  PUBLISH_URL=""
  INDEX="https://pypi.org/simple"
fi

# ── token ─────────────────────────────────────────────────────────
[ -f "$TOKEN_FILE" ] || {
  echo "publish: token file not found: $TOKEN_FILE" >&2; exit 1; }
# Accept a bare token or a file containing "pypi-..." anywhere.
TOKEN="$(grep -oE 'pypi-[A-Za-z0-9_-]+' "$TOKEN_FILE" | head -1 || true)"
[ -n "$TOKEN" ] || TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
[ -n "$TOKEN" ] || { echo "publish: $TOKEN_FILE is empty" >&2; exit 1; }
export UV_PUBLISH_TOKEN="$TOKEN"
unset TOKEN

perm="$(stat -c %a "$TOKEN_FILE")"
if [ "$perm" -gt 600 ]; then
  echo "publish: note — $TOKEN_FILE is mode $perm; 'chmod 600' recommended" >&2
fi
unset TOKEN_FILE

# ── clone ─────────────────────────────────────────────────────────
WORK="$(mktemp -d /tmp/gnosislab-publish.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

git clone --quiet --depth 1 ${REF:+--branch "$REF"} "$REPO" "$WORK/gnosislab"
echo "publish: cloned $REPO${REF:+ @ $REF}"

# ── build ─────────────────────────────────────────────────────────
(cd "$WORK/gnosislab" && uv build)
ls "$WORK"/gnosislab/dist/gnosislab-*.whl \
   "$WORK"/gnosislab/dist/gnosislab-*.tar.gz >/dev/null

BUILT="$(basename "$WORK"/gnosislab/dist/gnosislab-*.whl | head -1)"
echo "publish: built $BUILT"

# Guard: if --ref is a vX.Y.Z tag, the built version must match —
# otherwise we'd try re-uploading a stale version, which PyPI
# rejects with a hash mismatch.
if [ -n "$REF" ]; then
  ref_ver="${REF#v}"
  case "$BUILT" in
    gnosislab-"$ref_ver"-*) ;;
    *) echo "publish: ref $REF built $BUILT — version mismatch;" >&2
       echo "  bump version, commit, retag, then retry" >&2; exit 2 ;;
  esac
fi

# ── upload ────────────────────────────────────────────────────────
cd "$WORK/gnosislab"
if [ -n "$PUBLISH_URL" ]; then
  uv publish --publish-url "$PUBLISH_URL" --check-url "$INDEX"
else
  uv publish --check-url "$INDEX"
fi

if [ "$TEST" -eq 1 ]; then
  cat <<'EOF'
publish: done (TestPyPI). Verify:
  pip install --index-url https://test.pypi.org/simple \
              --extra-index-url https://pypi.org/simple gnosislab
  gnosislab --help
EOF
else
  cat <<'EOF'
publish: done (PyPI). Verify:
  pip install gnosislab
  gnosislab --help
EOF
fi

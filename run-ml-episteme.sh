#!/usr/bin/env bash
# Start the ml-episteme MCP server with HTTP transport and the observability GUI.
# Ports come from ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
set -a
source ./ports.env
# Token for the artifact-ingest surface — a per-deployment credential
# stored in ingest.env (gitignored), never in ports.env. The file is
# minted on first start with a fresh random token, mode 600; each
# deployment owns its own credential so rotating one never affects
# another. An empty/absent token leaves the surface off — the server
# refuses to run it unauthenticated.
if [ ! -f ./ingest.env ]; then
    ( umask 077; \
      echo "ML_EPISTEME_INGEST_TOKEN=$(openssl rand -hex 24)" \
      > ./ingest.env )
    echo "episteme: minted new ingest token in ./ingest.env" >&2
fi
chmod 600 ./ingest.env
source ./ingest.env
set +a

# Execution-integrity prerequisites. The default sandbox mode is
# "full" (read-only root, sealed-code overlays) which requires
# bubblewrap; if it is missing every trial run FAILS rather than
# degrading. strace powers executed-code capture (trace_reads=auto).
missing=()
command -v bwrap  >/dev/null 2>&1 || missing+=("bubblewrap")
command -v strace >/dev/null 2>&1 || missing+=("strace")
if [ ${#missing[@]} -gt 0 ]; then
    echo "WARNING: missing tools: ${missing[*]}" >&2
    echo "  Install them (e.g. 'apt install ${missing[*]}') or trials will" >&2
    echo "  run with degraded isolation/capture — or fail outright under" >&2
    echo "  executor.sandbox=auto|full|minimal." >&2
fi

args=(
    --transport http
    --port "${ML_EPISTEME_PORT}"
    --stateless
    --observability-port "${ML_EPISTEME_GUI_PORT}"
)
# Ingest is enabled iff a token is configured — the token is the
# switch, not the port. ports.env always carries the port; without
# a token (no ingest.env) the surface is simply absent. The server
# also reads ML_EPISTEME_INGEST_PORT straight from the environment
# (fail closed on a missing token), so the port must be unexported
# when there's no token — exporting it would kill startup instead.
if [ -n "${ML_EPISTEME_INGEST_PORT:-}" ] && \
   [ -n "${ML_EPISTEME_INGEST_TOKEN:-}" ]; then
    args+=(--ingest-port "${ML_EPISTEME_INGEST_PORT}")
else
    unset ML_EPISTEME_INGEST_PORT
fi

exec uv run ml-episteme-mcp "${args[@]}"

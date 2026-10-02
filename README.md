# GnosisLab

Umbrella project — a composed research-automation distribution.
Subsystems live in subfolders:

- `ml-scientist/` — the scientific-experiment MCP stack
  (hypothesis → experiment → evidence → conclusion): five MCP
  servers, sealed execution, invariant suite, observability GUIs.
- `openshell/` — (planned) NVIDIA OpenShell launch-within-shell
  subsystem.

## Host requirements

- **Linux** — trial sandboxing uses bubblewrap mount namespaces.
- **Python ≥ 3.10** and [`uv`](https://docs.astral.sh/uv/)
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`), or any pip for
  the PyPI path below.
- **bubblewrap + strace** — required by the sealed executor; the
  launchers refuse to start without them:

  ```bash
  sudo apt install bubblewrap strace    # Debian/Ubuntu
  ```

- Ports **38050–38091** free (five servers × MCP + GUI; see
  `ml-scientist/ports.env` to move them).

## Install

### From source (this repository)

```bash
git clone <this-repo> gnosislab && cd gnosislab
uv sync          # one root env for the whole distribution
```

`ml-scientist/` is also independently runnable — `cd ml-scientist &&
uv sync` gives it its own env.

### From PyPI

```bash
pip install gnosislab        # or, better for CLI use:
uv tool install gnosislab    # isolated env, commands on PATH
```

This installs the five MCP servers as console commands
(`ml-episteme-mcp`, `ml-anamnesis-mcp`, `ml-zetesis-mcp`,
`ml-arete-mcp`, `ml-agora-mcp`). The `gnosislab` lifecycle script is
repo-only for now (bash, not shipped in the wheel) — a `gnosislab`
CLI entry point is planned.

## Run

`gnosislab` is a symlink at the repo root into `ml-scientist/`:

```bash
./gnosislab start all     # upstreams first, agora last
./gnosislab status        # pid/ports/health per server
./gnosislab logs arete
./gnosislab stop all
```

(`./labloop` still works — transition symlink, prints a rename
notice.)

To launch from anywhere, put it on PATH:

```bash
ln -sf "$PWD/gnosislab" ~/.local/bin/gnosislab
gnosislab start all
```

Per-server observability GUIs: anamnesis :38091, episteme :38081,
zetesis :38071, arete :38061, agora :38051 (lab-level status hub).
Each server also responds to `ml-<name>-mcp` run directly in the
foreground via `ml-scientist/run-ml-<name>.sh` for debugging.

Config: `ml-scientist/ml-*.toml` (per server) + `ports.env`.
Runtime state lives outside the repo in `~/.ml-<name>/`
(databases, logs, pidfiles).

## Test

```bash
uv run pytest -q     # full suite (1289 tests)
```

## Publish

```bash
uv build
uv publish --publish-url https://test.pypi.org/legacy/   # TestPyPI
uv publish                                               # PyPI
```

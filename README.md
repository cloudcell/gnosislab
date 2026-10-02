# GnosisLab

[![PyPI](https://img.shields.io/pypi/v/gnosislab?cacheSeconds=3600)](https://pypi.org/project/gnosislab/)
[![Python](https://img.shields.io/pypi/pyversions/gnosislab)](https://pypi.org/project/gnosislab/)
[![License](https://img.shields.io/pypi/l/gnosislab)](https://github.com/cloudcell/gnosislab/blob/master/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/cloudcell/gnosislab)](https://github.com/cloudcell/gnosislab)

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

This installs the `gnosislab` lifecycle command plus the five MCP
servers (`ml-episteme-mcp`, `ml-anamnesis-mcp`, `ml-zetesis-mcp`,
`ml-arete-mcp`, `ml-agora-mcp`):

```bash
gnosislab start all
gnosislab status
gnosislab config          # resolved ports + config file location
```

Ports: built-in defaults (38050–38091) are overridden by
`~/.config/gnosislab/ports.env` (same KEY=VALUE format as the repo's
`ports.env`) and by `ML_*` environment variables. Runtime state lives
in `~/.ml-<name>/`.

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

## Stats

- PyPI: https://pypi.org/project/gnosislab/
- Downloads: https://pepy.tech/project/gnosislab (per-day/week/month +
  total; also [pypistats.org/packages/gnosislab](https://pypistats.org/packages/gnosislab)
  for version/platform breakdowns — both populate after first downloads)
- Repo: https://github.com/cloudcell/gnosislab

## Publish

```bash
uv build
uv publish --publish-url https://test.pypi.org/legacy/   # TestPyPI
uv publish                                               # PyPI
```

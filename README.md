<!-- markdownlint-disable MD033 MD041 MD001 -->
<div align="center">

# GnosisLab

### A scientific-method runtime for AI agents

**Turn hypotheses into experiments, experiments into evidence, and evidence into auditable conclusions.**

[![DOI](https://zenodo.org/badge/1401154499.svg)](https://doi.org/10.5281/zenodo.23128199)
[![SSRN](https://img.shields.io/badge/SSRN-preprint-blue)](https://doi.org/10.2139/ssrn.7573878)
[![CI](https://github.com/cloudcell/gnosislab/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudcell/gnosislab/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gnosislab?label=PyPI&cacheSeconds=3600)](https://pypi.org/project/gnosislab/)
[![Python](https://img.shields.io/pypi/pyversions/gnosislab?cacheSeconds=3600)](https://pypi.org/project/gnosislab/)
[![License](https://img.shields.io/pypi/l/gnosislab?cacheSeconds=3600)](https://github.com/cloudcell/gnosislab/blob/master/LICENSE)
[![piwheel downloads](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcloudcell%2Fgnosislab%2Fmaster%2Fstats%2Fpiwheels.json&query=%24.downloads&label=piwheels%20downloads&color=blue)](https://www.piwheels.org/project/gnosislab/)
[![PyPI downloads](https://pypistats.com/api/badges/gnosislab?period=total)](https://pypistats.com/packages/gnosislab)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/gnosislab?period=total&units=NONE&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/gnosislab)
[![GnosisLab-VM](https://img.shields.io/badge/GnosisLab--VM-isolated%20VM%20edition-6f42c1?logo=linux&logoColor=white)](https://github.com/cloudcell/gnosislab-vm)
[![Discord](https://img.shields.io/discord/903850592957517835?logo=discord&label=Discord)](https://discord.com/invite/v9JVtpVuUT)

**VM edition available:** [GnosisLab-VM](https://github.com/cloudcell/gnosislab-vm) provides an optional isolated KVM/libvirt environment for GnosisLab research workflows.

[Install](#quick-start) · [Documentation](https://loop.cloudcell.workers.dev/docs) · [How it works](#how-it-works) · [Architecture](#architecture) · [For researchers](#for-researchers) · [For developers](#for-developers) · [Paper](https://doi.org/10.2139/ssrn.7573878) · [Discord](https://discord.gg/v9JVtpVuUT)


</div>

---

## What is GnosisLab?

Most AI agents are optimized to **produce an answer**.

GnosisLab is built to help an agent **produce a defensible result**.

It turns the scientific process into explicit software objects:

> **hypothesis → experiment → observation → evidence → claim → conclusion**

Instead of leaving an agent's reasoning, experiments, and evidence scattered across prompts, shell history, notebooks, logs, and temporary files, GnosisLab gives the process a structured runtime.

Experiments are represented explicitly. Evidence is retained. Claims can be connected to their support. Execution is observable. State is separated by responsibility. The resulting research process can be inspected by a human rather than accepted as an opaque final answer.

GnosisLab is intended for machine-learning experimentation, computational research, autonomous research agents, evaluation systems, and other workflows where **how a result was obtained matters as much as the result itself**.

---

## Why this exists

LLMs can generate code, search literature, run analyses, compare alternatives, and propose conclusions.

That creates a new problem:

**How do we know what actually happened?**

A useful research system needs more than tool access. It needs a disciplined process around tool use:

- What hypothesis was being tested?
- Which experiment addressed it?
- What code actually ran?
- What observations were produced?
- Which evidence supports which claim?
- Which conclusions remain uncertain?
- What changed between one iteration and the next?
- Can a human inspect the path from question to result?

GnosisLab makes those questions part of the system itself.

---

## Quick start

### Install from PyPI

```bash
uv tool install gnosislab
```

or:

```bash
pip install gnosislab
```

### Full setup example

Register the five servers with your MCP client, then start the stack:

```bash
# see which clients are supported, preview without writing
gnosislab setup --list
gnosislab setup claude-code --dry-run    # or opencode, vscodium, vscode,
                                         # windsurf, cursor

# merge the server entries into the client's MCP config
gnosislab setup claude-code

# background all five servers (upstreams first)
gnosislab start all

# pid/ports/health per server
gnosislab status

# resolved ports + config file path
gnosislab config

# tail one server's log
gnosislab logs episteme

# open the lab status dashboard in your browser (agora by default)
gnosislab dashboard            # → ML_AGORA_GUI_URL (default http://localhost:28051)
gnosislab dashboard episteme   # any server's GUI works too

# stop everything (consumers first)
gnosislab stop all
```

Ports resolve as: built-in defaults < `~/.config/gnosislab/ports.env` <
environment variables — same `KEY=VALUE` format as the repo's
`ports.env`. Runtime state always lives in `~/.ml-<name>/`, regardless
of install method. The dashboard URL comes from the resolved
`ML_<NAME>_GUI_URL`, so remote/VM setups can point it at a reachable
host without editing scripts.

**Full documentation:** <https://loop.cloudcell.workers.dev/docs>

### Install from source

```bash
git clone https://github.com/cloudcell/gnosislab.git
cd gnosislab
uv sync
./gnosislab start all
```

> **Sealed trial execution needs Linux tooling.** The executor isolates
> trials with `bubblewrap` mount namespaces and traces reads with
> `strace`. Neither exists on Windows (or macOS) — there is no bwrap
> package to install. With the default `sandbox = "auto"`, trials on
> those platforms **fail closed** rather than silently running
> unsealed. To run trials without isolation, opt out explicitly in
> `~/.ml-episteme/config.toml`:
>
> ```toml
> [executor]
> sandbox = "none"
> ```
>
> (equivalently, `ML_EPISTEME_SANDBOX=none`). If you also configure
> `[executor] sealed_path_patterns`, add
> `sealed_enforcement = "audit"` — `deny` needs a mount namespace to
> arm. Coding agents often offer to "disable sandboxing" when they hit
> this error; the knob they should set is `sandbox`, shown above.
>
> On Debian/Ubuntu the full experience is:

```bash
sudo apt install bubblewrap strace
```

---

## The idea in one picture

```mermaid
flowchart LR
    Q["Research question"] --> H["Hypothesis"]
    H --> Z["Zetesis<br/>search & evidence discovery"]
    Z --> E["Episteme<br/>experiment & observation"]
    E --> X["Sealed execution"]
    X --> O["Artifacts & observations"]
    O --> A["Anamnesis<br/>claims, evidence & memory"]
    A --> C["Conclusion"]
    C --> R["Arete<br/>adaptation & improvement"]
    R --> H

    G["Agora<br/>coordination & lab-level view"] -.-> Z
    G -.-> E
    G -.-> A
    G -.-> R
```

The point is not the Greek names. The point is **separation of scientific responsibilities**.

Each subsystem owns a distinct part of the research process rather than collapsing everything into one agent loop and one unstructured memory.

---

## How it works

GnosisLab is composed of five MCP services.

| Service | Role | Think of it as |
| --- | --- | --- |
| **Episteme** | experiments, trials, observations and experimental state | the laboratory notebook |
| **Anamnesis** | claims, evidence relationships and persistent research memory | the evidence graph |
| **Zetesis** | search, investigation campaigns and evidence discovery | the investigator |
| **Arete** | improvement, comparison and meta-level adaptation | the critic / improver |
| **Agora** | coordination and lab-level observability | the control room |

The services deliberately keep their state boundaries separate. This makes ownership clearer, failures easier to isolate, schemas easier to evolve, and each subsystem independently inspectable.

---

## Architecture

```mermaid
flowchart TB
    U["Human / AI agent / MCP client"]

    subgraph GL["GnosisLab"]
        AG["Agora<br/>coordination"]
        ZE["Zetesis<br/>search"]
        EP["Episteme<br/>experiments"]
        AN["Anamnesis<br/>claims + evidence"]
        AR["Arete<br/>improvement"]

        AG --> ZE
        AG --> EP
        AG --> AN
        AG --> AR

        ZE --> EP
        EP --> AN
        AN --> AR
        AR --> ZE
    end

    subgraph EX["Execution boundary"]
        SE["Sealed experiment executor"]
        ART["Code, logs, outputs,<br/>observations, artifacts"]
        SE --> ART
    end

    U --> AG
    U --> ZE
    U --> EP
    U --> AN
    U --> AR

    EP --> SE
    ART --> EP
    ART --> AN
```

### Default service ports

| Service | GUI | MCP |
| --- | ---: | ---: |
| Agora | `28051` | `28050` |
| Arete | `28061` | `28060` |
| Zetesis | `28071` | `28070` |
| Episteme | `28081` | `28080` |
| Anamnesis | `28091` | `28090` |

Defaults can be overridden with:

```text
~/.config/gnosislab/ports.env
```

or the corresponding `ML_*` environment variables.

Runtime state lives outside the repository under:

```text
~/.ml-<name>/
```

---

## For researchers

GnosisLab is designed around a simple principle:

> **A scientific result should remain inspectable after the agent that produced it has moved on.**

That means treating research state as durable data rather than transient conversation context.

### Explicit experimental structure

Research can be represented in terms of programmes, trials, observations, evidence and claims rather than buried inside a chat transcript.

### Provenance

The system is designed to retain the relationship between experimental actions, artifacts, observations, evidence and downstream claims.

### Reproducibility

Experiment execution is separated from conversational reasoning. The aim is to preserve the material needed to understand and reproduce what was actually done.

### Human review

GnosisLab is not built around the assumption that an autonomous agent should be trusted by default. Its structure is intended to make intermediate state visible enough for review, correction and override.

### Uncertainty belongs in the record

A research system should distinguish a claim from the evidence supporting it and should avoid turning an agent's confidence into an unexplained magic number.

---

## For developers

GnosisLab is infrastructure, not a monolithic chatbot.

### MCP-native

The distribution exposes five MCP server entry points:

```text
ml-episteme-mcp
ml-anamnesis-mcp
ml-zetesis-mcp
ml-arete-mcp
ml-agora-mcp
```

This means an MCP-capable agent or client can work with the scientific process as a set of tools rather than requiring a proprietary frontend.

### One command for lifecycle management

```bash
gnosislab start all
gnosislab status
gnosislab logs arete
gnosislab stop all
```

### Resident experiment supervisor: `gnosislab daemon`

Each state root gets its own daemon — a persistent supervisor that drives existing CLI agents against an independent verifier, and exposes a local console plus the same service-lifecycle and export powers as this CLI:

```bash
# one daemon per experiment state root
gnosislab daemon serve --state-dir ~/exp-a/.daemon --port 38100
gnosislab daemon start --agent opencode --task task.md \
    --check ./verify.sh --state-dir ~/exp-a/.daemon
gnosislab daemon status --state-dir ~/exp-a/.daemon
gnosislab daemon open   --state-dir ~/exp-a/.daemon   # local console
```

The daemon never trusts an agent's claim of completion: after every invocation it runs the checker (`exit 0` = verified, `1` = continue, other = check error) and resumes the agent's session — or restarts it with saved context — until verification passes or a limit parks the loop as `BLOCKED`. Agent profiles are argv templates in `~/.config/gnosislab/daemon.toml`; adding an agent is config, not code. See `examples/daemon.toml` and `gnosislab daemon --help`.

Two agent backends fill the same verified-loop contract (`src/gnosislab_daemon/backends/`):

- **`exec`** (default): one CLI subprocess per turn; process exit is the turn boundary. Strongest isolation.
- **`session`**: a persistent *driver* process per loop, controlled over a stdio JSONL protocol (`send`/`status`/`cancel`/`fork`/`shutdown` ops; `ready`/`turn_end`/`session` events). The driver owns all vendor details — any agentic IDE or a bare LLM endpoint works by writing one small adaptor. `gnosislab_daemon/drivers/` ships the protocol conformance fixture (`fake_session_driver.py`) plus reference drivers for opencode and an OpenAI-compatible chat endpoint.

### Isolated workspaces

`gnosislab init <dir>` scaffolds a self-contained experiment sandbox in one command: its own HOME (service databases, opencode sessions), its own service port sub-block and daemon console (registered in `~/.config/gnosislab/workspaces.json` so workspaces can't collide), its own driver copies, and a runnable task + verdict checker. `source <dir>/env` puts a shell inside it — `gnosislab start all`, `daemon serve`, and the `opencode` TUI all resolve the sandbox stack.

The turn boundary is *never* a verdict: `turn_end` (or process exit) only ends the turn — the independent checker still decides. Structured checker diagnostics are supported via a `GNOSISLAB_VERDICT` JSON line (bounded, sanitized; contradictions fail closed as `CHECK_ERROR`), stagnation can trigger a single capability-gated session fork with recorded lineage, and a driver that dies mid-turn parks the loop `BLOCKED` with `uncertain_turn` rather than risking a silent duplicate dispatch.

### Sealed experiment execution

Trial execution uses Linux isolation primitives so experiments can run inside a constrained boundary rather than directly in the agent's host environment.

### Observable by design

Every service exposes a local observability GUI. You can inspect the system while it is operating instead of treating agent activity as an invisible background process.

### Separate state ownership

The major research loops keep their own persistent stores. This avoids turning one shared database into an ambiguous write boundary and lets each service evolve independently.

### Small dependency surface

The published package targets Python 3.10+ and keeps the core runtime dependency set focused around MCP, Pydantic and HTTPX.

---

## A research loop, expressed as software

A conventional agent loop often looks roughly like this:

```text
prompt → tool calls → more prompting → answer
```

GnosisLab makes the scientific structure explicit:

```text
question
  ↓
hypothesis
  ↓
search / prior evidence
  ↓
experiment design
  ↓
sealed execution
  ↓
observations + artifacts
  ↓
evidence-linked claims
  ↓
conclusion
  ↓
critique / adaptation
  ↓
next hypothesis
```

That difference matters when the work is long-running, iterative, expensive, safety-sensitive, or intended to survive peer review.

---

## What GnosisLab is not

GnosisLab is **not**:

- a replacement for a capable language model;
- a claim that autonomous agents can replace scientific judgment;
- a generic vector-memory wrapper;
- a single prompt that tells an LLM to "act like a scientist";
- an opaque autonomous system that asks you to trust the final answer.

It is an attempt to make the **process around AI-assisted research explicit, inspectable and programmable**.

---

## How GnosisLab compares
Against adjacent systems, including autonomous-research agents, agent frameworks, reproducibility tools, and experiment trackers, GnosisLab is distinguished not by any single capability but by their integration: scientific objects are represented as first-class state, methodological constraints are enforced at runtime, execution provenance is bound to the research record, and the stored record is reconciled against what actually executed.

<img src="docs-pub/alternatives-overview.png" alt="Comparison of verification and scientific-method capabilities: GnosisLab vs autoresearch, AIPOCH, EvoScientist, Paper2Agent, Wisp, LangChain, AutoGPT, and DVC/MLflow" width="100%">

**Legend:** ✓ = native or core capability; ◐ = partial, indirect, configurable, or implementation-dependent; — = not a core capability or not evidenced in the compared system.

The comparison is intended to distinguish architectural emphasis rather than overall system capability. Systems such as autoresearch, EvoScientist, and AutoGPT are primarily designed to increase autonomous task or experiment execution, whereas GnosisLab places greater emphasis on representing and verifying the scientific process itself.

---

## Example use cases

### Machine-learning research

An agent can propose an architecture change, execute a controlled trial, capture metrics and artifacts, attach evidence to the resulting claim, and use the result to choose the next experiment.

### Reproducible benchmarking

Benchmark runs can be treated as experiments with explicit observations rather than as disconnected terminal commands and copied numbers.

### Literature-to-experiment workflows

Search and evidence discovery can feed hypotheses and experimental design while keeping source evidence distinct from experimentally generated evidence.

### Long-running autonomous investigation

The system can preserve research state across many agent turns without relying on a single ever-growing context window.

### Human-supervised research agents

A researcher can inspect intermediate claims, evidence, trial outcomes and service state rather than reviewing only a final generated report.

---

## Project layout

```text
gnosislab/
├── src/                # gnosislab CLI + five ml_*_mcp packages
├── tests/              # test suite (pytest-xdist)
├── constants/          # grounded-constants registry + disclosure
├── diagnostics/        # diagnostics corpus
├── gnosislab           # lifecycle CLI entry point
├── labloop             # transition compatibility symlink
├── ml-*.toml           # per-server config
├── ports.env           # port assignments
├── pyproject.toml      # distribution metadata
├── LICENSE
├── NOTICE
└── README.md
```

The repository root is the runnable project:

```bash
uv sync
```

---

## Configuration

Per-service configuration lives under:

```text
ml-*.toml
```

Port configuration:

```text
ports.env
```

User overrides:

```text
~/.config/gnosislab/ports.env
```

Inspect the resolved configuration at any time:

```bash
gnosislab config
```

---

## Observability

After startup, each service exposes a local GUI.

For example:

```text
Agora      http://localhost:28051
Arete      http://localhost:28061
Zetesis    http://localhost:28071
Episteme   http://localhost:28081
Anamnesis  http://localhost:28091
```

Use:

```bash
gnosislab status
```

for process, port and health information.

Use:

```bash
gnosislab logs <service>
```

to inspect a service directly.

---

## Testing

Run the full suite:

```bash
uv run pytest -q
```

The repository currently contains **1,289 tests**.

For a serial run:

```bash
uv run pytest -n0
```

---

## Development status

GnosisLab is **alpha software**.

The architecture is usable and the package is published, but interfaces, schemas and internal boundaries may continue to evolve.

If you are evaluating it for serious research, inspect the code, test the failure modes, and treat the current release as research infrastructure under active development.

---

## Install and explore

```bash
uv tool install gnosislab
gnosislab start all
gnosislab status
```

Documentation: <https://loop.cloudcell.workers.dev/docs>

PyPI: <https://pypi.org/project/gnosislab/>

Repository: <https://github.com/cloudcell/gnosislab>

---

## Related projects

**[LabLoop](https://github.com/cloudcell/labloop)** — a secure lab
environment for running GnosisLab against untrusted, agent-generated code.
It builds a VM partitioned into `driver`, `mcp` and `hostile` zones: the
services and their stores live in the trusted `mcp` zone, the driving agent
in `driver`, and experiment code executes only in `hostile`, returning
results as inert data through a token-gated ingest surface. It is the
containment model described in the GnosisLab architecture, packaged as a
runnable environment.

---

## Contributing

GnosisLab is most useful when challenged by people who care about scientific rigor, agent engineering, reproducibility, provenance, evaluation and research infrastructure.

Useful contributions include:

- adversarial testing of the scientific state machine;
- new experiment and evidence workflows;
- integrations with MCP clients and research agents;
- reproducibility and provenance improvements;
- better observability;
- failure-mode documentation;
- benchmark suites;
- documentation and examples.

Open an issue with a concrete failure case, proposed experiment, or implementation idea.

---

## Philosophy

AI makes it dramatically cheaper to generate hypotheses, code, analyses and prose.

It does **not** automatically make those outputs scientific.

The difficult part is preserving the chain between:

**what was proposed → what was done → what was observed → what counts as evidence → what may reasonably be concluded**

GnosisLab exists to make that chain a first-class object.

---

## Paper

The GnosisLab design and the evidence-policy machinery are described in:

> Bikeyev, A. (2026). *GnosisLab: A Verifiable Foundation for Autonomous
> Scientific Discovery*. SSRN.
> <https://doi.org/10.2139/ssrn.7573878>

```bibtex
@misc{bikeyev2026gnosislab,
  author = {Bikeyev, Alexander},
  title  = {GnosisLab: A Verifiable Foundation for Autonomous Scientific Discovery},
  year   = {2026},
  howpublished = {SSRN},
  doi    = {10.2139/ssrn.7573878},
}
```

---

## License

Apache License 2.0. See [`LICENSE`](LICENSE).

---

<div align="center">

### Stop collecting runs. Start running programmes.

```bash
pip install gnosislab
```

**[GnosisLab](https://github.com/cloudcell/gnosislab)** — hypothesis in, auditable conclusion out.

</div>

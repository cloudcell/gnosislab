# Contributing to GnosisLab

Contributions are welcome, especially those that improve scientific rigor,
reproducibility, provenance, observability, isolation, and agent integration.

## Development setup

```bash
git clone https://github.com/cloudcell/gnosislab.git
cd gnosislab
uv sync
uv run pytest -q
```

## Architectural principle

GnosisLab deliberately separates scientific responsibilities.

- **Episteme** — experiments, trials and observations
- **Anamnesis** — claims, evidence and research memory
- **Zetesis** — investigation and evidence discovery
- **Arete** — adaptation and improvement
- **Agora** — coordination and observability

Changes should preserve clear state ownership and write boundaries.

## Pull requests

A pull request should:

1. explain the problem being solved;
2. identify the affected subsystem;
3. describe any state-machine or schema changes;
4. include tests for changed behaviour;
5. describe provenance or reproducibility implications where relevant;
6. keep unrelated changes out of the same pull request.

## Useful contributions

Particularly useful contributions include:

- adversarial tests of scientific state transitions;
- reproducibility improvements;
- provenance validation;
- MCP client integrations;
- observability improvements;
- experiment-isolation improvements;
- concrete research workflows;
- documentation;
- end-to-end examples;
- documented failure cases.

## Before submitting

```bash
uv run pytest -q
```

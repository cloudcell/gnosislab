#!/usr/bin/env bash
set -euo pipefail

REPO="cloudcell/gnosislab"
DOCS_URL="https://loop.cloudcell.workers.dev/docs"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

write_if_missing() {
  local path="$1"
  if [[ -e "$path" ]]; then
    echo "SKIP: $path already exists"
    cat >/dev/null
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  cat > "$path"
  echo "CREATE: $path"
}

echo "==> Verifying repository root"
git rev-parse --show-toplevel >/dev/null 2>&1 || die "Run this script from inside the gnosislab Git repository."
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

[[ -f README.md ]] || die "README.md not found at repository root."

command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) is required."
command -v python3 >/dev/null 2>&1 || die "python3 is required."

gh auth status >/dev/null 2>&1 || die "GitHub CLI is not authenticated. Run: gh auth login"

echo
echo "==> Configuring GitHub About metadata and repository settings"

gh repo edit "$REPO" \
  --description "A scientific-method runtime for AI agents: hypotheses, experiments, evidence, provenance, reproducibility, and auditable conclusions." \
  --homepage "$DOCS_URL" \
  --add-topic ai-agents \
  --add-topic ai-scientist \
  --add-topic scientific-method \
  --add-topic research-automation \
  --add-topic research-tools \
  --add-topic mcp \
  --add-topic model-context-protocol \
  --add-topic machine-learning \
  --add-topic experimentation \
  --add-topic hypothesis-testing \
  --add-topic reproducibility \
  --add-topic provenance \
  --add-topic autonomous-agents \
  --add-topic llm \
  --add-topic python \
  --enable-issues \
  --enable-discussions \
  --allow-update-branch \
  --delete-branch-on-merge

echo
echo "==> Creating CI workflow"

write_if_missing ".github/workflows/ci.yml" <<'EOF'
name: CI

on:
  push:
    branches: [master, main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    name: Python ${{ matrix.python-version }}
    runs-on: ubuntu-latest

    strategy:
      fail-fast: false
      matrix:
        python-version:
          - "3.10"
          - "3.11"
          - "3.12"

    steps:
      - name: Check out repository
        uses: actions/checkout@v5

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y bubblewrap strace

      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true

      - name: Install Python
        run: uv python install ${{ matrix.python-version }}

      - name: Install project
        run: uv sync --all-extras --dev --python ${{ matrix.python-version }}

      - name: Run tests
        run: uv run --python ${{ matrix.python-version }} pytest -q

      - name: Build distribution
        if: matrix.python-version == '3.12'
        run: uv build
EOF

echo
echo "==> Creating project trust/community files"

write_if_missing "SECURITY.md" <<'EOF'
# Security Policy

## Reporting a vulnerability

Please do not disclose security vulnerabilities in a public GitHub issue.

Use GitHub private vulnerability reporting when available, or contact the
project maintainers privately.

Please include:

- affected GnosisLab version or commit;
- operating system and environment;
- reproduction instructions;
- expected and observed behaviour;
- potential security impact;
- relevant logs or artifacts, after removing sensitive information.

## Scope

Security reports are particularly welcome for:

- experiment isolation;
- filesystem containment;
- command execution;
- MCP service boundaries;
- network exposure;
- artifact ingestion;
- provenance integrity;
- privilege escalation;
- unintended information disclosure.

GnosisLab is alpha software. Its isolation mechanisms should not be treated as
a substitute for appropriate host-level security controls.
EOF

write_if_missing "CONTRIBUTING.md" <<'EOF'
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
EOF

write_if_missing "CITATION.cff" <<'EOF'
cff-version: 1.2.0
message: "If you use GnosisLab in research, please cite the software."
title: "GnosisLab"
type: software
authors:
  - name: "Cloudcell Limited"
repository-code: "https://github.com/cloudcell/gnosislab"
url: "https://loop.cloudcell.workers.dev/docs"
license: Apache-2.0
keywords:
  - artificial intelligence
  - scientific method
  - research automation
  - AI agents
  - reproducibility
  - provenance
  - Model Context Protocol
EOF

write_if_missing "CHANGELOG.md" <<'EOF'
# Changelog

All notable changes to GnosisLab will be documented here.

## Unreleased

### Added

### Changed

### Fixed
EOF

write_if_missing "ROADMAP.md" <<'EOF'
# GnosisLab Roadmap

GnosisLab is an alpha scientific-method runtime for AI agents.

## Current foundations

- explicit hypotheses, experiments, observations and claims;
- evidence and provenance relationships;
- five cooperating MCP services;
- sealed experiment execution;
- persistent research state;
- local observability;
- lifecycle CLI;
- automated test suite.

## Near-term priorities

- richer end-to-end research examples;
- stronger provenance validation;
- improved execution isolation;
- improved confidence computation and validation;
- additional MCP client integrations;
- easier deployment;
- clearer research reporting and export;
- expanded documentation.

## Longer-term directions

- reproducible research bundles;
- richer human review and intervention;
- external artifact stores;
- distributed execution;
- standardized research-process disclosures;
- evaluation of autonomous research workflows.

This roadmap describes direction rather than a commitment to specific dates.
EOF

echo
echo "==> Creating GitHub issue and PR templates"

write_if_missing ".github/ISSUE_TEMPLATE/bug_report.yml" <<'EOF'
name: Bug report
description: Report reproducible incorrect behaviour
title: "[Bug]: "
labels: ["bug"]
body:
  - type: textarea
    id: summary
    attributes:
      label: What happened?
      description: Describe the problem clearly.
    validations:
      required: true

  - type: textarea
    id: reproduce
    attributes:
      label: Reproduction
      description: Provide the smallest reproducible sequence.
      placeholder: |
        1. Run ...
        2. Call ...
        3. Observe ...
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Expected behaviour
    validations:
      required: true

  - type: dropdown
    id: subsystem
    attributes:
      label: Subsystem
      options:
        - Episteme
        - Anamnesis
        - Zetesis
        - Arete
        - Agora
        - Execution / isolation
        - CLI / packaging
        - Documentation
        - Unknown
    validations:
      required: true

  - type: input
    id: version
    attributes:
      label: GnosisLab version
      placeholder: "0.x.x or commit SHA"

  - type: textarea
    id: environment
    attributes:
      label: Environment
      placeholder: |
        OS:
        Python:
        installation method:

  - type: textarea
    id: logs
    attributes:
      label: Relevant logs
      description: Remove secrets or private information before posting.
EOF

write_if_missing ".github/ISSUE_TEMPLATE/feature_request.yml" <<'EOF'
name: Feature or design proposal
description: Propose a new capability or architectural improvement
title: "[Proposal]: "
labels: ["enhancement"]
body:
  - type: textarea
    id: problem
    attributes:
      label: Problem
      description: What concrete problem should this solve?
    validations:
      required: true

  - type: textarea
    id: proposal
    attributes:
      label: Proposed solution
    validations:
      required: true

  - type: textarea
    id: scientific
    attributes:
      label: Scientific workflow
      description: How does this affect experiments, evidence, provenance, reproducibility or review?

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
EOF

write_if_missing ".github/ISSUE_TEMPLATE/research_workflow.yml" <<'EOF'
name: Research workflow
description: Describe a real research workflow GnosisLab should support
title: "[Workflow]: "
labels: ["research-workflow"]
body:
  - type: input
    id: domain
    attributes:
      label: Research domain
      placeholder: "e.g. machine learning, cheminformatics, computational biology"

  - type: textarea
    id: question
    attributes:
      label: Research question
    validations:
      required: true

  - type: textarea
    id: current
    attributes:
      label: Current workflow
      description: How is this research performed today?
    validations:
      required: true

  - type: textarea
    id: evidence
    attributes:
      label: Required evidence and artifacts
      description: What must be retained for the result to be defensible?

  - type: textarea
    id: desired
    attributes:
      label: Desired GnosisLab workflow
EOF

write_if_missing ".github/pull_request_template.md" <<'EOF'
## What changed?

## Why?

## Affected subsystem

- [ ] Episteme
- [ ] Anamnesis
- [ ] Zetesis
- [ ] Arete
- [ ] Agora
- [ ] Execution / isolation
- [ ] CLI / packaging
- [ ] Documentation

## Scientific / state-machine implications

Describe any changed invariants, state transitions, evidence relationships,
confidence rules, provenance behaviour, or reproducibility implications.

## Tests

Describe tests added or changed.

## Checklist

- [ ] Full test suite passes
- [ ] New behaviour is tested
- [ ] Documentation is updated where necessary
- [ ] No unrelated changes are included
EOF

echo
echo "==> Creating Dependabot configuration"

write_if_missing ".github/dependabot.yml" <<'EOF'
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
EOF

echo
echo "==> Creating example structure"

write_if_missing "examples/README.md" <<'EOF'
# GnosisLab Examples

These examples demonstrate complete research workflows rather than isolated
API calls.

## Minimal experiment

`minimal-experiment/` illustrates the intended lifecycle:

```text
research question
    ↓
hypothesis
    ↓
experiment
    ↓
observation
    ↓
evidence
    ↓
claim
    ↓
conclusion
```

Examples should remain small enough to reproduce locally and preserve the
complete provenance chain from question to conclusion.
EOF

write_if_missing "examples/minimal-experiment/README.md" <<'EOF'
# Minimal Experiment

A deliberately small example of the GnosisLab research lifecycle.

## Question

Does configuration A produce a measurably different result from configuration B?

## Intended lifecycle

1. Declare the hypothesis.
2. Define a controlled experiment.
3. Execute the trial.
4. Capture artifacts and observations.
5. Associate evidence with the resulting claim.
6. Record the conclusion and its uncertainty.
7. Preserve enough state to inspect or reproduce the experiment.

This directory is intentionally a starting point for a fully executable
end-to-end example.
EOF

echo
echo "==> Updating README where needed"

python3 <<'PY'
from pathlib import Path

p = Path("README.md")
s = p.read_text(encoding="utf-8")

docs = "https://loop.cloudcell.workers.dev/docs"

if "[Documentation](" not in s:
    needle = "[Install](#quick-start)"
    if needle in s:
        s = s.replace(
            needle,
            f"{needle} · [Documentation]({docs})",
            1
        )

ci_badge = (
    "[![CI](https://github.com/cloudcell/gnosislab/actions/workflows/ci.yml/badge.svg)]"
    "(https://github.com/cloudcell/gnosislab/actions/workflows/ci.yml)"
)

if "actions/workflows/ci.yml/badge.svg" not in s:
    marker = "[![PyPI]"
    if marker in s:
        s = s.replace(marker, ci_badge + "\n" + marker, 1)

docs_line = f"Documentation: <{docs}>"
if "## Install and explore" in s and docs_line not in s:
    pos = s.find("## Install and explore")
    tail = s[pos:]
    marker = "PyPI:"
    rel = tail.find(marker)
    if rel != -1:
        absolute = pos + rel
        s = s[:absolute] + docs_line + "\n\n" + s[absolute:]

p.write_text(s, encoding="utf-8")
print("UPDATE: README.md")
PY

echo
echo "==> Creating GitHub labels"

gh label create "research-workflow" \
  --repo "$REPO" \
  --description "A concrete research workflow or use case" \
  --color "5319E7" \
  --force

gh label create "provenance" \
  --repo "$REPO" \
  --description "Provenance, evidence or reproducibility" \
  --color "0E8A16" \
  --force

gh label create "state-machine" \
  --repo "$REPO" \
  --description "Scientific state-machine behaviour or invariants" \
  --color "1D76DB" \
  --force

gh label create "mcp" \
  --repo "$REPO" \
  --description "Model Context Protocol integration" \
  --color "0052CC" \
  --force

gh label create "security" \
  --repo "$REPO" \
  --description "Isolation or security work" \
  --color "D73A4A" \
  --force

echo
echo "============================================================"
echo "GnosisLab repository setup complete."
echo "============================================================"
echo
echo "Review:"
echo "  git status"
echo "  git diff"
echo
echo "If everything looks correct:"
echo
echo "  git add README.md SECURITY.md CONTRIBUTING.md CITATION.cff CHANGELOG.md ROADMAP.md .github examples"
echo '  git commit -m "chore: professionalize project repository"'
echo "  git push"
echo
echo "After pushing, watch CI with:"
echo
echo "  gh run watch"
echo
echo "This script intentionally does NOT create or publish a GitHub Release."

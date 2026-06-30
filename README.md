# Intelligent Data Quality Agent

This proof of concept validates Databricks-to-BigQuery migrations and BigQuery-only datasets. It combines deterministic safety controls with retrieval-grounded LLM reasoning for mapping, business-rule inference, measure reconciliation, and iterative root-cause investigation.

## Architecture

```text
versioned YAML business context
             ↓ rebuild/sync
SQLite records + FTS5 + relationship graph
             ↓ bounded context package with provenance
provider-neutral structured LLM reasoning
             ↓ typed intent / proposal
metadata, evidence, template and budget guards
             ↓ controlled read-only SQL
normalized evidence → reasoning loop → conclusion or review
```

YAML under `context_layer/` is the source of truth. `context_layer/context.db` is a disposable local index and can be rebuilt at any time. `ContextRetriever` keeps storage replaceable; a semantic sidecar such as ChromaDB or Vertex AI Vector Search can be added without changing workflow logic.

The LLM is mandatory for agent reasoning. It is checked before either warehouse is accessed, and runtime failures are surfaced rather than silently replaced with deterministic guesses. Prompts and Pydantic response schemas are transport-independent.

## Repository layout

```text
config/                 project and provider configuration
inputs/                 run-specific table/column mappings, tests, overrides
context_layer/          versioned business knowledge and learned context
examples/               sample warehouse metadata
approvals/
  pending/              workbook awaiting a reviewer
  reviewed/             reviewer-completed workbook
  processed/            successfully imported workbook
  archive/              historical workbook copy
logs/<run_id>/           SQL, evidence, manifest, events, checkpoints, logs
outputs/<run_id>/        dq_validation_report.xlsx only
notebooks/               guided setup, review, validation, and learning flow
src/dq_agent/            application modules
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` or set the referenced credential environment variables in your shell. The application never stores secret values in YAML.

## Runtime inputs

- `inputs/table_mappings.xlsx`: `pair_id`, `enabled`, `mode`, and source/target catalog coordinates. Modes are `migration` and `bigquery_only`.
- `inputs/column_mappings.xlsx`: `pair_id`, `source_column`, `target_column`, and `status` (`MANUAL`, `APPROVED`, `REJECTED`, or blank for inference).
- `inputs/runtime_overrides.yaml`: optional source/target predicates keyed by pair. Predicates are still parsed and identifier-validated.
- `inputs/human_tests.yaml`: typed tests: `null`, `accepted_values`, `row_count`, `duplicate`, `freshness`, `aggregate`, and guarded `custom_sql`.

All fields and examples are documented in `inputs/README.md`.

## Business context

- `tables.yaml`: table classification, descriptions, aliases, keys, column semantics, accepted values, filters, SCD behavior, and grouped profiles.
- `relationships.yaml`: approved joins, relationship cardinality, keys, and optional filters.
- `measures.yaml`: governed aggregations, filters, grouping mappings, tolerances, and diagnostic limits.
- `patterns.yaml`: reusable rule and semantic patterns.
- `issue_patterns.yaml`: known failure signatures and diagnostic guidance.
- `learned_context.yaml`: only human-approved reusable agent knowledge.

Every indexed record has a stable provenance ID, source path, trust level, approval state, and version metadata. Retrieval combines exact matches, graph neighbors, FTS lexical rank, schema overlap, and trust weight. The workflow supplies a size-bounded package to each LLM step and records the selected provenance IDs.

## LLM configuration

Edit `config/llm.yaml`; switching models or providers requires no application change.

```yaml
backend: ollama
api_family: openai_compatible
model: gemma3:4b
endpoint: http://localhost:11434
project: null
location: null
project_env: GCP_PROJECT_ID
api_key_env: null
temperature: 0.0
max_output_tokens: 2048
timeout_seconds: 120
```

Supported transport families are OpenAI-compatible/Ollama, Vertex Gemini, Anthropic including Vertex Anthropic, direct OpenAI, and direct Anthropic. Provider SDK details live only in `llm.py`; application code calls `LLMClient.health_check`, `capabilities`, and `generate_structured`.

Vertex transports use ADC. A Vertex OpenAI-compatible model additionally needs its configured compatibility endpoint; an API-token environment variable can override ADC when required by that endpoint.

Ollama models must already be installed. The agent does not start Ollama or download models automatically.

## Run flow

Use notebooks in order for an interactive walkthrough:

1. `00_context_store_setup.ipynb` — rebuild the local context index.
2. `01_context_management.ipynb` — inspect hybrid retrieval and provenance.
3. `02_approval_processing.ipynb` — import a workbook moved to `reviewed/`.
4. `03`–`06` — relationship and measure exploration.
5. `07_rca_and_context_learning.ipynb` — inspect RCA evidence and learning policy.
6. `08_validation_with_context.ipynb` — canonical end-to-end run.

Or run from Python:

```python
from pathlib import Path
from dq_agent.workflow import DQWorkflow

manifest = DQWorkflow(Path.cwd()).run(run_id="validation_20260628T120000Z")
```

Each run creates exactly one report at `outputs/<run_id>/dq_validation_report.xlsx`. Developer artifacts never enter the output folder. If decisions are required, the run creates at most one workbook under `approvals/pending/`.

## Evidence-gated execution

Trusted configured and approved rules run automatically. A newly proposed LLM rule auto-runs only when confidence is at least `0.85`, referenced identifiers are verified, independent evidence is present, and the proposal compiles through an allowlisted rule template. All other proposals are represented in the single approval workbook.

Agentic RCA uses a bounded state loop. The model can rank hypotheses and select the next typed diagnostic intent, but it cannot submit SQL. The system verifies identifiers and approved joins, compiles an allowlisted read-only query, performs a BigQuery dry run, applies byte/call/depth/row/group limits, executes it, and returns normalized evidence. Duplicate steps, exhausted budgets, unsupported intents, or insufficient evidence produce human review instead of an unsafe query.

Conclusions are `confirmed`, `likely`, `possible`, or `undetermined`, with evidence IDs and rejected hypotheses. Reusable RCA knowledge is written only after Excel approval.

## Verification

Offline verification includes Python compilation, configuration validation, repeatable SQLite rebuilds, FTS and relationship retrieval, provenance checks, rule/diagnostic compilation, unsafe-SQL rejection, notebook parsing, and report/approval workbook inspection. Live warehouse checks run only when credentials and mapped tables are available.

See `dq_issues_capability_matrix.md` for coverage of the benchmark workbook and `dq_agent_conceptual_flow.md` for the detailed execution model.

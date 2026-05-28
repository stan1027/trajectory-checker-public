---
name: check-trajectory
description: >
  Validate LLM agent trajectory data (JSONL/JSON) against buyer-specific format specifications,
  especially configurable trajectory delivery rules. Triggers on: "check trajectory",
  "validate trajectory", "check data", "validate data", "QC data", "audit trajectory",
  "check if data meets spec", "verify data format", "delivery format", "delivery requirements",
  "add buyer", "list buyers", "new buyer profile".
argument-hint: "<data-file> [--buyer name] | --add-buyer <spec-doc> | --list-buyers"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# Trajectory Data Validator

Validate LLM agent execution trace data against buyer-specific format specifications.

## Environment

- Plugin layout: find the directory containing `skills/check-trajectory/SKILL.md`
- Standalone skill layout: find the directory containing this `SKILL.md`
- Checker script: `skills/check-trajectory/scripts/checker.py` in plugin layout, or `scripts/checker.py` in standalone skill layout
- Buyer profiles: `buyers/*.yaml` at plugin root, or `buyers/*.yaml` inside this skill directory
- Reports output: prefer a user-provided writable directory; otherwise use `reports/`

Before running, verify PyYAML is installed:

```bash
python3 -c "import yaml" 2>/dev/null || pip3 install pyyaml -q
```

## Mode Dispatch

Parse arguments to determine the mode:

1. Contains `--add-buyer`: use **Add Buyer** workflow.
2. Contains `--list-buyers`: use **List Buyers** workflow.
3. Otherwise: use **Check** workflow.

## Check Workflow

### 1. Identify buyer profile

- If `--buyer <name>` is provided, pass that name to the checker; it resolves `buyers/<name>.yaml`.
- If the user asks for the bundled delivery rules, use `--buyer delivery-standard`.
- If no buyer is provided:
  - If only one buyer profile exists excluding `_template.yaml`, use it.
  - If multiple profiles exist, ask which buyer to use.
  - If none exist, run structural checks only.

### 2. Run checker

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --buyer <name> \
  --data <data-file-path> \
  --json
```

Generate a Markdown report only when useful or requested:

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --buyer <name> \
  --data <data-file-path> \
  --json \
  --report <writable-report-dir>
```

If the skill is installed standalone in Codex, run the same script as:

```bash
python3 scripts/checker.py \
  --buyer <name> \
  --data <data-file-path> \
  --json
```

For structural-only mode:

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --data <data-file-path> \
  --structural-only \
  --json
```

### 3. Interpret JSON output

Read these fields:

- `verdict`: `PASS` or `FAIL`
- `summary.errors`: blocking issue count
- `summary.warnings`: issue count needing review
- `summary.info`: informational count
- `findings[]`: check name, severity, message, record index, and details
- `statistics`: aggregate metrics

`PASS` means there are no `error` findings. `warning` findings still need explanation or buyer-facing notes.

### 4. Bundled Delivery Profile Interpretation

Treat these as blocking errors unless the profile disables them:

- Required top-level fields: `task_id`, `prompt`, `candidates`, `tools`, `meta`
- `prompt[0]` must be a valid `system` message
- `candidates` must be a valid non-empty assistant candidate list
- `tool_call.id` and following `tool.tool_call_id` must pair correctly
- Assistant tool calls must reference tools declared in `tools`
- Tool call arguments must satisfy basic JSON schema `required` and `type`
- `tools` schema must include function name, description, and parameters
- `meta.model` must match allowed model families in the buyer profile
- `meta.harness` or `meta.scaffold` must match allowed scaffolds
- Template-style tool results, user/tool merge markers, synthetic markers, and exact/prefix duplicates are blocking

Treat these as warnings by default:

- Missing `signature`: include thinking signature if available, but the source model may not return it.
- Missing `reasoning_content`/`thinking`: preferred for quality review, but not automatically blocking.
- System harness marker not recognized.
- Tool description is short.
- User injection marker is absent.
- System/tools name coverage is low.
- Assistant output may be repetitive or garbled.

Do not advise fabricating missing `signature` or thinking fields. Preserve existing original fields when present and explain missing fields as source-model availability when appropriate.

### 5. Present results

For user-facing summaries, include:

1. Total records.
2. Verdict.
3. Error count, warning count, info count.
4. A table grouped by check name with severity, affected records/count, meaning, and suggested action.
5. A practical delivery judgment: directly deliverable, deliverable with notes, or not deliverable until fixed.

When the user asks for a detailed delivery-spec comparison, map findings to the rule categories and include statistics such as system uniqueness, tool schema uniqueness, assistant content rate, first-user uniqueness, and duplicate/session containment checks.

## Add Buyer Workflow

When the user provides a new buyer spec document:

1. Read the document path after `--add-buyer`.
2. Extract required fields, meta fields, model/scaffold restrictions, tool requirements, thinking/signature requirements, diversity thresholds, and anti-patterns.
3. Create `buyers/<buyer-name>.yaml` from `buyers/_template.yaml`.
4. Validate it:

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --validate-profile <buyer-name>
```

5. Show the user what was encoded and note any manual-only requirements.

## List Buyers Workflow

Read all `.yaml` files in `buyers/` excluding `_template.yaml`.

Display:

| Buyer | Display Name | Spec Version | Checks Enabled |
|-------|--------------|--------------|----------------|
| delivery-standard | Delivery Standard | redacted | enabled/total |

## Manual Review Caveats

Some requirements cannot be fully automated from JSON alone:

- Semantic consistency between thinking, action, tool result, and final answer.
- Exact equivalence to an official external Harness tool definition if that definition is not bundled.
- Attachment completeness when file binaries are not present.
- Whether low diversity is justified by a genuinely single-project dataset.

Call these out as residual risk rather than pretending the checker proves them.

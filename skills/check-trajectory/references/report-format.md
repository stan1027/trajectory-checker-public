# Report Format Reference

When generating reports, use this structure:

## Terminal Output Format

```
============================================================
Trajectory Validation Report
============================================================
  File:    <filename>
  Buyer:   <buyer display name>
  Records: <count>
  Verdict: PASS / FAIL
  Errors: N | Warnings: N | Info: N

--- ERRORS ---
  [FAIL] [check_name] Record N: <message>
  [FAIL] [check_name] <message>

--- WARNINGS ---
  [WARN] [check_name] Record N: <message>

--- INFO ---
  [INFO] [check_name] <message>

--- STATISTICS ---
  system_unique_count: 7/7
  system_avg_length: 27950 chars
  tools_unique_schemas: 3/7
  ...
```

## Markdown Report Format

```markdown
# Trajectory Validation Report

**File**: `<filename>`
**Buyer**: <buyer name> (<spec version>)
**Date**: <ISO date>
**Records**: <count>
**Verdict**: **PASS / FAIL**

## Summary

- Errors: N (blocks delivery)
- Warnings: N (should investigate)
- Info: N (statistics)

## Errors (Must Fix)

### <check_name> (Record N)

- **What**: <what was found>
- **Why it matters**: <buyer requirement context>
- **Records affected**: <count or IDs>
- **Fix**: <actionable remediation>

## Warnings (Should Fix)

### <check_name>

- **What**: <finding>
- **Context**: <why this might matter>
- **Suggestion**: <what to do>

## Statistics

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| System unique ratio | 100% | >=50% | PASS |
| System avg length | 27950 chars | >=5000 | PASS |
| Tools unique schemas | 3/7 | >1 | PASS |
| Assistant content rate | 65% | >=30% | PASS |
| ... | ... | ... | ... |

## Checklist

- [ ] All errors resolved
- [ ] Warnings reviewed
- [ ] Data re-checked after fixes
```

## Severity Definitions

- **Error**: Blocks delivery. The buyer will reject data with this issue. Must fix.
- **Warning**: Should investigate. May be acceptable with explanation, but likely needs attention.
- **Info**: Context only. Statistics and recommendations, not blocking.

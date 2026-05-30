#!/usr/bin/env python3
"""Trajectory data validator for LLM training data quality assurance.

Validates agent execution trace data (JSONL/JSON) against buyer-specific
format specifications defined in YAML profiles. Can run standalone without
Claude Code.

Usage:
    python checker.py --buyer delivery-standard --data data.jsonl
    python checker.py --buyer buyers/delivery-standard.yaml --data data.jsonl
    python checker.py --data data.jsonl --structural-only
    python checker.py --buyer delivery-standard --data data.jsonl --json --report reports/
    python checker.py --validate-profile buyers/new.yaml
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install with: pip install pyyaml")


@dataclass
class Finding:
    check_name: str
    severity: str  # "error", "warning", "info"
    message: str
    record_index: Optional[int] = None
    details: Optional[dict] = None


@dataclass
class CheckReport:
    buyer: Optional[str]
    data_path: str
    total_records: int
    timestamp: str
    findings: list = field(default_factory=list)
    statistics: dict = field(default_factory=dict)

    def has_errors(self):
        return any(f.severity == "error" for f in self.findings)

    def to_json(self):
        return json.dumps({
            "buyer": self.buyer,
            "data_path": self.data_path,
            "total_records": self.total_records,
            "timestamp": self.timestamp,
            "verdict": "FAIL" if self.has_errors() else "PASS",
            "summary": {
                "errors": sum(1 for f in self.findings if f.severity == "error"),
                "warnings": sum(1 for f in self.findings if f.severity == "warning"),
                "info": sum(1 for f in self.findings if f.severity == "info"),
            },
            "findings": [asdict(f) for f in self.findings],
            "statistics": self.statistics,
        }, ensure_ascii=False, indent=2)

    def to_terminal(self):
        lines = []
        verdict = "FAIL" if self.has_errors() else "PASS"
        errors = sum(1 for f in self.findings if f.severity == "error")
        warnings = sum(1 for f in self.findings if f.severity == "warning")
        infos = sum(1 for f in self.findings if f.severity == "info")

        lines.append(f"{'='*60}")
        lines.append(f"Trajectory Validation Report")
        lines.append(f"{'='*60}")
        lines.append(f"  File:    {self.data_path}")
        lines.append(f"  Buyer:   {self.buyer or 'N/A'}")
        lines.append(f"  Records: {self.total_records}")
        lines.append(f"  Verdict: {verdict}")
        lines.append(f"  Errors: {errors} | Warnings: {warnings} | Info: {infos}")
        lines.append("")

        for severity in ["error", "warning", "info"]:
            items = [f for f in self.findings if f.severity == severity]
            if not items:
                continue
            label = {"error": "ERRORS", "warning": "WARNINGS", "info": "INFO"}[severity]
            marker = {"error": "[FAIL]", "warning": "[WARN]", "info": "[INFO]"}[severity]
            lines.append(f"--- {label} ---")
            for f in items:
                rec = f"Record {f.record_index}: " if f.record_index is not None else ""
                lines.append(f"  {marker} [{f.check_name}] {rec}{f.message}")
            lines.append("")

        if self.statistics:
            lines.append("--- STATISTICS ---")
            for k, v in self.statistics.items():
                lines.append(f"  {k}: {v}")
            lines.append("")

        return "\n".join(lines)

    def to_markdown(self):
        verdict = "FAIL" if self.has_errors() else "PASS"
        errors = [f for f in self.findings if f.severity == "error"]
        warnings = [f for f in self.findings if f.severity == "warning"]
        infos = [f for f in self.findings if f.severity == "info"]

        lines = [
            f"# Trajectory Validation Report",
            f"",
            f"**File**: `{self.data_path}`",
            f"**Buyer**: {self.buyer or 'N/A'}",
            f"**Date**: {self.timestamp}",
            f"**Records**: {self.total_records}",
            f"**Verdict**: **{verdict}**",
            f"",
            f"## Summary",
            f"",
            f"- Errors: {len(errors)} (blocks delivery)",
            f"- Warnings: {len(warnings)} (should investigate)",
            f"- Info: {len(infos)} (statistics)",
            f"",
        ]

        if errors:
            lines.append("## Errors (Must Fix)\n")
            for f in errors:
                rec = f" (Record {f.record_index})" if f.record_index is not None else ""
                lines.append(f"### {f.check_name}{rec}\n")
                lines.append(f"- **What**: {f.message}")
                if f.details:
                    for k, v in f.details.items():
                        lines.append(f"- **{k}**: {v}")
                lines.append("")

        if warnings:
            lines.append("## Warnings (Should Fix)\n")
            for f in warnings:
                rec = f" (Record {f.record_index})" if f.record_index is not None else ""
                lines.append(f"### {f.check_name}{rec}\n")
                lines.append(f"- **What**: {f.message}")
                if f.details:
                    for k, v in f.details.items():
                        lines.append(f"- **{k}**: {v}")
                lines.append("")

        if self.statistics:
            lines.append("## Statistics\n")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for k, v in self.statistics.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        lines.append("## Checklist\n")
        lines.append("- [ ] All errors resolved")
        lines.append("- [ ] Warnings reviewed")
        lines.append("- [ ] Data re-checked after fixes")

        return "\n".join(lines)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _extract_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text", ""))
        return " ".join(parts)
    return str(content)


def _detect_harness(system_text: str, signatures: dict) -> Optional[str]:
    for harness_name, sig in signatures.items():
        markers = sig.get("system_markers", [])
        if any(m in system_text for m in markers):
            return harness_name
    return None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _compact_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _get_tool_function(tool: Any) -> dict:
    if not isinstance(tool, dict):
        return {}
    fn = tool.get("function")
    if isinstance(fn, dict):
        return fn
    if "name" in tool:
        return tool
    return {}


def _json_type_matches(value: Any, schema_type: Any) -> bool:
    if isinstance(schema_type, list):
        return any(_json_type_matches(value, t) for t in schema_type)
    if schema_type in (None, "any"):
        return True
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "null":
        return value is None
    return True


def _parse_tool_arguments(arguments: Any) -> tuple[Optional[Any], Optional[str]]:
    if arguments in (None, ""):
        return {}, None
    if isinstance(arguments, dict):
        return arguments, None
    if isinstance(arguments, str):
        try:
            return json.loads(arguments), None
        except json.JSONDecodeError as e:
            return None, f"arguments is not valid JSON: {e}"
    return None, f"arguments is {type(arguments).__name__}, expected JSON string or object"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen = set()
    result = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
    return result


def resolve_profile_path(profile: str) -> Path:
    """Resolve a buyer profile from either a path or a buyer name.

    The repo is used both as a Claude Code plugin and as a standalone Codex
    skill. In the plugin layout buyer profiles live at repo_root/buyers; in the
    standalone skill layout they live at skill_root/buyers.
    """
    raw = Path(profile).expanduser()
    script = Path(__file__).resolve()
    skill_root = script.parents[1]
    repo_root = script.parents[3] if len(script.parents) > 3 else skill_root

    candidates: list[Path] = []
    direct = raw if raw.is_absolute() else Path.cwd() / raw
    candidates.append(direct)
    if raw.suffix not in {".yaml", ".yml"}:
        candidates.append(direct.with_suffix(".yaml"))
        candidates.append(direct.with_suffix(".yml"))

    names = [raw.name]
    if raw.suffix not in {".yaml", ".yml"}:
        names.extend([f"{raw.name}.yaml", f"{raw.name}.yml"])

    for root in [Path.cwd(), repo_root, skill_root]:
        for name in names:
            candidates.append(root / "buyers" / name)

    for candidate in _dedupe_paths(candidates):
        if candidate.exists():
            return candidate
    return direct


class TrajectoryChecker:
    def __init__(self, profile_path: Optional[str], data_path: str):
        self.data_path = data_path
        self.profile = {}
        if profile_path:
            with open(profile_path, "r", encoding="utf-8") as f:
                self.profile = yaml.safe_load(f) or {}
        self.records: list[dict] = []
        self.findings: list[Finding] = []
        self.statistics: dict[str, Any] = {}

    def _t(self, key: str, default=None):
        return self.profile.get("thresholds", {}).get(key, default)

    def _check_enabled(self, name: str) -> bool:
        enabled = self.profile.get("checks_enabled", {})
        return enabled.get(name, True)

    def load_data(self):
        path = Path(self.data_path)
        if not path.exists():
            self.findings.append(Finding("load_data", "error", f"File not found: {self.data_path}"))
            return

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            self.findings.append(Finding("load_data", "error", "File is empty"))
            return

        first_char = text[0]
        try:
            if first_char == "[":
                data = json.loads(text)
                if isinstance(data, list):
                    self.records = data
                else:
                    self.records = [data]
            elif first_char == "{":
                # Try as single JSON object first (handles pretty-printed files)
                try:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        self.records = [data]
                    else:
                        self.records = [data]
                except json.JSONDecodeError:
                    # Fall back to JSONL (one JSON object per line)
                    for i, line in enumerate(text.splitlines()):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self.records.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            self.findings.append(Finding("load_data", "error",
                                f"JSON parse error at line {i+1}: {e}"))
            else:
                self.findings.append(Finding("load_data", "error",
                    f"Unrecognized format (starts with '{first_char}')"))
        except json.JSONDecodeError as e:
            self.findings.append(Finding("load_data", "error", f"JSON parse error: {e}"))

    def run_structural_only(self) -> CheckReport:
        self.load_data()
        if not self.records:
            return self._build_report()
        for i, rec in enumerate(self.records):
            self._check_top_level_fields(rec, i)
            self._check_system_format(rec, i)
            self._check_message_order(rec, i)
            self._check_tool_call_pairing(rec, i)
            self._check_candidates_format(rec, i)
        return self._build_report()

    def run_all(self) -> CheckReport:
        self.load_data()
        if not self.records:
            return self._build_report()

        for i, rec in enumerate(self.records):
            self._check_top_level_fields(rec, i)
            self._check_system_format(rec, i)
            self._check_message_order(rec, i)
            self._check_tool_call_pairing(rec, i)
            self._check_candidates_format(rec, i)
            self._check_system_harness_markers(rec, i)
            self._check_tool_name_field(rec, i)
            self._check_tools_description_length(rec, i)
            self._check_tools_schema_shape(rec, i)
            self._check_tool_call_declared(rec, i)
            self._check_tool_call_arguments_schema(rec, i)
            self._check_tool_result_template_markers(rec, i)
            self._check_user_injection_markers(rec, i)
            self._check_user_tool_merge_markers(rec, i)
            self._check_meta_required_fields(rec, i)
            self._check_allowed_scaffold_model(rec, i)
            self._check_signature_shape(rec, i)
            self._check_thinking_presence(rec, i)
            self._check_system_tools_consistency(rec, i)
            self._check_repetition_garbled(rec, i)
            self._check_synthetic_markers(rec, i)

        self._check_system_uniqueness()
        self._check_system_length()
        self._check_tools_uniqueness()
        self._check_tool_result_uniqueness()
        self._check_assistant_content_rate()
        self._check_user_first_uniqueness()

        if len(self.records) > 1:
            self._check_cross_record_dedup()
            self._check_prompt_candidates_tools_dedup()
            self._check_session_containment()

        return self._build_report()

    def _build_report(self) -> CheckReport:
        coverage_values = self.statistics.pop("_system_tools_name_coverage_values", None)
        if coverage_values:
            self.statistics["system_tools_name_coverage_min"] = f"{min(coverage_values):.0%}"
            self.statistics["system_tools_name_coverage_avg"] = (
                f"{sum(coverage_values) / len(coverage_values):.0%}"
            )
        return CheckReport(
            buyer=self.profile.get("display_name") or self.profile.get("buyer"),
            data_path=self.data_path,
            total_records=len(self.records),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            findings=self.findings,
            statistics=self.statistics,
        )

    # ================================================================
    # Structural checks (per-record)
    # ================================================================

    def _check_top_level_fields(self, rec: dict, idx: int):
        if not self._check_enabled("top_level_fields"):
            return
        required = self.profile.get("required_top_fields",
                                    ["task_id", "prompt", "candidates", "tools", "meta"])
        missing = [f for f in required if f not in rec]
        if missing:
            self.findings.append(Finding("top_level_fields", "error",
                f"Missing required fields: {missing}", idx))

    def _check_system_format(self, rec: dict, idx: int):
        if not self._check_enabled("system_format"):
            return
        prompt = rec.get("prompt", [])
        if not prompt:
            self.findings.append(Finding("system_format", "error",
                "prompt is empty", idx))
            return
        first = prompt[0]
        if first.get("role") != "system":
            self.findings.append(Finding("system_format", "error",
                f"prompt[0] role is '{first.get('role')}', expected 'system'", idx))
            return
        content = first.get("content", "")
        if isinstance(content, list):
            text_parts = [p for p in content
                          if isinstance(p, dict) and p.get("type") == "text"]
            if len(text_parts) > 1:
                self.findings.append(Finding("system_format", "warning",
                    f"system content has {len(text_parts)} text parts (expected 1)", idx))
        elif not isinstance(content, str):
            self.findings.append(Finding("system_format", "error",
                f"system content is {type(content).__name__}, expected str or list", idx))

    def _check_message_order(self, rec: dict, idx: int):
        if not self._check_enabled("message_order"):
            return
        prompt = rec.get("prompt", [])
        for i, msg in enumerate(prompt):
            if msg.get("role") == "tool":
                found_assistant = False
                for j in range(i - 1, -1, -1):
                    r = prompt[j].get("role")
                    if r == "assistant":
                        found_assistant = bool(prompt[j].get("tool_calls"))
                        break
                    elif r == "tool":
                        continue
                    else:
                        break
                if not found_assistant:
                    self.findings.append(Finding("message_order", "error",
                        f"msg[{i}] role=tool without preceding assistant.tool_calls", idx))
                    return

    def _check_tool_call_pairing(self, rec: dict, idx: int):
        if not self._check_enabled("tool_call_pairing"):
            return
        prompt = rec.get("prompt", [])
        i = 0
        while i < len(prompt):
            msg = prompt[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                call_ids = {tc.get("id") for tc in msg["tool_calls"]}
                result_ids = set()
                j = i + 1
                while j < len(prompt) and prompt[j].get("role") == "tool":
                    result_ids.add(prompt[j].get("tool_call_id"))
                    j += 1
                if call_ids != result_ids:
                    missing = call_ids - result_ids
                    extra = result_ids - call_ids
                    self.findings.append(Finding("tool_call_pairing", "error",
                        f"msg[{i}] ID mismatch: missing_results={missing or 'none'}, "
                        f"orphan_results={extra or 'none'}", idx))
                i = j
            else:
                i += 1

    def _check_candidates_format(self, rec: dict, idx: int):
        if not self._check_enabled("candidates_format"):
            return
        candidates = rec.get("candidates")
        if candidates is None:
            return
        if not isinstance(candidates, list) or not candidates:
            self.findings.append(Finding("candidates_format", "error",
                "candidates must be a non-empty list", idx))
            return
        c0 = candidates[0]
        if not isinstance(c0, list) or not c0:
            self.findings.append(Finding("candidates_format", "error",
                "candidates[0] must be a non-empty list", idx))
            return
        if c0[0].get("role") != "assistant":
            self.findings.append(Finding("candidates_format", "error",
                f"candidates[0][0] role is '{c0[0].get('role')}', expected 'assistant'", idx))

    # ================================================================
    # Content checks (per-record, buyer-dependent)
    # ================================================================

    def _check_system_harness_markers(self, rec: dict, idx: int):
        if not self._check_enabled("system_harness_markers"):
            return
        signatures = self.profile.get("harness_signatures", {})
        if not signatures:
            return
        sys_text = self._get_system_text(rec)
        harness = _detect_harness(sys_text, signatures)
        if not harness:
            known = list(signatures.keys())
            self.findings.append(Finding("system_harness_markers", "warning",
                f"Cannot detect harness from system prompt (known: {known})", idx))

    def _check_tool_name_field(self, rec: dict, idx: int):
        if not self._check_enabled("tool_name_field"):
            return
        if not self._t("tool_require_name", True):
            return
        prompt = rec.get("prompt", [])
        tool_msgs = [m for m in prompt if m.get("role") == "tool"]
        no_name = sum(1 for m in tool_msgs if not m.get("name"))
        if no_name > 0:
            self.findings.append(Finding("tool_name_field", "error",
                f"{no_name}/{len(tool_msgs)} tool messages missing 'name' field", idx))

    def _check_tools_description_length(self, rec: dict, idx: int):
        if not self._check_enabled("tools_description_length"):
            return
        min_len = self._t("tools_desc_min_length", 20)
        tools = rec.get("tools", [])
        short = []
        for t in tools:
            fn = _get_tool_function(t)
            desc = fn.get("description", "")
            if len(desc) < min_len:
                short.append(fn.get("name", "?"))
        if short:
            self.findings.append(Finding("tools_description_length", "warning",
                f"{len(short)} tools with description < {min_len} chars: {short[:5]}", idx))

    def _check_tools_schema_shape(self, rec: dict, idx: int):
        if not self._check_enabled("tools_schema_shape"):
            return
        tools = rec.get("tools", [])
        if not isinstance(tools, list):
            self.findings.append(Finding("tools_schema_shape", "error",
                f"tools is {type(tools).__name__}, expected list", idx))
            return

        missing = []
        for pos, tool in enumerate(tools):
            fn = _get_tool_function(tool)
            name = fn.get("name")
            if not name:
                missing.append(f"tools[{pos}].function.name")
            if not fn.get("description"):
                missing.append(f"{name or f'tools[{pos}]'}.description")
            params = fn.get("parameters")
            if self._t("tools_parameters_required", True):
                if not isinstance(params, dict):
                    missing.append(f"{name or f'tools[{pos}]'}.parameters")
                elif params.get("type") != "object" and "properties" not in params:
                    missing.append(f"{name or f'tools[{pos}]'}.parameters object schema")

        if missing:
            self.findings.append(Finding("tools_schema_shape", "error",
                f"Invalid or simplified tool schema fields: {missing[:10]}", idx,
                {"missing_or_invalid": missing[:50]}))

    def _declared_tools(self, rec: dict) -> dict[str, dict]:
        declared = {}
        for tool in rec.get("tools", []) if isinstance(rec.get("tools", []), list) else []:
            fn = _get_tool_function(tool)
            name = fn.get("name")
            if name:
                declared[name] = fn
        return declared

    def _iter_assistant_tool_calls(self, rec: dict):
        for source, messages in (("prompt", rec.get("prompt", [])), ("candidates", rec.get("candidates", []))):
            if source == "candidates" and isinstance(messages, list):
                flat = []
                for cand in messages:
                    if isinstance(cand, list):
                        flat.extend(cand)
                    elif isinstance(cand, dict):
                        flat.append(cand)
                messages = flat
            if not isinstance(messages, list):
                continue
            for msg_index, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") == "assistant":
                    for call_index, tc in enumerate(msg.get("tool_calls") or []):
                        yield source, msg_index, call_index, tc

    def _check_tool_call_declared(self, rec: dict, idx: int):
        if not self._check_enabled("tool_call_declared"):
            return
        declared = self._declared_tools(rec)
        if not declared:
            return

        bad = []
        for source, msg_index, call_index, tc in self._iter_assistant_tool_calls(rec):
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name") or (tc.get("name") if isinstance(tc, dict) else None)
            if name and name not in declared:
                bad.append(f"{source}[{msg_index}].tool_calls[{call_index}]={name}")
            elif not name:
                bad.append(f"{source}[{msg_index}].tool_calls[{call_index}]=<missing name>")

        if bad:
            self.findings.append(Finding("tool_call_declared", "error",
                f"Assistant calls undeclared/malformed tools: {bad[:10]}", idx,
                {"undeclared_calls": bad[:50], "declared_tools": sorted(declared.keys())[:100]}))

    def _check_tool_call_arguments_schema(self, rec: dict, idx: int):
        if not self._check_enabled("tool_call_arguments_schema"):
            return
        declared = self._declared_tools(rec)
        if not declared:
            return

        problems = []
        for source, msg_index, call_index, tc in self._iter_assistant_tool_calls(rec):
            if not isinstance(tc, dict):
                problems.append(f"{source}[{msg_index}].tool_calls[{call_index}] is not object")
                continue
            fn_call = tc.get("function", {}) if isinstance(tc.get("function"), dict) else tc
            name = fn_call.get("name")
            schema = declared.get(name, {}).get("parameters", {})
            args, err = _parse_tool_arguments(fn_call.get("arguments"))
            loc = f"{source}[{msg_index}].tool_calls[{call_index}]({name or '?'})"
            if err:
                problems.append(f"{loc}: {err}")
                continue
            if not isinstance(args, dict):
                problems.append(f"{loc}: arguments parsed to {type(args).__name__}, expected object")
                continue
            if not isinstance(schema, dict):
                continue

            required = schema.get("required", [])
            if isinstance(required, list):
                missing = [r for r in required if r not in args]
                if missing:
                    problems.append(f"{loc}: missing required args {missing}")

            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for key, subschema in properties.items():
                    if key not in args or not isinstance(subschema, dict):
                        continue
                    expected_type = subschema.get("type")
                    if expected_type and not _json_type_matches(args[key], expected_type):
                        problems.append(
                            f"{loc}: arg '{key}' type {type(args[key]).__name__} != schema {expected_type}"
                        )

        if problems:
            self.findings.append(Finding("tool_call_arguments_schema", "error",
                f"Tool call arguments do not match schemas: {problems[:8]}", idx,
                {"problems": problems[:50]}))

    def _check_tool_result_template_markers(self, rec: dict, idx: int):
        if not self._check_enabled("tool_result_template_markers"):
            return
        markers = self.profile.get("tool_result_template_markers", [])
        if not markers:
            return
        found = []
        for mi, msg in enumerate(rec.get("prompt", [])):
            if msg.get("role") != "tool":
                continue
            text = _extract_text(msg.get("content", ""))
            low = text.lower()
            for marker in markers:
                if marker.lower() in low:
                    found.append(f"msg[{mi}] {msg.get('name', 'unknown')}: {marker}")
                    break
        if found:
            self.findings.append(Finding("tool_result_template_markers", "error",
                f"Templated tool result markers found: {found[:10]}", idx,
                {"matches": found[:50]}))

    def _check_user_injection_markers(self, rec: dict, idx: int):
        if not self._check_enabled("user_injection_markers"):
            return
        if not self._t("user_injection_required", True):
            return
        markers = self.profile.get("user_injection_markers", ["<system-reminder>"])
        prompt = rec.get("prompt", [])
        user_msgs = [m for m in prompt if m.get("role") == "user"]
        if not user_msgs:
            return
        has_marker = any(
            any(mk in _extract_text(m.get("content", "")) for mk in markers)
            for m in user_msgs
        )
        if not has_marker:
            self.findings.append(Finding("user_injection_markers", "warning",
                f"No user message contains harness injection markers {markers}", idx))

    def _check_user_tool_merge_markers(self, rec: dict, idx: int):
        if not self._check_enabled("user_tool_merge_markers"):
            return
        markers = self.profile.get("user_tool_merge_markers", [])
        if not markers:
            return
        found = []
        for mi, msg in enumerate(rec.get("prompt", [])):
            if msg.get("role") != "user":
                continue
            text = _extract_text(msg.get("content", ""))
            low = text.lower()
            for marker in markers:
                if marker.lower() in low:
                    found.append(f"user msg[{mi}] contains {marker}")
                    break
        if found:
            self.findings.append(Finding("user_tool_merge_markers", "error",
                f"User messages appear to contain tool result/use markers: {found[:10]}", idx,
                {"matches": found[:50]}))

    def _check_meta_required_fields(self, rec: dict, idx: int):
        if not self._check_enabled("meta_required_fields"):
            return
        required = self.profile.get("required_meta_fields",
                                    ["create_time", "model", "harness"])
        meta = rec.get("meta", {})
        missing = [f for f in required if f not in meta]
        if missing:
            self.findings.append(Finding("meta_required_fields", "error",
                f"meta missing required fields: {missing}", idx))

        recommended = self.profile.get("recommended_meta_fields", [])
        rec_missing = [f for f in recommended if f not in meta]
        if rec_missing:
            self.findings.append(Finding("meta_required_fields", "info",
                f"meta missing recommended fields: {rec_missing}", idx))

    def _check_allowed_scaffold_model(self, rec: dict, idx: int):
        if not self._check_enabled("allowed_scaffold_model"):
            return
        meta = rec.get("meta", {}) if isinstance(rec.get("meta"), dict) else {}

        allowed_scaffolds = self.profile.get("allowed_scaffolds", [])
        if allowed_scaffolds:
            raw_harness = meta.get("harness") or meta.get("scaffold") or meta.get("source") or ""
            raw_norm = _compact_token(raw_harness)
            aliases = self.profile.get("allowed_scaffold_aliases", {})
            allowed_tokens = set()
            for scaffold in allowed_scaffolds:
                allowed_tokens.add(_compact_token(scaffold))
                for alias in aliases.get(scaffold, []):
                    allowed_tokens.add(_compact_token(alias))
            if raw_norm and raw_norm not in allowed_tokens:
                self.findings.append(Finding("allowed_scaffold_model", "error",
                    f"meta harness/scaffold '{raw_harness}' is outside allowed scaffolds {allowed_scaffolds}", idx))
            elif not raw_norm:
                self.findings.append(Finding("allowed_scaffold_model", "error",
                    "meta harness/scaffold is empty; cannot verify allowed scaffold", idx))

        allowed_models = self.profile.get("allowed_models", [])
        if allowed_models:
            raw_model = str(meta.get("model") or rec.get("model") or "")
            model_norm = _compact_token(raw_model)
            allowed_model_tokens = [_compact_token(m) for m in allowed_models]
            if not model_norm:
                self.findings.append(Finding("allowed_scaffold_model", "error",
                    "model is empty; cannot verify allowed model family", idx))
            elif not any(token and token in model_norm for token in allowed_model_tokens):
                self.findings.append(Finding("allowed_scaffold_model", "error",
                    f"model '{raw_model}' is outside allowed models {allowed_models}", idx))

    def _check_signature_shape(self, rec: dict, idx: int):
        if not self._check_enabled("signature_shape"):
            return
        if "signature" not in rec:
            if self._t("signature_preferred", False):
                self.findings.append(Finding("signature_shape", "warning",
                    "signature field is missing; include it if available, "
                    "but the source model may not return thinking signatures", idx))
            return
        sig = rec.get("signature")
        if sig in (None, "", [], {}):
            self.findings.append(Finding("signature_shape", "warning",
                "signature field exists but is empty", idx))
            return
        if not isinstance(sig, (str, list, dict)):
            self.findings.append(Finding("signature_shape", "warning",
                f"signature is {type(sig).__name__}, expected str/list/dict", idx))

    def _message_has_thinking(self, msg: dict) -> bool:
        for key in ("reasoning_content", "thinking", "thinking_content"):
            if _extract_text(msg.get(key, "")).strip():
                return True
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).lower()
                if "thinking" in part_type or "reasoning" in part_type:
                    if _extract_text(part.get("text") or part.get("content") or part).strip():
                        return True
        return False

    def _check_thinking_presence(self, rec: dict, idx: int):
        if not self._check_enabled("thinking_presence"):
            return
        if not self._t("thinking_preferred", True):
            return
        has_thinking = False
        for msg in rec.get("prompt", []):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and self._message_has_thinking(msg):
                has_thinking = True
                break
        if not has_thinking:
            for cand in rec.get("candidates", []) if isinstance(rec.get("candidates", []), list) else []:
                cand_msgs = cand if isinstance(cand, list) else [cand]
                if any(isinstance(m, dict) and self._message_has_thinking(m) for m in cand_msgs):
                    has_thinking = True
                    break
        if not has_thinking:
            self.findings.append(Finding("thinking_presence", "warning",
                "No reasoning_content/thinking field found; trajectories with thinking are preferred", idx))

    def _check_system_tools_consistency(self, rec: dict, idx: int):
        if not self._check_enabled("system_tools_consistency"):
            return
        tools = self._declared_tools(rec)
        if not tools:
            return
        sys_text = self._get_system_text(rec).lower()
        if not sys_text:
            return
        names = sorted(tools.keys())
        mentioned = [name for name in names if name.lower() in sys_text]
        coverage = len(mentioned) / len(names) if names else 1
        min_coverage = self._t("system_tools_name_min_coverage", 0.5)
        self.statistics.setdefault("system_tools_name_coverage_min", "n/a")
        prev = self.statistics.get("_system_tools_name_coverage_values", [])
        prev.append(coverage)
        self.statistics["_system_tools_name_coverage_values"] = prev
        if coverage < min_coverage:
            self.findings.append(Finding("system_tools_consistency", "warning",
                f"Only {len(mentioned)}/{len(names)} tool names appear in system prompt "
                f"(coverage {coverage:.0%} < {min_coverage:.0%})", idx,
                {"missing_examples": [n for n in names if n not in mentioned][:20]}))

    def _check_repetition_garbled(self, rec: dict, idx: int):
        if not self._check_enabled("repetition_garbled"):
            return
        texts = []
        suspicious = []
        for mi, msg in enumerate(rec.get("prompt", [])):
            if msg.get("role") != "assistant":
                continue
            text = _extract_text(msg.get("content", "")).strip()
            if text:
                texts.append(text)
                if "\ufffd" in text or text.count("\x00") > 0:
                    suspicious.append(f"assistant msg[{mi}] contains replacement/null characters")
                if len(text) >= 80:
                    sample = text[:400]
                    for n in range(12, 81):
                        if len(sample) >= n * 3:
                            chunk = sample[:n]
                            if chunk and chunk * 3 in sample:
                                suspicious.append(f"assistant msg[{mi}] has repeated text chunk")
                                break
        counts = Counter(texts)
        repeated = [text for text, count in counts.items() if count >= 3 and len(text) > 20]
        if repeated:
            suspicious.append(f"{len(repeated)} assistant text blocks repeated >=3 times")
        if suspicious:
            self.findings.append(Finding("repetition_garbled", "warning",
                f"Possible loop/garbled assistant output: {suspicious[:8]}", idx,
                {"signals": suspicious[:50]}))

    def _check_synthetic_markers(self, rec: dict, idx: int):
        if not self._check_enabled("synthetic_markers"):
            return
        markers = self.profile.get("synthetic_markers", [])
        if not markers:
            return

        prompt = rec.get("prompt", [])
        found_in = []

        for mi, msg in enumerate(prompt):
            role = msg.get("role", "")
            if role in ("user", "system"):
                text = _extract_text(msg.get("content", ""))
                for mk in markers:
                    if mk.lower() in text.lower():
                        found_in.append(f"{role}[{mi}]: '{mk}'")

        if found_in:
            self.findings.append(Finding("synthetic_markers", "error",
                f"Synthetic/production markers found: {found_in}", idx,
                {"markers_found": found_in}))

    # ================================================================
    # Statistical checks (aggregate)
    # ================================================================

    def _check_system_uniqueness(self):
        if not self._check_enabled("system_uniqueness"):
            return
        if len(self.records) < 2:
            return
        hashes = set()
        for rec in self.records:
            sys_text = self._get_system_text(rec)
            hashes.add(_hash(sys_text))

        ratio = len(hashes) / len(self.records)
        threshold = self._t("system_unique_ratio", 0.5)
        self.statistics["system_unique_count"] = f"{len(hashes)}/{len(self.records)}"
        self.statistics["system_unique_ratio"] = f"{ratio:.1%}"

        if ratio < threshold:
            self.findings.append(Finding("system_uniqueness", "error",
                f"Only {len(hashes)} unique system prompts in {len(self.records)} records "
                f"(ratio {ratio:.1%} < {threshold:.0%})",
                details={"unique": len(hashes), "total": len(self.records)}))

    def _check_system_length(self):
        if not self._check_enabled("system_length"):
            return
        default_min = self._t("system_min_length", 5000)
        signatures = self.profile.get("harness_signatures", {})
        lengths = []

        for i, rec in enumerate(self.records):
            sys_text = self._get_system_text(rec)
            length = len(sys_text)
            lengths.append(length)

            harness = _detect_harness(sys_text, signatures)
            min_len = default_min
            if harness and harness in signatures:
                min_len = signatures[harness].get("min_system_length", default_min)

            if length < min_len:
                self.findings.append(Finding("system_length", "error",
                    f"system length {length} chars < {min_len} "
                    f"(harness: {harness or 'unknown'})", i))

        if lengths:
            self.statistics["system_avg_length"] = f"{sum(lengths)//len(lengths)} chars"
            self.statistics["system_min_length"] = f"{min(lengths)} chars"
            self.statistics["system_max_length"] = f"{max(lengths)} chars"

    def _check_tools_uniqueness(self):
        if not self._check_enabled("tools_uniqueness"):
            return
        if not self._t("tools_must_differ", True):
            return
        if len(self.records) < 2:
            return
        hashes = set()
        for rec in self.records:
            tools = rec.get("tools", [])
            h = _hash(json.dumps(tools, sort_keys=True))
            hashes.add(h)

        self.statistics["tools_unique_schemas"] = f"{len(hashes)}/{len(self.records)}"

        if len(hashes) == 1:
            self.findings.append(Finding("tools_uniqueness", "error",
                f"All {len(self.records)} records have identical tools schema"))

    def _check_tool_result_uniqueness(self):
        if not self._check_enabled("tool_result_uniqueness"):
            return
        min_unique = self._t("tool_result_min_unique", 5)
        exempt = set(self.profile.get("builtin_tools_exempt", []))
        results_by_name: dict[str, list[str]] = defaultdict(list)

        for rec in self.records:
            for msg in rec.get("prompt", []):
                if msg.get("role") == "tool":
                    name = msg.get("name", "unknown")
                    content = msg.get("content", "")
                    results_by_name[name].append(
                        json.dumps(content, sort_keys=True) if not isinstance(content, str) else content
                    )

        for name, results in results_by_name.items():
            if name in exempt:
                continue
            unique = len(set(results))
            total = len(results)
            if total > min_unique and unique <= min_unique:
                self.findings.append(Finding("tool_result_uniqueness", "warning",
                    f"Tool '{name}': {total} calls but only {unique} unique results "
                    f"(threshold: >{min_unique})",
                    details={"tool": name, "total": total, "unique": unique}))

        self.statistics["tool_types_used"] = str(len(results_by_name))

    def _check_assistant_content_rate(self):
        if not self._check_enabled("assistant_content_rate"):
            return
        min_rate = self._t("assistant_content_min_rate", 0.3)

        for i, rec in enumerate(self.records):
            prompt = rec.get("prompt", [])
            asst_msgs = [m for m in prompt if m.get("role") == "assistant"]
            inter_step = asst_msgs[:-1] if len(asst_msgs) > 1 else asst_msgs
            if not inter_step:
                continue

            has_content = 0
            for m in inter_step:
                text = _extract_text(m.get("content", ""))
                reasoning = " ".join(
                    _extract_text(m.get(k, ""))
                    for k in ("reasoning_content", "thinking", "thinking_content")
                )
                if text.strip() or reasoning.strip() or self._message_has_thinking(m):
                    has_content += 1

            rate = has_content / len(inter_step)
            if rate < min_rate:
                self.findings.append(Finding("assistant_content_rate", "error",
                    f"Inter-step assistant content non-empty rate {rate:.0%} "
                    f"< {min_rate:.0%} ({has_content}/{len(inter_step)})", i))

        total_asst = sum(
            sum(1 for m in rec.get("prompt", []) if m.get("role") == "assistant")
            for rec in self.records
        )
        self.statistics["total_assistant_messages"] = str(total_asst)

    def _check_user_first_uniqueness(self):
        if not self._check_enabled("user_first_uniqueness"):
            return
        if len(self.records) < 2:
            return
        min_ratio = self._t("user_first_unique_ratio", 0.5)
        first_hashes = []

        for rec in self.records:
            prompt = rec.get("prompt", [])
            for m in prompt:
                if m.get("role") == "user":
                    text = _extract_text(m.get("content", ""))
                    first_hashes.append(_hash(text))
                    break

        if not first_hashes:
            return
        unique = len(set(first_hashes))
        ratio = unique / len(first_hashes)
        self.statistics["user_first_unique"] = f"{unique}/{len(first_hashes)}"

        if ratio < min_ratio:
            self.findings.append(Finding("user_first_uniqueness", "warning",
                f"Only {unique}/{len(first_hashes)} unique first user messages "
                f"(ratio {ratio:.1%} < {min_ratio:.0%})"))

    # ================================================================
    # Cross-record checks
    # ================================================================

    def _check_cross_record_dedup(self):
        if not self._check_enabled("cross_record_dedup"):
            return
        max_overlap = self._t("cross_record_max_overlap", 0.5)

        def extract_hashes(rec, role):
            hashes = set()
            for m in rec.get("prompt", []):
                if m.get("role") == role:
                    text = _extract_text(m.get("content", ""))
                    if len(text.strip()) > 20:
                        hashes.add(_hash(text))
            return hashes

        pairs_to_check = list(combinations(range(len(self.records)), 2))
        if len(pairs_to_check) > 200:
            import random
            random.seed(42)
            pairs_to_check = random.sample(pairs_to_check, 200)

        involved = set()
        for i, j in pairs_to_check:
            involved.add(i)
            involved.add(j)

        high_overlap_pairs = []
        for role in ["assistant", "user", "tool"]:
            role_hashes = {i: extract_hashes(self.records[i], role) for i in involved}
            for i, j in pairs_to_check:
                si, sj = role_hashes.get(i, set()), role_hashes.get(j, set())
                if not si or not sj:
                    continue
                overlap = len(si & sj)
                max_ratio = max(overlap / len(si), overlap / len(sj))
                if max_ratio > max_overlap:
                    high_overlap_pairs.append((i, j, role, max_ratio))

        if high_overlap_pairs:
            grouped = defaultdict(list)
            for i, j, role, ratio in high_overlap_pairs:
                grouped[(i, j)].append(f"{role}({ratio:.0%})")

            for (i, j), roles in grouped.items():
                self.findings.append(Finding("cross_record_dedup", "error",
                    f"Record {i} vs {j}: high overlap in {', '.join(roles)}",
                    details={"record_a": i, "record_b": j, "roles": roles}))

    def _pct_key(self, rec: dict) -> dict:
        return {
            "prompt": rec.get("prompt"),
            "candidates": rec.get("candidates"),
            "tools": rec.get("tools"),
        }

    def _pct_flat_tokens(self, rec: dict) -> list[str]:
        key = self._pct_key(rec)
        tokens = []
        for msg in key.get("prompt") or []:
            tokens.append(_canonical_json(msg))
        tokens.append("__CANDIDATES__")
        tokens.append(_canonical_json(key.get("candidates")))
        tokens.append("__TOOLS__")
        tokens.append(_canonical_json(key.get("tools")))
        return tokens

    def _check_prompt_candidates_tools_dedup(self):
        if not self._check_enabled("prompt_candidates_tools_dedup"):
            return
        exact: dict[str, list[int]] = defaultdict(list)
        key_hashes = []
        for i, rec in enumerate(self.records):
            h = _hash(_canonical_json(self._pct_key(rec)))
            exact[h].append(i)
            key_hashes.append(h)

        duplicate_groups = [idxs for idxs in exact.values() if len(idxs) > 1]
        for group in duplicate_groups:
            self.findings.append(Finding("prompt_candidates_tools_dedup", "error",
                f"Exact duplicate by prompt+candidates+tools: keep first record {group[0]}, remove {group[1:]}",
                details={"records": group, "keep": group[0], "remove": group[1:]}))

        if duplicate_groups:
            dup_count = sum(len(g) - 1 for g in duplicate_groups)
            self.statistics["exact_duplicate_prompt_candidates_tools"] = str(dup_count)

        sys_buckets: dict[str, list[int]] = defaultdict(list)
        for i, rec in enumerate(self.records):
            sys_buckets[_hash(self._get_system_text(rec))].append(i)

        prefix_hits = []
        for bucket in sys_buckets.values():
            if len(bucket) < 2:
                continue
            bucket_with_len = [(i, len(self.records[i].get("prompt", []))) for i in bucket]
            bucket_with_len.sort(key=lambda x: x[1])
            for ai in range(len(bucket_with_len)):
                idx_i, len_i = bucket_with_len[ai]
                if len_i < 3:
                    continue
                prompt_i = self.records[idx_i].get("prompt", [])
                prefix_hash_i = _hash(_canonical_json(prompt_i))
                for bi in range(ai + 1, len(bucket_with_len)):
                    idx_j, len_j = bucket_with_len[bi]
                    if key_hashes[idx_i] == key_hashes[idx_j]:
                        continue
                    prompt_j = self.records[idx_j].get("prompt", [])[:len_i]
                    if _hash(_canonical_json(prompt_j)) == prefix_hash_i:
                        prefix_hits.append((idx_i, idx_j, len_i, len_j))
                        break

        for short_idx, long_idx, short_len, long_len in prefix_hits:
            self.findings.append(Finding("prompt_candidates_tools_dedup", "error",
                f"Record {short_idx} is a prompt+candidates+tools prefix/subset of record {long_idx}; "
                f"keep longer record {long_idx}",
                details={
                    "remove": short_idx,
                    "keep": long_idx,
                    "short_tokens": short_len,
                    "long_tokens": long_len,
                }))

        if prefix_hits:
            self.statistics["prefix_duplicate_prompt_candidates_tools"] = str(len(prefix_hits))

    def _check_session_containment(self):
        if not self._check_enabled("session_containment"):
            return

        sys_hashes: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, rec in enumerate(self.records):
            sys_text = self._get_system_text(rec)
            plen = len(rec.get("prompt", []))
            sys_hashes[_hash(sys_text)].append((i, plen))

        prefix_cache: dict[tuple[int, int], str] = {}

        def prompt_prefix_hash(idx: int, n: int) -> str:
            key = (idx, n)
            cached = prefix_cache.get(key)
            if cached is not None:
                return cached
            prompt = self.records[idx].get("prompt", [])[:n]
            h = _hash(json.dumps(prompt, sort_keys=True))
            prefix_cache[key] = h
            return h

        containment_groups = []
        checked = set()

        for bucket in sys_hashes.values():
            if len(bucket) < 2:
                continue
            bucket.sort(key=lambda x: x[1], reverse=True)
            for ai in range(len(bucket)):
                idx_a, len_a = bucket[ai]
                if idx_a in checked:
                    continue
                group = [idx_a]
                for bi in range(ai + 1, len(bucket)):
                    idx_b, len_b = bucket[bi]
                    if idx_b in checked or len_b < 5:
                        continue
                    check_len = max(1, len_b - 3)
                    if prompt_prefix_hash(idx_a, check_len) == prompt_prefix_hash(idx_b, check_len):
                        group.append(idx_b)
                        checked.add(idx_b)
                if len(group) > 1:
                    containment_groups.append(group)
                    checked.add(idx_a)

        for group in containment_groups:
            sizes = [len(self.records[i].get("prompt", [])) for i in group]
            self.findings.append(Finding("session_containment", "error",
                f"Records {group} appear to be the same session at different truncation points "
                f"(sizes: {sizes})",
                details={"records": group, "sizes": sizes}))

    # ================================================================
    # Helpers
    # ================================================================

    def _get_system_text(self, rec: dict) -> str:
        prompt = rec.get("prompt", [])
        if not prompt:
            return ""
        first = prompt[0]
        if first.get("role") != "system":
            return ""
        return _extract_text(first.get("content", ""))

    @staticmethod
    def validate_profile(path: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f)
        except Exception as e:
            print(f"[ERROR] Cannot parse YAML: {e}", file=sys.stderr)
            return False

        if not isinstance(profile, dict):
            print("[ERROR] Profile must be a YAML mapping", file=sys.stderr)
            return False

        required_keys = ["buyer", "display_name", "thresholds"]
        missing = [k for k in required_keys if k not in profile]
        if missing:
            print(f"[ERROR] Missing required keys: {missing}", file=sys.stderr)
            return False

        thresholds = profile.get("thresholds", {})
        numeric_keys = [
            "system_min_length", "system_unique_ratio", "tools_desc_min_length",
            "tool_result_min_unique", "assistant_content_min_rate",
            "user_first_unique_ratio", "cross_record_max_overlap",
        ]
        for k in numeric_keys:
            v = thresholds.get(k)
            if v is not None and not isinstance(v, (int, float)):
                print(f"[ERROR] thresholds.{k} must be numeric, got {type(v).__name__}", file=sys.stderr)
                return False

        print(f"[OK] Profile '{profile.get('buyer')}' is valid", file=sys.stderr)
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Validate LLM agent trajectory data against buyer specifications"
    )
    parser.add_argument("--data", help="Path to data file (JSONL or JSON)")
    parser.add_argument("--buyer", help="Buyer YAML profile path or buyer name (for example: delivery-standard)")
    parser.add_argument("--report", help="Directory to write Markdown report")
    parser.add_argument("--structural-only", action="store_true",
                        help="Run only structural checks (no buyer profile needed)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON to stdout")
    parser.add_argument("--validate-profile", metavar="PROFILE",
                        help="Validate a buyer YAML profile and exit")
    parser.add_argument("--verbose", action="store_true",
                        help="Include info-level findings in terminal output")

    args = parser.parse_args()

    if args.validate_profile:
        ok = TrajectoryChecker.validate_profile(str(resolve_profile_path(args.validate_profile)))
        sys.exit(0 if ok else 2)

    if not args.data:
        parser.error("--data is required")

    buyer_path = None if args.structural_only or not args.buyer else str(resolve_profile_path(args.buyer))
    checker = TrajectoryChecker(buyer_path, args.data)

    if args.structural_only:
        report = checker.run_structural_only()
    else:
        report = checker.run_all()

    if args.json:
        print(report.to_json())
    else:
        print(report.to_terminal(), file=sys.stderr)

    if args.report:
        report_dir = Path(args.report)
        report_dir.mkdir(parents=True, exist_ok=True)
        data_stem = Path(args.data).stem
        buyer_name = checker.profile.get("buyer", "unknown")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"{data_stem}_{buyer_name}_{ts}.md"
        report_path.write_text(report.to_markdown(), encoding="utf-8")
        print(f"Report written to: {report_path}", file=sys.stderr)

    sys.exit(1 if report.has_errors() else 0)


if __name__ == "__main__":
    main()

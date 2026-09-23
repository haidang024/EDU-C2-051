"""PreProcessNode — validates incoming input for the Teacher PD Evidence Agent.

Outer Cat 2 node: required_trust_level = VERIFIED_EXTERNAL.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Validation messages are recorded with a "<NodeName>: " prefix so they stay
# traceable in the audit event. That prefix is an implementation detail: the
# caller sees these strings in chat, so it is stripped at the render boundary.
_NODE_PREFIX_RE = re.compile(r"^[A-Za-z]+Node:\s*")


def _caller_safe_errors(errors: list[str]) -> str:
    """Join validation errors with the internal node-name prefix removed."""
    return "; ".join(_NODE_PREFIX_RE.sub("", str(item)).strip() for item in errors)


class PreProcessNode(FunctionNode):
    """Validate and normalise the incoming request before domain processing.

    Enforces mutual exclusivity of teacher_id / cohort_filter, validates
    optional reporting_period date strings, and checks that institution_config
    is present.  Returns validated_input on success or ERROR with error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 domain hook: block prompt-injection patterns in teacher_id/cohort_filter."""
        teacher_id = state.get("teacher_id", "")
        if teacher_id and any(c in teacher_id for c in ("<", ">", "{", "}", ";")):
            from framework.errors import SecurityViolationError

            raise SecurityViolationError("PreProcessNode: teacher_id contains prohibited characters")
        return state

    def execute(self, state: dict) -> dict:  # noqa: D102
        errors: list[str] = []

        # Domain fields may arrive as top-level state keys (platform path) or
        # JSON-encoded in user_input (standalone/STG HTTP path).
        parsed: dict = {}
        user_input: str = state.get("user_input", "")
        if user_input and user_input.strip().startswith("{"):
            try:
                parsed = json.loads(user_input)
            except json.JSONDecodeError:
                pass

        teacher_id: str = state.get("teacher_id", "") or parsed.get("teacher_id", "")
        cohort_filter: dict = state.get("cohort_filter", {}) or parsed.get("cohort_filter", {})
        reporting_period: dict = state.get("reporting_period", {}) or parsed.get("reporting_period", {})
        institution_config: dict = state.get("institution_config", {}) or parsed.get("institution_config", {})

        # Mutual exclusivity: exactly one of teacher_id / cohort_filter
        has_teacher = bool(teacher_id and teacher_id.strip())
        has_cohort = bool(cohort_filter)

        if has_teacher and has_cohort:
            errors.append("PreProcessNode: provide either teacher_id OR cohort_filter, not both")
        elif not has_teacher and not has_cohort:
            errors.append("PreProcessNode: one of teacher_id or cohort_filter is required")

        # Optional reporting_period validation
        if reporting_period:
            start = reporting_period.get("start", "")
            end = reporting_period.get("end", "")
            if start and not _ISO_DATE_RE.match(start):
                errors.append(f"PreProcessNode: reporting_period.start must be ISO date (YYYY-MM-DD), got: {start!r}")
            if end and not _ISO_DATE_RE.match(end):
                errors.append(f"PreProcessNode: reporting_period.end must be ISO date (YYYY-MM-DD), got: {end!r}")
            if start and end and start > end:
                errors.append("PreProcessNode: reporting_period.start must not be after end")

        # institution_config reference must be present
        if not institution_config:
            errors.append("PreProcessNode: institution_config is required")

        if errors:
            emit_trace_event(
                "PreProcessNode_input_validated",
                {"outcome": "error", "error_count": len(errors)},
                state,
            )
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": (
                    "The teacher evidence request failed validation: " + _caller_safe_errors(errors)
                ),
                "input_error_guidance": [
                    "Provide JSON containing either teacher_id or cohort_filter, but not both.",
                    "Include institution_config and use YYYY-MM-DD dates in reporting_period when provided.",
                ],
            }

        validated_input = {
            "teacher_id": teacher_id.strip() if has_teacher else "",
            "cohort_filter": cohort_filter,
            "reporting_period": reporting_period,
            "institution_config": institution_config,
            "mode": "individual" if has_teacher else "cohort",
        }

        emit_trace_event(
            "PreProcessNode_input_validated",
            {
                "outcome": "success",
                "mode": validated_input["mode"],
                "has_reporting_period": bool(reporting_period),
            },
            state,
        )

        return {
            "validated_input": validated_input,
            # Promote parsed fields to top-level state so inner nodes can read them directly.
            "teacher_id": teacher_id.strip() if has_teacher else "",
            "cohort_filter": cohort_filter,
            "reporting_period": reporting_period,
            "institution_config": institution_config,
            # Forward pre-set approval_decision (e.g. STG smoke-check auto-approve).
            "approval_decision": parsed.get("approval_decision", ""),
            "status": AgentStatus.SUCCESS.value,
        }

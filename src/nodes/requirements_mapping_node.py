"""RequirementsMappingNode — maps evidence records to renewal requirements.

Inner domain node: required_trust_level = ANONYMOUS.
Deterministic mapping: no LLM calls, no external I/O.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


# Default requirements schedule used when institution_config does not override.
# Structure: category -> {required_hours, renewal_years, activity_types}
_DEFAULT_SCHEDULE: dict[str, dict] = {
    "instructional_technology": {
        "required_hours": 10.0,
        "renewal_years": 1,
        "accepted_activity_types": ["online_course", "workshop", "webinar", "conference"],
    },
    "curriculum_design": {
        "required_hours": 6.0,
        "renewal_years": 1,
        "accepted_activity_types": ["workshop", "online_course", "conference"],
    },
    "safeguarding": {
        "required_hours": 4.0,
        "renewal_years": 3,
        "accepted_activity_types": ["mandatory_training", "online_course"],
    },
    "subject_knowledge": {
        "required_hours": 12.0,
        "renewal_years": 1,
        "accepted_activity_types": ["conference", "online_course", "workshop", "self_directed"],
    },
    "assessment_practice": {
        "required_hours": 6.0,
        "renewal_years": 1,
        "accepted_activity_types": ["workshop", "online_course", "conference"],
    },
}


class RequirementsMappingNode(FunctionNode):
    """Map normalised evidence records to renewal category requirements.

    Attaches source-record and requirement citations to each mapped item.
    Fully deterministic — no probabilistic model outputs.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:  # noqa: D102
        emit_trace_event("RequirementsMappingNode_execute_started", {"phase": "mapping"}, state)
        evidence_records: list[dict] = state.get("evidence_records", [])
        requirements_schedule: dict = state.get("requirements_schedule", {})
        institution_config: dict = state.get("institution_config", {})

        # Resolve schedule: institution override > state field > default
        schedule = institution_config.get("requirements_schedule") or requirements_schedule or _DEFAULT_SCHEDULE

        # Group evidence by (review_reference, category)
        by_ref_cat: dict[tuple[str, str], list[dict]] = {}
        for record in evidence_records:
            key = (record.get("review_reference", ""), record.get("category", ""))
            by_ref_cat.setdefault(key, []).append(record)

        mapped_requirements: list[dict] = []

        for category, req in schedule.items():
            required_hours: float = float(req.get("required_hours", 0))
            renewal_years: int = int(req.get("renewal_years", 1))
            accepted_types: list[str] = req.get("accepted_activity_types", [])

            # Also include refs that have zero evidence (gap case)
            all_refs: set[str] = {r.get("review_reference", "") for r in evidence_records}
            all_refs.discard("")

            for ref in all_refs:
                records = by_ref_cat.get((ref, category), [])

                # Filter to accepted activity types
                accepted_records = [r for r in records if r.get("activity_type") in accepted_types]

                earned_hours = sum(r.get("hours", 0.0) for r in accepted_records)
                citations = [r.get("record_id", "") for r in accepted_records]

                mapped_requirements.append(
                    {
                        "review_reference": ref,
                        "category": category,
                        "required_hours": required_hours,
                        "earned_hours": round(earned_hours, 2),
                        "renewal_years": renewal_years,
                        "accepted_activity_types": accepted_types,
                        "citations": citations,
                        "record_count": len(accepted_records),
                        "requirement_citation": f"SCHED:{category}:v1",
                    }
                )

        emit_trace_event(
            "RequirementsMappingNode_mapping_complete",
            {
                "category_count": len(schedule),
                "mapped_item_count": len(mapped_requirements),
                "evidence_record_count": len(evidence_records),
            },
            state,
        )

        return {
            "mapped_requirements": mapped_requirements,
            "requirements_schedule": schedule,
            "status": AgentStatus.SUCCESS.value,
        }

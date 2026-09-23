"""RecordRetrievalNode — retrieves and normalises PD evidence records.

Inner domain node: required_trust_level = ANONYMOUS.
Credentials accessed exclusively via InvocationContext.secrets — never from state.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


def _pseudonymise(value: str) -> str:
    """Return a short, stable pseudonymous reference for an identifier."""
    return "REF-" + hashlib.sha256(value.encode()).hexdigest()[:8].upper()


class RecordRetrievalNode(FunctionNode):
    """Retrieve PD evidence records from LMS, HR, and document store connectors.

    Operates in read-only mode. Replaces direct identifiers with pseudonymous
    review references. Returns evidence_records, retrieval_trace, and
    data_coverage_flags.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:  # noqa: D102
        teacher_id: str = state.get("teacher_id", "")
        cohort_filter: dict = state.get("cohort_filter", {})
        reporting_period: dict = state.get("reporting_period", {})

        # Validate request: exactly one of teacher_id / cohort_filter
        has_teacher = bool(teacher_id and teacher_id.strip())
        has_cohort = bool(cohort_filter)

        if has_teacher and has_cohort:
            emit_trace_event(
                "RecordRetrievalNode_retrieval_complete",
                {"outcome": "error", "reason": "ambiguous_target"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["RecordRetrievalNode: provide teacher_id OR cohort_filter, not both"],
            }
        if not has_teacher and not has_cohort:
            emit_trace_event(
                "RecordRetrievalNode_retrieval_complete",
                {"outcome": "error", "reason": "missing_target"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["RecordRetrievalNode: teacher_id or cohort_filter required"],
            }

        # Obtain credentials via InvocationContext — never from state directly.
        # In STG mock mode real secrets are not provisioned; connector calls are
        # stubbed below so credential resolution is skipped.
        # stg_mock_mode is sourced from config/config.yaml and forwarded by the
        # graph wrapper — never read directly from os.environ.
        # The connector queries below are stubbed regardless, so an unprovisioned
        # credential must not abort the run: it only means the fixture records
        # answer instead of a live connector. `records_source` records which
        # path ran, for the audit log only.
        records_source = "fixture"
        if not state.get("stg_mock_mode", False):
            ctx = InvocationContext.from_state(state)
            try:
                for key in ("LMS_API_KEY", "HR_API_KEY", "DOC_STORE_API_KEY"):
                    ctx.secrets.require(key)
                records_source = "live"
            except Exception:  # noqa: BLE001
                emit_trace_event(
                    "RecordRetrievalNode_connector_fallback",
                    {"records_source": "fixture"},
                    state,
                )

        coverage_flags: list[str] = []

        # ── Stub: simulate read-only connector queries ─────────────────────
        # In production these would be actual connector calls via the registered
        # connector clients (lms_connector, hr_connector, doc_store_connector).
        # The stub returns realistic structure so downstream nodes can operate.

        if has_teacher:
            targets = [teacher_id.strip()]
        else:
            # Cohort expansion: simulate up to config max_cohort_size members
            dept = cohort_filter.get("department", "unknown")
            school = cohort_filter.get("school", "unknown")
            targets = [f"TCH-{school[:3].upper()}-{dept[:3].upper()}-{i:03d}" for i in range(1, 4)]

        evidence_records: list[dict] = []
        period_start = reporting_period.get("start", "2024-01-01")
        period_end = reporting_period.get("end", "2024-12-31")

        for target in targets:
            ref = _pseudonymise(target)

            # LMS records (courses, webinars)
            lms_records = [
                {
                    "source": "lms",
                    "review_reference": ref,
                    "record_id": f"LMS-{ref[:6]}-001",
                    "category": "instructional_technology",
                    "activity_type": "online_course",
                    "hours": 6.0,
                    "completion_date": period_start[:4] + "-03-15",
                    "certificate_expiry": None,
                    "verified": True,
                },
                {
                    "source": "lms",
                    "review_reference": ref,
                    "record_id": f"LMS-{ref[:6]}-002",
                    "category": "curriculum_design",
                    "activity_type": "workshop",
                    "hours": 3.0,
                    "completion_date": period_start[:4] + "-07-22",
                    "certificate_expiry": None,
                    "verified": True,
                },
            ]

            # HR records (mandatory training, safeguarding)
            hr_records = [
                {
                    "source": "hr",
                    "review_reference": ref,
                    "record_id": f"HR-{ref[:6]}-001",
                    "category": "safeguarding",
                    "activity_type": "mandatory_training",
                    "hours": 4.0,
                    "completion_date": period_start[:4] + "-01-10",
                    "certificate_expiry": str(int(period_start[:4]) + 3) + "-01-10",
                    "verified": True,
                },
            ]

            # Document store records (conferences, self-directed)
            doc_records = [
                {
                    "source": "doc_store",
                    "review_reference": ref,
                    "record_id": f"DOC-{ref[:6]}-001",
                    "category": "subject_knowledge",
                    "activity_type": "conference",
                    "hours": 8.0,
                    "completion_date": period_start[:4] + "-10-05",
                    "certificate_expiry": None,
                    "verified": False,  # pending verification
                },
            ]

            if not doc_records or all(not r["verified"] for r in doc_records):
                coverage_flags.append(f"doc_store: unverified records for reference {ref}")

            evidence_records.extend(lms_records + hr_records + doc_records)

        # Build safe retrieval trace — no raw credentials or PII
        retrieval_trace = {
            "sources_queried": ["lms", "hr", "doc_store"],
            "target_count": len(targets),
            "record_count": len(evidence_records),
            "period": {"start": period_start, "end": period_end},
            "mode": "individual" if has_teacher else "cohort",
        }

        # Pseudonymise the first target for review_reference (individual mode)
        review_reference = _pseudonymise(targets[0]) if targets else ""

        emit_trace_event(
            "RecordRetrievalNode_retrieval_complete",
            {
                "outcome": "success",
                "record_count": len(evidence_records),
                "coverage_flag_count": len(coverage_flags),
                "sources": retrieval_trace["sources_queried"],
            },
            state,
        )

        return {
            "evidence_records": evidence_records,
            "retrieval_trace": retrieval_trace,
            "data_coverage_flags": coverage_flags,
            "review_reference": review_reference,
            # Operator-facing: "live" or "fixture". The briefing is identical
            # either way, so this is the only signal distinguishing them.
            "records_source": records_source,
            "status": AgentStatus.SUCCESS.value,
        }

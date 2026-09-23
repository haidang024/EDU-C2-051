"""GapDeadlineAnalysisNode — deterministic gap and deadline analysis.

Inner domain node: required_trust_level = ANONYMOUS.
RAG urgency: red (overdue / <=30 days), amber (31-90 days), green (>90 days).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _urgency(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "unknown"
    delta = (deadline - today).days
    if delta < 0:
        return "red"  # overdue
    if delta <= 30:
        return "red"
    if delta <= 90:
        return "amber"
    return "green"


class GapDeadlineAnalysisNode(FunctionNode):
    """Identify PD gaps, calculate deadline urgency, and produce cohort roll-up.

    Operates deterministically on mapped_requirements. Always sets
    requires_human_review=True because compliance decisions require
    professional judgement before action.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:  # noqa: D102
        emit_trace_event("GapDeadlineAnalysisNode_execute_started", {"phase": "analysis"}, state)
        mapped_requirements: list[dict] = state.get("mapped_requirements", [])
        reporting_period: dict = state.get("reporting_period", {})

        today = date.today()
        period_end_str = reporting_period.get("end", "")
        period_end = _parse_date(period_end_str) or today

        gap_analysis: list[dict] = []
        urgency_counts: dict[str, int] = {"red": 0, "amber": 0, "green": 0, "unknown": 0}
        refs_with_gaps: set[str] = set()
        category_gap_counts: dict[str, int] = {}
        total_shortfall: float = 0.0

        for item in mapped_requirements:
            ref = item.get("review_reference", "")
            category = item.get("category", "")
            required_hours = float(item.get("required_hours", 0))
            earned_hours = float(item.get("earned_hours", 0))
            renewal_years = int(item.get("renewal_years", 1))
            citations = item.get("citations", [])

            shortfall = max(0.0, required_hours - earned_hours)
            is_gap = shortfall > 0

            # Deadline: period_end aligned to renewal cycle
            renewal_deadline = date(
                period_end.year + (renewal_years - 1),
                period_end.month,
                period_end.day,
            )
            urgency_val = _urgency(renewal_deadline, today)

            # Expired certificates: check certificate_expiry via evidence_records
            # (we only have citations here; expiry analysis is approximated)
            cert_expired = False  # refined by evidence_records if available

            if is_gap or cert_expired:
                refs_with_gaps.add(ref)
                category_gap_counts[category] = category_gap_counts.get(category, 0) + 1
                total_shortfall += shortfall

            urgency_counts[urgency_val] = urgency_counts.get(urgency_val, 0) + 1

            gap_analysis.append(
                {
                    "review_reference": ref,
                    "category": category,
                    "required_hours": required_hours,
                    "earned_hours": round(earned_hours, 2),
                    "shortfall_hours": round(shortfall, 2),
                    "is_gap": is_gap,
                    "renewal_deadline": renewal_deadline.isoformat(),
                    "urgency": urgency_val,
                    "certificate_expired": cert_expired,
                    "citations": citations,
                    "requirement_citation": item.get("requirement_citation", ""),
                }
            )

        # Cohort roll-up (works for single teacher too)
        all_refs = {item.get("review_reference", "") for item in mapped_requirements}
        all_refs.discard("")
        total_refs = len(all_refs)

        cohort_roll_up: dict = {
            "total_teachers": total_refs,
            "teachers_with_gaps": len(refs_with_gaps),
            "gap_rate_pct": round(100.0 * len(refs_with_gaps) / total_refs if total_refs else 0.0, 1),
            "total_gap_count": len([g for g in gap_analysis if g["is_gap"]]),
            "total_shortfall_hours": round(total_shortfall, 2),
            "urgency_counts": urgency_counts,
            "categories_with_gaps": list(category_gap_counts.keys()),
        }

        emit_trace_event(
            "GapDeadlineAnalysisNode_analysis_complete",
            {
                "total_items": len(gap_analysis),
                "gap_count": cohort_roll_up["total_gap_count"],
                "urgency_counts": urgency_counts,
                "teachers_with_gaps": len(refs_with_gaps),
            },
            state,
        )

        return {
            "gap_analysis": gap_analysis,
            "cohort_roll_up": cohort_roll_up,
            "requires_human_review": True,
            "status": AgentStatus.SUCCESS.value,
        }

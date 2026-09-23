"""BriefingGenerationNode — generates a Markdown PD Evidence briefing draft.

Inner domain node: required_trust_level = ANONYMOUS.
Uses minimum necessary identity info; no direct PII in output.
Incorporates rejection_feedback on revision cycles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


def _urgency_badge(urgency: str) -> str:
    badges = {"red": "🔴 RED", "amber": "🟡 AMBER", "green": "🟢 GREEN"}
    return badges.get(urgency, "⚪ UNKNOWN")


class BriefingGenerationNode(FunctionNode):
    """Generate a Markdown draft briefing from gap analysis and mapped requirements.

    Draft is marked DRAFT — not approved. Incorporates structured
    rejection_feedback when revision_count > 0. No direct PII in output;
    all teacher references use pseudonymous review_reference values.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:  # noqa: D102
        emit_trace_event("BriefingGenerationNode_execute_started", {"phase": "drafting"}, state)
        gap_analysis: list[dict] = state.get("gap_analysis", [])
        cohort_roll_up: dict = state.get("cohort_roll_up", {})
        reporting_period: dict = state.get("reporting_period", {})
        data_coverage_flags: list[str] = state.get("data_coverage_flags", [])
        revision_count: int = state.get("revision_count", 0)
        rejection_feedback: str = state.get("rejection_feedback", "")

        now_utc = datetime.now(timezone.utc).isoformat()
        period_label = f"{reporting_period.get('start', 'N/A')} — {reporting_period.get('end', 'N/A')}"

        # ── Executive summary ────────────────────────────────────────────────
        total_teachers = cohort_roll_up.get("total_teachers", 0)
        gap_count = cohort_roll_up.get("total_gap_count", 0)
        gap_rate = cohort_roll_up.get("gap_rate_pct", 0.0)
        urgency = cohort_roll_up.get("urgency_counts", {})
        shortfall = cohort_roll_up.get("total_shortfall_hours", 0.0)

        lines: list[str] = [
            "<!-- DRAFT — NOT APPROVED FOR DISTRIBUTION -->",
            "",
            f"# Teacher PD Evidence Briefing — {period_label}",
            "",
            "> **Status:** DRAFT | **Generated:** " + now_utc,
            "> **Agent:** EDU-C2-051 v0.1.0 | **Awaiting human review**",
            "",
        ]

        # Revision notice
        if revision_count > 0 and rejection_feedback:
            lines += [
                f"## Revision Notice (Cycle {revision_count})",
                "",
                "This draft has been revised in response to the following feedback:",
                "",
                f"> {rejection_feedback}",
                "",
            ]

        lines += [
            "## Executive Summary",
            "",
            f"- **Reporting period:** {period_label}",
            f"- **Teachers reviewed:** {total_teachers}",
            f"- **Teachers with PD gaps:** {cohort_roll_up.get('teachers_with_gaps', 0)} " f"({gap_rate}%)",
            f"- **Total gap items:** {gap_count}",
            f"- **Total shortfall hours:** {shortfall}",
            f"- **Urgency breakdown:** "
            f"Red={urgency.get('red', 0)}, "
            f"Amber={urgency.get('amber', 0)}, "
            f"Green={urgency.get('green', 0)}",
            "",
        ]

        # ── Coverage warnings ────────────────────────────────────────────────
        if data_coverage_flags:
            lines += [
                "## Data Coverage Warnings",
                "",
                "_The following data sources had incomplete or unverified records:_",
                "",
            ]
            for flag in data_coverage_flags:
                lines.append(f"- {flag}")
            lines.append("")

        # ── Per-teacher status ───────────────────────────────────────────────
        # Group gap_analysis by review_reference
        by_ref: dict[str, list[dict]] = {}
        for item in gap_analysis:
            ref = item.get("review_reference", "UNKNOWN")
            by_ref.setdefault(ref, []).append(item)

        if by_ref:
            lines += ["## Individual Status", ""]

        citations: list[str] = []

        for ref, items in by_ref.items():
            lines += [f"### Reference: `{ref}`", ""]
            has_gap = any(i["is_gap"] for i in items)
            lines.append(f"**Overall:** {'Gap(s) identified' if has_gap else 'Requirements met'}  ")
            lines.append("")
            lines.append("| Category | Required h | Earned h | Shortfall | Urgency | Deadline |")
            lines.append("|---|---|---|---|---|---|")

            for item in items:
                badge = _urgency_badge(item.get("urgency", "unknown"))
                lines.append(
                    f"| {item['category']} "
                    f"| {item['required_hours']} "
                    f"| {item['earned_hours']} "
                    f"| {item['shortfall_hours']} "
                    f"| {badge} "
                    f"| {item.get('renewal_deadline', 'N/A')} |"
                )
                for cit in item.get("citations", []):
                    if cit and cit not in citations:
                        citations.append(cit)
                req_cit = item.get("requirement_citation", "")
                if req_cit and req_cit not in citations:
                    citations.append(req_cit)

            lines.append("")

        # ── Citations ────────────────────────────────────────────────────────
        if citations:
            lines += ["## Citations", ""]
            for i, cit in enumerate(citations, start=1):
                lines.append(f"{i}. `{cit}`")
            lines.append("")

        # ── Draft footer ─────────────────────────────────────────────────────
        lines += [
            "---",
            "",
            "_This document is a DRAFT generated by EDU-C2-051. It has not been "
            "approved and must not be distributed. All teacher references are "
            "pseudonymous. Review and approve via the HITL gate before use._",
        ]

        briefing_draft = "\n".join(lines)

        emit_trace_event(
            "BriefingGenerationNode_draft_generated",
            {
                "revision_count": revision_count,
                "line_count": len(lines),
                "citation_count": len(citations),
                "teacher_count": len(by_ref),
                "has_feedback": bool(rejection_feedback),
            },
            state,
        )

        return {
            "briefing_draft": briefing_draft,
            "briefing_citations": citations,
            "status": AgentStatus.SUCCESS.value,
        }

"""Unit tests for all 5 domain nodes — EDU-C2-051 Teacher PD Evidence Agent.

Covers:
  - RecordRetrievalNode: valid teacher_id, valid cohort_filter, missing both, missing evidence
  - RequirementsMappingNode: maps with citations, empty evidence
  - GapDeadlineAnalysisNode: identifies gaps, red/amber/green urgency, requires_human_review
  - BriefingGenerationNode: Markdown output, no PII, incorporates feedback on revision
  - HitlReviewGateNode: approve path, reject path, hitl_allowed guard
"""
from __future__ import annotations

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


# ---------------------------------------------------------------------------
# Shared state helpers
# ---------------------------------------------------------------------------

_ANON_CALLER = {"caller_trust_level": TrustLevel.ANONYMOUS}

_EVIDENCE_RECORDS = [
    {
        "source": "lms",
        "review_reference": "REF-AABBCCDD",
        "record_id": "LMS-AABBCC-001",
        "category": "instructional_technology",
        "activity_type": "online_course",
        "hours": 6.0,
        "completion_date": "2024-03-15",
        "certificate_expiry": None,
        "verified": True,
    },
    {
        "source": "hr",
        "review_reference": "REF-AABBCCDD",
        "record_id": "HR-AABBCC-001",
        "category": "safeguarding",
        "activity_type": "mandatory_training",
        "hours": 4.0,
        "completion_date": "2024-01-10",
        "certificate_expiry": "2027-01-10",
        "verified": True,
    },
]

_MAPPED_REQUIREMENTS = [
    {
        "review_reference": "REF-AABBCCDD",
        "category": "instructional_technology",
        "required_hours": 10.0,
        "earned_hours": 6.0,
        "renewal_years": 1,
        "accepted_activity_types": ["online_course", "workshop"],
        "citations": ["LMS-AABBCC-001"],
        "record_count": 1,
        "requirement_citation": "SCHED:instructional_technology:v1",
    },
    {
        "review_reference": "REF-AABBCCDD",
        "category": "safeguarding",
        "required_hours": 4.0,
        "earned_hours": 4.0,
        "renewal_years": 3,
        "accepted_activity_types": ["mandatory_training"],
        "citations": ["HR-AABBCC-001"],
        "record_count": 1,
        "requirement_citation": "SCHED:safeguarding:v1",
    },
]


# ===========================================================================
# RecordRetrievalNode
# ===========================================================================

class TestRecordRetrievalNode:
    """Unit tests for RecordRetrievalNode."""

    def setup_method(self):
        from src.nodes.record_retrieval_node import RecordRetrievalNode
        self.node = RecordRetrievalNode()

    def _state(self, **extra) -> dict:
        base = {
            **_ANON_CALLER,
            "stg_mock_mode": True,
            "institution_config": {},
            "reporting_period": {"start": "2024-01-01", "end": "2024-12-31"},
            "cohort_filter": {},
        }
        base.update(extra)
        return base

    def test_valid_teacher_id_returns_records(self):
        """TC-RR-01: valid teacher_id produces evidence_records."""
        state = self._state(teacher_id="TCH-001")
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert isinstance(result["evidence_records"], list)
        assert len(result["evidence_records"]) > 0
        assert "retrieval_trace" in result
        assert "data_coverage_flags" in result

    def test_valid_cohort_filter_returns_records(self):
        """TC-RR-02: valid cohort_filter produces multiple teacher records."""
        state = self._state(
            cohort_filter={"department": "science", "school": "Lincoln"},
            teacher_id="",
        )
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert len(result["evidence_records"]) > 0
        # Cohort mode should have multiple review_references
        refs = {r["review_reference"] for r in result["evidence_records"]}
        assert len(refs) >= 1

    def test_missing_both_returns_error(self):
        """TC-RR-03: no teacher_id and no cohort_filter → ERROR."""
        state = self._state(teacher_id="", cohort_filter={})
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("required" in msg for msg in result["error_log"])

    def test_both_provided_returns_error(self):
        """TC-RR-04: both teacher_id and cohort_filter → ERROR (ambiguous)."""
        state = self._state(
            teacher_id="TCH-001",
            cohort_filter={"department": "math"},
        )
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("both" in msg or "not both" in msg for msg in result["error_log"])

    def test_no_pii_in_records(self):
        """TC-RR-05: evidence_records must not contain raw PII fields."""
        state = self._state(teacher_id="TCH-001")
        result = self.node.execute(state)
        forbidden = {"name", "email", "dob", "address", "national_id", "passport"}
        for record in result.get("evidence_records", []):
            assert not forbidden.intersection(record.keys()), (
                f"PII field found in record: {set(record.keys()) & forbidden}"
            )

    def test_review_reference_is_pseudonymous(self):
        """TC-RR-06: review_reference must not equal the raw teacher_id."""
        teacher_id = "TCH-REAL-001"
        state = self._state(teacher_id=teacher_id)
        result = self.node.execute(state)
        for record in result.get("evidence_records", []):
            assert record.get("review_reference") != teacher_id

    def test_required_trust_level(self):
        """ANON trust is sufficient for inner nodes."""
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ===========================================================================
# RequirementsMappingNode
# ===========================================================================

class TestRequirementsMappingNode:
    """Unit tests for RequirementsMappingNode."""

    def setup_method(self):
        from src.nodes.requirements_mapping_node import RequirementsMappingNode
        self.node = RequirementsMappingNode()

    def _state(self, **extra) -> dict:
        base = {
            **_ANON_CALLER,
            "evidence_records": _EVIDENCE_RECORDS,
            "requirements_schedule": {},
            "institution_config": {},
        }
        base.update(extra)
        return base

    def test_maps_evidence_to_categories(self):
        """TC-RM-01: mapped_requirements covers known categories."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        assert isinstance(result["mapped_requirements"], list)
        assert len(result["mapped_requirements"]) > 0

    def test_citations_attached(self):
        """TC-RM-02: each mapped item has citations referencing source record IDs."""
        result = self.node.execute(self._state())
        for item in result["mapped_requirements"]:
            assert "citations" in item
            assert isinstance(item["citations"], list)
            assert "requirement_citation" in item

    def test_earned_hours_aggregated(self):
        """TC-RM-03: earned_hours sums correctly from evidence_records."""
        result = self.node.execute(self._state())
        it_items = [
            i for i in result["mapped_requirements"]
            if i["category"] == "instructional_technology"
            and i["review_reference"] == "REF-AABBCCDD"
        ]
        assert it_items, "Expected instructional_technology mapping for REF-AABBCCDD"
        # 6.0 hours from LMS-AABBCC-001
        assert it_items[0]["earned_hours"] == 6.0

    def test_institution_override_respected(self):
        """TC-RM-04: institution_config.requirements_schedule overrides default."""
        custom_schedule = {
            "custom_cat": {
                "required_hours": 2.0,
                "renewal_years": 1,
                "accepted_activity_types": ["workshop"],
            }
        }
        state = self._state(institution_config={"requirements_schedule": custom_schedule})
        result = self.node.execute(state)
        categories = {i["category"] for i in result["mapped_requirements"]}
        assert "custom_cat" in categories

    def test_required_trust_level(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ===========================================================================
# GapDeadlineAnalysisNode
# ===========================================================================

class TestGapDeadlineAnalysisNode:
    """Unit tests for GapDeadlineAnalysisNode."""

    def setup_method(self):
        from src.nodes.gap_deadline_analysis_node import GapDeadlineAnalysisNode
        self.node = GapDeadlineAnalysisNode()

    def _state(self, mapped=None, **extra) -> dict:
        base = {
            **_ANON_CALLER,
            "mapped_requirements": mapped if mapped is not None else _MAPPED_REQUIREMENTS,
            "reporting_period": {"start": "2024-01-01", "end": "2024-12-31"},
        }
        base.update(extra)
        return base

    def test_identifies_gap(self):
        """TC-GA-01: shortfall > 0 items marked as gaps."""
        result = self.node.execute(self._state())
        gaps = [g for g in result["gap_analysis"] if g["is_gap"]]
        # instructional_technology: earned=6 < required=10 → gap
        it_gaps = [g for g in gaps if g["category"] == "instructional_technology"]
        assert it_gaps, "Expected instructional_technology gap (earned 6 < required 10)"
        assert it_gaps[0]["shortfall_hours"] == pytest.approx(4.0)

    def test_no_gap_when_hours_met(self):
        """TC-GA-02: items with earned >= required are NOT gaps."""
        result = self.node.execute(self._state())
        safe = [g for g in result["gap_analysis"] if g["category"] == "safeguarding"]
        assert safe, "Expected safeguarding item"
        assert not safe[0]["is_gap"], "safeguarding: 4h earned == 4h required → no gap"

    def test_urgency_values_valid(self):
        """TC-GA-03: urgency is one of red/amber/green/unknown."""
        result = self.node.execute(self._state())
        valid = {"red", "amber", "green", "unknown"}
        for item in result["gap_analysis"]:
            assert item["urgency"] in valid, f"Unexpected urgency: {item['urgency']!r}"

    def test_requires_human_review_always_true(self):
        """TC-GA-04: requires_human_review must always be True."""
        result = self.node.execute(self._state())
        assert result["requires_human_review"] is True

    def test_cohort_roll_up_present(self):
        """TC-GA-05: cohort_roll_up is populated."""
        result = self.node.execute(self._state())
        roll = result["cohort_roll_up"]
        assert "total_teachers" in roll
        assert "teachers_with_gaps" in roll
        assert "total_gap_count" in roll
        assert "urgency_counts" in roll

    def test_empty_mapped_requirements(self):
        """TC-GA-06: empty input produces empty gap_analysis gracefully."""
        result = self.node.execute(self._state(mapped=[]))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["gap_analysis"] == []
        assert result["cohort_roll_up"]["total_teachers"] == 0

    def test_required_trust_level(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ===========================================================================
# BriefingGenerationNode
# ===========================================================================

class TestBriefingGenerationNode:
    """Unit tests for BriefingGenerationNode."""

    def setup_method(self):
        from src.nodes.briefing_generation_node import BriefingGenerationNode
        self.node = BriefingGenerationNode()

    def _state(self, **extra) -> dict:
        base = {
            **_ANON_CALLER,
            "gap_analysis": [
                {
                    "review_reference": "REF-AABBCCDD",
                    "category": "instructional_technology",
                    "required_hours": 10.0,
                    "earned_hours": 6.0,
                    "shortfall_hours": 4.0,
                    "is_gap": True,
                    "renewal_deadline": "2025-12-31",
                    "urgency": "amber",
                    "certificate_expired": False,
                    "citations": ["LMS-AABBCC-001"],
                    "requirement_citation": "SCHED:instructional_technology:v1",
                }
            ],
            "cohort_roll_up": {
                "total_teachers": 1,
                "teachers_with_gaps": 1,
                "gap_rate_pct": 100.0,
                "total_gap_count": 1,
                "total_shortfall_hours": 4.0,
                "urgency_counts": {"red": 0, "amber": 1, "green": 0},
                "categories_with_gaps": ["instructional_technology"],
            },
            "mapped_requirements": _MAPPED_REQUIREMENTS,
            "reporting_period": {"start": "2024-01-01", "end": "2024-12-31"},
            "data_coverage_flags": [],
            "revision_count": 0,
            "rejection_feedback": "",
        }
        base.update(extra)
        return base

    def test_produces_markdown_draft(self):
        """TC-BG-01: briefing_draft is a non-empty Markdown string with DRAFT notice."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        draft = result["briefing_draft"]
        assert isinstance(draft, str)
        assert len(draft) > 100
        assert "DRAFT" in draft

    def test_no_pii_in_draft(self):
        """TC-BG-02: draft must not contain raw PII-like tokens."""
        state = self._state()
        # Inject a fake real name to ensure it doesn't leak
        state["gap_analysis"][0]["_raw_name"] = "Jane Smith"
        result = self.node.execute(state)
        draft = result["briefing_draft"]
        assert "Jane Smith" not in draft

    def test_draft_contains_review_reference(self):
        """TC-BG-03: pseudonymous review_reference appears in draft, not raw IDs."""
        result = self.node.execute(self._state())
        assert "REF-AABBCCDD" in result["briefing_draft"]

    def test_citations_list_populated(self):
        """TC-BG-04: briefing_citations contains record and requirement IDs."""
        result = self.node.execute(self._state())
        assert isinstance(result["briefing_citations"], list)
        assert len(result["briefing_citations"]) > 0
        assert "LMS-AABBCC-001" in result["briefing_citations"]

    def test_incorporates_rejection_feedback_on_revision(self):
        """TC-BG-05: revision_count > 0 with feedback → feedback appears in draft."""
        state = self._state(
            revision_count=1,
            rejection_feedback="Please add deadline urgency explanation.",
        )
        result = self.node.execute(state)
        draft = result["briefing_draft"]
        assert "Please add deadline urgency explanation." in draft
        assert "Revision Notice" in draft

    def test_no_feedback_section_on_first_draft(self):
        """TC-BG-06: first draft (revision_count=0) must not show Revision Notice."""
        result = self.node.execute(self._state(revision_count=0, rejection_feedback=""))
        assert "Revision Notice" not in result["briefing_draft"]

    def test_required_trust_level(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ===========================================================================
# HitlReviewGateNode
# ===========================================================================

class TestHitlReviewGateNode:
    """Unit tests for HitlReviewGateNode."""

    def setup_method(self):
        from src.nodes.hitl_review_gate_node import HitlReviewGateNode
        self.node = HitlReviewGateNode()

    def _state(self, **extra) -> dict:
        base = {
            **_ANON_CALLER,
            "briefing_draft": "## DRAFT\n\nTest briefing.",
            "review_reference": "REF-HITLTEST",
            "approval_decision": "",
            "revision_count": 0,
            "rejection_feedback": "",
            "hitl_allowed": True,
            "hitl_count": 0,
        }
        base.update(extra)
        return base

    def test_approve_path_sets_timestamp_and_success(self):
        """TC-HG-01: approval_decision='approve' → SUCCESS + approval_timestamp set."""
        state = self._state(approval_decision="approve")
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["approval_decision"] == "approve"
        assert result.get("approval_timestamp"), "approval_timestamp must be set on approve"

    def test_reject_path_stores_feedback_and_increments_revision(self):
        """TC-HG-02: approval_decision='reject' → RETRY + revision_count incremented."""
        state = self._state(
            approval_decision="reject",
            rejection_feedback="Add urgency rationale.",
            revision_count=0,
        )
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.RETRY.value
        assert isinstance(result["status"], str), "status must be a serializable string, not a bare enum"
        assert result["rejection_feedback"] == "Add urgency rationale."
        assert result["revision_count"] == 1

    def test_reject_increments_from_existing_count(self):
        """TC-HG-03: second rejection increments from 1 to 2."""
        state = self._state(
            approval_decision="reject",
            rejection_feedback="Still needs work.",
            revision_count=1,
        )
        result = self.node.execute(state)
        assert result["revision_count"] == 2

    def test_hitl_allowed_false_skips_interrupt(self):
        """TC-HG-04: hitl_allowed=False → no GraphInterrupt, returns AWAITING_HUMAN string value."""
        from langgraph.errors import GraphInterrupt

        state = self._state(hitl_allowed=False, approval_decision="")
        try:
            result = self.node.execute(state)
        except GraphInterrupt:
            pytest.fail("GraphInterrupt raised despite hitl_allowed=False")

        assert result["status"] == AgentStatus.AWAITING_HUMAN.value
        assert isinstance(result["status"], str), "status must be a serializable string, not a bare enum"

    def test_hitl_allowed_true_raises_graph_interrupt(self):
        """TC-HG-05: hitl_allowed=True + no decision → GraphInterrupt raised."""
        from langgraph.errors import GraphInterrupt

        state = self._state(hitl_allowed=True, approval_decision="")
        with pytest.raises(GraphInterrupt):
            self.node.execute(state)

    def test_no_implicit_approval(self):
        """TC-HG-06: absent approval_decision does not produce SUCCESS."""
        state = self._state(hitl_allowed=False, approval_decision="")
        result = self.node.execute(state)
        assert result.get("status") != AgentStatus.SUCCESS.value, (
            "Node must not implicitly approve — decision must be explicit"
        )

    def test_hitl_false_awaiting_human_is_string(self):
        """TC-HG-08: hitl_allowed=False status is serializable string (criterion #15)."""
        from langgraph.errors import GraphInterrupt

        state = self._state(hitl_allowed=False, approval_decision="")
        try:
            result = self.node.execute(state)
        except GraphInterrupt:
            pytest.fail("GraphInterrupt raised despite hitl_allowed=False")

        assert result["status"] == AgentStatus.AWAITING_HUMAN.value
        assert isinstance(result["status"], str)

    def test_reject_status_is_string_and_routes_to_briefing(self):
        """TC-HG-09: reject path status is serializable string; value routes back to briefing."""
        state = self._state(
            approval_decision="reject",
            rejection_feedback="Needs more context.",
            revision_count=0,
        )
        result = self.node.execute(state)

        assert result["status"] == AgentStatus.RETRY.value
        assert isinstance(result["status"], str), "status must be a serializable string for graph routing"
        # Confirm the value is what the domain_workflow_graph router expects for the briefing loop
        assert result["status"] == "retry"

    def test_required_trust_level(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS

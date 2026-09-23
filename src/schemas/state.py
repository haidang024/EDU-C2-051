"""State schema for EDU-C2-051 Teacher Professional Development Evidence Agent.

Flat TypedDict inheriting AgentState. All fields use pseudonymous references
or aggregated data — no raw PII, credentials, or connection strings.

ADR-005: State must be a flat TypedDict. LangGraph checkpoints use msgpack
serialization, so only plain serializable fields are allowed.
"""

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Complete state for the Teacher PD Evidence workflow.

    Fields are grouped by workflow stage. No field may hold raw PII,
    API keys, secrets, tokens, passwords, or connection strings.
    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    # ── Input / routing ──────────────────────────────────────────────────────
    teacher_id: str  # pseudonymous internal reference (e.g. "TCH-0042")
    cohort_filter: dict  # e.g. {"department": "science", "school": "Lincoln"}
    reporting_period: dict  # {"start": "2024-01-01", "end": "2024-12-31"}
    institution_config: dict  # connector/schedule config refs (no credentials)
    stg_mock_mode: bool  # runtime connector stub switch from config/config.yaml
    max_cohort_size: int  # runtime cohort expansion ceiling

    # ── Record retrieval ─────────────────────────────────────────────────────
    evidence_records: list  # normalized PD records (sanitized, no raw PII)
    # Which source answered: "live" when the connector credentials are
    # provisioned, "fixture" otherwise. Operator-facing only.
    records_source: str | None
    retrieval_trace: dict  # safe audit refs (no raw PII/credentials)
    data_coverage_flags: list  # incomplete source warnings

    # ── Requirements mapping ─────────────────────────────────────────────────
    requirements_schedule: dict  # renewal categories/hours/periods
    mapped_requirements: list  # evidence mapped to requirements with citations

    # ── Gap / deadline analysis ──────────────────────────────────────────────
    gap_analysis: list  # gaps, deadlines, urgency per teacher/category
    cohort_roll_up: dict  # aggregated cohort summary
    requires_human_review: bool

    # ── Briefing generation ──────────────────────────────────────────────────
    briefing_draft: str  # Markdown draft (DRAFT status, not approved)
    briefing_citations: list  # evidence/requirement citation refs

    # ── HITL review ──────────────────────────────────────────────────────────
    review_reference: str  # pseudonymous reviewer reference
    approval_decision: str  # "approve" | "reject" | ""
    approval_timestamp: str  # ISO timestamp
    rejection_feedback: str  # structured feedback for revision
    revision_count: int  # number of revision cycles
    input_error_message: str | None
    input_error_guidance: list[str]
    # Inner-workflow failure reason, carried as a domain field so the run keeps
    # a valid AgentStatus and still reaches post_process.
    workflow_error_message: str | None
    # Stable classification + the raw cause. Both MUST stay declared here:
    # LangGraph drops undeclared keys between nodes, which would lose the
    # failure before post_process can report it. `workflow_error_detail` is
    # log-facing only — it can carry tracebacks and secret key names, so it is
    # never rendered into the caller-visible output.
    workflow_error_code: str | None
    workflow_error_detail: str | None
    generation_mode: str | None
    provider_error_message: str | None

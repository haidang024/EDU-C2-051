# Design Specification — EDU-C2-051 Teacher Professional Development Evidence Agent

## Position in AgentCore Architecture

- **Agent ID**: EDU-C2-051
- **Agent Class**: Graph
- **Industry**: EDU (Education)
- **Category**: Cat 2 — multi-step domain workflow
- **L1 Base**: AgentBaseGraph (outer) + BaseGraph (inner)
- **Three-Layer Separation**:
  - **State**: flat TypedDict (`State` extends `AgentState`) — no Pydantic, no dataclass (msgpack serialization compatibility)
  - **Node**: L1 inheritance (`FunctionNode.execute(self, state: dict) -> dict` override only)
  - **Graph**: composition (`register_nodes()` for node substitution; `add_edges()` in inner graph only)

---

## Architecture Overview

### Cat 2 Two-Layer Design

```
Outer graph (graph.py — Graph : AgentBaseGraph)
  Backbone: START → initialize → pre_process → main → post_process → finalize → END
                                                          ↓ (RETRY, max 3)
                                                       pre_process

  main slot: DomainWorkflowGraphNode (GraphNode, propagate_hitl=True)
      │
      └─► Inner graph (domain_workflow_graph.py — DomainWorkflowGraph : BaseGraph)
              START → retrieval → mapping → analysis → briefing → hitl_gate → END
                          ↓ error                                      ↓ reject
                         END                                        briefing (revision loop)
```

### Node Table

| Node | Class | Inherits | Trust Level | Responsibility |
|------|-------|----------|-------------|----------------|
| initialize | InitializeNode | FunctionNode | ANONYMOUS | Framework backbone — session init |
| pre_process | PreProcessNode | FunctionNode | **VERIFIED_EXTERNAL** | Input validation: teacher_id XOR cohort_filter, date formats, institution_config |
| main | DomainWorkflowGraphNode | GraphNode | ANONYMOUS | Wraps inner DomainWorkflowGraph; propagates HITL |
| post_process | PostProcessNode | FunctionNode | **VERIFIED_EXTERNAL** | Format approved briefing → `formatted_output` |
| finalize | FinalizeNode | FunctionNode | ANONYMOUS | Framework backbone — session close |
| *(inner)* retrieval | RecordRetrievalNode | FunctionNode | ANONYMOUS | Read-only connector queries; pseudonymise identifiers |
| *(inner)* mapping | RequirementsMappingNode | FunctionNode | ANONYMOUS | Map evidence to renewal categories with citations |
| *(inner)* analysis | GapDeadlineAnalysisNode | FunctionNode | ANONYMOUS | Deterministic gap + RAG urgency + cohort roll-up |
| *(inner)* briefing | BriefingGenerationNode | FunctionNode | ANONYMOUS | Markdown draft; no PII; revision feedback incorporated |
| *(inner)* hitl_gate | HitlReviewGateNode | FunctionNode | ANONYMOUS | interrupt() → human review; approve/reject loop |

> **Trust-trap rule**: inner nodes (inside BaseGraph) use ANONYMOUS. Only outer pre_process and post_process use VERIFIED_EXTERNAL. Assigning VERIFIED_EXTERNAL to inner nodes is a HIGH finding.

---

## State Field Table

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| teacher_id | str | caller | Pseudonymous internal ref (e.g. "TCH-0042") |
| cohort_filter | dict | caller | e.g. `{"department": "science", "school": "Lincoln"}` |
| reporting_period | dict | caller | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` |
| institution_config | dict | caller | Connector/schedule config refs (no credentials) |
| evidence_records | list | retrieval | Normalised PD records; no raw PII |
| retrieval_trace | dict | retrieval | Safe audit refs (no credentials/PII) |
| data_coverage_flags | list | retrieval | Incomplete/unverified source warnings |
| requirements_schedule | dict | mapping | Resolved renewal categories/hours/periods |
| mapped_requirements | list | mapping | Evidence mapped to categories with citations |
| gap_analysis | list | analysis | Gaps, deadlines, urgency per teacher/category |
| cohort_roll_up | dict | analysis | Aggregated cohort summary |
| requires_human_review | bool | analysis | Always True — compliance requires judgement |
| briefing_draft | str | briefing | Markdown draft (DRAFT status, not approved) |
| briefing_citations | list | briefing | Evidence/requirement citation refs |
| review_reference | str | retrieval | Pseudonymous reviewer reference |
| approval_decision | str | HITL resume | "approve" \| "reject" \| "" |
| approval_timestamp | str | hitl_gate | ISO timestamp on approve |
| rejection_feedback | str | HITL resume | Structured feedback for revision |
| revision_count | int | hitl_gate | Number of revision cycles completed |

**Prohibited state fields**: `jwt`, `token`, `api_key`, `secret`, `password`, `credential`, `connection_string` — these must never appear in state. Credentials are accessed exclusively via `InvocationContext.secrets.require()`.

---

## Data Flow Detail

### 1. Record Retrieval
- Validates `teacher_id` XOR `cohort_filter`
- Obtains connector credentials via `InvocationContext.from_state(state).secrets.require("LMS_API_KEY")` etc.
- Simulates read-only LMS / HR / document-store queries
- Replaces raw identifiers with `SHA-256[:8]` pseudonymous `review_reference`
- Emits `data_coverage_flags` for unverified doc-store records

### 2. Requirements Mapping
- Resolves schedule: `institution_config.requirements_schedule` > `state.requirements_schedule` > built-in default
- Groups evidence by `(review_reference, category)`; filters to accepted activity types
- Aggregates `earned_hours`; attaches `record_id` citations and `SCHED:category:v1` requirement citations

### 3. Gap & Deadline Analysis
- Deterministic: `shortfall = required_hours - earned_hours`
- RAG urgency: **red** (overdue or ≤30 days to deadline), **amber** (31–90 days), **green** (>90 days)
- Cohort roll-up: gap rate %, total shortfall hours, urgency breakdown
- `requires_human_review = True` always — compliance decisions are not automated

### 4. Briefing Generation
- Markdown sections: executive summary → coverage warnings → per-teacher status table → citations → DRAFT footer
- All teacher references use `review_reference` (pseudonymous); no direct PII
- Revision cycles: `revision_count > 0` → "Revision Notice" section incorporating `rejection_feedback`

### 5. HITL Review Gate
- **No decision yet** + `hitl_allowed=True` → raises `GraphInterrupt` with safe payload
- **Resume (approve)**: sets `approval_timestamp`, `review_reference`, `status=SUCCESS`
- **Resume (reject)**: stores `rejection_feedback`, increments `revision_count`, `status=RETRY` → `domain_workflow_graph.route()` loops back to briefing
- **`hitl_allowed=False`** (batch/test): skips interrupt, returns `AWAITING_HUMAN`

---

## Security Mapping

| ID | Control | Implementation |
|----|---------|----------------|
| S-1 | Trust gate | `BaseNode.__call__()` checks `caller_trust_level >= required_trust_level` before execute(); outer nodes require VERIFIED_EXTERNAL |
| S-2 | Input gate | `_security_gate_input()` (@final in framework); extension via `_extra_security_gate_input()` returning state on non-error path |
| S-3 | Output gate | `_security_gate_output()` (@final in framework); extension via `_extra_security_gate_output()` |
| S-4 | Lifecycle events | `emit_trace_event("node_start"/"node_complete")` called by framework __call__(); never in execute() |
| S-5 | No credentials in state | State TypedDict has no jwt/token/api_key/secret fields; enforced by CI credential scan |

---

## Runtime Configuration and Optional LLM Injection

- Static AgentRegistry identity is declared at root level in `config/agent.yaml`.
- Runtime controls (`memory_enabled`, `hitl`, retry/timeout, cohort size, connector
  mappings, and provider settings) live in `config/config.yaml`.
- Azure OpenAI is constructed per invocation by `src/services/llm_runtime.py`.
- Gap/deadline analysis and briefing
  generation remain deterministic and auditable (`generation_mode: deterministic`).
- `DomainWorkflowGraphNode` retains one checkpointer-backed subgraph instance so
  the resume call uses the same checkpoint created when the HITL interrupt fired.

## EU AI Act Art.13 Design-Time Evidence

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Decision support for authorized education administrators reviewing teacher professional-development evidence, renewal gaps, and deadlines |
| System capabilities and limitations | Retrieves declared records, pseudonymises identifiers, performs deterministic arithmetic and schedule mapping, and drafts a cited briefing; it cannot approve, renew, discipline, assign, or otherwise make employment decisions |
| User-facing transparency information | Every briefing is marked DRAFT, lists citations and coverage warnings, identifies the agent/version, and states that distribution requires approval |
| Human oversight mechanism | `HitlReviewGateNode` interrupts before delivery; only explicit approval reaches post-processing, while rejection loops to revision and non-interactive suppression remains `awaiting_human` |

---

## HITL State / Resume Design

```
Outer caller (API / LangGraph engine)
    │
    │  invoke(input, ctx)
    ▼
Graph (outer graph)
    │
    │  DomainWorkflowGraphNode.execute() [propagate_hitl=True]
    ▼
DomainWorkflowGraph (inner graph)
    │
    │  hitl_gate node
    ▼
HitlReviewGateNode.execute()
    │  approval_decision == "" and hitl_allowed=True
    │
    ├─► raise GraphInterrupt(payload)
    │       LangGraph checkpoints state
    │       Outer caller receives interrupt signal
    │       Human reviewer reads briefing_draft via payload
    │       Reviewer sets approval_decision + optional rejection_feedback
    │       Framework resumes from checkpoint
    │
    └─► On resume: approval_decision in state
            "approve" → SUCCESS → post_process → formatted_output
            "reject"  → RETRY  → route() → briefing (revision loop)
```

**Max HITL cycles**: `config/config.yaml` sets `hitl.max_hitl: 5`.

**Memory/checkpoint requirement**: `config/config.yaml` sets both
`memory_enabled: true` and `hitl.enabled: true`. The standalone adapter uses an
in-memory checkpointer; deployed environments must provide durable persistence.

---

## Design Decision Record

| DDR | Decision | Rationale |
|-----|----------|-----------|
| DDR-1 | Inner graph inherits BaseGraph (not AgentBaseGraph) | Fully custom node topology; no pre_process/main/post_process slots needed inside |
| DDR-2 | `propagate_hitl=True` on DomainWorkflowGraphNode | HITL must surface to external caller; keeping it inside inner graph would silently swallow the interrupt |
| DDR-3 | `requires_human_review=True` always | PD compliance decisions carry regulatory and professional liability; automation alone is not acceptable |
| DDR-4 | SHA-256[:8] pseudonymisation in retrieval | Stable, reversible (with original value), low collision risk for cohort sizes ≤50 |
| DDR-5 | Deterministic gap analysis (no LLM) | Reproducibility required for audit; hours/dates are unambiguous arithmetic |
| DDR-6 | Revision loop via RETRY status + conditional edge | Avoids outer graph retry counter; inner graph manages revision count independently up to max_hitl |

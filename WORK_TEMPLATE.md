# 🎯 Active Work Tracker - [Initiative Title]

> [!warning] **Template Usage Instructions** (DELETE THIS SECTION when creating your work file)
>
> **Quick Start**:
> 1. Copy this template: `cp WORK_TEMPLATE.md .claude/work/WORK.md`
> 2. Fill in `[Initiative Title]`, status tags, and epic details below
> 3. Delete this warning section
> 4. Use optional sections based on work type (#user-facing, #backend, #devops)
> 5. Track AI assistance in the 🤖 AI Collaboration section
>
> **Why `.claude/work/`?** Files in `.claude/` are auto-loaded by Claude Code for context
>
> **When Complete**: Archive to `docs/work-archive/WORK-YYYYMMDD-epic-name.md`

---

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[ROADMAP.md]].

**Current Initiative**: [Initiative Title]
**Status**: `#not-started` `#[type-tag]` `#[component-tag]` `#[version-tag]`
**Last Updated**: [Date]

> [!tip] Status Tag Guide
> - **Progress**: `#not-started`, `#in-progress`, `#blocked`, `#testing`, `#complete`
> - **Type**: `#user-facing`, `#backend`, `#devops`, `#refactor`, `#bugfix`
> - **Component**: `#domain`, `#infrastructure`, `#cli`, `#workflow`, `#database`
> - **Version**: `#v0.3.0`, `#v1.0.0`

## Progress Overview
- [ ] **[High-Level Goal 1]** 🔜 (Not Started - Current focus)
- [ ] **[High-Level Goal 2]**

---

## 🔜 NEW Epic: [Epic Name] `#not-started`

**Goal**: [Clearly state the primary goal of this epic. What user or system problem is being solved?]
**Why**: [Explain the user or business value. Why is this important? What new capabilities does it unlock? Refer to [[ROADMAP.md]] if applicable.]
**Effort**: [XS, S, M, L, XL] - [Brief justification for effort estimate]

> [!tip] Work Type Guidance
> Choose the sections below that match your work type:
> - **User-Facing** (#user-facing): Fill in "User Stories & Scenarios" and emphasize examples in "User-Facing Changes"
> - **Backend/Technical** (#backend, #refactor): Fill in "System Behavior Contract" and emphasize "Implementation Details"
> - **DevOps/Infrastructure** (#devops): Fill in "Deployment Impact" and "Rollback Strategy"
> - **Mixed**: Use whichever sections are relevant to your specific epic

### 👤 User Stories & Scenarios (Optional - For User-Facing Work)

> [!note] Skip this section for pure backend/technical work
> For user-facing features, describe who needs this and why

**User Stories**:
- As a [user type], I want to [action], so that [benefit]
- As a [user type], I want to [action], so that [benefit]

**Key Scenarios**:
1. [Scenario 1: Describe a typical user workflow]
2. [Scenario 2: Describe an edge case or alternate path]

### 🔒 System Behavior Contract (Optional - For Backend/Technical Work)

> [!warning] Critical for refactoring and internal changes
> What existing behavior MUST NOT break?

**Guaranteed Behaviors**:
- [Behavior 1 that external code relies on]
- [Behavior 2 that must remain stable]
- [Performance characteristic that must be maintained]

**Safe to Change**:
- [Internal implementation detail 1]
- [Internal implementation detail 2]

### 🤔 Architectural Decision Record

**Status**: [Proposed | Accepted | Superseded]
**Date**: [YYYY-MM-DD]
**Deciders**: [Who made this decision - e.g., "Solo dev after analyzing codebase" or "Team discussion"]

#### Context & Problem Statement
[What is the issue we're addressing? What constraints or requirements exist? What was discovered after analyzing the existing codebase?]

#### Decision
[What approach are we taking? Describe the chosen architecture or implementation strategy. How will it work? What are the key components and their interactions?]

#### Consequences

**Positive**:
- [Benefit 1 - e.g., Simplicity, Performance, Maintainability]
- [Benefit 2]
- [Benefit 3]

**Negative**:
- [Trade-off 1 - e.g., Increased complexity in X, Limited flexibility for Y]
- [Trade-off 2]

**Neutral**:
- [Side-effect 1 - e.g., Requires migration of existing data, Changes CLI interface]

#### Alternatives Considered

**Option A: [Alternative Name]**
- **Pros**: [What makes this attractive?]
- **Cons**: [Why not choose this?]
- **Rejected because**: [Specific reasoning]

**Option B: [Alternative Name]**
- **Pros**: [What makes this attractive?]
- **Cons**: [Why not choose this?]
- **Rejected because**: [Specific reasoning]

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: [Phase Name e.g., Foundational Work]**
- [ ] **Task 1.1**: [Brief description of the task.]
- [ ] **Task 1.2**: [Brief description of the task.]

**Phase 2: [Phase Name e.g., Feature Implementation]**
- [ ] **Task 2.1**: [Brief description of the task.]
- [ ] **Task 2.2**: [Brief description of the task.]

**Phase 3: [Phase Name e.g., Testing & Documentation]**
- [ ] **Task 3.1**: [Brief description of the task.]
- [ ] **Task 3.2**: [Brief description of the task.]

### ✨ User-Facing Changes & Examples
[Describe what the user will see or how they will interact with the new feature. Provide concrete examples of new CLI commands, API endpoints, or workflow node configurations. Keep examples brief and illustrative.]

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: [Changes to entities, repository interfaces, etc.]
- **Application**: [New or modified Use Cases.]
- **Infrastructure**: [New repository implementations, DB changes, etc.]
- **Interface**: [Changes to CLI, API, etc.]

**Testing Strategy**:
- **Unit**: [What to test at the unit level?]
- **Integration**: [What interactions to test?]
- **E2E/Workflow**: [What critical user path to validate?]
- **User Impact** (if #user-facing): [How will you validate the user experience?]

### 🚀 Deployment Impact (Optional - For DevOps/Infrastructure Work)

> [!caution] Skip this section for pure domain/application work
> For infrastructure changes, database migrations, or deployment process updates

**Deployment Steps**:
1. [Step 1 - e.g., "Run database migration"]
2. [Step 2 - e.g., "Update environment variables"]
3. [Step 3 - e.g., "Restart services"]

**Downtime**: [None | Estimated X minutes during migration | Rolling deployment]

**Risk Assessment**:
- **High Risk**: [What could go catastrophically wrong?]
- **Medium Risk**: [What might cause issues?]
- **Mitigation**: [How to reduce these risks?]

### ↩️ Rollback Strategy (Optional - For High-Risk Changes)

> [!important] Critical for database migrations and breaking changes
> How do we undo this if it goes wrong?

**Rollback Steps**:
1. [Step 1 to revert the change]
2. [Step 2 to restore previous state]
3. [Step 3 to validate rollback]

**Data Safety**:
- [How is existing data preserved during rollback?]
- [Are there any irreversible changes?]

**Key Files to Modify**:
- `src/domain/...`
- `src/application/...`
- `src/infrastructure/...`
- `tests/unit/...`
- `tests/integration/...`

---

## 🤖 AI Collaboration Tracking

### Agent Assistance Log

> [!note] Track which Claude Code agents helped and how
> This helps reuse knowledge and understand what worked well

| Agent Type | Task | Outcome | Notes |
|------------|------|---------|-------|
| Explore | [What you asked the agent to find] | [What it found] | [How it helped] |
| Plan | [What you asked the agent to design] | [What it recommended] | [Accepted/Modified/Rejected] |
| sqlalchemy-query-expert | [Database query optimization task] | [Recommended approach] | [How you applied it] |

### Context Boundaries

> [!important] Critical info for resuming work or future sessions
> This section helps Claude Code (or you) quickly get back up to speed

**Critical Files to Read First**:
- `[file_path]` - [Why this file is critical to understand]
- `[file_path]` - [Why this file is critical to understand]
- `CLAUDE.md` - [Always read for repository conventions]

**Key Concepts to Understand**:
- [Core concept 1 that's essential to this work]
- [Core concept 2 that's essential to this work]
- [Core concept 3 that's essential to this work]

**Dependencies & Prerequisites**:
- [Technology/pattern that must be understood - e.g., "SQLAlchemy 2.0 async patterns"]
- [Domain knowledge required - e.g., "Track matching algorithm fundamentals"]
- [Codebase patterns - e.g., "Repository pattern with UnitOfWork for transactions"]

### AI-Assisted Decisions

> [!tip] Maintain transparency and human oversight
> Track AI suggestions vs human decisions to learn what works

| Decision Point | AI Suggestion | Human Decision | Rationale |
|----------------|---------------|----------------|-----------|
| [What decision needed to be made] | [What AI recommended] | [Accepted/Rejected/Modified] | [Why you made this choice] |
| [Next decision point] | [AI recommendation] | [Your decision] | [Your reasoning] |
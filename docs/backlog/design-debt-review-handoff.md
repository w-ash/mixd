# Handoff: Design-Debt Review — Use-Case-by-Use-Case Fitness Audit

**For**: a review agent starting fresh on this codebase.
**Mission**: the codebase has grown substantially from its first design (v0.1 → v0.8.4, ~70k backend LOC, 69 use cases, 4 connectors' worth of infrastructure, dual CLI+Web presentation). A mid-2026 hygiene pass cleaned the lint-visible debt (suppressions, dead code, duplication detectable by tools). **This assignment is the other audit: design debt.** Walk the codebase *use case by use case* and ask, for each one: what is the user trying to accomplish? What is the system trying to accomplish in service of that? And is the code that accomplishes it modern, understandable, DRY, proportionate, well-named, and maintainable — or has accretion left it over-engineered, misnamed, or tangled?

The bias to hunt is **over-engineering and accretion drift**, not under-engineering: speculative abstractions that never earned their keep, ceremony that re-verifies what the platform already guarantees, names that describe the code's history rather than its function, and paths where a one-sentence user goal takes a five-file journey to satisfy.

**Deliverable**: a findings memo organized *by user flow* (not by file), plus a simplification design-space per hotspot, plus backlog stories. This is a review-and-recommend pass — no refactors. Where a finding implies a big move, decompose it into mechanical steps a future executor can follow one at a time.

---

## 1. Why "use case by use case" is feasible here (your raw materials)

This project documents intent unusually well. Use that — it converts "is this code good?" (taste) into "does this code fit its stated purpose?" (checkable):

| Artifact | Path | What it gives you |
|---|---|---|
| Personas | `docs/personas.md` | Who the user is; anti-personas (what we deliberately don't serve) |
| User flows | `docs/user-flows.md` | `US-AREA-N` flows with Given/When/Then — the ground-truth "what should happen" |
| Version history | `docs/backlog/` + `completed/` | How each feature accreted, version by version — the design's growth rings |
| Layer contracts | `CLAUDE.md` + `.claude/rules/*.md` | The declared ideals (route handlers 5–10 lines, domain purity, Command/Result shape, repetition-is-intentional clauses) |
| Use-case inventory | `src/application/use_cases/` (69 modules) | The system's verb list — each should map to a user goal or a named system obligation |
| API/CLI surfaces | `.claude/skills/api-contracts/SKILL.md` (freshly trued against OpenAPI), `src/interface/cli/` | What's actually exposed, on both presentation layers |

**The core review unit**: pick a flow from user-flows.md → trace it end-to-end (page/CLI command → route/command handler → use case → domain → repositories/connectors) → score it against §3's questions → record findings with file:line evidence.

Coverage expectation: every `US-*` flow gets at least a rapid pass; hotspots (§4) get depth. Use-case modules with no traceable user flow or system obligation are themselves findings ("orphan capability — who asked for this?").

---

## 2. What the hygiene pass already settled (don't re-litigate)

- Typing/idioms are modern (PEP 695-ready, zero `Optional[]`, PEP 649 native, strict pyright, `type: ignore` count is zero). Modernization sweeps are **done** — don't spend time there.
- Tool-visible duplication is measured at ~0.4% (jscpd) — the remaining DRY questions are *structural* (wrong-altitude scaffolds), not copy-paste.
- Dead symbols, dead endpoints, dead scripts: purged; `scripts/check_ratchet.sh` guards the floor.
- Identity resolution has its **own** parallel deep-dive (`docs/backlog/identity-resolution-research-handoff.md`). Coordinate: at the flow-walk level you may traverse matching code, but leave matching-engine design verdicts to that track; hand any findings over rather than duplicating.

---

## 3. The fitness questions (apply to every flow)

**Goal fit**
- State the user goal in one sentence (steal it from user-flows.md). Is the implementation *proportionate* to that sentence? Count the concepts a reader must hold (classes, hops, indirections) vs the concepts in the goal.
- Does anything in the path exist for a future that never arrived? (Check the backlog: was the generality speculative at birth, and did the consumer ever land?)

**Understandability**
- Could a competent newcomer, given only the class/module names, predict where each step lives? Name every point where they'd guess wrong — those are naming or placement findings.
- Do names match what the code does *now*? (Classes named for what they did three versions ago; `*Service`/`*Manager`/`*Coordinator`/`*Provider`/`*Builder` suffixes that don't discriminate; modules whose docstring promises something the code outgrew — the workflows route module's "5-10 lines per handler" claim was one such, found and fixed.)
- Spaghetti markers: one logical step smeared across 3+ files; boolean/mode parameters forking behavior mid-function; functions you can't summarize in a sentence; call chains that bounce between layers more than the architecture requires.

**DRY at the right altitude**
- Same *decision* encoded in two places (worse than duplicated code — they drift independently)?
- Inverse smell: an abstraction with one real consumer, or parameters every caller passes identically — inline candidates. The house rule is explicit: duplication is cheaper than the wrong abstraction; extraction wants 2–3 *real* consumers.
- The rules declare some repetition intentional (`application-patterns.md`: Command/Result repetition across use cases). You may **challenge a rule** if evidence says it's costing more than it buys — but do it explicitly, as a proposed rule change with the evidence, never by silently reviewing against a different standard.

**Maintainability mechanics**
- Shotgun-surgery measurement (do this empirically, not by feel): for the last ~5 shipped features, `git log` the commits and count files touched per user-visible change. Which files appear in *every* feature's diff? Those are the coupling hotspots, whatever the architecture diagram says.
- Change-coupling: which file pairs always change together (`git log --format=%H --name-only` analysis)? Hidden structure lives there.
- Monotonic growers: which modules only ever gain lines? (`git log --numstat` per file) — accretion without consolidation.

**Error and failure paths**
- For each flow: what does the user see when it fails halfway? Is partial failure a designed state or an accident? Silent degradation (empty results, swallowed exceptions, log-only errors) is a finding.

---

## 4. Leads from the hygiene pass (verified observations, design verdicts open)

These surfaced during the 2026-06 audit but were out of its scope to judge. They are *leads*, not conclusions — several may be justified once you read their history:

1. **`update_connector_playlist.py` (~1,000 lines, ~20 methods)** — the diff logic is properly delegated to `domain/playlist/diff_engine.py`, but the module's weight is "persistence-verification ceremony": `_persist_connector_playlist_with_verification`, `_upsert_and_verify_connector_playlist`, `_validate_playlist_pre/post_execution`. Is it re-verifying what transactions/constraints already guarantee? What incident produced it (git archaeology)? Is the ceremony load-bearing or scar tissue?
2. **`sync_likes.py` (~800 lines)** — three use cases + CLI runner wrappers + shared helpers in one module. A split into a `likes/` package was sketched and deferred ("lowest value-per-risk"). The interesting question isn't the split — it's *why* the module accreted this shape and whether the Command/Result-per-use-case pattern is helping or hurting at this size.
3. **The progress subsystem** — `ProgressCoordinator` (domain service), `RichProgressProvider` (CLI), `SSEProgressProvider`/`OperationRegistry` (API), `progress_manager`, emitters, `tracked_operation`. The hygiene pass deleted four test-only methods here — the classic symptom of an API designed ahead of its consumers. How many layers does a progress tick actually traverse, and does each earn its place across both presentation layers?
4. **`MetadataBuilder`** — a builder whose `with_custom` was deleted as test-only and whose `build_dict` has exactly one production caller. Is a builder pattern earning its keep over a plain function/dict?
5. **`base_repo.py` generic machinery** — `SimpleMapperFactory` ("replaces ~30 lines of boilerplate"), reflection-based column access (carries most of the repo layer's remaining pyright-ignores), generic `upsert`/`find_by` with dual-typed `conditions` (the unexecuted DUP-03 finding: `_apply_conditions` extraction). Generic-base-plus-reflection is a classic over-engineering pattern — or a justified one. Which?
6. **Dual threshold systems in `MatchingConfig`** — three-zone (auto-accept/review) *and* "legacy per-method thresholds" coexist (`settings.py:37` says "Legacy"). Half-dead config is design debt even when every field is technically read. (Coordinate with the identity track.)
7. **CLI ↔ Web parity drift** — two presentation layers over one application core is the stated design. Where has one grown capabilities the other lacks, and is each gap a decision or an accident? (The hygiene pass found workflow deletion existed in CLI but its API endpoint had zero consumers — one data point of drift.)
8. **DUP-06** (`playlist_commands.py:793`) — 14 lines of batch-outcome rendering duplicated verbatim across two CLI commands; small in itself, but check whether CLI output assembly generally lacks a home.
9. **69 use cases** — the Command/Result + `execute(command, uow)` uniformity is a declared rule. At this count, sample broadly: how many are substantive orchestrations vs pass-throughs to a single repo call? A high pass-through ratio would suggest the pattern's floor cost is being paid where a thinner idiom would do — that's a rule-change conversation, not a unilateral finding.
10. **Workflow engine altitude** — declarative pipelines, node registry, config-field schemas, execution strategies, level-based parallel DAG execution. It's the most architected subsystem; verify the architecture is pulled by real workflow definitions users run (check the seeded/user workflows) rather than by anticipated ones.

---

## 5. Suggested working order

1. **Calibrate** (half-day equivalent): read `personas.md`, `user-flows.md`, the rules files, and skim two *small, recent, well-regarded* flows end-to-end (e.g., a schedules flow — newest code, post-dates most accretion) to learn what "good" looks like locally. Your fitness bar should be *this codebase at its best*, not an imported ideal.
2. **Flow-walk matrix**: every `US-*` flow → rapid fitness pass → score (fit / minor drift / hotspot) with one-line justification. This matrix is deliverable #1 and keeps coverage explicit and complete.
3. **Hotspot deep dives**: the matrix's hotspots plus §4 leads. Git archaeology per hotspot (when did it get complicated, and what was happening?). Evidence per finding: file:line, the user-goal sentence it fails, and the specific fitness question it flunks.
4. **Cross-cutting passes**: naming audit (one pass over all class/module names against their current function); shotgun-surgery and change-coupling measurements (§3); error-path walk per flow.
5. **Synthesize**: `docs/backlog/design-debt-findings.md` — findings grouped by user flow, each rated (severity × effort × confidence), with a simplification design-space per hotspot (options + trade-offs, decomposed into mechanical steps), explicit "looks over-engineered but is justified" section (if it's empty you didn't look hard enough), and any proposed *rule changes* clearly separated from code findings. File follow-up stories to `docs/backlog/unscheduled.md` in backlog-format.

---

## 6. Guardrails

- **Evidence over taste**: every finding cites code, a measurement, or git history. "I'd have written it differently" is not a finding; "the goal takes 6 hops and 4 of them add no decision" is.
- **History before judgment**: this codebase contains scar tissue from real incidents (Spotify ID churn handling, checkpoint always-write-on-exit, RLS isolation). `git log -S` / `--follow` before calling anything needless.
- **Respect the layer invariants** (Interface → Application → Domain ← Infrastructure; domain pure). Simplifications must fit them; if an invariant itself seems to force ceremony, that's a rule-change proposal, surfaced explicitly.
- **The repetition rules are challengeable but not ignorable** — same protocol: evidence, explicit proposal, user decides.
- **No refactoring in this pass.** Read-only plus the two deliverable docs and backlog entries. Decompose big moves into steps; agents (and humans) execute design changes reliably only as mechanical sequences.
- **Verification plan required per recommendation**: name the existing tests that act as the net (fast suite <90s, 3,180 tests, 80% coverage gate) and the characterization tests that must exist *before* any hotspot refactor where coverage is thin (CLI layer is coverage-omitted — anything touching CLI behavior needs its net built first).

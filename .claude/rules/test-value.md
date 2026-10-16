---
paths:
  - "tests/**"
  - "web/src/**/*.test.ts"
  - "web/src/**/*.test.tsx"
  - "web/e2e/**"
---
# Test Value Rules

Canonical standard. Tooling (`scripts/check_test_vacuity.py`, hooks, audit agents) cites the V-codes and DUP below. Mechanics (fixtures, placement, markers) live in `test-patterns.md`.

## Kill criterion
- A test exists only if a plausible bug in owned code makes it fail, and an intended refactor leaves it green.
- Before you write a test, name the bug it catches. If you cannot, do not write it.
- Name two plausible wrong implementations. The test must fail on both.
- Writing no test is valid when behavior did not change.

## Expected values
- Take expected values from the spec, the user story, or domain knowledge. Never run the code and copy its output.
- Write literals. Do not import constants from the module under test.

## Mocks
- Mock only at boundaries: HTTP connector clients, clock, randomness, the UoW/repositories in use-case unit tests (`make_mock_uow`); connector resolution (`resolve_*_connector`); CLI call-site seams per `cli-patterns.md`.
- Never patch the module under test. Do not mock SQLAlchemy sessions; repositories get integration tests.
- Do not assert on mock return values you configured yourself. That tests the mock setup.
- Assert on outcomes. Assert a call only when the side effect IS the contract (commit happened, push sent these IDs, confirm token required), and assert its arguments.
- Web: mock the network with MSW only. `vi.mock` of an own module needs a comment that gives the reason.

## Banned patterns
| Code | Pattern | Instead |
|------|---------|---------|
| V1 | No assertion; relies on "no exception" | `pytest.raises` for the negative, or assert the resulting state. Exception: not raising IS the stated contract (absorbs an error, idempotent no-op, completes within a timeout); then name the test so and assert the unchanged state where it can be seen |
| V2 | Only mock-call assertions, and the call is not the contract or its arguments are unchecked (bare `assert_called`, `call_count`, `await_count >= 1`) | Assert the result or state; if the call is the contract, assert its exact args |
| V3 | Only existence/type checks: `is not None`, `isinstance`, `hasattr`, `callable`, `len(x) > 0`, `toBeDefined`, `toBeTruthy` | Assert the exact value or content |
| V4 | Construct an object, read its attributes back | Test the behavior that uses those attributes |
| V5 | Language/framework features: frozen raises, enum `.value` equals literal, default field values, dataclass equality | Delete; attrs, enum, and Python are not our code |
| V6 | Expected value from the code under test: imported constant, recomputed with the same function, unreviewed snapshot | Literal from the spec |
| V7 | Patches or mocks the unit under test | Mock the boundary below it |
| V8 | Name promises more than the assertions check | Add the missing assertions or rename to what is checked |
| V9 | Timing/sanity bounds: `execution_time_ms >= 0`, sleep-and-measure | Inject a clock and assert the exact value |
| V10 | Duplicates a contract already tested at a stronger layer | Delete; keep the primary test |
| DUP (checker) | Identical normalized test bodies | Keep one, delete the rest |

- Not V4: mappers, conversions, factories, and derived properties, where the output differs from the input. Reading the output fields IS the behavior.
- Not V5: our own `__eq__`/`__hash__`, attrs validators/converters, computed defaults, and enum values that are persisted or on the wire. Move a persisted value pin to a repository or API round-trip test; do not delete it without the replacement.

## Layer ownership
One primary test per contract, at the strongest layer that owns it:
- Domain transform → domain unit test. The use case test does not retest the transform.
- Use case orchestration → use case test that asserts the outcome.
- Persistence, RLS → repository integration test.
- HTTP contract → API integration test.
- UI → user-visible behavior via React Testing Library.

Add a test at another layer only for a risk the primary test cannot reach.

## Bug fixes: red first
Write the failing test. Run it. See it fail for the right reason. Then fix the code.

## Mutation survivors
Kill every surviving mutant in changed code with a new test, or note it as equivalent in the PR or release notes, with one line on why no test can tell it apart. There is no score threshold. A timeout counts as killed.

## Existing tests
- Never weaken, skip, delete, or loosen an assertion to get green.
- If a test is wrong, say so explicitly in your reply and in the commit message.
- A hook asks for confirmation when a change removes assertions (silent on `test-audit/*` branches, where verifier agents prove each deletion).

## Properties
Prefer Hypothesis (Python) or fast-check (TS; add the dependency with the first property) for pure transforms: matching, normalization, diffing, pagination. Example: `tests/unit/domain/matching/test_play_projection.py`.

## Protected tests
Never deletion candidates in an audit:
- RLS and tenant isolation
- Auth, OAuth, token handling
- Migration and schema gates
- `tests/integration/characterization/`
- Hypothesis properties
- Regression tests tied to a production bug fix: the test fails with that fix's src change reverted. A docstring citing a bug, version, or incident, or arrival in a `fix:` commit, is a lead to check, not proof — sweep commits and commits that only fixed tests do not qualify
- Destructive-operation confirm tokens

## Audit verdicts
- DELETE: V5, V10, DUP.
- REWRITE: V1, V2, V3, V6, V7, V8, V9, when no other test covers the contract. When another test covers it, DELETE and name that test.
- V4: DELETE, unless the behavior that uses the attributes is untested. Then REWRITE to test that behavior.
- A V3/V9 line in a test with other real assertions: fix the line, keep the test.
- Protected tests: REWRITE only, never DELETE.
- Every DELETE names the surviving test that still catches the bug, or states the test was vacuous (V1/V7 with nothing to catch).

## Self-check before you finish
1. What bug does each new test catch?
2. Where did each expected value come from?
3. What is mocked, and is each mock a boundary?
4. Did any existing assertion get weaker?

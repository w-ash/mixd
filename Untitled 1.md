⏺ 🔍 Critical Test Validation Needed - Error Handling System Integrity

  Goal: Ensure tests properly validate our error handling system and all error categories from src/infrastructure/connectors/lastfm/error_classifier.py behave correctly.

  🚨 Primary Investigation: Are Tests Hitting Real APIs?

  Critical Question: Are the LastFM integration test failures due to tests bypassing mocks and hitting real APIs?

  Evidence suggesting this:
  - Systematic "Track not found" responses across multiple tests
  - Class-level patching changes may have broken mock isolation
  - Pattern suggests real API calls failing (since we use test data)

  Immediate Debug:
  # Run with network monitoring to see if real HTTP calls are made
  poetry run pytest tests/integration/connectors/lastfm/test_concurrency_error_handling.py::TestConcurrencyErrorHandling::test_mixed_success_failure_batch -v -s
  --log-cli-level=DEBUG

  Look for: Real network requests, API timeouts, or pylast library HTTP calls instead of mock responses.

  🎯 Core Validation Required

  1. Error Classification Logic Integrity

  Our system has 5 error types that must behave correctly:

  From error_classifier.py:
  - permanent (codes 2,3,4,5,6,7,10,12,13,14,15,17,18,21,22,23,24,25,26,27) → No retries
  - temporary (codes 8,9,11,16,20) → 2-3 retries with exponential backoff
  - rate_limit (code 29 + text patterns) → 2-3 retries with constant delay
  - not_found (text patterns) → No retries, debug logging
  - unknown (fallback) → 2-3 retries with exponential backoff

  Validation needed:
  - Does error code "6" + text "rate limit" correctly return permanent (1 attempt) not rate_limit (3 attempts)?
  - Do all permanent error codes actually stop retrying immediately?
  - Do temporary/unknown errors actually retry 2-3 times with proper backoff timing?

  2. Mock vs Real API Detection

  Critical test: Mock application verification
  # In failing tests, add debugging to see what's actually being called:
  print(f"Mock applied: {LastFMAPIClient._get_comprehensive_track_data}")
  print(f"Instance method: {lastfm_client._get_comprehensive_track_data}")

  If mocks aren't being applied correctly, tests are validating real API behavior not our error handling behavior.

  3. Retry Behavior Validation

  Key question: Do our backoff decorators actually work as intended?

  Tests should verify:
  - Call counts: Permanent errors = 1 call, retriable errors = 3 calls
  - Timing patterns: Exponential vs constant backoff actually implemented
  - Error propagation: Failed retries properly return None vs raise exceptions
  - Concurrent isolation: Multiple concurrent requests with different error types don't interfere

  🔧 Specific Areas to Investigate

  Mock Strategy Issues

  Current class-level patching: patch.object(LastFMAPIClient, '_get_comprehensive_track_data')

  Potential problems:
  - Global mock scope affecting test isolation
  - Mock not being applied in correct context
  - Real API calls leaking through

  Alternative approaches to test:
  1. Module-level patching: Patch where imported, not on class
  2. Instance attribute modification: Make client test-friendly
  3. Dependency injection: Mock at constructor level

  Error Classification Edge Cases

  Test these scenarios specifically:
  - Error code "6" with "rate limit exceeded" text → Should be permanent
  - Error code "29" with any text → Should be rate_limit
  - Unknown error code with "rate limit" text → Should be rate_limit
  - No error code, just "not found" text → Should be not_found

  Integration vs Unit Test Boundaries

  Question: Are integration tests testing too much?

  - Integration tests should test: "Does the full error handling pipeline work?"
  - Unit tests should test: "Does error classification logic work correctly?"

  May need to separate concerns for better test reliability.

  ⚠️ Success Criteria

  1. All tests pass (680/680)
  2. Tests actually validate behavior (not hitting real APIs)
  3. Error categories behave correctly (proper retry counts and timing)
  4. System resilience confirmed (concurrent error handling works)

  🚀 Expected Outcome

  If tests are hitting real APIs → Fix mock application, should see dramatic improvement
  If tests are working correctly → Individual test logic fixes needed
  Either way → Comprehensive error handling validation is the ultimate goal

  The error handling system is critical infrastructure - tests must validate it thoroughly.
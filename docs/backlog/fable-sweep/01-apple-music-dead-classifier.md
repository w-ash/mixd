# 01 — ~~Delete dead AppleMusicErrorClassifier~~ — REJECTED (user decision)

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)).

**Area:** infrastructure · **Suggested executor:** — · **Effort:** — · **ROI:** — · **Risk:** — · **Status:** Rejected (2026-07-01)

## Decision

The audit proposed deleting `src/infrastructure/connectors/apple_music/error_classifier.py` (171 lines, no current callers) as dead code. **The user rejected this on 2026-07-01**: the Apple Music module is deliberate groundwork for the upcoming Apple integration — composability that is core to the system's future. It stays.

**No executing agent may delete or restructure `src/infrastructure/connectors/apple_music/` under this sweep.**

## Informational note for the future Apple integration (not scheduled work)

When the Apple Music integration is built, one observation from the audit may be useful to its author: `AppleMusicErrorClassifier` currently overrides `classify_error()` wholesale (error_classifier.py:26-68), while the other three connectors use the `_classify_service_error()` hook on `HTTPErrorClassifier` (`_shared/error_classifier.py:230-242`) and inherit the shared dispatch cascade. Aligning with the hook at integration time would keep the four classifiers consistent — a call for whoever ships the integration, entirely outside this sweep's scope.

## Audit disposition retained

The rest of the error-classifier family is **healthy, leave alone**: `_shared/error_classifier.py` provides the template + `_classify_service_error` hook; spotify (38 lines), lastfm (88), musicbrainz (46) are exemplary thin subclasses. The seed's "error_classifier ×5 duplication" is already-solved architecture.

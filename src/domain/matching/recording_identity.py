"""Is one description of a recording the same recording as another?

The question every "reuse a canonical instead of minting a second one" gate has
to answer, asked of a pair that no identifier vouches for. An ISRC would settle
it, and where one exists the ISRC path settles it — but a remaster never shares
its original's ISRC, so two rows describing one recording routinely arrive with
no shared identifier at all and only their names and lengths to go on.

One answer, two call sites: the Spotify inward resolver (which asks after the
provider has answered, about the metadata Spotify returned) and
``ingest_external_tracks_bulk`` (which asks about a playlist or likes payload
before creating a canonical for it). A second, subtly-different copy of "are
these the same recording?" is how the two paths drift apart, and drift here is
measured in duplicate canonicals.
"""

from attrs import define

from src.domain.entities.track import Track
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.text_normalization import normalize_for_comparison


@define(frozen=True, slots=True)
class RecordingDescription:
    """One side of the same-recording question, as some source describes it.

    Deliberately just the three fields the question turns on, so a provider
    payload, a connector track and a stored canonical can all be asked about
    on equal terms. ``duration_ms`` is ``None`` when the source did not say.
    """

    title: str
    artist: str
    duration_ms: int | None = None


def describe_track(track: Track) -> RecordingDescription:
    """A stored canonical, as the same-recording question sees it.

    The primary artist only, and unguarded because ``Track`` validates that it
    has one. A canonical's artist list is the credit order the source service
    supplied, and featured-artist credits differ between services for the same
    recording, so comparing beyond the first name would refuse pairs on a
    difference of billing. A blank *name* is still possible and is refused by
    the predicate, not here.
    """
    return RecordingDescription(
        title=track.title,
        artist=track.artists[0].name,
        duration_ms=track.duration_ms,
    )


def identity_key(description: RecordingDescription) -> tuple[str, str]:
    """The normalized (title, artist) pair ``describes_same_recording`` requires.

    Exposed so a caller with many descriptions can bucket them by this key
    instead of comparing every pair: equality on it is a *necessary* condition
    of the predicate below, so descriptions in different buckets can never be
    the same recording. It is not a *sufficient* one — the duration guard still
    decides within a bucket — which is why this is a lookup aid and never a
    second copy of the question.
    """
    return (
        normalize_for_comparison(description.title),
        normalize_for_comparison(description.artist),
    )


def describes_same_recording(a: RecordingDescription, b: RecordingDescription) -> bool:
    """Do two descriptions describe one recording? Two strictnesses decide it.

    **Normalized equality, not similarity.** The candidate that reaches here
    was usually proposed by ``find_tracks_by_title_artist``, which also answers
    on a parenthetical-stripped form — right for *proposing* a candidate, far
    too loose for reusing one unasked. It pairs "Ice Ice Baby (Wunderbros
    Dubstep Remix)" with "Ice Ice Baby", whose lengths sit 5.8s apart, so the
    duration guard would wave that through too. Equality on the normalized
    forms is what keeps a remix from silently becoming its original. A missing
    title or artist on either side is refused outright rather than compared as
    an empty string against another empty string.

    **An unknown duration refuses reuse.** This is where the question parts
    company with the ISRC path, which treats a missing duration as "no
    cross-check available" and lets the ISRC's own assertion stand. Here there
    is no assertion: the durations are the whole of the evidence that two
    same-named rows are one recording, and with them unknown there is nothing
    left to accept on.

    The tolerance itself is ``assess_isrc_match_reliability``'s, on purpose.
    One number decides "remaster or different version" everywhere, and it is
    hashed into the matcher version — a second private constant here would
    silently fall out of that hash.
    """
    if not (a.title and a.artist and b.title and b.artist):
        return False
    if identity_key(a) != identity_key(b):
        return False
    duration_diff_ms = compute_duration_diff_ms(a.duration_ms, b.duration_ms)
    return duration_diff_ms is not None and not (
        assess_isrc_match_reliability(duration_diff_ms).suspect
    )

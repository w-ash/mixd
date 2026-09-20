"""Set-based backfills whose SQL the test suite runs directly.

Integration tests build their schema with ``metadata.create_all`` and never run
the Alembic chain, so a backfill written inline in a migration is untestable.
Each statement lives here as a string the migration executes and the tests
exercise against the ORM-built schema.
"""


def backfill_track_artists_sql() -> str:
    """Expand ``tracks.artists`` JSONB into one ``track_artists`` row per credit.

    The JSONB is ``{"names": [...]}`` and carries nothing else, so only
    ``position`` (from the ordinality, zero-based) and ``credited_name`` are
    filled; join phrases and roles arrive later from MusicBrainz.

    ``artist_id`` is NULL for every credit, including the Various Artists
    sentinel. The sentinel never becomes an artist — it is an album-level flag,
    not a favouritable entity — and minting canonical artists for the rest is
    the import's job, not the backfill's.

    ``ON CONFLICT (track_id, position) DO NOTHING`` makes re-running converge:
    the credit at a given position is already there or it is not.
    """
    return """
        INSERT INTO track_artists (
            id, user_id, track_id, artist_id, position, credited_name,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), t.user_id, t.id, NULL, a.ord - 1, a.name,
               now(), now()
        FROM tracks t,
             jsonb_array_elements_text(t.artists->'names')
                 WITH ORDINALITY AS a(name, ord)
        ON CONFLICT (track_id, position) DO NOTHING
    """

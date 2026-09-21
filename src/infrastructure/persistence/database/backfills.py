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


def backfill_connector_artists_sql() -> str:
    """Mint the ``connector_artists`` rows the stored payloads' ``artists`` dumps name.

    A Spotify payload dumps its ``artists`` as ``[{"id", "name", ...}]``; every
    distinct ``(connector, id)`` with a name becomes one record whose
    ``raw_metadata`` is that dump. A payload whose ``artists`` is not an array
    of objects with ids (Apple, Last.fm, Tidal, MusicBrainz) mints nothing.

    ``ON CONFLICT DO NOTHING`` on the identity key: a record the import
    already wrote keeps its own payload, and re-running converges.
    """
    return """
        INSERT INTO connector_artists (
            id, connector_name, connector_artist_identifier, name, raw_metadata,
            last_updated, created_at, updated_at
        )
        SELECT DISTINCT ON (ct.connector_name, a.item->>'id')
               gen_random_uuid(), ct.connector_name, a.item->>'id',
               a.item->>'name', a.item, now(), now(), now()
        FROM connector_tracks ct,
             jsonb_array_elements(
                 CASE WHEN jsonb_typeof(ct.raw_metadata->'artists') = 'array'
                      THEN ct.raw_metadata->'artists' ELSE '[]'::jsonb END
             ) AS a(item)
        WHERE jsonb_typeof(a.item) = 'object'
          AND a.item->>'id' IS NOT NULL
          AND a.item->>'name' IS NOT NULL
        ON CONFLICT (connector_name, connector_artist_identifier) DO NOTHING
    """


def backfill_connector_track_artists_sql() -> str:
    """Expand ``connector_tracks.artists`` JSONB into ``connector_track_artists`` rows.

    One row per name in ``{"names": [...]}``, ``position`` from the
    ordinality. ``connector_artist_id`` joins to the record
    :func:`backfill_connector_artists_sql` minted from the ``artists`` dump at
    the same position, and is NULL where the dump has no id there. Join
    phrases and roles are not in the JSONB; the next import writes them.

    ``ON CONFLICT (connector_track_id, position) DO NOTHING`` makes re-running
    converge, and leaves a credit the import already wrote untouched.
    """
    return """
        INSERT INTO connector_track_artists (
            id, connector_track_id, connector_artist_id, position, credited_name,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), ct.id, ca.id, a.ord - 1, a.name, now(), now()
        FROM connector_tracks ct
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(ct.artists->'names') = 'array'
                 THEN ct.artists->'names' ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS a(name, ord)
        LEFT JOIN connector_artists ca
          ON ca.connector_name = ct.connector_name
         AND ca.connector_artist_identifier =
             ct.raw_metadata->'artists'->(a.ord::int - 1)->>'id'
        ON CONFLICT (connector_track_id, position) DO NOTHING
    """

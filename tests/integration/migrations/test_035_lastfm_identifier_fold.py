"""End-to-end tests for migration 035 (Last.fm identifier fold).

Drives the *real* Alembic ``upgrade``/``downgrade`` against throwaway Postgres
containers (schema owned by the migration chain, not ``create_all``), because
the fold's set-based row surgery — survivor election, mapping-conflict
tiebreak, primary re-assertion, review merge, raw_metadata provenance, and the
RLS ``NO FORCE`` bracket — lives entirely in SQL the integration harness cannot
reach.

Two scenarios:
- ``test_035_folds_variants_and_preserves_invariants`` seeds every identifier
  variant (URL / case / mbid / ``lastfm:`` / already-normalized) across three
  survivor-selection rules and asserts the collapsed state.
- ``test_035_fold_runs_under_non_superuser_owner_role`` re-runs the fold as a
  freshly-created NON-superuser table owner. The container superuser bypasses
  RLS and would mask a missing bracket; a non-bypass owner would see zero
  track_mappings/match_reviews under FORCE RLS and cascade-delete them with the
  loser — so this proves the ``NO FORCE`` bracket is load-bearing.

Marked ``slow``: each spins a dedicated container and runs the chain to 034.
"""

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import uuid

from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from alembic import command

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE = "034_track_mappings_last_seen_at"  # revision just before 035
_HEAD = "035_lastfm_identifier_fold"

_NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.slow


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch):
    """A throwaway Postgres whose schema is owned by Alembic, not ``create_all``."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2://", "psycopg://")
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


# --------------------------------------------------------------------------- #
# Seed helpers (raw SQL — the container superuser bypasses RLS while seeding).  #
# --------------------------------------------------------------------------- #
def _insert_track(conn: sa.Connection, tid: uuid.UUID, user_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, :uid, :title, CAST(:artists AS JSONB), 1, :now, :now)"
        ),
        {
            "id": tid,
            "uid": user_id,
            "title": f"track-{tid.hex[:6]}",
            "artists": json.dumps({"names": ["Someone"]}),
            "now": _NOW,
        },
    )


def _insert_ct(
    conn: sa.Connection,
    cid: uuid.UUID,
    identifier: str,
    artist: str,
    title: str,
    raw_metadata: dict[str, object],
    created_at: datetime,
) -> None:
    names = [artist] if artist else []
    conn.execute(
        sa.text(
            "INSERT INTO connector_tracks (id, connector_name, "
            "connector_track_identifier, title, artists, raw_metadata, "
            "last_updated, created_at, updated_at) VALUES "
            "(:id, 'lastfm', :ident, :title, CAST(:artists AS JSONB), "
            "CAST(:raw AS JSONB), :now, :created, :now)"
        ),
        {
            "id": cid,
            "ident": identifier,
            "title": title,
            "artists": json.dumps({"names": names}),
            "raw": json.dumps(raw_metadata),
            "now": _NOW,
            "created": created_at,
        },
    )


def _insert_mapping(
    conn: sa.Connection,
    mid: uuid.UUID,
    user_id: str,
    track_id: uuid.UUID,
    connector_track_id: uuid.UUID,
    *,
    is_primary: bool,
    origin: str,
    confidence: int,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO track_mappings (id, user_id, track_id, "
            "connector_track_id, connector_name, match_method, confidence, "
            "origin, is_primary, created_at, updated_at) VALUES "
            "(:id, :uid, :tid, :ctid, 'lastfm', 'seed', :conf, :origin, "
            ":primary, :now, :now)"
        ),
        {
            "id": mid,
            "uid": user_id,
            "tid": track_id,
            "ctid": connector_track_id,
            "conf": confidence,
            "origin": origin,
            "primary": is_primary,
            "now": _NOW,
        },
    )


def _insert_review(
    conn: sa.Connection,
    rid: uuid.UUID,
    user_id: str,
    track_id: uuid.UUID,
    connector_track_id: uuid.UUID,
    *,
    match_weight: float,
    created_at: datetime,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO match_reviews (id, user_id, track_id, "
            "connector_track_id, connector_name, match_method, confidence, "
            "match_weight, status, created_at, updated_at) VALUES "
            "(:id, :uid, :tid, :ctid, 'lastfm', 'seed', 60, :weight, "
            "'pending', :created, :now)"
        ),
        {
            "id": rid,
            "uid": user_id,
            "tid": track_id,
            "ctid": connector_track_id,
            "weight": match_weight,
            "created": created_at,
            "now": _NOW,
        },
    )


def _insert_play(
    conn: sa.Connection,
    pid: uuid.UUID,
    user_id: str,
    identifier: str,
    resolved_track_id: uuid.UUID,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO connector_plays (id, user_id, connector_name, "
            "connector_track_identifier, played_at, ms_played, raw_metadata, "
            "resolved_track_id, resolved_at, created_at, updated_at) VALUES "
            "(:id, :uid, 'lastfm', :ident, :now, 200000, CAST('{}' AS JSONB), "
            ":rtid, :now, :now, :now)"
        ),
        {
            "id": pid,
            "uid": user_id,
            "ident": identifier,
            "rtid": resolved_track_id,
            "now": _NOW,
        },
    )


def _insert_playlist(conn: sa.Connection, pid: uuid.UUID, user_id: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO playlists (id, user_id, name, track_count, "
            "created_at, updated_at) VALUES (:id, :uid, 'P', 0, :now, :now)"
        ),
        {"id": pid, "uid": user_id, "now": _NOW},
    )


def _insert_playlist_track(
    conn: sa.Connection,
    ptid: uuid.UUID,
    playlist_id: uuid.UUID,
    track_id: uuid.UUID,
    connector_track_id: uuid.UUID,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO playlist_tracks (id, playlist_id, track_id, "
            "connector_track_id, sort_key, created_at, updated_at) VALUES "
            "(:id, :plid, :tid, :ctid, 'a0', :now, :now)"
        ),
        {
            "id": ptid,
            "plid": playlist_id,
            "tid": track_id,
            "ctid": connector_track_id,
            "now": _NOW,
        },
    )


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def test_035_folds_variants_and_preserves_invariants(migration_db: str) -> None:
    cfg = _alembic_config()
    engine = sa.create_engine(migration_db)

    # Identity registry for cross-referencing seed rows in assertions.
    tracks = {name: _uid() for name in ("T1", "T2", "T3", "T4", "T5")}
    # Group A — one target, five identifier variants; survivor = already-normalized.
    a = {name: _uid() for name in ("url", "case", "mbid", "prefix", "norm")}
    a_idents = {
        "url": "https://www.last.fm/music/The+Beatles/_/Hey+Jude",
        "case": "The Beatles::Hey Jude",
        "mbid": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
        "prefix": "lastfm:the beatles::hey jude",
        "norm": "the beatles::hey jude",  # == target → survivor
    }
    a_target = "the beatles::hey jude"
    # Group B — most-mappings survivor (b2 is newer yet wins on mapping count).
    b = {"b1": _uid(), "b2": _uid()}
    b_target = "radiohead::creep"
    # Group C — oldest-created_at survivor (c1 is older, both zero mappings).
    c = {"c1": _uid(), "c2": _uid()}
    c_target = "daft punk::one more time"
    empty_ct = _uid()

    m = {name: _uid() for name in ("norm", "case", "mbid", "s", "l", "b2")}
    r = {name: _uid() for name in ("solo", "conflict_loser", "conflict_survivor")}
    play_id = _uid()
    playlist_id = _uid()
    pt_id = _uid()

    try:
        command.upgrade(cfg, _PRE)

        with engine.begin() as conn:
            for tid in tracks.values():
                _insert_track(conn, tid, "u1")

            # --- Group A rows (all fold to a_target) ---
            _insert_ct(
                conn,
                a["url"],
                a_idents["url"],
                "The Beatles",
                "Hey Jude",
                {"src": "url", "lastfm_mbid": "keep-loser"},
                _NOW,
            )
            _insert_ct(
                conn,
                a["case"],
                a_idents["case"],
                "The Beatles",
                "Hey Jude",
                {"src": "case"},
                _NOW,
            )
            _insert_ct(
                conn,
                a["mbid"],
                a_idents["mbid"],
                "The Beatles",
                "Hey Jude",
                {"src": "mbid"},
                _NOW,
            )
            _insert_ct(
                conn,
                a["prefix"],
                a_idents["prefix"],
                "The Beatles",
                "Hey Jude",
                {"src": "prefix"},
                _NOW,
            )
            _insert_ct(
                conn,
                a["norm"],
                a_idents["norm"],
                "The Beatles",
                "Hey Jude",
                {"src": "norm", "survivor_key": "kept"},
                _NOW,
            )

            # Mappings on Group A.
            # u1/T1: survivor's own primary + a losing duplicate (non-primary).
            _insert_mapping(
                conn,
                m["norm"],
                "u1",
                tracks["T1"],
                a["norm"],
                is_primary=True,
                origin="automatic",
                confidence=90,
            )
            _insert_mapping(
                conn,
                m["case"],
                "u1",
                tracks["T1"],
                a["case"],
                is_primary=False,
                origin="automatic",
                confidence=80,
            )
            # u2/T1: manual_override on a loser — moves intact, stays primary.
            _insert_mapping(
                conn,
                m["mbid"],
                "u2",
                tracks["T1"],
                a["mbid"],
                is_primary=True,
                origin="manual_override",
                confidence=70,
            )
            # u4: survivor primary (T5, automatic) vs loser manual_override (T4).
            # manual_override wins the slot, deletes the primary → re-assertion
            # must promote the surviving manual_override on T4.
            _insert_mapping(
                conn,
                m["s"],
                "u4",
                tracks["T5"],
                a["norm"],
                is_primary=True,
                origin="automatic",
                confidence=95,
            )
            _insert_mapping(
                conn,
                m["l"],
                "u4",
                tracks["T4"],
                a["url"],
                is_primary=False,
                origin="manual_override",
                confidence=50,
            )

            # Reviews on Group A.
            # solo review on a loser → moves to survivor unopposed.
            _insert_review(
                conn,
                r["solo"],
                "u1",
                tracks["T2"],
                a["mbid"],
                match_weight=0.5,
                created_at=_NOW,
            )
            # conflicting reviews for (u1, T3): loser is OLDER → kept.
            _insert_review(
                conn,
                r["conflict_loser"],
                "u1",
                tracks["T3"],
                a["case"],
                match_weight=0.1,
                created_at=_NOW - timedelta(days=10),
            )
            _insert_review(
                conn,
                r["conflict_survivor"],
                "u1",
                tracks["T3"],
                a["norm"],
                match_weight=0.9,
                created_at=_NOW - timedelta(days=1),
            )

            # connector_plays keyed on the normalized identifier — must be untouched.
            _insert_play(conn, play_id, "u1", a_target, tracks["T1"])

            # playlist_track pointing at a loser — repointed to survivor (not nulled).
            _insert_playlist(conn, playlist_id, "u1")
            _insert_playlist_track(conn, pt_id, playlist_id, tracks["T1"], a["url"])

            # --- Group B: most-mappings survivor ---
            _insert_ct(
                conn,
                b["b1"],
                "https://www.last.fm/music/Radiohead/_/Creep",
                "Radiohead",
                "Creep",
                {"g": "b1"},
                _NOW - timedelta(days=10),
            )
            _insert_ct(
                conn,
                b["b2"],
                "RADIOHEAD::CREEP",
                "Radiohead",
                "Creep",
                {"g": "b2"},
                _NOW - timedelta(days=1),
            )
            _insert_mapping(
                conn,
                m["b2"],
                "u3",
                tracks["T1"],
                b["b2"],
                is_primary=True,
                origin="automatic",
                confidence=88,
            )

            # --- Group C: oldest-created_at survivor ---
            _insert_ct(
                conn,
                c["c1"],
                "lastfm:daft punk::one more time",
                "Daft Punk",
                "One More Time",
                {"g": "c1"},
                _NOW - timedelta(days=10),
            )
            _insert_ct(
                conn,
                c["c2"],
                "DAFT PUNK::ONE MORE TIME",
                "Daft Punk",
                "One More Time",
                {"g": "c2"},
                _NOW - timedelta(days=1),
            )

            # --- Unfoldable: blank artist + blank title ---
            _insert_ct(conn, empty_ct, "junk-empty-row", "", "", {"g": "empty"}, _NOW)

        # Apply the fold.
        command.upgrade(cfg, _HEAD)

        with engine.connect() as conn:
            _assert_group_a(conn, a, a_idents, a_target, tracks, m, r)
            _assert_groups_b_c(conn, b, b_target, c, c_target)
            _assert_unfoldable(conn, empty_ct)
            _assert_plays_and_playlist(conn, play_id, a_target, tracks, pt_id, a)
            _assert_single_primary_invariant(conn)

        # Downgrade is a no-op: the folded state must be unchanged.
        command.downgrade(cfg, _PRE)
        with engine.connect() as conn:
            survivors = conn.execute(
                sa.text(
                    "SELECT count(*) FROM connector_tracks WHERE connector_name='lastfm'"
                )
            ).scalar_one()
            assert survivors == 4  # A, B, C survivors + the unfoldable row
            ff = conn.execute(
                sa.text(
                    "SELECT raw_metadata->'folded_from' FROM connector_tracks "
                    "WHERE connector_track_identifier = :t"
                ),
                {"t": a_target},
            ).scalar_one()
            assert isinstance(ff, list)
            assert len(ff) == 4
    finally:
        engine.dispose()


def _assert_group_a(conn, a, a_idents, a_target, tracks, m, r) -> None:
    # Exactly one survivor, keyed on the normalized target, and it is CT_norm.
    rows = (
        conn
        .execute(
            sa.text(
                "SELECT id, raw_metadata FROM connector_tracks "
                "WHERE connector_track_identifier = :t"
            ),
            {"t": a_target},
        )
        .mappings()
        .all()
    )
    assert len(rows) == 1
    survivor = rows[0]
    assert survivor["id"] == a["norm"]
    # Survivor's own raw_metadata wins the shallow merge.
    assert survivor["raw_metadata"]["survivor_key"] == "kept"
    assert survivor["raw_metadata"]["src"] == "norm"
    # folded_from records every loser identifier.
    assert set(survivor["raw_metadata"]["folded_from"]) == {
        a_idents["url"],
        a_idents["case"],
        a_idents["mbid"],
        a_idents["prefix"],
    }
    # All four loser rows are gone.
    for name in ("url", "case", "mbid", "prefix"):
        gone = conn.execute(
            sa.text("SELECT count(*) FROM connector_tracks WHERE id = :id"),
            {"id": a[name]},
        ).scalar_one()
        assert gone == 0, name

    # Mappings collapsed onto the survivor.
    maps = (
        conn
        .execute(
            sa.text(
                "SELECT id, user_id, track_id, is_primary, origin FROM track_mappings "
                "WHERE connector_track_id = :sid ORDER BY user_id"
            ),
            {"sid": a["norm"]},
        )
        .mappings()
        .all()
    )
    by_user = {row["user_id"]: row for row in maps}
    # u1: survivor primary kept; the conflicting duplicate (m_case) deleted.
    assert by_user["u1"]["id"] == m["norm"]
    assert by_user["u1"]["is_primary"] is True
    assert by_user["u1"]["track_id"] == tracks["T1"]
    assert _count(conn, "track_mappings", m["case"]) == 0
    # u2: manual_override preserved, still primary.
    assert by_user["u2"]["id"] == m["mbid"]
    assert by_user["u2"]["origin"] == "manual_override"
    assert by_user["u2"]["is_primary"] is True
    # u4: manual_override loser (m_l → T4) won the slot; automatic primary
    # (m_s → T5) deleted; re-assertion promoted m_l to primary.
    assert by_user["u4"]["id"] == m["l"]
    assert by_user["u4"]["track_id"] == tracks["T4"]
    assert by_user["u4"]["origin"] == "manual_override"
    assert by_user["u4"]["is_primary"] is True
    assert _count(conn, "track_mappings", m["s"]) == 0

    # Reviews collapsed onto the survivor.
    # solo review moved.
    solo = conn.execute(
        sa.text("SELECT connector_track_id FROM match_reviews WHERE id = :id"),
        {"id": r["solo"]},
    ).scalar_one()
    assert solo == a["norm"]
    # conflict resolved keeping the EARLIER row (conflict_loser); survivor row deleted.
    t3_reviews = (
        conn
        .execute(
            sa.text(
                "SELECT id, match_weight FROM match_reviews "
                "WHERE user_id='u1' AND track_id = :tid AND connector_name='lastfm'"
            ),
            {"tid": tracks["T3"]},
        )
        .mappings()
        .all()
    )
    assert len(t3_reviews) == 1
    assert t3_reviews[0]["id"] == r["conflict_loser"]
    assert t3_reviews[0]["match_weight"] == pytest.approx(0.1)
    assert _count(conn, "match_reviews", r["conflict_survivor"]) == 0


def _assert_groups_b_c(conn, b, b_target, c, c_target) -> None:
    # Group B: b2 (more mappings) survives and is renamed; b1 folded away.
    b_rows = (
        conn
        .execute(
            sa.text(
                "SELECT id, raw_metadata FROM connector_tracks "
                "WHERE connector_track_identifier = :t"
            ),
            {"t": b_target},
        )
        .mappings()
        .all()
    )
    assert len(b_rows) == 1
    assert b_rows[0]["id"] == b["b2"]
    assert set(b_rows[0]["raw_metadata"]["folded_from"]) == {
        "https://www.last.fm/music/Radiohead/_/Creep"
    }
    assert _count(conn, "connector_tracks", b["b1"]) == 0
    # b2's mapping survived the rename.
    assert (
        conn.execute(
            sa.text(
                "SELECT count(*) FROM track_mappings WHERE connector_track_id = :id"
            ),
            {"id": b["b2"]},
        ).scalar_one()
        == 1
    )

    # Group C: c1 (oldest) survives and is renamed; c2 folded away.
    c_rows = (
        conn
        .execute(
            sa.text(
                "SELECT id FROM connector_tracks WHERE connector_track_identifier = :t"
            ),
            {"t": c_target},
        )
        .scalars()
        .all()
    )
    assert c_rows == [c["c1"]]
    assert _count(conn, "connector_tracks", c["c2"]) == 0


def _assert_unfoldable(conn, empty_ct) -> None:
    # Blank-artist/title row is left completely untouched.
    row = conn.execute(
        sa.text(
            "SELECT connector_track_identifier FROM connector_tracks WHERE id = :id"
        ),
        {"id": empty_ct},
    ).scalar_one()
    assert row == "junk-empty-row"


def _assert_plays_and_playlist(conn, play_id, a_target, tracks, pt_id, a) -> None:
    # connector_plays untouched: same identifier, still resolvable.
    play = (
        conn
        .execute(
            sa.text(
                "SELECT connector_track_identifier, resolved_track_id "
                "FROM connector_plays WHERE id = :id"
            ),
            {"id": play_id},
        )
        .mappings()
        .one()
    )
    assert play["connector_track_identifier"] == a_target
    assert play["resolved_track_id"] == tracks["T1"]

    # playlist_track repointed from the loser to the survivor (not nulled).
    ct_id = conn.execute(
        sa.text("SELECT connector_track_id FROM playlist_tracks WHERE id = :id"),
        {"id": pt_id},
    ).scalar_one()
    assert ct_id == a["norm"]


def _assert_single_primary_invariant(conn) -> None:
    # No (user, track, 'lastfm') triple may hold more than one primary.
    over = conn.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT user_id, track_id, count(*) FILTER (WHERE is_primary) AS p "
            "  FROM track_mappings WHERE connector_name='lastfm' "
            "  GROUP BY user_id, track_id"
            ") s WHERE s.p > 1"
        )
    ).scalar_one()
    assert over == 0


# Whitelisted, fully-literal count queries — no identifier interpolation.
_COUNT_BY_ID = {
    "connector_tracks": "SELECT count(*) FROM connector_tracks WHERE id = :v",
    "track_mappings": "SELECT count(*) FROM track_mappings WHERE id = :v",
    "match_reviews": "SELECT count(*) FROM match_reviews WHERE id = :v",
}


def _count(conn, table: str, value: uuid.UUID) -> int:
    return conn.execute(sa.text(_COUNT_BY_ID[table]), {"v": value}).scalar_one()


def test_035_fold_runs_under_non_superuser_owner_role(migration_db: str) -> None:
    """The fold must move RLS-protected rows under a non-bypass table owner.

    Without the ``NO FORCE`` bracket, a non-superuser owner sees zero
    track_mappings/match_reviews (FORCE RLS + unset ``app.user_id``); the loser
    connector_track delete then cascade-drops those rows. This asserts they
    survive on the survivor, so the bracket is proven load-bearing.
    """
    cfg = _alembic_config()
    engine = sa.create_engine(migration_db)

    survivor = _uid()
    loser = _uid()
    track_id = _uid()
    mapping_id = _uid()
    review_id = _uid()
    target = "queen::bohemian rhapsody"

    try:
        command.upgrade(cfg, _PRE)

        with engine.begin() as conn:
            _insert_track(conn, track_id, "u1")
            _insert_ct(
                conn,
                survivor,
                target,
                "Queen",
                "Bohemian Rhapsody",
                {"src": "norm"},
                _NOW,
            )
            _insert_ct(
                conn,
                loser,
                "QUEEN::BOHEMIAN RHAPSODY",
                "Queen",
                "Bohemian Rhapsody",
                {"src": "loser"},
                _NOW,
            )
            _insert_mapping(
                conn,
                mapping_id,
                "u1",
                track_id,
                loser,
                is_primary=True,
                origin="manual_override",
                confidence=77,
            )
            _insert_review(
                conn,
                review_id,
                "u1",
                track_id,
                loser,
                match_weight=0.4,
                created_at=_NOW,
            )

            # Create a NON-superuser, NON-bypassrls role and hand it ownership of
            # the two RLS tables (needed to ALTER their FORCE state) plus full
            # privileges elsewhere.
            conn.execute(sa.text("DROP ROLE IF EXISTS mig_owner"))
            conn.execute(
                sa.text(
                    "CREATE ROLE mig_owner LOGIN PASSWORD 'mig_pw' "
                    "NOSUPERUSER NOBYPASSRLS"
                )
            )
            conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO mig_owner"))
            conn.execute(
                sa.text("GRANT ALL ON ALL TABLES IN SCHEMA public TO mig_owner")
            )
            conn.execute(
                sa.text("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO mig_owner")
            )
            for table in ("track_mappings", "match_reviews"):
                conn.execute(sa.text(f"ALTER TABLE {table} OWNER TO mig_owner"))

        # Run the fold connected AS the non-superuser owner (env.py binds its
        # engine from DATABASE_URL). render_as_string(hide_password=False): plain
        # str(url) masks the password as "***" and would fail scram auth.
        role_url = (
            make_url(migration_db)
            .set(username="mig_owner", password="mig_pw")
            .render_as_string(hide_password=False)
        )
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = role_url
        try:
            command.upgrade(cfg, _HEAD)
        finally:
            if prior is not None:
                os.environ["DATABASE_URL"] = prior

        # Assert as the container superuser (bypasses RLS to see the truth).
        with engine.connect() as conn:
            # Loser collapsed into the single survivor.
            assert _count(conn, "connector_tracks", loser) == 0
            assert _count(conn, "connector_tracks", survivor) == 1
            # The mapping was MOVED (not cascade-dropped): bracket worked.
            moved = (
                conn
                .execute(
                    sa.text(
                        "SELECT connector_track_id, origin FROM track_mappings "
                        "WHERE id = :id"
                    ),
                    {"id": mapping_id},
                )
                .mappings()
                .one()
            )
            assert moved["connector_track_id"] == survivor
            assert moved["origin"] == "manual_override"
            # The review was moved too.
            review_ct = conn.execute(
                sa.text("SELECT connector_track_id FROM match_reviews WHERE id = :id"),
                {"id": review_id},
            ).scalar_one()
            assert review_ct == survivor
            # RLS FORCE restored on exit.
            forced = conn.execute(
                sa.text(
                    "SELECT bool_and(relforcerowsecurity) FROM pg_class "
                    "WHERE relname IN ('track_mappings','match_reviews')"
                )
            ).scalar_one()
            assert forced is True
    finally:
        # The container is ephemeral (torn down by the fixture), so no role
        # cleanup is needed — just release the assertion engine.
        engine.dispose()

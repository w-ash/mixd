"""System prompt composition — block layout, cache discipline, and content.

Block A must be the only cached block and must contain nothing volatile;
per-user stats, the date, and the current-workflow context trail it uncached.
The token-floor test is a chars/3.5 heuristic guard — the authoritative check
is ``usage.cache_read_input_tokens`` in the live smoke.
"""

from datetime import date
import json

from src.application.chat.system_prompt import build_system_prompt
from src.application.tools.registry import build_tools
from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
from tests.fixtures import make_workflow

_TODAY = date(2026, 7, 11)

_STATS = DashboardStatsResult(
    total_tracks=4321,
    total_plays=98765,
    total_playlists=12,
    total_liked=567,
    tracks_by_connector={"spotify": 4000, "lastfm": 3200},
    liked_by_connector={"spotify": 567},
    plays_by_connector={"lastfm": 98765},
    playlists_by_connector={"spotify": 12},
    preference_counts={"star": 40, "yah": 200, "nah": 15},
)

_REQUIRED_SECTIONS = (
    "<identity>",
    "<scope>",
    "<domain_model>",
    "<node_catalog>",
    "<tool_habits>",
    "<mutation_rules>",
    "<untrusted_content>",
    # Opus 5 narrates more, expands scope, and over-narrates its own
    # corrections without these; the tail reminder pairs with the conciseness
    # instruction in <response_format> on a prompt this long.
    "<communication>",
    "<task_scope>",
    "<corrections>",
    "<response_format>",
    "<tone_preference>",
)


def _texts(blocks: list[dict[str, object]]) -> list[str]:
    return [str(b["text"]) for b in blocks]


class TestBlockLayout:
    def test_primer_contains_all_required_sections(self):
        blocks = build_system_prompt(_STATS, None, _TODAY)
        primer = _texts(blocks)[0]
        for section in _REQUIRED_SECTIONS:
            assert section in primer

    def test_cache_control_on_first_block_only(self):
        blocks = build_system_prompt(_STATS, make_workflow(), _TODAY)
        assert blocks[0].get("cache_control") == {"type": "ephemeral"}
        assert all("cache_control" not in b for b in blocks[1:])

    def test_volatile_values_stay_out_of_cached_block(self):
        blocks = build_system_prompt(_STATS, None, _TODAY)
        primer = _texts(blocks)[0]
        assert _TODAY.isoformat() not in primer
        assert "4321" not in primer

    def test_current_workflow_block_present_only_when_passed(self):
        # The primer *mentions* <current_workflow> in its mutation rules, so
        # probe block structure: no third block without a workflow, and the
        # third block IS the workflow context when one is passed.
        without = build_system_prompt(_STATS, None, _TODAY)
        assert len(without) == 2

        workflow = make_workflow()
        with_wf = build_system_prompt(_STATS, workflow, _TODAY)
        assert len(with_wf) == 3
        block = _texts(with_wf)[-1]
        assert block.startswith("<current_workflow>")
        assert str(workflow.id) in block
        assert workflow.definition.name in block


class TestUserContext:
    def test_stats_rendered(self):
        blocks = build_system_prompt(_STATS, None, _TODAY)
        context = _texts(blocks)[1]
        assert _TODAY.isoformat() in context
        assert "4321 tracks" in context
        assert "spotify: 4000" in context
        assert "star: 40" in context
        # Absent states render as zero rather than disappearing.
        assert "hmm: 0" in context

    def test_missing_stats_degrade_gracefully(self):
        blocks = build_system_prompt(None, None, _TODAY)
        context = _texts(blocks)[1]
        assert "unavailable" in context
        assert _TODAY.isoformat() in context


class TestNodeCatalog:
    def test_every_registered_node_listed(self):
        from src.application.workflows.nodes.registry import list_nodes

        primer = _texts(build_system_prompt(None, None, _TODAY))[0]
        for node_id in list_nodes():
            assert node_id in primer

    def test_current_workflow_definition_is_valid_json(self):
        workflow = make_workflow()
        block = _texts(build_system_prompt(None, workflow, _TODAY))[-1]
        payload = block.split("definition: ", 1)[1].rsplit("\n</current_workflow>", 1)[
            0
        ]
        parsed = json.loads(payload)
        assert parsed["name"] == workflow.definition.name
        assert parsed["tasks"][0]["type"] == "source.liked_tracks"


# Cache-activation minimums, in tokens, for the models this app can run.
# Opus 5 is the shipped default; Sonnet 5 is the documented CHAT__MODEL_ID
# override and has the higher floor. (Opus 4.8 was also 1024; the 4096 figure
# a previous revision of this file cited belongs to Opus 4.6/4.5 and Haiku 4.5.)
_CACHE_MIN_DEFAULT_MODEL = 512
_CACHE_MIN_OVERRIDE_MODEL = 1024


class TestCatalogIsSelfSufficient:
    def test_every_select_field_inlines_its_options(self):
        """No catalog line falls back to a bare ``select``.

        The catalog is the reason describe_node is not a mandatory pre-call:
        if a select field's options are suppressed, the model has to make a
        round-trip to learn them, and the deleted instruction earns its place
        back. metric_name (7 options) is the field that forced the old limit.
        """
        primer = _texts(build_system_prompt(None, None, _TODAY))[0]
        catalog = primer.split("<node_catalog>")[1].split("</node_catalog>")[0]
        assert " (select" not in catalog


class TestCacheFloor:
    """Each breakpoint's own prefix must clear the activation floor.

    Caching is a prefix match over ``tools`` -> ``system`` -> ``messages``, so
    every breakpoint caches everything before it, and each one has a different
    prefix length. Measuring the whole tool list against one floor tests the
    wrong thing: it reports the *system* breakpoint's prefix and says nothing
    about the tools breakpoint, which is an order of magnitude smaller and the
    only one anywhere near a floor.

    chars/3.5 is a heuristic and conservative for dense JSON (real tool schemas
    tokenize nearer 3 chars/token), so these under-report. The authoritative
    check remains ``usage.cache_read_input_tokens`` in the live smoke.
    """

    def test_tools_prefix_breakpoint_clears_the_floor(self):
        """The first breakpoint caches only tools[0..stamp] — the tight one.

        At ~1.1k estimated tokens this clears Opus 5's 512 comfortably but sits
        only marginally above Sonnet 5's 1024, so it is asserted against the
        default model's floor. If the non-deferred hot set ever shrinks, this
        is the segment that stops caching first — on the override model before
        the default one.
        """
        tools = build_tools()
        stamp = next(i for i, t in enumerate(tools) if "cache_control" in t)
        estimated_tokens = len(json.dumps(tools[: stamp + 1])) / 3.5
        assert estimated_tokens >= _CACHE_MIN_DEFAULT_MODEL * 1.2

    def test_system_block_breakpoint_clears_the_floor(self):
        """Block A's breakpoint caches every tool plus the primer.

        Asserted against the higher (override-model) floor because it has room
        to spare — this is what lets Phase-1 prompt trimming proceed without
        silently disabling the system-block cache.
        """
        primer = _texts(build_system_prompt(None, None, _TODAY))[0]
        prefix_chars = len(json.dumps(build_tools())) + len(primer)
        assert prefix_chars / 3.5 >= _CACHE_MIN_OVERRIDE_MODEL * 1.2

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
    "<response_format>",
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
        without = build_system_prompt(_STATS, None, _TODAY)
        assert not any("<current_workflow>" in t for t in _texts(without))

        workflow = make_workflow()
        with_wf = build_system_prompt(_STATS, workflow, _TODAY)
        block = _texts(with_wf)[-1]
        assert "<current_workflow>" in block
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


class TestCacheFloor:
    def test_cached_prefix_clears_activation_floor_with_margin(self):
        """chars/3.5 heuristic: tools + primer must clear 4096 tokens by >=20%.

        Opus 4.8's cache-activation minimum is 4096 tokens over the prefix
        (tools render before system blocks). Floors are model-specific and
        move — this guards against the primer shrinking below the largest
        current floor, not against SDK behavior.
        """
        primer = _texts(build_system_prompt(None, None, _TODAY))[0]
        tools_chars = len(json.dumps(build_tools()))
        estimated_tokens = (len(primer) + tools_chars) / 3.5
        assert estimated_tokens >= 4096 * 1.2

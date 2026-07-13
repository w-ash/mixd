# Context Engineering: Page-Contextual Tool Routing

> A portable pattern for agentic apps. This document explains a 2026 context-engineering
> best practice — **routing which tools an LLM sees based on where the user is** — why it is
> essential, exactly how mixd implements it, and how to reproduce it in any system. If you are
> building an agent with more than ~10 tools, the trade-offs here are yours too.

Audience: engineers building LLM agents (in mixd or elsewhere). Prerequisite: you have a tool
registry and an agentic loop. Nothing below is mixd-specific except the file paths.

---

## 1. The problem: tools are not free

Every tool you expose to a model costs twice.

- **Tokens.** A single non-trivial JSON tool schema is ~500 tokens. Ninety tools is ~50K tokens
  of schemas *before the model reads the user's question* — on every request.
- **Accuracy.** Selection quality degrades as the tool list grows. The industry rule of thumb in
  2026 is **fewer than 20 tools per agent, with accuracy degrading past ~10** (OpenAI's own
  guidance, echoed across the progressive-disclosure literature). Past that, the model picks the
  wrong tool, or claims it *can't* do something it has a tool for.

So "just register all the tools" stops working the moment your app is capable. mixd's registry is
~34 tools and climbing. The naive posture would make the assistant slower, more expensive, and
*less* able the more features we ship — a perverse incentive.

## 2. The 2026 consensus: progressive disclosure + context routing

Two complementary techniques form the current best practice.

### 2a. Progressive (tiered) disclosure

Don't load everything upfront. Reveal capability in tiers:

1. **Discovery** — the model sees only names + short descriptions (or a search tool).
2. **Activation** — a tool's full schema loads *when it becomes relevant*.
3. **Execution** — supporting detail loads only during use.

Anthropic's code-execution-with-MCP variant of this reports **~150K → ~2K input tokens (98.7%)**
for the same task. mixd's mechanism for this is `ToolSpec.defer_loading` (default **True**) plus a
server-side BM25 `tool_search` tool: cold tools are indexed but kept out of the model's context
until searched. That is disclosure across *time* — load when needed.

### 2b. Context routing — the part this doc is about

Progressive disclosure answers "load when relevant." **Context routing** answers *"relevant to
what?"* — and the cheapest, most reliable signal is often **already known before the model runs**:
the user's current context.

> Classify the request and direct it to the right context source **before anything enters the
> context window.** Rule-based routing for clean domains; semantic routing only for nuance.

For an app with a UI, **the route the user is on is the cleanest domain signal that exists.** A
user on the Playlists page is overwhelmingly likely to ask about playlists. So: promote the
playlist tools into the loaded set *for that page*, and leave the rest deferred. This is
**Page-Contextual Tool Routing** — disclosure across *space* (where the user is), complementing
disclosure across time (tool search).

Rule-based (a static route→tools map) beats a semantic classifier here because UI sections are
discrete and known: no latency, no model call, fully deterministic, trivially testable.

## 3. The non-obvious constraint the blogs underweight: prompt caching

Here is the trap, and mixd's main contribution to the pattern.

Agent loops re-send the entire tool array every turn, so you place **one prompt-cache breakpoint**
on the tool list; everything up to it becomes a cache *read* on subsequent turns. Cheap.

Now make the tool list vary by page. The intuitive implementation — *toggle `defer_loading` on the
current page's tools in place* — **silently destroys your cache**. Prompt caching keys on a byte
prefix; a promoted tool sitting *before* the breakpoint changes the cached prefix, so **every page
navigation is a full cache miss**. You'd trade a token win for a token loss and might never notice,
because nothing errors — the cache-hit rate just quietly craters.

**The fix is structural, not clever selection: make the cached prefix page-invariant.** Split the
tool array into:

- **Prefix (cached, page-invariant):** the always-hot curated core + the agentic tools. The cache
  breakpoint lives on the prefix's last entry. This never changes between pages.
- **Tail (uncached):** the deferred pool, the raw server-tool blocks, **and** whatever the current
  page promotes. Everything variable lives *after* the breakpoint.

Now page routing only ever mutates bytes *after* the breakpoint. Navigating pages re-sends a few
small promoted schemas uncached (negligible) while the expensive core prefix stays a cache hit.
You get contextual tools **and** a warm cache. That property — *per-request tool variation must
live behind the cache breakpoint* — is the load-bearing design rule. Everything else is detail.

## 4. How mixd implements it

All in `src/application/tools/registry.py`, threaded through the API and web client.

### 4a. The route→tools map (rule-based)

```python
# registry.py — kept to <=3 promotions/page so the loaded set stays under the ~10 ceiling.
_PAGE_TOOL_HINTS: Mapping[str, tuple[str, ...]] = {
    "playlists": ("query_playlists", "query_playlist_links"),
    "library": ("query_playlists", "query_stats"),
    "workflows": ("list_user_workflows", "get_workflow", "query_workflow_history"),
    "dashboard": ("query_stats", "query_operations"),
    "imports": ("query_operations",),
}


def _promoted_tool_names(page: str | None) -> frozenset[str]:
    # Unknown/absent page promotes nothing → degrades to static core + tool search.
    return frozenset(_PAGE_TOOL_HINTS.get(page or "", ()))
```

### 4b. The prefix/tail split (the cache-invariance property)

```python
def build_tools(*, enable_code_execution=True, page=None):
    promoted = _promoted_tool_names(page)
    prefix, tail = [], []  # prefix: cached + page-invariant
    for spec in TOOLS:
        if spec.dispatch is None and spec.kind == "agentic":
            tail.append(dict(spec.input_schema))  # raw server-tool block → tail
            continue
        tool = _tool_dict(spec)  # + allowed_callers on reads, etc.
        if not spec.defer_loading:
            prefix.append(tool)  # curated core + dispatched agentic
        elif spec.name in promoted:
            tail.append(tool)  # page-promoted: loaded, but uncached
        else:
            tool["defer_loading"] = True
            tail.append(tool)  # deferred: discovered via tool_search
    _stamp_cache(prefix, len(prefix) - 1)  # breakpoint on last prefix tool
    return prefix + tail
```

Key invariants, each guarded by a test in `tests/unit/application/tools/test_registry_parity.py`:

- **The cached prefix is byte-identical across every page** → `test_page_routing_promotes_within_the_cached_core`.
- **The loaded set stays ≤10 on every page** → `test_hot_set_stays_small_and_nonempty`.
- **The breakpoint sits on a dispatched tool, never a raw `{type,name}` server block** (those
  reject `cache_control`) → `test_build_tools_stamps_one_cache_breakpoint`.

### 4c. Threading the signal (front to back)

The web client sends the coarse section; the server maps it to tools. Mirrors the existing
`current_workflow_id` path.

| Layer | Location | What it does |
|---|---|---|
| UI | `web/src/components/chat/ChatPanel.tsx` | `pageSection(pathname)` → coarse section (index → `"dashboard"`); `SECTION_BY_SEGMENT` kept in sync with `_PAGE_TOOL_HINTS` |
| Transport | `web/src/api/chat-sse.ts` | adds `page` to the POST body |
| API | `src/interface/api/schemas/chat.py` | `ChatRequest.page: str \| None` (≤64 chars) |
| Route | `src/interface/api/routes/chat.py` | `build_tools(page=body.page)` |

### 4d. Same discipline for the research subagent

`build_subagent_tools()` applies the identical principle to the `delegate_analysis` subagent: a
curated ≤10 read hot set (`_SUBAGENT_HOT_TOOLS`) loaded, the rest deferred behind `tool_search`.
The subagent runs at **low effort**, where a bloated tool list hurts selection *most* — so it is
the case that benefits from the discipline the most, not the least. The routing signal differs
(the investigation's domain rather than a UI route), but the mechanism is the same: select a small
hot set from context, defer the tail.

## 5. Why this is essential (not a nice-to-have)

- **It removes a perverse incentive.** Without it, every feature you ship makes the agent worse
  (more tools → lower accuracy, higher cost). With it, capability and quality scale together.
- **It puts the model in its competence zone.** ≤10 visible tools is where selection is reliable.
  Routing keeps you there *without* amputating capability — the rest is one `tool_search` away.
- **It is nearly free.** Rule-based routing on a known signal costs no model call, no latency, and
  — done with the prefix/tail split — no cache penalty. The token/accuracy win is pure.

## 6. Porting this to another system (framework-agnostic recipe)

1. **Find your context signal.** A UI route, the active document/panel, the channel, the current
   task type — whatever is known *before* the model runs and correlates with intent.
2. **Curate a small static core** (the always-hot tools) that every context needs. Keep it under
   your accuracy ceiling with room to spare.
3. **Write a rule-based map** `signal → extra tool names`. Cap promotions so `core + promoted`
   stays under the ceiling (mixd: ≤3/page under a ~10 ceiling). Start rules-based; reach for a
   semantic/embedding router only if measurement shows the map is insufficient.
4. **Defer the long tail** behind a discovery mechanism (tool search / progressive disclosure) so
   nothing becomes *unreachable* — routing changes what's eager, never what's possible.
5. **Preserve the cache.** Put the always-hot core in a **page-invariant prefix** with the cache
   breakpoint on it; put deferred + promoted tools in the **tail after the breakpoint**. Verify
   the cached prefix is byte-stable across signals — this is the step everyone skips.
6. **Degrade cleanly.** An unknown signal promotes nothing; the agent still works via core +
   search. Routing is strictly additive.
7. **Test the invariants, not the wiring:** loaded-set size ≤ ceiling per signal; cached prefix
   identical across signals; on-signal tools eager, off-signal tools deferred.

## 7. Pitfalls (mistakes we made first, so you don't)

- **Breakpoint on a server tool.** Our first cut let the cache breakpoint land on the trailing
  `tool_search` server block (`{type, name}`), which rejects `cache_control` — risking a 400 on
  every request. Fix: choose the breakpoint among *dispatched* tools only.
- **In-place `defer_loading` toggle.** The intuitive "flip the flag for this page's tools" puts
  promoted tools before the breakpoint and busts the cache on every navigation. The prefix/tail
  split exists precisely to prevent this.
- **Over-promoting.** Each page must respect the ceiling. A test asserts `core + promoted ≤ 10`
  for *every* section, so a careless hint addition fails CI instead of silently degrading quality.
- **Semantic router by default.** Don't add a classifier/embedding dependency for discrete, known
  contexts — rule-based is deterministic, testable, and free. Escalate only on measured need.

## 8. References

- OpenAI tool-count guidance (<20 tools/agent, degradation past ~10) and the tiered-disclosure
  model, as synthesized in *State of Context Engineering in 2026* (swirlai).
- Thoughtworks Technology Radar — *Progressive context disclosure*.
- *Progressive tool loading is the new MCP context pattern* (Wire) — the 150K→2K figure.
- *Semantic Tool Selection: Context-Aware Routing* (vLLM Semantic Router) — rule-based vs semantic.

Internal: backlog epic *Page-Contextual Tool Routing* in
[`docs/backlog/v0.9.x.md`](../backlog/v0.9.x.md#v092-agentic-depth); implementation in
[`src/application/tools/registry.py`](../../src/application/tools/registry.py).

# Proto-Personas

> Proto-personas are hypothesis-based — no user research, just deliberate thinking about behaviors. Updated as understanding evolves.
>
> Template: [dev-setup-guide/product-context.md](dev-setup-guide/product-context.md)

---

## The Weekly Curator — Primary Persona

| Field | |
|---|---|
| **Context** | ~15k liked tracks across Spotify (daily driver) and Last.fm (scrobble tracking). Years of play history, likes, and curated playlists scattered across services that actively prevent cross-referencing. Manages 15+ playlists, updated on a weekly cadence. Web UI is the primary interface; CLI for automation and scripting. Technically proficient — built the tool, comfortable with JSON workflow definitions, terminal, and database queries. |
| **Goal** | "Own my listening data — the play history, likes, and playlists I've built up over years — and use it on MY terms. Not locked in a platform that won't even let me sort my own likes by how often I've played them." |
| **Key behavior** | Opens the web UI on Sunday evening. Checks the dashboard for data freshness. Refreshes imports if stale. Runs 3–5 workflows to update playlists. Spot-checks results, occasionally digs into track detail to fix a bad mapping or merge a duplicate. Pushes updated playlists to Spotify. Between rituals: explores play history, investigates listening trends, hunts for forgotten tracks buried in years of scrobbles. |
| **Pain point** | Streaming platforms are antagonistic toward self-understanding. They collect years of listening data and use it to feed algorithms you can't inspect, customize, or override. You can't ask "what did I love in 2019 that I've forgotten?" or "show me liked tracks unplayed for 6 months." Your own data is used to serve the platform's goals, not yours. Cross-service operations are impossible — Spotify doesn't know your Last.fm play counts exist. |
| **Not this person** | Not a casual listener happy with Discover Weekly. Not someone who sets up once and forgets — this person actively curates weekly. Not building a platform — building a personal tool to reclaim data sovereignty. |

### What the Weekly Curator uses

All of it — because the capabilities form a single story: **reclaim, unify, understand, act**.

- **Data reclamation** — Import play history, backup likes, sync playlists. Extract years of accumulated listening identity from platforms that lock it away.
- **Cross-service unification** — One library, one timeline, one set of likes — regardless of which service the data came from. Identity resolution (ISRC, fuzzy matching) bridges the silos.
- **History archaeology** — "What was I into 2 years ago?" Finding forgotten tracks, analyzing listening trends over time. The data is finally queryable.
- **Data quality** — Fixing bad mappings, merging duplicates, ensuring metadata accuracy. The foundation has to be trustworthy or nothing built on it is.
- **Metrics-driven curation** — Sorting/filtering by play counts, scrobble data, release dates — quantitative curation, not vibes. Playlists as an expression of your data, not an algorithm's guess.
- **Playlist automation** — Declarative workflows that encode personal taste as rules. The weekly ritual is the payoff — all the other capabilities feed into it.

---

## The Tinkerer — Secondary Persona

| Field | |
|---|---|
| **Context** | Tech-savvy music-obsessed friend. Uses Spotify and possibly Last.fm. Has a few hundred to a few thousand liked tracks and years of play history they've never been able to access or use. Comfortable with Docker, GitHub READMEs, and tinkering with config files — enjoys the setup process and understanding how things work. Heard about narada and wants to run their own instance. |
| **Goal** | "I want my own copy of my listening history that I can actually do something with — and I want to host it myself so it's truly mine." |
| **Key behavior** | Follows a deployment guide to get narada running on their own machine or VPS. Connects services via web OAuth. Explores the visual workflow editor, starts with templates, gradually builds custom workflows. Reads the docs. Files bug reports. Might contribute a workflow template or a feature request. Uses the CLI when it's faster. |
| **Pain point** | Setup friction: deployment docs don't exist yet, workflow creation has a learning curve, error messages assume builder-level context. Willing to learn, but needs the path to be well-lit — clear docs, good defaults, discoverable UI. |
| **Not this person** | Not a developer who will read source code or contribute PRs. Not someone who needs hand-holding — they can follow a guide and troubleshoot basic issues. Not the Casual Enthusiast — they enjoy understanding the system, not just using it. |

### What the Tinkerer needs that doesn't exist yet

- **Deployment guide + Docker** (v0.5.0) — Cannot self-host today
- **Web OAuth** (v0.5.0) — No env vars or CLI auth ceremony
- **More workflow templates** — Curated starting points they can learn from and customize
- **Good documentation** — Architecture overview, workflow cookbook, troubleshooting guide
- **Better error messages** — Actionable guidance, not HTTP status codes

---

## The Casual Enthusiast — Secondary Persona

| Field | |
|---|---|
| **Context** | Music-loving friend who uses Spotify daily, maybe Apple Music. Has thousands of liked tracks and years of listening history locked away in services that would rather feed them an algorithm than give them control. Knows their taste is more interesting than Discover Weekly gives them credit for. Proficient with web apps — comfortable navigating a dashboard, connecting accounts, editing settings — but isn't going to pull a repo from GitHub, run a CLI, or deploy anything. Needs narada hosted or delivered as an app. |
| **Goal** | "I just want an easier way to find music I'll actually like — based on what I already listen to, not what Spotify's algorithm thinks I should hear. And I want my playlists to be *mine*, not something a platform generated for me." |
| **Key behavior** | Signs up for a hosted instance or gets an invite from a friend who runs one. Connects Spotify via OAuth. Tells the LLM assistant what they want in plain English: "make me a playlist of tracks I've loved but haven't played in months" or "mix my most-played songs from this year with stuff I liked last summer." Reviews the generated playlist, tweaks it ("fewer remixes", "nothing before 2020"), pushes to Spotify. Comes back weekly when they see the results are actually good. |
| **Pain point** | Has always wanted more control over their music but the tools that offer it require technical knowledge — pipeline composition, config keys, or at minimum learning a new interface language. Streaming platforms exploit this gap: "you want better playlists? Here's our algorithm. Take it or leave it." The desire for control exists, the technical bar has always been too high, and the LLM agent finally changes that equation. |
| **Not this person** | Not a developer or a tinkerer. Not someone who will learn workflow node types or read documentation. Not non-technical either — they're proficient web app users who can navigate a dashboard and connect accounts. They just aren't going to self-host or use a CLI. |

### What the Casual Enthusiast needs

- **LLM-assisted workflow creation** (v0.8.0) — The game-changer. Natural language intent → working workflow, no node types or config keys. "Make me a playlist of my most played tracks from the last 3 months that I haven't added to any playlist yet" just works.
- **Hosted deployment or app** — Uses a friend's hosted instance, a shared deployment, or eventually a standalone app. Does not deploy infrastructure.
- **Web OAuth** (v0.5.0) — Click to connect, no env vars or CLI
- **Guided onboarding** — "Connect Spotify → here's what we found → what kind of playlist do you want?" Not a blank dashboard.
- **Conversational iteration** — "Too many sad songs" → LLM adds a mood filter. "Add more variety" → LLM adjusts the shuffle weight. The workflow evolves through conversation, not manual editing.

### How the Casual Enthusiast changes design priorities

This persona makes v0.8.0 (LLM-assisted creation) the most important adoption feature. Templates help the Tinkerer, but the Casual Enthusiast won't browse templates — they'll describe what they want and expect it to happen. The quality of the LLM workflow generation directly determines whether this person stays or bounces.

---

## The Passive Listener — Anti-Persona

| Field | |
|---|---|
| **Context** | Uses one streaming service. Listens to Discover Weekly, Release Radar, or algorithmic playlists. Does not manage playlists manually. |
| **Goal** | "Just play me something good." |
| **Key behavior** | Opens Spotify, hits play on a recommended playlist, never thinks about it again. |
| **Pain point** | None relevant to narada — satisfied with algorithmic curation. |
| **Not this person** | This is the litmus test: "Would the Passive Listener use this feature?" If yes, the feature is probably too generic or algorithmic. Narada builds tools for deliberate curation, not passive consumption. |

---

## The Platform Builder — Anti-Persona

| Field | |
|---|---|
| **Context** | Thinks in terms of user acquisition, social features, permissions models, and scaling to thousands of users. Wants collaborative playlists, public profiles, or multi-tenant SaaS. |
| **Goal** | "Let's add collaborative playlists so friends can contribute to each other's lists." |
| **Key behavior** | Requests features requiring multi-user infrastructure, social graphs, permission systems, or public-facing APIs. |
| **Pain point** | N/A — this person's pain points are not narada's problem to solve. |
| **Not this person** | This is the scope test: "Is this request pushing narada off its personal-tool axis?" Narada is a personal music metadata hub with optional sharing (<10 friends self-hosting). It is not a platform, not a social network, not a SaaS product. Features requiring multi-tenant data models, permission systems, or social mechanics serve this anti-persona. |

---

## Audit Findings (March 2026)

Findings from auditing the codebase and backlog against the personas above. Last updated: 2026-03-16.

### What's working

1. **No critical web-first gaps** — The Weekly Curator's Sunday ritual (check freshness → import → run workflows → spot-check → push) is fully coverable in the web UI at v0.4.11. No step requires the CLI.

2. **No orphan features** — Every feature in the active backlog (v0.4.x through v1.0.0) traces to a defined persona.

3. **Backlog ordering is sound** — Features that serve the primary persona (workflow execution, data quality, scheduling) ship before features that primarily serve the secondary (LLM creation, OAuth). Dependency chain is clean with no circular dependencies.

4. **Dependency chain validated** — v0.5.0 (foundation) → v0.6.x (connectors, independent of each other) → v0.7.0 (scheduling, hard dep on v0.5.0) → v0.7.1 (editor polish) → v0.8.0 (LLM, needs hosted instance + templates from v0.7.1) → v0.9.x (entity chain: artists → albums → Discogs → scrobbling) → v1.0.0 (auth, needs PostgreSQL from v0.5.0). Connectors-first ordering means scheduling automates a richer library.

### Changes applied

5. **v0.7.0 split into v0.7.0 + v0.7.1** — Scheduling (v0.7.0) is the critical automation feature for the Curator. Don't block it behind editor polish. v0.7.1 adds sub-flows, import/export, templates, and playlist browse — features that benefit Tinkerer onboarding and inform v0.8.0 LLM prompt engineering.

6. **Workflow Templates added to v0.7.1** — 5–8 curated templates ("Current Obsessions", "Hidden Gems", "Forgotten Favorites", etc.). Highest-impact improvement for the Tinkerer. Templates also inform LLM prompt engineering in v0.8.0.

7. **Browse/Search Playlists moved from unscheduled to v0.7.1** — Replaces paste-ID-to-link with a browse UI. Natural fit alongside advanced workflow features — playlists are workflow inputs/outputs.

8. **v0.8.0 LLM Feedback Loop descoped** — Removed A/B testing, feedback insights dashboard, model improvement framework (Platform Builder thinking with <10 users). Kept: simple thumbs-up/down feedback, quality tracking for manual prompt refinement.

9. **v1.0.0 renamed** — "Production-Ready Multi-User Platform" → "Multi-User Auth & Production Polish". Content was already sensibly scoped (<10 friends, no MFA, no RBAC). New title signals real engineering complexity (auth, isolation, security) without the scope-creep-inviting "platform" framing.

10. **Unscheduled cleanup** — "Multi-Language Support" and "Advanced Analytics Dashboard" moved to explicit "Not Building" section. Neither serves a defined persona.

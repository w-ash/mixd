# Entity Representation Findings

**Status**: Active as reference (run 2026-09-14) — the v0.12.0 spike deliverable; gates [v0.12.1](v0.12.x.md#v0121-first-class-artists) and [v0.12.2](v0.12.x.md#v0122-first-class-albums), whose Key Design Decisions cite it by section. **Two arms are not live-verified**: the Apple column was measured through the unauthenticated iTunes Search/Lookup API (same catalog ids as the Apple Music API; `relationships`, `isCompilation` and `filter[equivalents]` were not observable because the developer key lives only in the hosted environment), and Tidal was disconnected in prod at run time, so its column is read off the vendored OpenAPI spec and marked ⚠︎ `[RE-VERIFY AT IMPLEMENTATION]`. Delete when v0.12.2 ships and no unshipped epic cites a section here
**Run**: 2026-09-14, git `6aa87af6`
**Environments probed** (all read-only):
- Production Neon (`MIXD_USER_ID=cfe9d062…`, one tenant) — the corpus and its Spotify anchors come from `tracks` + `connector_tracks.raw_metadata` (82,081 Spotify rows; 1,127 Last.fm; **0** musicbrainz/apple/tidal/discogs connector rows)
- Spotify Web API (stored user token), Last.fm (app key), Discogs (stored personal token), MusicBrainz (no auth), iTunes Search/Lookup (no auth); Tidal ⚠︎ spec only
- **Corpus**: 51 artists (49 from the library's top-60 by plays plus the Various Artists sentinel and Ólafur Arnalds; buckets: 13 solo, 17 band/duo, 4 rename, 3 diacritic, 2 stylized, 4 name-collision, 2 the-prefix, 3 alias, 2 classical, 1 jazz, 1 sentinel — Radiohead, Tame Impala, Four Tet and Caribou are required inclusions) and 33 albums (canonical, reissue, expanded/deluxe/remaster, multi-disc, joint-credit, feat-heavy, DJ mix, compilation, classical, live, title-variant)

Query pack: `scripts/diagnose_entity_representation.py` — `sample` freezes the corpus from the library, `probe` fetches every service into `data/census/` (raw JSON, re-runs hit the cache), `report` prints the tables. Re-run: `MIXD_USER_ID=<id> uv run python scripts/diagnose_entity_representation.py sample|probe|report`.

---

## 1. Identity anchors — every service has an id except Last.fm, and two need the alias index to find it

Method: 51 artists resolved by search per service (exact normalized-name pick; renamed artists accept the top hit), Spotify by the library's stored artist id.

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Artist id | 22-char base62; resolved 51/51 | **none — the name string is the key**; `mbid` on 48/51 (missing: Kanye West, TEED, JAŸ-Z — all rename/variant pages) | numeric adamId; 50/51 (no "Various Artists" artist entity is searchable) | numeric; 50/51 fetched — `/artists/194` ("Various") is **404**, the sentinel is not a resource | string id (`Artists_Attributes`: name, popularity, handle, externalLinks) | MBID; 51/51, but 2 only via `alias:` search (below) |
| Batch fetch | `GET /artists?ids=` 50 ids → 50/50 returned (PDR-003) | n/a | `lookup?id=` multi-id | none | none | none (1 req/s) |
| Type / disambiguation | none (fields: id, name, genres, followers, popularity, images) | none | `artistType` "Artist" only | numeric suffix in the name — "Tycho (3)", "Justice (3)", "Jungle (12)"; `realname` on 27/50 | none | `type` Person 25 / Group 25 / Other 1; `disambiguation` on 27/51 |
| Search behaviour on renames | in-place rename **not applied**: id `5K4W6r…` still "Kanye West"; a separate "Ye" artist (`3NlsBP…`, 778k followers) exists | `artist.getCorrection` fired 1/51 (STRFKR → Starfucker); `autocorrect=1` swaps STRFKR's page for Starfucker's **with a different mbid** (`0a19e3e4` vs `e10e9128`) | current names as delivered by labels (TEED, STRFKR, JAŸ-Z are the primary names) | "Kanye West" primary, `realname` "Ye" | — | primary is **Ye**; `artist:"Kanye West"` ranks "Kanye West Tribute Band" at 100 and never returns Ye — `alias:"Kanye West"` does. `artist:"STRFKR"` returns nothing; `alias:` finds Starfucker |

**Implication (§1)**: an MBID is obtainable for every sampled artist, but only if the lookup queries the alias index; a name-only Last.fm mapping carries no id at all.

### 1a. Re-verify addendum (2026-09-19) — Apple and Spotify confirmed live

Method: `GET /artists/{id}` ×5 + one `GET /artists?ids=` batch (Spotify, local dev-DB token); `lookup?id=` + `search?entity=musicArtist` (iTunes, no auth) — Radiohead, Caribou, Four Tet, Bonobo, Kanye West.

Spotify `GET /artists/{id}` ×5 and the batch call (5/5) matched the census shape exactly — `id`, `name`, `genres`, `external_urls.spotify`, `images` (640/320/160px), plus `followers.total` and `popularity` (both present; PDR-003 flags them for import-path removal in dev mode, but this probe is one-off). The hosted Apple Music API `artists` relationship remains unreachable without the developer key, unchanged from the census. The keyless iTunes API confirms `artistId` matches the numeric suffix of `artistLinkUrl` exactly, the id/URL form MusicBrainz url-rels carry. Surprise: Kanye West's `search` also surfaces a separate "Ye" artist (`1714710847`) — the same rename split found on Spotify/MusicBrainz, not previously seen on Apple.

| artist | Spotify genres | followers | popularity | Apple artistId | Apple artistLinkUrl | primaryGenreName | amgArtistId |
|---|---|---|---|---|---|---|---|
| Radiohead | art rock, alternative rock | 17,139,207 | 87 | 657515 | `.../artist/radiohead/657515` | Alternative | 41092 |
| Caribou | idm | 750,267 | 56 | 45464574 | `.../artist/caribou/45464574` | Electronic | 683619 |
| Four Tet | idm, electronica | 845,028 | 62 | 35888604 | `.../artist/four-tet/35888604` | Electronic | 362101 |
| Bonobo | trip hop, downtempo, nu jazz, electronic | 1,535,668 | 68 | 416281071 | `.../artist/bonobo/416281071` | Electronic | 291709 |
| Kanye West | rap | 34,657,121 | 94 | 2715720 | `.../artist/kanye-west/2715720` | Hip-Hop/Rap | 353484 |

| artist | bucket | Spotify id | Last.fm mbid | Apple adamId | Discogs | MusicBrainz |
|---|---|---|---|---|---|---|
| Radiohead | band | `4Z8W4fKeB5YxbusRsdQVPb` | `a74b1b7f` | 657515 | 3840 · anv 11 · alias 3 · members | `a74b1b7f` Group · alias 5 |
| Tame Impala | solo-as-band | `5INjqkS1o8h1imAzPqGZBb` | `63aa26c3` | 290242959 | 1245513 · anv 0 · alias 0 · members | `63aa26c3` Person · alias 0 |
| Four Tet | alias | `7Eu1txygG6nJttLHbZdQOh` | `3bcff06f` | 35888604 | 3543 · anv 6 · alias 5 | `3bcff06f` Person · alias 4 |
| Caribou | alias | `4aEnNH9PuU1HF3TsZTru54` | `735e3514` | 45464574 | 275841 · anv 2 · alias 4 | `735e3514` Person · alias 3 |
| Daphni | alias | `4nhvb6x9ZhPiYCzrHDNia9` | `859216c4` | 1282779393 | 2127698 · anv 0 · alias 4 | `859216c4` Person · alias 1 |
| Jon Hopkins | solo | `7yxi31szvlbwvKq9dYOmFI` | `0b0c25f4` | 15040325 | 24056 · anv 7 · alias 1 | `0b0c25f4` Person · alias 1 |
| Bonobo | solo | `0cmWgDlu9CwTgxPhf403hb` | `9a709693` | 416281071 | 8708 · anv 3 · alias 3 | `9a709693` Person · alias 0 |
| Tycho | solo | `5oOhM2DFWab8XhSdQiITry` | `cbef45a9` | 119111355 | 71827 · anv 0 · alias 0 · members | `cbef45a9` Person · alias 1 |
| James Blake | solo | `53KwLdlmrlCelAZMaLVZqU` | `8dc08b1f` | 59504818 | 1500084 · anv 6 · alias 1 | `8dc08b1f` Person · alias 2 |
| Jamie xx | solo | `7A0awCXkE1FtSU8B0qwOJQ` | `d1515727` | 405563985 | 2109856 · anv 2 · alias 1 | `d1515727` Person · alias 3 |
| Max Cooper | solo | `0WSSKmoRbxqLf3MnXInQ2J` | `2d68f237` | 77685145 | 830699 · anv 0 · alias 1 | `2d68f237` Person · alias 1 |
| Grimes | solo | `053q0ukIDRgzwTr4vNSwab` | `7e5a2a59` | 2756920 | 1993487 · anv 1 · alias 3 | `7e5a2a59` Person · alias 6 |
| Sufjan Stevens | solo | `4MXUO7sVCaFgFjoTI5ox5c` | `01d3c51b` | 4273404 | 202598 · anv 10 · alias 0 | `01d3c51b` Person · alias 1 |
| Beck | solo | `3vbKDsSS70ZX9D2OcvbZmS` | `309c62ba` | 312095 | 3928 · anv 8 · alias 3 | `309c62ba` Person · alias 5 |
| Floating Points | solo | `2AR42Ur9PcchQDtEdwkv4L` | `69d9c5ba` | 311514259 | 1385084 · anv 2 · alias 1 | `69d9c5ba` Person · alias 4 |
| Toro y Moi | solo | `6O4EGCCb6DoIiR6B1QCQgp` | `3a6d6481` | 312848073 | 1362799 · anv 1 · alias 5 | `3a6d6481` Person · alias 0 |
| Washed Out | solo | `5juOkIIy18sFw9L30syt1Z` | `74a30f2a` | 330169462 | 1575877 · anv 1 · alias 1 | `74a30f2a` Person · alias 3 |
| Rival Consoles | solo | `05lIUgmmsmTX2N9dCKc8rC` | `803f2853` | 266175371 | 1012899 · anv 0 · alias 2 | `803f2853` Person · alias 0 |
| Kanye West | rename | `5K4W6rqBFWDnAN6FQUkS6x` | — | 2715720 | 137880 · anv 36 · alias 3 | `164f0d73` Person · alias 12 · primary **Ye** |
| TEED | rename | `0g3NiCRhEv7M4SEDMrpItN` | — | 1809828003 | 258514 · anv 9 · alias 0 | `bd075a82` Person · alias 3 |
| STRFKR | rename | `2Tz1DTzVJ5Gyh8ZwVr6ekU` | `0a19e3e4` | 336825579 | 1434932 · anv 2 · alias 1 · members | `0a19e3e4` Group · alias 2 |
| RÜFÜS DU SOL | rename | `5Pb27ujIyYb33zBqVysBkj` | `1cfa6ee5` | 799587823 | 5609881 · anv 0 · alias 0 · members | `1cfa6ee5` Group · alias 3 |
| JAŸ-Z | diacritic | `3nFkdlSjzX9mRTtwJOzDYB` | — | 1352449404 | 21742 · anv 48 · alias 5 | `f82bcf78` Person · alias 18 |
| Fred again.. | stylized | `4oLeXFyACqeem2VImYeBFe` | `bca46a0c` | 1455262408 | 8664813 · anv 8 · alias 2 | `bca46a0c` Person · alias 3 |
| CHVRCHES | stylized | `3CjlHNtplJyTf9npxaPl5w` | `6a93afbb` | 566867492 | 2953514 · anv 3 · alias 0 · members | `6a93afbb` Group · alias 4 |
| Christian Löffler | diacritic | `3tSvlEzeDnVbQJBTkIA6nO` | `a0d91c38` | 334282801 | 1317148 · anv 2 · alias 0 | `a0d91c38` Person · alias 1 |
| Ben Böhmer | diacritic | `5tDjiBYUsTqzd0RkTZxK7u` | `e4f12dfc` | 794108530 | 3796712 · anv 5 · alias 0 | `e4f12dfc` Person · alias 1 |
| Röyksopp | diacritic | `5nPOO9iTcrs9k6yFffPxjH` | `1c70a3fc` | 3432068 | 15596 · anv 12 · alias 2 · members | `1c70a3fc` Group · alias 8 |
| The Chemical Brothers | the-prefix | `1GhPHrq36VKCY3ucVaZCfo` | `1946a82a` | 3726283 | 2290 · anv 23 · alias 2 · members | `1946a82a` Group · alias 5 |
| The Avalanches | the-prefix | `3C8RpaI3Go0yFF9whvKoED` | `a6623d39` | 27524431 | 9130 · anv 2 · alias 2 · members | `a6623d39` Group · alias 1 |
| Hot Chip | band | `37uLId6Z5ZXCx19vuruvv5` | `d8915e13` | 24375409 | 159840 · anv 1 · alias 1 · members | `d8915e13` Group · alias 2 |
| Khruangbin | band | `2mVVjNmdjXZZDvhgQWiakk` | `aea4c9b9` | 478520598 | 3565949 · anv 4 · alias 0 · members | `aea4c9b9` Group · alias 4 |
| Boards of Canada | duo | `2VAvhf61GgLYmC6C8anyX1` | `69158f97` | 2989314 | 307 · anv 9 · alias 1 · members | `69158f97` Group · alias 2 |
| Maribou State | duo | `7zrkALJ9ayRjzysp4QYoEg` | `72034a05` | 439754171 | 2519329 · anv 2 · alias 0 · members | `72034a05` Group · alias 0 |
| Moderat | supergroup | `2exkZbmNqMKnT8LRWuxWgy` | `7754905b` | 298816155 | 92209 · anv 1 · alias 0 · members | `7754905b` Group · alias 0 |
| Kiasmos | duo | `6X8lhZ7YaRUBlOsOYimlyD` | `3252542b` | 306629563 | 1459655 · anv 0 · alias 0 · members | `3252542b` Group · alias 1 |
| Beach House | duo | `56ZTgzPBDge0OvCGgMO3OY` | `d5cc67b8` | 200823564 | 592308 · anv 2 · alias 0 · members | `d5cc67b8` Group · alias 0 |
| Disclosure | duo | `6nS5roXSAGhTGr34W6n7Et` | `5b5aac7b` | 520848228 | 2330068 · anv 1 · alias 0 · members | `ae65c507` Group · alias 0 |
| M83 | band | `63MQldklfxkjYDoUE4Tppz` | `6d7b7cd4` | 46086389 | 33591 · anv 2 · alias 1 · members | `6d7b7cd4` Group · alias 0 |
| Mount Kimbie | band | `3NUtpWpGDoffm3RCGhSHtl` | `4a3a5fc0` | 307909766 | 1354476 · anv 3 · alias 0 · members | `4a3a5fc0` Group · alias 0 |
| Purity Ring | duo | `1TtJ8j22Roc24e2Jx3OcU4` | `fdb53441` | 461030854 | 2215351 · anv 1 · alias 0 · members | `fdb53441` Group · alias 0 |
| Jungle | collision | `59oA5WbbQvomJz2BuRG071` | `3dd6a140` | 825833522 | 3388626 · anv 1 · alias 0 · members | `6bbb3983` Group · alias 0 |
| Justice | collision | `1gR0gsQYfi6joyO1dlp76N` | `860b2707` | 145072812 | 52758 · anv 2 · alias 0 · members | `860b2707` Group · alias 2 |
| Bob Moses | collision | `6LHsnRBUYhFyt01PdKXAF5` | `2a047ec5` | 877845967 | 279146 · anv 19 · alias 1 | `7ce3d484` Group · alias 0 |
| Tourist | collision | `2ABBMkcUeM9hdpimo86mo6` | `b1561ab7` | 771214275 | 3578931 · anv 0 · alias 2 | `4f31ccd8` Person · alias 1 |
| Booka Shade | duo | `2CKaDZ1Yo8YnWega9IeUzB` | `9940c604` | 129902516 | 16768 · anv 10 · alias 10 · members | `9940c604` Group · alias 1 |
| Weval | duo | `12tZvy2xFpWSkuJ3FsfisZ` | `cda72260` | 606587384 | 3789691 · anv 0 · alias 0 · members | `cda72260` Group · alias 0 |
| Max Richter | classical | `2VZNmg4vCnew4Pavo8zDdW` | `509f20b2` | 54782697 | 171698 · anv 3 · alias 0 | `509f20b2` Person · alias 5 |
| Ólafur Arnalds | classical | `7E3BRXV9ZbCt5lQTCXMTia` | `6655955b` | 265425550 | 986636 · anv 7 · alias 0 | `6655955b` Person · alias 3 |
| Vince Guaraldi Trio | jazz | `4ytkhMSAnrDP8XzRNlw9FS` | `744b52c8` | 2084011 | 271051 · anv 6 · alias 0 · members | `744b52c8` Group · alias 4 |
| Various Artists | sentinel | `0LyfQWJT6nXafLPZqxe9Of` | `4e46dd54` | — | 194 → HTTP 404 | `89ad4ac3` Other · alias 224 |

## 2. Aliases and name variation — only Discogs and MusicBrainz expose them, and they disagree on direction

Method: alias/ANV/related-artist fields on the 51 resolved artist objects; Spotify/Apple/Last.fm checked for any alias surface.

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Alias surface | none; aliases are **separate artists** — Four Tet `7Eu1tx…`, "⣎⡇ꉺლ…" `1TIbqr…` (405k followers), KH `7nwdED…` are three unlinked ids; Caribou and Daphni are two unlinked ids | none; parallel pages per name string (TEED 70k listeners, no mbid; JAŸ-Z 1.2M listeners, no mbid) | none | `namevariations` (credited spellings) on 41/50 — 284 total, max 48 (Jay-Z), 36 (Kanye West), 23 (Chemical Brothers); `aliases` (**separate linked artist entities**) on 28/50 — 71 total: Caribou ↔ Daphni ↔ Manitoba ↔ Dan Snaith, Four Tet → Kieran Hebden / 4T Recordings / Joshua Falken / Percussions / ⣎⡇ꉺლ… | none in `Artists_Attributes` | typed `aliases` on 36/51 — 338 total (untyped 186, Search hint 70, Artist name 65, Legal name 17); Various Artists alone carries 224 localized names |
| Primary ↔ alias direction | n/a | n/a | n/a | "Kanye West" primary, Ye = `realname` + alias entity "Ye (2)" | — | **Ye** primary, "Kanye West" ×2 Artist-name aliases; **TEED** primary, full name an alias; **JAŸ‐Z** primary (U+2010 hyphen) with 18 aliases |
| Same-person projects | unlinked | unlinked | unlinked | `aliases` both ways (Caribou lists Daphni; Daphni lists Caribou) | — | separate entities joined by `is person` artist-rels to "Dan Snaith" / "Kieran Hebden"; Daphni's only alias is "Daphni (Caribou)" |
| Bands → members | none | none | none | `members` on 25/50, `groups` on 5/50 | — | `member of band` rels on 34/51 |
| Search collisions (top-3) | Justice → Justice, Justice Der, Daft Punk; Bob Moses → 1 hit | Justice 13,311 matches; TEED 46,481 (Lou Reed second) | — | Justice (3)/Justice/Justice (2); Jungle (12)/Jungle Brothers/Jungle (2); Tourist (8)/(5)/Tourist | — | Justice: 3 entities at 100/92/84 with disambiguations "French electro house" / "drum & bass producer" / "thrash/death metal"; Jungle: Jungle Brothers scores 100, Jungle 97 |

**Implication (§2)**: the abbreviation gap (TEED, STRFKR) is bridged only by MusicBrainz aliases and Discogs ANVs; the primary/alias direction flips per service and over time, so nothing can key on "the primary name".

## 3. Multi-artist credits — structured on three services, strings on two, and remixers are indistinguishable from features

Method: album-level `artists` and track-level credits on the 32 Spotify, 41 Last.fm, 31 iTunes, 30 Discogs and 30 MusicBrainz album objects.

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Shape | ordered `artists[]` with ids, album and track level | one string per track/album (`"Moderat feat. Eased"`, `"The Avalanches [feat. Blood Orange]"`) — 20 track-artist strings in the corpus contain "feat" | one `artistName` string per collection/track, joined with `, ` and ` & `; `artistId` is the first artist's — except **"Karen O & Danger Mouse" has its own adamId 199356498** while "Khruangbin & Leon Bridges" carries Khruangbin's | `artists[]` with `name`, `anv` (credited spelling), `join` phrase, per master/release; `extraartists[]` with `role` on 27/30 releases (Voices 2: 107); track-level `artists` on 2/30 | `artists` relationship (ids only); roles are a separate `artistRoles` resource; no join phrase in `Albums_Attributes` | `artist-credit[]` = `name` + `joinphrase` + `artist.id`, at release-group, release and track level |
| Corpus counts | 97/499 tracks multi-credit; **0** "feat." in track titles; 778/2,395 discography items multi-artist | 20 feat-strings; joint album lookup by first artist alone ("Karen O", Lux Prima) returns a page with 0 tracks and no mbid | 4/31 collections with `&` credits; "feat." inside 3 track titles (Moderat deluxe) | join phrase on 2/30 masters ("&"); "Max Richter , Vivaldi" credits the composer as co-artist | — | joinphrase on 2/30 RGs (" & ") |
| Featured vs remixer | conflated: "Awake - Com Truise Remix" → `[Tycho, Com Truise]`, same shape as `[Caribou, Jessy Lanza]` | string only | string only | `extraartists.role` = "Remix", "Featuring", "Producer" … | roles resource | relationship types on the recording (not fetched) |
| Classical | album `artists` = [Max Richter, Daniel Hope, Konzerthaus Kammerorchester Berlin, Andre de Ridder], `album_type` **compilation** | album under "Max Richter" only | one 4-name string, Richter's adamId | composer as co-artist with join "," | — | RG not found by fielded search on the library title (title punctuation differs) ⚠︎ |

**Implication (§3)**: a `track_artists` row needs position, credited-name and join phrase; role (feat/remix/composer) is only available from Discogs and MusicBrainz and must be nullable.

## 4. Various Artists — six different sentinels, none shared

Method: the "Various Artists" artist lookup per service plus the two compilations and the DJ mix.

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Sentinel | real artist object `0LyfQWJT6nXafLPZqxe9Of` "Various Artists" (also `6Lk1jI…`, `2raOIg…` with 174k/82k followers — several); `/artists/{id}/albums` returns **0** items; the library holds 42 tracks under it | page "Various Artists", `mbid` `4e46dd54…` — **not** MusicBrainz's SPA `89ad4ac3…` | adamId **36270** on compilation collections; not returned by artist search | artist **194 "Various"**, not fetchable (404); `artist=Various` search finds neither compilation | none documented | Special Purpose Artist `89ad4ac3…`, type Other, 224 aliases, 259,110 release-groups |
| Compilation credit | `album_type` compilation, `artists` = [Various Artists], tracks credited individually (47 distinct on Birdsong) | album artist "Various Artists", per-track artist strings | `artistName` "Various Artists" + per-song `artistName`/`artistId` | not found under "Various" | — | RG secondary-type **Compilation** + credit Various Artists (Birdsong: Album+Compilation, 2 releases, 4 media) |
| DJ mix (fabric presents) | **not found** by album search (`album:` + `artist:` fielded query) | album artist "Various Artists", 23 tracks | credited to **Maribou State** (adamId 439754171), 19 distinct song artists | not found under "Various" (Discogs credits DJ mixes to the DJ) | — | not found with Various credit; MB models DJ mixes as RGs under the DJ with secondary type DJ-mix (Four Tet's discography: 12 DJ-mix RGs) |

**Implication (§4)**: "Various Artists" is not an artist to map; it is an album-level flag, and DJ mixes are credited to the DJ on three of six services.

## 5. Album vs release vs release-group vs master — two services model the work, three model the edition, one models the string

Method: 33 library album strings resolved per service; a match on the qualifier-stripped title is recorded as "stripped".

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Unit | one id per **edition**: OK Computer (12t) and OKNOTOK (23t/2d) are unrelated ids; KID A MNESIA is a third | one page per **album name string**: "Nevermind" (mbid) and "Nevermind (Remastered)" (no mbid) are separate pages | one collectionId per edition; `collectionType` is "Album" for all 31, EP/Single encoded as a title suffix (" - EP", " - Single": 5/31) | **master** (work) → `versions` (pressings): 30 masters, versions median 21, min 2, max 510 (Physical Graffiti), OK Computer 248; `main_release` = one canonical pressing | album per edition id; `version` attribute for the edition string; `albumType` | **release-group** → releases: 30 RGs, releases/RG median 7, max 25; primary Album 28 / Single 2; secondary Compilation 3, Live+Remix 1 |
| Library string match | exact 18, library anchor 13, stripped 1, none 1 | 41/42 found by string; edition pages mostly mbid-less (31/41 with mbid) | exact 26, stripped 1, top-hit 4 ⚠︎, none 2 | exact 22, **stripped 7**, top-hit 1, none 3 | — | exact 22, **stripped 7**, top-hit 1, none 3 |
| OKNOTOK | own album id | own page (mbid) | not found | no master — it is a release under master 21491 | — | no RG — it is a release inside RG `b1392450` |
| Surprises | Music For Psychedelic Therapy is `album`; Texas Sun is `single` | — | Currents search ranks "Currents B-Sides & Remixes - EP" first | Tycho's album is credited to "Tycho (3)"; Justice's to "Justice (3)" | — | Music For Psychedelic Therapy is a **Single**; The Campfire Headphase's only RG release is a Bootleg; Birdsong's first release is Withdrawn |
| Barcode / UPC | `external_ids.upc` on 32/32 | none | `upc` (Apple Music API) | `identifiers[]` Barcode on most physical releases | `barcodeId` | `barcode` on releases (blank on 7/30 first releases) |

**Implication (§5)**: 15/33 of the library's album strings carry an edition qualifier that Discogs and MusicBrainz never use as the work's title; the canonical album is the release group and the edition string belongs on the mapping, not the entity.

| album | bucket | Spotify | Last.fm | Apple (iTunes) | Discogs | MusicBrainz |
|---|---|---|---|---|---|---|
| Radiohead — OK Computer | canonical | album 12t/1d | mbid 12t | 12t/1d [Radiohead] | master 21491 · None versions · main 4950798 (Vinyl 16t) | RG `b1392450` Album · 25 releases · media 1 |
| Radiohead — OK Computer OKNOTOK 1997 2017 | reissue | album 23t/2d | mbid 23t | — | — | — |
| Radiohead — In Rainbows | multi-disc | album 10t/1d | mbid 10t | 10t/1d [Radiohead] | master 21520 · None versions · main 1174296 (Vinyl 10t) | RG `6e335887` Album · 18 releases · media 1 |
| Radiohead — Kid A | canonical | album 11t/1d | mbid 10t | 11t/1d [Radiohead] | master 21501 · None versions · main 74743 (Vinyl 15t) | RG `e75c0549` Album · 25 releases · media 2 |
| Radiohead — KID A MNESIA | reissue | album 34t/3d | mbid 33t | 34t/3d [Radiohead] | master 2363104 · None versions · main 20857672 (Vinyl 37t) | RG `6f25f9fb` Album+Compilation · 6 releases · media 3 |
| Tame Impala — Currents | canonical | album 13t/1d | mbid 13t | 5t/1d [Tame Impala] ⚠︎ top-hit | master 861083 · None versions · main 7252111 (Vinyl 13t) | RG `08aa7a6c` Album · 16 releases · media 1 |
| Tame Impala — The Slow Rush | deluxe-family | album 12t/1d | mbid 12t | 12t/1d [Tame Impala] | master 1681919 · None versions · main 14783822 (Vinyl 12t) | RG `c6fc678e` Album · 10 releases · media 1 |
| Four Tet — There Is Love in You (Expanded Edition) | expanded | album 18t/2d | mbid 18t | 1t/1d [Fred again.., Skrillex & Fou] ⚠︎ top-hit | master 220989 · None versions · main 2113861 (Vinyl 9t) · stripped | RG `36e5c63b` Album · 7 releases · media 1 · stripped |
| Four Tet — New Energy | canonical | album 14t/1d | mbid 14t | 14t/1d [Four Tet] | master 1244614 · None versions · main 10930262 (CD 14t) | RG `065cf7e0` Album · 4 releases · media 1 |
| Caribou — Swim | canonical | album 9t/1d | mbid 9t | 9t/1d [Caribou] | master 240989 · None versions · main 2239757 (CD 9t) | RG `1e103be2` Album · 10 releases · media 1 |
| Caribou — Our Love (Expanded Edition) | expanded | album 17t/1d | mbid 17t | 17t/1d [Caribou] | master 739909 · None versions · main 6000758 (File 1t) · stripped | RG `f90aafb2` Album · 10 releases · media 1 · stripped |
| Caribou — Suddenly | canonical | album 12t/1d | mbid 12t | 12t/1d [Caribou] | master 1688972 · None versions · main 14791710 (Vinyl 12t) | RG `975b39ae` Album · 11 releases · media 1 |
| Daphni — Jiaolong | alias-album | album 9t/1d | mbid 9t | 9t/1d [Daphni] | master 480403 · None versions · main 3897786 (Vinyl 9t) | RG `928b8330` Album · 6 releases · media 1 |
| Jon Hopkins — Music For Psychedelic Therapy | canonical | album 9t/1d | mbid 9t | 9t/1d [Jon Hopkins] | master 2377597 · None versions · main 20961415 (File 9t) | RG `461cae9e` Single · 1 releases · media 1 |
| Jamie xx — In Colour | canonical | album 11t/1d | mbid 11t | — | master 842201 · None versions · main 7067909 (CD 11t) | RG `a903a977` Album · 9 releases · media 4 |
| Boards of Canada — The Campfire Headphase | canonical | album 15t/1d | mbid 15t | 15t/1d [Boards of Canada] | master 2141 · None versions · main 541843 (CD 15t) | RG `646bff72` Album · 1 releases · media 1 |
| Hot Chip — Why Make Sense? (Definitive Version) | expanded | album 18t/2d | no mbid 18t | 18t/2d [Hot Chip] | master 835938 · None versions · main 7010427 (CD 10t) · stripped | RG `1ad35533` Album · 8 releases · media 2 · stripped |
| Tycho — Awake (Deluxe Version) | deluxe | album 17t/1d | no mbid 17t | 17t/1d [Tycho] | master 665381 · None versions · main 5456783 (Vinyl 8t) · stripped | RG `b4eab0c5` Album · 6 releases · media 1 · stripped |
| Nirvana — Nevermind (Remastered) | remaster | album 13t/1d | no mbid 13t | 1t/1d [Lady DD] | master 204840 · None versions · main 3270779 (DVD 20t) · stripped | RG `1b022e01` Album · 25 releases · media 1 · stripped |
| Led Zeppelin — Physical Graffiti (Deluxe Edition) | multi-disc | album 22t/3d | no mbid 22t | 22t/3d [Led Zeppelin] | master 4392 · None versions · main 458939 (Vinyl 15t) · stripped | RG `116c9490` Album · 25 releases · media 2 · stripped |
| Moderat — Moderat (Deluxe Version) | deluxe | album 15t/1d | no mbid 15t | 15t/1d [Moderat] | master 82410 · None versions · main 1773870 (CD 13t) · stripped | RG `6b3cd75d` Album · 11 releases · media 1 · stripped |
| Kiasmos — Kiasmos | duo-credit | album 8t/1d | mbid 8t | 8t/1d [Kiasmos] | master 751011 · None versions · main 10289135 (CD 8t) | RG `8f3d55a7` Album · 3 releases · media 1 |
| Khruangbin — Texas Sun | joint-credit | single 4t/1d | no mbid 0t | 4t/1d [Khruangbin & Leon Bridges] | master 1679242 · None versions · main 14756037 (Vinyl 4t) | RG `c1f33e46` Single · 1 releases · media 1 |
| Karen O — Lux Prima | joint-credit | album 9t/1d | no mbid 0t | 9t/1d [Karen O & Danger Mouse] | master 1518069 · None versions · main 13343237 (CD 9t) | RG `c5b39f08` Album · 4 releases · media 1 |
| Maribou State — Kingdoms In Colour | feat-tracks | album 13t/2d | mbid 10t | 10t/1d [Maribou State] | master 1414488 · None versions · main 12448883 (Vinyl 10t) | RG `281972e4` Album · 5 releases · media 1 |
| The Avalanches — We Will Always Love You | feat-tracks | album 25t/1d | no mbid 25t | 1t/1d [The Avalanches] ⚠︎ top-hit | master 1863498 · None versions · main 16483272 (File 25t) | RG `ad5a6f0f` Album · 5 releases · media 1 |
| Various Artists — fabric presents Maribou State | dj-mix | — | no mbid 23t | 22t/1d [Various Artists] | — | — |
| Various Artists — For the Birds: The Birdsong Project, Vol. I | compilation | compilation 47t/4d | no mbid 47t | 47t/4d [Various Artists] | — | RG `afe51f60` Album+Compilation · 2 releases · media 4 |
| Vince Guaraldi Trio — A Charlie Brown Christmas (2012 Remastered & Expanded Edition) | remaster | album 14t/1d | mbid 14t | 8t/1d [The Eric Byrd Trio] | master 3340036 · None versions · main 29176114 (CD 6t) · top-hit | RG `51279c71` Album+Compilation · 1 releases · media 1 · top-hit |
| Max Richter — Voices 2 | classical | album 10t/1d | mbid 10t | 10t/1d [Max Richter] ⚠︎ top-hit | master 2073139 · None versions · main 18236821 (Vinyl 10t) | RG `e9dc7956` Album · 2 releases · media 1 |
| Max Richter — Recomposed By Max Richter: Vivaldi, The Four Seasons | classical | compilation 18t/1d | mbid 18t | 24t/1d [Max Richter, Daniel Hope, Ko] | master 499517 · None versions · main 3840970 (CD 13t) | — |
| Justice — Woman Worldwide | live | album 15t/1d | mbid 15t | 15t/1d [Justice] | master 1409973 · None versions · main 12408759 (CD 15t) | RG `61c807dc` Album+Live+Remix · 3 releases · media 2 |
| Sufjan Stevens — Illinois | title-variant | album 22t/1d | mbid 22t | 26t/1d [Sufjan Stevens] | master 14863 · None versions · main 2829350 (Vinyl 23t) | RG `1fb75447` Album · 14 releases · media 1 |

## 6. Edition splits and successor pointers — only Tidal (spec) and Discogs (enumeration) relate editions to each other

Method: edition families in the corpus (Nevermind, Physical Graffiti, Moderat, Awake, Our Love, There Is Love in You, OK Computer, Kid A).

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Edition relation | none between album ids; relinking exists at **track** level only (v0.10.2.12) | none | none between collectionIds; `filter[equivalents]` maps storefronts, not editions (provider research §4) | `/masters/{id}/versions` enumerates every pressing with format/country/year; each release carries `master_id` | `albums/{id}/relationships/replacement` (to-one, availability substitution — IRDS §10.2); `Replacement_Provenance` meta on album items | releases share the RG; per release: `status` (Official 25, Promotion 1, Bootleg 1, Withdrawn 1 of 30 sampled), `date`, `country`, formats per medium |
| Deluxe/remaster naming | in the album `name` | in the page name | in `collectionName` | in release `title`/`formats.descriptions` (Reissue, Remastered, Deluxe) | `version` attribute | release `disambiguation` / title; RG title never carries it |

**Implication (§6)**: an album mapping needs an edition-family key (release group / master) beside the service's edition id; Tidal's `replacement` is a substitution pointer, not a family relation.

## 7. Track ordering and disc numbering — integers on three services, position strings on Discogs, rank on Last.fm

Method: track lists on the resolved album objects.

| | Spotify | Last.fm | Apple (iTunes) | Discogs | Tidal ⚠︎ | MusicBrainz |
|---|---|---|---|---|---|---|
| Fields | `disc_number`, `track_number` ints | `@attr.rank` (1-based), no disc | `discNumber`/`discCount`, `trackNumber`/`trackCount` ints | `position` **string**: vinyl sides (A1, B2) 190, plain numbers 162, "1-1" disc-track 15, **blank heading rows 14** of 381 | `volumeNumber`, `trackNumber` in the `items` relationship meta | `media[].position` + `tracks[].position` (int) and `number` (string, "A1" on vinyl) |
| Multi-disc in corpus | 7/32 albums (max 4 discs) | invisible | 4/31 | encoded in positions, not counts | `numberOfVolumes` | media count 2:4, 3:1, 4:2 of 30 |
| Track count agreement | OK Computer 12 | 12 | 12 | main release 16 (vinyl sides + headings) | — | first release 12 |

**Implication (§7)**: store (disc, track) integers; Discogs positions need a parser and heading rows must be dropped before counting.

## 8. Cross-service linkage — MusicBrainz url-rels reach every other service for 98% of the corpus; nothing links back

Method: `url-rels` on 51 MusicBrainz artists, 30 release-groups and 30 releases; `urls` on 50 Discogs artists; `mbid` on Last.fm objects.

| Link source → target | Spotify | Apple | Discogs | Tidal | Last.fm | Deezer | Wikidata |
|---|---|---|---|---|---|---|---|
| MB artist url-rels (artists with ≥1) | **51/51** | 50/51 | **51/51** | 50/51 | 47/51 | 50/51 | 50/51 |
| MB release-group url-rels (RGs with ≥1) | 0/30 | 0/30 | 26/30 | 0/30 | 13/30 | 0/30 | 23/30 |
| MB release url-rels (releases with ≥1) | 3/30 | 2/30 | 19/30 | 0/30 | 0/30 | 4/30 | 0/30 |
| Discogs artist `urls` → MusicBrainz | 0/50 (`urls` present on 50/50, none point at MB) | | | | | | |
| Last.fm `mbid` | artists 48/51; albums 31/41; Various Artists mbid disagrees with MB's SPA | | | | | | |

Multiplicity: Caribou has 2 Spotify and 2 Discogs url-rels (the alias entities), Max Richter 4 Spotify, Beck 5 Apple — one MBID maps to several service ids.

**Implication (§8)**: MusicBrainz artist url-rels can seed the artist mapping table for every connector at no matching cost; album linkage exists only for Discogs (RG level) and must be matched by barcode/title otherwise.

## 9. Implications — what v0.12.1 and v0.12.2 decisions inherit

1. **Artist identity anchors on the MBID, aliases are equal-rank keys** (§1, §2). Every sampled artist has an MBID, but two were reachable only through `alias:` search, and the primary name inverts (Ye, TEED, JAŸ‐Z) — the design-space option A "MB-alias lookup table keyed by MBID" is confirmed and option C's `artist.getCorrection` is nearly inert (1/51). The `connector_artists` row for Last.fm must key on the **name string** (its only identity), and its `mbid` is untrusted (STRFKR resolves to two MBIDs).
2. **`track_artists` = (position, credited name, join phrase, role?)** (§3). Spotify's structured credits carry no role, so "feat." and "remix" are indistinguishable there; Discogs (`extraartists.role`) and MusicBrainz relationships are the only role sources. Keep the credited spelling (Discogs `anv`) per row; a normalized artist name is a derived column, as today.
3. **Same-person aliases are separate artists with a link, not one artist** (§2). Caribou/Daphni are distinct ids on all six services; Discogs and MusicBrainz link them (`aliases`, `is person`). Model the link as an artist-to-artist relation, never a merge.
4. **Canonical album = MusicBrainz release group; the edition id lives on the mapping** (§5, §6). 15/33 library strings are Spotify edition names; 7/33 only match Discogs/MusicBrainz after stripping the qualifier; OKNOTOK has no work-level entity anywhere. Store the family key (RG / Discogs master) beside each service's edition id, and match on the stripped title with the edition string as a tiebreaker.
5. **Various Artists is an album flag** (§4). Six unrelated sentinels, one unfetchable; DJ mixes are credited to the DJ on Spotify, Apple, Discogs and MusicBrainz. `album_artists` carries an `is_various` boolean and a nullable artist list; never a canonical "Various Artists" artist row.
6. **Ordering is (disc, track) integers** (§7). Discogs positions need a parser that drops 14/381 heading rows; Last.fm has rank only.
7. **Seed artist mappings from MusicBrainz url-rels** (§8). 51/51 Spotify and Discogs, 50/51 Apple and Tidal — an MBID resolves to every connector's artist id before any name matching runs, with multiplicity (alias ids) preserved. Album linkage is Discogs-only at RG level; barcode (`upc`) is present on 32/32 Spotify albums and is the cross-service album key.
8. **Re-verify before implementation** ⚠︎: Tidal live behaviour of `replacement`, `version`, `numberOfVolumes`; MusicBrainz search under load (503s at 1 req/s forced retries on 8 queries). Apple and Spotify re-verified live 2026-09-19 ([§1a](#1a-re-verify-addendum-2026-09-19--apple-and-spotify-confirmed-live)) — Spotify matches the census; Apple's iTunes id/URL shape is confirmed, but the hosted `artists` relationship and `isCompilation` need the developer key.

# Android localization capture hook

This debug hook targets Unciv 4.22.10, upstream commit
`899367522cea36d6063c84b02b41eedfa9a0d0e9`. It changes no translations.
It is ignored by non-debuggable APKs. Without its extras, normal startup is
unchanged. A normal intent also exits an existing capture session after any
current load finishes, restoring ordinary settings from disk.

## Launch protocol, version 1

Use the SDK adb. All three string extras are required:

```sh
STATE_B64="$(python3 -c 'import base64; from pathlib import Path; print(base64.b64encode(Path("tech-state.json").read_bytes()).decode("ascii"))')"
adb shell am start -W -n com.unciv.app/.AndroidLauncher \
  --es l10n_screen tech-picker \
  --es l10n_request de-tech-001 \
  --es l10n_state_b64 "$STATE_B64"
```

The payload is standard padded base64 of UTF-8 JSON, with no line breaks.
Raw JSON is not accepted as a launch extra. Decoded state is limited to
32 KiB. Request ids match `[A-Za-z0-9._-]{1,64}`; callers must use a new id
for each request. Requests work on both cold startup and `onNewIntent`.
Submit sequentially, waiting for a terminal log event before the next request.

Every request disposes the previous screen stack, rereads settings, reloads the
named save from disk, resets ruleset/font resources and constructs the screen again.
New Game resources are reset before its inherited picker widgets are constructed.
Replaced font atlases are retained only until the old screen stack is disposed,
then released; no live widget loses its font during a rebuild. The previous selection,
tab, expanded sections and scroll positions are not reused. Language comes
only from normal `GameSettings.json`. Restart the app after changing language.

```json
{
  "schema_version": 1,
  "save": "issue-8-pair",
  "selection": {"technology": "Archery"},
  "tab": null,
  "expanded_sections": [],
  "scroll": {},
  "scroll_to": {
    "tech-tree": {"technology": "Mining", "align": "visible"},
    "description": {"edge": "start", "align": "visible"}
  },
  "camera": null,
  "new_game": null
}
```

Every top-level field shown above is required. Unknown, duplicate, missing or
inapplicable fields fail. `save` is an exact existing save name, without a
directory; it is null for the two menu screens. Source keys and entity names
are case sensitive and independent of the display language.

| Screen id | Required selection | Tab | Other state |
|---|---|---|---|
| `options-gameplay` | `{}` | `gameplay` | Fresh main menu underneath |
| `new-game` | `{}` | null | Required `new_game` object |
| `tech-picker` | `technology` | null | Select without confirming; anchor Mining |
| `city-production` | `city`, `construction` | null | Expand `Units`, select `Worker` without queuing |
| `empire-stats` | `{}` | `statistics` | Fresh Statistics overview |
| `policy-picker` | `{}` | null | Full expanded-branch list |
| `victory-status` | `{}` | `our-status` | First victory tab |
| `civilopedia` | `category`, `article` | null | `Tutorial`, `Differences from Civilization V` |
| `world` | `unit` | null | Required `camera` object |
| `city-state-diplomacy` | `city_state` | null | `Belgrade` must already be met in the save |

`city` is `{"owner":"The Ottomans","name":"Istanbul"}`.
`unit` is `{"owner":"The Ottomans","name":"Warrior","tile":[-7,11]}`.
Entity lookup must find exactly one entity belonging to the viewing player.
`city_state` is a string. `expanded_sections` is a complete array of source
section names; all other supported sections are collapsed. For policies use
`["Tradition","Liberty","Honor","Piety","Patronage"]`; it is empty for screens
without section controls. Unknown sections fail.

`camera` is `{"center_tile":[-7,11],"zoom":1.0}`. Tiles use saved hex x/y
coordinates. An unavailable tile or a zoom outside the map holder's supported
range fails. The camera controls the world map instead of a scroll instruction
for that pane.

`new_game` supplies all of these fields (the game is never started):

```json
{
  "ruleset":"Civ V - Gods & Kings", "difficulty":"Chieftain",
  "speed":"Quick", "era":"Ancient era", "max_turns":500,
  "city_states":6, "map_type":"Pangaea", "map_size":"Medium", "seed":0,
  "players":[
    {"type":"Human","civilization":"Random"},
    {"type":"AI","civilization":"Random"},
    {"type":"AI","civilization":"Random"},
    {"type":"AI","civilization":"Random"}
  ]
}
```

## Scrolling

Each attached visible ScrollPane requires exactly one instruction in either
`scroll` or `scroll_to`. A pane cannot appear in both. The hook reports the
complete pane-id list in `L10N_SCREEN_PANES` before validation and includes the
expected list in a pane-set error. Explicit ids include `tech-tree`,
`policy-tree`, `description`, `constructions`, `production-queue`, `categories`,
`entries`, `article`, `tabs-header`, `tabs-fixed`, `tabs-content` and `menu-map`.
Other panes use `pane-<depth-first-index>` within the pinned screen tree.
Zero-range panes still require an instruction; world camera is the exception.

Prefer anchors: they resolve against the actual layout in the active language.
An anchor has exactly one of `technology`, `construction`, `branch`, `entry`,
`category`, `tab`, `text` or `edge`, plus `align`. Technology, construction and
branch anchors use source names. Entry/tab anchors use the upstream actor's
source name; category anchors use the category enum name. `text` matches an
exact translated plain label within the pane and fails on ambiguous matches.
`edge` selects `start`, `center` or `end` of the pane's scroll range.

Supported alignments are `visible`, `center`, `start`, `end`. A named actor
must be fully visible after positioning. Missing, ambiguous, oversized or
out-of-range anchors fail. `edge` directly selects the boundary/centre;
its required `align` field does not change that boundary.

Use absolute offsets only when a stable anchor is unavailable:
`"scroll":{"pane-0":{"x":0.0,"y":0.0}}`. Units are Scene2D layout units,
not physical pixels; x starts at the left and y at the top. Values must be
finite, nonnegative and within the post-layout range. They are never silently
clamped. Scrolling is applied without easing or fling and checked again before
READY. The companion capture task owns the final per-language manifest.

## Readiness and errors

```text
L10N_SCREEN_PANES tech-picker request=de-tech-001 panes=tech-tree,description
L10N_SCREEN_READY tech-picker request=de-tech-001 frames=2
L10N_SCREEN_ERROR tech-picker request=de-tech-001 reason=save_not_found
```

Capture protocol events are emitted even when general debug logging is disabled.
Exactly one terminal READY or ERROR is emitted per request, including requests superseded while queued or loading. A replaced unfinished request emits `reason=superseded`; a normal launch retires unfinished work with `reason=normal_launch_requested`. Retired callbacks cannot publish READY/ERROR again or change the next screen. Wait on the
exact screen id and request id in fresh logcat output; `am start -W` only waits
for the Android activity, not the LibGDX screen. Do not use a fixed settle delay.

READY requires state application, completion of tracked preview/construction
jobs and at least two completed draws with unchanged layout and no remaining
Scene2D actions or pending tracked work. Partial target screens are not drawn
while background jobs are pending, so asynchronous completion cannot change
lazy font-atlas packing order. Retired menu/preview jobs are cancelled; a queued
menu callback checks its disposed owner before touching global fonts/resources. Slider values show immediately without a fade
or hide timer; world tile updates use stable insertion order for border blending. Before READY, scroll instructions are
verified again and GL commands completed. READY and ERROR disable continuous rendering;
subsequent hook render callbacks do no drawing. A new request or normal launch wakes the
hook. READY therefore leaves the final framebuffer available for capture. If a completed draw changes nested
pane bounds, positioning is reapplied and the two-frame gate starts over. In hook mode, stage clocks use zero delta during setup and do not
advance after application: map/unit/transition/cursor actions are frozen.
The main-menu background and New Game preview use seed 40 in hook mode
without seasonal variation, fade or periodic replacement. Upstream seed 0 means current time,
so it is not a deterministic preview seed. The requested New Game seed field
still controls the unstarted game setup. Unexpected popups and
tutorials are suppressed; Options is the intended popup for its screen.

Unknown ids, missing saves, invalid state, unavailable entities and failed
positioning produce ERROR and a blank screen, never the previous screen as a
successful substitute. A readiness timeout also fails. Hook-mode settings and
autosaves are not persisted. It does not advance turns, research technology,
queue production, adopt policies or create diplomatic contact.

For device verification, open A, then B, then A again and compare decoded PNG
pixels of the two A captures. Also test a regular launch without extras and
failure cases. APK installation and explicit app-data replacement require the
separate confirmation and backups documented in the checker repository.


## Additive protocol version 2

Build `1271047` adds optional string extra `l10n_protocol=2`. Absence selects
protocol v1. State `schema_version` remains **1** for both protocols; unsupported
protocol values fail with `unsupported_protocol`. V1 keeps its PANES, READY and
ERROR formats and creates no dump files. All four robustness fixes apply to v1
and v2. Non-debuggable APKs ignore all hook extras, including the fault extra.

Add the following to the existing launch command to request a text dump:

```text
--es l10n_protocol 2
```

A successful v2 request emits the following after the final quiet draw and
`glFinish`, before the unchanged READY event:

```text
L10N_SCREEN_DUMP tech-picker request=de-tech-001 file=files/l10n-hook/de-tech-001.json sha256=<64-lowercase-hex>
L10N_SCREEN_READY tech-picker request=de-tech-001 frames=2
```

READY is emitted only after successful atomic dump publication. Serialization,
geometry or output errors emit ERROR instead. Wait for the exact current request
and screen after the fresh logcat barrier; never pair an older dump with a new
PNG. Read only the path from that request's DUMP event, after its READY:

```sh
adb exec-out run-as com.unciv.app cat files/l10n-hook/de-tech-001.json
```

This command is documentation for a separately approved device phase; the
implementation's host tests never execute adb. Check the SHA-256 of the exact
retrieved bytes against the event before parsing. Host provenance associates
these bytes with the PNG byte hash, decoded RGBA pixel hash, fork commit and APK
SHA-256. The app does not include a self-referential build/commit/APK hash.

### Output writes and canonical bytes

The hook creates app-private `files/l10n-hook` if absent, exclusively creates
`files/l10n-hook/<request>.json.tmp`, writes UTF-8 and syncs the file descriptor,
then atomically renames within the same directory to
`files/l10n-hook/<request>.json`. It refuses an existing final **or temporary**
file. A failed temporary file may remain as diagnostic evidence. There is no
copy fallback and no automatic deletion, overwrite, wildcard cleanup or
caller-selected path. Only this single GL-thread publisher writes these outputs.
These temporary/final files are hook outputs, distinct from settings/save writes.
The device write plan must list every one, and any later individual cleanup.

The canonical dump has fixed key insertion order, deterministic actor/draw order,
locale-independent finite numbers without geometry rounding, UTF-8 with literal
Unicode and a trailing LF. Invalid UTF-16 fails encoding rather than silently
replacing characters. Request ids, output paths, timestamps, frame counters,
object ids, texture atlas coordinates and timings are absent from its bytes.
A fresh request token therefore cannot affect its hash. Cold-start and reversed
screen-order equality still require the approved device checks.

`state_sha256` hashes the **exact decoded UTF-8 JSON bytes** supplied in
`l10n_state_b64`, before JSON parsing. For v2, the host sends recursively
key-sorted compact JSON, preserving non-ASCII, serializing integer-valued floats
as integers and rejecting non-finite numbers. V1 state serialization is unchanged.

### JSON dump format 1

Each object has exactly the keys below. Rectangles are
`[left,top,right,bottom]`, in physical framebuffer/screenshot pixels with a
**top-left origin** and exclusive right/bottom edges. Full bounds can lie beyond
the viewport. Null means unavailable or no visible intersection, never a zero box.
The actual app surface must match the captured PNG dimensions; the host rejects
mismatched pairs instead of rescaling the dump.

Top-level keys in wire order:

| Key | Type / meaning |
|---|---|
| `dump_version` | integer `1` |
| `protocol_version` | integer `2` |
| `build_version_code` | positive integer from installed Android package metadata |
| `screen`, `language` | supported screen id and active normal game language |
| `state_sha256` | 64 lowercase hex characters |
| `image_size` | `[width,height]`, actual physical backbuffer size |
| `viewport` | actual GL viewport rectangle in physical pixels |
| `elements` | ordered array of draw records |

Element keys in wire order:

| Key(s) | Type / meaning |
|---|---|
| `id` | unique structural actor path plus per-actor draw ordinal; unmapped draws have explicit ordered ids |
| `logical_id` | logical occurrence path; shared only for explicit ShadowedLabel layers |
| `actor_path` | structural path or null |
| `source_key`, `source_keys` | known English translation entry or null; known component entries or `[]` |
| `input_text` | complete translated widget input, or null; provenance only |
| `display_text` | final submitted glyph strings and line breaks, after markup/layout/ellipsis |
| `bounds` | owner rectangle or null |
| `glyph_bounds` | bounds of all submitted glyph quads |
| `visible_bounds` | union of visible clipped geometry, or null |
| `drawn` | boolean: any nonzero-alpha visible glyph geometry |
| `effective_alpha` | maximum submitted glyph alpha, finite 0..1; mixed alpha warns |
| `visibility` | `full`, `partial` or `hidden` from per-glyph alpha and clipping |
| `clip_bounds` | actual effective scissor intersected with viewport, or viewport if scissor disabled; null if empty |
| `clipped_fraction` | finite 0..1: total quad area outside the effective clip / total full quad area |
| `clip_cause` | `none`, `ancestor_scissor`, `screen` or `mixed` |
| `ellipsis_applied` | actual engine truncation was observed, including when replacement text equals the original literal |
| `overflow_x`, `overflow_y` | glyph extents exceed owner bounds on that axis |
| `font_scale` | positive finite `[x,y]` scale when glyphs entered the cache |
| `pixel_scale` | positive finite `[x,y]` effective physical scale, including batch/projection transforms |
| `decoration` | true only for the explicitly identified ShadowedLabel shadow |
| `warnings` | ordered diagnostic strings; no defect labels |
| `lines` | ordered line objects |

Each line has exactly `text` (string), `bounds` (rectangle), `glyphs` (array).
Each glyph has exactly `text` (string), `bounds` (rectangle), `visible_bounds`
(rectangle or null), `visibility` (`full`, `partial`, `hidden`). Explicit blank
lines retain their position in the string and contain no glyph records. Their
line bounds are a degenerate point at the actual cached layout-origin baseline;
they do not imply an ink or glyph rectangle.

The dump retains hidden/partially clipped glyphs as diagnostics. A consumer must
use only fully visible glyph spans for a partially clipped unit, never complete
a cut character or hidden suffix from `input_text`. A fully visible unit uses
`display_text` exactly. Mixed-alpha text remains diagnostic where appropriate.
Empty-space glyphs have their submitted geometry; measurements concern glyph
quads, not opaque font ink. `clipped_left/top/right/bottom` warnings identify
which edges cross the effective clip. Source input and an English key must never
be substituted for visible text.

### Observation and coverage

`L10nBitmapFontCache` forwards every draw unchanged and observes the cache's
actual layouts, page vertices, draw ranges and packed vertex alpha immediately
before Batch submission. It does not call layout, validate, getGlyph or warm the
atlas. Glyph ids are converted through the existing DiacriticSupport mapping;
actual symbol/private-use icon characters are retained with `icon_glyph:<hex>`
warnings, never transcribed as ordinary words. Offscreen font-icon FBO drawing
is excluded by the actual framebuffer binding.

`NativeBitmapFontData.getGlyphs` additionally traces the actual libGDX 1.14.2
`GlyphLayout.truncate` invocation, associating its live line run with the cache
that immediately receives it. This observes applied truncation rather than
comparing strings or testing the Label's ellipsis-enabled flag. Stack inspection
is active only for v2. The implementation is pinned to this libGDX version and
its run-pool behavior; upgrades require the real GlyphLayout tests to pass.
Supported native fonts use blank-line scale 1. Unsupported line geometry or an
unobserved font layout fails the dump instead of inventing its semantics.

The stage walk supplies **metadata only**: stable actor paths and Label layout
ownership. Widget input and owner bounds are read at actual draw submission, while
Group.draw's temporary child offsets and the actual batch transform are active.
Batch projection/transform matrices, framebuffer size and actual GL viewport
project both owner rectangles and submitted glyph vertices. Actual GL scissor state supplies the already
intersected nested clip; polygon clipping measures rotated/scaled quad area.
The observer adds no actor and changes no text, position, cache or drawing call.

Source audit for the ten supported screens:

| Drawing path | Observation / ownership / source provenance |
|---|---|
| ordinary Labels, TextButton and CheckBox labels | shared font cache; Label layout identity; translating factories bind known source entries |
| ColorMarkupLabel and WrappableLabel | same cache; native markup/glyph order; source supplied by their translating constructors |
| ShadowedLabel | each real layer retained; explicit parent links logical occurrence, first layer marked decorative |
| translated SelectBox values | L10nSelectBox draw scope binds shared direct-font cache; original selected value supplies source |
| generic options/New Game SelectBoxes | same scoped owner with current selected display value; unknown source stays null |
| numeric/text fields and placeholder messages | TextFieldWithFixes draw scope covers TextField.drawText and drawMessageText, including selected ranges |
| raw Labels / already-composed strings | actual draw retained, owner known from layout; unavailable originating source remains null |
| other direct font drawing | recorded in order with null owner/source and `unknown_owner` / `unmapped_font_draw` warnings |

The generic SelectBox construction sites used by Options and New Game are scoped,
including map file/time controls. No direct application font.draw calls were found
outside these upstream widget paths. This is a source audit plus host mock-GL
verification, **not** a device coverage claim: inspect actual ten-screen dumps for
unknown owners, source gaps and alignment before acceptance.

Clipping/alpha/overflow/ellipsis describe engine behavior, not localization
defects. Later opaque actors can occlude earlier text; arbitrary occlusion and
opaque-ink visibility are not measured. Keep these limitations visible during
human review. The outstanding victory-status/Civilopedia scroll checks remain
human checks. No candidate/reference is approved by the engine.

### Forced construction failure (Android debug, v2 only)

For an otherwise valid `city-production` request add:

```text
--es l10n_protocol 2 --es l10n_fault construction-background
```

This throws `forced_construction_background_failure` before construction DTO
gathering on the background thread. Its exactly-once completion releases pending
work, forwards the sanitized actual cause on the GL thread and emits immediate
ERROR; it does not wait for the 60-second readiness timeout. It makes no
translation, settings or save change. All other non-null fault selections fail
with `unsupported_fault`. Release APKs ignore this facility. Host unit tests also
force GL callback failure and duplicate/late completion.

Loading requests drain before the next load starts. Their resource/load/GL
callbacks validate request ownership, and construction callbacks capture the
request that scheduled them. Retired callbacks cannot change the next screen or
emit a second terminal event. Ordinary non-hook construction failures still reach
the existing crash handler after releasing pending work.

### Host verification and remaining device gate

Run only the host tasks:

```sh
./gradlew :tests:test :android:assembleDebug :android:lintDebug
git diff --check
```

The l10n tests use real libGDX GlyphLayout, BitmapFontCache and Scene2D stages,
mocking textures/GL submission. They cover post-ellipsis strings including an
unchanged `...`, Unicode/markup, wrapping/blank lines, physical transforms,
zero alpha, nested scissors, unchanged submitted vertices, geometric fractions,
canonical bytes/state hashes, output collisions and all four #44 lifecycle fixes.
The generated `build/issue47-evidence/sample.texts.json` is synthetic geometry
test data. `build/issue47-evidence/host-draw-sample.texts.json` uses actual libGDX
layout/cache draws with mock GL and synthetic text. Neither is a device dump or
an accepted reference.

No device operation is part of these tasks. Device availability and the exact
install/settings/output write plan require Anton's separate gates. Still required:
40 PNG/dump pairs for all ten screens in EN/DE, forward/reverse passes; zero changed
decoded pixels against repeats and build 1271045; byte-identical repeated dumps;
forced failure latency, supersession, frozen READY framebuffer, and ordinary cold/
warm launch/input checks. Record every discrepancy. Source inspection or host
tests do not establish these device results. Human confirmation is separate.

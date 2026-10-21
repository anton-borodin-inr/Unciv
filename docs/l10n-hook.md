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
One terminal READY or ERROR is emitted per sequential request. Wait on the
exact screen id and request id in fresh logcat output; `am start -W` only waits
for the Android activity, not the LibGDX screen. Do not use a fixed settle delay.

READY requires state application, completion of tracked preview/construction
jobs and at least two completed draws with unchanged layout and no remaining
Scene2D actions or pending tracked work. Before READY, scroll instructions are
verified again and GL commands completed. If a completed draw changes nested
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

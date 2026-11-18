# Translation-only dev APK builder (issue #59)

The builder validates the frozen checker plan and build requests against the
pinned dev split, recipes, and screen manifest. It starts each request from
Unciv source `b534a8ae67c390412984869c7a03b168d532c36c`, applies only the
request's literal English/German translation edits, and assembles one debug APK.
The empty-patch control builds first with the same committed producer as the
calibration singleton and mutation batches. The Gradle init script sets a
reserved versionCode and disables texture packing. VersionCode reservations
use the fork's Git common directory.

Run from a committed builder revision. Supply an existing JDK 21, Android SDK
with platform 37 and build tools 37.0.0, and a complete local Gradle 9.4.1 cache.
Gradle runs offline. Use a new output directory under `~/Developer/sandbox/`.

```sh
python3 tools/l10n_apk_builder.py build \
  --repo "$PWD" --output-root "$APK_DIR" \
  --requests "$REQUESTS" --plan "$PLAN" \
  --recipes "$CHECKER/fixtures/stage3/recipes-v1.json" \
  --manifest "$CHECKER/fixtures/stage2/screens-v2.json" \
  --split "$CHECKER/fixtures/stage2/split.json" \
  --reference-apk "$REFERENCE_APK" \
  --jdk "$JDK" --sdk "$SDK" --gradle-cache "$GRADLE_CACHE"
```

Use `check` with the five handoff inputs to validate them before assembly. The
default digests are for the frozen initial dev plan. Explicit `--plan-sha256` and
`--requests-sha256` select a separately approved dev handoff with the same
source, recipe, and split authority.

On success the output contains only `builds.json` and the APKs it names. The
list records `source_commit`, `builder_commit`, `reference`, and `artifacts`.
The reference and every request ID in `artifacts` map to an object with a local
APK basename in `file` and a `sha256`. The pinned reference APK (1271048) is
carried for restoration. The builder verifies the reference pin, signing
continuity, assigned version codes, packaged translations, and equality of
nontranslation payloads with the empty control. Use `verify --builds PATH
--sha256 DIGEST` to check the published list and APK digests. Treat the APKs as
inputs to the checker's device qualification. Reuse this directory across
attempts for the task and remove the disposable set after the task (D-059).

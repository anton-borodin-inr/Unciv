# Translation-only dev APK builder (issue #59, part b)

## Boundary and authority

This builder consumes the checker's `l10n-mutate plan` handoff. It does not run
adb, install/uninstall an application, start an emulator, capture screens, invoke
an environment checker, call a provider/OCR/detector, create labels, or qualify
visible defects. Those operations and D-050's empirical gates remain part (c).

- Game/hook source: `b534a8ae67c390412984869c7a03b168d532c36c` (original 1271048).
- Checker contract: `e26914a4870d7755634cf547931f88c3e3093e5c`, specifically
  `mutation_cli.build_requests`, `mutation_recipes.Recipe.patch`, and
  `text_dump.canonical_bytes`.
- D-050 is Accepted. Empty controls must come from this same builder. A build
  catalog remains `built_unqualified`, `production_ready: false`.
- Only the dev membership in the hash-pinned split metadata is accepted. No
  future-cohort runner or bypass is provided. Discovery must start from known
  repository/artifact paths, never a home/parent-directory listing or a broad
  evidence/data scan.

The CLI and tests use Python's standard library. Existing Gradle/SDK/JDK tooling
is required; there is no package installer or dependency-version change. Use a
pre-existing, complete Gradle cache: builds run offline and fail when it lacks
pinned artifacts. The builder makes an isolated copy of only the cache's module
and pinned wrapper-distribution directories. It does not inherit user Gradle
properties/init scripts or provider environment credentials.

## Frozen initial input

| Input | SHA-256 |
|---|---|
| `recipes-v1.json` | `c181072b73ca7d16101db935ec0493df9f42c168b1d53139af546c845ce8d23f` |
| `plan.json` | `664f29f1902333c49427fdfeef4629b7d146c416e12100327001bd77aea12c55` |
| `build-requests.json` | `36cf23d92bba9a059284dbdd7dfe7ccc90ba95b52b3b9bae0c9997308d289b94` |
| `screens-v2.json` | `b80d9a8c976fdff19cb97d300b264efdcc926ea4baeb88d45e7cdbcea929276a` |
| `split.json` | `d2b7f97cc77f1d5f48d2c5e36972d2ece5ec71c128197ee157e2cec20c0c4c82` |

The initial request bundle contains the empty control
`batch-37517e5f3dc66819`, standalone calibration `batch-4334cc699af95644`, and
mutation batches `batch-99984bb52ea65c2f`, `batch-e45e29bd1f0f9039`, and
`batch-e46946a0afe9ec07`. Every recipe is checked against its exact pinned
original RHS or absent entry before the first APK assembly.

### Requests version 1 (unchanged)

Top-level fields, with no extras:

- `requests_version`: integer `1`, not a boolean.
- `source_commit`: the full game-source pin above.
- `plan_sha256`: SHA-256 of canonical `plan.json` bytes.
- `recipe_version`: `stage3-v1`.
- `requests`: sorted by request ID, with no duplicates.
- `constraints`: exactly `translation_files_only`,
  `same_builder_for_empty_and_mutated_patches`, and
  `build_only_no_device_operations`, all literal booleans `true`.

Each request has exactly `id`, `patch_set_sha256`, `patches`, `role`, and
`calibration`. Role is `clean`, `mutation`, or `calibration`; the boolean
calibration flag may also mark a singleton already requested as a mutation.
Exactly one empty clean request and one singleton calibration are required.
Each ID is `batch-` plus the first 16 lowercase hex digits of its full patch
hash. Duplicate full hashes or shortened-ID collisions fail closed.

Each patch has exactly `file`, `operation`, `key`, `original`, `replacement`.
Only the exact English/German paths under
`android/assets/jsons/translations/` are allowed. The patch must equal its
frozen recipe in the companion plan; arbitrary new source identifiers and
rename-by-remove/add are not accepted.

| Operation | Precondition and result |
|---|---|
| `add` | `original: null`; key must be absent, not present with empty RHS. Append the approved key and nonempty replacement. |
| `replace` | One unambiguous entry; literal RHS equals `original`, including `""`. Replace RHS only. |
| `remove` | One unambiguous entry; literal RHS equals `original`; `replacement: null`. Physically delete the locale entry. |

Unsupported escapes, CR/LF/NUL in patch literals, invalid Unicode, no-op/empty
replacements, unapproved paths, extra fields and conflicting edits are rejected.
No source tokens or broken placeholders are repaired. Untouched source bytes,
comments, spacing and line endings are retained. Additions are sorted by literal
key and use the file's existing LF/CRLF convention; a source without final LF is
not silently normalized to support additions.

Canonical JSON is UTF-8 with unescaped Unicode, sorted object keys, compact
separators, finite numbers (integral floats serialized as integers), and exactly
one final LF. Array order remains significant for hashes. Duplicate JSON keys
are invalid. The request file must be the exact canonical checker output.

Companion metadata is mandatory because requests contain no screen IDs. The
builder checks plan/recipe/authority hashes, complete dev EN/DE inventory
metadata, selected targets/occurrences, recipe coverage and the exact request
projection. It reads no PNG/dump/save payloads. This transport validation does
not replace `mutation_plan.load_plan` or runtime qualification on the checker.

## Commands

From the fork checkout, substitute the approved paths (there are no implicit
searches for APKs, data folders or tools):

```sh
python3 tools/l10n_apk_builder.py check \
  --requests "$PLAN/build-requests.json" --plan "$PLAN/plan.json" \
  --recipes "$CHECKER/fixtures/stage3/recipes-v1.json" \
  --manifest "$CHECKER/fixtures/stage2/screens-v2.json" \
  --split "$CHECKER/fixtures/stage2/split.json"

python3 tools/l10n_apk_builder.py build \
  --repo "$FORK" --output-root "$ARTIFACT_ROOT" \
  --requests "$PLAN/build-requests.json" --plan "$PLAN/plan.json" \
  --recipes "$CHECKER/fixtures/stage3/recipes-v1.json" \
  --manifest "$CHECKER/fixtures/stage2/screens-v2.json" \
  --split "$CHECKER/fixtures/stage2/split.json" \
  --reference-apk "$ORIGINAL_1271048_APK" \
  --jdk "$EXISTING_JDK21" --sdk "$EXISTING_ANDROID_SDK" \
  --gradle-cache "$EXISTING_COMPLETE_GRADLE_USER_HOME"
```

The authoritative allocation/freeze/publication registry is anchored at
`<git-common-dir>/l10n-apk-builder-v1`, shared by this fork's worktrees and
independent of output directories. Changing output roots cannot reset it.
Preserve this registry with the frozen catalogs; do not start another clone or
machine as an independent allocator for this reserved namespace. Migration of
that authority requires an explicit, verified transfer, not a new empty registry.
The approved initial root is a disjoint sibling of the worktree:
`/Users/antonborodin/Documents/Worktrees/Unciv-59b-artifacts`.
Existing catalog paths are never overwritten. Roots containing/inside the
checkout and symlink path components are refused.

For later **separately approved dev split plans**, provide their exact frozen
`--plan-sha256` and `--requests-sha256`. This does not change the recipe/source/
manifest/split authority or permit a nondev plan. A new catalog is published;
old catalogs are never amended. Reuse of existing artifacts requires an explicit
trusted parent via `--reuse-from PATH/catalog.json --reuse-sha256 SHA256`.
No rebuilding/overwriting of an already frozen identity is permitted.

## Signing gate: before the first APK assembly

The reference must be the actual original 1271048 APK with SHA-256
`91d9a900f8204880e590de7fb90f0fc15e2aa5cf5d94a3ae77364497d7b0541e`.
The builder verifies its signature, package, version and debuggable state. It
then runs the pinned source's debug `signingReport` with the same builder init
script and compares its single SHA-256 certificate fingerprint to the APK's.
Mismatch, missing/ambiguous signing information or invalid signature stops before
assembly. It does not change keys or propose an uninstall.

Catalog `signing` records both `reference_certificate_sha256` and
`builder_certificate_sha256`, plus the reference APK hash. Every produced APK's
actual signature is independently verified against that identity. The reference
APK itself is retained in the immutable catalog for the later restore operation.
This is an offline comparison against the exact APK provenance, not a claim that
the currently installed emulator package was queried; part (c) rechecks it.

## Independent builds, versionCodes and audits

Every assembly starts from a fresh `git archive` of the full source pin, never
from the working directory or a previously mutated variant. Builder code lives
outside that game snapshot. The external init script sets only versionCode and
disables texture packing. The command additionally excludes
`:android:texturePacker`. Pinned Kotlin/Gradle/build metadata source files are not
edited. The source already contains the packed atlases; their bytes are used
without regeneration.

A versioned build-identity SHA-256 binds source commit, full patch hash, exact
builder code-content hash, toolchain fingerprint and signing certificate.
VersionCode is:

`1800000000 + (integer(build_identity_sha256, 16) modulo 100000000)`.

All requested values and prior append-only reservations are checked under a
lock. A collision stops rather than probing another code or reassigning ranks.
1271048 is forbidden. Reservations are reusable only for the identical build
identity. A second registry binds frozen identities to their actual APK hashes;
publication/build locking prevents concurrent divergent artifacts under one
identity. Interrupted/corrupt version reservations fail closed and require
investigation. Publication uses an atomic prepared journal and atomic binding
writes: catalog rename/verification happens before freeze bindings. Startup
reconciles pending publications, verifying and rolling forward either the
retained stage or already-published target before recording/completing bindings.
Bindings must equal the identities/APK hashes read back from that verified
catalog. A rename or post-rename binding failure therefore remains recoverable.
Journal/completion identities hash the full publication transaction (including
stage/target paths), not just catalog content. Publishing identical catalog
bytes to a different output root cannot collide with an earlier completion or
poison subsequent recovery.

For each request, two fresh source directories are independently assembled with
the same allocated code, fixed debug signing, offline dependency artifacts and
locale/timezone. Build cache is disabled. Both passes verify:

- actual manifest/package/versionCode, debuggable flag and signature;
- exact packaged translation bytes;
- exact pinned nontranslation asset membership and bytes (respecting the pinned
  asset exclusion rules);
- every original tracked source file, allowing only the computed translation
  bytes to differ, plus full membership checks rejecting additional source or
  configuration inputs outside explicitly allowed generated build/cache/native
  output directories;
- all nontranslation APK payloads against the same-builder empty control.

The payload audit excludes only translation entries and standard signature
containers. It compares manifest semantics after normalizing **only the single
versionCode attribute**; DEX, native libraries, resources and all other payloads
must remain identical. A missing/extra asset or unexpected substantive drift
stops publication.

Toolchain identity includes the available cached dependency artifacts, Gradle
distribution files, JDK release/java/modules, pinned SDK platform/build-tools,
Python and host architecture. The full file-hash fingerprint is recorded and
checked again after builds. Builder script bytes are also rechecked before
publication. This is deliberately conservative: even an irrelevant dependency
cache-content change can require a new identity.

## `catalog_version: 1` contract

A successful command atomically publishes `catalog-<full catalog SHA-256>/`.
`catalog.json` is canonical; `catalog.sha256` contains its digest. Files and
directories become read-only. Read-only modes are an accidental-write guard,
not cryptographic authentication: part (c) must pin the catalog digest separately.
Operational clocks, absolute tool/output paths, commands, exits and logs are in
`runs/`, outside deterministic catalog content. Partial attempts stay under
`.staging/` and are never returned as successful catalogs.
The empty control is executed and verified first explicitly, independently of
hash/ID ordering; catalog entries still retain canonical request-ID order.
Parent reuse copies only hash-verified record references, never recursively
copies unreferenced files or follows source symlinks.

The index contains source/checker/tree identity, exact input snapshots, exact
builder-code snapshots and content identity, toolchain file-hash evidence,
signing gate, the original reference APK, original translations/source inventory,
installation semantics, control identity, and one record reference per request.
Every file reference has `path` (relative/contained), `sha256` and `bytes`.

Each batch record contains:

- `record_version: 1`, the exact original `request`, build identity and actual
  certificate fingerprint;
- checker-compatible `build`: decimal-string `version_code`, `apk_sha256`,
  `fork_commit`, `implementation_commit`; the last two remain the game-source pin,
  not the separate builder revision;
- APK file reference, complete English and German bytes (including unchanged
  files), each source hash, and full APK ZIP payload hashes;
- normalized manifest bytes and both source-audit receipts;
- two-pass SHA-256 values, measured APK equality, translation equality, changed
  ZIP-entry names and retained second-pass payload/manifest evidence;
- an explicit parent-catalog digest when an artifact was reused.

### Mandatory installation semantics

Hash-derived codes have arbitrary order. Switching batches **and restoring
1271048** can be downgrades. The contract therefore requires this debug-build
installation form in part (c):

```sh
adb install -r -d <apk>
```

The catalog's fixed `installation` object records `command_template`,
`requires_debuggable: true`, `downgrade_permission_required: true`,
`uninstall_allowed: false`, and `backup_restore_required_in_part_c: true`.
`emulator_downgrade_verified` and `app_data_preservation_verified` remain `false`.
A matching certificate is mandatory but is not a device-side data-preservation
proof. Part (c) must verify the installed signature, downgrade/replacement and
restoration behavior with the approved backup/restore. Failure must stop; an
uninstall/data wipe is not a fallback authorized by this contract.

## Reproducibility and reuse

Translation output is byte-deterministic for the same request/source/builder.
APK reproducibility is **measured**, never inferred. Each record says whether
the complete two-pass APK hashes match. A mismatch is retained as
`apk_byte_identical: false`, with the actual hashes and ZIP-entry comparison.
Substantive payload drift is still prohibited. Pure signature/container metadata
variation does not authorize a fabricated hash or replacement of a frozen APK.
Only the first verified APK is published; the second build supplies comparison
evidence, not another deployable artifact under the same identity.

For subsequent use, verify and return the existing frozen bytes:

```sh
python3 tools/l10n_apk_builder.py verify \
  --catalog "$CATALOG/catalog.json" --sha256 "$APPROVED_CATALOG_SHA256" \
  --sdk "$EXISTING_ANDROID_SDK" --jdk "$EXISTING_JDK21"

python3 tools/l10n_apk_builder.py reuse \
  --catalog "$CATALOG/catalog.json" --sha256 "$APPROVED_CATALOG_SHA256" \
  --sdk "$EXISTING_ANDROID_SDK" --jdk "$EXISTING_JDK21"
```

These commands rebuild nothing and perform no device operation. They verify
hashes/containment, input and request relationships, allocation identities,
signatures and manifest metadata, packaged translations, source/asset inventory,
control payload invariance and two-pass receipt consistency. Never trust just a
neighboring checksum file that could have been changed alongside the catalog.

## Checker-side part (c) reader

1. Require an approved catalog digest; verify exact canonical bytes and version.
2. Validate the original plan with the checker's existing authoritative loader.
   Match the catalog input hashes and exact request projection.
3. Match every request by full patch hash plus ID; reject missing, extra,
   duplicate or altered entries. Verify file paths, sizes and hashes before use.
4. Verify APK signature/package/debuggable/version metadata and APK-extracted
   translation bytes. Record separate builder provenance instead of changing the
   meaning of the source `implementation_commit`.
5. Produce per-artifact effective capture manifests using its actual `build`
   object. Do not rewrite the original 1271048 source-authority manifest or
   weaken the environment checker's version/hash checks.
6. Select the catalog's empty control and flagged singleton for calibration.
   Perform only the approved part-(c) device operations using the downgrade-
   permitting install form and verified backup/restore.
7. Keep D-050 calibration/visibility/non-interference and deterministic-split
   evidence separate. Built APKs and intended mutations are not human labels.

## Host verification

Test fixtures canonicalize their freshly created temporary root with
`Path.resolve(strict=True)` before passing fixture paths to the builder. No
particular TMPDIR or caller override is required, including for macOS's default
temporary directory. Deliberate symlinks created inside a fixture are still
passed unchanged to the production guard and must be rejected.

```sh
python3 -m unittest discover -s tools/tests -v
ruff check tools/l10n_apk_builder.py tools/l10n_builder_runtime.py tools/tests
ruff format --check tools/l10n_apk_builder.py tools/l10n_builder_runtime.py tools/tests
git diff --check
```

The simultaneous-pending-journal regression interrupts two distinct-root
publications after preparation, verifies both pending/completion filenames
against SHA-256 of the complete canonical state, then recovers twice and checks
that bindings and completed records are unchanged. Its synthetic transactions
are not APK evidence. Production path guards remain unchanged: they still reject
symlinked caller-supplied artifact/source paths. No device tests are run for this
part. Actual build/signature/reproducibility results belong in the issue-59b
Report, not an assumed success statement in this contract.

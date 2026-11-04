# Issue #59 part (b) — fork-side translation-only APK builder Report

**Status: part (b) implementation and host build verification complete locally.
Independent review passed; current production code matches the reviewed hashes
and the built catalog snapshots. Artifacts remain `built_unqualified` /
`production_ready: false`; this does not authorize part-(c) device operations.**

Publication of the code and this Report is now owner-authorized. The review PR
targets the fork's working branch `l10n-base-4.22.10` from
`feat/59-translation-apk-builder`; the original checkout stays on
`feat/40-l10n-screen-hook`. Related checker work:
`anton-borodin-inr/ai-localization-checker#59` (part b only; part c remains open).

## What changed

Implemented the approved dev-only translation APK builder in the isolated fork
worktree, branch `feat/59-translation-apk-builder`, from source
`b534a8ae67c390412984869c7a03b168d532c36c`. The pre-existing fork checkout/branch
was not moved. No game/hook/translation/Gradle source file was changed in the
worktree; every actual game build comes from a fresh archive of that pin.

The frozen checker authority is `e26914a4870d7755634cf547931f88c3e3093e5c`.
The approved initial requests/plan/recipe bytes are unchanged; their hashes and
the exact requests-version-1 contract are documented in
[l10n-apk-builder.md](l10n-apk-builder.md).

The builder provides strict handoff validation, byte-preserving literal edits,
source-input membership/hash checks, independent assemblies, pinned asset and
cross-control APK payload audits, matching-signature preflight, deterministic
collision-stop version allocation, and immutable catalog v1 with explicit
verification/reuse. Registry authority is shared through the fork's Git common
directory, not reset by an output-directory change. Prepared publication
transactions can recover before or after rename; transaction identity is
separate from catalog content identity.

### Files added

- `tools/.gitignore` — exclude generated Python bytecode caches.
- `tools/l10n_apk_builder.py` — input/patch/hash/allocation/path/signature guards.
- `tools/l10n_builder_runtime.py` — host execution, two-pass audits, catalog,
  verified-reference reuse, registry and recoverable publication.
- `tools/l10n-builder.init.gradle` — external versionCode override and disabled
  texture packing; no edit to the pinned Gradle/build metadata files.
- `tools/tests/test_l10n_apk_builder.py` — core contract and safety tests.
- `tools/tests/test_l10n_builder_runtime.py` — runtime/security/recovery regressions.
- `docs/l10n-apk-builder.md` — catalog version 1 and checker part-(c) reader contract.
- `docs/issue-59b-report.md` — this Report, included in the authorized PR description.

## Signing and installation contract

The actual original APK was located through committed issue-47 host provenance,
not a directory-wide search or a device query. Its bytes match SHA-256
`91d9a900f8204880e590de7fb90f0fc15e2aa5cf5d94a3ae77364497d7b0541e` and versionCode
1271048. Offline APK signature verification and the pinned debug signingReport
both gave certificate SHA-256:

`b4752f8cee80842f55883ca2a9b62ca75c898df5a1b7cefd63959ba1b8fecb99`.

The production builder repeats that comparison before APK assembly and verifies
every actual output signature. Both reference and builder certificate
fingerprints are stored in the catalog. No private key/password was printed.
The currently installed emulator package was not queried; part (c) must recheck it.

Catalog v1 explicitly requires debug replacement/downgrade installation using
`adb install -r -d <apk>`, including when restoring 1271048. Uninstall is not an
authorized fallback. Downgrade and app-data-preservation verification flags
remain false; backup/restore and emulator verification belong to part (c).

## Final catalog

Primary final-revision catalog selected for the part-(c) handoff. Reading this
catalog does not authorize device operations:

`/Users/antonborodin/Documents/Worktrees/Unciv-59b-artifacts/catalog-790b21921ca3d4988b6257a6da235817c8b8407b2f029871c74a05598efaf354/catalog.json`

Catalog SHA-256:
`790b21921ca3d4988b6257a6da235817c8b8407b2f029871c74a05598efaf354`.

Builder content SHA-256:
`e9ec71c5bb0d8816bfa84c1eab911e6b7a0d3300d0bc54ae2b8357a058478086`.

| Request | Role | versionCode | APK SHA-256 (both independent passes) |
|---|---|---:|---|
| `batch-37517e5f3dc66819` | Empty-patch control | `1888080431` | `fdd5274d6f02560fd10a241040a1a85ce7c3204927862d06d5409f7103001829` |
| `batch-4334cc699af95644` | EN-help calibration | `1808820362` | `c10423131eea3c82c4cf75e507f67cac4517cfcf0dd123e909bd03c665779423` |
| `batch-99984bb52ea65c2f` | Mutation | `1844475040` | `1e62957e0344f2317ee34e5d541be6c195a348f247db8a471d177713a257e631` |
| `batch-e45e29bd1f0f9039` | Mutation | `1872129848` | `d5a433ca6093b1b8e0769a456ba544960b39d6097e7814d6ef9355bbd3a36000` |
| `batch-e46946a0afe9ec07` | Mutation | `1895590174` | `4e2894a62be01e225e554cc4bd7a8b13b0bd0c79cb50daf45f15b2b2c6a354e4` |

There are five generated artifacts, plus the unchanged original reference APK.
Every record retains both complete translation files with byte counts, original
and output hashes, APK hash/version/signature, source audit and two-pass evidence.
The empty control uses this exact builder and source, not the older stock APK.
All allocated codes are distinct and in the approved reserved range; none is
1271048. Existing allocations and frozen bindings were not reset.

Translation bytes are deterministic under the exact handoff/source. All five
APKs were also **observed byte-identical in two independent local builds** with
the recorded toolchain, with no differing ZIP payload entries. Cross-machine or
cross-toolchain APK reproducibility is not promised. Frozen identities reuse
their original APK bytes instead of regenerating interchangeable replacements.

Part (c) must select this explicit digest, not a newest-directory glob, verify
the contained hash graph/native signatures, then derive per-artifact effective
capture identities without rewriting the original checker fixture manifest.
The reader procedure and downgrade/unqualified-state gates are in the contract.

## How it was verified

- The real five-request handoff passes exact hash/schema/dev/recipe projection
  validation. All 32 original-RHS/absent-key conditions match the source pin.
- Core features were exercised through RED/GREEN host tests. Review regressions
  reproduced added-source acceptance, malformed nested schema errors,
  unauthenticated-copy/recovery/ordering gaps and the cross-root completion
  collision before their fixes. SDK manifest parsing was corrected against
  actual `aapt2` output and tested for both namespace spellings and missing/
  duplicate versionCode attributes.
- **25 Python host tests passed** for the frozen producer revision, with no skips.
  Ruff check and formatting passed. These tests' synthetic ZIP/transaction
  fixtures are not real APK, device or signing evidence.
  That historical revision required a physical TMPDIR override: without it,
  macOS aliases caused fixture failures. The publication follow-up below fixes
  only fixture-root handling; production guards were not relaxed.
- The actual frozen handoff also rejected four adversarial cases after dependent
  patch/file hashes were recomputed: a nontranslation path, renamed source key,
  nondev selection and an injected command field. The host-only audit is
  `Unciv-59b-artifacts/verification/contract-negative.json`.
- `./gradlew --offline --no-daemon --no-configuration-cache --no-build-cache
  --max-workers=2 --init-script tools/l10n-builder.init.gradle :tests:test
  --rerun :android:lintDebug -x :android:texturePacker` exited 0 after the final
  builder change. Parsed JUnit: **937 passed, 20 skipped, 0 failures/errors**;
  skip names/reasons are preserved in
  `Unciv-59b-artifacts/verification/host-gates.json`. Android lint: **0 errors,
  16 warnings**. No existing Kotlin skip or lint rule was weakened.
- Independent review initially found six concrete runtime gaps. A follow-up
  confirmed those were addressed, then reproduced the transaction/content-ID
  completion collision. The final independent review **passed** with no blocking
  correctness/security findings. It ran all 25 host tests and Ruff, verified the
  transaction-identity and SDK parser fixes, and proved each regression fails
  when its legacy implementation is restored in memory. The parent's hash
  comparison confirmed the review's three production hashes exactly match both
  current code and the final catalog snapshots. The review did not inspect real
  APKs; real build/signing/reproducibility/replay evidence is the separately
  executed verification below. The verdict is preserved at
  `Unciv-59b-artifacts/verification/final-independent-review.json`.
  The reviewer's nonblocking simultaneous-pending-journal suggestion is now
  persisted by the publication follow-up below. No production behavior fix is
  outstanding; no author self-approval or merge is claimed.
- Final run `1793805849369572000` completed **ten actual APK assemblies**, each
  in a distinct fresh source tree. All command receipts exited 0, each pass
  audited 4,219 pinned source files, both English/German files matched expected
  bytes, all packaged assets matched their allowed source bytes and every
  nontranslation/nonsigning payload plus normalized manifest matched control.
- Actual SDK-backed `verify` and `reuse` CLI entry points both returned
  `verified: true` for the primary digest with five artifacts and
  `device_verification: not_run_part_c`. Independent graph readback verified all
  **54 file references / 56 catalog files**, no unreferenced files or symlinks,
  current production-code/snapshot equality and all five exact frozen bindings.
  See `Unciv-59b-artifacts/verification/final-catalog-audit.json`.
- A real `build --reuse-from ... --reuse-sha256 ...` replay then reused all five
  APKs with **zero new APK assemblies**. The derived reuse catalog digest is
  `ac381eed8fb9f85ee3f2e1843a6e1346936cded798f97afcc96b6d03b41d0d52`.
  Its verified references were copied and the identical catalog republished
  through the real transaction API into a second output root, with native
  verification before/after publication. Both independent completion records
  exist, recovery leaves zero pending journals, and the original frozen bindings
  remain byte-identical. This exercises the review's collision case with real
  artifacts, not only synthetic fixtures. These are verification copies, not
  additional mutations or the selected handoff. See
  `Unciv-59b-artifacts/verification/real-reuse-and-republication.json`.
- All eight added files passed whitespace checks; added Python AST scanning
  found no eval/exec, shell=True, os.system or unsafe pickle execution.
  At the original build freeze, worktree HEAD was the source pin and these eight
  new files were untracked. Publication commits do not alter those historical
  provenance fields or the archived producer snapshot.
  Original fork checkout remains at `c66d109fbe574cb3a9824fe3e346f0b0bd0ae4b2`,
  branch `feat/40-l10n-screen-hook`.

## Publication follow-up: portable fixtures and recovery regression

- Every test-owned temporary root is canonicalized inside the test using
  `Path.resolve(strict=True)`. Only the trusted newly created fixture root is
  resolved; deliberately symlinked descendants still reach the unchanged
  production rejection path. There is no global TMPDIR mutation or guard patch.
- **26 Python host tests pass**, with no skips, under both an unset
  TMPDIR/TMP/TEMP environment and macOS's native user temporary directory from
  `getconf DARWIN_USER_TEMP_DIR`. The whole suite also passes when TMPDIR points
  to a symlink alias of an owned temporary directory. Ruff check/format pass.
- The persisted regression prepares two same-content publications under distinct
  roots, interrupts both immediately after journal preparation, checks exact
  full-state SHA-256 pending filenames and canonical contents, then recovers
  twice. It checks completion filenames/contents, unchanged frozen bindings,
  no pending journals and idempotent second recovery.
- Scoped in-memory restorations of catalog-keyed pending and completion naming
  each caused **one assertion failure, zero harness errors** in the new test.
  The unchanged producer passes it. These probes do not edit production files.
- Only tests and documentation changed after the freeze. The three producer
  files are byte-identical to the catalog snapshots, so builder content identity
  remains `e9ec71c5bb0d8816bfa84c1eab911e6b7a0d3300d0bc54ae2b8357a058478086`.
  Test/docs bytes and Git commit/provenance metadata are not inputs to that
  identity. **The catalog does not need rebuilding and no rebuild was run.**
- A pre-publication read-only snapshot covers all 56 files in catalog
  `790b21921ca3d4988b6257a6da235817c8b8407b2f029871c74a05598efaf354`;
  publication verification compares every byte hash and the full file set.
  The catalog, its original provenance and its APKs are not rewritten.
- PR base `l10n-base-4.22.10` is verified at
  `de29574fb6dc057b20888d9c8773f735bea520f3`, the merge of the pinned source work.
  No game/hook/build version changes are introduced by this PR. The original
  fork checkout is not switched, rebased, reset or advanced.

## Preserved earlier attempts

The first signingReport invocation lacked the complete default Gradle cache;
Gradle's wrapper fetched its pinned distribution before Gradle's offline mode,
then offline plugin resolution failed. The existing complete issue-47 cache was
used instead, and production runs use an isolated copy. No dependency version,
SDK package or game-source change was introduced.

The first production preflight stopped before assembly because `aapt2` used the
full Android namespace URI; the old parser recognized only its short prefix.
The failure/logs are retained, not reported as a successful build.

A preceding builder revision then completed **ten actual APK assemblies** for
all five requests, with byte-identical pairs and all source/asset/signature/
payload audits passing. Its catalog digest is
`fa01a89f4d18e6d9658562cb9fa94295ca2f4b29f912aa83a2c4368e83647eda`.
That catalog is preserved unchanged but **superseded before part-(c) handoff**:
the final publication recovery fix changes the frozen builder identity.
The final revision therefore receives new allocations and rebuilds all five
artifacts twice rather than relabeling old APKs. The historical record is
`Unciv-59b-artifacts/verification/superseded-first-catalog.json`.

## Deviations, decisions and remaining work

- During planning, an initial parent-directory listing exposed the excluded
  directory's name; it was not entered and no contents were enumerated/read.
  After the owner's correction all discovery used known repository/artifact
  paths. The procedural skill was updated accordingly.
- The approved input freeze, reserved version range/collision-stop policy,
  disabled texture packing, actual two-pass comparisons and frozen-byte reuse
  policy are retained. Signing continuity and downgrade semantics include the
  owner's two explicit additions. No Accepted checker decision was edited.
- Builder identity is pinned by exact archived code-content hashes, with Git
  HEAD/dirty provenance recorded honestly. The original build stage did not
  authorize commit/push/PR. The subsequent owner authorization covers conventional
  commits on the feature branch, a fork push and a review PR, not merge or device
  work. This changes publication provenance, not the frozen producer identity.
- A directory-mode audit observed 0555 modes reset to 0700 between tool calls,
  outside the builder. Native stat and in-process tracing distinguished that
  metadata change from SDK verification: the latter preserved sealed modes and
  every payload byte/hash. Directories were resealed; immutable approval depends
  on the explicit catalog hash graph/freeze registry, not mutable OS mode bits.
- This Report accompanies the authorized fork PR description. It does not close
  the checker's full issue #59 or authorize its device/provider stages.
- Final device authorization, installation/downgrade behavior, backup/restore,
  calibration, runtime visibility, pixel/dump isolation, split rate and labels
  remain part (c). APK bit reproducibility does not establish raster isolation.

## API usage and excluded operations

**0 project provider/detector/baseline/OCR runs; USD 0.** No APK installation,
uninstall, emulator operation, capture, label creation, metric/golden change,
or catalog rebuild was performed in the publication follow-up. Authorized Git
publication is separate from those excluded operations. The final artifact
handoff remains unqualified for device/production use until part (c)'s gates pass.

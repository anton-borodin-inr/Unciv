"""Regression tests for runtime boundaries; all payloads here are synthetic."""

import copy
import hashlib
import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
c = importlib.import_module("l10n_apk_builder")
runtime = importlib.import_module("l10n_builder_runtime")


class RuntimeRegressionTests(unittest.TestCase):
    def test_sdk_full_namespace_manifest_normalizes_only_version_code(self):
        self.assertTrue(
            callable(getattr(runtime, "normalize_manifest", None)),
            "SDK manifest normalizer missing",
        )
        for namespace in ("android:", "http://schemas.android.com/apk/res/android:"):
            text = f'  A: {namespace}versionCode(0x0101021b)=1271048\n  A: {namespace}versionName(0x0101021c)="4.22.10"\n'
            expected = text.replace("=1271048", "=<allocated-versionCode>")
            self.assertEqual(runtime.normalize_manifest(text), expected)
            with self.assertRaises(c.BuilderError):
                runtime.normalize_manifest(text + text)
        with self.assertRaises(c.BuilderError):
            runtime.normalize_manifest("no versionCode attribute")

    def test_unexpected_source_membership_fails_but_generated_outputs_are_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            root = Path(folder)
            (root / "core/src").mkdir(parents=True)
            (root / "core/src/Original.kt").write_bytes(b"pinned")
            pin = runtime.inventory(root)
            (root / "core/build/classes").mkdir(parents=True)
            (root / "core/build/classes/Generated.class").write_bytes(
                b"generated test output"
            )
            runtime.audit_source(root, pin, {})
            (root / "core/src/Injected.kt").write_bytes(b"unexpected compilation input")
            with self.assertRaises(c.BuilderError):
                runtime.audit_source(root, pin, {})

    def test_reuse_copies_only_verified_references_and_never_follows_symlinks(self):
        self.assertTrue(
            callable(getattr(runtime, "copy_verified_artifact", None)),
            "Verified-reference copy missing",
        )
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            parent, destination = Path(folder) / "parent", Path(folder) / "new"
            parent.mkdir()
            destination.mkdir()
            prefix = "batches/batch-test/"
            reference = runtime.write_payload(
                parent, prefix + "approved.bin", b"approved synthetic bytes"
            )
            sentinel = Path(folder) / "sentinel"
            sentinel.write_bytes(b"must never be copied")
            (parent / prefix / "unreferenced-link.bin").symlink_to(sentinel)
            record = {"apk": reference, "nested": {"file": copy.deepcopy(reference)}}
            runtime.copy_verified_artifact(parent, destination, "batch-test", record)
            self.assertEqual(
                [p.name for p in (destination / prefix).iterdir()], ["approved.bin"]
            )
            (parent / prefix / "approved.bin").unlink()
            (parent / prefix / "approved.bin").symlink_to(sentinel)
            with self.assertRaises(c.BuilderError):
                runtime.copy_verified_artifact(
                    parent, destination, "batch-test", record
                )

    def test_control_execution_does_not_depend_on_hash_order(self):
        self.assertTrue(
            callable(getattr(runtime, "execution_order", None)),
            "Explicit control ordering missing",
        )
        canonical = [
            {"id": "batch-1", "role": "mutation"},
            {"id": "batch-3", "role": "clean"},
            {"id": "batch-4", "role": "calibration"},
        ]
        self.assertEqual(
            [r["id"] for r in runtime.execution_order(canonical)],
            ["batch-3", "batch-1", "batch-4"],
        )
        self.assertEqual(
            [r["id"] for r in canonical], ["batch-1", "batch-3", "batch-4"]
        )

    def test_registry_is_anchored_to_git_common_dir_not_output_folder(self):
        self.assertTrue(
            callable(getattr(runtime, "registry_directory", None)),
            "Persistent registry missing",
        )
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            repo = Path(folder) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(repo)], check=True, capture_output=True
            )
            first = runtime.registry_directory(repo)
            output_a, output_b = Path(folder) / "output-a", Path(folder) / "output-b"
            output_a.mkdir()
            output_b.mkdir()
            c.reserve_versions(first / "allocations", {"a": "0" * 64})
            second = runtime.registry_directory(repo)
            self.assertEqual(first, second)
            with self.assertRaises(c.BuilderError):
                c.reserve_versions(second / "allocations", {"b": f"{100_000_000:064x}"})
            self.assertFalse(first.is_relative_to(output_a))
            self.assertFalse(first.is_relative_to(output_b))

    def test_registry_is_shared_by_real_linked_worktrees(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            repo, linked = Path(folder) / "repo", Path(folder) / "linked"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(repo)], check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "add",
                    "--orphan",
                    "-b",
                    "linked",
                    "--quiet",
                    str(linked),
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                runtime.registry_directory(repo), runtime.registry_directory(linked)
            )

    def test_identical_catalog_in_distinct_output_roots_has_distinct_transactions(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            root = Path(folder)
            repo, registry = root / "repo", root / "registry"
            repo.mkdir()
            registry.mkdir()
            raw = b'{"synthetic_transaction_test":true}\n'
            checksum = c.digest(raw)
            bindings = [
                {
                    "build_identity_sha256": "a" * 64,
                    "apk_sha256": "b" * 64,
                    "catalog_sha256": checksum,
                }
            ]
            targets = []

            def verify(path, sha):
                self.assertEqual(c.digest(path.read_bytes()), sha)
                return {"a" * 64: "b" * 64}

            try:
                for name in ("first", "second"):
                    stage = root / name / ".staging/run"
                    stage.mkdir(parents=True)
                    (stage / "catalog.json").write_bytes(raw)
                    target = root / name / ("catalog-" + checksum)
                    targets.append(target)
                    try:
                        runtime.publish_transaction(
                            registry, repo, stage, target, checksum, bindings, verify
                        )
                    except c.BuilderError as exc:
                        self.fail(
                            f"A different output root must not poison publication recovery: {exc}"
                        )
                runtime.recover_publications(registry, repo, verify)
                self.assertFalse(
                    list((registry / "publications").glob("*.pending.json"))
                )
                self.assertEqual(
                    len(list((registry / "publications").glob("*.complete.json"))), 2
                )
            finally:
                for target in targets:
                    if target.exists():
                        target.chmod(0o755)
                        (target / "catalog.json").chmod(0o644)

    def test_simultaneous_pending_journals_keep_full_state_identity_and_recover_twice(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(strict=True)
            repo, registry = root / "repo", root / "registry"
            repo.mkdir()
            registry.mkdir()
            raw = b'{"synthetic_transaction_test":true}\n'
            checksum = hashlib.sha256(raw).hexdigest()
            bindings = [
                {
                    "build_identity_sha256": "a" * 64,
                    "apk_sha256": "b" * 64,
                    "catalog_sha256": checksum,
                }
            ]
            expected_states = {}
            targets = []
            verify_calls = []

            def verify(path, sha):
                self.assertEqual(sha, checksum)
                self.assertEqual(path.read_bytes(), raw)
                verify_calls.append(path)
                return {"a" * 64: "b" * 64}

            try:
                for name in ("first", "second"):
                    stage = root / name / ".staging/run"
                    stage.mkdir(parents=True)
                    (stage / "catalog.json").write_bytes(raw)
                    target = root / name / ("catalog-" + checksum)
                    targets.append(target)
                    state = {
                        "publication_version": 1,
                        "stage": str(stage),
                        "target": str(target),
                        "catalog_sha256": checksum,
                        "bindings": bindings,
                    }
                    # Compute the expectation independently of the producer's digest helper.
                    transaction_id = hashlib.sha256(
                        c.canonical_bytes(state)
                    ).hexdigest()
                    expected_states[transaction_id] = state
                    with (
                        mock.patch.object(
                            runtime,
                            "finish_publication",
                            side_effect=OSError(
                                "synthetic prepared-journal interruption"
                            ),
                        ),
                        self.assertRaisesRegex(
                            OSError, "prepared-journal interruption"
                        ),
                    ):
                        runtime.publish_transaction(
                            registry, repo, stage, target, checksum, bindings, verify
                        )
                    self.assertTrue(stage.is_dir())
                    self.assertFalse(target.exists())
                    pending = list((registry / "publications").glob("*.pending.json"))
                    self.assertEqual(
                        {p.name for p in pending},
                        {identity + ".pending.json" for identity in expected_states},
                    )
                    for journal in pending:
                        identity = journal.name.removesuffix(".pending.json")
                        self.assertEqual(
                            journal.read_bytes(),
                            c.canonical_bytes(expected_states[identity]),
                        )
                self.assertEqual(len(expected_states), 2)
                self.assertFalse((registry / "frozen-artifacts").exists())
                self.assertFalse(
                    list((registry / "publications").glob("*.complete.json"))
                )

                recovered_snapshot = None
                recovered_verify_calls = None
                for _ in range(2):
                    try:
                        runtime.recover_publications(registry, repo, verify)
                    except c.BuilderError as exc:
                        self.fail(
                            f"Distinct prepared transactions must recover independently: {exc}"
                        )
                    self.assertFalse(
                        list((registry / "publications").glob("*.pending.json"))
                    )
                    completed = list(
                        (registry / "publications").glob("*.complete.json")
                    )
                    self.assertEqual(
                        {p.name for p in completed},
                        {identity + ".complete.json" for identity in expected_states},
                    )
                    for completion in completed:
                        identity = completion.name.removesuffix(".complete.json")
                        self.assertEqual(
                            completion.read_bytes(),
                            c.canonical_bytes(expected_states[identity]),
                        )
                    for state in expected_states.values():
                        self.assertFalse(Path(state["stage"]).exists())
                        self.assertEqual(
                            (Path(state["target"]) / "catalog.json").read_bytes(), raw
                        )
                    frozen = list((registry / "frozen-artifacts").glob("*.json"))
                    self.assertEqual(len(frozen), 1)
                    self.assertEqual(
                        frozen[0].read_bytes(), c.canonical_bytes(bindings[0])
                    )
                    snapshot = {
                        p.relative_to(registry).as_posix(): p.read_bytes()
                        for p in registry.rglob("*")
                        if p.is_file()
                    }
                    if recovered_snapshot is None:
                        recovered_snapshot = snapshot
                        recovered_verify_calls = len(verify_calls)
                    else:
                        self.assertEqual(snapshot, recovered_snapshot)
                        self.assertEqual(len(verify_calls), recovered_verify_calls)
            finally:
                for target in targets:
                    if target.exists():
                        target.chmod(0o755)
                        (target / "catalog.json").chmod(0o644)

    def test_rename_failure_leaves_recoverable_journal_not_orphaned_freeze(self):
        self.assertTrue(
            callable(getattr(runtime, "publish_transaction", None)),
            "Recoverable publication missing",
        )
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            root = Path(folder)
            repo, output, registry = root / "repo", root / "output", root / "registry"
            for path in (repo, output, registry):
                path.mkdir()
            stage = output / ".staging/run"
            stage.mkdir(parents=True)
            raw = b'{"synthetic_transaction_test":true}\n'
            (stage / "catalog.json").write_bytes(raw)
            checksum = c.digest(raw)
            target = output / ("catalog-" + checksum)
            bindings = [
                {
                    "build_identity_sha256": "a" * 64,
                    "apk_sha256": "b" * 64,
                    "catalog_sha256": checksum,
                }
            ]

            def verify(path, sha):
                self.assertEqual(c.digest(path.read_bytes()), sha)
                return {"a" * 64: "b" * 64}

            rename = Path.rename

            def fail_rename(path, destination):
                if path == stage:
                    raise OSError("synthetic rename failure")
                return rename(path, destination)

            with (
                mock.patch.object(Path, "rename", fail_rename),
                self.assertRaises(OSError),
            ):
                runtime.publish_transaction(
                    registry, repo, stage, target, checksum, bindings, verify
                )
            self.assertFalse(target.exists())
            self.assertFalse(
                (registry / "frozen-artifacts" / ("a" * 64 + ".json")).exists()
            )
            self.assertEqual(
                len(list((registry / "publications").glob("*.pending.json"))), 1
            )
            runtime.recover_publications(registry, repo, verify)
            self.assertTrue(target.is_dir())
            self.assertTrue(
                (registry / "frozen-artifacts" / ("a" * 64 + ".json")).is_file()
            )
            self.assertFalse(list((registry / "publications").glob("*.pending.json")))
            runtime.recover_publications(registry, repo, verify)
            # Restore writable fixture modes for TemporaryDirectory cleanup.
            target.chmod(0o755)
            (target / "catalog.json").chmod(0o644)

    def test_freeze_bindings_must_match_the_verified_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(strict=True)
            repo, registry, stage = (
                root / "repo",
                root / "registry",
                root / "output/.staging/run",
            )
            repo.mkdir()
            registry.mkdir()
            stage.mkdir(parents=True)
            raw = b'{"synthetic_transaction_test":true}\n'
            (stage / "catalog.json").write_bytes(raw)
            checksum = c.digest(raw)
            target = root / "output" / ("catalog-" + checksum)
            bindings = [
                {
                    "build_identity_sha256": "a" * 64,
                    "apk_sha256": "b" * 64,
                    "catalog_sha256": checksum,
                }
            ]
            try:
                with self.assertRaises(c.BuilderError):
                    runtime.publish_transaction(
                        registry,
                        repo,
                        stage,
                        target,
                        checksum,
                        bindings,
                        lambda path, sha: {"c" * 64: "d" * 64},
                    )
                self.assertFalse(target.exists())
            finally:
                if target.exists():
                    target.chmod(0o755)
                    (target / "catalog.json").chmod(0o644)

    def test_publication_recovers_after_rename_before_atomic_freeze(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(strict=True)
            repo, registry, stage = (
                root / "repo",
                root / "registry",
                root / "output/.staging/run",
            )
            repo.mkdir()
            registry.mkdir()
            stage.mkdir(parents=True)
            raw = b'{"synthetic_transaction_test":true}\n'
            (stage / "catalog.json").write_bytes(raw)
            checksum = c.digest(raw)
            target = root / "output" / ("catalog-" + checksum)
            bindings = [
                {
                    "build_identity_sha256": "a" * 64,
                    "apk_sha256": "b" * 64,
                    "catalog_sha256": checksum,
                }
            ]

            def verify(path, sha):
                self.assertEqual(c.digest(path.read_bytes()), sha)
                return {"a" * 64: "b" * 64}

            atomic = runtime.atomic_json

            def fail_freeze(path, value):
                if "frozen-artifacts" in path.parts:
                    raise OSError("synthetic interrupted freeze")
                return atomic(path, value)

            with (
                mock.patch.object(runtime, "atomic_json", fail_freeze),
                self.assertRaises(OSError),
            ):
                runtime.publish_transaction(
                    registry, repo, stage, target, checksum, bindings, verify
                )
            self.assertTrue(target.exists())
            runtime.recover_publications(registry, repo, verify)
            self.assertFalse(list((registry / "publications").glob("*.pending.json")))
            self.assertTrue(
                (registry / "frozen-artifacts" / ("a" * 64 + ".json")).exists()
            )
            target.chmod(0o755)
            (target / "catalog.json").chmod(0o644)

    def test_malformed_nested_catalog_is_a_controlled_error_before_tool_calls(self):
        catalog: dict = {
            key: {}
            for key in (
                "inputs",
                "builder",
                "toolchain",
                "signing",
                "reference",
                "source_inventory",
                "source_assets",
                "source_translations",
            )
        }
        catalog.update(
            {
                "catalog_version": 1,
                "source_commit": c.SOURCE_COMMIT,
                "source_tree": "0" * 40,
                "checker_commit": c.CHECKER_COMMIT,
                "installation": runtime.INSTALLATION,
                "empty_control_id": "batch-test",
                "artifacts": [],
                "inputs": [],
                "qualification": {
                    "stage": "built_unqualified",
                    "production_ready": False,
                    "device_verification": "not_run_part_c",
                    "labels_created": False,
                },
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            path = Path(folder) / "catalog.json"
            raw = c.canonical_bytes(catalog)
            path.write_bytes(raw)
            with (
                mock.patch.object(
                    runtime,
                    "apk_tools",
                    side_effect=AssertionError("SDK tools must not run"),
                ),
                self.assertRaises(c.BuilderError),
            ):
                runtime.verify_catalog(
                    path,
                    c.digest(raw),
                    Path(folder),
                    Path(folder),
                    Path(folder) / "logs",
                )

    def test_file_reference_hash_tampering_and_path_escape_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(strict=True)
            reference = runtime.write_payload(root, "approved.txt", b"before")
            self.assertEqual(runtime.read_ref(root, reference), b"before")
            (root / "approved.txt").write_bytes(b"after")
            with self.assertRaises(c.BuilderError):
                runtime.read_ref(root, reference)
            with self.assertRaises(c.BuilderError):
                runtime.read_ref(root, {**reference, "path": "../escape"})


if __name__ == "__main__":
    unittest.main()

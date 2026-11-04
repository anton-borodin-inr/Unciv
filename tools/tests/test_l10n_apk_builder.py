"""Host-only builder tests. Synthetic data is never a build or device result."""

import copy
import importlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def api():
    assert importlib.util.find_spec("l10n_apk_builder") is not None, (
        "Builder is missing"
    )
    return importlib.import_module("l10n_apk_builder")


class CanonicalContractTests(unittest.TestCase):
    def test_checker_canonical_bytes_are_preserved(self):
        builder = api()
        self.assertEqual(
            builder.canonical_bytes({"z": 1.0, "a": "Erhöhen"}),
            '{"a":"Erhöhen","z":1}\n'.encode(),
        )
        with self.assertRaises(ValueError):
            builder.canonical_bytes({"bad": float("nan")})


def request_bundle():
    builder = api()
    patches = [
        {
            "file": builder.TRANSLATIONS[0],
            "operation": "replace",
            "key": "?",
            "original": "",
            "replacement": "Help",
        }
    ]
    requests = []
    for patch_set, role in [([], "clean"), (patches, "calibration")]:
        checksum = builder.digest(builder.canonical_bytes(patch_set))
        requests.append(
            {
                "id": "batch-" + checksum[:16],
                "patch_set_sha256": checksum,
                "patches": patch_set,
                "role": role,
                "calibration": role == "calibration",
            }
        )
    return {
        "requests_version": 1,
        "source_commit": builder.SOURCE_COMMIT,
        "plan_sha256": builder.FROZEN["plan"],
        "recipe_version": "stage3-v1",
        "requests": sorted(requests, key=lambda r: r["id"]),
        "constraints": copy.deepcopy(builder.CONSTRAINTS),
    }


class HandoffTests(unittest.TestCase):
    def test_only_frozen_dev_recipes_can_be_projected(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "project_requests", None)),
            "Handoff validator missing",
        )
        patch = request_bundle()["requests"][1]["patches"][0]
        recipe = {
            "id": "help-overflow-en",
            "language": "en",
            "key": "?",
            "operation": "replace",
            "original": "",
            "replacement": "Help",
            "targets": [{"screen": "options-gameplay"}],
        }
        batch = {
            "id": request_bundle()["requests"][1]["id"],
            "patch_set_sha256": request_bundle()["requests"][1]["patch_set_sha256"],
            "patches": [patch],
            "recipe_ids": [recipe["id"]],
            "screenshots": [{"screen": "options-gameplay", "language": "en"}],
        }
        clean = request_bundle()["requests"][0]
        plan = {
            "fork_commit": builder.SOURCE_COMMIT,
            "recipe_version": "stage3-v1",
            "screen_ids": ["options-gameplay"],
            "mutations": {
                recipe["id"]: {
                    "recipe": recipe,
                    "patch": patch,
                    "occurrences": [{"screen": "options-gameplay", "language": "en"}],
                }
            },
            "batches": [batch],
            "clean_build": {
                "id": clean["id"],
                "patch_set_sha256": clean["patch_set_sha256"],
                "patches": [],
            },
            "calibration": {"mutation_recipe_id": recipe["id"]},
        }
        projected = builder.project_requests(
            plan, {recipe["id"]: recipe}, {"options-gameplay"}
        )
        self.assertEqual(len(projected["requests"]), 2)
        self.assertTrue(projected["requests"][1]["calibration"])
        for corrupt in ["different-key", "excluded-screen"]:
            bad = copy.deepcopy(plan)
            if corrupt == "different-key":
                bad["mutations"][recipe["id"]]["recipe"]["key"] = "not-approved"
            else:
                bad["batches"][0]["screenshots"][0]["screen"] = "unapproved"
            with self.subTest(corrupt=corrupt), self.assertRaises(builder.BuilderError):
                builder.project_requests(
                    bad, {recipe["id"]: recipe}, {"options-gameplay"}
                )


class AllocationAndSigningTests(unittest.TestCase):
    def test_version_codes_are_stable_reserved_and_collisions_stop(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "reserve_versions", None)),
            "Version allocator missing",
        )
        first, colliding = "0" * 64, f"{100_000_000:064x}"
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            root = Path(folder)
            expected = {"a": "1800000000"}
            self.assertEqual(builder.reserve_versions(root, {"a": first}), expected)
            self.assertEqual(builder.reserve_versions(root, {"a": first}), expected)
            with self.assertRaises(builder.BuilderError):
                builder.reserve_versions(root, {"b": colliding})
            self.assertEqual(len(list(root.glob("*.json"))), 1)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda _: builder.reserve_versions(Path(folder), {"a": first}),
                        range(2),
                    )
                )
            self.assertEqual(results, [expected, expected])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            with self.assertRaises(builder.BuilderError):
                builder.reserve_versions(Path(folder), {"a": first, "b": colliding})
            self.assertFalse(list(Path(folder).glob("*.json")))

    def test_signing_mismatch_stops_before_build_and_records_both_fingerprints(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "signing_gate", None)), "Signing gate missing"
        )
        sha = "b" * 64
        self.assertEqual(
            builder.signing_gate(sha, sha),
            {"reference_certificate_sha256": sha, "builder_certificate_sha256": sha},
        )
        for reference, actual in [(sha, "a" * 64), ("", sha), (sha, None)]:
            with (
                self.subTest(reference=reference, actual=actual),
                self.assertRaises(builder.BuilderError),
            ):
                builder.signing_gate(reference, actual)


class BuildBoundaryTests(unittest.TestCase):
    def test_cli_has_no_device_command_and_rejects_incomplete_handoff(self):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "l10n_apk_builder.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("verify", result.stdout, "Executable builder CLI is missing")
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "l10n_apk_builder.py"),
                "install",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_output_must_be_disjoint_and_symlink_free(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "output_root", None)), "Output guard missing"
        )
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            parent = Path(folder)
            repo = parent / "repo"
            repo.mkdir()
            self.assertEqual(
                builder.output_root(parent / "artifacts", repo), parent / "artifacts"
            )
            for path in (parent, repo, repo / "inside"):
                with self.subTest(path=path), self.assertRaises(builder.BuilderError):
                    builder.output_root(path, repo)
            link = parent / "link"
            link.symlink_to(repo, target_is_directory=True)
            with self.assertRaises(builder.BuilderError):
                builder.output_root(link / "output", repo)

    def test_zip_assets_are_verified_against_pin_and_no_extra_payload_is_allowed(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "audit_zip", None)), "APK payload auditor missing"
        )
        originals = {path: b"? = ?\n" for path in builder.TRANSLATIONS}
        source_assets = {"assets/texture.atlas": builder.digest(b"pinned-atlas")}
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve(strict=True)
            path = Path(folder) / "sample.apk"

            def make(extra=None):
                with zipfile.ZipFile(path, "w") as archive:
                    for name, raw in originals.items():
                        archive.writestr(name.removeprefix("android/"), raw)
                    archive.writestr("assets/texture.atlas", b"pinned-atlas")
                    archive.writestr("classes.dex", b"synthetic-test-dex")
                    if extra:
                        archive.writestr(*extra)

            make()
            payloads = builder.audit_zip(path, originals, source_assets)
            self.assertEqual(
                payloads["classes.dex"], builder.digest(b"synthetic-test-dex")
            )
            make(("assets/unapproved.txt", b"extra"))
            with self.assertRaises(builder.BuilderError):
                builder.audit_zip(path, originals, source_assets)

    def test_certificate_parsers_require_exactly_one_verified_debug_identity(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "signing_report_certificate", None)),
            "Certificate parser missing",
        )
        sha = "b4" * 32
        report = (
            "Variant: debug\nConfig: debug\nSHA-256: "
            + ":".join(["B4"] * 32)
            + "\n----------\nVariant: release\nConfig: null\n"
        )
        self.assertEqual(builder.signing_report_certificate(report), sha)
        self.assertEqual(
            builder.apk_certificate(
                "Number of signers: 1\nV2 Signer: certificate SHA-256 digest: " + sha
            ),
            sha,
        )
        with self.assertRaises(builder.BuilderError):
            builder.apk_certificate(
                "Number of signers: 2\nV2 Signer: certificate SHA-256 digest: " + sha
            )

    def test_gradle_task_allowlist_cannot_install_or_repack(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "gradle_command", None)), "Build command missing"
        )
        cmd = builder.gradle_command(
            Path("/source"),
            Path("/init.gradle"),
            "1800000000",
            [":android:assembleDebug"],
        )
        self.assertIn("--offline", cmd)
        self.assertIn(":android:texturePacker", cmd)
        self.assertIn("-Pl10nBuilderVersionCode=1800000000", cmd)
        with self.assertRaises(builder.BuilderError):
            builder.gradle_command(
                Path("/source"),
                Path("/init.gradle"),
                "1800000000",
                [":android:installDebug"],
            )


class PatchTests(unittest.TestCase):
    def test_add_replace_remove_preserve_all_other_bytes(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "apply_patches", None)), "Byte patcher missing"
        )
        en, de = builder.TRANSLATIONS
        original = {
            en: b"# retained\r\n? = \r\nKeep =  x \r\n",
            de: b"? = ?\nMining = Bergbau\n",
        }
        edits = [
            {
                "file": en,
                "operation": "replace",
                "key": "?",
                "original": "",
                "replacement": "Help",
            },
            {
                "file": en,
                "operation": "add",
                "key": "+",
                "original": None,
                "replacement": "Increase",
            },
            {
                "file": de,
                "operation": "remove",
                "key": "Mining",
                "original": "Bergbau",
                "replacement": None,
            },
        ]
        result = builder.apply_patches(original, edits)
        self.assertEqual(
            result[en], b"# retained\r\n? = Help\r\nKeep =  x \r\n+ = Increase\r\n"
        )
        self.assertEqual(result[de], b"? = ?\n")
        self.assertEqual(builder.apply_patches(original, list(reversed(edits))), result)
        self.assertEqual(builder.apply_patches(original, []), original)
        self.assertEqual(original[de], b"? = ?\nMining = Bergbau\n")

    def test_literal_preconditions_scope_and_ambiguity_are_fail_closed(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "apply_patches", None)), "Byte patcher missing"
        )
        en, de = builder.TRANSLATIONS
        original = {en: b"? = \n", de: b"? = ?\n"}
        patch = {
            "file": en,
            "operation": "replace",
            "key": "?",
            "original": "",
            "replacement": "Help",
        }
        corruptions = [
            {"file": "../English.properties"},
            {"file": "core/Code.kt"},
            {"operation": "rename"},
            {"original": None},
            {"original": "wrong"},
            {"replacement": "x\nInjected = yes"},
            {"replacement": r"x\ny"},
            {"replacement": ""},
            {"key": " ?"},
            {"key": "? = injected"},
            {"key": "absent"},
            {"replacement": None},
            {"new_key": "other"},
        ]
        for changes in corruptions:
            with self.subTest(changes=changes), self.assertRaises(builder.BuilderError):
                builder.apply_patches(original, [{**patch, **changes}])
        with self.assertRaises(builder.BuilderError):
            builder.apply_patches(original, [patch, patch])
        with self.assertRaises(builder.BuilderError):
            builder.apply_patches({en: b"? = \n? = \n", de: original[de]}, [patch])


class RequestContractTests(unittest.TestCase):
    def test_accepts_exact_checker_shape_and_rejects_corrupt_identity(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "load_requests", None)),
            "Request validator missing",
        )
        value = request_bundle()
        self.assertEqual(builder.load_requests(builder.canonical_bytes(value)), value)
        value["requests"][1]["id"] = "batch-0000000000000000"
        with self.assertRaises(builder.BuilderError):
            builder.load_requests(builder.canonical_bytes(value))

    def test_schema_is_strict_and_untrusted_fields_cannot_enable_execution(self):
        builder = api()
        self.assertTrue(
            callable(getattr(builder, "load_requests", None)),
            "Request validator missing",
        )
        changes = [
            ("requests_version", True),
            ("source_commit", "0" * 40),
            ("command", "install"),
            ("constraints", {**builder.CONSTRAINTS, "translation_files_only": 1}),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                bundle = request_bundle()
                bundle[key] = value
                with self.assertRaises(builder.BuilderError):
                    builder.load_requests(builder.canonical_bytes(bundle))
        with self.assertRaises(builder.BuilderError):
            builder.load_requests(b'{"requests_version":1,"requests_version":1}')


if __name__ == "__main__":
    unittest.main()

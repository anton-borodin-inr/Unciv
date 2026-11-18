"""Regression tests for runtime boundaries; all payloads here are synthetic."""

import argparse
import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
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

    def test_each_request_builds_once_and_publishes_exactly_the_compact_set(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(strict=True)
            repo, sdk, jdk = [root / name for name in ("repo", "sdk", "jdk")]
            for subdir in (
                repo,
                sdk / "build-tools/37.0.0",
                sdk / "platforms/android-37.0",
                jdk,
            ):
                subdir.mkdir(parents=True)
            for file in (*sdk.glob("*/*"), jdk):
                (file / ("release" if file == jdk else "source.properties")).write_text(
                    "test tool revision"
                )
            originals = dict.fromkeys(c.TRANSLATIONS, b"Help = Help\n")
            reference = root / "reference.apk"
            cert = "b4" * 32
            commit = "a" * 40

            def write_apk(apk, translations):
                with zipfile.ZipFile(apk, "w") as archive:
                    for name, raw in translations.items():
                        archive.writestr(name.removeprefix("android/"), raw)
                    archive.writestr("classes.dex", b"same nontranslation payload")

            write_apk(reference, originals)
            requests = [
                {
                    "id": f"batch-{index}",
                    "patch_set_sha256": f"{index:064x}",
                    "role": role,
                    "patches": []
                    if role == "clean"
                    else [
                        {
                            "file": c.TRANSLATIONS[index % 2],
                            "operation": "replace",
                            "key": "Help",
                            "original": "Help",
                            "replacement": f"Changed {index}",
                        },
                    ],
                }
                for index, role in enumerate(
                    ("calibration", "mutation", "clean", "mutation", "mutation")
                )
            ]
            assemblies = {}

            def materialize(_, source):
                for name, raw in originals.items():
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)

            def checked(command, *, cwd, env, announce):
                if command[-1] == ":android:signingReport":
                    return (
                        f"Variant: debug\nSHA-256: {':'.join(['B4'] * 32)}\n----------\n"
                    ).encode()
                self.assertEqual(command[-1], ":android:assembleDebug")
                self.assertNotIn(cwd, assemblies)
                apk = cwd / "android/build/outputs/apk/debug/Unciv-debug.apk"
                apk.parent.mkdir(parents=True)
                write_apk(apk, {name: (cwd / name).read_bytes() for name in originals})
                assemblies[cwd] = command[-2].split("=")[1]
                return b""

            def apk_tools(apk, sdk, env):
                code = next(
                    (v for cwd, v in assemblies.items() if cwd in apk.parents),
                    "1271048",
                )
                return cert, code, b"normalized manifest"

            args = argparse.Namespace(
                repo=repo,
                output_root=root / "apks",
                sdk=sdk,
                jdk=jdk,
                reference_apk=reference,
                gradle_cache=root,
            )
            with (
                mock.patch.object(
                    c, "REFERENCE_APK_SHA256", c.digest(reference.read_bytes())
                ),
                mock.patch.object(
                    runtime, "frozen_builder", return_value=(commit, b"init")
                ),
                mock.patch.object(runtime, "git", return_value=b"source archive"),
                mock.patch.object(
                    runtime, "registry_directory", return_value=root / "registry"
                ),
                mock.patch.object(runtime, "prepare_gradle_cache"),
                mock.patch.object(runtime, "materialize", side_effect=materialize),
                mock.patch.object(runtime, "checked", side_effect=checked),
                mock.patch.object(runtime, "apk_tools", side_effect=apk_tools),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                runtime.build(args, requests)
                with self.assertRaisesRegex(c.BuilderError, "Output directory exists"):
                    runtime.build(args, requests)
            builds = c.json_bytes((args.output_root / "builds.json").read_bytes())
            self.assertEqual(len(assemblies), len(requests))
            self.assertTrue(next(iter(assemblies)).parent.name.startswith("batch-2-"))
            self.assertEqual(
                set(builds),
                {"source_commit", "builder_commit", "reference", "artifacts"},
            )
            self.assertEqual(builds["source_commit"], c.SOURCE_COMMIT)
            self.assertEqual(builds["builder_commit"], commit)
            self.assertEqual(set(builds["artifacts"]), {r["id"] for r in requests})
            self.assertEqual(
                builds["reference"],
                {
                    "file": runtime.REFERENCE_NAME,
                    "sha256": c.digest(reference.read_bytes()),
                },
            )
            for record in builds["artifacts"].values():
                self.assertEqual(set(record), {"file", "sha256"})
                self.assertEqual(
                    c.digest((args.output_root / record["file"]).read_bytes()),
                    record["sha256"],
                )
            self.assertEqual(
                {path.name for path in args.output_root.iterdir()},
                {"builds.json", runtime.REFERENCE_NAME}
                | {record["file"] for record in builds["artifacts"].values()},
            )
            self.assertEqual(list(root.glob(".l10n-build-*")), [])

            build_list = args.output_root / "builds.json"
            checksum = c.digest(build_list.read_bytes())
            self.assertEqual(runtime.verify_builds(build_list, checksum), len(requests))
            (args.output_root / runtime.REFERENCE_NAME).write_bytes(b"changed APK")
            with self.assertRaisesRegex(c.BuilderError, "APK digest differs"):
                runtime.verify_builds(build_list, checksum)


if __name__ == "__main__":
    unittest.main()

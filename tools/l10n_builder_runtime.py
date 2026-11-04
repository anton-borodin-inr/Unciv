"""Host execution and immutable catalog v1. No adb or provider execution path."""

import argparse
import contextlib
import fcntl
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import l10n_apk_builder as c

BUILDER_FILES = (
    "l10n_apk_builder.py",
    "l10n_builder_runtime.py",
    "l10n-builder.init.gradle",
)
INSTALLATION = {
    "command_template": ["adb", "install", "-r", "-d", "<apk>"],
    "requires_debuggable": True,
    "downgrade_permission_required": True,
    "uninstall_allowed": False,
    "backup_restore_required_in_part_c": True,
    "emulator_downgrade_verified": False,
    "app_data_preservation_verified": False,
}


def checked(command, *, cwd=None, env=None, log=None):
    """Only internal fixed commands reach this function; no shell interpolation."""
    if log is None:
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, check=False
        )
        c.require(
            result.returncode == 0,
            f"Command failed ({result.returncode}): {command[0]}",
        )
        return result.stdout
    log = c.no_symlinks(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("xb") as stream:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    receipt = {
        "command": [str(value) for value in command],
        "exit_code": result.returncode,
    }
    (log.parent / (log.name + ".exit.json")).write_bytes(c.canonical_bytes(receipt))
    c.require(
        result.returncode == 0, f"Command failed ({result.returncode}); see {log}"
    )
    return log.read_bytes()


def file_hash(path):
    digest = c.hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root):
    return {
        path.relative_to(root).as_posix(): file_hash(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def materialize(raw, destination):
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        c.require(
            all(member.isdir() or member.isfile() for member in members),
            "Source archive contains links or special files",
        )
        c.require(
            all(
                not Path(member.name).is_absolute()
                and ".." not in Path(member.name).parts
                for member in members
            ),
            "Unsafe source archive member",
        )
        archive.extractall(destination, filter="data")


def source_assets(source_inventory):
    """Pinned AGP asset exclusion rules; never accept untracked generated atlases."""
    c.require(type(source_inventory) is dict, "Source inventory must be an object")
    excluded = {"SaveFiles", "fonts", "maps", "music", "mods", "CVS", "SCCS"}
    result = {}
    for path, checksum in source_inventory.items():
        if not path.startswith("android/assets/"):
            continue
        parts = Path(path.removeprefix("android/assets/")).parts
        if any(
            part in excluded or part.startswith(".") or part.endswith("~")
            for part in parts
        ):
            continue
        if parts[-1].lower() in {"thumbs.db", "picasa.ini"} or parts[-1].endswith(
            ".scc"
        ):
            continue
        result[path.removeprefix("android/")] = checksum
    return result


def audit_source(source, original, translations):
    expected = {
        **original,
        **{path: c.digest(raw) for path, raw in translations.items()},
    }
    for path, checksum in expected.items():
        actual = c.no_symlinks(source / path)
        c.require(
            actual.is_file() and file_hash(actual) == checksum,
            f"Pinned source changed outside exact edits: {path}",
        )
    generated = (
        ".gradle/",
        ".kotlin/",
        "build/",
        "buildSrc/.gradle/",
        "buildSrc/.kotlin/",
        "buildSrc/build/",
        "core/build/",
        "core/.kotlin/",
        "android/build/",
        "android/.gradle/",
        "android/libs/",
        "desktop/build/",
        "tests/build/",
        "server/build/",
    )
    for path in source.rglob("*"):
        relative = path.relative_to(source).as_posix()
        c.require(
            not path.is_symlink(), f"Unexpected source/output symlink: {relative}"
        )
        if path.is_file():
            c.require(
                relative in expected or relative.startswith(generated),
                f"Unexpected source or configuration input: {relative}",
            )
    return {
        "files_verified": len(expected),
        "expected_inventory_sha256": c.digest(c.canonical_bytes(expected)),
    }


def tool_environment(jdk, sdk, gradle_home):
    # Do not pass provider credentials, user Gradle options or Java agents through.
    scratch = Path.home() / ".hermes/cache/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return {
        "JAVA_HOME": str(jdk),
        "ANDROID_HOME": str(sdk),
        "ANDROID_SDK_ROOT": str(sdk),
        "GRADLE_USER_HOME": str(gradle_home),
        "PATH": str(jdk / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(gradle_home / "empty-home"),
        "TMPDIR": str(scratch),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }


def prepare_gradle_cache(seed, destination):
    c.no_symlinks(seed)
    c.no_symlinks(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for relative in ("wrapper/dists/gradle-9.4.1-bin", "caches/modules-2"):
        source, target = seed / relative, destination / relative
        c.require(source.is_dir(), f"Missing pre-existing pinned cache: {relative}")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                source, target, ignore=shutil.ignore_patterns("*.lock", "*.lck")
            )
    # An isolated home must not inherit another session's init scripts/properties.
    c.require(
        not (destination / "init.d").exists()
        and not (destination / "init.gradle").exists()
        and not (destination / "gradle.properties").exists(),
        "Unexpected user Gradle configuration",
    )
    wrappers = list(
        (destination / "wrapper/dists/gradle-9.4.1-bin").glob(
            "*/gradle-9.4.1/bin/gradle"
        )
    )
    c.require(
        len(wrappers) == 1,
        "Pinned Gradle distribution must already be installed; no download fallback",
    )
    return wrappers[0].parents[1]


def toolchain_fingerprint(jdk, sdk, gradle_home, distribution):
    c.require((jdk / "bin/java").is_file(), "JDK missing")
    modules = gradle_home / "caches/modules-2/files-2.1"
    files = {"dependencies/" + key: value for key, value in inventory(modules).items()}
    files.update(
        {"gradle/" + key: value for key, value in inventory(distribution).items()}
    )
    for label, directory in [
        ("sdk-platform", sdk / "platforms/android-37.0"),
        ("sdk-build-tools", sdk / "build-tools/37.0.0"),
    ]:
        c.require(directory.is_dir(), f"Pinned toolchain directory missing: {label}")
        files.update(
            {label + "/" + key: value for key, value in inventory(directory).items()}
        )
    for relative in ("release", "bin/java", "lib/modules"):
        files["jdk/" + relative] = file_hash(jdk / relative)
    return {
        "fingerprint_version": 1,
        "python": sys.version,
        "host_system": platform.system(),
        "host_machine": platform.machine(),
        "gradle": "9.4.1",
        "compile_sdk": 37,
        "build_tools": "37.0.0",
        "offline": True,
        "build_cache": False,
        "texture_packer": "disabled",
        "locale": "C",
        "timezone": "UTC",
        "files": files,
    }


def normalize_manifest(xml):
    normalized, count = re.subn(
        r"(?m)^(\s*A: (?:android:|http://schemas\.android\.com/apk/res/android:)versionCode\(0x0101021b\)=).*$",
        r"\1<allocated-versionCode>",
        xml,
    )
    c.require(count == 1, "Cannot isolate exactly one manifest versionCode attribute")
    return normalized


def apk_tools(apk, sdk, env, logs, prefix):
    signature = checked(
        [
            str(sdk / "build-tools/37.0.0/apksigner"),
            "verify",
            "--verbose",
            "--print-certs",
            str(apk),
        ],
        env=env,
        log=logs / (prefix + ".signature.txt"),
    ).decode()
    certificate = c.apk_certificate(signature)
    badging = checked(
        [str(sdk / "build-tools/37.0.0/aapt2"), "dump", "badging", str(apk)],
        env=env,
        log=logs / (prefix + ".badging.txt"),
    ).decode()
    match = re.search(
        r"(?m)^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'",
        badging,
    )
    if match is None:
        raise c.BuilderError("APK identity missing")
    package, version, name = match.groups()
    c.require(
        package == "com.unciv.app"
        and name == "4.22.10"
        and "application-debuggable" in badging.splitlines(),
        "APK must retain pinned debug package/version name",
    )
    xml = checked(
        [
            str(sdk / "build-tools/37.0.0/aapt2"),
            "dump",
            "xmltree",
            str(apk),
            "--file",
            "AndroidManifest.xml",
        ],
        env=env,
        log=logs / (prefix + ".manifest.txt"),
    ).decode()
    normalized = normalize_manifest(xml)
    return (
        certificate,
        {
            "package": package,
            "version_code": version,
            "version_name": name,
            "debuggable": True,
        },
        normalized.encode(),
    )


def file_ref(root, path):
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": file_hash(path),
        "bytes": path.stat().st_size,
    }


def write_payload(root, relative, raw):
    path = c.no_symlinks(root / relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return file_ref(root, path)


def read_ref(root, reference):
    c.fields(reference, {"path", "sha256", "bytes"})
    relative = reference["path"]
    c.require(
        type(relative) is str
        and bool(relative)
        and not Path(relative).is_absolute()
        and all(part not in ("", ".", "..") for part in relative.split("/")),
        "Unsafe catalog path",
    )
    path = c.no_symlinks(root / relative)
    c.require(
        path.is_file()
        and c.valid_hash(reference["sha256"])
        and type(reference["bytes"]) is int,
        "Invalid catalog file reference",
    )
    raw = path.read_bytes()
    c.require(
        len(raw) == reference["bytes"] and c.digest(raw) == reference["sha256"],
        f"Catalog file hash/size mismatch: {relative}",
    )
    return raw


def copy_verified_artifact(parent, destination, identity, record):
    """Copy only authenticated references, never enumerate the parent payload dir."""
    references = {}

    def visit(value):
        if type(value) is dict:
            if set(value) == {"path", "sha256", "bytes"}:
                path = value["path"]
                c.require(
                    type(path) is str and path.startswith("batches/" + identity + "/"),
                    "Artifact reference escapes its batch",
                )
                c.require(
                    path not in references or references[path] == value,
                    "Conflicting artifact file references",
                )
                references[path] = value
            else:
                for item in value.values():
                    visit(item)
        elif type(value) is list:
            for item in value:
                visit(item)

    visit(record)
    c.require(bool(references), "Artifact has no verified file references")
    for path, reference in sorted(references.items()):
        raw = read_ref(parent, reference)
        target = c.no_symlinks(destination / path)
        if target.exists():
            c.require(target.read_bytes() == raw, "Existing reuse target differs")
        else:
            write_payload(destination, path, raw)


def execution_order(requests):
    return sorted(
        requests, key=lambda request: (request["role"] != "clean", request["id"])
    )


def registry_directory(repo):
    common = (
        checked(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ]
        )
        .decode()
        .strip()
    )
    registry = c.no_symlinks(Path(common) / "l10n-apk-builder-v1")
    registry.mkdir(parents=True, exist_ok=True)
    return registry


def build_identity(request, builder_sha, toolchain_sha, certificate):
    return c.digest(
        c.canonical_bytes(
            {
                "identity_version": 1,
                "source_commit": c.SOURCE_COMMIT,
                "patch_set_sha256": request["patch_set_sha256"],
                "builder_sha256": builder_sha,
                "toolchain_sha256": toolchain_sha,
                "certificate_sha256": certificate,
            }
        )
    )


def verify_catalog(path, expected_sha, sdk, jdk, logs):
    path = c.no_symlinks(path)
    root = path.parent
    raw = path.read_bytes()
    c.require(
        c.valid_hash(expected_sha) and c.digest(raw) == expected_sha,
        "Catalog must match the independently approved SHA-256",
    )
    catalog = c.json_bytes(raw)
    c.require(raw == c.canonical_bytes(catalog), "Noncanonical catalog")
    c.fields(
        catalog,
        {
            "catalog_version",
            "source_commit",
            "source_tree",
            "checker_commit",
            "inputs",
            "builder",
            "toolchain",
            "signing",
            "reference",
            "installation",
            "empty_control_id",
            "artifacts",
            "qualification",
            "source_inventory",
            "source_assets",
            "source_translations",
        },
    )
    c.require(
        type(catalog["catalog_version"]) is int
        and catalog["catalog_version"] == 1
        and catalog["source_commit"] == c.SOURCE_COMMIT
        and catalog["checker_commit"] == c.CHECKER_COMMIT,
        "Unsupported catalog authority",
    )
    c.require(
        c.canonical_bytes(catalog["installation"]) == c.canonical_bytes(INSTALLATION),
        "Installation semantics differ from catalog v1",
    )
    c.require(
        catalog["qualification"]
        == {
            "stage": "built_unqualified",
            "production_ready": False,
            "device_verification": "not_run_part_c",
            "labels_created": False,
        },
        "Catalog must not claim runtime qualification",
    )
    c.fields(catalog["inputs"], c.FROZEN)
    c.fields(
        catalog["builder"], {"files", "content_sha256", "git_head", "worktree_dirty"}
    )
    c.fields(catalog["builder"]["files"], BUILDER_FILES)
    c.fields(catalog["reference"], {"apk", "version_code"})
    c.fields(
        catalog["signing"],
        {
            "reference_certificate_sha256",
            "builder_certificate_sha256",
            "reference_apk_sha256",
        },
    )
    c.fields(catalog["source_translations"], c.TRANSLATIONS)
    c.require(type(catalog["artifacts"]) is list, "Artifacts must be an array")
    for entry in catalog["artifacts"]:
        c.fields(entry, {"id", "record"})
    inputs = {
        name: read_ref(root, reference) for name, reference in catalog["inputs"].items()
    }
    requests, _ = c.load_handoff(
        inputs,
        plan_sha256=c.digest(inputs["plan"]),
        requests_sha256=c.digest(inputs["requests"]),
    )
    builder_files = catalog["builder"]["files"]
    c.require(set(builder_files) == set(BUILDER_FILES), "Incomplete builder snapshot")
    for reference in builder_files.values():
        read_ref(root, reference)
    builder_sha = c.digest(
        c.canonical_bytes(
            {key: value["sha256"] for key, value in builder_files.items()}
        )
    )
    c.require(
        builder_sha == catalog["builder"]["content_sha256"], "Builder identity mismatch"
    )
    toolchain_raw = read_ref(root, catalog["toolchain"])
    toolchain_sha = c.digest(toolchain_raw)
    env = tool_environment(jdk, sdk, logs / "unused-gradle-home")
    reference = catalog["reference"]
    reference_raw = read_ref(root, reference["apk"])
    c.require(
        c.digest(reference_raw) == c.REFERENCE_APK_SHA256,
        "Reference must be the original 1271048 APK",
    )
    ref_cert, metadata, _ = apk_tools(
        root / reference["apk"]["path"], sdk, env, logs, "reference"
    )
    c.require(metadata["version_code"] == "1271048", "Reference versionCode mismatch")
    c.require(
        catalog["signing"]
        == {
            **c.signing_gate(
                ref_cert, catalog["signing"]["builder_certificate_sha256"]
            ),
            "reference_apk_sha256": c.REFERENCE_APK_SHA256,
        },
        "Reference/builder signing evidence mismatch",
    )
    original = {
        key: read_ref(root, value)
        for key, value in catalog["source_translations"].items()
    }
    c.require(set(original) == set(c.TRANSLATIONS), "Incomplete original translations")
    pin = c.json_bytes(read_ref(root, catalog["source_inventory"]))
    assets = c.json_bytes(read_ref(root, catalog["source_assets"]))
    c.require(assets == source_assets(pin), "Source asset inventory mismatch")
    c.require(
        all(c.digest(value) == pin[key] for key, value in original.items()),
        "Original translation provenance mismatch",
    )
    entries = catalog["artifacts"]
    c.require(
        [entry["id"] for entry in entries]
        == [request["id"] for request in requests["requests"]],
        "Missing/extra/duplicate catalog artifact",
    )
    records, codes, invariant = {}, set(), None
    positions = {
        request["id"]: index
        for index, request in enumerate(execution_order(requests["requests"]))
    }
    pairs = sorted(
        zip(entries, requests["requests"], strict=True),
        key=lambda pair: positions[pair[1]["id"]],
    )
    for entry, request in pairs:
        record_raw = read_ref(root, entry["record"])
        record = c.json_bytes(record_raw)
        c.require(
            record_raw == c.canonical_bytes(record), "Noncanonical artifact record"
        )
        c.fields(
            record,
            {
                "record_version",
                "request",
                "build_identity_sha256",
                "build",
                "apk",
                "translations",
                "zip_payloads",
                "normalized_manifest",
                "certificate_sha256",
                "source_audits",
                "reproducibility",
                "reused_from_catalog_sha256",
            },
        )
        c.require(
            record["record_version"] == 1 and record["request"] == request,
            "Artifact/request identity mismatch",
        )
        c.fields(
            record["build"],
            {"version_code", "apk_sha256", "fork_commit", "implementation_commit"},
        )
        c.fields(record["translations"], c.TRANSLATIONS)
        for value in record["translations"].values():
            c.fields(value, {"source_sha256", "file"})
        c.fields(
            record["reproducibility"],
            {
                "passes",
                "translation_files_byte_identical",
                "apk_byte_identical",
                "pass_1_apk_sha256",
                "pass_2_apk_sha256",
                "pass_2_zip_payloads",
                "pass_2_normalized_manifest",
                "different_zip_entries",
            },
        )
        identity = build_identity(request, builder_sha, toolchain_sha, ref_cert)
        code = str(1_800_000_000 + int(identity, 16) % 100_000_000)
        c.require(
            record["build_identity_sha256"] == identity
            and record["build"]["version_code"] == code
            and code not in codes,
            "Invalid/duplicate versionCode or build identity",
        )
        codes.add(code)
        apk_raw = read_ref(root, record["apk"])
        c.require(
            record["build"]
            == {
                "version_code": code,
                "apk_sha256": c.digest(apk_raw),
                "fork_commit": c.SOURCE_COMMIT,
                "implementation_commit": c.SOURCE_COMMIT,
            },
            "Checker build projection mismatch",
        )
        cert, metadata, normalized = apk_tools(
            root / record["apk"]["path"], sdk, env, logs, request["id"]
        )
        c.signing_gate(ref_cert, cert)
        c.require(
            record["certificate_sha256"] == cert and metadata["version_code"] == code,
            "APK manifest/signature differs from record",
        )
        expected = c.apply_patches(original, request["patches"])
        c.require(
            set(record["translations"]) == set(c.TRANSLATIONS),
            "Incomplete artifact translations",
        )
        for name, value in record["translations"].items():
            c.require(
                value["source_sha256"] == c.digest(original[name])
                and read_ref(root, value["file"]) == expected[name],
                "Translation bytes/source hash mismatch",
            )
        payloads = c.audit_zip(io.BytesIO(apk_raw), expected, assets)
        c.require(
            payloads == c.json_bytes(read_ref(root, record["zip_payloads"])),
            "APK payload inventory mismatch",
        )
        c.require(
            read_ref(root, record["normalized_manifest"]) == normalized,
            "Normalized manifest mismatch",
        )
        current = c.invariant_payloads(payloads, c.digest(normalized))
        if invariant is None:
            c.require(
                request["role"] == "clean"
                and catalog["empty_control_id"] == request["id"],
                "First artifact must be the same-builder empty control",
            )
            invariant = current
        c.require(current == invariant, "Unapproved nontranslation APK payload drift")
        repro = record["reproducibility"]
        c.require(
            repro["passes"] == 2
            and repro["translation_files_byte_identical"] is True
            and repro["pass_1_apk_sha256"] == record["apk"]["sha256"]
            and c.valid_hash(repro["pass_2_apk_sha256"]),
            "Invalid two-pass evidence",
        )
        c.require(
            type(repro["apk_byte_identical"]) is bool
            and repro["apk_byte_identical"]
            == (repro["pass_1_apk_sha256"] == repro["pass_2_apk_sha256"]),
            "False APK reproducibility claim",
        )
        second = c.json_bytes(read_ref(root, repro["pass_2_zip_payloads"]))
        c.require(
            type(second) is dict, "Second-pass payload inventory must be an object"
        )
        second_manifest = read_ref(root, repro["pass_2_normalized_manifest"])
        c.require(
            c.invariant_payloads(second, c.digest(second_manifest)) == invariant,
            "Second-pass nontranslation payload drift",
        )
        c.require(
            {key: value for key, value in second.items() if key.startswith("assets/")}
            == {
                **assets,
                **{
                    key.removeprefix("android/"): c.digest(value)
                    for key, value in expected.items()
                },
            },
            "Second-pass asset/translation mismatch",
        )
        differences = sorted(
            key
            for key in set(payloads) | set(second)
            if payloads.get(key) != second.get(key)
        )
        c.require(
            repro["different_zip_entries"] == differences,
            "Two-pass difference report mismatch",
        )
        expected_inventory = {
            **pin,
            **{key: c.digest(value) for key, value in expected.items()},
        }
        audit = {
            "files_verified": len(pin),
            "expected_inventory_sha256": c.digest(
                c.canonical_bytes(expected_inventory)
            ),
        }
        c.require(
            record["source_audits"] == [audit, audit],
            "Incomplete two-pass source audit",
        )
        records[request["id"]] = record
    # Tools must have read exactly the same APK bytes verified above.
    for record in records.values():
        c.require(
            file_hash(root / record["apk"]["path"]) == record["apk"]["sha256"],
            "APK changed during verification",
        )
    return catalog, records


def atomic_json(path, value):
    path = c.no_symlinks(path)
    raw = c.canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        c.require(
            path.is_file() and path.read_bytes() == raw,
            "Conflicting immutable registry record",
        )
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".atomic-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            c.require(path.read_bytes() == raw, "Concurrent registry record conflict")
    finally:
        Path(temporary).unlink()


def finish_publication(registry, repo, journal, verify):
    state = c.json_bytes(c.no_symlinks(journal).read_bytes())
    c.fields(
        state, {"publication_version", "stage", "target", "catalog_sha256", "bindings"}
    )
    checksum = state["catalog_sha256"]
    c.require(
        type(state["publication_version"]) is int
        and state["publication_version"] == 1
        and c.valid_hash(checksum),
        "Invalid publication journal",
    )
    c.require(
        type(state["stage"]) is str and type(state["target"]) is str,
        "Invalid publication paths",
    )
    stage, target = c.no_symlinks(state["stage"]), c.no_symlinks(state["target"])
    c.output_root(target.parent, repo)
    c.require(
        target.name == "catalog-" + checksum
        and stage.parent == target.parent / ".staging",
        "Publication journal paths do not describe an approved catalog transaction",
    )
    c.require(
        type(state["bindings"]) is list and bool(state["bindings"]),
        "Missing freeze bindings",
    )
    identities = []
    for binding in state["bindings"]:
        c.fields(binding, {"build_identity_sha256", "apk_sha256", "catalog_sha256"})
        c.require(
            all(c.valid_hash(value) for value in binding.values())
            and binding["catalog_sha256"] == checksum,
            "Invalid publication freeze binding",
        )
        identities.append(binding["build_identity_sha256"])
    c.require(
        len(set(identities)) == len(identities), "Duplicate publication freeze binding"
    )
    expected_bindings = {
        binding["build_identity_sha256"]: binding["apk_sha256"]
        for binding in state["bindings"]
    }
    if not target.exists():
        c.require(
            stage.is_dir(),
            "Pending publication has neither stage nor published target; stop for recovery",
        )
        c.require(
            verify(stage / "catalog.json", checksum) == expected_bindings,
            "Publication bindings differ from verified catalog",
        )
        stage.rename(target)
    c.require(target.is_dir(), "Publication target is not a catalog directory")
    c.require(
        verify(target / "catalog.json", checksum) == expected_bindings,
        "Publication bindings differ from verified catalog",
    )
    for path in sorted(target.rglob("*"), reverse=True):
        c.require(
            not path.is_symlink(), "Published catalog contains an unexpected symlink"
        )
        path.chmod(0o555 if path.is_dir() else 0o444)
    target.chmod(0o555)
    # Freeze bindings are installed only after publication and full verification.
    for binding in state["bindings"]:
        path = c.no_symlinks(
            registry / "frozen-artifacts" / (binding["build_identity_sha256"] + ".json")
        )
        if path.exists():
            previous = c.json_bytes(path.read_bytes())
            c.fields(previous, binding)
            c.require(
                previous["build_identity_sha256"] == binding["build_identity_sha256"]
                and previous["apk_sha256"] == binding["apk_sha256"],
                "Frozen artifact byte identity conflict",
            )
        else:
            atomic_json(path, binding)
    transaction_id = c.digest(c.canonical_bytes(state))
    atomic_json(journal.with_name(transaction_id + ".complete.json"), state)
    journal.unlink()


def recover_publications(registry, repo, verify):
    publications = c.no_symlinks(registry / "publications")
    if publications.exists():
        for journal in sorted(publications.glob("*.pending.json")):
            finish_publication(registry, repo, journal, verify)


def publish_transaction(registry, repo, stage, target, checksum, bindings, verify):
    c.require(
        not target.exists(), "Catalog exists; use explicit reuse rather than overwrite"
    )
    state = {
        "publication_version": 1,
        "stage": str(stage),
        "target": str(target),
        "catalog_sha256": checksum,
        "bindings": bindings,
    }
    transaction_id = c.digest(c.canonical_bytes(state))
    journal = registry / "publications" / (transaction_id + ".pending.json")
    atomic_json(journal, state)
    finish_publication(registry, repo, journal, verify)


@contextlib.contextmanager
def build_lock(root):
    descriptor = os.open(
        c.no_symlinks(root / ".build.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def build_catalog(args, raws):
    requests, _ = c.load_handoff(
        raws, plan_sha256=args.plan_sha256, requests_sha256=args.requests_sha256
    )
    repo = c.no_symlinks(args.repo)
    root = c.output_root(args.output_root, repo)
    root.mkdir(parents=True, exist_ok=True)
    registry = registry_directory(repo)
    with build_lock(registry):
        return build_locked(args, raws, requests, repo, root, registry)


def build_locked(args, raws, requests, repo, root, registry):
    run_id = str(time.time_ns())
    logs, work, stage = (
        root / "runs" / run_id,
        root / ".work",
        root / ".staging" / run_id,
    )
    for path in (logs, work, stage):
        c.no_symlinks(path).mkdir(parents=True, exist_ok=path != stage)
    jdk, sdk = c.no_symlinks(args.jdk), c.no_symlinks(args.sdk)
    recovery_counter = iter(range(1_000_000))

    def verify_publication(path, checksum):
        _, verified_records = verify_catalog(
            path,
            checksum,
            sdk,
            jdk,
            logs / ("publication-" + str(next(recovery_counter))),
        )
        return {
            record["build_identity_sha256"]: record["apk"]["sha256"]
            for record in verified_records.values()
        }

    recover_publications(registry, repo, verify_publication)
    cache = root / "toolchain/gradle-home"
    distribution = prepare_gradle_cache(c.no_symlinks(args.gradle_cache), cache)
    env = tool_environment(jdk, sdk, cache)
    c.require(
        shutil.disk_usage(root).free >= 2 * 1024**3,
        "Less than 2 GiB free; stop before building",
    )
    source_archive = checked(
        ["git", "-C", str(repo), "archive", "--format=tar", c.SOURCE_COMMIT]
    )
    source_tree = (
        checked(["git", "-C", str(repo), "rev-parse", c.SOURCE_COMMIT + "^{tree}"])
        .decode()
        .strip()
    )
    scripts = {
        name: (Path(__file__).parent / name).read_bytes() for name in BUILDER_FILES
    }
    builder_sha = c.digest(
        c.canonical_bytes({name: c.digest(raw) for name, raw in scripts.items()})
    )
    init_script = stage / "builder/l10n-builder.init.gradle"
    builder_refs = {
        name: write_payload(stage, "builder/" + name, raw)
        for name, raw in scripts.items()
    }
    input_refs = {
        name: write_payload(stage, "inputs/" + name + ".json", raw)
        for name, raw in raws.items()
    }
    reference_raw = c.no_symlinks(args.reference_apk).read_bytes()
    c.require(
        c.digest(reference_raw) == c.REFERENCE_APK_SHA256,
        "Reference APK is not the hash-pinned 1271048 build",
    )
    reference_ref = write_payload(
        stage, "reference/Unciv-1271048-debug.apk", reference_raw
    )
    reference_cert, reference_metadata, _ = apk_tools(
        stage / reference_ref["path"], sdk, env, logs, "reference-preflight"
    )
    c.require(
        reference_metadata["version_code"] == "1271048",
        "Reference versionCode mismatch",
    )
    with tempfile.TemporaryDirectory(prefix="signing-", dir=work) as folder:
        source = Path(folder) / "source"
        materialize(source_archive, source)
        pin = inventory(source)
        originals = {name: (source / name).read_bytes() for name in c.TRANSLATIONS}
        # Check every request independently before the first APK assembly.
        for request in requests["requests"]:
            c.apply_patches(originals, request["patches"])
        report = checked(
            c.gradle_command(source, init_script, None, [":android:signingReport"]),
            cwd=source,
            env=env,
            log=logs / "signing-preflight.txt",
        ).decode()
        signing = {
            **c.signing_gate(reference_cert, c.signing_report_certificate(report)),
            "reference_apk_sha256": c.REFERENCE_APK_SHA256,
        }
        audit_source(source, pin, originals)
    fingerprint = toolchain_fingerprint(jdk, sdk, cache, distribution)
    toolchain_raw = c.canonical_bytes(fingerprint)
    toolchain_sha = c.digest(toolchain_raw)
    toolchain_ref = write_payload(stage, "toolchain.json", toolchain_raw)
    identities = {
        request["id"]: build_identity(
            request, builder_sha, toolchain_sha, reference_cert
        )
        for request in requests["requests"]
    }
    versions = c.reserve_versions(registry / "allocations", identities)
    assets = source_assets(pin)
    catalog = {
        "catalog_version": 1,
        "source_commit": c.SOURCE_COMMIT,
        "source_tree": source_tree,
        "checker_commit": c.CHECKER_COMMIT,
        "inputs": input_refs,
        "builder": {
            "files": builder_refs,
            "content_sha256": builder_sha,
            "git_head": checked(["git", "-C", str(repo), "rev-parse", "HEAD"])
            .decode()
            .strip(),
            "worktree_dirty": bool(
                checked(["git", "-C", str(repo), "status", "--porcelain"])
            ),
        },
        "toolchain": toolchain_ref,
        "signing": signing,
        "reference": {"apk": reference_ref, "version_code": "1271048"},
        "installation": INSTALLATION,
        "empty_control_id": next(
            request["id"]
            for request in requests["requests"]
            if request["role"] == "clean"
        ),
        "artifacts": [],
        "qualification": {
            "stage": "built_unqualified",
            "production_ready": False,
            "device_verification": "not_run_part_c",
            "labels_created": False,
        },
        "source_inventory": write_payload(
            stage, "source-inventory.json", c.canonical_bytes(pin)
        ),
        "source_assets": write_payload(
            stage, "source-assets.json", c.canonical_bytes(assets)
        ),
        "source_translations": {
            name: write_payload(stage, "source-translations/" + Path(name).name, raw)
            for name, raw in originals.items()
        },
    }
    parent, parent_records = None, {}
    if args.reuse_from:
        parent, parent_records = verify_catalog(
            c.no_symlinks(args.reuse_from),
            args.reuse_sha256,
            sdk,
            jdk,
            logs / "parent-verification",
        )
        c.require(
            parent["builder"]["content_sha256"] == builder_sha
            and parent["toolchain"]["sha256"] == toolchain_sha,
            "Parent catalog uses another builder/toolchain",
        )
    baseline = None
    records = []
    for request in execution_order(requests["requests"]):
        identity, code = identities[request["id"]], versions[request["id"]]
        binding = c.no_symlinks(registry / "frozen-artifacts" / (identity + ".json"))
        previous = parent_records.get(request["id"])
        if binding.exists():
            frozen = c.json_bytes(binding.read_bytes())
            c.require(
                previous is not None
                and previous["build_identity_sha256"] == identity
                and previous["apk"]["sha256"] == frozen["apk_sha256"],
                "Artifact identity already frozen; supply its approved --reuse-from catalog",
            )
        prefix = "batches/" + request["id"] + "/"
        if previous is not None:
            c.require(
                previous["build_identity_sha256"] == identity,
                "Reused artifact identity mismatch",
            )
            copy_verified_artifact(
                Path(args.reuse_from).parent, stage, request["id"], previous
            )
            record = {
                **previous,
                "request": request,
                "reused_from_catalog_sha256": args.reuse_sha256,
            }
        else:
            expected = c.apply_patches(originals, request["patches"])
            passes, audits = [], []
            apk_ref = None
            for number in (1, 2):
                print(
                    f"Building {request['id']} pass {number}/2 versionCode={code}",
                    flush=True,
                )
                c.require(
                    shutil.disk_usage(root).free >= 1024**3,
                    "Less than 1 GiB free before APK assembly",
                )
                with tempfile.TemporaryDirectory(
                    prefix=f"{request['id']}-pass-{number}-", dir=work
                ) as folder:
                    source = Path(folder) / "source"
                    materialize(source_archive, source)
                    for name, raw in expected.items():
                        (source / name).write_bytes(raw)
                    checked(
                        c.gradle_command(
                            source, init_script, code, [":android:assembleDebug"]
                        ),
                        cwd=source,
                        env=env,
                        log=logs / f"{request['id']}-pass-{number}.build.txt",
                    )
                    apk = source / "android/build/outputs/apk/debug/Unciv-debug.apk"
                    c.require(
                        apk.is_file(), "Gradle did not produce the expected debug APK"
                    )
                    cert, metadata, normalized = apk_tools(
                        apk, sdk, env, logs, f"{request['id']}-pass-{number}"
                    )
                    c.signing_gate(reference_cert, cert)
                    c.require(
                        metadata["version_code"] == code,
                        "Gradle versionCode override did not take effect",
                    )
                    payloads = c.audit_zip(apk, expected, assets)
                    current = c.invariant_payloads(payloads, c.digest(normalized))
                    if baseline is None:
                        c.require(
                            request["role"] == "clean", "Control must build first"
                        )
                        baseline = current
                    c.require(
                        current == baseline,
                        "Nontranslation APK payload differs from the empty control",
                    )
                    audits.append(audit_source(source, pin, expected))
                    checksum = file_hash(apk)
                    if number == 1:
                        apk_ref = write_payload(
                            stage, prefix + "Unciv-debug.apk", apk.read_bytes()
                        )
                    payload_ref = write_payload(
                        stage,
                        prefix + f"pass-{number}-zip-payloads.json",
                        c.canonical_bytes(payloads),
                    )
                    manifest_ref = write_payload(
                        stage, prefix + f"pass-{number}-manifest.txt", normalized
                    )
                    passes.append(
                        {
                            "sha256": checksum,
                            "payloads": payloads,
                            "payload_ref": payload_ref,
                            "manifest_ref": manifest_ref,
                        }
                    )
                    print(
                        f"Verified {request['id']} pass {number}: {checksum}",
                        flush=True,
                    )
            if apk_ref is None:
                raise c.BuilderError("First-pass APK was not retained")
            different = sorted(
                name
                for name in set(passes[0]["payloads"]) | set(passes[1]["payloads"])
                if passes[0]["payloads"].get(name) != passes[1]["payloads"].get(name)
            )
            record = {
                "record_version": 1,
                "request": request,
                "build_identity_sha256": identity,
                "build": {
                    "version_code": code,
                    "apk_sha256": apk_ref["sha256"],
                    "fork_commit": c.SOURCE_COMMIT,
                    "implementation_commit": c.SOURCE_COMMIT,
                },
                "apk": apk_ref,
                "translations": {
                    name: {
                        "source_sha256": c.digest(originals[name]),
                        "file": write_payload(stage, prefix + Path(name).name, raw),
                    }
                    for name, raw in expected.items()
                },
                "zip_payloads": passes[0]["payload_ref"],
                "normalized_manifest": passes[0]["manifest_ref"],
                "certificate_sha256": reference_cert,
                "source_audits": audits,
                "reproducibility": {
                    "passes": 2,
                    "apk_byte_identical": passes[0]["sha256"] == passes[1]["sha256"],
                    "translation_files_byte_identical": True,
                    "pass_1_apk_sha256": passes[0]["sha256"],
                    "pass_2_apk_sha256": passes[1]["sha256"],
                    "different_zip_entries": different,
                    "pass_2_zip_payloads": passes[1]["payload_ref"],
                    "pass_2_normalized_manifest": passes[1]["manifest_ref"],
                },
                "reused_from_catalog_sha256": None,
            }
        payloads = c.json_bytes(read_ref(stage, record["zip_payloads"]))
        normalized = read_ref(stage, record["normalized_manifest"])
        current = c.invariant_payloads(payloads, c.digest(normalized))
        baseline = current if baseline is None else baseline
        c.require(
            current == baseline,
            "Reused/new artifact differs from the same-builder control",
        )
        catalog["artifacts"].append(
            {
                "id": request["id"],
                "record": write_payload(
                    stage, prefix + "record.json", c.canonical_bytes(record)
                ),
            }
        )
        records.append(record)
    c.require(
        toolchain_fingerprint(jdk, sdk, cache, distribution) == fingerprint,
        "Toolchain/dependency artifacts changed during builds",
    )
    c.require(
        all(
            (Path(__file__).parent / name).read_bytes() == raw
            for name, raw in scripts.items()
        ),
        "Builder changed during execution; do not publish",
    )
    catalog["artifacts"].sort(key=lambda entry: entry["id"])
    catalog_raw = c.canonical_bytes(catalog)
    checksum = c.digest(catalog_raw)
    write_payload(stage, "catalog.json", catalog_raw)
    write_payload(stage, "catalog.sha256", (checksum + "\n").encode())
    verify_catalog(
        stage / "catalog.json", checksum, sdk, jdk, logs / "final-verification"
    )
    target = c.no_symlinks(root / ("catalog-" + checksum))
    c.require(
        not target.exists(), "Catalog exists; use explicit reuse rather than overwrite"
    )
    bindings = [
        {
            "build_identity_sha256": record["build_identity_sha256"],
            "apk_sha256": record["apk"]["sha256"],
            "catalog_sha256": checksum,
        }
        for record in records
    ]
    publish_transaction(
        registry, repo, stage, target, checksum, bindings, verify_publication
    )
    print(
        c.canonical_bytes(
            {
                "catalog": str(target / "catalog.json"),
                "sha256": checksum,
                "artifacts": len(records),
                "all_apks_byte_identical": all(
                    record["reproducibility"]["apk_byte_identical"]
                    for record in records
                ),
            }
        ).decode(),
        end="",
        flush=True,
    )
    return 0


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "build"):
        command = commands.add_parser(name)
        for option in ("requests", "plan", "recipes", "manifest", "split"):
            command.add_argument("--" + option, type=Path, required=True)
        command.add_argument("--plan-sha256", default=c.FROZEN["plan"])
        command.add_argument("--requests-sha256", default=c.FROZEN["requests"])
        if name == "build":
            for option in (
                "repo",
                "output-root",
                "reference-apk",
                "jdk",
                "sdk",
                "gradle-cache",
            ):
                command.add_argument("--" + option, type=Path, required=True)
            command.add_argument("--reuse-from", type=Path)
            command.add_argument("--reuse-sha256")
    for name in ("verify", "reuse"):
        command = commands.add_parser(name)
        command.add_argument("--catalog", type=Path, required=True)
        command.add_argument("--sha256", required=True)
        command.add_argument("--sdk", type=Path, required=True)
        command.add_argument("--jdk", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("verify", "reuse"):
            path = c.no_symlinks(args.catalog)
            logs = path.parent.parent / "runs" / ("verification-" + str(time.time_ns()))
            catalog, records = verify_catalog(
                path, args.sha256, args.sdk, args.jdk, logs
            )
            print(
                c.canonical_bytes(
                    {
                        "verified": True,
                        "catalog_sha256": args.sha256,
                        "artifacts": len(records),
                        "device_verification": catalog["qualification"][
                            "device_verification"
                        ],
                    }
                ).decode(),
                end="",
            )
            return 0
        raws = {
            name: c.no_symlinks(getattr(args, name)).read_bytes() for name in c.FROZEN
        }
        requests, _ = c.load_handoff(
            raws, plan_sha256=args.plan_sha256, requests_sha256=args.requests_sha256
        )
        if args.command == "check":
            print(
                c.canonical_bytes(
                    {
                        "valid": True,
                        "source_commit": c.SOURCE_COMMIT,
                        "requests": len(requests["requests"]),
                    }
                ).decode(),
                end="",
            )
            return 0
        c.require(
            bool(args.reuse_from) == bool(args.reuse_sha256),
            "Reuse requires both a catalog and its approved SHA-256",
        )
        return build_catalog(args, raws)
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError) as exc:
        print(f"Localization APK builder failed: {exc}", file=sys.stderr)
        return 1

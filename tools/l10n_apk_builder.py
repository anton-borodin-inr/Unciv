"""Translation-only, dev-only APK builder; never operates a device or provider."""

import fcntl
import hashlib
import json
import math
import os
import re
import zipfile
from pathlib import Path

SOURCE_COMMIT = "b534a8ae67c390412984869c7a03b168d532c36c"
CHECKER_COMMIT = "e26914a4870d7755634cf547931f88c3e3093e5c"
FROZEN = {
    "recipes": "c181072b73ca7d16101db935ec0493df9f42c168b1d53139af546c845ce8d23f",
    "plan": "664f29f1902333c49427fdfeef4629b7d146c416e12100327001bd77aea12c55",
    "requests": "36cf23d92bba9a059284dbdd7dfe7ccc90ba95b52b3b9bae0c9997308d289b94",
    "manifest": "b80d9a8c976fdff19cb97d300b264efdcc926ea4baeb88d45e7cdbcea929276a",
    "split": "d2b7f97cc77f1d5f48d2c5e36972d2ece5ec71c128197ee157e2cec20c0c4c82",
}
REFERENCE_APK_SHA256 = (
    "91d9a900f8204880e590de7fb90f0fc15e2aa5cf5d94a3ae77364497d7b0541e"
)
TRANSLATIONS = tuple(
    f"android/assets/jsons/translations/{language}.properties"
    for language in ("English", "German")
)
CONSTRAINTS = {
    "translation_files_only": True,
    "same_builder_for_empty_and_mutated_patches": True,
    "build_only_no_device_operations": True,
}


class BuilderError(ValueError):
    """Invalid input or failed verification; never a successful build."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value):
    """Byte-compatible with checker e26914a text_dump.canonical_bytes."""

    def numbers(item):
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Non-finite JSON number")
            return int(item) if item.is_integer() else item
        if isinstance(item, dict):
            return {key: numbers(value) for key, value in item.items()}
        if isinstance(item, list):
            return [numbers(value) for value in item]
        return item

    return (
        json.dumps(
            numbers(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def require(condition, message):
    if not condition:
        raise BuilderError(message)


def fields(value, expected):
    require(
        type(value) is dict and set(value) == set(expected), "Unexpected JSON fields"
    )


def json_bytes(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"Duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid(value):
        raise BuilderError(f"Non-finite JSON number: {value}")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, ValueError) as exc:
        raise BuilderError(f"Invalid JSON: {exc}") from exc


def valid_hash(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_patch(patch):
    fields(patch, {"file", "operation", "key", "original", "replacement"})
    require(
        patch["file"] in TRANSLATIONS,
        "Only literal English/German translation paths are allowed",
    )
    require(
        patch["operation"] in ("add", "replace", "remove"),
        "Unsupported patch operation",
    )
    for name in ("key", "original", "replacement"):
        value = patch[name]
        require(
            value is None or type(value) is str,
            "Patch values must be literal strings/null",
        )
        if value is not None:
            require(
                not any(c in value for c in ("\n", "\r", "\\", "\0")),
                "Escaped/multiline edits are unsupported",
            )
            require(
                not any(0xD800 <= ord(c) <= 0xDFFF for c in value),
                "Invalid Unicode scalar",
            )
    key = patch["key"]
    require(
        type(key) is str
        and bool(key)
        and key == key.strip()
        and " = " not in key
        and not key.startswith("#"),
        "Invalid source key",
    )
    require(
        (patch["operation"] == "add") == (patch["original"] is None),
        "Absent original is required only for add",
    )
    require(
        (patch["operation"] == "remove") == (patch["replacement"] is None),
        "Absent replacement is required only for remove",
    )
    if patch["replacement"] is not None:
        require(
            bool(patch["replacement"].strip())
            and patch["replacement"] != patch["original"],
            "Empty or unchanged replacement",
        )


def load_requests(raw):
    value = json_bytes(raw)
    fields(
        value,
        {
            "requests_version",
            "source_commit",
            "plan_sha256",
            "recipe_version",
            "requests",
            "constraints",
        },
    )
    require(
        type(value["requests_version"]) is int and value["requests_version"] == 1,
        "Unsupported requests version",
    )
    require(
        value["source_commit"] == SOURCE_COMMIT,
        "Source commit must be the approved pin",
    )
    require(valid_hash(value["plan_sha256"]), "Invalid plan SHA-256")
    require(value["recipe_version"] == "stage3-v1", "Unsupported recipe version")
    fields(value["constraints"], CONSTRAINTS)
    require(
        all(item is True for item in value["constraints"].values()),
        "All builder constraints must be true",
    )
    require(
        type(value["requests"]) is list and bool(value["requests"]),
        "Empty request list",
    )
    ids, hashes, clean, calibration = set(), set(), 0, 0
    for request in value["requests"]:
        fields(request, {"id", "patch_set_sha256", "patches", "role", "calibration"})
        require(valid_hash(request["patch_set_sha256"]), "Invalid patch SHA-256")
        require(type(request["patches"]) is list, "Patches must be an array")
        require(
            request["role"] in ("clean", "mutation", "calibration")
            and type(request["calibration"]) is bool,
            "Invalid request role/calibration",
        )
        targets = set()
        for patch in request["patches"]:
            validate_patch(patch)
            identity = patch["file"], patch["key"]
            require(identity not in targets, "Duplicate/conflicting translation edit")
            targets.add(identity)
        checksum = digest(canonical_bytes(request["patches"]))
        require(
            checksum == request["patch_set_sha256"]
            and request["id"] == "batch-" + checksum[:16],
            "Patch hash/ID mismatch",
        )
        require(
            request["id"] not in ids and checksum not in hashes,
            "Duplicate request or truncated-hash collision",
        )
        ids.add(request["id"])
        hashes.add(checksum)
        require(
            (request["role"] == "clean") == (not request["patches"]),
            "Only the clean request may have empty patches",
        )
        if request["role"] == "clean":
            clean += 1
            require(
                not request["calibration"],
                "Clean control cannot be the mutation calibration",
            )
        if request["calibration"]:
            calibration += 1
            require(len(request["patches"]) == 1, "Calibration must be a singleton")
        require(
            request["role"] != "calibration" or request["calibration"],
            "Standalone calibration flag missing",
        )
    require(
        clean == 1 and calibration == 1,
        "Exactly one empty control and one calibration are required",
    )
    require(
        value["requests"] == sorted(value["requests"], key=lambda r: r["id"]),
        "Request order is not canonical",
    )
    require(
        raw == canonical_bytes(value),
        "Requests must be the exact canonical checker bytes",
    )
    return value


def apply_patches(originals, patches):
    """Preserve source bytes, including empty RHS, comments and line endings."""
    require(
        set(originals) == set(TRANSLATIONS), "Both pinned translations are required"
    )
    result = dict(originals)
    targets = set()
    for patch in patches:
        validate_patch(patch)
        target = patch["file"], patch["key"]
        require(target not in targets, "Duplicate/conflicting translation edit")
        targets.add(target)
    for path in TRANSLATIONS:
        raw = originals[path]
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise BuilderError("Translation source is not UTF-8") from exc
        lines = raw.splitlines(keepends=True)
        changes, additions = {}, []
        for patch in sorted(
            (p for p in patches if p["file"] == path), key=lambda p: p["key"]
        ):
            key = patch["key"].encode("utf-8")
            hits = []
            for index, line in enumerate(lines):
                content = line.rstrip(b"\r\n")
                if not content.startswith(b"#") and b" = " in content:
                    source_key, rhs = content.split(b" = ", 1)
                    if source_key == key:
                        hits.append((index, rhs, line[len(content) :]))
            if patch["operation"] == "add":
                require(not hits, "Add requires a genuinely absent entry")
                additions.append(key + b" = " + patch["replacement"].encode("utf-8"))
            else:
                require(len(hits) == 1, "Missing or ambiguous literal entry")
                index, rhs, ending = hits[0]
                require(
                    rhs == patch["original"].encode("utf-8"), "Original RHS mismatch"
                )
                changes[index] = (
                    b""
                    if patch["operation"] == "remove"
                    else key + b" = " + patch["replacement"].encode("utf-8") + ending
                )
        output = b"".join(changes.get(index, line) for index, line in enumerate(lines))
        if additions:
            require(
                not raw or raw.endswith(b"\n"),
                "Adding to a source without a final LF is unsupported",
            )
            ending = (
                b"\r\n" if raw.count(b"\r\n") == raw.count(b"\n") and raw else b"\n"
            )
            output += ending.join(additions) + ending
        result[path] = output
    return result


def project_requests(plan, recipes, dev):
    """Validate the dev transport boundary and reproduce the checker projection."""
    require(
        plan["fork_commit"] == SOURCE_COMMIT and plan["recipe_version"] == "stage3-v1",
        "Plan source/version mismatch",
    )
    selected = plan["screen_ids"]
    require(
        bool(selected) and len(set(selected)) == len(selected) and set(selected) <= dev,
        "Plan must select dev screens only",
    )
    require(
        set(plan["mutations"]) == set(recipes),
        "Plan recipe coverage differs from frozen catalog",
    )
    for identity, item in plan["mutations"].items():
        fields(item, {"recipe", "patch", "occurrences"})
        recipe = recipes[identity]
        require(item["recipe"] == recipe, "Recipe is not the frozen literal recipe")
        require(recipe["language"] in ("en", "de"), "Unsupported language")
        patch = {
            key: recipe[key] for key in ("key", "operation", "original", "replacement")
        }
        patch["file"] = TRANSLATIONS[("en", "de").index(recipe["language"])]
        require(item["patch"] == patch, "Patch changes a frozen source key/value")
        validate_patch(patch)
        targets = {target["screen"] for target in recipe["targets"]}
        require(
            bool(targets) and targets <= set(selected),
            "Recipe target is outside selected dev scope",
        )
        require(
            type(item["occurrences"]) is list and bool(item["occurrences"]),
            "Missing bound occurrence",
        )
        for occurrence in item["occurrences"]:
            require(
                occurrence["screen"] in targets
                and occurrence["language"] == recipe["language"],
                "Occurrence outside recipe/dev scope",
            )
    projected, covered = {}, []
    for batch, role in [
        (plan["clean_build"], "clean"),
        *[(b, "mutation") for b in plan["batches"]],
    ]:
        if role == "clean":
            patches = []
        else:
            ids = batch["recipe_ids"]
            require(
                bool(ids) and ids == sorted(set(ids)) and set(ids) <= set(recipes),
                "Invalid batch recipe membership",
            )
            covered.extend(ids)
            patches = [plan["mutations"][identity]["patch"] for identity in ids]
        require(
            batch["patches"] == patches, "Batch patches differ from literal recipes"
        )
        checksum = digest(canonical_bytes(patches))
        require(
            batch["patch_set_sha256"] == checksum
            and batch["id"] == "batch-" + checksum[:16],
            "Batch identity mismatch",
        )
        require(checksum not in projected, "Duplicate patch set in plan")
        for shot in batch.get("screenshots", []):
            require(
                shot["screen"] in dev and shot["language"] in ("en", "de"),
                "Batch screenshot outside dev scope",
            )
        projected[checksum] = {
            "id": batch["id"],
            "patch_set_sha256": checksum,
            "patches": patches,
            "role": role,
            "calibration": False,
        }
    require(
        sorted(covered) == sorted(recipes),
        "Every recipe must belong to exactly one batch",
    )
    identity = plan["calibration"]["mutation_recipe_id"]
    require(identity in recipes, "Unknown calibration recipe")
    patches = [plan["mutations"][identity]["patch"]]
    checksum = digest(canonical_bytes(patches))
    if checksum not in projected:
        projected[checksum] = {
            "id": "batch-" + checksum[:16],
            "patch_set_sha256": checksum,
            "patches": patches,
            "role": "calibration",
            "calibration": True,
        }
    projected[checksum]["calibration"] = True
    return {
        "requests_version": 1,
        "source_commit": SOURCE_COMMIT,
        "plan_sha256": digest(canonical_bytes(plan)),
        "recipe_version": plan["recipe_version"],
        "requests": sorted(projected.values(), key=lambda r: r["id"]),
        "constraints": CONSTRAINTS,
    }


def load_handoff(
    raws, *, plan_sha256=FROZEN["plan"], requests_sha256=FROZEN["requests"]
):
    fields(raws, FROZEN)
    expected = {**FROZEN, "plan": plan_sha256, "requests": requests_sha256}
    for name, raw in raws.items():
        require(
            valid_hash(expected[name]) and digest(raw) == expected[name],
            f"Frozen {name} hash mismatch",
        )
    requests = load_requests(raws["requests"])
    plan, catalog, manifest, split = [
        json_bytes(raws[name]) for name in ("plan", "recipes", "manifest", "split")
    ]
    require(
        raws["plan"] == canonical_bytes(plan), "Plan must be canonical checker bytes"
    )
    require(
        plan["manifest_version"] == 1
        and plan["stage"] == "offline_plan"
        and plan["production_ready"] is False,
        "Unsupported plan or qualification claim",
    )
    require(
        plan["source_manifest_sha256"] == FROZEN["manifest"]
        and plan["split_sha256"] == FROZEN["split"],
        "Plan authority mismatch",
    )
    require(
        manifest["build"]["fork_commit"] == SOURCE_COMMIT
        and manifest["build"]["apk_sha256"] == REFERENCE_APK_SHA256,
        "Source authority mismatch",
    )
    require(
        catalog["fork_commit"] == SOURCE_COMMIT
        and catalog["recipe_version"] == "stage3-v1",
        "Recipe authority mismatch",
    )
    dev = set(split["dev"])
    require(len(dev) == len(split["dev"]) == 5, "Incomplete dev authority")
    expected_pairs = {(screen, language) for screen in dev for language in ("en", "de")}
    actual_pairs = [(item["screen"], item["language"]) for item in plan["inventory"]]
    require(
        len(actual_pairs) == len(expected_pairs)
        and set(actual_pairs) == expected_pairs,
        "Incomplete/duplicate/nondev inventory",
    )
    recipes = {recipe["id"]: recipe for recipe in catalog["recipes"]}
    require(len(recipes) == len(catalog["recipes"]), "Duplicate frozen recipe")
    require(
        requests == project_requests(plan, recipes, dev),
        "Requests are not the exact projection of the approved plan",
    )
    return requests, plan


def signing_gate(reference, actual):
    require(
        valid_hash(reference) and valid_hash(actual),
        "Missing or malformed signing certificate SHA-256",
    )
    require(
        reference == actual,
        "Signing identity mismatch: stop; never uninstall or rotate the key",
    )
    return {
        "reference_certificate_sha256": reference,
        "builder_certificate_sha256": actual,
    }


def no_symlinks(path):
    path = Path(path).absolute()
    require(
        not any(part.is_symlink() for part in (path, *path.parents)),
        "Symlink path component is forbidden",
    )
    return path


def reserve_versions(registry, identities):
    registry = no_symlinks(registry)
    registry.mkdir(parents=True, exist_ok=True)
    require(
        all(valid_hash(value) for value in identities.values()),
        "Invalid build identity",
    )
    versions = {
        key: str(1_800_000_000 + int(value, 16) % 100_000_000)
        for key, value in identities.items()
    }
    require(
        len(set(versions.values())) == len(versions),
        "VersionCode collision in requested batch set",
    )
    require("1271048" not in versions.values(), "Stock versionCode cannot be reused")
    descriptor = os.open(
        registry / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = {}
        for identity, checksum in identities.items():
            code = versions[identity]
            record = {
                "allocation_version": 1,
                "version_code": code,
                "build_identity_sha256": checksum,
            }
            path = no_symlinks(registry / f"{code}.json")
            if path.exists():
                require(
                    path.is_file() and path.read_bytes() == canonical_bytes(record),
                    "Reserved versionCode collision or corrupt allocation",
                )
            records[path] = record
        for path, record in records.items():
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(canonical_bytes(record))
                    stream.flush()
                    os.fsync(stream.fileno())
    return versions


def output_root(path, repo):
    path, repo = no_symlinks(path), no_symlinks(repo)
    existing = [item for item in (path, *path.parents) if item.exists()]
    require(
        not any(item.samefile(repo) for item in existing),
        "Output must not be inside the checkout",
    )
    if path.exists():
        require(path.is_dir(), "Output root is not a directory")
        require(
            not any(item.samefile(path) for item in (repo, *repo.parents)),
            "Output root must not contain the checkout",
        )
    return path


def signing_report_certificate(text):
    debug = re.findall(r"(?m)^Variant: debug\n(.*?)(?=^----------)", text, re.DOTALL)
    require(len(debug) == 1, "Missing/ambiguous debug signing configuration")
    hashes = re.findall(r"(?m)^SHA-256: ([A-Fa-f0-9:]+)$", debug[0])
    require(len(hashes) == 1, "Missing debug signing certificate")
    value = hashes[0].replace(":", "").lower()
    require(valid_hash(value), "Invalid debug certificate fingerprint")
    return value


def apk_certificate(text):
    require(
        re.search(r"(?m)^Number of signers: 1$", text) is not None,
        "APK must have exactly one signer",
    )
    hashes = re.findall(r"certificate SHA-256 digest: ([a-f0-9]{64})", text)
    require(len(hashes) == 1, "Missing/ambiguous APK certificate fingerprint")
    return hashes[0]


def gradle_command(source, init_script, version_code, tasks):
    allowed = {
        ":android:assembleDebug",
        ":android:signingReport",
        ":tests:test",
        ":android:lintDebug",
    }
    require(
        bool(tasks) and set(tasks) <= allowed,
        "Only approved host build/verification tasks are allowed",
    )
    command = [
        str(source / "gradlew"),
        "--offline",
        "--no-daemon",
        "--no-configuration-cache",
        "--no-build-cache",
        "--no-parallel",
        "--max-workers=2",
        "--console=plain",
        "-Pkotlin.compiler.execution.strategy=in-process",
        "-Dmaven.repo.local=" + str(source / ".empty-m2"),
        "--init-script",
        str(init_script),
        "-x",
        ":android:texturePacker",
    ]
    if version_code is not None:
        require(
            type(version_code) is str
            and version_code.isascii()
            and version_code.isdecimal()
            and 1_800_000_000 <= int(version_code) <= 1_899_999_999,
            "Invalid builder versionCode",
        )
        command.append("-Pl10nBuilderVersionCode=" + version_code)
    return command + list(tasks)


def audit_zip(apk, translations, source_assets):
    """Hash actual ZIP payloads; require exact translations and pinned asset set."""
    with zipfile.ZipFile(apk) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
        require(len(names) == len(set(names)), "Duplicate APK ZIP member")
        require(
            all(
                not name.startswith("/") and ".." not in Path(name).parts
                for name in names
            ),
            "Unsafe APK member",
        )
        payloads = {name: digest(archive.read(name)) for name in names}
        expected_assets = dict(source_assets)
        for path, raw in translations.items():
            name = path.removeprefix("android/")
            expected_assets[name] = digest(raw)
            require(
                name in payloads and archive.read(name) == raw,
                "Packaged translation bytes differ",
            )
        actual_assets = {
            name: checksum
            for name, checksum in payloads.items()
            if name.startswith("assets/")
        }
        require(
            actual_assets == expected_assets,
            "Nontranslation assets differ from the pin or asset membership changed",
        )
    return payloads


def invariant_payloads(payloads, manifest_sha256):
    """Exclude only translations, v1 signature containers and parsed versionCode."""
    translations = {path.removeprefix("android/") for path in TRANSLATIONS}
    result = {
        name: checksum
        for name, checksum in payloads.items()
        if name not in translations
        and name != "AndroidManifest.xml"
        and name != "META-INF/MANIFEST.MF"
        and not re.fullmatch(r"META-INF/[^/]+\.(SF|RSA|DSA|EC)", name)
    }
    result["AndroidManifest.xml (versionCode normalized)"] = manifest_sha256
    return result


if __name__ == "__main__":
    from l10n_builder_runtime import cli

    raise SystemExit(cli())

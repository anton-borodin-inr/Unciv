"""Build the frozen dev requests once and publish a compact builds.json."""

import argparse
import io
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import l10n_apk_builder as c

BUILDER_FILES = (
    "l10n_apk_builder.py",
    "l10n_builder_runtime.py",
    "l10n-builder.init.gradle",
)
APK_LIMIT_BYTES = 500_000_000
REFERENCE_NAME = "Unciv-1271048-debug.apk"


def checked(command, *, cwd=None, env=None, announce=False):
    command = list(map(str, command))
    if announce:
        print(f"$ {shlex.join(command)}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, check=False)
    if result.returncode:
        tail = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        raise c.BuilderError(
            f"{command[0]} exited {result.returncode}\n"
            + "\n".join(tail.splitlines()[-60:])
        )
    return result.stdout


def git(repo, *args):
    return checked(["git", "-C", repo, *args])


def frozen_builder(repo):
    commit = git(repo, "rev-parse", "HEAD").decode().strip()
    files = Path(__file__).resolve().parent
    scripts = {
        name: git(repo, "show", f"{commit}:tools/{name}") for name in BUILDER_FILES
    }
    c.require(
        all((files / name).read_bytes() == raw for name, raw in scripts.items()),
        "Run the builder from a committed revision",
    )
    return commit, scripts["l10n-builder.init.gradle"]


def materialize(raw, destination):
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        c.require(
            all(
                (member.isdir() or member.isfile())
                and not Path(member.name).is_absolute()
                and ".." not in Path(member.name).parts
                for member in members
            ),
            "Unsafe source archive member",
        )
        archive.extractall(destination, filter="data")


def source_assets(source):
    """Derive the expected APK assets from the pinned source and AGP exclusions."""
    root = source / "android/assets"
    excluded = {"SaveFiles", "fonts", "maps", "music", "mods", "CVS", "SCCS"}
    result = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(
            part in excluded or part.startswith(".") or part.endswith("~")
            for part in parts
        ):
            continue
        if path.name.lower() in {"thumbs.db", "picasa.ini"} or path.suffix == ".scc":
            continue
        result[path.relative_to(source / "android").as_posix()] = c.digest(
            path.read_bytes()
        )
    return result


def tool_environment(jdk, sdk, gradle_home, temp):
    # Run Gradle with the pinned SDK and an isolated cache and configuration.
    return {
        "JAVA_HOME": str(jdk),
        "ANDROID_HOME": str(sdk),
        "ANDROID_SDK_ROOT": str(sdk),
        "GRADLE_USER_HOME": str(gradle_home),
        "PATH": str(jdk / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(temp),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }


def prepare_gradle_cache(seed, destination):
    wrapper = Path("wrapper/dists/gradle-9.4.1-bin")
    for relative in (wrapper, Path("caches/modules-2")):
        source, target = seed / relative, destination / relative
        c.require(source.is_dir(), f"Missing pre-existing pinned cache: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source, target, ignore=shutil.ignore_patterns("*.lock", "*.lck")
        )
    c.require(
        len(list((destination / wrapper).glob("*/gradle-9.4.1/bin/gradle"))) == 1,
        "Pinned Gradle distribution must already be installed",
    )


def normalize_manifest(xml):
    normalized, count = re.subn(
        r"(?m)^(\s*A: (?:android:|http://schemas\.android\.com/apk/res/android:)versionCode\(0x0101021b\)=).*$",
        r"\1<allocated-versionCode>",
        xml,
    )
    c.require(count == 1, "Cannot isolate exactly one manifest versionCode attribute")
    return normalized


def apk_tools(apk, sdk, env):
    tools = sdk / "build-tools/37.0.0"
    signature = checked(
        [tools / "apksigner", "verify", "--verbose", "--print-certs", apk], env=env
    ).decode()
    badging = checked([tools / "aapt2", "dump", "badging", apk], env=env).decode()
    match = re.search(
        r"(?m)^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'",
        badging,
    )
    c.require(match is not None, "APK identity missing")
    package, version, name = match.groups()
    c.require(
        package == "com.unciv.app"
        and name == "4.22.10"
        and "application-debuggable" in badging.splitlines(),
        "APK must retain pinned debug package/version name",
    )
    xml = checked(
        [tools / "aapt2", "dump", "xmltree", apk, "--file", "AndroidManifest.xml"],
        env=env,
    ).decode()
    return c.apk_certificate(signature), version, normalize_manifest(xml).encode()


def execution_order(requests):
    return sorted(
        requests, key=lambda request: (request["role"] != "clean", request["id"])
    )


def registry_directory(repo):
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return c.no_symlinks(Path(common.decode().strip()) / "l10n-apk-builder-v1")


def build_identity(request, builder_commit, toolchain, certificate):
    return c.digest(
        c.canonical_bytes(
            {
                "source_commit": c.SOURCE_COMMIT,
                "patch_set_sha256": request["patch_set_sha256"],
                "builder_commit": builder_commit,
                "toolchain": toolchain,
                "certificate_sha256": certificate,
            }
        )
    )


def apk_record(path, raw):
    return {"file": path.name, "sha256": c.digest(raw)}


def assemble_requests(requests, source_archive, versions, stage, work, sdk, env, cert):
    reference = stage / REFERENCE_NAME
    with zipfile.ZipFile(reference) as archive:
        originals = {
            name: archive.read(name.removeprefix("android/")) for name in c.TRANSLATIONS
        }
    ordered = execution_order(requests)
    edits = {r["id"]: c.apply_patches(originals, r["patches"]) for r in ordered}
    total_size = reference.stat().st_size
    artifacts = {}
    init_script = work / "l10n-builder.init.gradle"
    for request in ordered:
        identity = request["id"]
        code = versions[identity]
        expected = edits[identity]
        with tempfile.TemporaryDirectory(prefix=f"{identity}-", dir=work) as folder:
            source = Path(folder) / "source"
            materialize(source_archive, source)
            if request["role"] == "clean":
                c.require(
                    all(
                        (source / name).read_bytes() == raw
                        for name, raw in originals.items()
                    ),
                    "Pinned source translations differ from the reference APK",
                )
                assets = source_assets(source)
                report = checked(
                    c.gradle_command(
                        source, init_script, None, [":android:signingReport"]
                    ),
                    cwd=source,
                    env=env,
                    announce=True,
                ).decode()
                c.signing_gate(cert, c.signing_report_certificate(report))
            for name, raw in expected.items():
                if raw != originals[name]:
                    (source / name).write_bytes(raw)
            print(f"Building {identity} versionCode={code}", flush=True)
            checked(
                c.gradle_command(source, init_script, code, [":android:assembleDebug"]),
                cwd=source,
                env=env,
                announce=True,
            )
            apk = source / "android/build/outputs/apk/debug/Unciv-debug.apk"
            total_size += apk.stat().st_size
            c.require(
                total_size <= APK_LIMIT_BYTES,
                f"APK set would reach {total_size} bytes, over the 500 MB limit",
            )
            signer, version, normalized = apk_tools(apk, sdk, env)
            c.signing_gate(cert, signer)
            c.require(
                version == code, "Gradle versionCode override did not take effect"
            )
            payloads = c.audit_zip(apk, expected, assets)
            current = c.invariant_payloads(payloads, c.digest(normalized))
            if request["role"] == "clean":
                baseline = current
            c.require(
                current == baseline,
                "Nontranslation APK payload differs from the empty control",
            )
            target = stage / f"{identity}.apk"
            raw = apk.read_bytes()
            target.write_bytes(raw)
            artifacts[identity] = apk_record(target, raw)
            print(f"Verified {identity}: {artifacts[identity]['sha256']}", flush=True)
    return artifacts, total_size


def build(args, requests):
    repo = c.no_symlinks(args.repo)
    output = c.output_root(args.output_root, repo)
    c.require(not output.exists(), "Output directory exists; reuse the current APK set")
    builder_commit, init_script = frozen_builder(repo)
    sdk, jdk = c.no_symlinks(args.sdk), c.no_symlinks(args.jdk)
    reference = c.no_symlinks(args.reference_apk).read_bytes()
    c.require(
        c.digest(reference) == c.REFERENCE_APK_SHA256,
        "Reference APK is not the hash-pinned 1271048 build",
    )
    source_archive = git(repo, "archive", "--format=tar", c.SOURCE_COMMIT)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".l10n-build-", dir=output.parent
    ) as folder:
        work = Path(folder)
        stage, temp = work / "apks", work / "tmp"
        stage.mkdir()
        temp.mkdir()
        gradle_home = work / "gradle"
        env = tool_environment(jdk, sdk, gradle_home, temp)
        reference_path = stage / REFERENCE_NAME
        reference_path.write_bytes(reference)
        cert, version, _ = apk_tools(reference_path, sdk, env)
        c.require(version == "1271048", "Reference versionCode mismatch")
        toolchain = {
            "gradle": "9.4.1",
            "jdk": (jdk / "release").read_text(),
            "sdk": (sdk / "platforms/android-37.0/source.properties").read_text(),
            "build_tools": (sdk / "build-tools/37.0.0/source.properties").read_text(),
        }
        identities = {
            r["id"]: build_identity(r, builder_commit, toolchain, cert)
            for r in requests
        }
        prepare_gradle_cache(c.no_symlinks(args.gradle_cache), gradle_home)
        (work / "l10n-builder.init.gradle").write_bytes(init_script)
        versions = c.reserve_versions(
            registry_directory(repo) / "allocations", identities
        )
        artifacts, size = assemble_requests(
            requests, source_archive, versions, stage, work, sdk, env, cert
        )
        raw = c.canonical_bytes(
            {
                "source_commit": c.SOURCE_COMMIT,
                "builder_commit": builder_commit,
                "reference": apk_record(reference_path, reference),
                "artifacts": artifacts,
            }
        )
        (stage / "builds.json").write_bytes(raw)
        c.require(not output.exists(), "Output directory appeared during the build")
        stage.rename(output)
    print(
        c.canonical_bytes(
            {
                "builds": str(output / "builds.json"),
                "sha256": c.digest(raw),
                "apk_count": len(artifacts) + 1,
                "apk_bytes": size,
            }
        ).decode(),
        end="",
        flush=True,
    )


def verify_builds(path, checksum):
    path = c.no_symlinks(path)
    raw = path.read_bytes()
    c.require(c.digest(raw) == checksum, "Build list differs from approved SHA-256")
    value = c.json_bytes(raw)
    c.fields(value, {"source_commit", "builder_commit", "reference", "artifacts"})
    c.require(
        value["source_commit"] == c.SOURCE_COMMIT
        and re.fullmatch(r"[a-f0-9]{40}", value["builder_commit"]) is not None,
        "Invalid source/builder commit",
    )
    artifacts = value["artifacts"]
    c.require(type(artifacts) is dict and artifacts, "Build list contains no artifacts")
    for reference in (value["reference"], *artifacts.values()):
        c.fields(reference, {"file", "sha256"})
        name = reference["file"]
        c.require(
            re.fullmatch(r"[A-Za-z0-9_.-]+\.apk", name) is not None,
            "Invalid local APK filename",
        )
        apk = c.no_symlinks(path.parent / name)
        c.require(
            c.digest(apk.read_bytes()) == reference["sha256"],
            f"APK digest differs: {name}",
        )
    return len(artifacts)


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
    verify = commands.add_parser(
        "verify", help="Validate a compact build list and APK digests"
    )
    verify.add_argument("--builds", type=Path, required=True)
    verify.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            count = verify_builds(args.builds, args.sha256)
            print(f"Verified reference and {count} artifacts in {args.builds}")
            return 0
        raws = {
            name: c.no_symlinks(getattr(args, name)).read_bytes() for name in c.FROZEN
        }
        requests, _ = c.load_handoff(
            raws, plan_sha256=args.plan_sha256, requests_sha256=args.requests_sha256
        )
        if args.command == "check":
            print(
                f"Validated {len(requests['requests'])} requests for {c.SOURCE_COMMIT}"
            )
        else:
            build(args, requests["requests"])
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Localization APK builder failed: {exc}", file=sys.stderr)
        return 1
    return 0

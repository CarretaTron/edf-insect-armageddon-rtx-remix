"""Attach implementation evidence without erasing the previous target's audit."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--gpu-verification", type=Path, action="append", default=[])
    parser.add_argument("--raster-verification", type=Path, action="append", default=[])
    args = parser.parse_args()
    runtime_sha256 = None
    runtime_size = None
    if args.runtime:
        with args.runtime.open("rb") as stream:
            runtime_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        runtime_size = args.runtime.stat().st_size
    if args.gpu_verification and not args.runtime:
        raise ValueError("GPU replay evidence requires --runtime for binary provenance validation.")
    candidate = args.analysis / "new_candidate"
    current = json.loads((candidate / "validation.json").read_text(encoding="utf-8"))
    next_dir = args.analysis / "next_validation"
    previous = next_dir / "previous_target_validation.json"
    if not previous.exists():
        shutil.copy2(next_dir / "validation.json", previous)
    current["historicalPreviousTarget"] = {
        "path": str(previous),
        "retargetAuthorizedByUser": True,
        "reason": "The user authorized selecting a recurring static mesh instead of requiring the former 84/56 target.",
    }
    evidence = []
    replay_directories = set()
    for path in args.gpu_verification:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if (record.get("result") != "passed" or record.get("errors")
                or record.get("fixtureOnly") is not False
                or record.get("notGpuEvidence") is not False
                or record.get("evidenceKind") != "controlled_runtime_replay"):
            raise ValueError(f"Not a successful controlled GPU replay: {path}")
        if (record.get("runtimeSha256") != runtime_sha256
                or record.get("runtimeSize") != runtime_size
                or record.get("runtimeProvenance", {}).get("available") is not True):
            raise ValueError(f"GPU replay Runtime DLL does not match --runtime: {path}")
        replay_directory = record.get("replayDirectory")
        if not isinstance(replay_directory, str) or not replay_directory:
            raise ValueError(f"GPU replay has no replayDirectory provenance: {path}")
        replay_directories.add(Path(replay_directory).resolve())
        evidence.append({"path": str(path), "result": record})
    raster_evidence = []
    for path in args.raster_verification:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if (record.get("result") != "passed" or record.get("errors")
                or record.get("fixtureOnly") is not False
                or record.get("notGpuEvidence") is not False
                or record.get("evidenceKind") != "controlled_replay"):
            raise ValueError(f"Not a successful controlled raster comparison: {path}")
        profile_directory = record.get("profileDirectory")
        if (not isinstance(profile_directory, str) or not profile_directory
                or Path(profile_directory).resolve() not in replay_directories):
            raise ValueError(f"Raster evidence has no matching positive GPU replay: {path}")
        raster_evidence.append({"path": str(path), "result": record})
    implementation = {
        "class": "EdfStaticDrawDecoder",
        "scope": "Exact five stream hashes, raw index hash and original shader pair only.",
        "optIn": "rtx.edfStatic.enabled",
        "defaultEnabled": False,
        "defaultRasterOnly": True,
        "preservesRasterByDesign": True,
        "controlledReplayRasterVerified": bool(raster_evidence),
        "actualEdfRunStillRequired": True,
        "gpuReplay": evidence,
        "rasterComparison": raster_evidence,
        "status": ("compiled_controlled_replay_passed_game_test_pending"
                   if args.runtime and evidence and raster_evidence
                   else "implemented_validation_pending"),
    }
    if args.runtime:
        implementation["runtimeBinary"] = str(args.runtime)
        implementation["runtimeSha256"] = runtime_sha256
        implementation["runtimeSize"] = runtime_size
    current["implementation"] = implementation
    (next_dir / "validation.json").write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(next_dir / "validation.json")


if __name__ == "__main__":
    main()

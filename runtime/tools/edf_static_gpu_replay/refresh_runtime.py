"""Refresh only the rebuilt Runtime DLL in existing workspace test bundles."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--replay", type=Path, action="append", default=[])
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[3]
    source = args.runtime.resolve()
    package = args.package.resolve()
    paths = [source, package, *(path.resolve() for path in args.replay)]
    if any(not path.is_relative_to(workspace) for path in paths):
        raise ValueError("Only paths inside this workspace can be refreshed.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for architecture, relative in (("x64", "d3d9.dll"), ("x86", ".trex/d3d9.dll")):
        bundle = package / f"edf-diagnostics-{architecture}"
        manifest_path = bundle / "package-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        entries = [entry for entry in manifest["files"] if entry["path"] == relative]
        if len(entries) != 1:
            raise ValueError(f"Expected one Runtime manifest entry: {bundle}")
        target = (bundle / relative).resolve()
        if not target.is_relative_to(bundle.resolve()):
            raise ValueError("Runtime target escapes bundle.")
        shutil.copy2(source, target)
        entries[0].update(size=target.stat().st_size, sha256=digest)
        manifest["createdUtc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest["traceCorrelationFixApplied"] = True
        manifest["normalCaptureSpirvTypeFixApplied"] = True
        manifest["vertexCaptureSelectTypeFixApplied"] = True
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for replay in args.replay:
        directory = replay.resolve()
        relative = ".trex/d3d9.dll" if (directory / ".trex/NvRemixBridge.exe").is_file() else "d3d9.dll"
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory) or not target.is_file():
            raise ValueError(f"Invalid replay Runtime target: {target}")
        shutil.copy2(source, target)
    print(json.dumps({"runtimeSha256": digest, "size": source.stat().st_size,
                      "package": str(package), "replays": [str(path) for path in args.replay]}))


if __name__ == "__main__":
    main()

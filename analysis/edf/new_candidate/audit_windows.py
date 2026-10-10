"""Read-only integrity audit of all blobs and references in the three windows."""
import collections
import hashlib
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).parent
sys.path.insert(0, str(WORKSPACE / "runtime/tools/edf_capture"))
from analyze_capture import BlobStore, read_records


def references(value):
    if isinstance(value, dict):
        if "path" in value and "hash" in value:
            yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def main():
    windows = []
    for path in sorted((WORKSPACE / "captures/edf-static").iterdir()):
        if not (path / "records.jsonl").is_file():
            continue
        rows, errors = read_records(path / "records.jsonl")
        store = BlobStore(path)
        refs = list(references(rows))
        reference_paths = set()
        for ref in refs:
            reference_paths.add(ref["path"].replace("\\", "/"))
            try:
                store.read(ref)
            except (ValueError, OSError) as error:
                errors.append(str(error))
        disk_paths = set()
        for blob in (path / "blobs").glob("*.bin"):
            disk_paths.add(blob.relative_to(path).as_posix())
            if hashlib.sha1(blob.read_bytes()).hexdigest() != blob.stem:
                errors.append("Invalid content hash: " + blob.name)
        ends = [r for r in rows if r["type"] == "capture_end"]
        status = rows[-1] if rows and rows[-1]["type"] == "capture_status" else {}
        counts = collections.Counter(r["type"] for r in rows)
        complete = (len(ends) == 1 and ends[0]["reason"] == "window_complete"
                    and ends[0]["selectedDraws"] == counts["draw"]
                    and status.get("truncated") is False and status.get("error") is None
                    and status.get("written_records") == len(rows) - 1
                    and status.get("written_blobs") == len(disk_paths)
                    and reference_paths == disk_paths and not errors)
        windows.append({"capture": path.name,
                        "recordsSha256": hashlib.sha256((path / "records.jsonl").read_bytes()).hexdigest(),
                        "complete": complete, "status": status, "captureEnds": ends,
                        "drawCount": counts["draw"], "onDiskBlobs": len(disk_paths),
                        "uniqueBlobReferences": len(reference_paths),
                        "missingPaths": sorted(reference_paths - disk_paths),
                        "orphanPaths": sorted(disk_paths - reference_paths),
                        "integrityErrors": errors})
    result = {"allThreeCompleteAndIntegral": len(windows) == 3 and all(w["complete"] for w in windows),
              "totalVerifiedBlobs": sum(w["onDiskBlobs"] for w in windows), "windows": windows}
    (OUTPUT / "integrity.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"integral": result["allThreeCompleteAndIntegral"],
                      "blobs": result["totalVerifiedBlobs"],
                      "errors": [w["integrityErrors"] for w in windows]}))
    return int(not result["allThreeCompleteAndIntegral"])


if __name__ == "__main__":
    raise SystemExit(main())

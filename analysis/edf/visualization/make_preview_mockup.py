"""Illustrate the proposed presentation ROI offline; never loads the renderer."""
from pathlib import Path
import hashlib
import json

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "images"
OUTPUT = ROOT / "mockup"
ROI = (1040, 800, 820, 200)  # x, y, width, height in the captured 1920x1080 image.


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest_path = SOURCE / "viewer_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest["passed"] or manifest["finalEncoding"] != "display":
        raise ValueError("A validated display-encoded viewer export is required.")
    if OUTPUT.exists():
        raise ValueError("The mockup directory must not already exist.")
    outputs = {item["file"]: item for item in manifest["outputs"]}
    report = {"schema": 1, "kind": "offline_png_mockup_only", "compositionPerformedInGame": False,
              "rendererChanged": False, "roiXYWH": list(ROI), "samples": [],
              "viewerManifestSha256": sha256(manifest_path)}
    OUTPUT.mkdir()
    for sample in manifest["samples"]:
        prefix = f"request_{sample['requestId']}_frame_{sample['frameId']}"
        original = SOURCE / f"{prefix}_original_backbuffer.png"
        preview = SOURCE / f"{prefix}_final_offscreen_crop.png"
        for path in (original, preview):
            if sha256(path) != outputs[path.name]["sha256"]:
                raise ValueError(f"Viewer image changed: {path}")
        with Image.open(original) as image:
            base = image.convert("RGB")
        with Image.open(preview) as image:
            crop = image.convert("RGB")
        if base.size != (1920, 1080):
            raise ValueError("The illustrative ROI is only defined for 1920x1080.")
        x, y, width, height = ROI
        panel = Image.new("RGB", (width, height), (12, 16, 22))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(55, 210, 230), width=2)
        draw.text((12, 10), "OFFLINE MOCKUP | RTX preview | not an in-game composition", fill=(235, 235, 235))
        scale = min((width - 24) / crop.width, (height - 48) / crop.height)
        size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
        crop = crop.resize(size, Image.Resampling.NEAREST)
        panel.paste(crop, ((width - size[0]) // 2, 34 + (height - 48 - size[1]) // 2))
        composed = base.copy()
        composed.paste(panel, (x, y))
        before, after = np.asarray(base), np.asarray(composed)
        outside = np.ones(before.shape[:2], dtype=bool)
        outside[y:y+height, x:x+width] = False
        changed = np.any(before != after, axis=2)
        outside_changes = int(np.count_nonzero(changed & outside))
        if outside_changes:
            raise ValueError("Offline mockup changed pixels outside the ROI.")
        destination = OUTPUT / f"{prefix}_offline_preview_mockup.png"
        composed.save(destination)
        report["samples"].append({"frameId": sample["frameId"], "file": destination.name,
                                  "sha256": sha256(destination), "changedPixelsOutsideROI": outside_changes,
                                  "changedPixelsInsideROI": int(np.count_nonzero(changed & ~outside))})
    for filename, record in outputs.items():
        if sha256(SOURCE / filename) != record["sha256"]:
            raise ValueError(f"Viewer image modified: {filename}")
    report.update(passed=True, sourcePNGsPreserved=True)
    (OUTPUT / "mockup_verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "sampleCount": len(report["samples"]),
                      "changedPixelsOutsideROI": 0, "kind": report["kind"]}))


if __name__ == "__main__":
    main()

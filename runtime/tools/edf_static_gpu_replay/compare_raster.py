#!/usr/bin/env python3
"""Compare the three original MRT0 readbacks from baseline and EDF-profile replays."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any


SOURCE_FRAMES = (755, 1182, 1292)


def read_bitmap(path: Path) -> tuple[dict[str, Any], bytes, bytes]:
    data = path.read_bytes()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError(f"{path}: invalid BMP file header.")
    file_size, _, _, pixel_offset = struct.unpack_from("<IHHI", data, 2)
    header_size, width, signed_height, planes, bits, compression, image_size = struct.unpack_from("<IiiHHII", data, 14)
    if file_size != len(data) or header_size < 40 or pixel_offset < 14 + header_size:
        raise ValueError(f"{path}: inconsistent BMP file/header sizes.")
    if width <= 0 or signed_height == 0 or planes != 1 or bits != 32 or compression != 0:
        raise ValueError(f"{path}: requires a nonempty 32-bit BI_RGB bitmap from the replay.")
    height = abs(signed_height)
    row_bytes = width * 4
    payload_size = row_bytes * height
    if image_size not in (0, payload_size) or pixel_offset + payload_size > len(data):
        raise ValueError(f"{path}: truncated or inconsistent BMP pixel payload.")
    pixels = data[pixel_offset:pixel_offset + payload_size]
    if signed_height > 0:
        pixels = b"".join(pixels[row * row_bytes:(row + 1) * row_bytes]
                          for row in range(height - 1, -1, -1))
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = pixels[2::4], pixels[1::4], pixels[0::4]
    rgb = bytes(rgb)
    reference = tuple(rgb[:3])
    changed = 0
    minimum_x, minimum_y, maximum_x, maximum_y = width, height, -1, -1
    for index, color in enumerate(struct.iter_unpack("BBB", rgb)):
        if color != reference:
            changed += 1
            x, y = index % width, index // width
            minimum_x, minimum_y = min(minimum_x, x), min(minimum_y, y)
            maximum_x, maximum_y = max(maximum_x, x), max(maximum_y, y)
    bounds = None if not changed else {"xMin": minimum_x, "yMin": minimum_y,
                                       "xMaxInclusive": maximum_x, "yMaxInclusive": maximum_y}
    metadata = {
        "path": str(path.resolve()), "fileBytes": len(data), "width": width, "height": height,
        "bitsPerPixel": bits, "topDown": signed_height < 0,
        "fileSha256": hashlib.sha256(data).hexdigest(),
        "pixelBgraSha256": hashlib.sha256(pixels).hexdigest(),
        "visibleRgbSha256": hashlib.sha256(rgb).hexdigest(),
        "uniformVisibleRgb": not bool(changed), "referencePixel": {"x": 0, "y": 0, "rgb": list(reference)},
        "nonUniformPixels": changed, "nonUniformBounds": bounds,
    }
    return metadata, pixels, rgb


def compare(baseline: Path, profile: Path) -> dict[str, Any]:
    errors = []
    frames = []
    for frame in SOURCE_FRAMES:
        baseline_path = baseline / f"original_mrt0_{frame}.bmp"
        profile_path = profile / f"original_mrt0_{frame}.bmp"
        try:
            left, left_pixels, left_rgb = read_bitmap(baseline_path)
            right, right_pixels, right_rgb = read_bitmap(profile_path)
            dimensions_equal = (left["width"], left["height"]) == (right["width"], right["height"])
            files_equal = baseline_path.read_bytes() == profile_path.read_bytes()
            pixels_equal = dimensions_equal and left_pixels == right_pixels
            rgb_equal = dimensions_equal and left_rgb == right_rgb
            frame_errors = []
            if not dimensions_equal:
                frame_errors.append("Bitmap dimensions differ.")
            if not files_equal or not pixels_equal or not rgb_equal:
                frame_errors.append("Baseline/profile BMP bytes or decoded pixels differ.")
            if left["uniformVisibleRgb"] or right["uniformVisibleRgb"]:
                frame_errors.append("At least one MRT0 is uniform: visible raster geometry is not demonstrated.")
            errors.extend(f"Source frame {frame}: {error}" for error in frame_errors)
            frames.append({"sourceFrame": frame, "result": "passed" if not frame_errors else "failed",
                           "baseline": left, "profile": right, "dimensionsEqual": dimensions_equal,
                           "fileBytesEqual": files_equal, "decodedBgraPixelsEqual": pixels_equal,
                           "visibleRgbPixelsEqual": rgb_equal, "errors": frame_errors})
        except (OSError, ValueError, struct.error) as error:
            errors.append(f"Source frame {frame}: {error}")
            frames.append({"sourceFrame": frame, "result": "failed", "errors": [str(error)]})
    fixture_only = any((directory / "UNIT_TEST_FIXTURE_ONLY.txt").is_file() for directory in (baseline, profile))
    return {"schema": 1, "result": "passed" if not errors else "failed", "errors": errors,
            "evidenceKind": "synthetic_verifier_fixture" if fixture_only else "controlled_replay",
            "controlledReplay": True, "syntheticTextures": True,
            "fixtureOnly": fixture_only, "notGpuEvidence": fixture_only,
            "baselineDirectory": str(baseline.resolve()), "profileDirectory": str(profile.resolve()),
            "frames": frames,
            "limitations": ["Replay textures are synthetic; these are not original EDF texture pixels.",
                            "BMP readbacks precede Present; scene-only and AS execution require the separate Runtime verification.",
                            "Nonuniform bounds compare visible RGB with the top-left pixel, not an inferred object segmentation."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_directory", type=Path)
    parser.add_argument("profile_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.profile_directory / "raster_comparison.json"
    result = compare(args.baseline_directory, args.profile_directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": result["result"], "errors": result["errors"],
                      "fixtureOnly": result["fixtureOnly"], "comparison": str(output)}))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

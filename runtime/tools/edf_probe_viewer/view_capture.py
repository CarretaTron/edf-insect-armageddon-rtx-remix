#!/usr/bin/env python3
"""Read-only PNG viewer for bounded EDF RTX GPU readbacks (schema 1)."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw


FORMATS = {"linear_z": (100, 4), "albedo": (64, 4),
           "direct_diffuse_radiance": (97, 8), "final_offscreen": (97, 8)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, name, minimum=0):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
            f"Invalid integer {name}.")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fnv1a64(raw):
    value = 14695981039346656037
    for byte in raw:
        value = ((value ^ byte) * 1099511628211) & 0xffffffffffffffff
    return f"0x{value:016x}"


def safe_input(root, name):
    require(isinstance(name, str) and bool(name), "Invalid input filename.")
    path = (root / name).resolve()
    require(path.is_relative_to(root) and path.is_file(), f"Missing/escaping input file: {name}.")
    return path


def check_output(root, output):
    require(not output.is_relative_to(root) and not root.is_relative_to(output),
            "Output must be disjoint from the input capture directory.")
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
            "Output directory must be new or empty; existing results are never overwritten.")


def linear_to_srgb(values):
    values = np.asarray(values, dtype=np.float64)
    require(np.all(np.isfinite(values)), "Nonfinite linear RGB.")
    clipped = np.maximum(values, 0)
    return np.where(clipped <= .0031308, 12.92 * clipped,
                    1.055 * np.power(clipped, 1 / 2.4) - .055)


def rgb8(values):
    require(np.all(np.isfinite(values)), "Nonfinite display RGB.")
    return np.floor(np.clip(values, 0, 1) * 255 + .5).astype(np.uint8)


def display_rgb(values, encoding, exposure_ev):
    values = np.asarray(values, dtype=np.float64)
    require(np.all(np.isfinite(values)), "Nonfinite image RGB.")
    if encoding in ("display", "srgb"):
        return rgb8(values)
    if encoding == "linear":
        return rgb8(linear_to_srgb(values))
    require(encoding == "hdr" and math.isfinite(exposure_ev) and abs(exposure_ev) <= 32,
            "Unsupported color encoding/exposure.")
    exposed = np.maximum(values, 0) * 2 ** exposure_ev
    return rgb8(linear_to_srgb(exposed / (1 + exposed)))


def decode(raw, name, width, height):
    require(name in FORMATS and len(raw) == width * height * FORMATS[name][1],
            "Unexpected raw buffer name/length.")
    if name == "linear_z":
        return np.frombuffer(raw, dtype="<f4").reshape(height, width)
    if name == "albedo":
        packed = np.frombuffer(raw, dtype="<u4").reshape(height, width)
        # VK_FORMAT_A2B10G10R10_UNORM_PACK32 places red in the least significant bits.
        return np.stack([(packed & 1023) / 1023., ((packed >> 10) & 1023) / 1023.,
                         ((packed >> 20) & 1023) / 1023., (packed >> 30) / 3.], axis=2)
    return np.frombuffer(raw, dtype="<f2").reshape(height, width, 4).astype(np.float32)


def depth_data(depth, miss, flags):
    require(math.isfinite(miss) and np.all(np.isfinite(depth)), "Nonfinite depth/sentinel.")
    mask = depth != np.float32(miss)
    require(np.any(mask), "Capture contains no mesh hits.")
    distance = -depth if flags & 1 else depth
    require(np.all(distance[mask] > 0), "Depth sign contradicts the declared camera handedness.")
    rows, cols = np.nonzero(mask)
    bounds = [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())]
    return mask, distance, bounds


def depth_rgb(distance, mask, minimum, maximum):
    # One continuous scale shared by all frames. Misses stay black.
    t = np.clip((distance - minimum) / max(maximum - minimum, 1e-20), 0, 1)
    color = np.stack((t, 1 - np.abs(2 * t - 1), 1 - t), axis=2)
    color[~mask] = 0
    return rgb8(color)


def verification_cache(root, verification):
    if verification is None:
        return {}, None
    data = json.loads(verification.read_text(encoding="utf-8-sig"))
    require(data.get("schema") == 1 and data.get("passed") is True and data.get("errors") == [],
            "Verification cache has not passed.")
    samples = data.get("samples")
    require(isinstance(samples, list) and samples, "Verification has no samples.")
    cache = {}
    for sample in samples:
        require(sample.get("passed") is True and sample.get("gpuCompleted") is True,
                "Verification sample has not completed/passed.")
        metadata = safe_input(root, Path(sample["metadata"]).name)
        require(sha256(metadata) == sample["metadataSha256"], "Cached metadata SHA256 mismatch.")
        records = list(sample["images"])
        for target in sample.get("preservedTargets", []):
            records.extend([target["before"], target["after"]])
        for record in records:
            path = safe_input(root, Path(record["path"]).name)
            require(path not in cache, "Duplicate verification raw path.")
            cache[path] = record
    return cache, {"path": str(verification), "sha256": sha256(verification),
                   "method": "Recompute raw SHA256 against passed verifier's metadata-bound SHA256/FNV evidence."}


def checked_raw(root, name, byte_count, checksum, cache):
    path = safe_input(root, name)
    raw = path.read_bytes()
    require(len(raw) == byte_count, f"Truncated/extra data in {path.name}.")
    digest = hashlib.sha256(raw).hexdigest()
    cached = cache.get(path)
    if cached is not None:
        require(cached.get("sha256") == digest and cached.get("fnv1a64") == checksum
                and cached.get("byteCount") == len(raw), "Raw file differs from bound verification checksum.")
        method = "recomputed SHA256 + metadata-bound verified FNV"
    else:
        require(fnv1a64(raw) == checksum, f"FNV checksum mismatch for {path.name}.")
        method = "recomputed SHA256 and FNV"
    return raw, {"file": path.name, "sha256": digest, "fnv1a64": checksum,
                 "byteCount": len(raw), "checksumValidation": method}


def resolve_final_encoding(mode, proof_path, metadata_records):
    if mode != "auto":
        return mode, {"method": "explicit CLI", "encoding": mode}
    require(proof_path is not None, "Auto final encoding requires --encoding-proof; float16 storage does not prove HDR.")
    proof = json.loads(proof_path.read_text(encoding="utf-8-sig"))
    require(proof.get("schema") == 1 and proof.get("finalEncoding") in ("display", "srgb", "linear", "hdr"),
            "Invalid final encoding proof schema.")
    require(isinstance(proof.get("explanation"), str) and proof["explanation"]
            and isinstance(proof.get("sources"), list) and proof["sources"], "Encoding proof needs explanation and source evidence.")
    hashes = {x["sha256"] for x in metadata_records}
    require(hashes <= set(proof.get("metadataSha256", [])), "Encoding proof does not cover the current metadata hashes.")
    return proof["finalEncoding"], {"path": str(proof_path), "sha256": sha256(proof_path), "proof": proof}


def snapshot(root):
    paths = [path for path in sorted(root.rglob("*")) if path.is_file()]
    require(all(path.resolve().is_relative_to(root) for path in paths), "Input contains an escaping file link.")
    return {str(path.relative_to(root)): sha256(path) for path in paths}


def render(directory, output, *, verification=None, encoding_proof=None,
           final_encoding="auto", exposure_ev=0., expected_samples=3, padding=16):
    root, output = directory.resolve(), output.resolve()
    require(root.is_dir(), "Input capture directory is missing.")
    check_output(root, output)
    require(math.isfinite(exposure_ev) and abs(exposure_ev) <= 32, "Exposure EV must be finite and within [-32, 32].")
    integer(expected_samples, "expected_samples", 1)
    integer(padding, "padding")
    require(padding <= 512, "Crop padding limit is 512 pixels.")
    protected_before = snapshot(root)
    cache, cache_provenance = verification_cache(root, verification)
    files = sorted(root.glob("edf_pt_request_*_frame_*.json"))
    require(len(files) == expected_samples, f"Expected {expected_samples} metadata samples, found {len(files)}.")
    metadata_records, frames, seen = [], [], set()
    common_min, common_max = math.inf, -math.inf
    for file in files:
        file = safe_input(root, file.name)
        data = json.loads(file.read_text(encoding="utf-8-sig"))
        require(data.get("schema") == 1 and data.get("gpuCompleted") is True
                and data.get("compositionPerformed") is False, "Unsupported/uncompleted/composed capture.")
        frame = integer(data.get("frameId"), "frameId")
        request = integer(data.get("requestId"), "requestId", 1)
        require((frame, request) not in seen, "Duplicate frame/request.")
        seen.add((frame, request))
        width, height = data.get("rendererResolution", [None, None])
        integer(width, "width", 1)
        integer(height, "height", 1)
        require(width * height <= 4_194_304 and data.get("injectionTargetExtent") == [width, height],
                "Unsupported/inconsistent image dimensions.")
        require(data.get("cameraLastUpdateFrame") == frame and data.get("currentFrameDrawCount") == 1,
                "Camera/draw frame mismatch.")
        flags = integer(data.get("rendererCameraFlags"), "rendererCameraFlags")
        miss = float(data.get("missLinearZ"))
        images = data.get("images")
        require(isinstance(images, list) and len(images) == 4
                and {x.get("name") for x in images} == set(FORMATS), "Missing/duplicate image entries.")
        image_reports = []
        for item in images:
            name = item["name"]
            format_id, bpp = FORMATS[name]
            require(item.get("format") == format_id and item.get("bytesPerPixel") == bpp
                    and item.get("width") == width and item.get("height") == height
                    and item.get("byteCount") == width * height * bpp,
                    f"Invalid format/extent/byte count for {name}.")
            require(item.get("checksumAlgorithm") == "fnv1a64", "Unsupported checksum algorithm.")
            raw, report = checked_raw(root, item.get("file"), item["byteCount"], item.get("checksum"), cache)
            path = safe_input(root, item.get("file"))
            cached = cache.get(path)
            if cached is not None:
                require(cached.get("format") == format_id and cached.get("name") == name,
                        "Raw format/name differs from bound verification.")
            decoded = decode(raw, name, width, height)
            require(np.all(np.isfinite(decoded)), f"Nonfinite values in {name}.")
            report.update(name=name, format=format_id, bytesPerPixel=bpp, nonfiniteValues=0)
            if name == "linear_z":
                mask, distance, bounds = depth_data(decoded, miss, flags)
                require(int(mask.sum()) == data.get("depthHitCount") and bounds == data.get("hitBounds"),
                        "Reported depth count/bounds disagree with the raw buffer.")
                minimum, maximum = float(distance[mask].min()), float(distance[mask].max())
                common_min, common_max = min(common_min, minimum), max(common_max, maximum)
            else:
                rgb, alpha = decoded[:, :, :3], decoded[:, :, 3]
                report.update(rgbMinimum=rgb.min(axis=(0, 1)).tolist(), rgbMaximum=rgb.max(axis=(0, 1)).tolist(),
                              negativeRgbValues=int((rgb < 0).sum()), rgbValuesAboveOne=int((rgb > 1).sum()),
                              alphaMinimum=float(alpha.min()), alphaMaximum=float(alpha.max()),
                              alphaUsedForCoverage=False)
            image_reports.append(report)
        metadata_records.append({"file": file.name, "sha256": sha256(file)})
        frames.append({"data": data, "width": width, "height": height,
                       "images": image_reports, "bounds": bounds,
                       "distanceRange": [minimum, maximum]})
    frames.sort(key=lambda item: (item["data"]["requestId"], item["data"]["frameId"]))
    require(len({(x["width"], x["height"]) for x in frames}) == 1, "Comparison requires matching frame extents.")
    encoding, encoding_report = resolve_final_encoding(final_encoding, encoding_proof, metadata_records)
    for frame in frames:
        for image in frame["images"]:
            name = image["name"]
            if name == "linear_z":
                continue
            image_encoding = "hdr" if name == "direct_diffuse_radiance" else ("linear" if name == "albedo" else encoding)
            image.update(displayEncoding=image_encoding,
                         displayExposureEV=exposure_ev if image_encoding == "hdr" else None,
                         negativeDisplayClampRequired=image["negativeRgbValues"] > 0,
                         upperDisplayClampRequired=image["rgbValuesAboveOne"] > 0 and image_encoding != "hdr",
                         reinhardApplied=image_encoding == "hdr",
                         srgbTransferApplied=image_encoding in ("linear", "hdr"))
    width, height = frames[0]["width"], frames[0]["height"]
    all_bounds = np.asarray([x["bounds"] for x in frames])
    common_roi = [max(0, int(all_bounds[:, 0].min())-padding), max(0, int(all_bounds[:, 1].min())-padding),
                  min(width, int(all_bounds[:, 2].max())+padding+1), min(height, int(all_bounds[:, 3].max())+padding+1)]
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "passed": False, "inputDirectory": str(root), "outputDirectory": str(output),
                "compositionPerformed": False, "originalFilesPreserved": False,
                "exposureEV": exposure_ev, "directRadianceTransform": "max(0,RGB)*2^EV; Reinhard x/(1+x); exact IEC sRGB transfer",
                "albedoTransform": "linear UNORM RGB; exact IEC sRGB transfer; alpha ignored",
                "finalEncoding": encoding, "finalEncodingEvidence": encoding_report,
                "depthTransform": "positive view-space distance (-z for camera flags & 1); shared blue/green/red scale; black misses",
                "depthRange": [common_min, common_max], "commonCropBoxExclusive": common_roi,
                "checksumCacheEvidence": cache_provenance, "metadata": metadata_records,
                "samples": [], "outputs": [], "protectedInputSha256": protected_before}
    panels = {name: [] for name in [*FORMATS, "hit_mask", "original_backbuffer"]}

    def save_image(array, relative):
        path = output / relative
        Image.fromarray(array).save(path, format="PNG")
        manifest["outputs"].append({"file": relative, "width": int(array.shape[1]),
                                    "height": int(array.shape[0]), "sha256": sha256(path)})

    for frame in frames:
        data = frame["data"]
        basename = f"request_{data['requestId']}_frame_{data['frameId']}"
        arrays = {x["name"]: decode(safe_input(root, x["file"]).read_bytes(), x["name"], width, height)
                  for x in data["images"]}
        mask, distance, bounds = depth_data(arrays["linear_z"], float(data["missLinearZ"]), data["rendererCameraFlags"])
        display = {"linear_z": depth_rgb(distance, mask, common_min, common_max),
                   "hit_mask": np.repeat((mask.astype(np.uint8) * 255)[:, :, None], 3, axis=2),
                   "albedo": display_rgb(arrays["albedo"][:, :, :3], "linear", 0),
                   "direct_diffuse_radiance": display_rgb(arrays["direct_diffuse_radiance"][:, :, :3], "hdr", exposure_ev),
                   "final_offscreen": display_rgb(arrays["final_offscreen"][:, :, :3], encoding, exposure_ev)}
        target0 = [x for x in data.get("preservedTargets", []) if x.get("slot") == 0]
        backbuffer_report = None
        if target0:
            require(len(target0) == 1, "Duplicate original backbuffer.")
            target = target0[0]
            require(target["format"] in (37, 43, 44, 50) and target["byteCount"] == width * height * 4
                    and target["width"] == width and target["height"] == height
                    and target.get("unchanged") is True, "Unsupported original backbuffer.")
            raw, before_report = checked_raw(root, target["beforeFile"], target["byteCount"], target["beforeFnv1a64"], cache)
            after, after_report = checked_raw(root, target["afterFile"], target["byteCount"], target["afterFnv1a64"], cache)
            require(raw == after, "Original backbuffer changed.")
            backbuffer_report = {"slot": 0, "format": target["format"], "before": before_report,
                                 "after": after_report, "fullMemcmpEqual": True}
            rgba = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
            display["original_backbuffer"] = rgba[:, :, [2, 1, 0] if target["format"] in (44, 50) else [0, 1, 2]]
        individual_roi = [max(0, bounds[0]-padding), max(0, bounds[1]-padding),
                          min(width, bounds[2]+padding+1), min(height, bounds[3]+padding+1)]
        for name, image in display.items():
            save_image(image, f"{basename}_{name}.png")
            x0, y0, x1, y1 = individual_roi
            save_image(image[y0:y1, x0:x1], f"{basename}_{name}_crop.png")
            x0, y0, x1, y1 = common_roi
            panels[name].append((data["requestId"], data["frameId"], Image.fromarray(image[y0:y1, x0:x1])))
        manifest["samples"].append({"requestId": data["requestId"], "frameId": data["frameId"],
                                    "width": width, "height": height, "hitCount": int(mask.sum()),
                                    "hitBoundsInclusive": bounds, "cropBoxExclusive": individual_roi,
                                    "distanceRange": frame["distanceRange"], "sourceImages": frame["images"],
                                    "originalBackbuffer": backbuffer_report,
                                    "gpuCompleted": True, "alphaUsedForCoverage": False})
    for name, items in panels.items():
        if not items:
            continue
        tile_width, tile_height = items[0][2].size
        comparison = Image.new("RGB", (tile_width * len(items), tile_height + 28), (28, 28, 28))
        draw = ImageDraw.Draw(comparison)
        for n, (request, frame_id, image) in enumerate(items):
            draw.text((n * tile_width + 6, 8), f"Request {request} / frame {frame_id} / {name}", fill="white")
            comparison.paste(image, (n * tile_width, 28))
        save_image(np.asarray(comparison), f"comparison_{name}.png")
    protected_after = snapshot(root)
    require(protected_before == protected_after, "Input files changed while the viewer was running.")
    manifest.update(passed=True, originalFilesPreserved=True, protectedInputFileCount=len(protected_before))
    (output / "viewer_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification", type=Path, help="Optional passed probe_verification.json SHA256/FNV cache.")
    parser.add_argument("--final-encoding", choices=("auto", "display", "srgb", "linear", "hdr"), default="auto")
    parser.add_argument("--encoding-proof", type=Path, help="Metadata-bound source/config proof needed for auto.")
    parser.add_argument("--exposure-ev", type=float, default=0)
    parser.add_argument("--expected-samples", type=int, default=3)
    parser.add_argument("--crop-padding", type=int, default=16)
    args = parser.parse_args()
    try:
        result = render(args.capture_directory, args.output, verification=args.verification,
                        encoding_proof=args.encoding_proof, final_encoding=args.final_encoding,
                        exposure_ev=args.exposure_ev, expected_samples=args.expected_samples, padding=args.crop_padding)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Viewer rejected the capture: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": result["passed"], "sampleCount": len(result["samples"]),
                      "originalFilesPreserved": result["originalFilesPreserved"],
                      "pngCount": len(result["outputs"]), "depthRange": result["depthRange"],
                      "finalEncoding": result["finalEncoding"], "manifest": str(args.output.resolve() / "viewer_manifest.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Consolidate measured native replay evidence without claiming an actual EDF PT run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME_SHA256 = '93b265b00cb55b51b25c5b7f0495d72a9e0cf76d55abe1462f932501843b4641'
BASE = '0e18e55c04309c08e6206e768bc72b24ac83f853'


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path: Path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_package(architecture):
    bundle = ROOT / f'packages/edf-pt-probe/edf-diagnostics-{architecture}'
    manifest_path = bundle / 'package-manifest.json'
    manifest = read(manifest_path)
    require(manifest['runtimeBaseCommit'] == BASE, 'Package base revision changed.')
    expected = {entry['path'].replace('\\', '/') for entry in manifest['files']}
    actual = {str(path.relative_to(bundle)).replace('\\', '/') for path in bundle.rglob('*') if path.is_file()}
    require(actual == expected | {'package-manifest.json'}, f'{architecture} package file inventory differs.')
    for entry in manifest['files']:
        path = bundle / entry['path']
        require(path.is_file() and path.stat().st_size == entry['size'], f'Package size mismatch: {path}.')
        require(digest(path) == entry['sha256'], f'Package SHA256 mismatch: {path}.')
        require('.pdb' not in path.name.lower(), f'Debug artifact was packaged: {path}.')
    renderer = bundle / ('.trex/d3d9.dll' if architecture == 'x86' else 'd3d9.dll')
    require(digest(renderer) == RUNTIME_SHA256, 'Package renderer is not the tested binary.')
    return {'architecture': architecture, 'bundle': str(bundle), 'manifestSha256': digest(manifest_path),
            'filesChecked': len(manifest['files']), 'runtimeBinary': str(renderer),
            'runtimeSize': renderer.stat().st_size, 'runtimeSha256': RUNTIME_SHA256, 'passed': True}


def collect_case(architecture, illuminated):
    variant = 'lit' if illuminated else 'probe'
    name = f'{architecture}_{"lit" if illuminated else "probe"}_verification.json'
    verification_path = HERE / name
    verification = read(verification_path)
    require(verification['passed'] and not verification['fixtureOnly'] and not verification['notGpuEvidence'],
            f'{name}: native GPU evidence failed.')
    require(verification['runtimeSha256'] == RUNTIME_SHA256, 'Replay renderer provenance changed.')
    require(verification['cameraLifecycle']['passed'], 'Camera lifecycle test failed.')
    samples = verification['samples']
    require(len(samples) == 3 and [sample['frameId'] for sample in samples] == [0, 1, 2],
            'Expected exactly three original-camera native GPU samples.')
    case = ROOT / f'runtime/_build/edf-pt-{architecture}-{variant}'
    events = [json.loads(line) for line in (case / 'replay_stdout.jsonl').read_text().splitlines()]
    require(events[-1]['event'] == 'success' and events[-1]['hresultChecks'] == 1303, 'Replay D3D9 calls failed.')
    frames = [event for event in events if event['event'] == 'frame']
    require(len(frames) == 6 and frames[3]['targetSkipped'] and frames[3]['draws'] == 1,
            'Missing-mesh negative control was not exercised.')
    require(all(frame['draws'] == 2 for frame in frames if frame['replayFrame'] != 3),
            'Expected positive draw and mutated-vertex negative control.')
    runtime_log = case / 'rtx-remix/logs/remix-dxvk.log'
    log = runtime_log.read_text(encoding='utf-8-sig')
    require(len(re.findall(r'\[EDF-Static-Probe\] stage=gpu_completed ', log)) == 3,
            'Expected exactly three GPU completion callbacks.')
    require('stage=readback_failed' not in log, 'Runtime readback failed.')
    preserved = []
    for sample in samples:
        metadata = read(Path(sample['metadata']))
        require(metadata['gpuCompleted'] and not metadata['compositionPerformed'], 'Invalid offscreen result.')
        require(metadata['originalTargetsUnchanged'] and len(metadata['preservedTargets']) == 4,
                'All four original targets were not proven preserved.')
        require(metadata['rendererViewError'] == 0 and metadata['rendererProjectionError'] == 0
                and metadata['jitterPixels'] == [0, 0], 'Actual renderer camera differs from decoded camera.')
        pixels = metadata['rendererResolution'][0] * metadata['rendererResolution'][1]
        images = {entry['name']: entry for entry in metadata['images']}
        directory = Path(sample['metadata']).parent
        z = np.fromfile(directory / images['linear_z']['file'], dtype='<f4')
        radiance = np.fromfile(directory / images['direct_diffuse_radiance']['file'], dtype='<f2').reshape(pixels, 4)
        hits = np.isfinite(z) & (z != np.float32(metadata['missLinearZ']))
        positive = np.any(radiance[:, :3] > 0, axis=1)
        require(np.isfinite(radiance[:, :3]).all(), 'Nonfinite direct radiance.')
        if illuminated:
            require(np.array_equal(hits, positive), 'Controlled light did not illuminate exactly the mesh-hit pixels.')
        else:
            require(not positive.any(), 'Initial dark control unexpectedly had direct radiance.')
        preserved.append({'frame': sample['frameId'], 'gpuHitCount': int(hits.sum()),
                          'directRadiancePositivePixels': int(positive.sum()), 'originalTargetsUnchanged': True,
                          'independentCpuRayTriangles': sample['independentCpuRayTriangles'],
                          'metadata': sample['metadata'], 'metadataSha256': digest(Path(sample['metadata']))})
    comparison_name = f'{architecture}_{"lit_" if illuminated else ""}raster_preservation.json'
    comparison_path = HERE / comparison_name
    comparison = read(comparison_path)
    require(comparison['result'] == 'passed' and not comparison['fixtureOnly'], 'Original raster comparison failed.')
    return {'architecture': architecture, 'illuminated': illuminated, 'passed': True,
            'runtimeLog': str(runtime_log), 'runtimeLogSha256': digest(runtime_log),
            'verification': str(verification_path), 'verificationSha256': digest(verification_path),
            'rasterComparison': str(comparison_path), 'rasterComparisonSha256': digest(comparison_path),
            'replayExecutableSha256': digest(case / 'edf_static_gpu_replay.exe'),
            'configuration': str(case / 'rtx.conf'), 'configurationSha256': digest(case / 'rtx.conf'),
            'replayFrameCount': 6, 'successfulHresultChecks': 1303, 'gpuCompletedFrames': 3,
            'missingCameraFrame': 3, 'recoveryCameraFrame': 4, 'mutatedVertexDrawRejectedOnProbeFrames': True,
            'debugBuildExitCommonObjects': re.findall(r'\[(\d+)\] common device objects were not disposed of', log),
            'samples': preserved}


def main():
    compiled = ROOT / 'runtime/_CompDebugOptimized_x64/src/d3d9/d3d9.dll'
    require(digest(compiled) == RUNTIME_SHA256, 'Compiled binary changed after native tests.')
    report = {'schema': 1, 'passed': True, 'evidenceKind': 'native_offscreen_gpu_replays_from_measured_edf_data',
              'runtimeBaseCommit': BASE, 'runtimeSha256': RUNTIME_SHA256,
              'runtimeSize': compiled.stat().st_size,
              'packages': [verify_package(architecture) for architecture in ('x64', 'x86')],
              'replays': [collect_case(architecture, lit) for architecture in ('x64', 'x86') for lit in (False, True)],
              'actualEdfProbeRunPending': True, 'visualCompositionValidated': False,
              'globalPathTracingEnabled': False, 'decoderExpanded': False,
              'limits': ['Native replay textures are synthetic; original EDF materials/lighting are not recovered.',
                         'Full comparison covers the backbuffer and three original color MRTs, not future partial composition.',
                         'Original game DLL and log remain unchanged and contain the previous scene-only prototype.',
                         'Existing debug-build shutdown object notices also occur in the raster baseline.']}
    output = HERE / 'delivery_verification.json'
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    validation_path = ROOT / 'analysis/edf/next_validation/validation.json'
    validation = read(validation_path)
    validation['pathTracingProbe'] = {
        'status': 'compiled_native_x64_x86_gpu_validated_actual_edf_probe_pending',
        'runtimeSha256': RUNTIME_SHA256, 'runtimeSize': compiled.stat().st_size,
        'bundle': str(ROOT / 'packages/edf-pt-probe/edf-diagnostics-x86'),
        'profile': str(ROOT / 'profiles/edf-pt-probe/rtx.conf'),
        'instructions': str(ROOT / 'profiles/edf-pt-probe/TESTAR.md'),
        'report': str(HERE / 'REPORT.md'), 'deliveryVerification': str(output),
        'deliveryVerificationSha256': digest(output),
        'illuminationAbEvidence': str(HERE / 'analyze_probe_illumination.json'),
        'scope': 'Exactly one 252-vertex/144-triangle mesh, current camera and one visible instance; bounded offscreen only.',
        'controls': {'arm': 'F9', 'cancelPending': 'F8', 'autorun': False, 'framesPerRequest': 1},
        'actualEdfProbeRunPending': True, 'visualCompositionAllowed': False,
        'cameraGuardsRelaxed': False, 'otherDrawsOrShadersChanged': False,
        'nativeEvidence': report['replays'], 'actualGameEvidence': validation['implementation']['actualEdfEvidence']}
    validation_path.write_text(json.dumps(validation, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'packages': 2, 'nativeReplays': 4, 'gpuCompletedFrames': 12,
                      'illuminatedGpuFrames': 6, 'actualEdfProbeRunPending': True, 'output': str(output)}))


if __name__ == '__main__':
    main()

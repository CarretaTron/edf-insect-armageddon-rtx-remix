"""Consolidate the pinned, post-lifetime-fix evidence without changing inputs."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
TOOLS = ROOT / 'runtime/tools/edf_static_gpu_replay'
sys.path.insert(0, str(TOOLS))
import verify_composition
import run_composition_matrix

RUNTIME_SHA = 'b051fe14b0965c7a45d3a2370d8dc68fc7a18e6ae8ba43ef4a5c4e0d7d745f9e'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def evidence(path):
    return {'path': str(path), 'sha256': sha(path)}


def native_matrix(architecture, expected_count):
    directory = ROOT / f'runtime/_build/edf-composition-retained-{architecture}'
    path = directory / 'matrix_verification.json'
    data = read(path)
    assert data['passed'] and data['executed'] and data['notEdfGameplay']
    assert len(data['cases']) == expected_count
    cases = []
    for case in data['cases']:
        assert case['passed'] and case['runtimeSha256After'] == RUNTIME_SHA
        assert sha(Path(case['runtimeLog'])) == case['runtimeLogSha256']
        assert not verify_composition.audit_runtime_log(Path(case['runtimeLog']))
        probe = read(Path(case['probeVerification']))
        assert probe['passed'] and probe['runtimeSha256'] == RUNTIME_SHA
        sample = probe['samples'][0]
        item = {'name': case['case'], 'passed': True,
                'probe': evidence(Path(case['probeVerification'])),
                'runtimeLog': evidence(Path(case['runtimeLog'])),
                'callbackCompletion': case['callbackCompletion'],
                'frame': sample['frameId'], 'cpuGeometry': sample['independentCpuRayTriangles'],
                'radianceNonzeroObserved': sample['radianceNonzeroObserved']}
        if 'compositionVerification' in case:
            composition_path = Path(case['compositionVerification'])
            composition = read(composition_path)
            assert composition['passed'] and composition['runtimeSha256'] == RUNTIME_SHA
            panel = composition['samples'][0]
            assert panel['outsideRoiChangedPixels'] == 0 and panel['originalTargetsUnchanged']
            item.update(composition=evidence(composition_path),
                        outsideRoiChangedPixels=panel['outsideRoiChangedPixels'],
                        insideRoiChangedPixels=panel['insideRoiChangedPixels'],
                        cpuPanelMaximumByteDifference=panel['cpuPanelMaximumByteDifference'],
                        originalsPreserved=4, gpuCompleted=panel['gpuCompleted'],
                        currentFrameComposition=panel['compositionPerformed'])
        else:
            item['currentFrameComposition'] = False
        cases.append(item)
    return {'architecture': architecture, 'passed': True, 'caseCount': len(cases),
            'matrix': evidence(path), 'cases': cases}


def sync_case(directory, name):
    folder = ROOT / 'runtime/_build' / directory / name
    log = folder / 'rtx-remix/logs/remix-dxvk.log'
    text = log.read_text(encoding='utf-8-sig', errors='replace')
    result = read(folder / 'composition_case_result.json')
    manifest = read(folder / 'composition_case.json')
    assert manifest['runtimeSha256Before'] == RUNTIME_SHA
    headers = re.findall(r'err:\s+\[VK_DEBUG_REPORT\] Code (-?\d+): ([^\n]+)', text)
    codes = sorted({code for code, _ in headers})
    hazards = [re.sub(r'0x[0-9a-fA-F]+', '<handle>', message) for _, message in headers
               if re.search(r'hazard detected', message, re.I)]
    activation = run_composition_matrix.validation_activation(log, [folder / 'replay_stdout.jsonl', folder / 'replay_stderr.log'])
    return {'name': name, 'result': evidence(folder / 'composition_case_result.json'),
            'passed': result['passed'], 'runtimeLog': evidence(log),
            'layerEnabled': activation['layerActivatedInRuntime'],
            'syncDiagnosticsObserved': bool(hazards), 'activationBannerObserved': activation['syncActivationObserved'],
            'errorCodes': codes, 'errorHeaderCount': len(headers),
            'hazards': hazards, 'queueSubmitInvalidationCount': text.count('VUID-vkQueueSubmit-pCommandBuffers-00070'),
            'globalAuditFailureCount': len(verify_composition.audit_runtime_log(log))}


def main():
    matrices = [native_matrix('x64', 12), native_matrix('x86', 6)]
    baseline = sync_case('edf-composition-retained-sync-baseline-x86', 'disabled')
    sync = [sync_case('edf-composition-retained-sync-positive-x86', 'positive_camera0'),
            sync_case('edf-composition-retained-sync-reset-x86', 'reset_after')]
    for case in sync:
        case['errorCodesAbsentFromBaseline'] = sorted(set(case['errorCodes']) - set(baseline['errorCodes']))
        case['hazardsAbsentFromBaseline'] = sorted(set(case['hazards']) - set(baseline['hazards']))
        assert case['queueSubmitInvalidationCount'] == 0
        assert not case['errorCodesAbsentFromBaseline'] and not case['hazardsAbsentFromBaseline']
    integrity_path = OUT / 'integrity.json'
    integrity = read(integrity_path)
    assert integrity['passed'] and integrity['rendererExpectedSha256'] == RUNTIME_SHA
    tests_path = OUT / 'python_tests_postfix.log'
    tests_text = tests_path.read_text(encoding='utf-8-sig')
    assert re.search(r'Ran 115 tests', tests_text) and re.search(r'^OK$', tests_text, re.M)
    contracts = read(OUT / 'source_audit.json')
    assert contracts['passed'] and contracts['testCount'] == 15
    report = {'schema': 1, 'generatedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'one_current_frame_diagnostic_panel_exact_static_mesh',
              'runtimeSha256': RUNTIME_SHA, 'runtimeBytes': 189184512,
              'build': evidence(ROOT / 'runtime/_build/edf_composition_build_lifetime_fix.log'),
              'defaultEnabled': False, 'controlledNativeChecksPassed': True,
              'controlledNativeCaseCount': 18, 'native': matrices,
              'syncValidation': {'globalPassed': False, 'baseline': baseline, 'postFix': sync,
                                 'newDescriptorPoolLifetimeErrorRemoved': True,
                                 'existingBaselineErrorsNotSuppressed': True},
              'automatedPythonTests': {'passed': True, 'count': 115, 'log': evidence(tests_path)},
              'staticSourceContracts': {'passed': True, 'count': 15, 'report': evidence(OUT / 'source_audit.json')},
              'deploymentAssertions': {'passed': True, 'count': 34,
                    'report': evidence(ROOT / 'runtime/_build/edf_composition_deployment_tests/6afca7f7609c4de0a4199cae9c5d9e46/deployment_verification.json')},
              'integrity': evidence(integrity_path), 'experimentalPackagePrepared': True,
              'compositionExperimentApproved': False, 'actualEdfCompositionPending': True,
              'presentationDisplayVerified': False, 'realtimePathTracingProven': False,
              'spatialObjectReplacementProven': False, 'activeGameInstallTouched': False,
              'syntheticReplayTextures': True, 'originalEdfShadersGeometryCamera': True,
              'limits': ['Khronos validation also rejects the composition-disabled baseline; no global Vulkan approval.',
                         'Readbacks verify completed GPU pixels, not physical display or EDF gameplay composition.',
                         'Depth/stencil is not bound or written by the compositor; full binary comparison is not implemented.',
                         'Only the exact mesh and one manually requested frame are supported.']}
    for arch in ('x64', 'x86'):
        comparison_path = OUT / f'raster_comparison_retained_{arch}.json'
        assert read(comparison_path)['result'] == 'passed'
    report['rasterComparisons'] = [evidence(OUT / f'raster_comparison_retained_{arch}.json') for arch in ('x64', 'x86')]
    report['previewImages'] = [evidence(path) for path in sorted((OUT / 'images').glob('*.png'))]
    (OUT / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    previous_path = ROOT / 'analysis/edf/next_validation/validation.json'
    previous = read(previous_path)
    preparation = previous['pathTracingProbe']['compositionPreparation']
    preparation.update(status='implemented_and_native_gpu_verified_global_vulkan_validation_pending',
                       implementationAvailable=True, controlledNativeChecksPassed=True,
                       report=str(OUT / 'validation.json'), reportSha256=sha(OUT / 'validation.json'),
                       runtimeSha256=RUNTIME_SHA, globalVulkanValidationPassed=False,
                       actualEdfCompositionPending=True, presentationDisplayVerified=False,
                       realTimePathTracingProven=False)
    previous_path.write_text(json.dumps(previous, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'controlledNativeChecksPassed': True, 'caseCount': 18,
                      'globalVulkanValidationPassed': False, 'actualEdfPending': True}))


if __name__ == '__main__':
    main()

"""Merge the audited recapture gate without confusing old matches with new ones."""
import json
from pathlib import Path


def main():
    root = Path(__file__).parent
    result = json.loads((root / 'validation.json').read_text())
    audit = json.loads((root / 'audit/audit.json').read_text())
    comparison = json.loads((root / 'comparison.json').read_text())
    windows = []
    failures = [e for e in result['failures'] if e.get('stage') != 'recapture_verification']
    for window in audit['windows']:
        inventory = window['inventory']
        status = inventory['status']
        inspection = json.loads((root / window['capture'] / 'report.json').read_text())
        ranges_errors = [e for draw in inspection['draws'] for e in draw['errors']]
        integrity = (inventory['complete'] and not window['parseErrors'] and
                     not window['integrityErrors'] and not window['missingPaths'] and
                     not window['orphanPaths'] and not ranges_errors and
                     status['written_blobs'] == window['onDiskBlobs'])
        mesh_present = bool(window['exactMeshMatches'])
        summary = {'capture': window['capture'], 'ordinal': window['ordinal'],
                   'userProcedure': window['userProcedure'], 'frames': inventory['frames'],
                   'selectedDraws': inventory['recordedDraws'], 'windowComplete': inventory['complete'],
                   'captureEndReason': window['captureEnds'][-1]['reason'],
                   'truncated': status['truncated'], 'writerError': status['error'],
                   'integrityVerified': bool(integrity), 'verifiedBlobs': window['onDiskBlobs'],
                   'missingBlobs': len(window['missingPaths']), 'orphanBlobs': len(window['orphanPaths']),
                   'integrityErrors': window['integrityErrors'], 'drawRangeErrors': ranges_errors,
                   'maxQueueBytes': status['max_queue_bytes'], 'peakQueueBytes': status['peak_queue_bytes'],
                   'requiredMeshPresent': mesh_present, 'exactMeshMatches': window['exactMeshMatches'],
                   'drawsWith84Vertices56Triangles': window['count84Vertices56TrianglesDraws'],
                   'exactPositionSequenceOccurrences': window['exact672BytePositionOccurrencesInAllBlobs'],
                   'samePositionRangeDraws': window['samePositionRangeDraws'],
                   'sameIndexRangeDraws': window['sameIndexRangeDraws']}
        windows.append(summary)
        if not integrity:
            failures.append({'stage': 'recapture_verification', 'capture': window['capture'],
                             'error': 'Capture integrity or completion did not pass.'})
        if not mesh_present:
            failures.append({'stage': 'recapture_verification', 'capture': window['capture'],
                             'error': 'Required reference mesh absent from selected draws; no 84-vertex/56-triangle draw, matching POSITION/IB, or original POSITION sequence in any captured blob.'})
    if len(windows) != 3:
        raise ValueError('This audit requires exactly the three reported recapture windows.')
    result['failures'] = failures
    result['recaptureVerification'] = {
        'schemaVersion': 1, 'orderedWindows': windows,
        'allWindowsCompleteAndIntegral': all(w['integrityVerified'] for w in windows),
        'requiredReference': {'capture': audit['referenceCapture'], 'draw': audit['referenceDraw'],
                              'geometryKey': audit['referenceGeometryKey'], 'positionSha1': audit['positionSha1'],
                              'numVertices': 84, 'primitiveCount': 56},
        'newRequiredMeshMatches': sum(len(w['exactMeshMatches']) for w in windows),
        'oldMatchesRetained': len([m for m in result['matchedDraws'] if m['capture'] not in {w['capture'] for w in windows}]),
        'requiredMeshPresentInAllThree': all(w['requiredMeshPresent'] for w in windows),
        'requiredMeshStationaryComparison': 'not_assessable_required_mesh_absent',
        'requiredMeshMovedCameraComparison': 'not_assessable_required_mesh_absent',
        'otherGeometryComparison': {**comparison, 'pairs': [{k:v for k,v in p.items() if k != 'observations'} for p in comparison['pairs']]},
        'decoderGate': {'allowed': False, 'reason': 'Required reference mesh is absent in all three recaptures; user explicitly prohibits decoder advancement in this case.',
                        'failedCaptures': [w['capture'] for w in windows if not w['integrityVerified'] or not w['requiredMeshPresent']]},
        'runtimeChangesThisTurn': [], 'evidenceFiles': ['audit/audit.json', 'comparison.json']}
    (root / 'validation.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'windows': len(windows), 'integrity': result['recaptureVerification']['allWindowsCompleteAndIntegral'],
                      'newRequiredMeshMatches': result['recaptureVerification']['newRequiredMeshMatches'],
                      'decoderAllowed': False, 'failures': len(failures)}))


if __name__ == '__main__':
    main()

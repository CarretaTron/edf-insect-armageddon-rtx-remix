"""Compare recurrent captured content; never substitutes it for the required mesh."""
import collections
import hashlib
import itertools
import json
import math
import statistics
import struct
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE / 'runtime/tools/edf_capture'))
from validate_edf_draw import BlobStore, fit_camera, geometry_key, load_capture, mul


def fingerprint(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    root = WORKSPACE / 'captures/edf-static'
    windows = []
    by_capture = {}
    for path in sorted(root.iterdir()):
        if not (path / 'records.jsonl').is_file():
            continue
        draws, shaders, inventory = load_capture(path)
        store = BlobStore(path)
        entries = collections.defaultdict(list)
        for draw in draws:
            state = draw['state']
            raw = store.read(state['vsConstants']['float'])
            constants = list(struct.iter_unpack('<4f', raw))
            basis = [row[:3] for row in constants[4:7]]
            fit, _ = fit_camera(constants)
            key = (geometry_key(state), fingerprint(basis), fingerprint(constants[9]))
            entries[key].append({'draw': draw['draw'], 'frame': draw['frame'],
                                 'c0ThroughC9': constants[:10], 'basis': basis,
                                 'cameraLocal': constants[7][:3], 'cameraFit': fit})
        by_capture[path.name] = entries
        windows.append({'capture': path.name, 'frames': inventory['frames'],
                        'draws': len(draws), 'cameraFitsValidated': sum(map(len, entries.values())),
                        'geometryKeys': len({k[0] for k in entries})})
    pairs = []
    for first, second in itertools.combinations(by_capture, 2):
        observations = []
        for key in sorted(by_capture[first].keys() & by_capture[second].keys()):
            left, right = by_capture[first][key], by_capture[second][key]
            if len(left) != 1 or len(right) != 1:
                continue
            left, right = left[0], right[0]
            delta = mul([[b-a for a,b in zip(left['cameraLocal'],right['cameraLocal'])]], left['basis'])[0]
            forward_left = [row[2] for row in left['cameraFit']['viewRowMajor'][:3]]
            forward_right = [row[2] for row in right['cameraFit']['viewRowMajor'][:3]]
            cross = [forward_left[1]*forward_right[2]-forward_left[2]*forward_right[1],
                     forward_left[2]*forward_right[0]-forward_left[0]*forward_right[2],
                     forward_left[0]*forward_right[1]-forward_left[1]*forward_right[0]]
            forward_angle = math.degrees(math.atan2(math.sqrt(sum(v*v for v in cross)),
                                                    sum(a*b for a,b in zip(forward_left, forward_right))))
            observations.append({'geometryKey': key[0], 'draws': [left['draw'], right['draw']],
                                 'basisHash': key[1], 'scaleHash': key[2],
                                 'c0ThroughC9Equal': left['c0ThroughC9'] == right['c0ThroughC9'],
                                 'constantsMaxDifference': max(abs(a-b) for x,y in zip(left['c0ThroughC9'],right['c0ThroughC9']) for a,b in zip(x,y)),
                                 'cameraLocal': [left['cameraLocal'],right['cameraLocal']],
                                 'basis': left['basis'], 'cameraDeltaIfObjectStatic': delta,
                                 'forwardAngleDegrees': forward_angle})
        independent = {fingerprint([e['basis'], e['cameraLocal']]): e for e in observations}
        values = [e['cameraDeltaIfObjectStatic'] for e in independent.values()]
        median = [statistics.median(v[i] for v in values) for i in range(3)] if values else None
        residuals = [max(abs(v[i]-median[i]) for i in range(3)) for v in values]
        pairs.append({'captures': [first,second], 'singleOccurrenceContentMatches': len(observations),
                      'geometryBasisScaleMultisetsEqual':
                          {k:len(v) for k,v in by_capture[first].items()} == {k:len(v) for k,v in by_capture[second].items()},
                      'exactC0ThroughC9Matches': sum(e['c0ThroughC9Equal'] for e in observations),
                      'maxC0ThroughC9Difference': max((e['constantsMaxDifference'] for e in observations), default=None),
                      'forwardAngleMedianDegrees': statistics.median(e['forwardAngleDegrees'] for e in observations) if observations else None,
                      'distinctTransformPairs': len(values), 'cameraDeltaMedian': median,
                      'cameraDeltaLength': math.sqrt(sum(v*v for v in median)) if median else None,
                      'maxAbsoluteResidual': max(residuals) if residuals else None,
                      'observations': observations})
    report = {'scope': 'other_recurrent_geometry_in_new_windows_not_the_required_84_vertex_mesh',
              'derivation': 'deltaCamera=(c7_second-c7_first)*B, conditional on invariant object translation; content/B/c9 matches and consensus reduce ambiguous associations.',
              'windows': windows, 'pairs': pairs,
              'limitations': ['Required reference mesh is absent; these comparisons cannot pass its decoder gate.',
                              'Content identity is not an engine entity ID; ambiguous multi-occurrence keys are excluded.',
                              'No engine absolute world origin or GPU/raster agreement is inferred.']}
    (Path(__file__).parent / 'comparison.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'windows': windows, 'pairs': [{k:v for k,v in p.items() if k != 'observations'} for p in pairs]}, indent=2))


if __name__ == '__main__':
    main()

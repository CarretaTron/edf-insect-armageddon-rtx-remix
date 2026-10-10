"""Read-only audit of the three EDF recapture windows and exact old mesh."""
from pathlib import Path
import hashlib
import json
import sys
from collections import Counter

ROOT = Path(r'D:\EDF IA RTX REMIX')
sys.path.insert(0, str(ROOT / 'runtime/tools/edf_capture'))
from analyze_capture import BlobStore, read_records
from validate_edf_draw import load_capture, geometry_key, verify_draw, VS_HASH, PS_HASH

old_path = ROOT / 'captures/edf/1791384430306-28796-6'
old_draws, _, _ = load_capture(old_path)
old = next(d for d in old_draws if d['draw'] == 284)
old_key = geometry_key(old['state'])
old_position = next(s for s in old['state']['streams'] if s['stream'] == 2)['blob']
old_index = old['state']['indices']['blob']
needle = BlobStore(old_path).read(old_position)
assert len(needle) == 672

def references(value):
    if isinstance(value, dict):
        if 'path' in value and isinstance(value['path'], str) and 'hash' in value:
            yield value
        for nested in value.values():
            yield from references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from references(nested)

reports = []
for ordinal, path in enumerate(sorted((ROOT / 'captures/edf-static').iterdir())):
    if not (path / 'records.jsonl').is_file():
        continue
    records, parse_errors = read_records(path / 'records.jsonl')
    draws, shaders, inventory = load_capture(path)
    store = BlobStore(path)
    refs = list(references(records))
    unique = {(ref['path'], ref.get('hash'), ref.get('size')): ref for ref in refs}
    integrity_errors = []
    for ref in unique.values():
        try:
            store.read(ref)
        except Exception as error:
            integrity_errors.append({'path': ref['path'], 'error': str(error)})
    file_checks = []
    position_occurrences = []
    for blob in sorted((path / 'blobs').glob('*')):
        if not blob.is_file():
            continue
        data = blob.read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        filename_matches = blob.name.split('.')[0] == digest
        if not filename_matches:
            integrity_errors.append({'path': blob.relative_to(path).as_posix(), 'error': 'SHA1 disagrees with filename'})
        offset = data.find(needle)
        while offset >= 0:
            position_occurrences.append({'path': blob.relative_to(path).as_posix(), 'size': len(data), 'offset': offset, 'sha1': digest})
            offset = data.find(needle, offset + 1)
        file_checks.append({'path': blob.relative_to(path).as_posix(), 'size': len(data), 'hash': digest, 'filenameHashMatches': filename_matches})
    exact_draws = []
    shader_pair_draws = []
    same_position_draws = []
    same_index_draws = []
    count84_56 = []
    for draw in draws:
        state = draw['state']
        pair = state['vertexShader'].get('hash') == VS_HASH and state['pixelShader'].get('hash') == PS_HASH
        if pair:
            shader_pair_draws.append(draw['draw'])
        if state.get('indices', {}).get('blob', {}).get('hash') == old_index['hash']:
            same_index_draws.append(draw['draw'])
        if draw['args']['numVertices'] == 84 and draw['args']['primitiveCount'] == 56:
            count84_56.append({'draw': draw['draw'], 'args': draw['args'],
                              'position': stream2 if (stream2 := next((s for s in state['streams'] if s['stream'] == 2), None)) else None,
                              'indices': state.get('indices'), 'geometryKey': geometry_key(state)})
        stream2 = next((s for s in state['streams'] if s['stream'] == 2), None)
        if stream2 and stream2.get('blob', {}).get('hash') == old_position['hash']:
            same_position_draws.append({'draw': draw['draw'], 'args': draw['args'], 'vertexShader': state['vertexShader'].get('hash'), 'pixelShader': state['pixelShader'].get('hash')})
        if pair and geometry_key(state) == old_key:
            proof, _ = verify_draw(path, draw, shaders)
            proof.pop('vertices')
            exact_draws.append(proof)
    referenced_paths = {ref['path'].replace('\\', '/') for ref in unique.values()}
    file_paths = {f['path'] for f in file_checks}
    reports.append({'capture': path.name, 'ordinal': ordinal + 1,
                    'userProcedure': ['stationary_first', 'stationary_second', 'moved_third'][ordinal],
                    'inventory': inventory, 'parseErrors': parse_errors,
                    'recordTypeCounts': dict(Counter(r['type'] for r in records)),
                    'captureEnds': [r for r in records if r['type'] == 'capture_end'],
                    'uniqueBlobReferences': len(unique), 'onDiskBlobs': len(file_checks),
                    'onDiskBytes': sum(f['size'] for f in file_checks),
                    'orphanPaths': sorted(file_paths - referenced_paths),
                    'missingPaths': sorted(referenced_paths - file_paths),
                    'integrityErrors': integrity_errors, 'shaderPairDraws': shader_pair_draws,
                    'exactMeshMatches': exact_draws, 'samePositionRangeDraws': same_position_draws,
                    'sameIndexRangeDraws': same_index_draws, 'count84Vertices56TrianglesDraws': count84_56,
                    'exact672BytePositionOccurrencesInAllBlobs': position_occurrences,
                    'fileChecks': file_checks})

output = Path(__file__).parent / 'audit.json'
output.write_text(json.dumps({'referenceCapture': old_path.name, 'referenceDraw': 284,
                               'referenceGeometryKey': old_key, 'positionSha1': old_position['hash'],
                               'windows': reports}, indent=2) + '\n')
print(json.dumps([{'capture': r['capture'], 'frame': r['inventory']['frames'],
                   'complete': r['inventory']['complete'], 'status': r['inventory']['status'],
                   'draws': r['inventory']['recordedDraws'], 'blobs': r['onDiskBlobs'],
                   'integrityErrors': r['integrityErrors'], 'exactMeshMatches': len(r['exactMeshMatches']),
                   'positionSequenceOccurrences': r['exact672BytePositionOccurrencesInAllBlobs']}
                  for r in reports], indent=2))

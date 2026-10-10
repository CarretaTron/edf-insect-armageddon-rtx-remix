"""Cross-frame consistency of local-camera constants. Never labels capture intent."""
import collections, itertools, json, math, statistics
from pathlib import Path
from target_matrix_analysis import multiply, fingerprint

OUTPUT=Path(__file__).parent


def main():
    report=json.loads((OUTPUT/"target_matrix_analysis.json").read_text())
    entries=report["allF098Entries"]
    captures=sorted({entry["capture"] for entry in entries})
    # Strong content identity + decoded fixed point scale + exact vector basis.
    by_capture=collections.defaultdict(lambda:collections.defaultdict(list))
    for entry in entries:
        if entry["targetResource"]==4831:
            key=(entry["geometry"],fingerprint(entry["b"]),fingerprint(entry["c9"]))
            by_capture[entry["capture"]][key].append(entry)
    pairs=[]
    for first,second in itertools.combinations(captures,2):
        observations=[]
        for key in by_capture[first].keys() & by_capture[second].keys():
            a,b=by_capture[first][key],by_capture[second][key]
            if len(a)!=1 or len(b)!=1:continue
            a,b=a[0],b[0]
            delta=multiply([[y-x for x,y in zip(a["c7"],b["c7"])]],a["b"])[0]
            observations.append({"geometry":key[0],"bHash":key[1],"scaleHash":key[2],"draws":[a["draw"],b["draw"]],"c7":[a["c7"],b["c7"]],"b":a["b"],"deltaCameraIfObjectStatic":delta})
        if not observations:continue
        # Different submeshes/PS using the same B and local-camera pair count once.
        independent={fingerprint([e["b"],e["c7"]]):e for e in observations}
        values=[e["deltaCameraIfObjectStatic"] for e in independent.values()]
        median=[statistics.median(v[i] for v in values) for i in range(3)]
        residuals=[max(abs(v[i]-median[i]) for i in range(3)) for v in values]
        result={"captures":[first,second],"frames":[next(e["frame"] for e in entries if e["capture"]==first),next(e["frame"] for e in entries if e["capture"]==second)],"complete":[next(e["complete"] for e in entries if e["capture"]==first),next(e["complete"] for e in entries if e["capture"]==second)],"singleOccurrenceGeometryMatches":len(observations),"distinctTransformPairs":len(independent),"deltaMedian":median,"maxAbsoluteResidual":max(residuals),"residualMedian":statistics.median(residuals),"matchesWithin0_001":sum(r<.001 for r in residuals),"geometryObservations":observations,"distinctTransformObservations":list(independent.values())}
        pairs.append(result)
    result={"derivation":"For a recurrent object with invariant B and position scale: delta=(c7_frame2-c7_frame1)*B. This equals world-camera displacement if object translation is invariant. Agreement across distinct B/c7 pairs supports static-scene interpretation but cannot exclude common global scene motion.","pairs":pairs,"limitations":["Single captured occurrence does not prove object identity; content + B + scale constrain it.","Truncated frames provide only matched captured draws, never proof of absence.","Transform-pair dedup avoids counting submeshes/pixel-shader passes as independent objects.","Coordinate origin and absolute World translations remain underdetermined."]}
    (OUTPUT/"camera_motion_pairs.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps([{k:v for k,v in p.items() if k not in ("geometryObservations","distinctTransformObservations")} for p in pairs],indent=2))


if __name__=="__main__":main()

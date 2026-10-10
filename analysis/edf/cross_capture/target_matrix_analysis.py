"""Reproducible evidence-only math for VS f098. No Runtime changes."""
import collections, hashlib, json, math, statistics, struct
from pathlib import Path

CAPTURES=Path("D:/EDF IA RTX REMIX/captures/edf")
OUTPUT=Path(__file__).parent
VS="f098f457d9ae603bea5f84ec985d473c29cfb0c9"
PS="44b92a29eb3e5f2ee6d53f02e14fb11a01511da8"


def multiply(a,b):
    return [[sum(x*y for x,y in zip(row,column)) for column in zip(*b)] for row in a]


def inverse(matrix):
    n=len(matrix)
    a=[list(row)+[float(i==j) for j in range(n)] for i,row in enumerate(matrix)]
    for col in range(n):
        pivot=max(range(col,n),key=lambda row:abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12: raise ValueError("singular")
        a[col],a[pivot]=a[pivot],a[col]
        divisor=a[col][col]
        a[col]=[value/divisor for value in a[col]]
        for row in range(n):
            if row!=col:
                factor=a[row][col]
                a[row]=[x-factor*y for x,y in zip(a[row],a[col])]
    return [row[n:] for row in a]


def blob(directory,reference):
    data=(directory/reference["path"]).read_bytes()
    assert len(data)==reference["size"] and hashlib.sha1(data).hexdigest()==reference["hash"]
    return data


def fingerprint(value):
    return hashlib.sha1(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def active_geometry(state,args):
    active={element["stream"] for element in state["vertexDeclaration"]["elements"]}
    streams=[(s["stream"],s["stride"],s["blob"].get("hash"),s["blob"].get("size")) for s in state["streams"] if s["stream"] in active]
    index=state["indices"]
    return fingerprint([state["vertexDeclaration"]["hash"],streams,index["format"],index["blob"].get("hash"),args["primitiveType"],args["primitiveCount"]])


def range_stats(values):
    if not values:return None
    columns=list(zip(*(sum(value,[]) if isinstance(value[0],list) else value for value in values)))
    return {"maxAbsoluteRange":max(max(col)-min(col) for col in columns),"median": [statistics.median(col) for col in columns]}


def main():
    summaries=json.loads((OUTPUT/"cross_capture.json").read_text())["captures"]
    integrity={item["capture"]:item["complete"] for item in summaries}
    entries=[]
    selected=[]
    for directory in sorted(CAPTURES.iterdir()):
        if not (directory/"records.jsonl").exists(): continue
        records=[json.loads(line) for line in (directory/"records.jsonl").read_text().splitlines() if line]
        states={r["id"]:r["state"] for r in records if r["type"]=="state"}
        for draw in (r for r in records if r["type"]=="draw"):
            state=states[draw["stateId"]]
            if not state.get("vertexShader") or state["vertexShader"]["hash"]!=VS: continue
            c=list(struct.iter_unpack("<4f",blob(directory,state["vsConstants"]["float"])))
            b=[list(row[:3]) for row in c[4:7]]
            a=[list(row) for row in c[:3]]
            inv=inverse(b)
            m=multiply(inv,a)
            k=[x+y for x,y in zip(multiply([c[7][:3]],a)[0],c[3])]
            norms=[math.sqrt(sum(row[column]**2 for row in m)) for column in range(4)]
            dotxy=sum(row[0]*row[1] for row in m)
            dotxw=sum(row[0]*row[3] for row in m)
            dotyw=sum(row[1]*row[3] for row in m)
            zscale=sum(row[2]*row[3] for row in m)/sum(row[3]**2 for row in m)
            zresidual=max(abs(row[2]-zscale*row[3]) for row in m)
            orth=multiply(b,list(map(list,zip(*b))))
            orth_error=max(abs(orth[i][j]-float(i==j)) for i in range(3) for j in range(3))
            target=state["renderTargets"][0]
            entry={"capture":directory.name,"complete":integrity[directory.name],"frame":draw["frame"],"draw":draw["draw"],"ps":state["pixelShader"]["hash"],"geometry":active_geometry(state,draw["args"]),"primitiveCount":draw["args"]["primitiveCount"],"targetResource":target["resource"],"targetDescriptor":target["descriptorHash"],"b":b,"c7":list(c[7][:3]),"a":a,"c3":list(c[3]),"c8":list(c[8]),"c9":list(c[9]),"cameraRotationProjection":m,"cameraPointClip":k,"bOrthogonalityError":orth_error,"projectionColumnNorms":norms,"projectionColumnDotProducts":[dotxy,dotxw,dotyw],"zOverW":zscale,"zProportionalResidual":zresidual,"alphaBlend":state["renderStates"][27],"alphaTest":state["renderStates"][15],"zWrite":state["renderStates"][14]}
            entries.append(entry)
            if entry["ps"]==PS:
                e=dict(entry)
                e["stateId"]=draw["stateId"]
                e["args"]=draw["args"]
                e["declaration"]=state["vertexDeclaration"]
                e["streams"]=state["streams"]
                e["indices"]=state["indices"]
                indices=[x[0] for x in struct.iter_unpack("<H",blob(directory,state["indices"]["blob"]))]
                pos=next(s for s in state["streams"] if s["stream"]==2)
                uv=next(s for s in state["streams"] if s["stream"]==4)
                p=[list(v[:3]) for v in struct.iter_unpack("<4h",blob(directory,pos["blob"]))]
                uv_values=[list(v) for v in struct.iter_unpack("<2h",blob(directory,uv["blob"]))]
                p=[[x*c[9][0] for x in v] for v in p]
                uvs=[[x*c[9][1] for x in v] for v in uv_values]
                clip=[[x+y for x,y in zip(multiply([v],a)[0],c[3])] for v in p]
                ndc=[[x/v[3] for x in v[:3]] for v in clip]
                e["vertexCount"]=len(p)
                e["indexCount"]=len(indices)
                e["indexMinMax"]=[min(indices),max(indices)]
                e["localPositionBounds"]=[[min(v[i] for v in p),max(v[i] for v in p)] for i in range(3)]
                e["uvBounds"]=[[min(v[i] for v in uvs),max(v[i] for v in uvs)] for i in range(2)]
                e["ndcBounds"]=[[min(v[i] for v in ndc),max(v[i] for v in ndc)] for i in range(3)]
                e["clipWBounds"]=[min(v[3] for v in clip),max(v[3] for v in clip)]
                selected.append(e)
    passgroups=collections.defaultdict(list)
    for entry in entries:
        passgroups[(entry["capture"],entry["targetResource"])].append(entry)
    passes=[]
    for (capture,target),group in passgroups.items():
        passes.append({"capture":capture,"complete":integrity[capture],"target":target,"drawCount":len(group),"draws":[entry["draw"] for entry in group],"mVariation":range_stats([entry["cameraRotationProjection"] for entry in group]),"kVariation":range_stats([entry["cameraPointClip"] for entry in group]),"maxBOrthogonalityError":max(e["bOrthogonalityError"] for e in group),"maxZProportionalResidual":max(e["zProportionalResidual"] for e in group),"projectionColumnNorms":range_stats([entry["projectionColumnNorms"] for entry in group]),"zOverWRange":[min(e["zOverW"] for e in group),max(e["zOverW"] for e in group)]})
    # All recurrent active geometry+B observations between the two full scene frames.
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    for entry in entries:
        if entry["complete"] and entry["targetResource"]==4831:
            groups[(entry["geometry"],fingerprint(entry["b"]))][entry["capture"]].append(entry)
    motion=[]
    for key,bycapture in groups.items():
        if len(bycapture)!=2:continue
        ordered=sorted(bycapture.items())
        if any(len(values)!=1 for _,values in ordered):continue
        a,b=ordered[0][1][0],ordered[1][1][0]
        dc=[y-x for x,y in zip(a["c7"],b["c7"])]
        delta=multiply([dc],a["b"])[0]
        motion.append({"geometry":key[0],"bHash":key[1],"draws":[[a["capture"],a["draw"]],[b["capture"],b["draw"]]],"ps":[a["ps"],b["ps"]],"deltaCameraIfObjectStatic":delta,"alphaBlend":[a["alphaBlend"],b["alphaBlend"]],"alphaTest":[a["alphaTest"],b["alphaTest"]],"zWrite":[a["zWrite"],b["zWrite"]]})
    report={"verifiedShader":VS,"positionEquation":"p=v2.xyz*c9.x; q=p.x*c0+p.y*c1+p.z*c2+c3; B rows=c4.xyz,c5.xyz,c6.xyz; camera-local-position=c7.xyz (shader symbol and vector subtraction)","math":"M=inverse(B)*A_linear; k=c7*A_linear+c3; q=(p-c7)*B*M+k. B encodes vector mapping only. Translation absent from c4..6 use; absolute world origin not recovered.","passes":passes,"selectedPair":selected,"recurrentUniqueObjectsCameraMotionTests":motion,"motionRange":range_stats([entry["deltaCameraIfObjectStatic"] for entry in motion]),"allF098Entries":entries,"limitations":["Camera/rotation-projection is algebraic evidence, not original CPU matrix labels.","Relative camera motion tests assume an individual recurrent object did not translate; consensus is evidence for that assumption, not proof for every object.","No absolute World translation is stored in B; a global origin remains arbitrary.","Both user gameplay labeling and texture contents are unavailable."]}
    (OUTPUT/"target_matrix_analysis.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"passes":[{k:v for k,v in p.items() if k!="draws"} for p in passes],"selected":[{k:v for k,v in p.items() if k not in ("streams","indices","declaration")} for p in selected if p["complete"]],"motionTests":len(motion),"motionRange":report["motionRange"]},indent=2))


if __name__=="__main__":main()

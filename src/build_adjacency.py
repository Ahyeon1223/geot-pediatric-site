"""시군구 경계 GeoJSON에서 '서로 맞닿은 시군구' 표를 만듭니다. (한계 대응방안 ②)

실행 (한 번만 하면 됨, 결과 CSV는 GitHub에 올림):
    python src/build_adjacency.py 경계파일.geojson

경계 파일:
    - 기본으로 쓴 파일: 통계청 SGIS 경계를 행정동 단위로 정리한 공개본
      (github.com/vuski/admdongkor, ver20250401). 행정동마다 시군구 코드(sgg)가 붙어 있어
      같은 시군구의 행정동을 묶어 시군구 경계로 씁니다.
    - SGIS에서 직접 받은 시군구 경계를 쓸 때는 --code-field, --sido-field, --name-field로
      속성 이름을 바꿔 주면 됩니다.

판단 방법:
    두 시군구의 경계선이 꼭짓점을 2개 이상 같이 쓰면 맞닿은 것으로 봅니다.
    (같은 경계 파일 안에서는 맞닿은 선의 좌표가 똑같이 들어 있음)
    섬 지역(울릉군, 옹진군 등)은 맞닿은 시군구가 없습니다. 다리·배편은 반영하지 않습니다.

외부 패키지(shapely 등) 없이 표준 라이브러리만 씁니다.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "reference" / "sigungu_adjacency.csv"
MIN_SHARED_POINTS = 2


def rings(geometry):
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"면(Polygon) 경계가 아닙니다: {geometry['type']}")
    for polygon in polygons:
        yield from polygon


def build(path, code_field, sido_field, name_field):
    with open(path, encoding="utf-8") as stream:
        features = json.load(stream)["features"]

    names = {}
    owners = defaultdict(set)  # 꼭짓점 → 그 점을 쓰는 시군구 코드들
    for feature in features:
        props = feature["properties"]
        code = str(props[code_field])
        names[code] = (props[sido_field], props[name_field])
        for ring in rings(feature["geometry"]):
            for x, y, *_ in ring:
                owners[(round(x, 6), round(y, 6))].add(code)

    shared = Counter()
    for codes in owners.values():
        if len(codes) > 1:
            ordered = sorted(codes)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    shared[(a, b)] += 1

    neighbors = defaultdict(set)
    for (a, b), count in shared.items():
        if count >= MIN_SHARED_POINTS:
            neighbors[a].add(b)
            neighbors[b].add(a)
    return names, neighbors


def save(names, neighbors, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["code", "sido", "sigungu", "neighbor_count", "neighbor_codes", "neighbor_names"])
        for code in sorted(names):
            near = sorted(neighbors[code])
            writer.writerow([
                code, *names[code], len(near), ";".join(near),
                ";".join(names[other][1] for other in near),
            ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("geojson", type=Path)
    parser.add_argument("--code-field", default="sgg")
    parser.add_argument("--sido-field", default="sidonm")
    parser.add_argument("--name-field", default="sggnm")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    names, neighbors = build(args.geojson, args.code_field, args.sido_field, args.name_field)
    save(names, neighbors, args.output)
    alone = [f"{sido} {name}" for code, (sido, name) in sorted(names.items()) if not neighbors[code]]
    pairs = sum(len(v) for v in neighbors.values()) // 2
    print(f"완료: 시군구 {len(names):,}개, 맞닿은 쌍 {pairs:,}개 → {args.output}")
    print(f"맞닿은 시군구가 없는 곳 {len(alone)}개: {', '.join(alone)}")

"""기본 지표(소아 1만 명당 의원 수)를 보완하는 보조 지표 계산

계획서 '범위와 한계'의 대응방안을 코드로 옮긴 파일입니다.
기본 지표와 기본 순위는 바꾸지 않고, 보조 지표를 옆에 붙이기만 합니다.

    ① '소아' 이름 누락   clinics_per_10k_broad   어린이·아동·키즈 이름까지 넣은 의원 수 기준 (참고)
    ② 시군구 경계 너머   clinics_per_10k_with_neighbors  맞닿은 시군구까지 합친 1만 명당 의원 수
    ③ 수집 시점 스냅샷   openings_by_year         지금 영업 중인 의원의 개설 연도별 수
    ④ 의원 1곳 = 1       doctors_per_10k          소아 1만 명당 의사 수

서버(api.py)와 화면이 같이 쓸 수 있게 FastAPI와 상관없는 함수만 둡니다.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ADJACENCY_PATH = ROOT / "data" / "reference" / "sigungu_adjacency.csv"

# 경계 파일(2025년 4월) 이후 새로 생긴 시군구는 옛 지역의 경계를 대신 씁니다.
# 2026년 인천 행정체제 개편(중구·동구 → 제물포구·영종구, 서구 → 서구·검단구),
# 2026년 화성시 일반구 설치(만세구·효행구·병점구·동탄구).
# 같은 옛 지역을 나눠 가진 시군구끼리도 서로 이웃으로 칩니다.
# 영종구는 섬(영종도·용유도)이라 육지로 맞닿은 시군구가 없는 것으로 둡니다.
# 제물포구는 옛 중구 경계에 영종도가 들어 있어 실제보다 넓게 잡힙니다(근사).
FORMER_AREAS = {
    ("인천", "제물포구"): ["28110", "28140"],
    ("인천", "영종구"): [],
    ("인천", "검단구"): ["28260"],
    ("경기", "만세구"): ["41590"],
    ("경기", "효행구"): ["41590"],
    ("경기", "병점구"): ["41590"],
    ("경기", "동탄구"): ["41590"],
}


def per_10k(count, population):
    """소아 인구가 0이거나 비어 있으면 None(계산 불가)."""
    count = pd.to_numeric(count, errors="coerce")
    population = pd.to_numeric(population, errors="coerce")
    return (count / population.where(population > 0) * 10000).round(2)


def sido_key(name):
    """시도 이름을 비교용으로 줄입니다. 서울특별시 → 서울, 광주광역시·전라남도 → 전남광주"""
    name = str(name).replace(" ", "")
    if "광주" in name or name.startswith(("전남", "전라남")):
        return "전남광주"
    for long, short in (("충청북", "충북"), ("충청남", "충남"), ("전라북", "전북"),
                        ("경상북", "경북"), ("경상남", "경남")):
        if name.startswith(long):
            return short
    return name[:2]


def load_adjacency(path=ADJACENCY_PATH):
    if not Path(path).exists():
        return None
    table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    table["neighbors"] = table["neighbor_codes"].map(lambda text: set(filter(None, text.split(";"))))
    table["key"] = table["sido"].map(sido_key)
    table["short"] = table["sigungu"].str.replace(" ", "", regex=False)
    return table


def match_boundaries(regions, adjacency):
    """regions의 각 시군구가 경계 파일의 어느 시군구(들)에 해당하는지 찾습니다.

    1) 새로 생긴 시군구는 FORMER_AREAS  2) 코드와 이름이 같으면 그대로
    3) 코드가 바뀐 곳(전남광주 통합 등)은 같은 시도 안에서 이름으로
    못 찾으면 None, 섬처럼 맞닿은 곳이 없으면 빈 집합입니다.
    """
    by_code = adjacency.set_index("code")
    matched = {}
    for row in regions.itertuples(index=False):
        key, short = sido_key(row.sido), str(row.sigungu).replace(" ", "")
        if (key, short) in FORMER_AREAS:
            matched[row.code] = set(FORMER_AREAS[(key, short)])
            continue
        if row.code in by_code.index and by_code.at[row.code, "short"].endswith(short):
            matched[row.code] = {row.code}
            continue
        same_sido = adjacency[adjacency["key"].eq(key)]
        hits = same_sido[same_sido["short"].eq(short)]
        if hits.empty:  # 일반구: 경계 파일은 '수원시장안구', KOSIS는 '장안구'
            hits = same_sido[same_sido["short"].str.endswith(short) & same_sido["short"].str.contains("시")]
        matched[row.code] = set(hits["code"]) if len(hits) == 1 else None
    return matched


def add_neighbor_indicator(df, adjacency):
    """맞닿은 시군구까지 합친 지표를 붙입니다. 경계를 못 찾은 곳은 None."""
    df = df.copy()
    columns = ["neighbor_codes", "neighbor_count", "area_clinic_count", "area_child_pop",
               "clinics_per_10k_with_neighbors"]
    if adjacency is None:
        for column in columns:
            df[column] = None
        return df

    own = match_boundaries(df, adjacency)
    near = adjacency.set_index("code")["neighbors"].to_dict()
    neighbor_codes = {}
    for code, areas in own.items():
        if areas is None:
            neighbor_codes[code] = None
            continue
        reach = areas | set().union(*(near.get(area, set()) for area in areas))
        neighbor_codes[code] = sorted(
            other for other, other_areas in own.items()
            if other != code and other_areas and other_areas & reach
        )

    clinics = pd.to_numeric(df["clinic_count"], errors="coerce").fillna(0)
    pop = pd.to_numeric(df["child_pop"], errors="coerce").fillna(0)
    clinic_by_code = dict(zip(df["code"], clinics))
    pop_by_code = dict(zip(df["code"], pop))

    def area_sum(code, values):
        codes = neighbor_codes.get(code)
        if codes is None:
            return None
        return int(values[code] + sum(values[other] for other in codes))

    df["neighbor_codes"] = df["code"].map(neighbor_codes)
    df["neighbor_count"] = df["neighbor_codes"].map(lambda codes: None if codes is None else len(codes))
    df["area_clinic_count"] = df["code"].map(lambda code: area_sum(code, clinic_by_code))
    df["area_child_pop"] = df["code"].map(lambda code: area_sum(code, pop_by_code))
    df["clinics_per_10k_with_neighbors"] = per_10k(df["area_clinic_count"], df["area_child_pop"])
    return df


def add_secondary_indicators(df, adjacency=None):
    """regions 표에 보조 지표 열을 붙입니다. 원본에 열이 없으면 그 지표는 None."""
    df = df.copy()
    for source, target in (("clinic_count_broad", "clinics_per_10k_broad"),
                           ("doctor_count", "doctors_per_10k")):
        df[target] = per_10k(df[source], df["child_pop"]) if source in df else None
    return add_neighbor_indicator(df, adjacency)


def openings_by_year(clinics, last_year, years=10):
    """지금 영업 중인 의원을 개설 연도별로 셉니다 (폐업한 의원은 원본에 없어 빠짐)."""
    first_year = last_year - years + 1
    opened = pd.to_datetime(clinics["opened_date"].astype(str).str[:8], format="%Y%m%d", errors="coerce")
    counts = opened.dt.year.value_counts()
    return [{"year": year, "count": int(counts.get(year, 0))} for year in range(first_year, last_year + 1)]

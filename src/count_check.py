"""실제 원본 파일로 건수 검증표(reports/count_check.md)를 만듭니다.

실행 (프로젝트 맨 위 폴더에서, collect.py 를 돌린 뒤):
    python src/count_check.py

읽는 파일
    data/raw/hira_pediatric_candidates.csv   심평원 수집 원본
    data/raw/101_DT_1B04006_M_2026.csv(.zip) KOSIS 원본

만드는 파일
    reports/count_check.md          건수 검증표 (11/27 중간 점검 제출용)
    reports/name_filter_check.csv   이름에 '소아'가 없어 빠진 의원 중 소아 진료로 보이는 이름 목록

clean.py 와 같은 규칙(같은 함수)을 써서 센다. 숫자가 서로 맞지 않으면 표에 '불일치'로 적는다.
"""
import argparse
import csv
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from clean import (
    AGES, KOSIS_BASE, KOSIS_COLUMNS, MAPPING_KEYS, ROOT, load_population, region_mapping,
)

# '소아'가 이름에 없지만 소아 진료일 수 있는 이름 (피드백 2번: 몇 곳이 빠지는지 기록)
CHILD_WORDS = ["아이", "어린이", "키즈", "kids", "아동", "베이비", "baby", "우리아기", "아기"]
EXAMPLE_REGION = "11710"  # 손계산 예시: 서울 송파구
MIN_CHILD_POP = 1000  # 11/19 결정: 소아 인구 1,000명 미만은 기본 순위에서 제외


def count_kosis_rows(path, month):
    """KOSIS 원본의 전체 데이터 행, 기준월 행, 기준월 0~14세 행을 센다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            if path.suffix.lower() == ".zip":
                with ZipFile(path) as archive:
                    member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                    with TextIOWrapper(archive.open(member), encoding=encoding, newline="") as stream:
                        return _count_rows(stream, month)
            with path.open(encoding=encoding, newline="") as stream:
                return _count_rows(stream, month)
        except UnicodeDecodeError:
            if encoding == "cp949":
                raise


def _count_rows(stream, month):
    reader = csv.reader(stream)
    for row in reader:
        header = [name.strip() for name in row]
        if KOSIS_COLUMNS.keys() <= set(header):
            break
    month_col, age_col = header.index("시점"), header.index("연령별")
    total = in_month = in_month_child = 0
    for row in reader:
        if not row:
            continue
        total += 1
        if row[month_col].strip() == month:
            in_month += 1
            if row[age_col].strip() in AGES:
                in_month_child += 1
    return total, in_month, in_month_child


def kosis_region_steps(path, month, regions):
    """기준월 0~14세 행을 지역 단위로 묶은 뒤, 시군구가 아닌 행을 몇 개 뺐는지 센다."""
    import clean

    for encoding in ("utf-8-sig", "cp949"):
        try:
            if path.suffix.lower() == ".zip":
                with ZipFile(path) as archive:
                    member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                    with TextIOWrapper(archive.open(member), encoding=encoding, newline="") as s:
                        rows = clean.read_kosis_rows(s, month)
            else:
                with path.open(encoding=encoding, newline="") as s:
                    rows = clean.read_kosis_rows(s, month)
            break
        except UnicodeDecodeError:
            if encoding == "cp949":
                raise
    rows["region_code"] = rows["region_code"].str.lstrip("'")
    areas = rows[["region_code", "region_name"]].drop_duplicates()
    five = areas[areas["region_code"].str.fullmatch(r"\d{5}")]
    branch = five[five["region_name"].str.endswith("출장소")]
    sigungu_like = five[~five["region_name"].str.endswith("출장소")]
    city_totals = sigungu_like[~sigungu_like["region_code"].isin(regions["region_code"])]
    return {
        "areas": len(areas),
        "national_sido": len(areas) - len(five),
        "branch": len(branch),
        "branch_names": branch["region_name"].tolist(),
        "city_totals": len(city_totals),
        "city_total_names": city_totals["region_name"].tolist(),
        "regions": len(regions),
    }


def fmt(n):
    return f"{n:,}"


def check(ok):
    return "맞음" if ok else "**불일치**"


def build(args):
    kosis_path = args.kosis
    if kosis_path is None:
        kosis_path = KOSIS_BASE.with_suffix(".csv")
        if not kosis_path.is_file():
            kosis_path = KOSIS_BASE.with_suffix(".zip")
    hira = pd.read_csv(args.hira, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    population = load_population(kosis_path, args.month)

    # ---------- 1. 심평원 정리 단계 ----------
    raw = len(hira)
    sido_counts = hira.groupby(["sidoCd", "sidoCdNm"]).size().reset_index(name="n")
    ids = hira["ykiho"].str.strip()
    blank_id = int(ids.eq("").sum())
    dup_id = int(ids[ids.ne("")].duplicated().sum())
    blank_name = int(hira["yadmNm"].str.strip().eq("").sum())
    base = hira[ids.ne("") & ~ids.duplicated() & hira["yadmNm"].str.strip().ne("")]
    has_child = base["yadmNm"].str.contains("소아", regex=False)
    no_child = int((~has_child).sum())
    clinics = base[has_child]
    kept = len(clinics)

    dropped = base[~has_child].copy()
    pattern = "|".join(CHILD_WORDS)
    suspects = dropped[dropped["yadmNm"].str.contains(pattern, case=False, regex=True)]
    suspects[["sidoCdNm", "sgguCdNm", "yadmNm", "addr"]].to_csv(
        args.report_dir / "name_filter_check.csv", index=False, encoding="utf-8-sig",
    )
    seoul = hira[hira["sidoCd"].eq("110000")]
    seoul_child = int(seoul["yadmNm"].str.contains("소아", regex=False).sum())
    seoul_suspects = int(suspects["sidoCd"].eq("110000").sum())

    # ---------- 2. KOSIS 정리 단계 ----------
    k_total, k_month, k_child = count_kosis_rows(kosis_path, args.month)
    steps = kosis_region_steps(kosis_path, args.month, population)

    # ---------- 3. 결합 단계 ----------
    mapping = region_mapping(hira, population)
    combos = len(mapping)
    combo_ok = int(mapping["mapping_status"].eq("mapped").sum())
    combo_fail = mapping[mapping["mapping_status"].ne("mapped")]
    joined = clinics.merge(mapping, on=MAPPING_KEYS, how="left", validate="many_to_one")
    clinic_ok = int(joined["region_code"].notna().sum())
    clinic_fail = joined[joined["region_code"].isna()]
    kosis_only = population[~population["region_code"].isin(mapping["region_code"])]
    counts = joined["region_code"].value_counts()
    population = population.assign(clinic_count=population["region_code"].map(counts).fillna(0).astype(int))
    zero_clinic = population[population["clinic_count"].eq(0)]
    small = population[population["population_0_14"].lt(MIN_CHILD_POP)]
    zero_pop = population[population["population_0_14"].eq(0)]

    full_name = lambda r: " ".join(x for x in (r.sido_name, r.city_name, r.region_name) if x)

    # ---------- 손계산 예시 ----------
    ex = population[population["region_code"].eq(EXAMPLE_REGION)]

    lines = []
    w = lines.append
    w("# 건수 검증표 — 팀 곁")
    w("")
    w(f"- 자동 생성: `python src/count_check.py` (인구 기준월 {args.month})")
    w(f"- 심평원 원본: `{args.hira.relative_to(ROOT) if args.hira.is_relative_to(ROOT) else args.hira}`")
    w(f"- KOSIS 원본: `{kosis_path.name}` (통계표 DT_1B04006 행정구역(시군구)별/1세별 주민등록인구)")
    w("")
    w("## 행 하나의 뜻")
    w("")
    w("| 데이터 | 행 하나 |")
    w("|---|---|")
    w("| 심평원 원본 | 소아청소년과를 진료과목으로 신고한 의원 1곳 (`ykiho` 기준) |")
    w("| 정리 후 의원 목록 | 기관명에 '소아'가 들어간 소아청소년과 의원 1곳 |")
    w("| KOSIS 원본 | 지역 1곳 × 나이 1세 × 월 1개 |")
    w("| 최종 결과 `regions.csv` | 시군구 1곳 (일반구가 있는 시는 구 단위) |")
    w("")
    w("## 1. 정리 단계 — 심평원 의원")
    w("")
    w("| 단계 | 건수 |")
    w("|---|---:|")
    w(f"| 원본 행 ({len(sido_counts)}개 시도) | {fmt(raw)} |")
    w(f"| − 기관 ID(`ykiho`) 빈값 | {fmt(blank_id)} |")
    w(f"| − 기관 ID 중복 | {fmt(dup_id)} |")
    w(f"| − 기관명 빈값 | {fmt(blank_name)} |")
    w(f"| − 기관명에 '소아' 없음 | {fmt(no_child)} |")
    w(f"| **= 정리 후 의원** | **{fmt(kept)}** |")
    w("")
    w(f"검산: {fmt(raw)} − {fmt(blank_id)} − {fmt(dup_id)} − {fmt(blank_name)} − {fmt(no_child)} = "
      f"{fmt(raw - blank_id - dup_id - blank_name - no_child)} → {check(raw - blank_id - dup_id - blank_name - no_child == kept)}")
    w("")
    w("0건 확인 방법: 빈 ID는 `ykiho`를 공백 제거 후 빈 문자열인지, 중복은 `ykiho` 값이 두 번 이상 나오는지 "
      "전체 행에서 셌다. `collect.py`도 저장 전에 같은 검사를 하고, 시도별 받은 건수가 API `totalCount`와 같은지 확인한다.")
    w("")
    w("시도별 원본 행")
    w("")
    w("| 시도코드 | 시도 | 행 |")
    w("|---|---|---:|")
    for r in sido_counts.itertuples(index=False):
        w(f"| {r.sidoCd} | {r.sidoCdNm} | {fmt(r.n)} |")
    w(f"| | 합계 | {fmt(int(sido_counts['n'].sum()))} |")
    w("")
    w("### '소아' 이름 기준으로 빠진 의원 확인 (피드백 2번)")
    w("")
    w(f"- 이름에 '소아'가 없어 빠진 {fmt(no_child)}곳 중 이름에 "
      f"{', '.join(repr(x) for x in CHILD_WORDS)}가 들어간 곳: **{fmt(len(suspects))}곳** "
      f"(목록: `reports/name_filter_check.csv`)")
    w(f"- 서울: 진료과목 기준 {fmt(len(seoul))}곳 → 이름 기준 {fmt(seoul_child)}곳, "
      f"빠진 곳 중 소아 진료로 보이는 이름 {fmt(seoul_suspects)}곳")
    w("- 직접 확인한 결과: (팀이 목록을 보고 몇 곳을 포함할지 적기)")
    w("")
    w("## 2. 정리 단계 — KOSIS 인구")
    w("")
    w("| 단계 | 건수 |")
    w("|---|---:|")
    w(f"| 원본 행 (지역×나이×월) | {fmt(k_total)} |")
    w(f"| − 기준월({args.month})이 아닌 행 | {fmt(k_total - k_month)} |")
    w(f"| − 0~14세가 아닌 행 (15세 이상, 계 등) | {fmt(k_month - k_child)} |")
    w(f"| = 기준월 0~14세 행 | {fmt(k_child)} |")
    w(f"| 지역 수 (나이 15개 묶음) | {fmt(steps['areas'])} |")
    w(f"| − 전국·시도 합계 행 | {fmt(steps['national_sido'])} |")
    w(f"| − 출장소 | {fmt(steps['branch'])} |")
    w(f"| − 일반구가 있는 시의 시 전체 행 | {fmt(steps['city_totals'])} |")
    w(f"| **= 시군구** | **{fmt(steps['regions'])}** |")
    w("")
    ok = steps["areas"] - steps["national_sido"] - steps["branch"] - steps["city_totals"] == steps["regions"]
    w(f"검산: {fmt(k_child)} ÷ 15세 = {k_child / 15:,.0f}개 지역, "
      f"{fmt(steps['areas'])} − {steps['national_sido']} − {steps['branch']} − {steps['city_totals']} = "
      f"{fmt(steps['regions'])} → {check(ok)}")
    w("")
    w(f"- 뺀 출장소: {', '.join(steps['branch_names']) or '없음'}")
    w(f"- 뺀 시 전체 행: {', '.join(steps['city_total_names']) or '없음'}")
    w("- 추가 확인: 남은 시군구의 0~14세 인구 합계가 전국 행(code=00)과 같은지 `clean.py`가 검사한다 (다르면 멈춤).")
    w(f"- 시군구 0~14세 인구 합계: {fmt(int(population['population_0_14'].sum()))}명")
    w("")
    w("## 3. 결합 단계")
    w("")
    w("| 단계 | 건수 |")
    w("|---|---:|")
    w(f"| 심평원 시군구 조합 (시도코드+시군구코드) | {fmt(combos)} |")
    w(f"| = 매칭 성공 + 매칭 실패 | {fmt(combo_ok)} + {fmt(len(combo_fail))} |")
    w(f"| 정리 후 의원 | {fmt(kept)} |")
    w(f"| = 매칭 성공 + 매칭 실패 | {fmt(clinic_ok)} + {fmt(len(clinic_fail))} |")
    w(f"| KOSIS 시군구 | {fmt(len(population))} |")
    w(f"| = 심평원에 있음 + KOSIS에만 있음 | {fmt(len(population) - len(kosis_only))} + {fmt(len(kosis_only))} |")
    w("")
    w(f"검산: 지역별 의원 수 합계 {fmt(int(population['clinic_count'].sum()))} = 매칭 성공 의원 {fmt(clinic_ok)} → "
      f"{check(int(population['clinic_count'].sum()) == clinic_ok)}")
    w("")
    w("매칭 실패 목록")
    w("")
    if combo_fail.empty and clinic_fail.empty and kosis_only.empty:
        w("- 없음 (0건)")
    for r in combo_fail.itertuples(index=False):
        w(f"- 심평원 {r.sidoCdNm} {r.sgguCdNm} ({r.sgguCd}): KOSIS에서 같은 이름을 찾지 못함")
    for r in clinic_fail.itertuples(index=False):
        w(f"- 의원 {r.yadmNm} ({r.sidoCdNm} {r.sgguCdNm}): 지역 매칭 실패")
    for r in kosis_only.itertuples(index=False):
        w(f"- KOSIS {full_name(r)} ({r.region_code}): 심평원 수집본에 이 지역 의원이 한 곳도 없음 "
          "(소아청소년과 진료과목 의원 0곳)")
    w("")
    w("0건 확인 방법: 매칭은 시도 이름과 시군구 이름을 같이 비교한다(중구·동구처럼 겹치는 이름 때문). "
      "같은 이름이 두 지역에 걸리면 매칭하지 않고 실패로 남기며, 의원 매칭 실패가 1건이라도 있으면 `clean.py`가 멈춘다.")
    w("")
    w("## 4. 0인 값과 제외 대상")
    w("")
    w(f"- 소아청소년과 의원 0곳인 시군구: **{fmt(len(zero_clinic))}곳** — 지표가 0이 된다 (가장 부족한 곳으로 나옴)")
    if len(zero_clinic):
        w("  - " + ", ".join(full_name(r) for r in zero_clinic.itertuples(index=False)))
    w(f"- 0~14세 인구 0명인 시군구: {fmt(len(zero_pop))}곳 (지표 계산 불가)")
    w(f"- 0~14세 인구 {fmt(MIN_CHILD_POP)}명 미만 (기본 순위 제외): **{fmt(len(small))}곳**")
    if len(small):
        w("  - " + ", ".join(f"{full_name(r)} {fmt(r.population_0_14)}명" for r in small.itertuples(index=False)))
    w("")
    w("## 5. 손으로 계산한 예시")
    w("")
    if len(ex):
        r = next(ex.itertuples(index=False))
        value = r.clinic_count / r.population_0_14 * 10000
        w(f"{full_name(r)}: 의원 {fmt(r.clinic_count)}곳 ÷ 0~14세 {fmt(r.population_0_14)}명 × 10,000 "
          f"= **{value:.2f}곳** (소아 1만 명당)")
    else:
        w(f"(예시 지역 {EXAMPLE_REGION}을 찾지 못함)")
    w("")

    out = args.report_dir / "count_check.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"완료: {out}")
    print(f"심평원 {raw:,} → 정리 후 {kept:,}, 의원 매칭 {clinic_ok:,}+{len(clinic_fail):,}, "
          f"시군구 {len(population):,} (KOSIS에만 {len(kosis_only):,})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hira", type=Path, default=ROOT / "data/raw/hira_pediatric_candidates.csv")
    parser.add_argument("--kosis", type=Path, help="KOSIS 원본 CSV 또는 ZIP 경로")
    parser.add_argument("--month", default="202610")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    build(parser.parse_args())

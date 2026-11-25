"""심평원 의원 목록과 KOSIS 인구를 시군구별로 결합합니다."""
import argparse
import csv
import re
import shutil
from io import TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIDO_PREFIX = {
    "서울": "11", "전남광주": "12", "부산": "26", "대구": "27",
    "인천": "28", "대전": "30", "울산": "31", "세종시": "36", "경기": "41",
    "강원": "51", "충북": "43", "충남": "44", "전북": "52",
    "경북": "47", "경남": "48", "제주": "50",
}
AGES = {f"{age}세" for age in range(15)}
YOUNG_AGES = {f"{age}세" for age in range(6)}
KOSIS_BASE = ROOT / "data/raw/101_DT_1B04006_M_2026"
KOSIS_COLUMNS = {
    "C행정구역(시군구)별": "region_code",
    "행정구역(시군구)별": "region_name",
    "연령별": "age",
    "시점": "month",
    "총인구수 (명)": "population",
}
MAPPING_KEYS = ["sidoCd", "sidoCdNm", "sgguCd", "sgguCdNm"]
MAPPING_HEADERS = {
    "sidoCd": "심평원 시도코드",
    "sidoCdNm": "심평원 시도명",
    "sgguCd": "심평원 시군구코드",
    "sgguCdNm": "심평원 시군구명",
    "region_code": "KOSIS 지역코드",
    "region_full_name": "KOSIS 지역명",
    "mapping_status": "매핑 결과",
}
OPTIONAL_CLINIC_COLUMNS = {"XPos": "경도", "YPos": "위도", "drTotCnt": "전체 의사 수"}

# 서버용 세부 목록과 같은 KOSIS 지역 코드를 사용한다.
REGION_HEADERS = {
    "region_code": "code",
    "sido_name": "sido",
    "region_name": "sigungu",
    "population_0_14": "child_pop",
    "clinic_count": "clinic_count",
    "recent_3yr_openings": "recent_3yr_openings",
    # 아래 열은 한계 대응방안용 보조 열이다. 위 6개 열의 값은 바뀌지 않는다.
    "clinic_count_broad": "clinic_count_broad",
    "doctor_count": "doctor_count",
    "population_month": "population_month",
    "hira_collected": "hira_collected",
}

# 대응방안 ①: 기관명에 '소아'가 없는 소아 진료 의원(○○어린이의원 등).
# 기본 순위의 clinic_count는 지금처럼 '소아'만 센다(검증표 2,117곳 유지).
# 아래 키워드까지 넣은 수는 참고용 clinic_count_broad 열과
# reports/name_keyword_check.csv(누락 의심 의원 목록)에만 남긴다.
EXTRA_NAME_KEYWORDS = ("어린이", "아동", "키즈")


def read_kosis_rows(stream, month):
    reader = csv.reader(stream, strict=True)
    for row in reader:
        header = [name.strip() for name in row]
        if KOSIS_COLUMNS.keys() <= set(header):
            break
        if len(header) > 1:
            raise ValueError("KOSIS CSV 헤더가 다릅니다. 시점이 열로 표시된 일반 CSV를 사용하세요.")
    else:
        raise ValueError("KOSIS CSV에서 필수 헤더를 찾을 수 없습니다.")

    if len(header) != len(set(header)):
        raise ValueError("KOSIS CSV 헤더에 중복 열이 있습니다.")

    positions = [header.index(name) for name in KOSIS_COLUMNS]
    month_column = header.index("시점")
    age_column = header.index("연령별")
    selected = []
    for row in reader:
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"KOSIS CSV {reader.line_num}행의 열 수가 헤더와 다릅니다.")
        if row[month_column].strip() == month and row[age_column].strip() in AGES:
            selected.append([row[index].strip() for index in positions])
    return pd.DataFrame(selected, columns=list(KOSIS_COLUMNS.values()))


def load_population(path, month):
    for encoding in ("utf-8-sig", "cp949"):
        try:
            if path.suffix.lower() == ".zip":
                with ZipFile(path) as archive:
                    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                    if len(members) != 1:
                        raise ValueError("KOSIS ZIP 안에는 CSV가 한 개 있어야 합니다.")
                    with TextIOWrapper(archive.open(members[0]), encoding=encoding, newline="") as stream:
                        data = read_kosis_rows(stream, month)
            else:
                with path.open(encoding=encoding, newline="") as stream:
                    data = read_kosis_rows(stream, month)
            break
        except UnicodeDecodeError:
            if encoding == "cp949":
                raise

    if data.empty:
        raise ValueError(f"KOSIS에서 {month} 0~14세 자료를 찾을 수 없습니다.")

    data["region_code"] = data["region_code"].str.lstrip("'")
    if not data["region_code"].str.fullmatch(r"[0-9]{2}(?:[0-9]{3})?").all() or data["region_name"].eq("").any():
        raise ValueError("KOSIS 지역 코드 또는 지역명이 올바르지 않습니다.")
    data["population"] = pd.to_numeric(data["population"], errors="raise")
    if (data["population"].isna() | data["population"].lt(0)
            | data["population"].mod(1).ne(0)).any():
        raise ValueError("KOSIS 인구에 누락 또는 잘못된 값이 있습니다.")
    if data.duplicated(["region_code", "age"]).any():
        raise ValueError("KOSIS 지역·나이별 행이 중복됩니다.")

    data["young_population"] = data["population"].where(data["age"].isin(YOUNG_AGES), 0)
    population = data.groupby(["region_code", "region_name"], as_index=False).agg(
        population_0_5=("young_population", "sum"),
        population_0_14=("population", "sum"),
        age_count=("age", "size"),
    )
    if not population["age_count"].eq(len(AGES)).all():
        raise ValueError("0~14세 자료가 모두 들어 있지 않은 지역이 있습니다.")
    age_columns = ["population_0_5", "population_0_14"]
    population[age_columns] = population[age_columns].astype(int)

    names = population.set_index("region_code")["region_name"].to_dict()
    regions = population[population["region_code"].str.fullmatch(r"\d{5}")].copy()
    regions = regions[~regions["region_name"].str.endswith("출장소")].copy()

    # 일반구가 있는 시의 합계 행은 제외하고 일반구를 사용한다.
    cities = {
        code[:4]: name.split("(")[0]
        for code, name in zip(regions["region_code"], regions["region_name"])
        if code.endswith("0") and name.split("(")[0].endswith("시")
    }
    district_rows = regions.loc[
        ~regions["region_code"].str.endswith("0")
        & regions["region_name"].str.endswith("구")
        & regions["region_code"].str[:4].isin(cities)
    ]
    district_groups = district_rows.groupby(district_rows["region_code"].str[:4])[age_columns].sum()
    for prefix, district_totals in district_groups.iterrows():
        city_totals = regions.loc[regions["region_code"].eq(prefix + "0"), age_columns].iloc[0]
        if not district_totals.eq(city_totals).all():
            raise ValueError(f"{cities[prefix]}의 일반구 인구 합계가 시 전체 인구와 다릅니다.")

    regions = regions[~(
        regions["region_code"].str.endswith("0")
        & regions["region_code"].str[:4].isin(district_groups.index)
    )].copy()

    regions["sido_prefix"] = regions["region_code"].str[:2]
    regions["sido_name"] = regions["sido_prefix"].map(names)
    regions["city_name"] = regions["region_code"].str[:4].map(cities).where(
        regions["region_code"].str[:4].isin(district_groups.index), ""
    )
    if regions["sido_name"].isna().any():
        raise ValueError("KOSIS 시군구의 상위 시도명을 확인할 수 없습니다.")

    national = population.loc[population["region_code"].eq("00"), age_columns]
    if len(national) != 1:
        raise ValueError("KOSIS 전국 인구 행(code=00)이 한 개여야 합니다.")
    totals = regions[age_columns].sum()
    if not totals.eq(national.iloc[0]).all():
        raise ValueError(
            "출장소와 시 전체 합계를 제외한 뒤 전국 인구와 일치하지 않습니다. "
            f"0~5세 지역합계 {totals.iloc[0]:,}/전국 {national.iloc[0, 0]:,}, "
            f"0~14세 지역합계 {totals.iloc[1]:,}/전국 {national.iloc[0, 1]:,}"
        )
    return regions.drop(columns="age_count")


def region_mapping(hira, regions):
    official_names = regions[["sido_name", "sido_prefix"]].drop_duplicates()
    sido_prefixes = {**SIDO_PREFIX, **dict(official_names.itertuples(index=False, name=None))}
    mapping = hira[MAPPING_KEYS].drop_duplicates().copy()
    mapping["sido_prefix"] = mapping["sidoCdNm"].str.strip().map(sido_prefixes)
    mapping["name_key"] = mapping["sgguCdNm"].str.replace(r"\s+", "", regex=True)
    aliases = []
    for region in regions.itertuples(index=False):
        short_name = region.region_name.replace(" ", "")
        if region.city_name:
            names = {region.city_name + short_name, region.city_name.removesuffix("시") + short_name}
        else:
            names = {short_name}
            if region.sido_prefix == "36":
                names.add("세종시")
            else:
                prefixes = {name for name, code in sido_prefixes.items() if code == region.sido_prefix}
                if region.sido_prefix == "12":
                    prefixes.add("광주")
                names.update(prefix + short_name for prefix in prefixes)
        aliases.extend((region.sido_prefix, name, region.region_code) for name in names)

    lookup = pd.DataFrame(aliases, columns=["sido_prefix", "name_key", "region_code"])
    lookup = lookup.drop_duplicates()
    ambiguous = lookup.duplicated(["sido_prefix", "name_key"], keep=False)
    mapping = mapping.merge(
        lookup.loc[~ambiguous], on=["sido_prefix", "name_key"], how="left", validate="many_to_one",
    )
    mapping["mapping_status"] = mapping["region_code"].notna().map(
        {True: "mapped", False: "unmatched"}
    )
    return mapping.drop(columns=["sido_prefix", "name_key"]).sort_values(["sidoCd", "sgguCd"])


def save_results(output_dir, regions, clinics):
    output_dir.mkdir(parents=True, exist_ok=True)
    region_path = output_dir / "regions.csv"
    clinic_path = output_dir / "clinics.csv"
    clinic_headers = {
        "region_code": "region_code",
        "yadmNm": "clinic_name",
        "addr": "address",
        "XPos": "longitude",
        "YPos": "latitude",
        "estbDd": "opened_date",
        "drTotCnt": "doctor_count",
    }
    clinic_output = clinics[list(clinic_headers)].rename(columns=clinic_headers)

    # 새 파일 두 개를 모두 쓰고 나서 기존 결과를 교체한다.
    with TemporaryDirectory(dir=output_dir) as directory:
        pending = Path(directory)
        next_regions = pending / "regions.csv"
        next_clinics = pending / "clinics.csv"
        regions.to_csv(next_regions, index=False, encoding="utf-8-sig")
        clinic_output.to_csv(next_clinics, index=False, encoding="utf-8-sig")
        backup = pending / "previous_regions.csv"
        if region_path.exists():
            shutil.copyfile(region_path, backup)

        next_regions.replace(region_path)
        try:
            next_clinics.replace(clinic_path)
        except OSError:
            if backup.exists():
                backup.replace(region_path)
            else:
                region_path.unlink()
            raise


def collected_date(path):
    """수집 날짜를 따로 주지 않으면 심평원 원본 파일을 마지막으로 저장한 날을 쓴다."""
    return pd.Timestamp(path.stat().st_mtime, unit="s", tz="Asia/Seoul").strftime("%Y%m%d")


def prepare_results(args):
    if not re.fullmatch(r"[0-9]{4}(?:0[1-9]|1[0-2])", args.month):
        raise ValueError("인구 기준월은 YYYYMM 형식으로 입력하세요. 예: 202610")
    if args.collected and not re.fullmatch(r"[0-9]{8}", args.collected):
        raise ValueError("수집일은 YYYYMMDD 형식으로 입력하세요. 예: 20261122")
    kosis_path = args.kosis
    if kosis_path is None:
        kosis_path = KOSIS_BASE.with_suffix(".csv")
        if not kosis_path.is_file():
            kosis_path = KOSIS_BASE.with_suffix(".zip")
    if not kosis_path.is_file():
        raise ValueError(f"KOSIS 원본을 찾을 수 없습니다: {kosis_path}")
    if not args.hira.is_file():
        raise ValueError(f"심평원 원본을 찾을 수 없습니다: {args.hira}")
    population = load_population(kosis_path, args.month)
    hira = pd.read_csv(args.hira, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    required = {*MAPPING_KEYS, "ykiho", "yadmNm", "addr"}
    missing_columns = sorted(required - set(hira.columns))
    if missing_columns:
        message = "심평원 원본 필수 열 누락: " + ", ".join(missing_columns)
        raise ValueError(message)
    ids = hira["ykiho"].str.strip()
    blank_ids = int(ids.eq("").sum())
    duplicate_ids = int(ids[ids.ne("")].duplicated().sum())
    blank_names = int(hira["yadmNm"].str.strip().eq("").sum())
    if hira.empty or blank_ids or duplicate_ids or blank_names:
        message = (
            f"심평원 원본 {len(hira):,}행, 빈 기관 ID {blank_ids:,}건, "
            f"중복 기관 ID {duplicate_ids:,}건, 빈 기관명 {blank_names:,}건"
        )
        raise ValueError(message)
    has_doctor_count = "drTotCnt" in hira
    for column in (*OPTIONAL_CLINIC_COLUMNS, "estbDd"):
        if column not in hira:
            hira[column] = ""

    mapping = region_mapping(hira, population)
    mapping_path = args.report_dir / "region_mapping.csv"
    region_names = (
        population["sido_name"] + " " + population["city_name"] + " " + population["region_name"]
    ).str.replace(r"\s+", " ", regex=True)
    region_names = region_names.where(
        population["sido_name"].ne(population["region_name"]), population["sido_name"],
    )
    mapping_output = mapping.assign(
        region_full_name=mapping["region_code"].map(dict(zip(population["region_code"], region_names))),
        mapping_status=mapping["mapping_status"].map({"mapped": "연결 성공", "unmatched": "연결 실패"}),
    )
    # 매핑표에는 심평원 지역 조합별 연결 결과와 KOSIS에만 있는 지역을 함께 남긴다.
    kosis_only = population.loc[
        ~population["region_code"].isin(mapping["region_code"]), ["region_code"]
    ].assign(region_full_name=region_names, mapping_status="KOSIS에만 있음")
    if not kosis_only.empty:
        mapping_output = pd.concat([mapping_output, kosis_only], ignore_index=True)
    with TemporaryDirectory(dir=args.report_dir) as directory:
        pending = Path(directory) / mapping_path.name
        mapping_output[list(MAPPING_HEADERS)].rename(columns=MAPPING_HEADERS).to_csv(
            pending, index=False, encoding="utf-8-sig",
        )
        pending.replace(mapping_path)
    name_matches = hira["yadmNm"].str.contains("소아", regex=False)
    clinics = hira.loc[name_matches].merge(
        mapping, on=MAPPING_KEYS, how="left", validate="many_to_one",
    )
    names = population[["region_code", "sido_name", "city_name", "region_name"]]
    clinics = clinics.merge(names, on="region_code", how="left", validate="many_to_one")
    counts = clinics["region_code"].value_counts()
    unmatched = clinics.loc[clinics["region_code"].isna()]
    source_codes = clinics[["sidoCd", "sgguCd"]].apply(lambda column: column.str.strip())
    blank_codes = source_codes.eq("").any(axis=1)
    associations = source_codes.assign(region_code=clinics["region_code"])
    associations = associations.loc[~blank_codes].dropna().drop_duplicates()
    conflicts = associations.loc[associations.duplicated(["sidoCd", "sgguCd"], keep=False)]
    conflict_count = len(conflicts[["sidoCd", "sgguCd"]].drop_duplicates())
    summary = population.drop(columns="sido_prefix").copy()
    summary["population_month"] = args.month
    summary["hira_collected"] = args.collected or collected_date(args.hira)
    summary["clinic_count"] = summary["region_code"].map(counts).fillna(0).astype(int)
    # 인구 기준월을 포함한 36개월 동안 개설한 의원을 센다.
    month = pd.Period(args.month, freq="M")
    dates = clinics["estbDd"].str.strip()
    opened = pd.to_datetime(
        dates.where(dates.str.fullmatch(r"[0-9]{8}")), format="%Y%m%d", errors="coerce",
    )
    in_period = opened.ge((month - 35).start_time) & opened.lt((month + 1).start_time)
    recent_counts = clinics.loc[in_period, "region_code"].value_counts()
    summary["recent_3yr_openings"] = summary["region_code"].map(recent_counts).fillna(0).astype(int)

    # 대응방안 ④: 의원 1곳 = 1 대신 의사 수(drTotCnt)로도 볼 수 있게 지역별 합계를 남긴다.
    doctors = pd.to_numeric(clinics["drTotCnt"].str.strip(), errors="coerce")
    clinics["drTotCnt"] = doctors.astype("Int64")
    if has_doctor_count:
        doctor_sums = doctors.groupby(clinics["region_code"]).sum(min_count=1)
        summary["doctor_count"] = summary["region_code"].map(doctor_sums).fillna(0).astype(int)
    else:
        summary["doctor_count"] = pd.NA

    # 대응방안 ①: 추가 키워드로 잡히는 의원 수(참고용).
    extra = hira.loc[~name_matches & hira["yadmNm"].str.contains("|".join(EXTRA_NAME_KEYWORDS))]
    extra = extra.merge(mapping, on=MAPPING_KEYS, how="left", validate="many_to_one")
    extra_counts = extra["region_code"].value_counts()
    summary["clinic_count_broad"] = (
        summary["clinic_count"] + summary["region_code"].map(extra_counts).fillna(0).astype(int)
    )
    keyword_report = pd.DataFrame({
        "KOSIS 지역코드": extra["region_code"],
        "KOSIS 지역명": extra["region_code"].map(dict(zip(population["region_code"], region_names))),
        "기관명": extra["yadmNm"],
        "잡힌 키워드": extra["yadmNm"].map(
            lambda name: ", ".join(word for word in EXTRA_NAME_KEYWORDS if word in name)
        ),
        "주소": extra["addr"],
        "전체 의사 수": extra["drTotCnt"],
        "개설일": extra["estbDd"],
        "확인 결과": "",
    }).sort_values(["KOSIS 지역코드", "기관명"])
    with TemporaryDirectory(dir=args.report_dir) as directory:
        pending = Path(directory) / "name_keyword_check.csv"
        keyword_report.to_csv(pending, index=False, encoding="utf-8-sig")
        pending.replace(args.report_dir / "name_keyword_check.csv")
    if len(summary) != summary["region_code"].nunique():
        raise ValueError("최종 집계에 시군구 코드가 중복됩니다.")
    if summary["clinic_count"].sum() != len(clinics) - len(unmatched):
        raise ValueError("최종 집계와 매핑 성공 의원 수가 일치하지 않습니다.")

    errors = []
    if not unmatched.empty:
        errors.append(f"지역 매핑 실패 의원 {len(unmatched):,}곳")
    if blank_codes.any():
        errors.append(f"심평원 지역코드 빈값 {blank_codes.sum():,}곳")
    if conflict_count:
        errors.append(f"심평원 지역코드 충돌 {conflict_count:,}개 조합")
    if errors:
        raise ValueError(", ".join(errors))

    return summary[list(REGION_HEADERS)].rename(columns=REGION_HEADERS), clinics


def clean(args):
    args.report_dir.mkdir(parents=True, exist_ok=True)
    regional_output, clinics = prepare_results(args)
    try:
        save_results(args.output_dir, regional_output, clinics)
    except PermissionError as exc:
        raise ValueError(
            f"결과 CSV를 교체할 수 없습니다. Excel 등에서 열린 결과 파일을 닫고 다시 실행하세요. ({exc})"
        ) from exc
    print(f"완료: {len(regional_output):,}개 지역, {len(clinics):,}개 의원. 결과: {args.output_dir}")
    extra = int(regional_output["clinic_count_broad"].sum() - regional_output["clinic_count"].sum())
    print(f"참고: '{'·'.join(EXTRA_NAME_KEYWORDS)}' 키워드로 더 잡히는 의원 {extra:,}곳 "
          f"(기본 순위에는 넣지 않음, 목록: {args.report_dir / 'name_keyword_check.csv'})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hira", type=Path, default=ROOT / "data/raw/hira_pediatric_candidates.csv")
    parser.add_argument("--kosis", type=Path, help="KOSIS 원본 CSV 또는 ZIP 경로")
    parser.add_argument("--month", default="202610")
    parser.add_argument("--collected", help="심평원 수집일 YYYYMMDD (비우면 원본 파일 저장일)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    try:
        clean(parser.parse_args())
    except (ValueError, OSError, BadZipFile, csv.Error) as exc:
        parser.exit(1, f"오류: {exc}\n")

"""교차 실행용 샘플 데이터를 시도 하나로 줄여 data/sample/ 에 만듭니다.

실행 (clean.py 를 돌린 뒤):
    python src/make_sample.py            # 기본: 서울(KOSIS 코드 11)
    python src/make_sample.py --sido 31  # 울산

data/processed/regions.csv, clinics.csv 에서 지역 코드가 해당 시도로 시작하는 행만 남긴다.
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sido", default="11", help="KOSIS 시도 코드 2자리 (기본 11 = 서울)")
    parser.add_argument("--processed", type=Path, default=ROOT / "data/processed")
    parser.add_argument("--sample", type=Path, default=ROOT / "data/sample")
    args = parser.parse_args()

    regions = pd.read_csv(args.processed / "regions.csv", dtype={"code": str}, encoding="utf-8-sig")
    clinics = pd.read_csv(args.processed / "clinics.csv", dtype={"region_code": str}, encoding="utf-8-sig")
    regions = regions[regions["code"].str.startswith(args.sido)]
    clinics = clinics[clinics["region_code"].str.startswith(args.sido)]
    if regions.empty:
        raise SystemExit(f"시도 코드 {args.sido}로 시작하는 지역이 없습니다.")
    if clinics["region_code"].nunique() and not clinics["region_code"].isin(regions["code"]).all():
        raise SystemExit("샘플 의원 중 지역 요약에 없는 지역 코드가 있습니다.")

    args.sample.mkdir(parents=True, exist_ok=True)
    regions.to_csv(args.sample / "regions.csv", index=False, encoding="utf-8-sig")
    clinics.to_csv(args.sample / "clinics.csv", index=False, encoding="utf-8-sig")
    print(f"샘플 완료: 지역 {len(regions):,}개, 의원 {len(clinics):,}곳 "
          f"(의원 수 합계 {int(regions['clinic_count'].sum()):,}) → {args.sample}")

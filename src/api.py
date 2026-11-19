"""
곁 서버 (FastAPI) — 서버 담당 파일

clean.py가 만든 시군구별 표를 읽어서, 화면(app.py)이 쓸 수 있게 JSON으로 내보냅니다.
서버는 공공 API를 직접 부르지 않으므로 인증키(.env)가 없어도 실행됩니다.

실행 (프로젝트 맨 위 폴더에서):
    uvicorn src.api:app --reload
확인:
    http://127.0.0.1:8000/docs
"""
import os
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 데이터 담당과 맞춰야 하는 부분 (여기만 고치면 됩니다)
# ---------------------------------------------------------------------------

# 데이터 파일 위치. 위에서부터 찾아서 처음 있는 파일을 씁니다.
# 1) clean.py 결과  2) 인증키 없이 실행할 때 쓰는 샘플
DATA_CANDIDATES = [
    ROOT / "data" / "processed" / "regions.csv",
    ROOT / "data" / "sample" / "regions.csv",
]

# 데이터 파일에 꼭 있어야 하는 열
REQUIRED_COLUMNS = [
    "code",          # 시군구 코드 (예: 11110)
    "sido",          # 시도 이름 (예: 서울특별시)
    "sigungu",       # 시군구 이름 (예: 종로구)
    "child_pop",     # 0~14세 인구
    "clinic_count",  # 소아청소년과 의원 수
]

# 테스트할 때는 환경변수로 다른 파일을 가리킬 수 있습니다.
ENV_DATA_PATH = "GEOT_DATA_PATH"

# ---------------------------------------------------------------------------
# 데이터 읽기와 계산 (서버 없이도 테스트할 수 있게 함수로 분리)
# ---------------------------------------------------------------------------


class DataError(Exception):
    """데이터 파일이 없거나 형식이 맞지 않을 때"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def find_data_file() -> Path:
    env_path = os.getenv(ENV_DATA_PATH)
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        raise DataError(503, f"데이터 파일이 없습니다: {path}")

    for path in DATA_CANDIDATES:
        if path.exists():
            return path
    raise DataError(
        503,
        "데이터 파일이 없습니다. clean.py를 먼저 실행하거나 "
        "data/sample/regions.csv가 있는지 확인하세요.",
    )


def load_regions(path: Path) -> pd.DataFrame:
    """CSV를 읽고, 소아 1만 명당 의원 수(clinics_per_10k)를 계산해 붙입니다."""
    df = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(500, f"데이터 파일에 필요한 열이 없습니다: {missing}")

    df["child_pop"] = pd.to_numeric(df["child_pop"], errors="coerce")
    df["clinic_count"] = pd.to_numeric(df["clinic_count"], errors="coerce")

    # 소아 인구가 0이거나 비어 있으면 나눌 수 없으므로 비워 둠 (응답에서는 null)
    valid = df["child_pop"] > 0
    df["clinics_per_10k"] = float("nan")
    df.loc[valid, "clinics_per_10k"] = (
        df.loc[valid, "clinic_count"] / df.loc[valid, "child_pop"] * 10000
    ).round(2)
    return df


def to_records(df: pd.DataFrame) -> list[dict]:
    """표를 JSON으로 보낼 수 있는 목록으로 바꿉니다. 빈 값(NaN)은 None으로."""
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def get_data() -> tuple[pd.DataFrame, Path]:
    try:
        path = find_data_file()
        return load_regions(path), path
    except DataError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

app = FastAPI(
    title="곁 API",
    description="시군구별 소아(0~14세) 인구 1만 명당 소아청소년과 의원 수",
)


@app.get("/health")
def health():
    """서버가 켜져 있는지 확인"""
    return {"status": "ok"}


@app.get("/sido")
def list_sido():
    """화면의 시도 선택 목록용"""
    df, _ = get_data()
    return {"items": sorted(df["sido"].dropna().unique().tolist())}


@app.get("/regions")
def list_regions(
    sido: str | None = Query(None, description="시도 이름. 비우면 전국"),
    min_child_pop: int = Query(0, ge=0, description="소아 인구가 이 값 이상인 곳만"),
    sort: Literal["asc", "desc"] = Query(
        "asc", description="asc: 소아과가 부족한 곳부터, desc: 많은 곳부터"
    ),
    limit: int = Query(20, ge=1, le=300, description="최대 몇 개를 보여줄지"),
):
    """개원 후보 지역 목록 (1만 명당 의원 수 기준 정렬)"""
    df, path = get_data()

    if sido:
        df = df[df["sido"] == sido]
        if df.empty:
            raise HTTPException(404, f"'{sido}'에 해당하는 지역이 없습니다.")

    df = df[df["child_pop"].fillna(0) >= min_child_pop]

    # 계산할 수 없는 지역(소아 인구 0)은 항상 맨 뒤로
    df = df.sort_values(
        "clinics_per_10k", ascending=(sort == "asc"), na_position="last"
    ).head(limit)

    return {
        "count": len(df),
        "data_file": path.name,
        "items": to_records(df),
    }


@app.get("/regions/{code}")
def get_region(code: str):
    """시군구 코드 하나의 상세"""
    df, _ = get_data()
    row = df[df["code"] == code]
    if row.empty:
        raise HTTPException(404, f"시군구 코드 '{code}'를 찾을 수 없습니다.")
    return to_records(row)[0]
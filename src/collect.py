import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# =========================================================
# 기본 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")

API_KEY = os.getenv("HIRA_API_KEY")

if not API_KEY:
    raise SystemExit(".env 파일에 HIRA_API_KEY가 없습니다.")

URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"

RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

FINAL_PATH = RAW_DIR / "hira_pediatric_candidates.csv"
PARTIAL_PATH = RAW_DIR / "hira_pediatric_candidates.partial.csv"
STATUS_PATH = RAW_DIR / "hira_collect_status.json"

# 현재 API에서 확인한 시도 코드
SIDO_CODES = {
    "110000": "서울",
    "210000": "부산",
    "220000": "인천",
    "230000": "대구",
    "250000": "대전",
    "260000": "울산",
    "310000": "경기",
    "320000": "강원",
    "330000": "충북",
    "340000": "충남",
    "350000": "전북",
    "360000": "전남광주",
    "370000": "경북",
    "380000": "경남",
    "390000": "제주",
    "410000": "세종시",
}

NUM_ROWS = 500
MAX_RETRIES = 5
TIMEOUT = (5, 20)  # 연결 5초, 응답 20초

# =========================================================
# 파일 저장
# =========================================================


def save_csv(df, path):
    temp = path.with_suffix(path.suffix + ".tmp")

    df.to_csv(
        temp,
        index=False,
        encoding="utf-8-sig",
    )

    os.replace(temp, path)


def save_status(status):
    temp = STATUS_PATH.with_suffix(".json.tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    os.replace(temp, STATUS_PATH)


def load_status():
    if not STATUS_PATH.exists():
        return {}

    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    # 이전 형식의 중첩 상태도 시도 코드와 양의 정수 건수로 정규화
    entries = dict(raw)
    for value in raw.values():
        if isinstance(value, dict):
            entries.update({code: count for code, count in value.items() if code in SIDO_CODES})

    status = {}
    for code in SIDO_CODES:
        count = entries.get(code)
        if isinstance(count, dict):
            count = next(
                (count[key] for key in ("count", "rows", "totalCount", "row_count") if key in count),
                None,
            )
        if isinstance(count, str) and count.isascii() and count.isdecimal():
            count = int(count)
        if type(count) is int and count > 0:
            status[code] = count

    return status


def read_csv(path):
    return pd.read_csv(
        path,
        dtype={"sidoCd": str, "sgguCd": str, "ykiho": str},
    )


def resume_partial(status):
    if not PARTIAL_PATH.exists():
        return pd.DataFrame(), {}

    try:
        df = read_csv(PARTIAL_PATH)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), {}

    if "sidoCd" not in df or "ykiho" not in df:
        return pd.DataFrame(), {}

    counts = df["sidoCd"].value_counts()
    confirmed = {}
    for code, expected in status.items():
        actual = counts.get(code, 0)
        if actual == expected:
            confirmed[code] = expected
        else:
            print(f"[경고] {SIDO_CODES[code]} 건수 불일치: {actual:,}/{expected:,}건. 다시 수집합니다.")

    df = df[df["sidoCd"].isin(confirmed)]
    ids = institution_ids(df)
    invalid = ids.isna() | ids.duplicated(keep=False)
    for code in df.loc[invalid, "sidoCd"].unique():
        print(f"[경고] {SIDO_CODES[code]} 빈 ID 또는 중복 ID가 있어 다시 수집합니다.")
        del confirmed[code]

    return df[df["sidoCd"].isin(confirmed)].copy(), confirmed

# =========================================================
# API 요청
# =========================================================


def fetch_page(session, sido_code, page_no):
    params = {
        "ServiceKey": API_KEY,
        "pageNo": page_no,
        "numOfRows": NUM_ROWS,
        "sidoCd": sido_code,

        # 의원
        "clCd": "31",

        # 소아청소년과 진료과목
        "dgsbjtCd": "11",

        "_type": "json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        error_name = "UnknownError"

        try:
            response = session.get(
                URL,
                params=params,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            response_data = response.json().get("response")

            if not isinstance(response_data, dict):
                raise ValueError("invalid_response")

            header = response_data.get("header", {})

            result_code = str(header.get("resultCode", ""))

            if result_code and result_code != "00":
                raise ValueError(f"api_error_{result_code}")

            body = response_data.get("body")

            if not isinstance(body, dict):
                raise ValueError("invalid_body")

            total_count = int(body.get("totalCount", 0))

            # 이번 프로젝트에서는 모든 시도에 데이터가 있어야 함
            if total_count == 0:
                raise ValueError("zero_total_count")

            items = body.get("items") or {}

            if isinstance(items, dict):
                rows = items.get("item", [])
            else:
                rows = []

            if isinstance(rows, dict):
                rows = [rows]

            # 아직 받을 데이터가 남았는데 빈 페이지가 나온 경우
            expected_start = (page_no - 1) * NUM_ROWS

            if total_count > expected_start and not rows:
                raise ValueError("unexpected_empty_page")

            return rows, total_count
        except requests.HTTPError as e:
            if e.response is not None:
                error_name = f"HTTP {e.response.status_code}"
            else:
                error_name = "HTTPError"
        except requests.Timeout:
            error_name = "Timeout"
        except requests.ConnectionError:
            error_name = "ConnectionError"
        except requests.RequestException:
            error_name = "RequestException"
        except (ValueError, KeyError, TypeError) as e:
            # 우리가 만든 짧은 메시지만 사용
            error_name = str(e)

        if attempt == MAX_RETRIES:
            raise RuntimeError(
                f"API 요청 최종 실패 "
                f"(시도={sido_code}, 페이지={page_no}, 오류={error_name})"
            ) from None

        wait = min(2 ** (attempt - 1), 8)

        print(
            f"[경고] 요청 실패 "
            f"{attempt}/{MAX_RETRIES} | "
            f"{error_name} | "
            f"{wait}초 후 재시도"
        )

        time.sleep(wait)

# =========================================================
# 한 시도 수집
# =========================================================


def fetch_region(session, sido_code, sido_name):
    rows = []
    page_no = 1
    expected_total = None
    start = time.perf_counter()

    while True:
        page_rows, total_count = fetch_page(
            session,
            sido_code,
            page_no,
        )

        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise RuntimeError(
                f"{sido_name}: 수집 중 totalCount가 변경되었습니다."
            )

        rows.extend(page_rows)

        print(
            f"{sido_name} | "
            f"{len(rows):,} / {expected_total:,}건"
        )

        if len(rows) >= expected_total:
            break

        page_no += 1

    # 일부 페이지만 받은 상태를 완료 처리하지 않음
    if len(rows) != expected_total:
        raise RuntimeError(
            f"{sido_name}: "
            f"{len(rows):,}/{expected_total:,}건만 수집됨"
        )

    elapsed = time.perf_counter() - start

    print(
        f"[완료] {sido_name} "
        f"{len(rows):,}건 | {elapsed:.1f}초"
    )

    return rows

# =========================================================
# 데이터 검증
# =========================================================


def institution_ids(df):
    if "ykiho" not in df:
        raise RuntimeError("기관 ID 열(ykiho)이 없습니다.")

    ids = df["ykiho"].astype("string").str.strip()
    return ids.replace("", pd.NA)


def validate_institution_ids(df):
    ids = institution_ids(df)
    blank_count = int(ids.isna().sum())
    duplicate_count = int(ids.dropna().duplicated().sum())
    if blank_count or duplicate_count:
        raise RuntimeError(
            f"ykiho 검증 실패: 빈 ID {blank_count:,}건, 중복 ID {duplicate_count:,}건"
        )
    return ids


def validate(df, status=None):
    if df.empty:
        raise RuntimeError("수집 데이터가 없습니다.")

    df["sidoCd"] = df["sidoCd"].astype(str)

    codes = set(df["sidoCd"].unique())
    expected_codes = set(SIDO_CODES)

    missing = expected_codes - codes

    if missing:
        names = [SIDO_CODES[x] for x in sorted(missing)]

        raise RuntimeError(
            "미수집 지역: " + ", ".join(names)
        )

    validate_institution_ids(df)

    if status:
        counts = df["sidoCd"].value_counts()
        for code, expected_count in status.items():
            actual_count = counts.get(code, 0)
            if actual_count != expected_count:
                raise RuntimeError(
                    f"{SIDO_CODES[code]} 건수 불일치: "
                    f"{actual_count:,}/{expected_count:,}"
                )

    return {
        "rows": len(df),
        "regions": len(codes),
        "duplicates": 0,
    }


def print_result(result, elapsed=None):
    print("\n==============================")
    print("수집 검증 결과")
    print("==============================")

    print(f"전체 행 수: {result['rows']:,}")
    print(f"수집 시도: {result['regions']} / {len(SIDO_CODES)}")
    print(f"ykiho 중복: {result['duplicates']:,}")
    print("[완료] 전국 수집 완료")

    if elapsed is not None:
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        print(
            f"총 소요시간: "
            f"{minutes:02d}분 {seconds:02d}초"
        )

# =========================================================
# 전국 수집
# =========================================================


def collect(fresh=False):
    start_time = time.perf_counter()

    # --fresh는 기존 최종 CSV는 보존하고
    # 새 수집 작업만 처음부터 시작
    if fresh:
        if PARTIAL_PATH.exists():
            PARTIAL_PATH.unlink()

        if STATUS_PATH.exists():
            STATUS_PATH.unlink()

    # 최종 CSV가 있고 진행 중 파일이 없으면 바로 검증
    if (
        FINAL_PATH.exists()
        and not PARTIAL_PATH.exists()
        and not STATUS_PATH.exists()
        and not fresh
    ):
        print_result(validate(read_csv(FINAL_PATH)))
        return

    status = load_status()

    # 최종 CSV 교체 후 상태 파일을 지우기 전에 중단된 경우
    if FINAL_PATH.exists() and STATUS_PATH.exists() and not PARTIAL_PATH.exists() and not fresh:
        if status.keys() == SIDO_CODES.keys():
            try:
                result = validate(read_csv(FINAL_PATH), status)
            except (RuntimeError, pd.errors.EmptyDataError, KeyError) as exc:
                print(f"[경고] 최종 CSV와 완료 상태를 확인할 수 없습니다: {exc}")
            else:
                STATUS_PATH.unlink()
                print_result(result)
                return

    df, confirmed = resume_partial(status)
    if confirmed != status:
        save_status(confirmed)
    status = confirmed

    print(
        f"이어받기 완료 지역: "
        f"{len(status)} / {len(SIDO_CODES)}"
    )

    with requests.Session() as session:
        for sido_code, sido_name in SIDO_CODES.items():
            if sido_code in status:
                print(f"[완료] {sido_name} 건너뜀")
                continue

            print(
                f"\n===== {sido_name} 수집 시작 ====="
            )

            rows = fetch_region(
                session,
                sido_code,
                sido_name,
            )

            region_df = pd.DataFrame(rows)

            if "sidoCd" not in region_df:
                raise RuntimeError(f"{sido_name}: 응답에 시도 코드(sidoCd)가 없습니다.")

            region_df["sidoCd"] = (
                region_df["sidoCd"]
                .astype(str)
            )

            mismatched_count = int(region_df["sidoCd"].ne(sido_code).sum())
            if mismatched_count:
                raise RuntimeError(
                    f"{sido_name}: 요청한 시도 코드({sido_code})와 "
                    f"일치하지 않는 응답 {mismatched_count:,}건"
                )

            if "sgguCd" in region_df.columns:
                region_df["sgguCd"] = (
                    region_df["sgguCd"]
                    .astype(str)
                )

            try:
                region_ids = validate_institution_ids(region_df)
            except RuntimeError as exc:
                raise RuntimeError(f"{sido_name}: {exc}") from exc

            if not df.empty:
                conflicts = institution_ids(df).isin(region_ids)
                if conflicts.any():
                    affected = df.loc[conflicts, "sidoCd"].unique()
                    for code in affected:
                        status.pop(code)
                    save_status(status)
                    names = ", ".join(SIDO_CODES[code] for code in affected)
                    raise RuntimeError(
                        f"{sido_name}: {names}와 ykiho 중복. "
                        "관련 시도는 다음 실행에서 다시 수집합니다."
                    )

            df = pd.concat(
                [df, region_df],
                ignore_index=True,
            )

            # CSV 저장 성공 후에만 완료 상태 기록
            save_csv(df, PARTIAL_PATH)

            status[sido_code] = len(region_df)

            save_status(status)

    # =====================================================
    # 최종 검증
    # =====================================================
    result = validate(df, status)

    # 모든 검증 성공 후에만 최종 CSV 교체
    os.replace(
        PARTIAL_PATH,
        FINAL_PATH,
    )

    if STATUS_PATH.exists():
        STATUS_PATH.unlink()

    elapsed = time.perf_counter() - start_time

    print_result(
        result,
        elapsed,
    )

# =========================================================
# 실행
# =========================================================


if __name__ == "__main__":
    fresh = "--fresh" in sys.argv

    collect(fresh=fresh)

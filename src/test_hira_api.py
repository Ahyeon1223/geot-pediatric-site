"""
심평원 병원정보서비스 시험 호출
- 확인할 것: (1) 진료과목으로 걸러지는지  (2) 지역코드 체계  (3) 응답 필드
- 인증키는 같은 폴더의 .env 파일에서 읽는다 (HIRA_API_KEY=발급받은키)
- 실행: pip install requests python-dotenv  →  python test_hira_api.py
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("HIRA_API_KEY")
if not KEY:
    raise SystemExit(".env 파일에 HIRA_API_KEY가 없습니다.")

# 공공데이터포털 상세 페이지의 '요청주소'와 다르면 여기만 고치세요.
URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"


def call(label, **params):
    base = {"ServiceKey": KEY, "pageNo": 1, "numOfRows": 5, "_type": "json"}
    base.update(params)
    r = requests.get(URL, params=base, timeout=20)
    print(f"\n===== {label} =====")
    print("HTTP", r.status_code)
    try:
        body = r.json()["response"]["body"]
    except Exception:
        # JSON이 아니면(오류 메시지·XML) 앞부분만 보여 준다
        print(r.text[:500])
        return None
    print("전체 건수(totalCount):", body.get("totalCount"))
    items = body.get("items") or {}
    items = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(items, dict):
        items = [items]
    if items:
        print("응답 필드:", list(items[0].keys()))
    for it in items:
        print(" -", it.get("yadmNm"), "|", it.get("clCdNm"),
              "| 시도", it.get("sidoCd"), it.get("sidoCdNm"),
              "| 시군구", it.get("sgguCd"), it.get("sgguCdNm"),
              "| 개설일", it.get("estbDd"))
    return body.get("totalCount")


# 1) 서울 전체 의원 (sidoCd 110000 = 서울, clCd 31 = 의원으로 추정)
all_cnt = call("서울 의원 전체", sidoCd="110000", clCd="31")

# 2) 같은 조건 + 진료과목 소아청소년과 (dgsbjtCd 11로 추정)
ped_cnt = call("서울 의원 중 소아청소년과", sidoCd="110000", clCd="31", dgsbjtCd="11")

print("\n===== 판단 =====")
if all_cnt and ped_cnt and int(ped_cnt) < int(all_cnt):
    print(f"진료과목 필터가 작동하는 것으로 보임: {all_cnt} → {ped_cnt}건")
else:
    print("진료과목 필터가 안 먹었을 수 있음. 위 결과를 확인하세요.")


# 3) 진료과목 필터는 '소아청소년과를 진료과목 중 하나로 둔' 의원까지 모두 가져온다.
#    소아청소년과를 간판으로 건 의원만 세기 위해 이름(yadmNm)에 '소아'가 들어간 곳을 센다.
def fetch_all(**params):
    rows, page = [], 1
    while True:
        base = {"ServiceKey": KEY, "pageNo": page, "numOfRows": 500, "_type": "json"}
        base.update(params)
        body = requests.get(URL, params=base, timeout=30).json()["response"]["body"]
        items = body.get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        rows.extend(items)
        if not items or len(rows) >= int(body.get("totalCount", 0)):
            return rows
        page += 1


print("\n===== 이름 기준으로 다시 세기 (서울) =====")
rows = fetch_all(sidoCd="110000", clCd="31", dgsbjtCd="11")
named = [r for r in rows if "소아" in str(r.get("yadmNm", ""))]
print(f"진료과목 필터 결과 {len(rows)}건 중 이름에 '소아' 포함: {len(named)}건")

by_gu = {}
for r in named:
    gu = r.get("sgguCdNm")
    by_gu[gu] = by_gu.get(gu, 0) + 1
print("구별 개수 (많은 순):")
for gu, n in sorted(by_gu.items(), key=lambda x: -x[1]):
    print(f"  {gu}: {n}")
print("이름 예시:", [r.get("yadmNm") for r in named[:5]])

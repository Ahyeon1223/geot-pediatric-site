"""
교차 실행용 서울 샘플(data/sample/)이 건수 검증표의 화면 설계안 값과 같은지 확인

    1위 서대문구 10 / 24,934 × 10,000 = 4.01
    종로구        4 /  8,870 × 10,000 = 4.51
"""
from fastapi.testclient import TestClient

from src.api import ROOT, app


def test_sample_matches_count_check(monkeypatch):
    monkeypatch.setenv("GEOT_DATA_PATH", str(ROOT / "data" / "sample" / "regions.csv"))
    client = TestClient(app)
    items = client.get("/regions", params={"sido": "서울특별시", "limit": 300}).json()["items"]
    assert len(items) == 25
    assert (items[0]["sigungu"], items[0]["clinics_per_10k"]) == ("서대문구", 4.01)
    assert client.get("/regions/11110").json()["clinics_per_10k"] == 4.51
    assert client.get("/meta").json()["population_month"] == "202610"
    detail = client.get("/regions/11410").json()
    assert len(detail["clinics"]) == 10
    # 의사 수(drTotCnt): 서대문구 의원 10곳의 의사 11명 → 11 / 24,934 × 10,000 = 4.41
    assert sum(c["doctor_count"] for c in detail["clinics"]) == detail["doctor_count"] == 11
    assert detail["doctors_per_10k"] == 4.41

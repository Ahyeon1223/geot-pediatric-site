
"""
서버(src/api.py) 테스트 — 서버 담당 파일

실행 (프로젝트 맨 위 폴더에서):
    python -m pytest

진짜 데이터 대신 테스트 전용 작은 CSV를 임시 폴더에 만들어 씁니다.
그래서 데이터 담당의 파일이 아직 없어도 테스트가 돌아갑니다.
"""
import pytest
from fastapi.testclient import TestClient

from src.api import app

# 종로구: 5 / 10000 * 10000 = 5.0
# 중구(서울): 1 / 8000 * 10000 = 1.25  → 가장 부족
# 중구(부산): 소아 인구 0 → 계산 불가(null), 항상 맨 뒤
SAMPLE_CSV = """code,sido,sigungu,child_pop,clinic_count
11110,서울특별시,종로구,10000,5
11140,서울특별시,중구,8000,1
26110,부산광역시,중구,0,2
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_file = tmp_path / "regions.csv"
    data_file.write_text(SAMPLE_CSV, encoding="utf-8")
    monkeypatch.setenv("GEOT_DATA_PATH", str(data_file))
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_regions_sorted_shortage_first(client):
    r = client.get("/regions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["code"] for i in items] == ["11140", "11110", "26110"]
    assert items[0]["clinics_per_10k"] == 1.25
    assert items[1]["clinics_per_10k"] == 5.0


def test_zero_child_pop_is_null(client):
    r = client.get("/regions/26110")
    assert r.status_code == 200
    assert r.json()["clinics_per_10k"] is None


def test_regions_desc(client):
    items = client.get("/regions", params={"sort": "desc"}).json()["items"]
    assert items[0]["code"] == "11110"
    assert items[-1]["code"] == "26110"  # 계산 불가는 내림차순에서도 맨 뒤


def test_filter_by_sido(client):
    r = client.get("/regions", params={"sido": "서울특별시"})
    assert r.json()["count"] == 2


def test_unknown_sido_404(client):
    r = client.get("/regions", params={"sido": "없는도"})
    assert r.status_code == 404


def test_min_child_pop(client):
    r = client.get("/regions", params={"min_child_pop": 9000})
    assert [i["code"] for i in r.json()["items"]] == ["11110"]


def test_limit(client):
    r = client.get("/regions", params={"limit": 1})
    assert r.json()["count"] == 1


def test_bad_limit_422(client):
    assert client.get("/regions", params={"limit": 0}).status_code == 422


def test_region_not_found_404(client):
    assert client.get("/regions/99999").status_code == 404


def test_sido_list(client):
    r = client.get("/sido")
    assert r.json()["items"] == ["부산광역시", "서울특별시"]


def test_missing_data_file_503(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOT_DATA_PATH", str(tmp_path / "없는파일.csv"))
    r = TestClient(app).get("/regions")
    assert r.status_code == 503


def test_missing_column_500(monkeypatch, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("code,sido\n11110,서울특별시\n", encoding="utf-8")
    monkeypatch.setenv("GEOT_DATA_PATH", str(bad))
    r = TestClient(app).get("/regions")
    assert r.status_code == 500
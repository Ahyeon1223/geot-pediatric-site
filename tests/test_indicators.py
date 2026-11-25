"""
보조 지표(계획서 한계 대응방안) 테스트

실행 (프로젝트 맨 위 폴더에서):
    python -m pytest tests

손계산 (테스트용 3개 시군구, 종로구와 중구는 맞닿음, 울릉군은 섬):
    종로구  의원 4, 의사 6, 소아 8,870  → 기본 4 / 8,870 × 10,000 = 4.51
                                          의사 6 / 8,870 × 10,000 = 6.76
                                          인접 (4+4) / (8,870+7,428) × 10,000 = 4.91
    중구    의원 4, 이름 확장 5, 소아 7,428 → 확장 5 / 7,428 × 10,000 = 6.73
    울릉군  맞닿은 곳 없음 → 인접 지표 = 기본 지표
"""
import pytest
from fastapi.testclient import TestClient

from src.api import app

REGIONS_CSV = """code,sido,sigungu,child_pop,clinic_count,recent_3yr_openings,clinic_count_broad,doctor_count,population_month,hira_collected
11110,서울특별시,종로구,8870,4,0,4,6,202610,20261122
11140,서울특별시,중구,7428,4,0,5,4,202610,20261122
47940,경상북도,울릉군,438,0,0,0,0,202610,20261122
"""

CLINICS_CSV = """region_code,clinic_name,address,longitude,latitude,opened_date,doctor_count
11110,가소아과의원,서울,127,37,20170301,2
11110,나소아과의원,서울,127,37,20240105,1
11110,다소아과의원,서울,127,37,20241130,2
11110,라소아과의원,서울,127,37,19990818,1
"""

ADJACENCY_CSV = """code,sido,sigungu,neighbor_count,neighbor_codes,neighbor_names
11110,서울특별시,종로구,1,11140,중구
11140,서울특별시,중구,1,11110,종로구
47940,경상북도,울릉군,0,,
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "regions.csv").write_text(REGIONS_CSV, encoding="utf-8")
    (tmp_path / "clinics.csv").write_text(CLINICS_CSV, encoding="utf-8")
    (tmp_path / "adjacency.csv").write_text(ADJACENCY_CSV, encoding="utf-8")
    monkeypatch.setenv("GEOT_DATA_PATH", str(tmp_path / "regions.csv"))
    monkeypatch.setenv("GEOT_ADJACENCY_PATH", str(tmp_path / "adjacency.csv"))
    return TestClient(app)


def test_basic_indicator_unchanged(client):
    # 보조 열이 붙어도 기본 지표는 그대로 (완료 기준: 종로구 4.51)
    assert client.get("/regions/11110").json()["clinics_per_10k"] == 4.51


def test_doctor_indicator(client):
    assert client.get("/regions/11110").json()["doctors_per_10k"] == 6.76


def test_broad_name_indicator(client):
    assert client.get("/regions/11140").json()["clinics_per_10k_broad"] == 6.73


def test_neighbor_indicator(client):
    r = client.get("/regions/11110").json()
    assert r["neighbor_codes"] == ["11140"]
    assert r["area_clinic_count"] == 8
    assert r["area_child_pop"] == 16298
    assert r["clinics_per_10k_with_neighbors"] == 4.91
    assert [n["sigungu"] for n in r["neighbors"]] == ["중구"]


def test_island_uses_own_value(client):
    r = client.get("/regions/47940").json()
    assert r["neighbor_codes"] == []
    assert r["clinics_per_10k_with_neighbors"] == r["clinics_per_10k"] == 0.0


def test_openings_by_year(client):
    years = {y["year"]: y["count"] for y in client.get("/regions/11110").json()["openings_by_year"]}
    assert list(years)[0] == 2017 and list(years)[-1] == 2026
    assert years[2017] == 1 and years[2024] == 2 and years[2025] == 0
    assert sum(years.values()) == 3  # 1999년 개설은 최근 10년 밖


def test_meta_shows_data_dates(client):
    m = client.get("/meta").json()
    assert m["population_month"] == "202610"
    assert m["hira_collected"] == "20261122"


def test_default_ranking_order_unchanged(client):
    # 기본 순위는 여전히 clinics_per_10k 기준 (울릉군은 1,000명 미만이라 기본 제외)
    codes = [i["code"] for i in client.get("/regions").json()["items"]]
    assert codes == ["11110", "11140"]


def test_summary_extra_totals(client):
    s = client.get("/summary").json()
    assert s["clinic_total"] == 8
    assert s["clinic_total_broad"] == 9
    assert s["doctor_total"] == 10
    assert s["no_boundary_names"] == []


def test_region_detail_lists_clinics(client):
    clinics = client.get("/regions/11110").json()["clinics"]
    assert len(clinics) == 4
    assert clinics[0]["clinic_name"] == "다소아과의원"  # 개설일 최근 순
    assert clinics[0]["doctor_count"] == 2

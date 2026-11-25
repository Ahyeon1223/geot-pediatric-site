"""
곁 화면 (Streamlit) — 화면 담당 파일

서버(src/api.py)가 내보내는 JSON을 받아 두 화면을 그립니다.
    메인   : 01 관심 지역 고르기 → 02 개원 후보지 순위 (+ 지역별 지표 비교)
    상세   : 03 시군구 하나의 의원 목록·위치·연도별 개원 수

실행 (프로젝트 맨 위 폴더에서):
    streamlit run app.py
서버를 따로 켜 두면(uvicorn src.api:app) 서버에서 받고,
서버가 꺼져 있으면 같은 계산 함수(src/api.py)를 직접 불러 씁니다.
그래서 인증키도 서버도 없이 data/sample/ 의 서울 샘플만으로 실행됩니다.
"""
import os
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import requests
import streamlit as st
from fastapi import HTTPException

from src import api

API_URL = os.getenv("GEOT_API_URL", "http://127.0.0.1:8000").rstrip("/")
MIN_CHILD_POP = api.MIN_CHILD_POP_DEFAULT

st.set_page_config(page_title="곁 · 소아청소년과 개원 입지 찾기", page_icon="🏥", layout="wide")


# ---------------------------------------------------------------------------
# 데이터 받기: 서버 → (꺼져 있으면) 같은 함수 직접 호출
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def server_is_up() -> bool:
    try:
        return requests.get(f"{API_URL}/health", timeout=1).ok
    except requests.RequestException:
        return False


def call_directly(path: str, params: dict):
    if path == "/meta":
        return api.meta()
    if path == "/summary":
        return api.summary()
    if path == "/sido":
        return api.list_sido()
    if path == "/regions":
        return api.list_regions(
            sido=params.get("sido"),
            min_child_pop=params.get("min_child_pop", MIN_CHILD_POP),
            sort=params.get("sort", "asc"),
            limit=params.get("limit", 20),
        )
    if path.startswith("/regions/"):
        return api.get_region(path.removeprefix("/regions/"))
    raise ValueError(path)


@st.cache_data(ttl=60, show_spinner=False)
def fetch(path: str, **params):
    """(결과, 오류 문구). 오류가 없으면 오류 문구는 None"""
    if server_is_up():
        try:
            r = requests.get(f"{API_URL}{path}", params=params, timeout=5)
        except requests.RequestException as e:
            return None, f"서버에 연결하지 못했습니다: {e}"
        if r.ok:
            return r.json(), None
        return None, r.json().get("detail", f"서버 오류 {r.status_code}")
    try:
        return call_directly(path, params), None
    except HTTPException as e:
        return None, e.detail


def fmt_int(value) -> str:
    return "-" if value is None or pd.isna(value) else f"{int(value):,}"


def fmt_rate(value) -> str:
    return "계산 불가" if value is None or pd.isna(value) else f"{value:.2f}"


def fmt_date(value) -> str:
    text = "" if value is None else str(value).removesuffix(".0")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}년 {int(text[4:])}월"
    return text or "미기재"


def sido_average(regions: list[dict]) -> float | None:
    """시도 전체 의원 수 ÷ 시도 전체 소아 인구 × 10,000 (순위에서 뺀 지역 포함)"""
    clinics = sum(r["clinic_count"] or 0 for r in regions)
    pop = sum(r["child_pop"] or 0 for r in regions)
    return round(clinics / pop * 10000, 2) if pop else None


# ---------------------------------------------------------------------------
# 모양 (디자인 시안 screen-main.png / screen-detail.png 색)
# ---------------------------------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap');
:root { --bg:#FBF6EF; --card:#FFFDF9; --line:#EFE4D6; --mint:#BFE6DC; --mint2:#D7F0E9;
        --teal:#2E9C8A; --ink:#1E4D47; --muted:#6B7A77; --warm:#FFF6E0; --warmline:#F1D9A6; --brown:#8A5A12; }
html, body, [class*="css"], .stApp, .stMarkdown, button, input, select { font-family:'Noto Sans KR', sans-serif; }
.stApp { background: var(--bg); }
.block-container { padding-top: 1.6rem; max-width: 1280px; }
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3, h4 { color: var(--ink); }

.g-top { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:18px; }
.g-brand { display:flex; align-items:center; gap:12px; }
.g-logo { width:52px; height:52px; border-radius:14px; background:var(--teal); display:flex; align-items:center; justify-content:center; }
.g-name { font-size:34px; font-weight:900; color:var(--ink); line-height:1; }
.g-sub { font-size:14px; color:var(--muted); margin-top:4px; }
.g-badge { background:var(--mint2); color:var(--ink); border-radius:999px; padding:10px 18px; font-size:14px; }
.g-badge b { font-weight:700; }

.g-hero { position:relative; overflow:hidden; border-radius:16px; background:var(--mint); padding:34px 40px 70px; margin-bottom:22px; }
.g-hero h1 { font-size:30px; font-weight:900; margin:0 0 6px; padding:0; color:var(--ink); }
.g-hero p { margin:0; color:#2F4F4A; font-size:15px; }
.g-hero .num { color:var(--teal); font-weight:900; font-size:42px; margin-right:14px; }
.g-hero svg.wave { position:absolute; left:0; right:0; bottom:0; width:100%; height:80px; }

.g-card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px 22px; margin-bottom:16px; }
.g-card h3 { font-size:19px; font-weight:700; margin:0 0 10px; padding:0; }
.g-card .step { color:var(--teal); font-weight:900; font-size:30px; margin-right:8px; opacity:.75; }
.g-card p { color:#3E4F4C; font-size:14px; margin:4px 0; line-height:1.6; }
.g-note { color:var(--muted); font-size:12px; font-family:ui-monospace, monospace; margin-top:8px; }
.g-warn { background:var(--warm); border:1px solid var(--warmline); }
.g-warn h3, .g-warn p { color:var(--brown); }

.g-kpi { min-height:118px; display:flex; gap:14px; align-items:center; }
.g-kpi .ic { width:52px; height:52px; border-radius:12px; background:var(--mint2); display:flex; align-items:center; justify-content:center; flex:none; }
.g-kpi .lb { color:var(--muted); font-size:13px; }
.g-kpi .v { color:var(--ink); font-size:28px; font-weight:900; line-height:1.2; }
.g-kpi .s { color:var(--muted); font-size:12px; }

.g-table { width:100%; border-collapse:collapse; font-size:15px; }
.g-table th { white-space:nowrap; background:var(--mint); color:var(--ink); font-weight:500; text-align:left; padding:10px 12px; }
.g-table th:first-child { border-radius:8px 0 0 8px; } .g-table th:last-child { border-radius:0 8px 8px 0; }
.g-table td { white-space:nowrap; padding:10px 12px; border-bottom:1px solid var(--line); color:#2F3E3B; }
.g-table td.r, .g-table th.r { text-align:right; }
.g-table td.rank { color:var(--teal); font-weight:900; font-size:18px; }
.g-table td.val { font-weight:700; color:var(--ink); }
.g-table a.g-btn { display:inline-block; border:1px solid var(--teal); color:var(--teal); background:#fff; border-radius:8px;
  padding:6px 14px; font-weight:700; font-size:14px; text-decoration:none; white-space:nowrap; }
.g-table a.g-btn:hover { background:var(--mint2); }
.g-chip { display:inline-block; border:1px solid var(--line); background:#fff; border-radius:999px; padding:5px 12px; margin:3px 4px 3px 0; font-size:13px; color:#3E4F4C; }
.g-foot { color:var(--muted); font-size:13px; margin-top:20px; }

div[data-testid="stButton"] button { border:1px solid var(--teal); color:var(--teal); background:#fff; border-radius:8px; font-weight:700; }
div[data-testid="stButton"] button:hover { background:var(--mint2); color:var(--ink); border-color:var(--teal); }
div[data-testid="stVerticalBlockBorderWrapper"] { background:var(--card); border-color:var(--line) !important; border-radius:14px; }
</style>
"""

ICONS = {
    "logo": '<svg width="30" height="30" viewBox="0 0 24 24" fill="none"><circle cx="8" cy="7" r="3.2" fill="#fff"/><path d="M2.5 19c0-3.3 2.5-6 5.5-6s5.5 2.7 5.5 6z" fill="#fff"/><circle cx="16.5" cy="10" r="2.4" fill="#F7C9A9"/><path d="M12.5 19c0-2.6 1.8-4.6 4-4.6s4 2 4 4.6z" fill="#F7C9A9"/></svg>',
    "pin": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E4D47" stroke-width="2"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
    "clinic": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E4D47" stroke-width="2"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M12 8v6M9 11h6M10 21v-3h4v3"/></svg>',
    "people": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E4D47" stroke-width="2"><circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.3"/><path d="M16 14.2c2.8.3 5 2.6 5 5.8"/></svg>',
    "pulse": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E4D47" stroke-width="2"><path d="M3 12h4l2-5 4 10 2-5h6"/></svg>',
    "cal": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E4D47" stroke-width="2"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
}

WAVE = """<svg class="wave" viewBox="0 0 1200 80" preserveAspectRatio="none">
<path d="M0 40 C 300 10, 600 60, 1200 20 L1200 80 L0 80Z" fill="#A6DCCD"/>
<path d="M0 55 C 350 35, 750 75, 1200 45 L1200 80 L0 80Z" fill="#7FCBB8"/>
<path d="M0 70 C 400 58, 800 82, 1200 66 L1200 80 L0 80Z" fill="#52B39E"/></svg>"""

HOSPITAL = """<svg style="position:absolute;right:120px;top:22px" width="150" height="150" viewBox="0 0 150 150">
<circle cx="250" cy="20" r="0"/><rect x="60" y="0" width="26" height="26" rx="5" fill="#E0574A"/>
<path d="M73 5v16M65 13h16" stroke="#fff" stroke-width="5"/><rect x="71" y="26" width="4" height="14" fill="#C9B8A0"/>
<rect x="18" y="40" width="110" height="7" rx="2" fill="#2E8C7C"/><rect x="24" y="47" width="98" height="90" fill="#fff"/>
<g fill="#A9D3F0"><rect x="34" y="56" width="12" height="8"/><rect x="54" y="56" width="12" height="8"/><rect x="80" y="56" width="12" height="8"/><rect x="100" y="56" width="12" height="8"/>
<rect x="34" y="74" width="12" height="8"/><rect x="54" y="74" width="12" height="8"/><rect x="80" y="74" width="12" height="8"/><rect x="100" y="74" width="12" height="8"/></g>
<rect x="50" y="92" width="46" height="10" rx="2" fill="#FCE3DC"/><text x="73" y="100" font-size="7" text-anchor="middle" fill="#C0392B" font-family="Noto Sans KR">소아청소년과</text>
<rect x="62" y="112" width="22" height="25" fill="#7FB8E0"/></svg>"""


def html(text: str):
    st.markdown(text, unsafe_allow_html=True)


def kpi(icon: str, label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="s">{sub}</div>' if sub else ""
    return (f'<div class="g-card g-kpi"><div class="ic">{ICONS[icon]}</div>'
            f'<div><div class="lb">{label}</div><div class="v">{value}</div>{sub_html}</div></div>')


def header(meta: dict | None):
    if meta:
        badge = (f'데이터 기준일 · 인구 <b>{fmt_date(meta.get("population_month"))}</b>'
                 f' · 의원 수집 <b>{fmt_date(meta.get("hira_collected"))}</b>')
    else:
        badge = "데이터 기준일 확인 불가"
    html(f"""<div class="g-top"><div class="g-brand"><div class="g-logo">{ICONS['logo']}</div>
<div><div class="g-name">곁</div><div class="g-sub">소아청소년과 개원 입지 찾기</div></div></div>
<div class="g-badge">{badge}</div></div>""")


def footer(meta: dict | None):
    source = "서버(API)에서 받음" if server_is_up() else "서버 없이 데이터 파일을 직접 읽음"
    file_name = (meta or {}).get("data_file", "-")
    html(f'<div class="g-foot">출처: 건강보험심사평가원 병원정보서비스, 행정안전부 주민등록 연령별 인구(KOSIS)'
         f'<br>데이터 파일: {file_name} · {source}</div>')


def go_main():
    st.query_params.pop("code", None)


# ---------------------------------------------------------------------------
# 메인 화면
# ---------------------------------------------------------------------------


def main_page(meta):
    html(f"""<div class="g-hero">{HOSPITAL}<h1>아이 수에 비해 소아과가 부족한 곳을 찾아요</h1>
<p>시군구별 소아(0~14세) 1만 명당 소아청소년과 의원 수를 비교해 개원 후보지를 좁혀 드려요.</p>{WAVE}</div>""")

    sido_list, err = fetch("/sido")
    if err:
        st.error(err)
        return
    sido_list = sido_list["items"]

    left, right = st.columns([1.05, 3], gap="medium")
    with left:
        with st.container(border=True):
            html('<h3 style="margin:0;font-size:20px;white-space:nowrap"><span style="color:#2E9C8A;opacity:.75;font-weight:900;font-size:28px;margin-right:6px">01</span>관심 지역 고르기</h3>')
            chosen = st.query_params.get("sido", "서울특별시")
            default = sido_list.index(chosen) if chosen in sido_list else 0
            sido = st.selectbox("시도", sido_list, index=default)
            st.query_params["sido"] = sido
            top_n = st.slider("몇 위까지 볼까요", 5, 50, 10, step=5)
            html('<div class="g-note">데이터: /sido, /regions</div>')
        html("""<div class="g-card"><h3>지표 설명</h3>
<p><b>소아 1만 명당 소아청소년과 의원 수</b><br>= 의원 수 ÷ 0~14세 인구 × 10,000</p>
<p style="color:#6B7A77">낮을수록 아이 수에 비해 소아과가 부족한 지역이에요.</p></div>""")
        html(f"""<div class="g-card g-warn"><h3>예외 처리 (요건정의서)</h3>
<p>소아 인구 {MIN_CHILD_POP:,}명 미만 지역은 순위에서 빼고 따로 표시해요.</p>
<p>인구가 0이거나 매핑에 실패한 지역은 "데이터 없음"으로 표시해요.</p></div>""")

    all_regions, err = fetch("/regions", sido=sido, min_child_pop=0, limit=300)
    if err:
        right.error(err)
        return
    all_items = all_regions["items"]
    ranked = [r for r in all_items if (r["child_pop"] or 0) >= MIN_CHILD_POP and r["clinics_per_10k"] is not None]
    excluded = [r for r in all_items if r not in ranked]
    avg = sido_average(all_items)

    with right:
        c1, c2, c3 = st.columns(3)
        c1.markdown(kpi("pin", "시군구 수", f"{len(all_items):,}", f"순위 대상 {len(ranked):,}곳"), unsafe_allow_html=True)
        c2.markdown(kpi("clinic", "소아청소년과 의원",
                        f"{sum(r['clinic_count'] or 0 for r in all_items):,}곳"), unsafe_allow_html=True)
        c3.markdown(kpi("people", "시도 평균 (1만 명당)", fmt_rate(avg), "시도 전체 의원 ÷ 시도 전체 소아"),
                    unsafe_allow_html=True)

        table_col, chart_col = st.columns([1.7, 1], gap="medium")
        with table_col, st.container(border=True):
            html('<h3 style="margin:0 0 8px"><span style="color:#2E9C8A;opacity:.75;font-weight:900;font-size:30px;margin-right:8px">02</span>'
                 '개원 후보지 순위 <span style="font-size:14px;font-weight:400;color:#6B7A77">1만 명당 의원 수가 낮은 순</span></h3>')
            ranking_table(ranked[:top_n], sido)
            html("<div class=\"g-note\">데이터: 심평원 의원 수(yadmNm에 '소아') + KOSIS 0~14세 인구</div>")
            with st.expander(f"순위에서 뺀 지역 {len(excluded)}곳 · 소아 인구 {MIN_CHILD_POP:,}명 미만 또는 데이터 없음"):
                if excluded:
                    chips = "".join(
                        f'<span class="g-chip">{r["sigungu"]} · '
                        + (f'소아 {fmt_int(r["child_pop"])}명' if r["child_pop"] else "데이터 없음") + "</span>"
                        for r in excluded)
                    html(chips)
                else:
                    st.caption("이 시도에는 뺀 지역이 없어요.")
        with chart_col, st.container(border=True):
            html('<h3 style="margin:0 0 4px">지역별 지표 비교</h3>')
            st.caption("막대가 짧을수록(색이 진할수록) 소아과가 부족해요. 점선은 시도 평균.")
            indicator_chart(ranked, avg)


def ranking_table(rows: list[dict], sido: str):
    if not rows:
        st.info("순위에 넣을 지역이 없어요.")
        return
    body = "".join(
        f'<tr><td class="rank">{i}</td><td>{r["sigungu"]}</td><td class="r">{fmt_int(r["child_pop"])}</td>'
        f'<td class="r">{fmt_int(r["clinic_count"])}</td><td class="r val">{fmt_rate(r["clinics_per_10k"])}</td>'
        f'<td class="r"><a class="g-btn" target="_self" href="?{urlencode({"sido": sido, "code": r["code"]})}">상세 보기 →</a></td></tr>'
        for i, r in enumerate(rows, start=1))
    html(f'''<table class="g-table"><tr><th>순위</th><th>시군구</th><th class="r">0~14세 인구</th>
<th class="r">의원 수</th><th class="r">1만 명당</th><th></th></tr>{body}</table>''')


def indicator_chart(ranked: list[dict], avg: float | None):
    if not ranked:
        st.info("표시할 지역이 없어요.")
        return
    df = pd.DataFrame(ranked)[["sigungu", "clinics_per_10k"]]
    height = max(260, 24 * len(df))
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("clinics_per_10k:Q", title="소아 1만 명당 의원 수"),
        y=alt.Y("sigungu:N", sort="x", title=None, axis=alt.Axis(labelOverlap=False, labelFontSize=12)),
        color=alt.Color("clinics_per_10k:Q", scale=alt.Scale(range=["#1E6E62", "#CDEDE4"]), legend=None),
        tooltip=[alt.Tooltip("sigungu:N", title="시군구"), alt.Tooltip("clinics_per_10k:Q", title="1만 명당")],
    )
    layers = [bars]
    if avg is not None:
        layers.append(alt.Chart(pd.DataFrame({"avg": [avg]})).mark_rule(strokeDash=[4, 4], color="#C0392B")
                      .encode(x="avg:Q"))
    chart = alt.layer(*layers).properties(height=height).configure_view(stroke=None).configure(
        font="Noto Sans KR", background="transparent")
    st.altair_chart(chart, width="stretch")


# ---------------------------------------------------------------------------
# 상세 화면
# ---------------------------------------------------------------------------


def detail_page(code: str, meta):
    st.button("‹ 후보지 순위로 돌아가기", on_click=go_main)
    d, err = fetch(f"/regions/{code}")
    if err:
        st.error(err)
        return

    html(f"""<div class="g-hero" style="padding-bottom:64px"><span class="num" style="color:#2E9C8A;font-weight:900;font-size:42px;margin-right:14px;vertical-align:middle">03</span>
<span style="display:inline-block;vertical-align:middle"><h1 style="margin:0">{d['sido']} {d['sigungu']}</h1>
<p>개원 후보지 상세</p></span>{WAVE}</div>""")

    same_sido, _ = fetch("/regions", sido=d["sido"], min_child_pop=0, limit=300)
    items = same_sido["items"] if same_sido else []
    avg = sido_average(items)
    ranked = [r for r in items if (r["child_pop"] or 0) >= MIN_CHILD_POP and r["clinics_per_10k"] is not None]
    rank = next((i for i, r in enumerate(ranked, start=1) if r["code"] == code), None)

    rate = d["clinics_per_10k"]
    if rate is None or avg is None:
        compare = "시도 평균과 비교할 수 없음"
    else:
        word = "낮음" if rate < avg else ("높음" if rate > avg else "같음")
        compare = f"시도 평균 {avg:.2f}보다 {word}"
    if rank:
        compare += f" · 시도 내 {rank}위/{len(ranked)}"
    elif (d["child_pop"] or 0) < MIN_CHILD_POP:
        compare += f" · 소아 {MIN_CHILD_POP:,}명 미만이라 순위 제외"

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(kpi("people", "0~14세 인구", f"{fmt_int(d['child_pop'])}명"), unsafe_allow_html=True)
    c2.markdown(kpi("clinic", "소아청소년과 의원", f"{fmt_int(d['clinic_count'])}곳"), unsafe_allow_html=True)
    c3.markdown(kpi("pulse", "1만 명당 의원 수", fmt_rate(rate), compare), unsafe_allow_html=True)
    c4.markdown(kpi("cal", "최근 3년 개원", f"{fmt_int(d.get('recent_3yr_openings'))}곳"), unsafe_allow_html=True)

    clinics = pd.DataFrame(d.get("clinics") or [])
    map_col, list_col = st.columns([1.35, 1], gap="medium")
    with map_col, st.container(border=True):
        html('<h3 style="margin:0 0 8px">기존 소아청소년과 위치</h3>')
        if {"latitude", "longitude"} <= set(clinics.columns) and clinics[["latitude", "longitude"]].notna().any().all():
            st.map(clinics.dropna(subset=["latitude", "longitude"]), latitude="latitude", longitude="longitude",
                   color="#2E9C8A", size=40, height=420)
        else:
            st.info("좌표가 있는 의원이 없어요.")
        html('<div class="g-note">데이터: XPos·YPos (좌표)</div>')
    with list_col, st.container(border=True):
        html(f'<h3 style="margin:0 0 8px">의원 목록 <span style="font-size:14px;font-weight:400;color:#6B7A77">{len(clinics)}곳 · 개설일 최근 순</span></h3>')
        if clinics.empty:
            st.info("이 지역에는 이름에 '소아'가 들어간 의원이 없어요.")
        else:
            has_doctor = "doctor_count" in clinics
            rows = "".join(
                f'<tr><td>{c["clinic_name"]}</td><td>{fmt_date(c["opened_date"])}</td>'
                f'<td class="r">{fmt_int(c["doctor_count"]) if has_doctor else "-"}</td></tr>'
                for _, c in clinics.iterrows())
            html(f'<div style="max-height:420px;overflow-y:auto"><table class="g-table"><tr><th>의원명</th><th>개설일</th>'
                 f'<th class="r">의사 수</th></tr>{rows}</table></div>')
            if not has_doctor:
                st.caption("이 데이터 파일에는 의사 수(drTotCnt) 열이 없어 '-'로 표시합니다.")
        html('<div class="g-note">데이터: yadmNm, estbDd, drTotCnt</div>')

    with st.container(border=True):
        html('<h3 style="margin:0 0 4px">연도별 개원 수 <span style="font-size:14px;font-weight:400;color:#6B7A77">최근 10년 · 경쟁이 늘고 있는지 확인</span></h3>')
        years = d.get("openings_by_year")
        if years:
            ydf = pd.DataFrame(years)
            ydf["year"] = ydf["year"].astype(str)
            chart = alt.Chart(ydf).mark_bar(color="#2E9C8A", cornerRadiusEnd=3).encode(
                x=alt.X("year:N", title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("count:Q", title="개원 수", axis=alt.Axis(tickMinStep=1)),
                tooltip=[alt.Tooltip("year:N", title="연도"), alt.Tooltip("count:Q", title="개원 수")],
            ).properties(height=240).configure_view(stroke=None).configure(font="Noto Sans KR", background="transparent")
            st.altair_chart(chart, width="stretch")
            st.caption(meta["notes"]["openings_by_year"] if meta else "")
        else:
            st.info("개설일 자료(clinics.csv)나 인구 기준월이 없어 그릴 수 없어요.")
        html('<div class="g-note">데이터: estbDd (개설일)</div>')

    with st.expander("보조 지표 (계획서 한계 대응방안) — 기본 순위는 바꾸지 않아요"):
        notes = (meta or {}).get("notes", {})
        neighbors = d.get("neighbors") or []
        rows = [
            ("맞닿은 시군구까지 합친 1만 명당", fmt_rate(d.get("clinics_per_10k_with_neighbors")),
             notes.get("clinics_per_10k_with_neighbors", "")),
            ("소아 1만 명당 의사 수", fmt_rate(d.get("doctors_per_10k")), notes.get("doctors_per_10k", "")),
            ("어린이·아동·키즈 이름 포함 1만 명당", fmt_rate(d.get("clinics_per_10k_broad")),
             notes.get("clinics_per_10k_broad", "")),
        ]
        body = "".join(f'<tr><td>{a}</td><td class="r val">{b}</td><td style="color:#6B7A77;font-size:13px">{c}</td></tr>'
                       for a, b, c in rows)
        html(f'<table class="g-table"><tr><th>지표</th><th class="r">값</th><th>설명</th></tr>{body}</table>')
        if neighbors:
            st.caption("맞닿은 시군구: " + ", ".join(
                f'{n["sigungu"]}({fmt_rate(n["clinics_per_10k"])})' for n in neighbors)
                + " · 샘플 데이터에서는 같은 파일 안에 있는 시군구만 합쳐져요.")
        st.caption("'계산 불가'는 데이터 파일에 그 열이 없다는 뜻이에요. clean.py 결과(data/processed)로 실행하면 채워져요.")


# ---------------------------------------------------------------------------

html(CSS)
meta, meta_err = fetch("/meta")
header(meta)
if meta_err:
    st.error(meta_err)
    st.stop()

code = st.query_params.get("code")
if code:
    detail_page(code, meta)
else:
    main_page(meta)
footer(meta)

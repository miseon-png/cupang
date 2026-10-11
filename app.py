import streamlit as st
import pandas as pd
import math
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from streamlit_gsheets import GSheetsConnection

# 웹페이지 기본 설정
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 및 거래명세서 시스템", layout="wide")

# metric 및 스타일 CSS (글자 짤림 방지 및 강조 박스)
st.markdown("""
<style>
div[data-testid="stMetricValue"] {
    font-size: 1.8rem !important;
}
.today-work-box {
    background-color: #fff0f0;
    border-left: 6px solid #e60000;
    border-radius: 8px;
    padding: 15px 20px;
    margin-bottom: 20px;
    color: #990000;
}
.today-work-title {
    font-size: 1.2rem;
    font-weight: bold;
    margin-bottom: 8px;
}
.today-work-content {
    font-size: 1.05rem;
    line-height: 1.6;
}
</style>
""", unsafe_allow_html=True)

st.title("📦 거래처별 발주 및 거래명세서 관리 시스템")
st.write("발주 데이터를 입력하고 확인서 조회 및 거래명세서 자동 발행/인쇄를 이용할 수 있습니다.")

st.divider()

# 내부 고정 계산 기준 값
carrot2_box_unit = 6       # 당근2 (6개/박스)
spinach_box_unit = 5       # 시금치 (5개/박스)
carrot4_box_unit = 8       # 당근4 (8개/박스)
coupang_exp_days = 4       # 쿠팡 소비기한 (+4일)

# 한국 표준시(KST: UTC+9) 기준 날짜 구하기 함수
def get_kst_now():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)

# 구글 시트 연결
conn = st.connection("gsheets", type=GSheetsConnection)

def load_data(worksheet_name):
    try:
        df = conn.read(worksheet=worksheet_name, ttl=0)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [str(c).strip() for c in df.columns]
        
        if "날짜" in df.columns:
            df["날짜"] = df["날짜"].astype(str).str.strip()
            df = df[~df["날짜"].isin(["", "nan", "None", "NaT"])]
            
        return df.dropna(how="all")
    except Exception as e:
        if "Quota exceeded" in str(e) or "429" in str(e):
            st.warning("⚠️ 구글 시트 요청 한도가 초과되었습니다. 약 1분 후 자동으로 다시 불러옵니다.")
        else:
            st.error(f"[{worksheet_name}] 시트 읽기 오류: {e}")
        return pd.DataFrame()

def parse_date_str(date_val):
    if pd.isna(date_val) or str(date_val).strip() in ["", "nan", "NaT", "None"]:
        return ""
    try:
        dt = pd.to_datetime(date_val, errors='coerce')
        if pd.isna(dt):
            return str(date_val).strip().split(" ")[0].replace(".", "-").replace("/", "-")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(date_val).strip()

def calc_exp_date(date_val, days):
    try:
        d_str = parse_date_str(date_val)
        if not d_str:
            return ""
        dt = pd.to_datetime(d_str)
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""

# 스윗밸런스 전용 소비기한 계산 함수 (9/22 이전: +3일, 9/23 이후: +4일)
def calc_sweet_exp_date(date_val):
    try:
        d_str = parse_date_str(date_val)
        if not d_str:
            return ""
        dt = pd.to_datetime(d_str)
        cutoff_date = pd.to_datetime("2026-09-22")
        
        if dt <= cutoff_date:
            days = 3
        else:
            days = 4
            
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""

def get_month_code(month):
    codes = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F', 
             7: 'G', 8: 'H', 9: 'I', 10: 'J', 11: 'K', 12: 'L'}
    return codes.get(month, '')

# 비표 계산
def calc_bipyo(row):
    try:
        in_spinach = row.get("인천 시금치", 0)
        bu_spinach = row.get("부천 시금치", 0)
        if (in_spinach + bu_spinach) <= 0:
            return ""
        date_val = row.get("날짜", "")
        d_str = parse_date_str(date_val)
        if not d_str:
            return ""
        dt = pd.to_datetime(d_str)
        prod_dt = dt - timedelta(days=1)
        return f"{get_month_code(prod_dt.month)}{prod_dt.day}"
    except Exception:
        return ""

# 쿠팡 소비기한 계산
def calc_coupang_exp(row, exp_days):
    try:
        in_c2 = row.get("인천 당근2", 0)
        bu_c2 = row.get("부천 당근2", 0)
        in_c4 = row.get("인천 당근4", 0)
        bu_c4 = row.get("부천 당근4", 0)
        if (in_c2 + bu_c2 + in_c4 + bu_c4) <= 0:
            return ""
        date_val = row.get("날짜", "")
        return calc_exp_date(date_val, exp_days)
    except Exception:
        return ""

def calculate_coupang(df, c2_unit, s_unit, c4_unit, exp_days):
    empty_cols = ["날짜", "인천 당근2", "부천 당근2", "인천 시금치", "부천 시금치", "인천 당근4", "부천 당근4", "당근2 합계", "시금치 합계", "당근4 합계", "소비기한", "비표", "인천 박스수량", "부천 박스수량"]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    
    res_df = df.copy()

    # 호환성 처리 (이전 구글시트 컬럼명이 "인천 당근" / "인천 400g" 일 수 있으므로 매핑)
    rename_map = {
        "인천 당근": "인천 당근2", "부천 당근": "부천 당근2",
        "인천 400g": "인천 당근4", "부천 400g": "부천 당근4"
    }
    res_df = res_df.rename(columns=rename_map)

    num_cols = ["인천 당근2", "부천 당근2", "인천 시금치", "부천 시금치", "인천 당근4", "부천 당근4"]
    for col in num_cols:
        if col in res_df.columns:
            res_df[col] = pd.to_numeric(res_df[col], errors='coerce').fillna(0).astype(int)
        else:
            res_df[col] = 0

    if "날짜" in res_df.columns:
        res_df["날짜"] = res_df["날짜"].apply(parse_date_str)
        res_df = res_df[res_df["날짜"] != ""]
        if not res_df.empty:
            res_df = res_df.groupby("날짜", as_index=False)[num_cols].sum()

    if res_df.empty:
        return pd.DataFrame(columns=empty_cols)

    res_df["당근2 합계"] = res_df["인천 당근2"] + res_df["부천 당근2"]
    res_df["시금치 합계"] = res_df["인천 시금치"] + res_df["부천 시금치"]
    res_df["당근4 합계"] = res_df["인천 당근4"] + res_df["부천 당근4"]

    res_df["소비기한"] = res_df.apply(lambda r: calc_coupang_exp(r, exp_days), axis=1)
    res_df["비표"] = res_df.apply(calc_bipyo, axis=1)

    # 박스 수량 계산 (당근2/6 + 시금치/5 + 당근4/8 올림 합산)
    res_df["인천 박스수량"] = res_df.apply(lambda r: math.ceil(r["인천 당근2"] / c2_unit) + math.ceil(r["인천 시금치"] / s_unit) + math.ceil(r["인천 당근4"] / c4_unit), axis=1)
    res_df["부천 박스수량"] = res_df.apply(lambda r: math.ceil(r["부천 당근2"] / c2_unit) + math.ceil(r["부천 시금치"] / s_unit) + math.ceil(r["부천 당근4"] / c4_unit), axis=1)

    cols = [c for c in empty_cols if c in res_df.columns]
    return res_df[cols]

def highlight_next_day(row):
    kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    date_val = str(row.get("날짜", "")).strip()
    if date_val == kst_tomorrow_str:
        return ['background-color: #ffcccc; color: #990000; font-weight: bold;'] * len(row)
    return [''] * len(row)

def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='최종집계결과')
    return output.getvalue()

# 기본 설정값 관리 함수 (세션 스테이트 초기화)
def init_settings():
    if "supplier_info" not in st.session_state:
        st.session_state.supplier_info = {
            "name": "농업회사법인 주식회사 팜360닷에이아이 익산지점",
            "owner": "RHEE INJONG",
            "biz_no": "125-85-69135",
            "addr": "전북특별자치도 익산시 왕궁면 푸드폴리스로 10길 22",
            "biz_type": "제조업",
            "biz_item": "식품 제조업"
        }
    if "buyer_sweet" not in st.session_state:
        st.session_state.buyer_sweet = {
            "name": "(주)스윗밸런스랩",
            "owner": "이운성",
            "biz_no": "397-85-00686",
            "addr": "경기도 성남시 중원구 둔촌대로388번길 20",
            "biz_type": "제조업",
            "biz_item": "식품가공"
        }
    if "buyer_coupang" not in st.session_state:
        st.session_state.buyer_coupang = {
            "name": "쿠팡 풀필먼트서비스(유)",
            "owner": "강한승 외",
            "biz_no": "120-88-00000",
            "addr": "서울특별시 송파구 송파대로 570",
            "biz_type": "도소매업",
            "biz_item": "전자상거래업"
        }
    if "unit_prices" not in st.session_state:
        st.session_state.unit_prices = {
            "carrot2": 1500,
            "spinach": 2000,
            "carrot4": 3000,
            "brunch_mix": 4720
        }

init_settings()

# 6개 탭 구성
tab_c_input, tab_s_input, tab_coupang, tab_sweet, tab_invoice, tab_config = st.tabs([
    "🚀 쿠팡 입력", 
    "🥗 스윗밸런스 입력", 
    "📊 쿠팡 확인서", 
    "📊 스윗밸런스 확인서",
    "📑 거래명세서 발행",
    "⚙️ 설정 (기초정보 & 단가)"
])


# --- [TAB 1: 쿠팡 입력] ---
with tab_c_input:
    st.subheader("🚀 쿠팡 발주 수량 입력")
    
    kst_today = get_kst_now().date()
    c_date = st.date_input("발주 날짜 선택", kst_today, key="c_date_input")
    c_date_str = c_date.strftime("%Y-%m-%d")
    c_exp_preview = (c_date + timedelta(days=coupang_exp_days)).strftime("%Y-%m-%d")
    
    prod_c_date = c_date - timedelta(days=1)
    c_bipyo_preview = f"{get_month_code(prod_c_date.month)}{prod_c_date.day}"
    
    st.caption(f"💡 자동 산출 - 소비기한(+4일): **{c_exp_preview} (당근2/당근4 포함 시)** | 예상 비표(전날 생산 기준): **{c_bipyo_preview} (시금치 포함 시)**")
    st.markdown("---")
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📍 인천센터")
        c_in_c2 = st.number_input("인천 당근2 수량", min_value=0, value=0, step=1, key="c_in_c2")
        c_in_s = st.number_input("인천 시금치 수량", min_value=0, value=0, step=1, key="c_in_s")
        c_in_c4 = st.number_input("인천 당근4 수량", min_value=0, value=0, step=1, key="c_in_c4")
    with c2:
        st.markdown("##### 📍 부천센터")
        c_bu_c2 = st.number_input("부천 당근2 수량", min_value=0, value=0, step=1, key="c_bu_c2")
        c_bu_s = st.number_input("부천 시금치 수량", min_value=0, value=0, step=1, key="c_bu_s")
        c_bu_c4 = st.number_input("부천 당근4 수량", min_value=0, value=0, step=1, key="c_bu_c4")

    st.markdown("---")
    if st.button("🚀 쿠팡 발주 저장하기", type="primary", use_container_width=True):
        total_qty = c_in_c2 + c_bu_c2 + c_in_s + c_bu_s + c_in_c4 + c_bu_c4
        if total_qty >= 0:
            try:
                existing_df = load_data("쿠팡")
                new_row = pd.DataFrame([{
                    "날짜": c_date_str,
                    "인천 당근2": c_in_c2,
                    "부천 당근2": c_bu_c2,
                    "인천 시금치": c_in_s,
                    "부천 시금치": c_bu_s,
                    "인천 당근4": c_in_c4,
                    "부천 당근4": c_bu_c4
                }])
                updated_df = pd.concat([existing_df, new_row], ignore_index=True)
                conn.update(worksheet="쿠팡", data=updated_df)
                st.cache_data.clear()
                st.success(f"[{c_date_str}] 쿠팡 발주가 구글 시트에 성공적으로 저장되었습니다!")
            except Exception as err:
                st.error(f"저장 중 오류 발생: {err}")


# --- [TAB 2: 스윗밸런스 입력] ---
with tab_s_input:
    st.subheader("🥗 스윗밸런스 발주 수량 및 퀵비 입력")
    
    kst_today = get_kst_now().date()
    s_date = st.date_input("발주 날짜 선택", kst_today, key="s_date_input")
    s_date_str = s_date.strftime("%Y-%m-%d")
    
    s_exp_preview = calc_sweet_exp_date(s_date_str)
    s_days_applied = 3 if s_date <= datetime.strptime("2026-09-22", "%Y-%m-%d").date() else 4
    
    st.caption(f"💡 자동으로 산출되는 소비기한(+{s_days_applied}일 적용): **{s_exp_preview}**")
    st.markdown("---")
    
    col_s1, col_s2, col_s3 = st.columns([2.5, 2, 2])
    with col_s1:
        s_item_name = st.text_input("품목명", value="브런치빈 샐러드믹스 1KG", disabled=True, key="s_item")
    with col_s2:
        s

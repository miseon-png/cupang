import streamlit as st
import pandas as pd
import math
import time
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from streamlit_gsheets import GSheetsConnection

# 웹페이지 기본 설정
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 및 거래명세서 시스템", layout="wide")

# metric 및 스타일 CSS (글자 짤림 방지, 강조 박스 등)
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

# 내부 고정 계산 기준 값
carrot200_box_unit = 6      # 당근 200 (6개/박스)
carrot400_box_unit = 8      # 당근 400 (8개/박스)
spinach_box_unit = 5        # 시금치 (5개/박스)
coupang_exp_days = 4        # 쿠팡 소비기한 (+4일)

# 한국 표준시(KST: UTC+9) 기준 날짜 구하기 함수
def get_kst_now():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)

# 파일명에서 발주 날짜 추출 함수 (예: 1004 -> 2026-10-04, 1017 -> 2026-10-17)
def extract_date_from_filename(filename):
    if not filename:
        return ""
    # 1. YYYY-MM-DD 또는 YYYYMMDD 형태 검색
    match = re.search(r'(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})', filename)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    # 2. MMDD 형태 검색 (예: 1004, 1017) -> 현재 연도 적용
    match = re.search(r'(?<!\d)(\d{2})(\d{2})(?!\d)', filename)
    if match:
        month, day = match.groups()
        current_year = get_kst_now().year
        return f"{current_year}-{month}-{day}"
    return ""

# 스윗밸런스 품목명 표준화(매핑) 함수 - 충돌 방지 로직
def normalize_sweet_item_name(item_name):
    item = str(item_name).strip()
    if not item or item in ["nan", "None", ""]:
        return "-"
    if "베이직샐러드믹스" in item or "브런치빈" in item:
        return "브런치빈 샐러드믹스 1KG"
    elif "크라이" in item:
        return "크라이 컷레터스 1KG"
    return item

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
        in_c200 = row.get("인천 당근 200", 0)
        bu_c200 = row.get("부천 당근 200", 0)
        in_c400 = row.get("인천 당근 400", 0)
        bu_c400 = row.get("부천 당근 400", 0)
        if (in_c200 + bu_c200 + in_c400 + bu_c400) <= 0:
            return ""
        date_val = row.get("날짜", "")
        return calc_exp_date(date_val, exp_days)
    except Exception:
        return ""

def calculate_coupang(df, c200_unit, c400_unit, s_unit, exp_days):
    empty_cols = ["날짜", "인천 당근 200", "부천 당근 200", "인천 당근 400", "부천 당근 400", "인천 시금치", "부천 시금치", "당근 200 합계", "당근 400 합계", "시금치 합계", "소비기한", "비표", "인천 박스수량", "부천 박스수량"]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    
    res_df = df.copy()

    rename_map = {
        "인천 당근": "인천 당근 200", "부천 당근": "부천 당근 200",
        "인천 당근2": "인천 당근 200", "부천 당근2": "부천 당근 200",
        "인천 400g": "인천 당근 400", "부천 400g": "부천 당근 400",
        "인천 당근4": "인천 당근 400", "부천 당근4": "부천 당근 400"
    }
    res_df = res_df.rename(columns=rename_map)

    if res_df.columns.duplicated().any():
        res_df = res_df.groupby(level=0, axis=1).first()

    num_cols = ["인천 당근 200", "부천 당근 200", "인천 당근 400", "부천 당근 400", "인천 시금치", "부천 시금치"]
    for col in num_cols:
        if col in res_df.columns:
            series_target = res_df[col]
            if isinstance(series_target, pd.DataFrame):
                series_target = series_target.iloc[:, 0]
            res_df[col] = pd.to_numeric(series_target, errors='coerce').fillna(0).astype(int)
        else:
            res_df[col] = 0

    if "날짜" in res_df.columns:
        date_target = res_df["날짜"]
        if isinstance(date_target, pd.DataFrame):
            date_target = date_target.iloc[:, 0]
        res_df["날짜"] = date_target.apply(parse_date_str)
        res_df = res_df[res_df["날짜"] != ""]
        if not res_df.empty:
            res_df = res_df.groupby("날짜", as_index=False)[num_cols].sum()

    if res_df.empty:
        return pd.DataFrame(columns=empty_cols)

    res_df["당근 200 합계"] = res_df["인천 당근 200"] + res_df["부천 당근 200"]
    res_df["당근 400 합계"] = res_df["인천 당근 400"] + res_df["부천 당근 400"]
    res_df["시금치 합계"] = res_df["인천 시금치"] + res_df["부천 시금치"]

    res_df["소비기한"] = res_df.apply(lambda r: calc_coupang_exp(r, exp_days), axis=1)
    res_df["비표"] = res_df.apply(calc_bipyo, axis=1)

    res_df["인천 박스수량"] = res_df.apply(lambda r: math.ceil(r["인천 당근 200"] / c200_unit) + math.ceil(r["인천 당근 400"] / c400_unit) + math.ceil(r["인천 시금치"] / s_unit), axis=1)
    res_df["부천 박스수량"] = res_df.apply(lambda r: math.ceil(r["부천 당근 200"] / c200_unit) + math.ceil(r["부천 당근 400"] / c400_unit) + math.ceil(r["부천 시금치"] / s_unit), axis=1)

    cols = [c for c in empty_cols if c in res_df.columns]
    return res_df[cols]

# 스윗밸런스 지점별/품목별 박스 수량 산출 함수
def calc_sweet_boxes(qty):
    try:
        q = int(qty)
    except Exception:
        q = 0
    full_boxes = q // 5
    rem = q % 5
    mid_boxes = 1 if rem in [3, 4] else 0
    small_boxes = 1 if rem in [1, 2] else 0
    total_labels = full_boxes + mid_boxes + small_boxes
    return pd.Series([full_boxes, mid_boxes, small_boxes, total_labels], 
                     index=['중박스(Full)', '중박스(3~4개)', '소박스(1~2개)', '라벨 수량'])

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
            "carrot200": 1500,
            "carrot400": 3000,
            "spinach": 2000,
            "브런치빈 샐러드믹스 1KG": 4720,
            "크라이 컷레터스 1KG": 5605
        }

init_settings()


# ==========================================
# --- [좌측 사이드바: 상위 메뉴 구성] ---
# ==========================================
with st.sidebar:
    st.title("📦 메뉴 관리")
    st.markdown("---")
    
    selected_menu = st.radio(
        "이동할 메뉴를 선택하세요",
        ["🚀 쿠팡 관리", "🥗 스윗밸런스 관리", "📑 거래명세서 발행", "⚙️ 설정 (기초정보 & 단가)"],
        index=0
    )
    
    st.markdown("---")
    st.caption("💡 거래처별 발주 입력, 엑셀 업로드, 박스 및 센터 관리, 거래명세서를 통합 관리합니다.")


# ==========================================
# --- [메인 화면 1: 쿠팡 관리] ---
# ==========================================
if selected_menu == "🚀 쿠팡 관리":
    st.title("🚀 쿠팡 발주 관리")
    st.write("쿠팡 센터별 발주 수량 입력 및 확인서를 조회합니다.")
    st.divider()
    
    c_sub_tab1, c_sub_tab2 = st.tabs(["✍️ 발주 수량 입력", "📊 발주 확인서"])
    
    # [쿠팡 - 1. 발주 수량 입력]
    with c_sub_tab1:
        st.subheader("✍️ 쿠팡 발주 수량 입력")
        
        kst_today = get_kst_now().date()
        c_date = st.date_input("발주 날짜 선택", kst_today, key="c_date_input")
        c_date_str = c_date.strftime("%Y-%m-%d")
        c_exp_preview = (c_date + timedelta(days=coupang_exp_days)).strftime("%Y-%m-%d")
        
        prod_c_date = c_date - timedelta(days=1)
        c_bipyo_preview = f"{get_month_code(prod_c_date.month)}{prod_c_date.day}"
        
        st.caption(f"💡 자동 산출 - 소비기한(+4일): **{c_exp_preview} (당근 200/당근 400 포함 시)** | 예상 비표(전날 생산 기준): **{c_bipyo_preview} (시금치 포함 시)**")
        st.markdown("---")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 📍 인천센터")
            c_in_c200 = st.number_input("인천 당근 200 수량", min_value=0, value=0, step=1, key="c_in_c200")
            c_in_c400 = st.number_input("인천 당근 400 수량", min_value=0, value=0, step=1, key="c_in_c400")
            c_in_s = st.number_input("인천 시금치 수량", min_value=0, value=0, step=1, key="c_

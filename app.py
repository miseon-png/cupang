import streamlit as st
import pandas as pd
import math
import time
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from streamlit_gsheets import GSheetsConnection
import gspread
from google.oauth2.service_account import Credentials

# 웹페이지 기본 설정
st.set_page_config(page_title="발주 및 거래명세서 시스템", layout="wide")

# =========================================================
# 💡 [설정] 외포장 공정일지 표시 여부 플래그
# False: 사이드바 및 스윗밸런스 외포장일지 블라인드(숨김) / True: 다시 표시
# =========================================================
SHOW_SIDEBAR_OUTER_LOG = False
SHOW_SWEET_OUTER_LOG = False  # 💡 스윗밸런스 외포장일지 블라인드 플래그

# metric 및 스타일 CSS
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
/* 2단 병합 헤더 테이블 스타일 */
.custom-table-container {
    width: 100%;
    overflow-x: auto;
    margin-bottom: 1rem;
    border: 1px solid #ddd;
    border-radius: 4px;
}
.custom-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
    text-align: center;
}
.custom-table th, .custom-table td {
    border: 1px solid #e0e0e0;
    padding: 8px 6px;
    white-space: nowrap;
}
.custom-table th {
    background-color: #f7f9fa;
    font-weight: 600;
    color: #333;
}
.custom-table tr:hover {
    background-color: #f5f8ff;
}
.custom-table .highlight-row {
    background-color: #ffcccc !important;
    color: #990000 !important;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# =========================================================
# 📦 거래처별 전용 품목 박스 입수 단위 및 기준 설정
# =========================================================
# 1. 쿠팡 전용 품목 박스 단위 (곰곰믹스 8개입 추가)
coupang_box_units = {
    "당근 200": 6,   # 6개/박스
    "당근 400": 8,   # 8개/박스
    "시금치": 5,      # 5개/박스
    "곰곰믹스": 8     # 8개/박스 (카톤박스 대)
}
coupang_exp_days = 4  # 쿠팡 소비기한 (+4일)

# 2. 오아시스 전용 품목 박스 단위
oasis_box_units = {
    "채소믹스 500": 5,
    "양적 500": 5,
    "컷레터스 500": 5,
    "유럽믹스 200": 6
}

# 한국 표준시(KST: UTC+9) 기준 날짜 구하기 함수
def get_kst_now():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)

# ==========================================
# 🌟 수불부(Carrot 앱) 자동 전송 설정
# ==========================================
SUBUL_SPREADSHEET_ID = "1vfDcssJtoq79GGirW4aBcpXN-0_NjhVOkJbG-I609PY"

def send_to_subul_inventory(date_str, box_name, qty, reason):
    if qty <= 0: return False
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        credentials = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        client = gspread.authorize(credentials)
        doc = client.open_by_key(SUBUL_SPREADSHEET_ID)
        sheet = doc.worksheet("수기출고")
        
        row = [date_str, box_name, int(qty), "개", "발주 앱 자동 연동", reason]
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"수불부 전송 중 오류 발생: {e}")
        return False

# 중복 제거 텍스트 연결 함수
def join_unique_notes(series):
    unique_vals = list(dict.fromkeys([str(v).strip() for v in series if str(v).strip() and str(v).strip() != "nan"]))
    return ", ".join(unique_vals)

# 파일명에서 발주 날짜 추출 함수
def extract_date_from_filename(filename):
    if not filename:
        return ""
    match = re.search(r'(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})', filename)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    match = re.search(r'(?<!\d)(\d{2})(\d{2})(?!\d)', filename)
    if match:
        month, day = match.groups()
        current_year = get_kst_now().year
        return f"{current_year}-{month}-{day}"
    return ""

# 스윗밸런스 품목명 표준화(매핑) 함수
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

def calc_sweet_exp_date(date_val):
    try:
        d_str = parse_date_str(date_val)
        if not d_str:
            return ""
        dt = pd.to_datetime(d_str)
        cutoff_date = pd.to_datetime("2026-09-22")
        days = 3 if dt <= cutoff_date else 4
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""

def get_month_code(month):
    codes = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F', 
             7: 'G', 8: 'H', 9: 'I', 10: 'J', 11: 'K', 12: 'L'}
    return codes.get(month, '')

def calc_bipyo(row):
    try:
        s_total = row.get("시금치 합계", 0)
        if s_total <= 0:
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

def calc_coupang_exp(row, exp_days):
    try:
        c200_tot = row.get("당근 200 합계", 0)
        c400_tot = row.get("당근 400 합계", 0)
        gom_tot = row.get("곰곰믹스 합계", 0)
        if (c200_tot + c400_tot + gom_tot) <= 0:
            return ""
        date_val = row.get("날짜", "")
        return calc_exp_date(date_val, exp_days)
    except Exception:
        return ""

# 1) 쿠팡 전용 계산 함수
def calculate_coupang(df, box_units, exp_days):
    empty_cols = [
        "날짜", 
        "인천 당근 200", "부천 당근 200", 
        "인천 당근 400", "부천 당근 400", 
        "인천 시금치", "부천 시금치", 
        "인천 곰곰믹스", "부천 곰곰믹스",
        "당근 200 합계", "당근 400 합계", "시금치 합계", "곰곰믹스 합계", 
        "소비기한", "비표", "인천 박스수량", "부천 박스수량"
    ]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    
    res_df = df.copy()
    rename_map = {
        "인천 당근": "인천 당근 200", "부천 당근": "부천 당근 200",
        "인천 당근2": "인천 당근 200", "부천 당근2": "부천 당근 200",
        "인천 400g": "인천 당근 400", "부천 당근 400": "부천 당근 400",
        "인천 당근4": "인천 당근 400", "부천 당근4": "부천 당근 400"
    }
    res_df = res_df.rename(columns=rename_map)

    if res_df.columns.duplicated().any():
        res_df = res_df.groupby(level=0, axis=1).first()

    num_cols = [
        "인천 당근 200", "부천 당근 200", 
        "인천 당근 400", "부천 당근 400", 
        "인천 시금치", "부천 시금치",
        "인천 곰곰믹스", "부천 곰곰믹스"
    ]
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
    res_df["곰곰믹스 합계"] = res_df["인천 곰곰믹스"] + res_df["부천 곰곰믹스"]

    res_df["소비기한"] = res_df.apply(lambda r: calc_coupang_exp(r, exp_days), axis=1)
    res_df["비표"] = res_df.apply(calc_bipyo, axis=1)

    c200_u = box_units.get("당근 200", 6)
    c400_u = box_units.get("당근 400", 8)
    s_u = box_units.get("시금치", 5)
    gom_u = box_units.get("곰곰믹스", 8)

    res_df["인천 박스수량"] = res_df.apply(
        lambda r: math.ceil(r["인천 당근 200"] / c200_u) + 
                  math.ceil(r["인천 당근 400"] / c400_u) + 
                  math.ceil(r["인천 시금치"] / s_u) +
                  math.ceil(r["인천 곰곰믹스"] / gom_u), 
        axis=1
    )
    res_df["부천 박스수량"] = res_df.apply(
        lambda r: math.ceil(r["부천 당근 200"] / c200_u) + 
                  math.ceil(r["부천 당근 400"] / c400_u) + 
                  math.ceil(r["부천 시금치"] / s_u) +
                  math.ceil(r["부천 곰곰믹스"] / gom_u), 
        axis=1
    )

    cols = [c for c in empty_cols if c in res_df.columns]
    return res_df[cols]

# 2단 병합 헤더 HTML 렌더링 함수
def render_coupang_two_tier_table(df):
    if df.empty:
        st.info("조회할 쿠팡 데이터가 없습니다.")
        return

    kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")

    html = """
    <div class="custom-table-container">
        <table class="custom-table">
            <thead>
                <tr>
                    <th rowspan="2">날짜</th>
                    <th colspan="2">당근 200</th>
                    <th colspan="2">당근 400</th>
                    <th colspan="2">시금치</th>
                    <th colspan="2">곰곰믹스</th>
                    <th colspan="4">합계</th>
                    <th rowspan="2">소비기한</th>
                    <th rowspan="2">비표</th>
                    <th colspan="2">박스수량</th>
                </tr>
                <tr>
                    <th>인천</th><th>부천</th>
                    <th>인천</th><th>부천</th>
                    <th>인천</th><th>부천</th>
                    <th>인천</th><th>부천</th>
                    <th>당근 200</th><th>당근 400</th><th>시금치</th><th>곰곰믹스</th>
                    <th>인천</th><th>부천</th>
                </tr>
            </thead>
            <tbody>
    """
    for _, r in df.iterrows():
        is_highlight = str(r.get("날짜", "")).strip() == kst_tomorrow_str
        tr_class = ' class="highlight-row"' if is_highlight else ''

        html += f"""
            <tr{tr_class}>
                <td>{r.get('날짜', '')}</td>
                <td>{int(r.get('인천 당근 200', 0)):,}</td>
                <td>{int(r.get('부천 당근 200', 0)):,}</td>
                <td>{int(r.get('인천 당근 400', 0)):,}</td>
                <td>{int(r.get('부천 당근 400', 0)):,}</td>
                <td>{int(r.get('인천 시금치', 0)):,}</td>
                <td>{int(r.get('부천 시금치', 0)):,}</td>
                <td>{int(r.get('인천 곰곰믹스', 0)):,}</td>
                <td>{int(r.get('부천 곰곰믹스', 0)):,}</td>
                <td style="font-weight:bold;">{int(r.get('당근 200 합계', 0)):,}</td>
                <td style="font-weight:bold;">{int(r.get('당근 400 합계', 0)):,}</td>
                <td style="font-weight:bold;">{int(r.get('시금치 합계', 0)):,}</td>
                <td style="font-weight:bold;">{int(r.get('곰곰믹스 합계', 0)):,}</td>
                <td>{r.get('소비기한', '') or '-'}</td>
                <td>{r.get('비표', '') or '-'}</td>
                <td style="font-weight:bold;">{int(r.get('인천 박스수량', 0)):,}</td>
                <td style="font-weight:bold;">{int(r.get('부천 박스수량', 0)):,}</td>
            </tr>
        """
    html += """
            </tbody>
        </table>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

# 2) 오아시스 전용 계산 함수
def calculate_oasis(df):
    oasis_cols = ["채소믹스 500", "양적 500", "컷레터스 500", "유럽믹스 200"]
    empty_cols = ["날짜"] + oasis_cols + ["총 발주 수량", "총 박스수량"]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    
    res_df = df.copy()
    for col in oasis_cols:
        if col in res_df.columns:
            s_target = res_df[col]
            if isinstance(s_target, pd.DataFrame):
                s_target = s_target.iloc[:, 0]
            res_df[col] = pd.to_numeric(s_target, errors='coerce').fillna(0).astype(int)
        else:
            res_df[col] = 0

    if "날짜" in res_df.columns:
        date_target = res_df["날짜"]
        if isinstance(date_target, pd.DataFrame):
            date_target = date_target.iloc[:, 0]
        res_df["날짜"] = date_target.apply(parse_date_str)
        res_df = res_df[res_df["날짜"] != ""]
        if not res_df.empty:
            res_df = res_df.groupby("날짜", as_index=False)[oasis_cols].sum()

    if res_df.empty:
        return pd.DataFrame(columns=empty_cols)

    res_df["총 발주 수량"] = res_df[oasis_cols].sum(axis=1)
    
    res_df["총 박스수량"] = res_df.apply(
        lambda r: sum(math.ceil(r[item] / oasis_box_units.get(item, 5)) for item in oasis_cols),
        axis=1
    )

    return res_df[["날짜"] + oasis_cols + ["총 발주 수량", "총 박스수량"]]

# 세부 박스 계산 함수
def calc_coupang_boxes_detail(qty, pack_unit):
    try:
        q = int(qty)
    except Exception:
        q = 0
    full_boxes = q // pack_unit
    rem = q % pack_unit
    has_partial = 1 if rem > 0 else 0
    total_boxes = full_boxes + has_partial
    return full_boxes, rem, total_boxes

# 스윗밸런스 박스 수량 산출 함수
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

# 스윗밸런스 외포장일지 전용 세부 박스 계산 함수
def calc_sweet_boxes_detail(qty):
    try:
        q = int(qty)
    except Exception:
        q = 0
    full_boxes = q // 5
    rem = q % 5

    mid_4 = 1 if rem == 4 else 0
    mid_3 = 1 if rem == 3 else 0
    small_2 = 1 if rem == 2 else 0
    small_1 = 1 if rem == 1 else 0
    total_labels = full_boxes + mid_4 + mid_3 + small_2 + small_1

    return pd.Series(
        [full_boxes, mid_4, mid_3, small_2, small_1, total_labels], 
        index=['중박스(5개-Full)', '중박스(4개)', '중박스(3개)', '소박스(2개)', '소박스(1개)', '총 박스(라벨) 수']
    )

def highlight_next_day(row):
    kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    date_val = str(row.get("납품일자", row.get("날짜", row.get("작업일자", "")))).strip()
    if date_val == kst_tomorrow_str:
        return ['background-color: #ffcccc; color: #990000; font-weight: bold;'] * len(row)
    return [''] * len(row)

def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='최종집계결과')
    return output.getvalue()

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
    if "unit_prices" not in st.session_state:
        st.session_state.unit_prices = {
            "carrot200": 1500,
            "carrot400": 3000,
            "spinach": 2000,
            "gomgom": 3500,
            "브런치빈 샐러드믹스 1KG": 4720,
            "크라이 컷레터스 1KG": 5605,
            "채소믹스 500": 3500,
            "양적 500": 3500,
            "컷레터스 500": 3800,
            "유럽믹스 200": 2800
        }

init_settings()

# ==========================================
# --- [좌측 사이드바: 상위 메뉴 구성] ---
# ==========================================
with st.sidebar:
    st.title("📦 메뉴 관리")
    st.markdown("---")
    
    menu_list = [
        "🚀 쿠팡 관리", 
        "🏝️ 오아시스 관리", 
        "🛒 자사몰 관리", 
        "🥗 스윗밸런스 관리", 
        "📑 거래명세서 발행 (스윗밸런스)"
    ]
    
    if SHOW_SIDEBAR_OUTER_LOG:
        menu_list.append("📋 외포장 공정일지 (양식)")
        
    menu_list.append("⚙️ 설정 (기초정보 & 단가)")
    
    selected_menu = st.radio("이동할 메뉴를 선택하세요", menu_list, index=0)
    st.markdown("---")
    st.caption("💡 거래처별 발주 입력, 외포장 작업일지, 거래명세서를 통합 관리합니다.")


# ==========================================
# --- [메인 화면 1: 쿠팡 관리] ---
# ==========================================
if selected_menu == "🚀 쿠팡 관리":
    st.title("🚀 쿠팡 발주 관리")
    st.write("쿠팡 센터별 발주 수량 입력, 발주 확인서 및 전체 통합 외포장일지를 관리합니다.")
    st.divider()
    
    c_sub_tab1, c_sub_tab2, c_sub_tab3 = st.tabs(["✍️ 발주 수량 입력", "📊 발주 확인서 (부자재전송)", "📋 외포장일지"])
    
    with c_sub_tab1:
        st.subheader("✍️ 쿠팡 발주 수량 입력")
        kst_today = get_kst_now().date()
        c_date = st.date_input("발주 날짜 선택", kst_today, key="c_date_input")
        c_date_str = c_date.strftime("%Y-%m-%d")
        c_exp_preview = (c_date + timedelta(days=coupang_exp_days)).strftime("%Y-%m-%d")
        
        prod_c_date = c_date - timedelta(days=1)
        c_bipyo_preview = f"{get_month_code(prod_c_date.month)}{prod_c_date.day}"
        
        st.caption(f"💡 자동 산출 - 소비기한(+4일): **{c_exp_preview}** | 예상 비표: **{c_bipyo_preview}**")
        st.markdown("---")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 📍 인천센터")
            c_in_c200 = st.number_input("인천 당근 200 수량", min_value=0, value=0, step=1, key="c_in_c200")
            c_in_c400 = st.number_input("인천 당근 400 수량", min_value=0, value=0, step=1, key="c_in_c400")
            c_in_s = st.number_input("인천 시금치 수량", min_value=0, value=0, step=1, key="c_in_s")
            c_in_gom = st.number_input("인천 곰곰믹스 수량", min_value=0, value=0, step=1, key="c_in_gom")
        with c2:
            st.markdown("##### 📍 부천센터")
            c_bu_c200 = st.number_input("부천 당근 200 수량", min_value=0, value=0, step=1, key="c_bu_c200")
            c_bu_c400 = st.number_input("부천 당근 400 수량", min_value=0, value=0, step=1, key="c_bu_c400")
            c_bu_s = st.number_input("부천 시금치 수량", min_value=0, value=0, step=1, key="c_bu_s")
            c_bu_gom = st.number_input("부천 곰곰믹스 수량", min_value=0, value=0, step=1, key="c_bu_gom")

        st.markdown("---")
        if st.button("🚀 쿠팡 발주 저장하기", type="primary", use_container_width=True):
            total_qty = c_in_c200 + c_bu_c200 + c_in_c400 + c_bu_c400 + c_in_s + c_bu_s + c_in_gom + c_bu_gom
            if total_qty >= 0:
                try:
                    existing_df = load_data("쿠팡")
                    new_row = pd.DataFrame([{
                        "날짜": c_date_str,
                        "인천 당근 200": c_in_c200,
                        "부천 당근 200": c_bu_c200,
                        "인천 당근 400": c_in_c400,
                        "부천 당근 400": c_bu_c400,
                        "인천 시금치": c_in_s,
                        "부천 시금치": c_bu_s,
                        "인천 곰곰믹스": c_in_gom,
                        "부천 곰곰믹스": c_bu_gom
                    }])
                    updated_df = pd.concat([existing_df, new_row], ignore_index=True)
                    conn.update(worksheet="쿠팡", data=updated_df)
                    st.cache_data.clear()
                    st.success(f"[{c_date_str}] 쿠팡 발주가 구글 시트에 성공적으로 저장되었습니다!")
                except Exception as err:
                    st.error(f"저장 중 오류 발생: {err}")

    with c_sub_tab2:
        col_c_head, col_c_btn = st.columns([4, 1])
        with col_c_head:
            st.subheader("📊 쿠팡 발주 확인서 (부자재전송)")
        with col_c_btn:
            if st.button("🔄 쿠팡 데이터 새로고침", use_container_width=True, key="btn_refresh_c"):
                st.cache_data.clear()
                st.rerun()

        df_c_raw = load_data("쿠팡")
        df_c_all_calc = calculate_coupang(df_c_raw, coupang_box_units, coupang_exp_days)

        kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today_c_work = df_c_all_calc[df_c_all_calc["날짜"] == kst_tomorrow_str] if not df_c_all_calc.empty else pd.DataFrame()

        if not today_c_work.empty:
            row_t = today_c_work.iloc[0]
            in_c200 = row_t.get("인천 당근 200", 0)
            bu_c200 = row_t.get("부천 당근 200", 0)
            in_c400 = row_t.get("인천 당근 400", 0)
            bu_c400 = row_t.get("부천 당근 400", 0)
            in_s = row_t.get("인천 시금치", 0)
            bu_s = row_t.get("부천 시금치", 0)
            in_gom = row_t.get("인천 곰곰믹스", 0)
            bu_gom = row_t.get("부천 곰곰믹스", 0)
            in_box = row_t.get("인천 박스수량", 0)
            bu_box = row_t.get("부천 박스수량", 0)
            bipyo_txt = row_t.get("비표", "") or "없음(시금치 0개)"
            exp_txt = row_t.get("소비기한", "") or "없음"

            st.markdown(f"""
            <div class="today-work-box">
                <div class="today-work-title">🚨 오늘 작업할 쿠팡 발주 내용 (납품 예정일: {kst_tomorrow_str})</div>
                <div class="today-work-content">
                    • <b>인천센터:</b> 당근 200 <b>{in_c200:,}</b>개 / 당근 400 <b>{in_c400:,}</b>개 / 시금치 <b>{in_s:,}</b>개 / 곰곰믹스 <b>{in_gom:,}</b>개 👉 <b>인천 총 {in_box:,} 박스</b><br>
                    • <b>부천센터:</b> 당근 200 <b>{bu_c200:,}</b>개 / 당근 400 <b>{bu_c400:,}</b>개 / 시금치 <b>{bu_s:,}</b>개 / 곰곰믹스 <b>{bu_gom:,}</b>개 👉 <b>부천 총 {bu_box:,} 박스</b><br>
                    • <b>시금치 비표:</b> <span style="font-size:1.15rem; font-weight:bold; color:#cc0000;">{bipyo_txt}</span> | <b>소비기한:</b> {exp_txt}
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info(f"💡 오늘 작업 예정인 쿠팡 발주(납품일: {kst_tomorrow_str}) 내역이 없습니다.")

        st.markdown("---")

        current_month_str = get_kst_now().strftime("%Y-%m")
        valid_months = []
        if not df_c_raw.empty and "날짜" in df_c_raw.columns:
            df_c_raw["정제날짜"] = df_c_raw["날짜"].apply(parse_date_str)
            df_c_raw["연월"] = df_c_raw["정제날짜"].apply(lambda d: d[:7] if len(d) >= 7 else "")
            valid_months = sorted([m for m in df_c_raw["연월"].unique() if m and len(m) == 7], reverse=True)

        default_month_c = current_month_str if current_month_str in valid_months else (valid_months[0] if valid_months else "")
        available_months = [default_month_c] + [m for m in valid_months if m != default_month_c] + ["전체 보기"] if valid_months else ["전체 보기"]

        selected_month = st.selectbox("📅 조회할 월을 선택하세요", available_months, index=0, key="c_month_select")
        
        if not df_c_raw.empty and "연월" in df_c_raw.columns and selected_month != "전체 보기":
            filtered_c_df = df_c_raw[df_c_raw["연월"] == selected_month].drop(columns=["정제날짜", "연월"], errors='ignore')
        else:
            filtered_c_df = df_c_raw.drop(columns=["정제날짜", "연월"], errors='ignore') if "연월" in df_c_raw.columns else df_c_raw

        calculated_c_df = calculate_coupang(filtered_c_df, coupang_box_units, coupang_exp_days)

        st.markdown("##### 📊 최종 집계 및 박스 수량 결과")
        
        # 💡 [핵심] 2단 병합 헤더 테이블 렌더링
        render_coupang_two_tier_table(calculated_c_df)

        excel_data_c = to_excel(calculated_c_df)
        st.download_button(
            label=f"📥 쿠팡 {selected_month} 최종 결과 엑셀 다운로드",
            data=excel_data_c,
            file_name=f"쿠팡_발주확인서_{selected_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        # ---------------------------------------------------------
        # 📦 쿠팡 시금치 & 곰곰믹스 포장재/부재료 전송 전 데이터 미리보기 & 일괄 전송
        # ---------------------------------------------------------
        st.markdown("---")
        st.markdown("##### 📦 수불부(Carrot 앱)로 쿠팡 포장재 및 부재료 월별 일괄 전송")
        st.caption("선택된 월 기간의 실제 발주량에 맞춰 수불부(Carrot 앱의 수기출고 탭)에 등록될 포장재/부재료를 미리 확인한 뒤 전송합니다.")
        
        spinach_box_unit = coupang_box_units.get("시금치", 5)
        spinach_total = int(calculated_c_df["시금치 합계"].sum()) if not calculated_c_df.empty and "시금치 합계" in calculated_c_df else 0
        spinach_boxes = math.ceil(spinach_total / spinach_box_unit) if spinach_total > 0 else 0
        
        gom_box_unit = coupang_box_units.get("곰곰믹스", 8)
        gom_total = int(calculated_c_df["곰곰믹스 합계"].sum()) if not calculated_c_df.empty and "곰곰믹스 합계" in calculated_c_df else 0
        gom_pouch_qty = gom_total * 5
        gom_outer_qty = gom_total * 1
        gom_dressing_qty = gom_total * 5
        gom_boxes = math.ceil(gom_total / gom_box_unit) if gom_total > 0 else 0

        period_label = selected_month if selected_month != "전체 보기" else "전체 기간"

        preview_data = [
            {"구분": "시금치 용기", "자재명(Carrot 앱 등록명)": "SW-002 (용기)", "산출 수량": f"{spinach_total:,} 개", "단위": "개", "출고사유": f"쿠팡 시금치 용기 ({period_label} 일괄)"},
            {"구분": "시금치 뚜껑", "자재명(Carrot 앱 등록명)": "SW-002 (뚜껑)", "산출 수량": f"{spinach_total:,} 개", "단위": "개", "출고사유": f"쿠팡 시금치 뚜껑 ({period_label} 일괄)"},
            {"구분": "시금치 박스", "자재명(Carrot 앱 등록명)": "카톤박스(중)", "산출 수량": f"{spinach_boxes:,} 개", "단위": "개", "출고사유": f"쿠팡 시금치 박스 ({period_label} 일괄)"},
            {"구분": "곰곰믹스 소포장", "자재명(Carrot 앱 등록명)": "파우치 (200*160)", "산출 수량": f"{gom_pouch_qty:,} 개", "단위": "개", "출고사유": f"곰곰믹스 파우치 ({period_label} 일괄)"},
            {"구분": "곰곰믹스 외포장", "자재명(Carrot 앱 등록명)": "곰곰믹스 외포장지", "산출 수량": f"{gom_outer_qty:,} 장", "단위": "장", "출고사유": f"곰곰믹스 외포장지 ({period_label} 일괄)"},
            {"구분": "곰곰믹스 부재료", "자재명(Carrot 앱 등록명)": "오리엔탈 드레싱 25g", "산출 수량": f"{gom_dressing_qty:,} 개", "단위": "개", "출고사유": f"곰곰믹스 드레싱 ({period_label} 일괄)"},
            {"구분": "곰곰믹스 박스", "자재명(Carrot 앱 등록명)": "카톤박스(대)", "산출 수량": f"{gom_boxes:,} 개", "단위": "개", "출고사유": f"곰곰믹스 대박스 ({period_label} 일괄)"}
        ]
        preview_df = pd.DataFrame(preview_data)

        st.markdown("###### 🔍 전송 예정 포장재 및 부재료 목록 미리보기")
        st.dataframe(preview_df, use_container_width=True, hide_index=True)

        st.caption("📊 품목별 발주 요약")
        pm1, pm2, pm3, pm4 = st.columns(4)
        pm1.metric("시금치 총 발주", f"{spinach_total:,} 개")
        pm2.metric("시금치 박스(중)", f"{spinach_boxes:,} 박스")
        pm3.metric("곰곰믹스 총 발주", f"{gom_total:,} 개")
        pm4.metric("곰곰믹스 박스(대)", f"{gom_boxes:,} 박스")

        col_c_send1, col_c_send2 = st.columns([2, 1])
        with col_c_send1:
            send_c_date = st.date_input("수불부 마감 기록 일자 지정 (예: 월말일)", get_kst_now().date(), key="send_c_date")
        with col_c_send2:
            st.write("") 
            st.write("")
            btn_disabled = (spinach_total <= 0 and gom_total <= 0)
            if st.button(f"🚀 {selected_month} 포장재/부재료 일괄 전송하기", type="primary", use_container_width=True, disabled=btn_disabled):
                send_d_str = send_c_date.strftime("%Y-%m-%d")
                success_list = []
                
                if spinach_total > 0:
                    success_list.append(send_to_subul_inventory(send_d_str, "SW-002 (용기)", spinach_total, f"쿠팡 시금치 용기 ({period_label} 일괄)"))
                    success_list.append(send_to_subul_inventory(send_d_str, "SW-002 (뚜껑)", spinach_total, f"쿠팡 시금치 뚜껑 ({period_label} 일괄)"))
                    success_list.append(send_to_subul_inventory(send_d_str, "카톤박스(중)", spinach_boxes, f"쿠팡 시금치 박스 ({period_label} 일괄)"))
                
                if gom_total > 0:
                    success_list.append(send_to_subul_inventory(send_d_str, "파우치 (200*160)", gom_pouch_qty, f"곰곰믹스 파우치 ({period_label} 일괄)"))
                    success_list.append(send_to_subul_inventory(send_d_str, "곰곰믹스 외포장지", gom_outer_qty, f"곰곰믹스 외포장지 ({period_label} 일괄)"))
                    success_list.append(send_to_subul_inventory(send_d_str, "오리엔탈 드레싱 25g", gom_dressing_qty, f"곰곰믹스 드레싱 ({period_label} 일괄)"))
                    success_list.append(send_to_subul_inventory(send_d_str, "카톤박스(대)", gom_boxes, f"곰곰믹스 대박스 ({period_label} 일괄)"))
                
                if all(success_list):
                    st.success(f"✅ [{send_d_str}] 일자로 쿠팡 부자재 및 부재료 전송이 성공적으로 완료되었습니다!")
                else:
                    st.error("일부 자재 전송 중 오류가 발생했습니다. 로그를 확인하세요.")
        
        if spinach_total <= 0 and gom_total <= 0:
            st.info("💡 조회된 기간에 시금치 및 곰곰믹스 발주 내역이 없어 전송할 자재가 없습니다.")

    with c_sub_tab3:
        st.subheader("📋 쿠팡 외포장 작업일지 (전체 통합)")
        df_c_raw_log = load_data("쿠팡")
        df_c_log_calc = calculate_coupang(df_c_raw_log, coupang_box_units, coupang_exp_days)

        if not df_c_log_calc.empty:
            def get_prod_date(date_str):
                try:
                    dt = pd.to_datetime(date_str)
                    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    return ""

            df_c_log_calc["작업일자(생산일)"] = df_c_log_calc["날짜"].apply(get_prod_date)
            valid_months_cp_log = sorted([m[:7] for m in df_c_log_calc["작업일자(생산일)"].unique() if len(m) >= 7], reverse=True)
            current_m_cp = get_kst_now().strftime("%Y-%m")
            def_m_cp = current_m_cp if current_m_cp in valid_months_cp_log else (valid_months_cp_log[0] if valid_months_cp_log else "")
            avail_m_cp = [def_m_cp] + [m for m in valid_months_cp_log if m != def_m_cp] + ["전체 보기"] if valid_months_cp_log else ["전체 보기"]

            sel_m_cp_log = st.selectbox("📅 조회할 작업 월 선택", avail_m_cp, index=0, key="cp_log_month_select")

            if sel_m_cp_log != "전체 보기":
                df_c_log_calc = df_c_log_calc[df_c_log_calc["작업일자(생산일)"].str.startswith(sel_m_cp_log)]

            log_rows = []
            for _, r in df_c_log_calc.iterrows():
                c2_total = r.get("당근 200 합계", 0)
                c4_total = r.get("당근 400 합계", 0)
                s_total = r.get("시금치 합계", 0)
                gom_total = r.get("곰곰믹스 합계", 0)

                c2_full, c2_rem, c2_box = calc_coupang_boxes_detail(c2_total, coupang_box_units["당근 200"])
                c4_full, c4_rem, c4_box = calc_coupang_boxes_detail(c4_total, coupang_box_units["당근 400"])
                s_full, s_rem, s_box = calc_coupang_boxes_detail(s_total, coupang_box_units["시금치"])
                gom_full, gom_rem, gom_box = calc_coupang_boxes_detail(gom_total, coupang_box_units["곰곰믹스"])

                tot_boxes = c2_box + c4_box + s_box + gom_box

                log_rows.append({
                    "작업일자(생산일)": r["작업일자(생산일)"],
                    "납품일자": r["날짜"],
                    "소비기한": r["소비기한"] if r["소비기한"] else "-",
                    "시금치 비표": r["비표"] if r["비표"] else "-",
                    "당근 200g (6개입)": f"{c2_full}박스" + (f" + {c2_rem}개" if c2_rem > 0 else ""),
                    "당근 400g (8개입)": f"{c4_full}박스" + (f" + {c4_rem}개" if c4_rem > 0 else ""),
                    "시금치 (5개입)": f"{s_full}박스" + (f" + {s_rem}개" if s_rem > 0 else ""),
                    "곰곰믹스 (8개입-대)": f"{gom_full}박스" + (f" + {gom_rem}개" if gom_rem > 0 else ""),
                    "총 박스 수량": tot_boxes
                })

            df_cp_log_final = pd.DataFrame(log_rows)

            if not df_cp_log_final.empty:
                st.markdown(f"##### 📦 쿠팡 통합 외포장 작업일지 집계표 ({sel_m_cp_log})")
                st.dataframe(df_cp_log_final.style.apply(highlight_next_day, axis=1), use_container_width=True)

                excel_cp_log = to_excel(df_cp_log_final)
                st.download_button(
                    label="📥 쿠팡 외포장 작업일지 엑셀 다운로드",
                    data=excel_cp_log,
                    file_name=f"쿠팡_외포장일지_{sel_m_cp_log}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.info("조회 조건에 해당하는 데이터가 없습니다.")
        else:
            st.info("쿠팡 발주 데이터가 존재하지 않습니다.")


# ==========================================
# --- [메인 화면 2: 오아시스 관리] ---
# ==========================================
elif selected_menu == "🏝️ 오아시스 관리":
    st.title("🏝️ 오아시스 발주 관리")
    st.write("오아시스 전용 품목(채소믹스 500, 양적 500, 컷레터스 500, 유럽믹스 200) 발주 및 외포장일지를 관리합니다.")
    st.divider()

    o_tab1, o_tab2, o_tab3 = st.tabs(["✍️ 발주 수량 입력", "📊 발주 확인서", "📋 외포장일지"])

    with o_tab1:
        st.subheader("✍️ 오아시스 발주 수량 입력")
        kst_today = get_kst_now().date()
        o_date = st.date_input("발주 날짜 선택", kst_today, key="oasis_date_input")
        o_date_str = o_date.strftime("%Y-%m-%d")

        st.markdown("---")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            o_veg = st.number_input("채소믹스 500 (개)", min_value=0, value=0, step=1, key="o_veg")
        with c2:
            o_yang = st.number_input("양적 500 (개)", min_value=0, value=0, step=1, key="o_yang")
        with c3:
            o_cut = st.number_input("컷레터스 500 (개)", min_value=0, value=0, step=1, key="o_cut")
        with c4:
            o_eur = st.number_input("유럽믹스 200 (개)", min_value=0, value=0, step=1, key="o_eur")

        st.markdown("---")
        if st.button("🚀 오아시스 발주 저장하기", type="primary", use_container_width=True, key="btn_save_oasis"):
            tot = o_veg + o_yang + o_cut + o_eur
            if tot >= 0:
                try:
                    existing_df = load_data("오아시스")
                    new_row = pd.DataFrame([{
                        "날짜": o_date_str,
                        "채소믹스 500": o_veg,
                        "양적 500": o_yang,
                        "컷레터스 500": o_cut,
                        "유럽믹스 200": o_eur
                    }])
                    updated_df = pd.concat([existing_df, new_row], ignore_index=True)
                    conn.update(worksheet="오아시스", data=updated_df)
                    st.cache_data.clear()
                    st.success(f"[{o_date_str}] 오아시스 발주가 구글 시트에 성공적으로 저장되었습니다!")
                except Exception as err:
                    st.error(f"저장 중 오류 발생: {err}")

    with o_tab2:
        col_head, col_btn = st.columns([4, 1])
        with col_head:
            st.subheader("📊 오아시스 발주 확인서")
        with col_btn:
            if st.button("🔄 데이터 새로고침", use_container_width=True, key="btn_refresh_oasis"):
                st.cache_data.clear()
                st.rerun()

        df_oasis_raw = load_data("오아시스")
        df_oasis_calc = calculate_oasis(df_oasis_raw)

        kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today_o_work = df_oasis_calc[df_oasis_calc["날짜"] == kst_tomorrow_str] if not df_oasis_calc.empty else pd.DataFrame()

        if not today_o_work.empty:
            row_t = today_o_work.iloc[0]
            v_veg = row_t.get("채소믹스 500", 0)
            v_yang = row_t.get("양적 500", 0)
            v_cut = row_t.get("컷레터스 500", 0)
            v_eur = row_t.get("유럽믹스 200", 0)
            tot_b = row_t.get("총 박스수량", 0)

            st.markdown(f"""
            <div class="today-work-box">
                <div class="today-work-title">🚨 오늘 작업할 오아시스 발주 내용 (납품 예정일: {kst_tomorrow_str})</div>
                <div class="today-work-content">
                    • <b>품목별 수량:</b> 채소믹스 500 <b>{v_veg:,}</b>개 / 양적 500 <b>{v_yang:,}</b>개 / 컷레터스 500 <b>{v_cut:,}</b>개 / 유럽믹스 200 <b>{v_eur:,}</b>개<br>
                    • <b>총 박스수량:</b> <b>{tot_b:,}</b> 박스
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info(f"💡 오늘 작업 예정인 오아시스 발주(납품일: {kst_tomorrow_str}) 내역이 없습니다.")

        st.markdown("---")

        current_month_str = get_kst_now().strftime("%Y-%m")
        valid_months = []
        if not df_oasis_raw.empty and "날짜" in df_oasis_raw.columns:
            df_oasis_raw["정제날짜"] = df_oasis_raw["날짜"].apply(parse_date_str)
            df_oasis_raw["연월"] = df_oasis_raw["정제날짜"].apply(lambda d: d[:7] if len(d) >= 7 else "")
            valid_months = sorted([m for m in df_oasis_raw["연월"].unique() if m and len(m) == 7], reverse=True)

        default_m = current_month_str if current_month_str in valid_months else (valid_months[0] if valid_months else "")
        avail_m = [default_m] + [m for m in valid_months if m != default_m] + ["전체 보기"] if valid_months else ["전체 보기"]

        sel_m = st.selectbox("📅 조회할 월을 선택하세요", avail_m, index=0, key="oasis_month_select")
        
        if not df_oasis_raw.empty and "연월" in df_oasis_raw.columns and sel_m != "전체 보기":
            filtered_df = df_oasis_raw[df_oasis_raw["연월"] == sel_m].drop(columns=["정제날짜", "연월"], errors='ignore')
        else:
            filtered_df = df_oasis_raw.drop(columns=["정제날짜", "연월"], errors='ignore') if "연월" in df_oasis_raw.columns else df_oasis_raw

        res_calc = calculate_oasis(filtered_df)

        st.markdown(f"##### 📊 오아시스 최종 집계 결과 ({sel_m})")
        st.dataframe(res_calc.style.apply(highlight_next_day, axis=1), use_container_width=True)

        excel_data = to_excel(res_calc)
        st.download_button(
            label=f"📥 오아시스 {sel_m} 발주 확인서 엑셀 다운로드",
            data=excel_data,
            file_name=f"오아시스_발주확인서_{sel_m}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="btn_excel_oasis"
        )

    with o_tab3:
        st.subheader("📋 오아시스 외포장 작업일지")
        df_log_raw = load_data("오아시스")
        df_log_calc = calculate_oasis(df_log_raw)

        if not df_log_calc.empty:
            def get_prod_date(date_str):
                try:
                    dt = pd.to_datetime(date_str)
                    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    return ""

            df_log_calc["작업일자(생산일)"] = df_log_calc["날짜"].apply(get_prod_date)
            valid_months_log = sorted([m[:7] for m in df_log_calc["작업일자(생산일)"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_log else (valid_months_log[0] if valid_months_log else "")
            avail_m = [def_m] + [m for m in valid_months_log if m != def_m] + ["전체 보기"] if valid_months_log else ["전체 보기"]

            sel_m_log = st.selectbox("📅 조회할 작업 월 선택", avail_m, index=0, key="oasis_log_month_select")

            if sel_m_log != "전체 보기":
                df_log_calc = df_log_calc[df_log_calc["작업일자(생산일)"].str.startswith(sel_m_log)]

            log_rows = []
            for _, r in df_log_calc.iterrows():
                q_veg = r.get("채소믹스 500", 0)
                q_yang = r.get("양적 500", 0)
                q_cut = r.get("컷레터스 500", 0)
                q_eur = r.get("유럽믹스 200", 0)

                b_veg_f, b_veg_r, b_veg = calc_coupang_boxes_detail(q_veg, oasis_box_units["채소믹스 500"])
                b_yang_f, b_yang_r, b_yang = calc_coupang_boxes_detail(q_yang, oasis_box_units["양적 500"])
                b_cut_f, b_cut_r, b_cut = calc_coupang_boxes_detail(q_cut, oasis_box_units["컷레터스 500"])
                b_eur_f, b_eur_r, b_eur = calc_coupang_boxes_detail(q_eur, oasis_box_units["유럽믹스 200"])

                tot_boxes = b_veg + b_yang + b_cut + b_eur

                log_rows.append({
                    "작업일자(생산일)": r["작업일자(생산일)"],
                    "납품일자": r["날짜"],
                    "채소믹스 500": f"{b_veg_f}박스" + (f" + {b_veg_r}개" if b_veg_r > 0 else ""),
                    "양적 500": f"{b_yang_f}박스" + (f" + {b_yang_r}개" if b_yang_r > 0 else ""),
                    "컷레터스 500": f"{b_cut_f}박스" + (f" + {b_cut_r}개" if b_cut_r > 0 else ""),
                    "유럽믹스 200": f"{b_eur_f}박스" + (f" + {b_eur_r}개" if b_eur_r > 0 else ""),
                    "총 박스 수량": tot_boxes
                })

            df_log_final = pd.DataFrame(log_rows)

            if not df_log_final.empty:
                st.markdown(f"##### 📦 오아시스 외포장 작업일지 집계표 ({sel_m_log})")
                st.dataframe(df_log_final.style.apply(highlight_next_day, axis=1), use_container_width=True)

                excel_log = to_excel(df_log_final)
                st.download_button(
                    label="📥 오아시스 외포장 작업일지 엑셀 다운로드",
                    data=excel_log,
                    file_name=f"오아시스_외포장일지_{sel_m_log}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="btn_oasis_log_excel"
                )
            else:
                st.info("조회 조건에 해당하는 데이터가 없습니다.")
        else:
            st.info("오아시스 발주 데이터가 존재하지 않습니다.")


# ==========================================
# --- [메인 화면 3: 자사몰 관리 (서브탭 뼈대)] ---
# ==========================================
elif selected_menu == "🛒 자사몰 관리":
    st.title("🛒 자사몰 발주 관리")
    st.write("자사몰 발주 수량 입력, 발주 확인서 및 외포장일지를 관리합니다.")
    st.divider()

    m_tab1, m_tab2, m_tab3 = st.tabs(["✍️ 발주 수량 입력", "📊 발주 확인서", "📋 외포장일지"])

    with m_tab1:
        st.subheader("✍️ 자사몰 발주 수량 입력")
        st.info("💡 자사몰 출고 품목 및 규격이 아직 확정되지 않았습니다. 품목 확정 후 입력 기능이 활성화됩니다.")

    with m_tab2:
        st.subheader("📊 자사몰 발주 확인서")
        st.info("💡 자사몰 출고 품목 및 규격 확정 후 확인서 기능이 오픈됩니다.")

    with m_tab3:
        st.subheader("📋 자사몰 외포장일지")
        st.info("💡 자사몰 품목별 박스 입수 규격 확정 후 외포장일지가 오픈됩니다.")


# ==========================================
# --- [메인 화면 4: 스윗밸런스 관리] ---
# ==========================================
elif selected_menu == "🥗 스윗밸런스 관리":
    st.title("🥗 스윗밸런스 발주, 박스 및 센터 관리")
    st.write("스윗밸런스 엑셀 파일 업로드, 발주 확인서, 박스 수량, 센터별 라벨 관리 기능을 제공합니다.")
    st.divider()
    
    df_s_base = load_data("스윗밸런스")
    if not df_s_base.empty:
        if "날짜" in df_s_base.columns:
            df_s_base["날짜"] = df_s_base["날짜"].apply(parse_date_str)
            df_s_base = df_s_base[df_s_base["날짜"] != ""]
        if "품목" in df_s_base.columns:
            df_s_base["품목"] = df_s_base["품목"].apply(normalize_sweet_item_name)
        if "수량" in df_s_base.columns:
            df_s_base["수량"] = pd.to_numeric(df_s_base["수량"], errors='coerce').fillna(0).astype(int)
        else:
            df_s_base["수량"] = 0
            
        for empty_col in ["센터", "퀵비", "퀵비 비고", "비고"]:
            if empty_col not in df_s_base.columns:
                df_s_base[empty_col] = 0 if empty_col == "퀵비" else ("미지정" if empty_col == "센터" else "")
            elif empty_col == "퀵비":
                df_s_base[empty_col] = pd.to_numeric(df_s_base[empty_col], errors='coerce').fillna(0).astype(int)

    s_tabs = [
        "📁 발주 파일 업로드", 
        "✍️ 직접 입력", 
        "📊 발주 확인서", 
        "📦 박스 관리 (부자재전송)", 
        "🏭 센터 관리"
    ]
    if SHOW_SWEET_OUTER_LOG:
        s_tabs.append("📋 외포장일지")
        
    created_s_tabs = st.tabs(s_tabs)
    s_sub_tab1, s_sub_tab2, s_sub_tab3, s_sub_tab4, s_sub_tab5 = created_s_tabs[:5]
    s_sub_tab6 = created_s_tabs[5] if SHOW_SWEET_OUTER_LOG else None
    
    with s_sub_tab1:
        st.subheader("📁 스윗밸런스 엑셀 발주 파일 업로드")
        uploaded_file = st.file_uploader("발주 엑셀 파일 첨부 (.xlsx)", type=["xlsx", "xls"], key="s_excel_uploader")
        
        if uploaded_file is not None:
            try:
                uploaded_df = pd.read_excel(uploaded_file)
                filename = uploaded_file.name
                extracted_date = extract_date_from_filename(filename)
                
                st.success(f"✅ 엑셀 파일[{filename}]을 정상적으로 읽어왔습니다!")
                
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    if extracted_date:
                        s_order_date_str = st.text_input("📅 파일명 자동 인식 발주/출고 날짜", value=extracted_date, key="u_date_auto")
                    else:
                        kst_today = get_kst_now().date()
                        s_order_date_str = st.date_input("📅 파일명에 날짜가 없음 (직접 선택)", kst_today, key="u_date_manual").strftime("%Y-%m-%d")
                
                req_cols = ['상품 명', '발주수량']
                if all(c in uploaded_df.columns for c in req_cols):
                    has_center = '센터' in uploaded_df.columns
                    parse_cols = (['센터'] if has_center else []) + req_cols
                    parsed_df = uploaded_df[parse_cols].copy()
                    
                    parsed_df['날짜'] = s_order_date_str
                    parsed_df['품목'] = parsed_df['상품 명'].apply(normalize_sweet_item_name)
                    parsed_df['수량'] = pd.to_numeric(parsed_df['발주수량'], errors='coerce').fillna(0).astype(int)
                    parsed_df['센터'] = parsed_df['센터'].astype(str).str.strip() if has_center else "미지정 센터"
                    
                    st.markdown("##### 🔍 업로드 데이터 미리보기")
                    disp_u_cols = ['날짜', '센터', '품목', '수량'] if has_center else ['날짜', '품목', '수량']
                    st.dataframe(parsed_df[disp_u_cols], use_container_width=True)
                    
                    col_u1, col_u2 = st.columns(2)
                    with col_u1:
                        u_quick_fee = st.number_input("해당 엑셀 전체 퀵비 금액 (원)", min_value=0, value=0, step=1000, key="u_quick_fee")
                    with col_u2:
                        u_quick_note = st.text_input("퀵비 비고 / 메모", value="", placeholder="예: 성남 퀵 등", key="u_quick_note")
                    
                    if st.button("💾 구글 시트에 반영 및 저장하기", type="primary", use_container_width=True):
                        existing_s_df = load_data("스윗밸런스")
                        rows_to_save = []
                        for idx, r in parsed_df.iterrows():
                            q_f = u_quick_fee if idx == 0 else 0
                            q_n = u_quick_note if idx == 0 else ""
                            rows_to_save.append({
                                "날짜": r["날짜"], "센터": r["센터"], "품목": r["품목"],
                                "수량": r["수량"], "퀵비": q_f, "퀵비 비고": q_n,
                                "비고": f"엑셀 업로드 ({filename})"
                            })
                            
                        new_upload_df = pd.DataFrame(rows_to_save)
                        updated_s_df = pd.concat([existing_s_df, new_upload_df], ignore_index=True)
                        cols_order = ["날짜", "센터", "품목", "수량", "퀵비", "퀵비 비고", "비고"]
                        updated_s_df = updated_s_df[[c for c in cols_order if c in updated_s_df.columns]]
                        conn.update(worksheet="스윗밸런스", data=updated_s_df)
                        st.cache_data.clear()
                        st.success(f"🎉 [{s_order_date_str}] 스윗밸런스 저장 완료!")
                else:
                    st.warning(f"⚠️ 필수 항목 컬럼이 부족합니다: {req_cols}")
            except Exception as e:
                st.error(f"엑셀 처리 중 오류: {e}")

    with s_sub_tab2:
        st.subheader("✍️ 수동 발주 수량 및 퀵비 입력")
        kst_today = get_kst_now().date()
        s_date = st.date_input("발주 날짜 선택", kst_today, key="s_date_input_m")
        s_date_str = s_date.strftime("%Y-%m-%d")
        
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            s_qty_brunch = st.number_input("브런치빈 샐러드믹스 1KG (개)", min_value=0, value=0, step=1, key="s_qty_brunch")
        with col_s2:
            s_qty_cry = st.number_input("크라이 컷레터스 1KG (개)", min_value=0, value=0, step=1, key="s_qty_cry")

        col_q1, col_q2 = st.columns([2, 3])
        with col_q1:
            s_quick_fee = st.number_input("퀵비 금액(원)", min_value=0, value=0, step=1000, key="s_quick_fee_input")
        with col_q2:
            s_quick_note = st.text_input("퀵비 비고", value="", placeholder="예: 성남 퀵 등", key="s_quick_note_input")

        s_note = st.text_input("발주 비고", value="", placeholder="특이사항", key="s_note_input")

        if st.button("🥗 스윗밸런스 발주 저장하기", type="primary", use_container_width=True):
            if s_qty_brunch + s_qty_cry == 0 and s_quick_fee == 0:
                st.warning("⚠️ 입력된 수량 또는 퀵비가 없습니다.")
            else:
                try:
                    existing_df = load_data("스윗밸런스")
                    rows_to_add = []
                    if s_qty_brunch > 0:
                        rows_to_add.append({"날짜": s_date_str, "센터": "수동 입력", "품목": "브런치빈 샐러드믹스 1KG", "수량": s_qty_brunch, "퀵비": s_quick_fee, "퀵비 비고": s_quick_note, "비고": s_note})
                        s_quick_fee = 0
                    if s_qty_cry > 0:
                        rows_to_add.append({"날짜": s_date_str, "센터": "수동 입력", "품목": "크라이 컷레터스 1KG", "수량": s_qty_cry, "퀵비": s_quick_fee, "퀵비 비고": s_quick_note, "비고": s_note})

                    new_df = pd.DataFrame(rows_to_add)
                    updated_df = pd.concat([existing_df, new_df], ignore_index=True)
                    conn.update(worksheet="스윗밸런스", data=updated_df)
                    st.cache_data.clear()
                    st.success(f"[{s_date_str}] 스윗밸런스 수동 저장 완료!")
                except Exception as err:
                    st.error(f"저장 중 오류: {err}")

    # [스윗밸런스 - 3. 발주 확인서]
    with s_sub_tab3:
        col_s_head, col_s_btn = st.columns([4, 1])
        with col_s_head:
            st.subheader("📊 스윗밸런스 발주 확인서")
        with col_s_btn:
            if st.button("🔄 스윗밸런스 데이터 새로고침", use_container_width=True, key="btn_refresh_s"):
                st.cache_data.clear()
                st.rerun()

        df_s_processed = df_s_base.copy()
        kst_today_str = get_kst_now().strftime("%Y-%m-%d")
        today_s_work = df_s_processed[df_s_processed["날짜"] == kst_today_str] if not df_s_processed.empty else pd.DataFrame()

        if not today_s_work.empty:
            items_summary_str = ""
            today_grouped = today_s_work.groupby("품목", as_index=False)["수량"].sum()
            for _, r in today_grouped.iterrows():
                if r.get('품목') != "-":
                    exp_date_t = calc_sweet_exp_date(kst_today_str)
                    items_summary_str += f"• <b>{r.get('품목', '품목')}:</b> 총 수량 <b>{r.get('수량', 0):,}</b> 개 | 소비기한: {exp_date_t}<br>"
            
            s_w_quick = today_s_work["퀵비"].sum()
            q_notes = [str(n) for n in today_s_work["퀵비 비고"].unique() if str(n).strip()]
            s_w_q_note = ", ".join(q_notes)
            quick_info = f"{s_w_quick:,} 원" + (f" ({s_w_q_note})" if s_w_q_note else "")

            st.markdown(f"""
            <div class="today-work-box">
                <div class="today-work-title">🚨 오늘 발주/출고 스윗밸런스 내역 (출고일: {kst_today_str})</div>
                <div class="today-work-content">
                    {items_summary_str}
                    • <b>총 퀵비:</b> {quick_info}
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info(f"💡 오늘 출고 예정인 스윗밸런스 발주(출고일: {kst_today_str}) 내역이 없습니다.")

        st.markdown("---")
        col_f1, col_f2 = st.columns(2)
        valid_months_s = sorted([m[:7] for m in df_s_processed["날짜"].unique() if len(m) >= 7], reverse=True) if not df_s_processed.empty else []
        current_month_str = get_kst_now().strftime("%Y-%m")
        default_month_s = current_month_str if current_month_str in valid_months_s else (valid_months_s[0] if valid_months_s else "")
        available_months_s = [default_month_s] + [m for m in valid_months_s if m != default_month_s] + ["전체 보기"] if valid_months_s else ["전체 보기"]

        with col_f1:
            selected_month_s = st.selectbox("📅 조회할 월을 선택하세요", available_months_s, index=0, key="s_month_select")

        if not df_s_processed.empty and selected_month_s != "전체 보기":
            filtered_s_df = df_s_processed[df_s_processed["날짜"].str.startswith(selected_month_s)]
        else:
            filtered_s_df = df_s_processed.copy()

        item_options = ["전체 품목 보기"]
        if not filtered_s_df.empty:
            unique_items = [itm for itm in filtered_s_df["품목"].unique() if str(itm).strip() and str(itm).strip() != "-"]
            item_options.extend(unique_items)

        with col_f2:
            selected_item_s = st.selectbox("🥗 조회할 제품(품목)을 선택하세요", item_options, index=0, key="s_item_select")

        if selected_item_s != "전체 품목 보기" and not filtered_s_df.empty:
            final_view_df = filtered_s_df[filtered_s_df["품목"] == selected_item_s]
        else:
            final_view_df = filtered_s_df.copy()

        if not final_view_df.empty:
            agg_view_df = final_view_df.groupby(["날짜", "품목"], as_index=False).agg({
                "수량": "sum", "퀵비": "sum", "퀵비 비고": join_unique_notes, "비고": join_unique_notes
            })
            agg_view_df["소비기한"] = agg_view_df["날짜"].apply(calc_sweet_exp_date)
            ordered_s_cols = ["날짜", "품목", "수량", "소비기한", "퀵비", "퀵비 비고", "비고"]
            agg_view_df = agg_view_df[[c for c in ordered_s_cols if c in agg_view_df.columns]]
        else:
            agg_view_df = pd.DataFrame(columns=["날짜", "품목", "수량", "소비기한", "퀵비", "퀵비 비고", "비고"])

        st.markdown(f"##### 📊 발주 확인서 요약 집계 결과 ({selected_month_s} / {selected_item_s})")
        st.dataframe(agg_view_df.style.apply(highlight_next_day, axis=1), use_container_width=True)

        excel_data_s = to_excel(agg_view_df)
        st.download_button(
            label=f"📥 스윗밸런스 {selected_month_s} 발주 확인서 엑셀 다운로드",
            data=excel_data_s,
            file_name=f"스윗밸런스_발주확인서_{selected_month_s}_{selected_item_s}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    # [스윗밸런스 - 4. 박스 관리 (부자재전송)]
    with s_sub_tab4:
        st.subheader("📦 스윗밸런스 박스 산출 관리 (부자재전송)")
        st.caption("날짜 및 제품(품목)별 발주 수량을 기준으로 사용되는 박스 종류별 개수를 합산하여 보여줍니다.")
        
        df_s_box = df_s_base[
            (df_s_base["품목"].notnull()) & 
            (df_s_base["품목"] != "-") & 
            (df_s_base["수량"] > 0)
        ].copy()
        
        if not df_s_box.empty:
            col_b1, col_b2 = st.columns(2)
            valid_months_b = sorted([m[:7] for m in df_s_box["날짜"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_b else (valid_months_b[0] if valid_months_b else "")
            avail_m = [def_m] + [m for m in valid_months_b if m != def_m] + ["전체 보기"] if valid_months_b else ["전체 보기"]
            
            with col_b1:
                sel_m_b = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="box_month_select")
                
            if sel_m_b != "전체 보기":
                df_s_box = df_s_box[df_s_box["날짜"].str.startswith(sel_m_b)]
                
            box_item_options = ["전체 품목 보기"] + [i for i in df_s_box["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
            with col_b2:
                sel_i_b = st.selectbox("🥗 조회할 제품 선택", box_item_options, index=0, key="box_item_select")
                
            if sel_i_b != "전체 품목 보기":
                df_s_box = df_s_box[df_s_box["품목"] == sel_i_b]
                
            if not df_s_box.empty:
                boxes_df = df_s_box["수량"].apply(calc_sweet_boxes)
                df_s_box_calc = pd.concat([df_s_box.reset_index(drop=True), boxes_df.reset_index(drop=True)], axis=1)
                
                agg_box_df = df_s_box_calc.groupby(["날짜", "품목"], as_index=False)[
                    ["수량", "중박스(Full)", "중박스(3~4개)", "소박스(1~2개)"]
                ].sum()
                
                disp_cols = ["날짜", "품목", "수량", "중박스(Full)", "중박스(3~4개)", "소박스(1~2개)"]
                res_box_df = agg_box_df[disp_cols]
                
                st.markdown(f"##### 📦 날짜 및 제품별 박스 합산 내역 ({sel_m_b} / {sel_i_b})")
                st.dataframe(res_box_df.style.apply(highlight_next_day, axis=1), use_container_width=True)
                
                st.markdown("---")
                bm1, bm2, bm3, bm4 = st.columns(4)
                bm1.metric("전체 발주수량", f"{res_box_df['수량'].sum():,} 개")
                bm2.metric("📦 중박스 (Full: 5개)", f"{res_box_df['중박스(Full)'].sum():,} 개")
                bm3.metric("📦 중박스 (3~4개)", f"{res_box_df['중박스(3~4개)'].sum():,} 개")
                bm4.metric("📦 소박스 (1~2개)", f"{res_box_df['소박스(1~2개)'].sum():,} 개")
                
                excel_box = to_excel(res_box_df)
                st.download_button(
                    label="📥 박스 산출 요약 내역 엑셀 다운로드",
                    data=excel_box,
                    file_name=f"스윗밸런스_박스관리_{sel_m_b}_{sel_i_b}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

                # ---------------------------------------------------------
                # 📦 스윗밸런스 포장재 전송 전 데이터 미리보기 & 일괄 전송
                # ---------------------------------------------------------
                st.markdown("---")
                st.markdown("##### 📦 수불부(Carrot 앱)로 스윗밸런스 포장재 월별 일괄 전송")
                st.caption("선택된 월 기간의 실제 샐러드 출고량에 맞춰 수불부(Carrot 앱의 수기출고 탭)에 등록될 포장재를 미리 확인한 뒤 전송합니다.")
                
                target_items = ["브런치빈 샐러드믹스 1KG", "크라이 컷레터스 1KG"]
                product_data = df_s_box[df_s_box["품목"].isin(target_items)]
                sweet_total_qty = product_data["수량"].sum() if not product_data.empty else 0

                boxes_day_df = df_s_box["수량"].apply(calc_sweet_boxes)
                mid_boxes = int(boxes_day_df["중박스(Full)"].sum() + boxes_day_df["중박스(3~4개)"].sum())
                small_boxes = int(boxes_day_df["소박스(1~2개)"].sum())
                period_label = sel_m_b if sel_m_b != "전체 보기" else "전체 기간"

                preview_sweet_data = [
                    {"구분": "1. 파우치", "자재명(Carrot 앱 등록명)": "파우치 (300*400)", "산출 수량": f"{sweet_total_qty:,} 개", "단위": "개", "출고사유": f"스윗밸런스 파우치 ({period_label} 일괄)"},
                    {"구분": "2. 중박스", "자재명(Carrot 앱 등록명)": "카톤박스(중)", "산출 수량": f"{mid_boxes:,} 개", "단위": "개", "출고사유": f"스윗밸런스 출고 중박스 ({period_label} 일괄)"},
                    {"구분": "3. 소박스", "자재명(Carrot 앱 등록명)": "카톤박스(소)", "산출 수량": f"{small_boxes:,} 개", "단위": "개", "출고사유": f"스윗밸런스 출고 소박스 ({period_label} 일괄)"}
                ]
                preview_sweet_df = pd.DataFrame(preview_sweet_data)

                st.markdown("###### 🔍 전송 예정 포장재 목록 미리보기")
                st.dataframe(preview_sweet_df, use_container_width=True, hide_index=True)

                sm1, sm2, sm3 = st.columns(3)
                sm1.metric("파우치(300*400) 소요량", f"{sweet_total_qty:,} 개")
                sm2.metric("카톤박스(중) 소요량", f"{mid_boxes:,} 개")
                sm3.metric("카톤박스(소) 소요량", f"{small_boxes:,} 개")

                col_s_send1, col_s_send2 = st.columns([2, 1])
                with col_s_send1:
                    send_s_date = st.date_input("수불부 마감 기록 일자 지정 (예: 월말일)", get_kst_now().date(), key="send_s_date")
                with col_s_send2:
                    st.write("")
                    st.write("")
                    btn_sweet_disabled = (sweet_total_qty <= 0 and mid_boxes <= 0 and small_boxes <= 0)
                    if st.button(f"🥗 {sel_m_b} 포장재 일괄 전송하기", type="primary", use_container_width=True, disabled=btn_sweet_disabled):
                        send_s_str = send_s_date.strftime("%Y-%m-%d")
                        sent_any = False
                        
                        if sweet_total_qty > 0:
                            if send_to_subul_inventory(send_s_str, "파우치 (300*400)", sweet_total_qty, f"스윗밸런스 파우치 ({period_label} 일괄)"):
                                st.success(f"✅ 파우치 (300*400) {sweet_total_qty:,}개 전송 완료!")
                                sent_any = True
                        
                        if mid_boxes > 0:
                            if send_to_subul_inventory(send_s_str, "카톤박스(중)", mid_boxes, f"스윗밸런스 출고 중박스 ({period_label} 일괄)"):
                                st.success(f"✅ 카톤박스(중) {mid_boxes:,}개 전송 완료!")
                                sent_any = True
                                
                        if small_boxes > 0:
                            if send_to_subul_inventory(send_s_str, "카톤박스(소)", small_boxes, f"스윗밸런스 출고 소박스 ({period_label} 일괄)"):
                                st.success(f"✅ 카톤박스(소) {small_boxes:,}개 전송 완료!")
                                sent_any = True
                        
                        if not sent_any:
                            st.info("전송할 포장재 수량이 없어 전송하지 않았습니다.")

                if sweet_total_qty <= 0 and mid_boxes <= 0 and small_boxes <= 0:
                    st.info("💡 조회된 기간에 계산된 포장재 내역이 없습니다.")
            else:
                st.info("조회 조건에 해당되는 데이터가 없습니다.")
        else:
            st.info("스윗밸런스 발주 데이터가 존재하지 않습니다.")

    # [스윗밸런스 - 5. 센터 관리]
    with s_sub_tab5:
        st.subheader("🏭 스윗밸런스 센터 관리")
        st.caption("각 날짜별, 물류센터별, 제품별 발주 수량 및 박스(라벨) 수량을 확인합니다.")
        
        df_s_center = df_s_base[
            (df_s_base["품목"].notnull()) & 
            (df_s_base["품목"] != "-") & 
            (df_s_base["수량"] > 0)
        ].copy()
        
        if not df_s_center.empty:
            col_c1, col_c2, col_c3 = st.columns(3)
            valid_months_c = sorted([m[:7] for m in df_s_center["날짜"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_c else (valid_months_c[0] if valid_months_c else "")
            avail_m = [def_m] + [m for m in valid_months_c if m != def_m] + ["전체 보기"] if valid_months_c else ["전체 보기"]
            
            with col_c1:
                sel_m_c = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="center_month_select")
                
            if sel_m_c != "전체 보기":
                df_s_center = df_s_center[df_s_center["날짜"].str.startswith(sel_m_c)]
                
            center_list = ["전체 센터 보기"] + [c for c in df_s_center["센터"].unique() if str(c).strip() and str(c).strip() != "nan"]
            with col_c2:
                sel_center = st.selectbox("🏭 조회할 센터 선택", center_list, index=0, key="center_filter_select")
                
            if sel_center != "전체 센터 보기":
                df_s_center = df_s_center[df_s_center["센터"] == sel_center]

            item_list_c = ["전체 품목 보기"] + [i for i in df_s_center["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
            with col_c3:
                sel_item_c = st.selectbox("🥗 조회할 품목 선택", item_list_c, index=0, key="center_item_filter_select")

            if sel_item_c != "전체 품목 보기":
                df_s_center = df_s_center[df_s_center["품목"] == sel_item_c]
                
            if not df_s_center.empty:
                boxes_c_df = df_s_center["수량"].apply(calc_sweet_boxes)
                df_s_center_calc = pd.concat([df_s_center.reset_index(drop=True), boxes_c_df.reset_index(drop=True)], axis=1)
                
                center_summary = df_s_center_calc.groupby(["날짜", "센터", "품목"], as_index=False).agg(
                    발주수량=("수량", "sum"),
                    박스수량=("라벨 수량", "sum")
                )
                
                center_summary = center_summary.rename(columns={
                    "센터": "물류센터명",
                    "품목": "제품명"
                })
                
                center_summary = center_summary[["날짜", "물류센터명", "제품명", "발주수량", "박스수량"]]
                center_summary = center_summary.sort_values(by=["날짜", "물류센터명", "제품명"])
                
                st.markdown(f"##### 🏭 센터별 품목 수불 내역 ({sel_m_c} / {sel_center} / {sel_item_c})")
                st.dataframe(center_summary.style.apply(highlight_next_day, axis=1), use_container_width=True)
                
                st.markdown("---")
                cm1, cm2 = st.columns(2)
                cm1.metric("총 발주수량 합계", f"{center_summary['발주수량'].sum():,} 개")
                cm2.metric("총 박스수량 합계", f"{center_summary['박스수량'].sum():,} 박스")
                
                excel_center = to_excel(center_summary)
                st.download_button(
                    label="📥 센터 관리 데이터 엑셀 다운로드",
                    data=excel_center,
                    file_name=f"스윗밸런스_센터관리_{sel_m_c}_{sel_center}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.info("조회 조건에 해당되는 센터 데이터가 없습니다.")
        else:
            st.info("스윗밸런스 발주 데이터가 존재하지 않습니다.")

    # [스윗밸런스 - 6. 외포장일지]
    if SHOW_SWEET_OUTER_LOG and s_sub_tab6 is not None:
        with s_sub_tab6:
            st.subheader("📋 스윗밸런스 외포장 작업일지")
            st.caption("발주 수량에 따라 사용되는 세부 박스 규격 및 총 박스(라벨) 수량과 소비기한을 세부 집계합니다.")
            
            df_s_log = df_s_base[
                (df_s_base["품목"].notnull()) & 
                (df_s_base["품목"] != "-") & 
                (df_s_base["수량"] > 0)
            ].copy()
            
            if not df_s_log.empty:
                col_l1, col_l2 = st.columns(2)
                valid_months_l = sorted([m[:7] for m in df_s_log["날짜"].unique() if len(m) >= 7], reverse=True)
                current_m = get_kst_now().strftime("%Y-%m")
                def_m = current_m if current_m in valid_months_l else (valid_months_l[0] if valid_months_l else "")
                avail_m = [def_m] + [m for m in valid_months_l if m != def_m] + ["전체 보기"] if valid_months_l else ["전체 보기"]

                with col_l1:
                    sel_m_l = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="log_sub_month_select")

                if sel_m_l != "전체 보기":
                    df_s_log = df_s_log[df_s_log["날짜"].str.startswith(sel_m_l)]

                log_item_options = ["전체 품목 보기"] + [i for i in df_s_log["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
                with col_l2:
                    sel_i_l = st.selectbox("🥗 조회할 제품 선택", log_item_options, index=0, key="log_sub_item_select")

                if sel_i_l != "전체 품목 보기":
                    df_s_log = df_s_log[df_s_log["품목"] == sel_i_l]

                if not df_s_log.empty:
                    boxes_detail_df = df_s_log["수량"].apply(calc_sweet_boxes_detail)
                    df_s_log_calc = pd.concat([df_s_log.reset_index(drop=True), boxes_detail_df.reset_index(drop=True)], axis=1)

                    log_summary = df_s_log_calc.groupby(["날짜", "품목"], as_index=False)[
                        [
                            "수량", 
                            "중박스(5개-Full)", 
                            "중박스(4개)", 
                            "중박스(3개)", 
                            "소박스(2개)", 
                            "소박스(1개)", 
                            "총 박스(라벨) 수"
                        ]
                    ].sum()

                    log_summary["소비기한"] = log_summary["날짜"].apply(calc_sweet_exp_date)

                    log_summary = log_summary[[
                        "날짜", "품목", "수량", "소비기한", 
                        "중박스(5개-Full)", "중박스(4개)", "중박스(3개)", 
                        "소박스(2개)", "소박스(1개)", "총 박스(라벨) 수"
                    ]]

                    st.markdown(f"##### 📦 외포장 작업일지 세부 집계표 ({sel_m_l} / {sel_i_l})")
                    st.dataframe(log_summary.style.apply(highlight_next_day, axis=1), use_container_width=True)

                    st.markdown("---")
                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("총 발주 수량", f"{log_summary['수량'].sum():,} 개")
                    m2.metric("중박스(5개-Full)", f"{log_summary['중박스(5개-Full)'].sum():,} 박스")
                    m3.metric("중박스(4개)", f"{log_summary['중박스(4개)'].sum():,} 박스")
                    m4.metric("중박스(3개)", f"{log_summary['중박스(3개)'].sum():,} 박스")
                    m5.metric("소박스(2개)", f"{log_summary['소박스(2개)'].sum():,} 박스")
                    m6.metric("소박스(1개)", f"{log_summary['소박스(1개)'].sum():,} 박스")

                    st.markdown("---")
                    excel_log = to_excel(log_summary)
                    st.download_button(
                        label="📥 외포장 작업일지 엑셀 다운로드",
                        data=excel_log,
                        file_name=f"스윗밸런스_외포장일지_{sel_m_l}_{sel_i_l}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                else:
                    st.info("조회 조건에 해당되는 데이터가 없습니다.")
            else:
                st.info("스윗밸런스 발주 데이터가 존재하지 않습니다.")


# ==========================================
# --- [메인 화면 5: 거래명세서 발행 (스윗밸런스 전용)] ---
# ==========================================
elif selected_menu == "📑 거래명세서 발행 (스윗밸런스)":
    st.title("📑 (주)스윗밸런스랩 거래명세서 발행")
    st.write("스윗밸런스 출고 기간을 지정하여 거래명세서를 조회하고 인쇄/PDF 저장 또는 엑셀로 다운로드합니다.")
    st.divider()
    
    col_inv1, col_inv2 = st.columns(2)
    today_dt = get_kst_now().date()
    
    with col_inv1:
        start_date = st.date_input("조회 시작일", today_dt, key="inv_start_date")
    with col_inv2:
        end_date = st.date_input("조회 종료일", today_dt, key="inv_end_date")
    
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    today_issue_date_str = today_dt.strftime("%Y-%m-%d")
    
    buyer = st.session_state.buyer_sweet
    supplier = st.session_state.supplier_info
    items_list = []
    
    raw_df = load_data("스윗밸런스")
    if not raw_df.empty and "날짜" in raw_df.columns:
        raw_df["품목"] = raw_df["품목"].apply(normalize_sweet_item_name)
        raw_df["정제날짜"] = raw_df["날짜"].apply(parse_date_str)
        target_data = raw_df[(raw_df["정제날짜"] >= start_date_str) & (raw_df["정제날짜"] <= end_date_str)]
        
        if not target_data.empty:
            for _, r in target_data.iterrows():
                q = int(pd.to_numeric(r.get("수량", 0), errors='coerce') or 0)
                itm = str(r.get("품목", "")).strip()
                unit_p = st.session_state.unit_prices.get(itm, 4720)
                if q > 0 and itm != "-":
                    items_list.append({
                        "date": r["정제날짜"], 
                        "item": itm, 
                        "spec": "EA", 
                        "qty": q, 
                        "price": unit_p, 
                        "amount": q * unit_p, 
                        "note": str(r.get("비고", ""))
                    })

    custom_bottom_memo = st.text_input("하단 메모 (계좌번호/입금조건 등)", value=" ", key="custom_bottom_memo")
    
    st.markdown("---")
    if not items_list:
        st.warning(f"⚠️ 선택하신 기간 [{start_date_str} ~ {end_date_str}] 내 스윗밸런스 발주 내역이 존재하지 않습니다.")
    else:
        total_amount = sum(i["amount"] for i in items_list)
        items_html_rows = ""
        for idx, itm in enumerate(items_list, 1):
            items_html_rows += f"""
            <tr>
                <td>{idx}</td>
                <td>{itm['date']}</td>
                <td style="text-align:left;">{itm['item']}</td>
                <td>{itm['spec']}</td>
                <td style="text-align:right;">{itm['qty']:,}</td>
                <td style="text-align:right;">{itm['price']:,}</td>
                <td style="text-align:right;">{itm['amount']:,}</td>
                <td style="text-align:left;">{itm['note']}</td>
            </tr>
            """

        full_invoice_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Malgun Gothic', sans-serif; margin: 0; padding: 10px; }}
            .invoice-box {{ border: 2px solid #333; padding: 15px; }}
            .title {{ text-align: center; font-size: 24px; font-weight: bold; margin-bottom: 15px; letter-spacing: 5px; text-decoration: underline; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 12px; }}
            th, td {{ border: 1px solid #333; padding: 6px; text-align: center; }}
            th {{ background: #f2f2f2; }}
        </style>
        </head>
        <body>
            <div class="invoice-box">
                <div class="title">거 래 명 세 서</div>
                <table>
                    <tr>
                        <td style="border:none; text-align:left; font-weight:bold;">발행일자: {today_issue_date_str} (거래기간: {start_date_str} ~ {end_date_str})</td>
                    </tr>
                </table>
                <table>
                    <tr>
                        <th rowspan="4" style="width:4%;">공<br>급<br>자</th>
                        <th>등록번호</th><td colspan="3">{supplier['biz_no']}</td>
                        <th rowspan="4" style="width:4%;">공급<br>받는자</th>
                        <th>등록번호</th><td colspan="3">{buyer['biz_no']}</td>
                    </tr>
                    <tr>
                        <th>상호</th><td>{supplier['name']}</td><th>성명</th><td>{supplier['owner']} (인)</td>
                        <th>상호</th><td>{buyer['name']}</td><th>성명</th><td>{buyer['owner']} (인)</td>
                    </tr>
                    <tr>
                        <th>주소</th><td colspan="3">{supplier['addr']}</td>
                        <th>주소</th><td colspan="3">{buyer['addr']}</td>
                    </tr>
                    <tr>
                        <th>업태</th><td>{supplier['biz_type']}</td><th>종목</th><td>{supplier['biz_item']}</td>
                        <th>업태</th><td>{buyer['biz_type']}</td><th>종목</th><td>{buyer['biz_item']}</td>
                    </tr>
                </table>
                <table>
                    <tr>
                        <th>No</th><th>일자</th><th>품목명</th><th>규격</th><th>수량</th><th>단가</th><th>금액</th><th>비고</th>
                    </tr>
                    {items_html_rows}
                    <tr>
                        <th colspan="4">합 계 금 액</th>
                        <td colspan="4" style="text-align:right; font-weight:bold; font-size:14px;">₩ {total_amount:,} 원</td>
                    </tr>
                </table>
                <div style="border:1px solid #333; padding:8px; font-size:12px; background:#fafafa;">
                    <b>📌 메모:</b> {custom_bottom_memo}
                </div>
            </div>
        </body>
        </html>
        """
        st.components.v1.html(full_invoice_html, height=560, scrolling=True)

        # ---------------------------------------------------------
        # 🖨️ [복원됨] 거래명세서 인쇄/PDF 및 엑셀 다운로드 버튼
        # ---------------------------------------------------------
        st.markdown("<br>", unsafe_allow_html=True)
        btn_c1, btn_c2 = st.columns(2)
        
        with btn_c1:
            js_invoice_content = full_invoice_html.replace('`', '\\`').replace('${', '\\${')
            
            print_button_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    background: transparent;
                }}
                .print-btn {{
                    width: 100%;
                    height: 38px;
                    background-color: #ff4b4b;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    font-size: 14px;
                    font-weight: 500;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    box-sizing: border-box;
                    font-family: inherit;
                    transition: background-color 0.2s ease;
                }}
                .print-btn:hover {{
                    background-color: #e03e3e;
                }}
            </style>
            </head>
            <body>
            <script>
            function printInvoice() {{
                var printWindow = window.open('', '_blank');
                printWindow.document.write(`{js_invoice_content}`);
                printWindow.document.close();
                printWindow.focus();
                setTimeout(function() {{
                    printWindow.print();
                    printWindow.close();
                }}, 500);
            }}
            </script>
            <button class="print-btn" onclick="printInvoice()">🖨️ 거래명세서 인쇄 / PDF 저장</button>
            </body>
            </html>
            """
            st.components.v1.html(print_button_html, height=42)

        with btn_c2:
            inv_df = pd.DataFrame(items_list)
            inv_df = inv_df.rename(columns={
                "date": "일자",
                "item": "품목명",
                "spec": "규격",
                "qty": "수량",
                "price": "단가",
                "amount": "금액",
                "note": "비고"
            })
            excel_inv = to_excel(inv_df)
            st.download_button(
                label="📥 거래명세서 데이터 엑셀 다운로드",
                data=excel_inv,
                file_name=f"거래명세서_스윗밸런스_{start_date_str}_to_{end_date_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )


# ==========================================
# --- [메인 화면 6: 설정 (기초정보 & 단가)] ---
# ==========================================
elif selected_menu == "⚙️ 설정 (기초정보 & 단가)":
    st.title("⚙️ 기초 정보 및 공급 단가 관리")
    st.write("공급자 정보, (주)스윗밸런스랩 거래처 정보 및 각 거래처별 품목 단가를 설정합니다.")
    st.divider()
    
    st.markdown("##### 🏢 공급자 (내 회사) 정보")
    s_col1, s_col2 = st.columns(2)
    with s_col1:
        st.session_state.supplier_info["name"] = st.text_input("상호명", st.session_state.supplier_info["name"], key="cfg_s_name")
        st.session_state.supplier_info["owner"] = st.text_input("대표자 성명", st.session_state.supplier_info["owner"], key="cfg_s_owner")
        st.session_state.supplier_info["biz_no"] = st.text_input("사업자 등록번호", st.session_state.supplier_info["biz_no"], key="cfg_s_no")
    with s_col2:
        st.session_state.supplier_info["addr"] = st.text_input("사업장 주소", st.session_state.supplier_info["addr"], key="cfg_s_addr")
        st.session_state.supplier_info["biz_type"] = st.text_input("업태", st.session_state.supplier_info["biz_type"], key="cfg_s_type")
        st.session_state.supplier_info["biz_item"] = st.text_input("종목", st.session_state.supplier_info["biz_item"], key="cfg_s_item")
        
    st.markdown("---")
    st.markdown("##### 🤝 거래명세서 공급받는자: (주)스윗밸런스랩 정보")
    sb1, sb2 = st.columns(2)
    with sb1:
        st.session_state.buyer_sweet["name"] = st.text_input("상호명 ", st.session_state.buyer_sweet["name"], key="cfg_sw_name")
        st.session_state.buyer_sweet["owner"] = st.text_input("대표자 성명 ", st.session_state.buyer_sweet["owner"], key="cfg_sw_owner")
        st.session_state.buyer_sweet["biz_no"] = st.text_input("사업자 등록번호 ", st.session_state.buyer_sweet["biz_no"], key="cfg_sw_no")
    with sb2:
        st.session_state.buyer_sweet["addr"] = st.text_input("사업장 주소 ", st.session_state.buyer_sweet["addr"], key="cfg_sw_addr")
        st.session_state.buyer_sweet["biz_type"] = st.text_input("업태 ", st.session_state.buyer_sweet["biz_type"], key="cfg_sw_type")
        st.session_state.buyer_sweet["biz_item"] = st.text_input("종목 ", st.session_state.buyer_sweet["biz_item"], key="cfg_sw_item")

    st.markdown("---")
    st.markdown("##### 💵 거래처별 품목 공급 단가 설정 (원)")
    
    st.caption("**1. 스윗밸런스 단가**")
    sw1, sw2 = st.columns(2)
    with sw1:
        st.session_state.unit_prices["브런치빈 샐러드믹스 1KG"] = st.number_input("브런치빈 샐러드믹스 1KG 단가", value=st.session_state.unit_prices.get("브런치빈 샐러드믹스 1KG", 4720), step=100, key="cfg_p_brunch")
    with sw2:
        st.session_state.unit_prices["크라이 컷레터스 1KG"] = st.number_input("크라이 컷레터스 1KG 단가", value=st.session_state.unit_prices.get("크라이 컷레터스 1KG", 5605), step=100, key="cfg_p_cry")

    st.caption("**2. 쿠팡 단가**")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        st.session_state.unit_prices["carrot200"] = st.number_input("당근 200 단가", value=st.session_state.unit_prices.get("carrot200", 1500), step=100, key="cfg_p_c200")
    with p2:
        st.session_state.unit_prices["carrot400"] = st.number_input("당근 400 단가", value=st.session_state.unit_prices.get("carrot400", 3000), step=100, key="cfg_p_c400")
    with p3:
        st.session_state.unit_prices["spinach"] = st.number_input("시금치 단가", value=st.session_state.unit_prices.get("spinach", 2000), step=100, key="cfg_p_s")
    with p4:
        st.session_state.unit_prices["gomgom"] = st.number_input("곰곰믹스 단가", value=st.session_state.unit_prices.get("gomgom", 3500), step=100, key="cfg_p_gom")

    st.caption("**3. 오아시스 단가**")
    oa1, oa2, oa3, oa4 = st.columns(4)
    with oa1:
        st.session_state.unit_prices["채소믹스 500"] = st.number_input("채소믹스 500 단가", value=st.session_state.unit_prices.get("채소믹스 500", 3500), step=100, key="cfg_p_oa1")
    with oa2:
        st.session_state.unit_prices["양적 500"] = st.number_input("양적 500 단가", value=st.session_state.unit_prices.get("양적 500", 3500), step=100, key="cfg_p_oa2")
    with oa3:
        st.session_state.unit_prices["컷레터스 500"] = st.number_input("컷레터스 500 단가", value=st.session_state.unit_prices.get("컷레터스 500", 3800), step=100, key="cfg_p_oa3")
    with oa4:
        st.session_state.unit_prices["유럽믹스 200"] = st.number_input("유럽믹스 200 단가", value=st.session_state.unit_prices.get("유럽믹스 200", 2800), step=100, key="cfg_p_oa4")

    st.success("💡 설정값이 성공적으로 업데이트되었습니다!")

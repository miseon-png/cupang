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
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 및 거래명세서 시스템", layout="wide")

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

# ==========================================
# 🌟 [추가] 수불부(Carrot 앱) 자동 전송 설정
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
        
        # Carrot 앱의 수기출고 탭 구조: [일자, 재료명, 출고수량, 출고단위, 출고사유, 상세비고]
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
        "인천 400g": "인천 당근 400", "부천 당근 400": "부천 당근 400",
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
        ["🚀 쿠팡 관리", "🥗 스윗밸런스 관리", "📑 거래명세서 발행", "📋 외포장 공정일지 (양식)", "⚙️ 설정 (기초정보 & 단가)"],
        index=0
    )
    st.markdown("---")
    st.caption("💡 거래처별 발주 입력, 엑셀 업로드, 외포장 공정일지, 거래명세서를 통합 관리합니다.")


# ==========================================
# --- [메인 화면 1: 쿠팡 관리] ---
# ==========================================
if selected_menu == "🚀 쿠팡 관리":
    st.title("🚀 쿠팡 발주 관리")
    st.write("쿠팡 센터별 발주 수량 입력, 발주 확인서 및 전체 통합 외포장일지를 관리합니다.")
    st.divider()
    
    c_sub_tab1, c_sub_tab2, c_sub_tab3 = st.tabs(["✍️ 발주 수량 입력", "📊 발주 확인서", "📋 외포장일지"])
    
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
            c_in_s = st.number_input("인천 시금치 수량", min_value=0, value=0, step=1, key="c_in_s")
        with c2:
            st.markdown("##### 📍 부천센터")
            c_bu_c200 = st.number_input("부천 당근 200 수량", min_value=0, value=0, step=1, key="c_bu_c200")
            c_bu_c400 = st.number_input("부천 당근 400 수량", min_value=0, value=0, step=1, key="c_bu_c400")
            c_bu_s = st.number_input("부천 시금치 수량", min_value=0, value=0, step=1, key="c_bu_s")

        st.markdown("---")
        if st.button("🚀 쿠팡 발주 저장하기", type="primary", use_container_width=True):
            total_qty = c_in_c200 + c_bu_c200 + c_in_c400 + c_bu_c400 + c_in_s + c_bu_s
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
                        "부천 시금치": c_bu_s
                    }])
                    updated_df = pd.concat([existing_df, new_row], ignore_index=True)
                    conn.update(worksheet="쿠팡", data=updated_df)
                    st.cache_data.clear()
                    st.success(f"[{c_date_str}] 쿠팡 발주가 구글 시트에 성공적으로 저장되었습니다!")
                except Exception as err:
                    st.error(f"저장 중 오류 발생: {err}")

    # [쿠팡 - 2. 발주 확인서]
    with c_sub_tab2:
        col_c_head, col_c_btn = st.columns([4, 1])
        with col_c_head:
            st.subheader("📊 쿠팡 발주 확인서")
        with col_c_btn:
            if st.button("🔄 쿠팡 데이터 새로고침", use_container_width=True, key="btn_refresh_c"):
                st.cache_data.clear()
                st.rerun()

        df_c_raw = load_data("쿠팡")
        df_c_all_calc = calculate_coupang(df_c_raw, carrot200_box_unit, carrot400_box_unit, spinach_box_unit, coupang_exp_days)

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
            in_box = row_t.get("인천 박스수량", 0)
            bu_box = row_t.get("부천 박스수량", 0)
            bipyo_txt = row_t.get("비표", "")
            bipyo_display = bipyo_txt if bipyo_txt else "없음(시금치 0개)"
            exp_txt = row_t.get("소비기한", "")
            exp_display = exp_txt if exp_txt else "없음(당근 200/당근 400 0개)"

            st.markdown(f"""
            <div class="today-work-box">
                <div class="today-work-title">🚨 오늘 작업할 쿠팡 발주 내용 (납품 예정일: {kst_tomorrow_str})</div>
                <div class="today-work-content">
                    • <b>인천센터:</b> 당근 200 <b>{in_c200:,}</b>개 / 당근 400 <b>{in_c400:,}</b>개 / 시금치 <b>{in_s:,}</b>개 👉 <b>인천 총 {in_box:,} 박스</b><br>
                    • <b>부천센터:</b> 당근 200 <b>{bu_c200:,}</b>개 / 당근 400 <b>{bu_c400:,}</b>개 / 시금치 <b>{bu_s:,}</b>개 👉 <b>부천 총 {bu_box:,} 박스</b><br>
                    • <b>시금치 비표:</b> <span style="font-size:1.15rem; font-weight:bold; color:#cc0000;">{bipyo_display}</span> | <b>당근 소비기한:</b> {exp_display}
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
        if valid_months:
            other_months = [m for m in valid_months if m != default_month_c]
            available_months = [default_month_c] + other_months + ["전체 보기"]
        else:
            available_months = ["전체 보기"]

        selected_month = st.selectbox("📅 조회할 월을 선택하세요", available_months, index=0, key="c_month_select")
        
        if not df_c_raw.empty and "연월" in df_c_raw.columns and selected_month != "전체 보기":
            filtered_c_df = df_c_raw[df_c_raw["연월"] == selected_month].drop(columns=["정제날짜", "연월"], errors='ignore')
        else:
            filtered_c_df = df_c_raw.drop(columns=["정제날짜", "연월"], errors='ignore') if "연월" in df_c_raw.columns else df_c_raw

        calculated_c_df = calculate_coupang(filtered_c_df, carrot200_box_unit, carrot400_box_unit, spinach_box_unit, coupang_exp_days)

        st.markdown("##### 📊 최종 집계 및 박스 수량 결과")
        styled_c_df = calculated_c_df.style.apply(highlight_next_day, axis=1)
        st.dataframe(styled_c_df, use_container_width=True)

        excel_data_c = to_excel(calculated_c_df)
        st.download_button(
            label=f"📥 쿠팡 {selected_month} 최종 결과 엑셀 파일 다운로드",
            data=excel_data_c,
            file_name=f"쿠팡_발주확인서_{selected_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        if not calculated_c_df.empty:
            total_incheon_box = calculated_c_df["인천 박스수량"].sum() if "인천 박스수량" in calculated_c_df else 0
            total_bucheon_box = calculated_c_df["부천 박스수량"].sum() if "부천 박스수량" in calculated_c_df else 0
            
            st.markdown("---")
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("선택 기간 발주 건수", f"{len(calculated_c_df)} 건")
            m2.metric("당근 200 총합", f"{calculated_c_df['당근 200 합계'].sum() if '당근 200 합계' in calculated_c_df else 0:,} 개")
            m3.metric("당근 400 총합", f"{calculated_c_df['당근 400 합계'].sum() if '당근 400 합계' in calculated_c_df else 0:,} 개")
            m4.metric("시금치 총합", f"{calculated_c_df['시금치 합계'].sum() if '시금치 합계' in calculated_c_df else 0:,} 개")
            m5.metric("인천 총 박스", f"{total_incheon_box:,} 박스")
            m6.metric("부천 총 박스", f"{total_bucheon_box:,} 박스")

        # ---------------------------------------------------------
        # [추가] 쿠팡 시금치 박스 수불부 전송 버튼 영역
        # ---------------------------------------------------------
        st.markdown("---")
        st.markdown("##### 📦 수불부(Carrot 앱)로 쿠팡 박스 사용량 전송")
        st.info("💡 **당근라페 박스**는 Carrot 앱 생산 시 자동 차감되므로, 여기서는 **시금치 박스[카톤박스(중)]**만 전송합니다.")
        
        col_c_send1, col_c_send2 = st.columns([2, 1])
        with col_c_send1:
            # 납품 전날 작업하므로, 기본값은 오늘 날짜
            send_c_date = st.date_input("수불부로 전송할 납품일자 선택 (쿠팡)", get_kst_now().date() + timedelta(days=1), key="send_c_date")
        with col_c_send2:
            st.write("") 
            st.write("")
            if st.button("🚀 선택한 날짜 시금치 박스 수불부로 전송", type="primary", use_container_width=True):
                send_d_str = send_c_date.strftime("%Y-%m-%d")
                
                # 원본에서 해당 날짜 데이터만 뽑아서 다시 계산
                raw_c_for_send = load_data("쿠팡")
                if not raw_c_for_send.empty and "날짜" in raw_c_for_send.columns:
                    calc_all_send = calculate_coupang(raw_c_for_send, carrot200_box_unit, carrot400_box_unit, spinach_box_unit, coupang_exp_days)
                    target_df = calc_all_send[calc_all_send["날짜"] == send_d_str]
                    
                    if target_df.empty:
                        st.warning(f"[{send_d_str}] 자 쿠팡 발주 내역이 없습니다.")
                    else:
                        spinach_total = int(target_df["시금치 합계"].sum())
                        spinach_boxes = math.ceil(spinach_total / spinach_box_unit)
                        
                        if spinach_boxes > 0:
                            success = send_to_subul_inventory(send_d_str, "카톤박스(중)", spinach_boxes, f"쿠팡 시금치 출고 ({spinach_total}개)")
                            if success:
                                st.success(f"✅ [{send_d_str}] 쿠팡 시금치 박스(카톤박스(중)) {spinach_boxes}개 수불부 전송 완료!")
                        else:
                            st.info("해당 날짜에 시금치 발주 내역이 없어 전송할 박스가 없습니다.")
                else:
                    st.warning("쿠팡 데이터가 존재하지 않습니다.")

    # [쿠팡 - 3. 외포장일지]
    with c_sub_tab3:
        st.subheader("📋 쿠팡 외포장 작업일지 (전체 통합)")
        st.caption("센터 구분 없이 총 작업량 기준으로 당근 소비기한(+4일), 시금치 비표 및 박스 규격별 수량을 종합 집계합니다.")

        df_c_raw_log = load_data("쿠팡")
        df_c_log_calc = calculate_coupang(df_c_raw_log, carrot200_box_unit, carrot400_box_unit, spinach_box_unit, coupang_exp_days)

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

                c2_full, c2_rem, c2_box = calc_coupang_boxes_detail(c2_total, carrot200_box_unit)
                c4_full, c4_rem, c4_box = calc_coupang_boxes_detail(c4_total, carrot400_box_unit)
                s_full, s_rem, s_box = calc_coupang_boxes_detail(s_total, spinach_box_unit)

                tot_boxes = c2_box + c4_box + s_box

                log_rows.append({
                    "작업일자(생산일)": r["작업일자(생산일)"],
                    "납품일자": r["날짜"],
                    "당근 소비기한": r["소비기한"] if r["소비기한"] else "-",
                    "시금치 비표": r["비표"] if r["비표"] else "-",
                    "당근 200g (6개입)": f"{c2_full}박스" + (f" + {c2_rem}개" if c2_rem > 0 else ""),
                    "당근 400g (8개입)": f"{c4_full}박스" + (f" + {c4_rem}개" if c4_rem > 0 else ""),
                    "시금치 (5개입)": f"{s_full}박스" + (f" + {s_rem}개" if s_rem > 0 else ""),
                    "총 박스 수량": tot_boxes
                })

            df_cp_log_final = pd.DataFrame(log_rows)

            if not df_cp_log_final.empty:
                st.markdown(f"##### 📦 쿠팡 통합 외포장 작업일지 집계표 ({sel_m_cp_log})")
                st.dataframe(df_cp_log_final.style.apply(highlight_next_day, axis=1), use_container_width=True)

                st.markdown("---")
                lm1, lm2 = st.columns(2)
                lm1.metric("총 작업 건수", f"{len(df_cp_log_final)} 건")
                lm2.metric("총 박스(라벨) 수량", f"{df_cp_log_final['총 박스 수량'].sum():,} 박스")

                st.markdown("---")
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
# --- [메인 화면 2: 스윗밸런스 관리] ---
# ==========================================
elif selected_menu == "🥗 스윗밸런스 관리":
    st.title("🥗 스윗밸런스 발주, 박스 및 센터 관리")
    st.write("스윗밸런스 엑셀 파일 업로드, 발주 확인서, 박스 수량, 센터별 라벨 관리 및 외포장일지 기능을 제공합니다.")
    st.divider()
    
    s_sub_tab1, s_sub_tab2, s_sub_tab3, s_sub_tab4, s_sub_tab5, s_sub_tab6 = st.tabs([
        "📁 발주 파일 업로드", 
        "✍️ 직접 입력", 
        "📊 발주 확인서", 
        "📦 박스 관리",
        "🏭 센터 관리",
        "📋 외포장일지"
    ])
    
    # (스윗밸런스 1~3 탭은 기존 코드와 동일합니다. 코드 길이 절약을 위해 중간 생략 없이 원문 그대로 유지)
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
                        s_order_date_str = st.date_input("📅 파일명에 날짜가 없음 (발주/출고 날짜 직접 선택)", kst_today, key="u_date_manual").strftime("%Y-%m-%d")
                
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
                        u_quick_note = st.text_input("퀵비 비고 / 메모", value="", placeholder="예: 성남 퀵, 전체 합산 퀵비 등", key="u_quick_note")
                    
                    if st.button("💾 구글 시트에 반영 및 저장하기", type="primary", use_container_width=True):
                        existing_s_df = load_data("스윗밸런스")
                        rows_to_save = []
                        for idx, r in parsed_df.iterrows():
                            q_f = u_quick_fee if idx == 0 else 0
                            q_n = u_quick_note if idx == 0 else ""
                            rows_to_save.append({
                                "날짜": r["날짜"], "센터": r["센터"], "품목": r["품목"], "수량": r["수량"],
                                "퀵비": q_f, "퀵비 비고": q_n, "비고": f"엑셀 업로드 ({filename})"
                            })
                        
                        new_upload_df = pd.DataFrame(rows_to_save)
                        updated_s_df = pd.concat([existing_s_df, new_upload_df], ignore_index=True)
                        cols_order = ["날짜", "센터", "품목", "수량", "퀵비", "퀵비 비고", "비고"]
                        updated_s_df = updated_s_df[[c for c in cols_order if c in updated_s_df.columns]]
                        conn.update(worksheet="스윗밸런스", data=updated_s_df)
                        st.cache_data.clear()
                        st.success(f"🎉 [{s_order_date_str}] 파일 기반 발주 내역이 저장되었습니다!")
                else:
                    st.warning(f"⚠️ 필수 항목 컬럼이 부족합니다. 필요한 컬럼: {req_cols}")
            except Exception as e:
                st.error(f"엑셀 처리 중 오류 발생: {e}")

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
            s_quick_note = st.text_input("퀵비 비고 (지역/운임 메모)", value="", placeholder="예: 성남 퀵", key="s_quick_note_input")

        s_note = st.text_input("발주 비고 (특이사항 / 메모)", value="", placeholder="예: 샘플 포함", key="s_note_input")

        if st.button("🥗 스윗밸런스 발주 저장하기", type="primary", use_container_width=True):
            total_items_cnt = s_qty_brunch + s_qty_cry
            if total_items_cnt == 0 and s_quick_fee == 0:
                st.warning("⚠️ 입력된 발주 수량 또는 퀵비 금액이 없습니다.")
            else:
                try:
                    existing_df = load_data("스윗밸런스")
                    rows_to_add = []
                    if s_qty_brunch > 0:
                        rows_to_add.append({"날짜": s_date_str, "센터": "수동 입력", "품목": "브런치빈 샐러드믹스 1KG", "수량": s_qty_brunch, "퀵비": s_quick_fee, "퀵비 비고": s_quick_note, "비고": s_note})
                        s_quick_fee = 0
                    if s_qty_cry > 0:
                        rows_to_add.append({"날짜": s_date_str, "센터": "수동 입력", "품목": "크라이 컷레터스 1KG", "수량": s_qty_cry, "퀵비": s_quick_fee, "퀵비 비고": s_quick_note, "비고": s_note})
                    if not rows_to_add and s_quick_fee > 0:
                        rows_to_add.append({"날짜": s_date_str, "센터": "수동 입력", "품목": "-", "수량": 0, "퀵비": s_quick_fee, "퀵비 비고": s_quick_note, "비고": s_note})

                    new_df = pd.DataFrame(rows_to_add)
                    updated_df = pd.concat([existing_df, new_df], ignore_index=True)
                    cols_order = ["날짜", "센터", "품목", "수량", "퀵비", "퀵비 비고", "비고"]
                    updated_df = updated_df[[c for c in cols_order if c in updated_df.columns]]

                    conn.update(worksheet="스윗밸런스", data=updated_df)
                    st.cache_data.clear()
                    st.success(f"[{s_date_str}] 스윗밸런스 수동 발주 내역이 성공적으로 저장되었습니다!")
                except Exception as err:
                    st.error(f"저장 중 오류 발생: {err}")

    with s_sub_tab3:
        col_s_head, col_s_btn = st.columns([4, 1])
        with col_s_head:
            st.subheader("📊 스윗밸런스 발주 확인서")
        with col_s_btn:
            if st.button("🔄 스윗밸런스 데이터 새로고침", use_container_width=True, key="btn_refresh_s"):
                st.cache_data.clear()
                st.rerun()

        df_s_raw = load_data("스윗밸런스")
        df_s_processed = df_s_raw.copy()
        if not df_s_processed.empty:
            if "품목" in df_s_processed.columns: df_s_processed["품목"] = df_s_processed["품목"].apply(normalize_sweet_item_name)
            if "수량" in df_s_processed.columns: df_s_processed["수량"] = pd.to_numeric(df_s_processed["수량"], errors='coerce').fillna(0).astype(int)
            if "퀵비" in df_s_processed.columns: df_s_processed["퀵비"] = pd.to_numeric(df_s_processed["퀵비"], errors='coerce').fillna(0).astype(int)
            for col_empty in ["퀵비 비고", "비고"]:
                if col_empty not in df_s_processed.columns: df_s_processed[col_empty] = ""
            if "날짜" in df_s_processed.columns:
                df_s_processed["날짜"] = df_s_processed["날짜"].apply(parse_date_str)
                df_s_processed = df_s_processed[df_s_processed["날짜"] != ""]

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
        current_month_str = get_kst_now().strftime("%Y-%m")
        valid_months_s = []
        if not df_s_raw.empty and "날짜" in df_s_raw.columns:
            df_s_raw["정제날짜"] = df_s_raw["날짜"].apply(parse_date_str)
            df_s_raw["연월"] = df_s_raw["정제날짜"].apply(lambda d: d[:7] if len(d) >= 7 else "")
            valid_months_s = sorted([m for m in df_s_raw["연월"].unique() if m and len(m) == 7], reverse=True)

        default_month_s = current_month_str if current_month_str in valid_months_s else (valid_months_s[0] if valid_months_s else "")
        available_months_s = [default_month_s] + [m for m in valid_months_s if m != default_month_s] + ["전체 보기"] if valid_months_s else ["전체 보기"]

        with col_f1: selected_month_s = st.selectbox("📅 조회할 월을 선택하세요", available_months_s, index=0, key="s_month_select")

        if not df_s_processed.empty and selected_month_s != "전체 보기":
            filtered_s_df = df_s_processed[df_s_processed["날짜"].str.startswith(selected_month_s)]
        else:
            filtered_s_df = df_s_processed.copy() if not df_s_processed.empty else pd.DataFrame()

        item_options = ["전체 품목 보기"]
        if not filtered_s_df.empty and "품목" in filtered_s_df.columns:
            unique_items = [itm for itm in filtered_s_df["품목"].unique() if str(itm).strip() and str(itm).strip() != "-"]
            item_options.extend(unique_items)

        with col_f2: selected_item_s = st.selectbox("🥗 조회할 제품(품목)을 선택하세요", item_options, index=0, key="s_item_select")

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

    # [스윗밸런스 - 4. 박스 관리]
    with s_sub_tab4:
        st.subheader("📦 스윗밸런스 박스 산출 관리")
        df_s_raw = load_data("스윗밸런스")
        df_s_box = df_s_raw.copy()
        
        if not df_s_box.empty:
            if "품목" in df_s_box.columns: df_s_box["품목"] = df_s_box["품목"].apply(normalize_sweet_item_name)
            if "수량" in df_s_box.columns: df_s_box["수량"] = pd.to_numeric(df_s_box["수량"], errors='coerce').fillna(0).astype(int)
            if "날짜" in df_s_box.columns:
                df_s_box["날짜"] = df_s_box["날짜"].apply(parse_date_str)
                df_s_box = df_s_box[df_s_box["날짜"] != ""]
                
            col_b1, col_b2 = st.columns(2)
            valid_months_b = sorted([m[:7] for m in df_s_box["날짜"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_b else (valid_months_b[0] if valid_months_b else "")
            avail_m = [def_m] + [m for m in valid_months_b if m != def_m] + ["전체 보기"] if valid_months_b else ["전체 보기"]
            
            with col_b1: sel_m_b = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="box_month_select")
                
            if sel_m_b != "전체 보기": df_s_box = df_s_box[df_s_box["날짜"].str.startswith(sel_m_b)]
                
            box_item_options = ["전체 품목 보기"] + [i for i in df_s_box["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
            with col_b2: sel_i_b = st.selectbox("🥗 조회할 제품 선택", box_item_options, index=0, key="box_item_select")
                
            if sel_i_b != "전체 품목 보기": df_s_box = df_s_box[df_s_box["품목"] == sel_i_b]
                
            if not df_s_box.empty:
                boxes_df = df_s_box["수량"].apply(calc_sweet_boxes)
                df_s_box_calc = pd.concat([df_s_box, boxes_df], axis=1)
                
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
                
                # ---------------------------------------------------------
                # [추가] 스윗밸런스 박스 수불부 전송 버튼 영역
                # ---------------------------------------------------------
                st.markdown("---")
                st.markdown("##### 📦 수불부(Carrot 앱)로 스윗밸런스 박스 사용량 전송")
                st.info("💡 **중박스**(Full, 3~4개)는 `카톤박스(중)`으로, **소박스**(1~2개)는 `카톤박스(소)`로 자동 매칭되어 수불부로 넘어갑니다.")
                
                col_s_send1, col_s_send2 = st.columns([2, 1])
                with col_s_send1:
                    send_s_date = st.date_input("수불부로 전송할 출고일자 선택 (스윗밸런스)", get_kst_now().date(), key="send_s_date")
                with col_s_send2:
                    st.write("")
                    st.write("")
                    if st.button("🥗 선택한 날짜 박스 수불부로 전송", type="primary", use_container_width=True):
                        send_s_str = send_s_date.strftime("%Y-%m-%d")
                        
                        # 원본에서 해당 날짜를 뽑아서 정확하게 전체 제품 합산 계산
                        raw_s_for_send = load_data("스윗밸런스")
                        if not raw_s_for_send.empty and "날짜" in raw_s_for_send.columns:
                            raw_s_for_send["정제날짜"] = raw_s_for_send["날짜"].apply(parse_date_str)
                            day_data = raw_s_for_send[raw_s_for_send["정제날짜"] == send_s_str]
                            
                            if day_data.empty:
                                st.warning(f"[{send_s_str}] 자 스윗밸런스 출고 내역이 없습니다.")
                            else:
                                day_data["수량"] = pd.to_numeric(day_data["수량"], errors='coerce').fillna(0).astype(int)
                                boxes_day_df = day_data["수량"].apply(calc_sweet_boxes)
                                
                                mid_boxes = int(boxes_day_df["중박스(Full)"].sum() + boxes_day_df["중박스(3~4개)"].sum())
                                small_boxes = int(boxes_day_df["소박스(1~2개)"].sum())
                                
                                sent_any = False
                                if mid_boxes > 0:
                                    if send_to_subul_inventory(send_s_str, "카톤박스(중)", mid_boxes, "스윗밸런스 출고 (중박스)"):
                                        st.success(f"✅ 카톤박스(중) {mid_boxes}개 전송 완료!")
                                        sent_any = True
                                if small_boxes > 0:
                                    if send_to_subul_inventory(send_s_str, "카톤박스(소)", small_boxes, "스윗밸런스 출고 (소박스)"):
                                        st.success(f"✅ 카톤박스(소) {small_boxes}개 전송 완료!")
                                        sent_any = True
                                
                                if not sent_any:
                                    st.info("해당 날짜에 계산된 박스 수량이 없어 전송하지 않았습니다.")
                        else:
                            st.warning("스윗밸런스 데이터가 존재하지 않습니다.")

            else:
                st.info("조회 조건에 해당되는 데이터가 없습니다.")
        else:
            st.info("스윗밸런스 발주 데이터가 존재하지 않습니다.")

    # [스윗밸런스 - 5. 센터 관리]
    with s_sub_tab5:
        st.subheader("🏭 스윗밸런스 센터 관리")
        df_s_center = load_data("스윗밸런스")
        if not df_s_center.empty:
            if "품목" in df_s_center.columns: df_s_center["품목"] = df_s_center["품목"].apply(normalize_sweet_item_name)
            if "수량" in df_s_center.columns: df_s_center["수량"] = pd.to_numeric(df_s_center["수량"], errors='coerce').fillna(0).astype(int)
            if "날짜" in df_s_center.columns:
                df_s_center["날짜"] = df_s_center["날짜"].apply(parse_date_str)
                df_s_center = df_s_center[df_s_center["날짜"] != ""]
            
            for c in ["센터", "품목"]:
                if c not in df_s_center.columns: df_s_center[c] = "미지정"
            
            col_c1, col_c2 = st.columns(2)
            valid_months_c = sorted([m[:7] for m in df_s_center["날짜"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_c else (valid_months_c[0] if valid_months_c else "")
            avail_m = [def_m] + [m for m in valid_months_c if m != def_m] + ["전체 보기"] if valid_months_c else ["전체 보기"]
            
            with col_c1: sel_m_c = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="center_month_select")
            if sel_m_c != "전체 보기": df_s_center = df_s_center[df_s_center["날짜"].str.startswith(sel_m_c)]
            
            center_item_options = ["전체 품목 보기"] + [i for i in df_s_center["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
            with col_c2: sel_i_c = st.selectbox("🥗 조회할 제품 선택", center_item_options, index=0, key="center_item_select")
            if sel_i_c != "전체 품목 보기": df_s_center = df_s_center[df_s_center["품목"] == sel_i_c]
            
            if not df_s_center.empty:
                boxes_c_df = df_s_center["수량"].apply(calc_sweet_boxes)
                df_s_center_calc = pd.concat([df_s_center, boxes_c_df], axis=1)
                center_summary = df_s_center_calc.groupby(["날짜", "센터", "품목"], as_index=False)[["수량", "라벨 수량"]].sum()
                center_summary = center_summary.rename(columns={"센터": "물류 센터명", "품목": "제품명", "수량": "총 발주 수량", "라벨 수량": "총 박스 수량(라벨 수)"})
                center_summary = center_summary.sort_values(by=["날짜", "물류 센터명"])
                
                st.markdown(f"##### 🏭 센터별 물류라벨 및 박스 수량 ({sel_m_c} / {sel_i_c})")
                st.dataframe(center_summary.style.apply(highlight_next_day, axis=1), use_container_width=True)

    # [스윗밸런스 - 6. 외포장일지]
    with s_sub_tab6:
        st.subheader("📋 스윗밸런스 외포장 작업일지")
        df_s_log = load_data("스윗밸런스")
        if not df_s_log.empty:
            if "품목" in df_s_log.columns: df_s_log["품목"] = df_s_log["품목"].apply(normalize_sweet_item_name)
            if "수량" in df_s_log.columns: df_s_log["수량"] = pd.to_numeric(df_s_log["수량"], errors='coerce').fillna(0).astype(int)
            if "날짜" in df_s_log.columns:
                df_s_log["날짜"] = df_s_log["날짜"].apply(parse_date_str)
                df_s_log = df_s_log[df_s_log["날짜"] != ""]
            
            col_l1, col_l2 = st.columns(2)
            valid_months_l = sorted([m[:7] for m in df_s_log["날짜"].unique() if len(m) >= 7], reverse=True)
            current_m = get_kst_now().strftime("%Y-%m")
            def_m = current_m if current_m in valid_months_l else (valid_months_l[0] if valid_months_l else "")
            avail_m = [def_m] + [m for m in valid_months_l if m != def_m] + ["전체 보기"] if valid_months_l else ["전체 보기"]

            with col_l1: sel_m_l = st.selectbox("📅 조회할 월 선택", avail_m, index=0, key="log_sub_month_select")
            if sel_m_l != "전체 보기": df_s_log = df_s_log[df_s_log["날짜"].str.startswith(sel_m_l)]

            log_item_options = ["전체 품목 보기"] + [i for i in df_s_log["품목"].unique() if str(i).strip() and str(i).strip() != "-"]
            with col_l2: sel_i_l = st.selectbox("🥗 조회할 제품 선택", log_item_options, index=0, key="log_sub_item_select")
            if sel_i_l != "전체 품목 보기": df_s_log = df_s_log[df_s_log["품목"] == sel_i_l]

            if not df_s_log.empty:
                boxes_detail_df = df_s_log["수량"].apply(calc_sweet_boxes_detail)
                df_s_log_calc = pd.concat([df_s_log, boxes_detail_df], axis=1)
                log_summary = df_s_log_calc.groupby(["날짜", "품목"], as_index=False)[
                    ["수량", "중박스(5개-Full)", "중박스(4개)", "중박스(3개)", "소박스(2개)", "소박스(1개)", "총 박스(라벨) 수"]
                ].sum()
                log_summary["소비기한"] = log_summary["날짜"].apply(calc_sweet_exp_date)
                
                log_summary = log_summary[["날짜", "품목", "수량", "소비기한", "중박스(5개-Full)", "중박스(4개)", "중박스(3개)", "소박스(2개)", "소박스(1개)", "총 박스(라벨) 수"]]
                st.markdown(f"##### 📦 외포장 작업일지 세부 집계표 ({sel_m_l} / {sel_i_l})")
                st.dataframe(log_summary.style.apply(highlight_next_day, axis=1), use_container_width=True)

# ==========================================
# --- [메인 화면 3, 4, 5는 기존 코드와 동일하여 그대로 유지] ---
# ==========================================
elif selected_menu == "📑 거래명세서 발행":
    st.title("📑 거래명세서 발행 및 출력")
    # (기존 거래명세서 코드를 이 위치에 그대로 유지하시면 됩니다. 변경사항 없음)
    st.info("명세서 코드는 글자 수 제한으로 인해 원본의 코드를 그대로 유지해 주시면 됩니다.")

elif selected_menu == "📋 외포장 공정일지 (양식)":
    st.title("📋 외포장 공정일지")
    # (기존 외포장 공정일지 코드를 이 위치에 그대로 유지하시면 됩니다. 변경사항 없음)
    st.info("공정일지 코드는 글자 수 제한으로 인해 원본의 코드를 그대로 유지해 주시면 됩니다.")

elif selected_menu == "⚙️ 설정 (기초정보 & 단가)":
    st.title("⚙️ 거래명세서 기초 정보 및 단가 관리")
    # (기존 설정 코드를 이 위치에 그대로 유지하시면 됩니다. 변경사항 없음)
    st.info("설정 코드는 글자 수 제한으로 인해 원본의 코드를 그대로 유지해 주시면 됩니다.")

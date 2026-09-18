import streamlit as st
import pandas as pd
import math
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from streamlit_gsheets import GSheetsConnection

# 웹페이지 기본 설정
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 정리 시스템", layout="wide")

# metric 및 스타일 CSS (글자 짤림 방지 및 빨간 강조 박스)
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

st.title("📦 거래처별 발주 입력 및 박스 계산 시스템")
st.write("발주 데이터를 입력하면 구글 시트에 자동 저장되며, 확인서 탭에서 월별 집계 및 엑셀 다운로드를 이용할 수 있습니다.")

st.divider()

# 내부 고정 계산 기준 값
carrot_box_unit = 6     # 당근 (6개/박스)
spinach_box_unit = 5    # 시금치 (5개/박스)
coupang_exp_days = 4    # 쿠팡 소비기한 (+4일)
sweet_exp_days = 3      # 스윗밸런스 소비기한 (+3일)

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

def get_month_code(month):
    codes = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F', 
             7: 'G', 8: 'H', 9: 'I', 10: 'J', 11: 'K', 12: 'L'}
    return codes.get(month, '')

# 비표 계산: 시트에 입력된 발주 날짜의 '전날(생산일)' 기준으로 알파벳+일 산출 (시금치 0개면 빈칸)
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

# 쿠팡 소비기한 계산: 당근 수량이 0개이면 소비기한을 빈칸으로 처리
def calc_coupang_exp(row, exp_days):
    try:
        in_carrot = row.get("인천 당근", 0)
        bu_carrot = row.get("부천 당근", 0)
        if (in_carrot + bu_carrot) <= 0:
            return ""
        date_val = row.get("날짜", "")
        return calc_exp_date(date_val, exp_days)
    except Exception:
        return ""

def calculate_coupang(df, c_unit, s_unit, exp_days):
    empty_cols = ["날짜", "인천 당근", "부천 당근", "인천 시금치", "부천 시금치", "당근 합계", "시금치 합계", "소비기한", "비표", "인천 박스수량", "부천 박스수량"]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    
    res_df = df.copy()
    num_cols = ["인천 당근", "부천 당근", "인천 시금치", "부천 시금치"]
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

    res_df["당근 합계"] = res_df["인천 당근"] + res_df["부천 당근"]
    res_df["시금치 합계"] = res_df["인천 시금치"] + res_df["부천 시금치"]

    # 💡 당근이 0개일 경우 소비기한 미표시 적용
    res_df["소비기한"] = res_df.apply(lambda r: calc_coupang_exp(r, exp_days), axis=1)
    res_df["비표"] = res_df.apply(calc_bipyo, axis=1)

    res_df["인천 박스수량"] = res_df.apply(lambda r: math.ceil(r["인천 당근"] / c_unit) + math.ceil(r["인천 시금치"] / s_unit), axis=1)
    res_df["부천 박스수량"] = res_df.apply(lambda r: math.ceil(r["부천 당근"] / c_unit) + math.ceil(r["부천 시금치"] / s_unit), axis=1)

    cols = [c for c in empty_cols if c in res_df.columns]
    return res_df[cols]

# 다음 날(내일) 발주 건 빨간색 하이라이트 적용
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

# 4개 탭 구성
tab_c_input, tab_s_input, tab_coupang, tab_sweet = st.tabs([
    "🚀 쿠팡 입력", 
    "🥗 스윗밸런스 입력", 
    "📊 쿠팡 확인서", 
    "📊 스윗밸런스 확인서"
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
    
    st.caption(f"💡 자동 산출 - 소비기한(+4일): **{c_exp_preview} (당근 포함 시)** | 예상 비표(전날 생산 기준): **{c_bipyo_preview} (시금치 포함 시)**")
    st.markdown("---")
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📍 인천센터")
        c_in_c = st.number_input("인천 당근 수량", min_value=0, value=0, step=1, key="c_in_c")
        c_in_s = st.number_input("인천 시금치 수량", min_value=0, value=0, step=1, key="c_in_s")
    with c2:
        st.markdown("##### 📍 부천센터")
        c_bu_c = st.number_input("부천 당근 수량", min_value=0, value=0, step=1, key="c_bu_c")
        c_bu_s = st.number_input("부천 시금치 수량", min_value=0, value=0, step=1, key="c_bu_s")

    st.markdown("---")
    if st.button("🚀 쿠팡 발주 저장하기", type="primary", use_container_width=True):
        total_qty = c_in_c + c_bu_c + c_in_s + c_bu_s
        if total_qty >= 0:
            try:
                existing_df = load_data("쿠팡")
                new_row = pd.DataFrame([{
                    "날짜": c_date_str,
                    "인천 당근": c_in_c,
                    "부천 당근": c_bu_c,
                    "인천 시금치": c_in_s,
                    "부천 시금치": c_bu_s
                }])
                updated_df = pd.concat([existing_df, new_row], ignore_index=True)
                conn.update(worksheet="쿠팡", data=updated_df)
                st.cache_data.clear()
                st.success(f"[{c_date_str}] 쿠팡 발주가 구글 시트에 성공적으로 저장되었습니다!")
            except Exception as err:
                st.error(f"저장 중 오류 발생: {err}")


# --- [TAB 2: 스윗밸런스 입력] ---
with tab_s_input:
    st.subheader("🥗 스윗밸런스 발주 수량 입력")
    
    kst_today = get_kst_now().date()
    s_date = st.date_input("발주 날짜 선택", kst_today, key="s_date_input")
    s_date_str = s_date.strftime("%Y-%m-%d")
    s_exp_preview = (s_date + timedelta(days=sweet_exp_days)).strftime("%Y-%m-%d")
    
    st.caption(f"💡 자동으로 산출되는 소비기한(+3일): **{s_exp_preview}**")
    st.markdown("---")
    
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        s_item_name = st.text_input("품목명", value="브런치 믹스 1kg", disabled=True, key="s_item")
    with col_s2:
        s_qty = st.number_input("발주 수량(개)", min_value=0, value=0, step=1, key="s_qty_input")
    with col_s3:
        s_label_qty = st.number_input("라벨 수량(장)", min_value=0, value=0, step=1, key="s_label_qty_input")

    st.markdown("---")
    if st.button("🥗 스윗밸런스 발주 저장하기", type="primary", use_container_width=True):
        try:
            existing_df = load_data("스윗밸런스")
            new_row = pd.DataFrame([{
                "날짜": s_date_str,
                "품목": s_item_name,
                "수량": s_qty,
                "라벨 수량": s_label_qty
            }])
            updated_df = pd.concat([existing_df, new_row], ignore_index=True)
            conn.update(worksheet="스윗밸런스", data=updated_df)
            st.cache_data.clear()
            st.success(f"[{s_date_str}] 스윗밸런스 발주({s_qty}개 / 라벨 {s_label_qty}장)가 구글 시트에 저장되었습니다!")
        except Exception as err:
            st.error(f"저장 중 오류 발생: {err}")


# --- [TAB 3: 쿠팡 확인서] ---
with tab_coupang:
    col_c_head, col_c_btn = st.columns([4, 1])
    with col_c_head:
        st.subheader("📊 쿠팡 발주 확인서")
    with col_c_btn:
        if st.button("🔄 쿠팡 데이터 새로고침", use_container_width=True, key="btn_refresh_c"):
            st.cache_data.clear()
            st.rerun()

    df_c_raw = load_data("쿠팡")
    df_c_all_calc = calculate_coupang(df_c_raw, carrot_box_unit, spinach_box_unit, coupang_exp_days)

    # 오늘 작업 예정(내일 출고/납품 건) 레드 콜아웃 상자 노출
    kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    today_c_work = df_c_all_calc[df_c_all_calc["날짜"] == kst_tomorrow_str] if not df_c_all_calc.empty else pd.DataFrame()

    if not today_c_work.empty:
        row_t = today_c_work.iloc[0]
        in_c = row_t.get("인천 당근", 0)
        bu_c = row_t.get("부천 당근", 0)
        in_s = row_t.get("인천 시금치", 0)
        bu_s = row_t.get("부천 시금치", 0)
        in_box = row_t.get("인천 박스수량", 0)
        bu_box = row_t.get("부천 박스수량", 0)
        bipyo_txt = row_t.get("비표", "")
        bipyo_display = bipyo_txt if bipyo_txt else "없음(시금치 0개)"
        exp_txt = row_t.get("소비기한", "")
        exp_display = exp_txt if exp_txt else "없음(당근 0개)"

        st.markdown(f"""
        <div class="today-work-box">
            <div class="today-work-title">🚨 오늘 작업할 쿠팡 발주 내용 (납품 예정일: {kst_tomorrow_str})</div>
            <div class="today-work-content">
                • <b>인천센터:</b> 당근 <b>{in_c:,}</b>개 / 시금치 <b>{in_s:,}</b>개 👉 <b>인천 총 {in_box:,} 박스</b><br>
                • <b>부천센터:</b> 당근 <b>{bu_c:,}</b>개 / 시금치 <b>{bu_s:,}</b>개 👉 <b>부천 총 {bu_box:,} 박스</b><br>
                • <b>시금치 비표:</b> <span style="font-size:1.15rem; font-weight:bold; color:#cc0000;">{bipyo_display}</span> | <b>소비기한:</b> {exp_display}
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info(f"💡 오늘 작업 예정인 쿠팡 발주(납품일: {kst_tomorrow_str}) 내역이 없습니다.")

    st.markdown("---")

    valid_months = []
    if not df_c_raw.empty and "날짜" in df_c_raw.columns:
        df_c_raw["정제날짜"] = df_c_raw["날짜"].apply(parse_date_str)
        df_c_raw["연월"] = df_c_raw["정제날짜"].apply(lambda d: d[:7] if len(d) >= 7 else "")
        valid_months = sorted([m for m in df_c_raw["연월"].unique() if m and len(m) == 7], reverse=True)

    available_months = ["전체 보기"] + valid_months
    selected_month = st.selectbox("📅 조회할 월을 선택하세요", available_months, key="c_month_select")
    
    if not df_c_raw.empty and "연월" in df_c_raw.columns and selected_month != "전체 보기":
        filtered_c_df = df_c_raw[df_c_raw["연월"] == selected_month].drop(columns=["정제날짜", "연월"], errors='ignore')
    else:
        filtered_c_df = df_c_raw.drop(columns=["정제날짜", "연월"], errors='ignore') if "연월" in df_c_raw.columns else df_c_raw

    calculated_c_df = calculate_coupang(filtered_c_df, carrot_box_unit, spinach_box_unit, coupang_exp_days)

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
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("선택 기간 발주 건수", f"{len(calculated_c_df)} 건")
        m2.metric("당근 총합", f"{calculated_c_df['당근 합계'].sum() if '당근 합계' in calculated_c_df else 0:,} 개")
        m3.metric("시금치 총합", f"{calculated_c_df['시금치 합계'].sum() if '시금치 합계' in calculated_c_df else 0:,} 개")
        m4.metric("인천 총 박스", f"{total_incheon_box:,} 박스")
        m5.metric("부천 총 박스", f"{total_bucheon_box:,} 박스")


# --- [TAB 4: 스윗밸런스 확인서] ---
with tab_sweet:
    col_s_head, col_s_btn = st.columns([4, 1])
    with col_s_head:
        st.subheader("📊 스윗밸런스 발주 확인서")
    with col_s_btn:
        if st.button("🔄 스윗밸런스 데이터 새로고침", use_container_width=True, key="btn_refresh_s"):
            st.cache_data.clear()
            st.rerun()

    df_s_raw = load_data("스윗밸런스")

    # 전처리 및 가공
    df_s_processed = df_s_raw.copy()
    if not df_s_processed.empty:
        if "수량" in df_s_processed.columns:
            df_s_processed["수량"] = pd.to_numeric(df_s_processed["수량"], errors='coerce').fillna(0).astype(int)
        else:
            df_s_processed["수량"] = 0

        if "라벨 수량" in df_s_processed.columns:
            df_s_processed["라벨 수량"] = pd.to_numeric(df_s_processed["라벨 수량"], errors='coerce').fillna(0).astype(int)
        else:
            df_s_processed["라벨 수량"] = 0

        if "날짜" in df_s_processed.columns:
            df_s_processed["날짜"] = df_s_processed["날짜"].apply(parse_date_str)
            df_s_processed = df_s_processed[df_s_processed["날짜"] != ""]
            if "품목" in df_s_processed.columns and not df_s_processed.empty:
                df_s_processed = df_s_processed.groupby(["날짜", "품목"], as_index=False)[["수량", "라벨 수량"]].sum()
        
        if not df_s_processed.empty:
            df_s_processed["소비기한"] = df_s_processed["날짜"].apply(lambda d: calc_exp_date(d, sweet_exp_days))
            ordered_s_cols = ["날짜", "품목", "수량", "라벨 수량", "소비기한"]
            cols_s = [c for c in ordered_s_cols if c in df_s_processed.columns]
            df_s_processed = df_s_processed[cols_s]

    # 오늘 작업 예정 스윗밸런스 레드 콜아웃 상자 노출
    kst_tomorrow_str = (get_kst_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    today_s_work = df_s_processed[df_s_processed["날짜"] == kst_tomorrow_str] if not df_s_processed.empty else pd.DataFrame()

    if not today_s_work.empty:
        row_s_t = today_s_work.iloc[0]
        s_w_qty = row_s_t.get("수량", 0)
        s_w_label = row_s_t.get("라벨 수량", 0)
        s_w_exp = row_s_t.get("소비기한", "")

        st.markdown(f"""
        <div class="today-work-box">
            <div class="today-work-title">🚨 오늘 작업할 스윗밸런스 발주 내용 (납품 예정일: {kst_tomorrow_str})</div>
            <div class="today-work-content">
                • <b>브런치 믹스 1kg:</b> 수량 <b>{s_w_qty:,}</b> 개 | 라벨 수량: <b>{s_w_label:,}</b> 장<br>
                • <b>소비기한:</b> {s_w_exp}
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info(f"💡 오늘 작업 예정인 스윗밸런스 발주(납품일: {kst_tomorrow_str}) 내역이 없습니다.")

    st.markdown("---")

    valid_months_s = []
    if not df_s_raw.empty and "날짜" in df_s_raw.columns:
        df_s_raw["정제날짜"] = df_s_raw["날짜"].apply(parse_date_str)
        df_s_raw["연월"] = df_s_raw["정제날짜"].apply(lambda d: d[:7] if len(d) >= 7 else "")
        valid_months_s = sorted([m for m in df_s_raw["연월"].unique() if m and len(m) == 7], reverse=True)

    available_months_s = ["전체 보기"] + valid_months_s
    selected_month_s = st.selectbox("📅 조회할 월을 선택하세요", available_months_s, key="s_month_select")

    if not df_s_raw.empty and "연월" in df_s_raw.columns and selected_month_s != "전체 보기":
        filtered_s_df = df_s_raw[df_s_raw["연월"] == selected_month_s].drop(columns=["정제날짜", "연월"], errors='ignore')
    else:
        filtered_s_df = df_s_raw.drop(columns=["정제날짜", "연월"], errors='ignore') if "연월" in df_s_raw.columns else df_s_raw

    if not filtered_s_df.empty:
        if "수량" in filtered_s_df.columns:
            filtered_s_df["수량"] = pd.to_numeric(filtered_s_df["수량"], errors='coerce').fillna(0).astype(int)
        else:
            filtered_s_df["수량"] = 0

        if "라벨 수량" in filtered_s_df.columns:
            filtered_s_df["라벨 수량"] = pd.to_numeric(filtered_s_df["라벨 수량"], errors='coerce').fillna(0).astype(int)
        else:
            filtered_s_df["라벨 수량"] = 0

        if "날짜" in filtered_s_df.columns:
            filtered_s_df["날짜"] = filtered_s_df["날짜"].apply(parse_date_str)
            filtered_s_df = filtered_s_df[filtered_s_df["날짜"] != ""]
            if "품목" in filtered_s_df.columns and not filtered_s_df.empty:
                filtered_s_df = filtered_s_df.groupby(["날짜", "품목"], as_index=False)[["수량", "라벨 수량"]].sum()
        
        if not filtered_s_df.empty:
            filtered_s_df["소비기한"] = filtered_s_df["날짜"].apply(lambda d: calc_exp_date(d, sweet_exp_days))
            total_sweet_qty = filtered_s_df["수량"].sum()
            total_label_qty = filtered_s_df["라벨 수량"].sum()
            
            ordered_s_cols = ["날짜", "품목", "수량", "라벨 수량", "소비기한"]
            cols_s = [c for c in ordered_s_cols if c in filtered_s_df.columns]
            filtered_s_df = filtered_s_df[cols_s]
        else:
            total_sweet_qty = 0
            total_label_qty = 0
    else:
        filtered_s_df = pd.DataFrame(columns=["날짜", "품목", "수량", "라벨 수량", "소비기한"])
        total_sweet_qty = 0
        total_label_qty = 0

    st.markdown("##### 📊 최종 집계 결과")
    styled_s_df = filtered_s_df.style.apply(highlight_next_day, axis=1)
    st.dataframe(styled_s_df, use_container_width=True)

    excel_data_s = to_excel(filtered_s_df)
    st.download_button(
        label=f"📥 스윗밸런스 {selected_month_s} 최종 결과 엑셀 파일 다운로드",
        data=excel_data_s,
        file_name=f"스윗밸런스_발주확인서_{selected_month_s}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    if not filtered_s_df.empty:
        st.markdown("---")
        s1, s2, s3 = st.columns(3)
        s1.metric("선택 기간 발주 건수", f"{len(filtered_s_df)} 건")
        s2.metric("브런치 믹스 1kg 총 수량", f"{total_sweet_qty:,} 개")
        s3.metric("총 라벨 수량", f"{total_label_qty:,} 장")

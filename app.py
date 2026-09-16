import streamlit as st
import pandas as pd
import math
from datetime import datetime, timedelta
from io import BytesIO
from streamlit_gsheets import GSheetsConnection

# 웹페이지 기본 설정
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 정리 시스템", layout="wide")

st.title("📦 거래처별 발주 입력 및 박스 계산 시스템")
st.write("발주 데이터를 입력하면 구글 시트에 저장되며, 월별 조회, 박스/비표 자동 계산 및 엑셀 다운로드를 지원합니다.")

st.divider()

# 내부 고정 계산 기준 값
carrot_box_unit = 6     # 당근 (6개/박스)
spinach_box_unit = 5    # 시금치 (5개/박스)
coupang_exp_days = 4    # 쿠팡 소비기한 (+4일)
sweet_exp_days = 3      # 스윗밸런스 소비기한 (+3일)

# 구글 시트 연결
conn = st.connection("gsheets", type=GSheetsConnection)

def load_data(worksheet_name):
    try:
        df = conn.read(worksheet=worksheet_name, ttl=0)
        return df.dropna(how="all")
    except Exception as e:
        st.error(f"[{worksheet_name}] 시트 로딩 중 오류 발생: {e}")
        return pd.DataFrame()

def calc_exp_date(date_val, days):
    try:
        if pd.isna(date_val) or str(date_val).strip() == "":
            return ""
        dt = pd.to_datetime(date_val)
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
        if pd.isna(date_val) or str(date_val).strip() == "":
            return ""
        dt = pd.to_datetime(date_val)
        return f"{get_month_code(dt.month)}{dt.day}"
    except Exception:
        return ""

def calculate_coupang(df, c_unit, s_unit, exp_days):
    if df.empty:
        return pd.DataFrame(columns=["날짜", "인천 당근", "부천 당근", "인천 시금치", "부천 시금치", "당근 합계", "시금치 합계", "소비기한", "비표", "인천 박스수량", "부천 박스수량"])
    
    res_df = df.copy()
    num_cols = ["인천 당근", "부천 당근", "인천 시금치", "부천 시금치"]
    for col in num_cols:
        if col in res_df.columns:
            res_df[col] = pd.to_numeric(res_df[col], errors='coerce').fillna(0).astype(int)
        else:
            res_df[col] = 0

    if "날짜" in res_df.columns:
        res_df["날짜"] = pd.to_datetime(res_df["날짜"], errors='coerce').dt.strftime('%Y-%m-%d')
        res_df = res_df.groupby("날짜", as_index=False)[num_cols].sum()

    res_df["당근 합계"] = res_df["인천 당근"] + res_df["부천 당근"]
    res_df["시금치 합계"] = res_df["인천 시금치"] + res_df["부천 시금치"]

    if "날짜" in res_df.columns:
        res_df["소비기한"] = res_df["날짜"].apply(lambda d: calc_exp_date(d, exp_days))
        res_df["비표"] = res_df.apply(calc_bipyo, axis=1)
    else:
        res_df["소비기한"] = ""
        res_df["비표"] = ""

    res_df["인천 박스수량"] = res_df.apply(lambda r: math.ceil(r["인천 당근"] / c_unit) + math.ceil(r["인천 시금치"] / s_unit), axis=1)
    res_df["부천 박스수량"] = res_df.apply(lambda r: math.ceil(r["부천 당근"] / c_unit) + math.ceil(r["부천 시금치"] / s_unit), axis=1)

    ordered_cols = ["날짜", "인천 당근", "부천 당근", "인천 시금치", "부천 시금치", "당근 합계", "시금치 합계", "소비기한", "비표", "인천 박스수량", "부천 박스수량"]
    cols = [c for c in ordered_cols if c in res_df.columns]
    return res_df[cols]

# 엑셀 변환 함수
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
    
    c_date = st.date_input("발주 날짜 선택", datetime.now(), key="c_date_input")
    c_date_str = c_date.strftime("%Y-%m-%d")
    c_exp_preview = (c_date + timedelta(days=coupang_exp_days)).strftime("%Y-%m-%d")
    c_bipyo_preview = f"{get_month_code(c_date.month)}{c_date.day}"
    
    st.caption(f"💡 자동 산출 - 소비기한(+4일): **{c_exp_preview}** | 예상 비표(시금치 포함 시): **{c_bipyo_preview}**")
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
        if c_in_c > 0 or c_bu_c > 0 or c_in_s > 0 or c_bu_s > 0:
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
            st.success(f"[{c_date_str}] 쿠팡 발주가 구글 시트에 저장되었습니다!")
        else:
            st.warning("수량을 1개 이상 입력해 주세요.")


# --- [TAB 2: 스윗밸런스 입력] ---
with tab_s_input:
    st.subheader("🥗 스윗밸런스 발주 수량 입력")
    
    s_date = st.date_input("발주 날짜 선택", datetime.now(), key="s_date_input")
    s_date_str = s_date.strftime("%Y-%m-%d")
    s_exp_preview = (s_date + timedelta(days=sweet_exp_days)).strftime("%Y-%m-%d")
    
    st.caption(f"💡 자동으로 산출되는 소비기한(+3일): **{s_exp_preview}**")
    st.markdown("---")
    
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        s_item_name = st.text_input("품목명", value="브런치 믹스 1kg", disabled=True, key="s_item")
    with col_s2:
        s_qty = st.number_input("발주 수량(개)", min_value=0, value=0, step=1, key="s_qty_input")

    st.markdown("---")
    if st.button("🥗 스윗밸런스 발주 저장하기", type="primary", use_container_width=True):
        if s_qty > 0:
            existing_df = load_data("스윗밸런스")
            new_row = pd.DataFrame([{
                "날짜": s_date_str,
                "품목": s_item_name,
                "수량": s_qty
            }])
            updated_df = pd.concat([existing_df, new_row], ignore_index=True)
            conn.update(worksheet="스윗밸런스", data=updated_df)
            st.success(f"[{s_date_str}] 스윗밸런스 발주가 구글 시트에 저장되었습니다!")
        else:
            st.warning("수량을 1개 이상 입력해 주세요.")


# --- [TAB 3: 쿠팡 확인서] ---
with tab_coupang:
    st.subheader("📊 쿠팡 발주 확인서")
    
    df_c_raw = load_data("쿠팡")

    if not df_c_raw.empty and "날짜" in df_c_raw.columns:
        dt_series = pd.to_datetime(df_c_raw["날짜"], errors='coerce')
        df_c_raw["날짜"] = dt_series.dt.strftime('%Y-%m-%d')
        df_c_raw["연월"] = dt_series.dt.strftime('%Y-%m')
        
        valid_months = sorted([m for m in df_c_raw["연월"].dropna().unique() if m and str(m) != "nan"], reverse=True)
        available_months = ["전체 보기"] + valid_months
        
        selected_month = st.selectbox("📅 조회할 월을 선택하세요", available_months, key="c_month_select")
        
        if selected_month != "전체 보기":
            filtered_c_df = df_c_raw[df_c_raw["연월"] == selected_month].drop(columns=["연월"])
        else:
            filtered_c_df = df_c_raw.drop(columns=["연월"], errors='ignore')
    else:
        filtered_c_df = df_c_raw

    st.markdown("##### 📝 구글 시트 데이터 조회 및 편집")
    edited_c_df = st.data_editor(filtered_c_df, num_rows="dynamic", use_container_width=True, key="c_editor")

    if st.button("💾 쿠팡 수정사항 구글 시트에 반영", key="btn_save_c"):
        conn.update(worksheet="쿠팡", data=edited_c_df)
        st.success("구글 시트에 성공적으로 업데이트되었습니다!")

    # 최종 박스 및 비표 집계
    calculated_c_df = calculate_coupang(edited_c_df, carrot_box_unit, spinach_box_unit, coupang_exp_days)

    st.markdown("---")
    st.markdown("##### 📊 최종 집계 및 박스 수량 결과")
    st.dataframe(calculated_c_df, use_container_width=True)

    # 📥 엑셀 다운로드 버튼 (언제나 표기)
    excel_data_c = to_excel(calculated_c_df)
    st.download_button(
        label="📥 쿠팡 최종 결과 엑셀 파일 다운로드",
        data=excel_data_c,
        file_name=f"쿠팡_발주확인서_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    if not calculated_c_df.empty:
        total_incheon_box = calculated_c_df["인천 박스수량"].sum() if "인천 박스수량" in calculated_c_df else 0
        total_bucheon_box = calculated_c_df["부천 박스수량"].sum() if "부천 박스수량" in calculated_c_df else 0
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("선택 기간 발주 건수", f"{len(calculated_c_df)} 건")
        m2.metric("당근 총합", f"{calculated_c_df['당근 합계'].sum() if '당근 합계' in calculated_c_df else 0:,} 개")
        m3.metric("시금치 총합", f"{calculated_c_df['시금치 합계'].sum() if '시금치 합계' in calculated_c_df else 0:,} 개")
        m4.metric("총 박스 수량", f"인천: {total_incheon_box} / 부천: {total_bucheon_box} 박스")


# --- [TAB 4: 스윗밸런스 확인서] ---
with tab_sweet:
    st.subheader("📊 스윗밸런스 발주 확인서")
    
    df_s_raw = load_data("스윗밸런스")

    if not df_s_raw.empty and "날짜" in df_s_raw.columns:
        dt_series_s = pd.to_datetime(df_s_raw["날짜"], errors='coerce')
        df_s_raw["날짜"] = dt_series_s.dt.strftime('%Y-%m-%d')
        df_s_raw["연월"] = dt_series_s.dt.strftime('%Y-%m')
        
        valid_months_s = sorted([m for m in df_s_raw["연월"].dropna().unique() if m and str(m) != "nan"], reverse=True)
        available_months_s = ["전체 보기"] + valid_months_s
        
        selected_month_s = st.selectbox("📅 조회할 월을 선택하세요", available_months_s, key="s_month_select")
        
        if selected_month_s != "전체 보기":
            filtered_s_df = df_s_raw[df_s_raw["연월"] == selected_month_s].drop(columns=["연월"])
        else:
            filtered_s_df = df_s_raw.drop(columns=["연월"], errors='ignore')
    else:
        filtered_s_df = df_s_raw

    st.markdown("##### 📝 구글 시트 데이터 조회 및 편집")
    edited_s_df = st.data_editor(filtered_s_df, num_rows="dynamic", use_container_width=True, key="s_editor")

    if st.button("💾 스윗밸런스 수정사항 구글 시트에 반영", key="btn_save_s"):
        conn.update(worksheet="스윗밸런스", data=edited_s_df)
        st.success("구글 시트에 성공적으로 업데이트되었습니다!")

    if not edited_s_df.empty and "수량" in edited_s_df.columns:
        edited_s_df["수량"] = pd.to_numeric(edited_s_df["수량"], errors='coerce').fillna(0).astype(int)
        if "날짜" in edited_s_df.columns and "품목" in edited_s_df.columns:
            edited_s_df["날짜"] = pd.to_datetime(edited_s_df["날짜"], errors='coerce').dt.strftime('%Y-%m-%d')
            edited_s_df = edited_s_df.groupby(["날짜", "품목"], as_index=False)["수량"].sum()
        edited_s_df["소비기한"] = edited_s_df["날짜"].apply(lambda d: calc_exp_date(d, sweet_exp_days))
        total_sweet_qty = edited_s_df["수량"].sum()
    else:
        total_sweet_qty = 0

    st.markdown("---")
    st.markdown("##### 📊 선택 월 최종 결과")
    st.dataframe(edited_s_df, use_container_width=True)

    # 📥 엑셀 다운로드 버튼 (언제나 표기)
    excel_data_s = to_excel(edited_s_df)
    st.download_button(
        label="📥 스윗밸런스 최종 결과 엑셀 파일 다운로드",
        data=excel_data_s,
        file_name=f"스윗밸런스_발주확인서_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    if not edited_s_df.empty:
        s1, s2 = st.columns(2)
        s1.metric("선택 기간 발주 건수", f"{len(edited_s_df)} 건")
        s2.metric("브런치 믹스 1kg 총 수량", f"{total_sweet_qty:,} 개")

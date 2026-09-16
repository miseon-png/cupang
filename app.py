import streamlit as st
import pandas as pd
import math
from datetime import datetime, timedelta

# 웹페이지 기본 설정
st.set_page_config(page_title="쿠팡 & 스윗밸런스 발주 정리 시스템", layout="wide")

st.title("📦 발주 정리 및 박스 계산 시스템")
st.write("발주 데이터를 수동 입력하거나 엑셀로 관리하며 소비기한과 박스 수량을 자동 계산합니다.")

st.divider()

# 박스 입수량 및 소비기한 일수 설정 (사이드바)
st.sidebar.header("⚙️ 계산 기준 설정")
carrot_box_unit = st.sidebar.number_input("당근 (개/박스)", min_value=1, value=6)
spinach_box_unit = st.sidebar.number_input("시금치 (개/박스)", min_value=1, value=5)
st.sidebar.markdown("---")
coupang_exp_days = st.sidebar.number_input("쿠팡 소비기한 (+일수)", min_value=0, value=4)
sweet_exp_days = st.sidebar.number_input("스윗밸런스 소비기한 (+일수)", min_value=0, value=3)

# 세션 상태(Session State) 초기화 - 데이터 저장소
if "coupang_list" not in st.session_state:
    st.session_state.coupang_list = [
        {"날짜": "2026-09-01", "인천 당근": 18, "부천 당근": 12, "인천 시금치": 15, "부천 시금치": 15},
        {"날짜": "2026-09-02", "인천 당근": 12, "부천 당근": 12, "인천 시금치": 20, "부천 시금치": 20}
    ]

if "sweet_list" not in st.session_state:
    st.session_state.sweet_list = [
        {"날짜": "2026-09-06", "품목": "브런치 믹스 1kg", "수량": 101},
        {"날짜": "2026-09-07", "품목": "브런치 믹스 1kg", "수량": 166}
    ]

# 날짜에 일수를 더하는 계산 함수
def calc_exp_date(date_val, days):
    try:
        if pd.isna(date_val) or date_val == "" or str(date_val).strip() == "":
            return ""
        dt = pd.to_datetime(date_val)
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""

# 쿠팡 집계 및 박스 계산 함수
def calculate_coupang(df, c_unit, s_unit, exp_days):
    res_df = df.copy()
    num_cols = ["인천 당근", "부천 당근", "인천 시금치", "부천 시금치"]
    for col in num_cols:
        if col in res_df.columns:
            res_df[col] = pd.to_numeric(res_df[col], errors='coerce').fillna(0).astype(int)
        else:
            res_df[col] = 0

    res_df["당근 합계"] = res_df["인천 당근"] + res_df["부천 당근"]
    res_df["시금치 합계"] = res_df["인천 시금치"] + res_df["부천 시금치"]

    # 소비기한 자동 계산 (+4일)
    if "날짜" in res_df.columns:
        res_df["소비기한"] = res_df["날짜"].apply(lambda d: calc_exp_date(d, exp_days))
    else:
        res_df["소비기한"] = ""

    # 박스 수량 계산 (올림 처리)
    res_df["인천 박스수량"] = res_df.apply(lambda r: math.ceil(r["인천 당근"] / c_unit) + math.ceil(r["인천 시금치"] / s_unit), axis=1)
    res_df["부천 박스수량"] = res_df.apply(lambda r: math.ceil(r["부천 당근"] / c_unit) + math.ceil(r["부천 시금치"] / s_unit), axis=1)

    ordered_cols = ["날짜", "인천 당근", "부천 당근", "인천 시금치", "부천 시금치", "당근 합계", "시금치 합계", "소비기한", "인천 박스수량", "부천 박스수량"]
    cols = [c for c in ordered_cols if c in res_df.columns]
    return res_df[cols]

# 탭 구성
tab_input, tab_coupang, tab_sweet = st.tabs(["✍️ 발주 수량 입력", "🚀 쿠팡 발주 확인서", "🥗 스윗밸런스 발주 확인서"])


# --- [TAB 1: 발주 수량 입력] ---
with tab_input:
    st.subheader("✍️ 발주 데이터 입력")
    
    order_date = st.date_input("발주 날짜 선택", datetime.now())
    date_str = order_date.strftime("%Y-%m-%d")
    
    # 자동 산출되는 소비기한 안내 표시
    c_exp_preview = (order_date + timedelta(days=coupang_exp_days)).strftime("%Y-%m-%d")
    s_exp_preview = (order_date + timedelta(days=sweet_exp_days)).strftime("%Y-%m-%d")
    
    st.caption(f"💡 쿠팡 소비기한(자동 +4일): **{c_exp_preview}** | 스윗밸런스 소비기한(자동 +3일): **{s_exp_preview}**")

    st.markdown("---")
    
    col_c, col_s = st.columns(2)
    
    # 쿠팡 수량 입력 Form
    with col_c:
        st.markdown("#### 🚀 쿠팡 수량 입력")
        c_incheon_carrot = st.number_input("인천센터 당근 수량", min_value=0, value=0, step=1)
        c_bucheon_carrot = st.number_input("부천센터 당근 수량", min_value=0, value=0, step=1)
        c_incheon_spinach = st.number_input("인천센터 시금치 수량", min_value=0, value=0, step=1)
        c_bucheon_spinach = st.number_input("부천센터 시금치 수량", min_value=0, value=0, step=1)

    # 스윗밸런스 수량 입력 Form
    with col_s:
        st.markdown("#### 🥗 스윗밸런스 수량 입력")
        s_item_name = st.text_input("품목명", value="브런치 믹스 1kg", disabled=True)
        s_qty = st.number_input("브런치 믹스 1kg 수량", min_value=0, value=0, step=1)

    st.markdown("---")
    
    if st.button("💾 발주 데이터 저장하기", type="primary", use_container_width=True):
        # 쿠팡 데이터 저장
        if c_incheon_carrot > 0 or c_bucheon_carrot > 0 or c_incheon_spinach > 0 or c_bucheon_spinach > 0:
            st.session_state.coupang_list.append({
                "날짜": date_str,
                "인천 당근": c_incheon_carrot,
                "부천 당근": c_bucheon_carrot,
                "인천 시금치": c_incheon_spinach,
                "부천 시금치": c_bucheon_spinach
            })

        # 스윗밸런스 데이터 저장
        if s_qty > 0:
            st.session_state.sweet_list.append({
                "날짜": date_str,
                "품목": s_item_name,
                "수량": s_qty
            })

        st.success(f"[{date_str}] 발주 데이터가 성공적으로 저장되었습니다!")


# --- [TAB 2: 쿠팡 발주 확인서] ---
with tab_coupang:
    st.subheader("🚀 쿠팡 발주 확인서")
    
    file_coupang = st.file_uploader("쿠팡 엑셀/CSV 파일 업로드 (선택)", type=["xlsx", "xls", "csv"], key="c_file")
    
    if file_coupang is not None:
        try:
            df_c_raw = pd.read_csv(file_coupang) if file_coupang.name.endswith('.csv') else pd.read_excel(file_coupang)
        except Exception as e:
            st.error(f"파일 오류: {e}")
            df_c_raw = pd.DataFrame(st.session_state.coupang_list)
    else:
        df_c_raw = pd.DataFrame(st.session_state.coupang_list)

    st.markdown("##### 📝 데이터 수정 (날짜 변경 시 소비기한 자동 계산)")
    edited_c_df = st.data_editor(df_c_raw, num_rows="dynamic", use_container_width=True, key="c_editor")

    calculated_c_df = calculate_coupang(edited_c_df, carrot_box_unit, spinach_box_unit, coupang_exp_days)

    st.markdown("##### 📊 최종 집계 및 박스 수량 결과")
    st.dataframe(calculated_c_df, use_container_width=True)

    # 지표 요약
    total_incheon_box = calculated_c_df["인천 박스수량"].sum() if "인천 박스수량" in calculated_c_df else 0
    total_bucheon_box = calculated_c_df["부천 박스수량"].sum() if "부천 박스수량" in calculated_c_df else 0
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 발주 건수", f"{len(calculated_c_df)} 건")
    m2.metric("당근 총합", f"{calculated_c_df['당근 합계'].sum() if '당근 합계' in calculated_c_df else 0:,} 개")
    m3.metric("시금치 총합", f"{calculated_c_df['시금치 합계'].sum() if '시금치 합계' in calculated_c_df else 0:,} 개")
    m4.metric("총 박스 수량", f"인천: {total_incheon_box} / 부천: {total_bucheon_box} 박스")


# --- [TAB 3: 스윗밸런스 발주 확인서] ---
with tab_sweet:
    st.subheader("🥗 스윗밸런스 발주 확인서")
    
    file_sweet = st.file_uploader("스윗밸런스 엑셀/CSV 파일 업로드 (선택)", type=["xlsx", "xls", "csv"], key="s_file")
    
    if file_sweet is not None:
        try:
            df_s_raw = pd.read_csv(file_sweet) if file_sweet.name.endswith('.csv') else pd.read_excel(file_sweet)
        except Exception as e:
            st.error(f"파일 오류: {e}")
            df_s_raw = pd.DataFrame(st.session_state.sweet_list)
    else:
        df_s_raw = pd.DataFrame(st.session_state.sweet_list)

    st.markdown("##### 📝 데이터 수정")
    edited_s_df = st.data_editor(df_s_raw, num_rows="dynamic", use_container_width=True, key="s_editor")

    # 소비기한 (+3일) 자동 계산
    if "날짜" in edited_s_df.columns:
        edited_s_df["소비기한"] = edited_s_df["날짜"].apply(lambda d: calc_exp_date(d, sweet_exp_days))
    else:
        edited_s_df["소비기한"] = ""

    if "수량" in edited_s_df.columns:
        edited_s_df["수량"] = pd.to_numeric(edited_s_df["수량"], errors='coerce').fillna(0).astype(int)
        total_sweet_qty = edited_s_df["수량"].sum()
    else:
        total_sweet_qty = 0

    st.markdown("##### 📊 스윗밸런스 최종 결과")
    st.dataframe(edited_s_df, use_container_width=True)

    s1, s2 = st.columns(2)
    s1.metric("스윗밸런스 총 발주 건수", f"{len(edited_s_df)} 건")
    s2.metric("브런치 믹스 1kg 총 수량", f"{total_sweet_qty:,} 개")

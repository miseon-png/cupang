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
carrot200_box_unit = 6     # 당근 200 (6개/박스)
carrot400_box_unit = 8     # 당근 400 (8개/박스)
spinach_box_unit = 5       # 시금치 (5개/박스)
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

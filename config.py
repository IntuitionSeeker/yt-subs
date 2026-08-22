"""공통 설정·경로·상수."""
from pathlib import Path

# ─── 경로 ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
OUTPUT_BASE   = BASE_DIR / "output"
CHANNELS_YAML = BASE_DIR / "channels.yaml"

# ─── 자막/파일명 ─────────────────────────────────────────────────────────────
DEFAULT_LANG  = "ko"
FALLBACK_LANG = "en"
TITLE_MAX_LEN = 50

# ─── 청킹 ────────────────────────────────────────────────────────────────────
SRT_WINDOW_SEC = 120          # SRT 타임스탬프 청킹 윈도우 (초)
DESC_CHUNK_TOKENS = 300       # 설명 청킹 토큰 수

# ─── 임베딩 / KL ─────────────────────────────────────────────────────────────
EMBED_MODEL   = "BAAI/bge-m3"
COL_SUBTITLE  = "subtitle_chunks"
COL_DESC      = "desc_chunks"

# ─── LLM ─────────────────────────────────────────────────────────────────────
LLM_MODEL          = "claude-sonnet-4-6"
LLM_MAX_TOKENS     = 4096
HARNESS_MAX_STEPS  = 10
LLM_REVIEW_SAMPLE  = 500       # 장편 영상 품질 검토 시 앞 N단어 샘플링

# ─── yt-dlp 공통 옵션 ────────────────────────────────────────────────────────
# EJS 솔버는 yt-dlp[default] 패키지에 포함됨 (yt_dlp_ejs) → remote 다운로드 불필요
# deno 런타임만 있으면 YouTube JS 챌린지 해결 가능
# 429 완화: 요청 간 딜레이 랜덤화(8~20초) + 자동 재시도
# (yt-dlp 위키 권장 하한 5초보다 보수적으로 설정해 차단 위험 최소화)
YTDLP_COMMON = {
    "quiet": True,
    "no_warnings": False,
    "js_runtimes": {"deno": {}},           # deno 런타임 사용 (dict 형식)
    "sleep_interval": 8,                    # 최소 딜레이 (초)
    "max_sleep_interval": 20,              # 최대 딜레이 → 8~20초 랜덤 (429 완화)
    "sleep_requests": 2,                   # 데이터 요청(extract_info 등) 간 딜레이 (FR14.4)
    "retries": 5,                          # 실패 시 재시도
    "extractor_retries": 3,
}

# 자막 VTT 직접 다운로드용 (yt-dlp 미경유 → 자체 딜레이·헤더 필요, FR13.4)
SUB_FETCH_SLEEP_SEC = 3
SUB_FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ─── 배치 휴식 & 백오프 (FR14) ───────────────────────────────────────────────
# 고정 주기(10개/60초)는 패턴 기반 차단 탐지에 기계 서명이 되므로 랜덤화 (FR14.2)
BATCH_SIZE_RANGE = (8, 12)    # N개 처리할 때마다 — 배치마다 범위 내 재추첨
BATCH_REST_RANGE = (45, 90)   # 휴식 (초) — 매번 범위 내 랜덤
BACKOFF_BASE_SEC = 30     # 429 1회당 기본 대기 (초), 연속 시 배수 증가

# ─── 쿠키 인증 (FR13) ────────────────────────────────────────────────────────
# cookies.txt가 존재하면 yt-dlp 인증에 사용 → 429 대폭 감소
# 없으면 비로그인으로 폴백 (선택적 동작)
# 보안: 원본은 읽기전용 마운트(COOKIE_FILE), yt-dlp는 쓰기 가능한 작업본 사용
import shutil as _shutil

COOKIE_FILE     = BASE_DIR / "cookies.txt"          # 원본 (읽기전용일 수 있음)
COOKIE_WORKFILE = Path("/tmp/cookies_work.txt")     # yt-dlp 작업본 (쓰기 가능)

# ─── Firefox 쿠키 직접 읽기 (FR13.6) ─────────────────────────────────────────
# Firefox 프로필 폴더가 이 경로에 마운트되어 있으면 yt-dlp가 매 실행마다
# 최신 쿠키를 브라우저 DB(cookies.sqlite)에서 직접 읽는다.
# 수동 내보내기·쿠키 회전(만료) 문제가 사라지므로 cookies.txt보다 우선한다.
FIREFOX_PROFILE = BASE_DIR / "firefox_profile"


def firefox_profile_dir():
    """마운트된 Firefox 프로필 경로(str) 반환. cookies.sqlite 없으면 None."""
    if (FIREFOX_PROFILE / "cookies.sqlite").exists():
        return str(FIREFOX_PROFILE)
    return None


def has_auth() -> bool:
    """인증 수단(Firefox 프로필 또는 cookies.txt) 보유 여부.
    FR19.1 멤버십 재시도 판정용 — 부작용 없는 존재 확인만 한다."""
    return firefox_profile_dir() is not None or COOKIE_FILE.exists()


def resolve_cookiefile():
    """
    쿠키 파일 경로 반환. 원본이 있으면 쓰기 가능한 /tmp로 복사해 반환.
    원본이 읽기전용이어도 yt-dlp가 종료 시 갱신할 수 있도록 함.
    없으면 None (비로그인 폴백).
    """
    if not COOKIE_FILE.exists():
        return None
    try:
        _shutil.copy(COOKIE_FILE, COOKIE_WORKFILE)
        return str(COOKIE_WORKFILE)
    except Exception:
        # 복사 실패 시 원본 직접 사용 (쓰기 가능하면 동작)
        return str(COOKIE_FILE)

# ─── 429 가드 ────────────────────────────────────────────────────────────────
CONSECUTIVE_429_LIMIT = 5      # 연속 429 N회 발생 시 추출 자동 중단

# ─── 품질 검토 임계값 ────────────────────────────────────────────────────────
MIN_WORD_COUNT     = 30
MAX_REPEAT_RATIO   = 0.50
MIN_KO_RATIO       = 0.30
MAX_SPECIAL_RATIO  = 0.20


def channel_dir(channel: str) -> Path:
    """채널별 출력 폴더 경로."""
    return OUTPUT_BASE / channel


def channel_subdirs(channel: str) -> dict:
    """채널별 하위 폴더 경로 딕셔너리."""
    base = channel_dir(channel)
    return {
        "srt":    base / "srt",
        "txt":    base / "txt",
        "desc":   base / "desc",
        "meta":   base / "meta",
        "chroma": base / "chroma",
    }

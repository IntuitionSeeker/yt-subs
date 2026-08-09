"""쿠키 유효성 상태 감지·조회. FR19.2, FR19.3.

yt-dlp의 "cookies no longer valid" 경고는 stderr로 직행해 logging 핸들러로
캡처할 수 없다 → yt-dlp `logger` 옵션에 어댑터를 주입해 감지한다.
감지 결과는 output/.cookie_status.json 에 영속한다 (DQ-11:
CLI 컨테이너와 serve 컨테이너가 공유하는 유일한 쓰기 마운트).
"""
import json
import logging
import datetime

import config

log = logging.getLogger("cookie_health")

# 쿠키 경고 상태 파일 (DQ-11)
STATUS_FILE = config.OUTPUT_BASE / ".cookie_status.json"

# 쿠키 무효 판정 패턴 (대소문자 무시)
INVALID_PATTERNS = ("no longer valid", "cookies are invalid")

# yt-dlp는 요청마다 같은 경고를 반복 발생시킨다 → 같은 쿠키 파일에 대해서는
# 프로세스당 1회만 기록한다 (디스크 쓰기 증폭 방지 + detected_at을 "최초 감지"
# 시각으로 유지, F-3). 쿠키를 교체하면 mtime이 달라져 다시 기록한다
# (serve 컨테이너처럼 장수 프로세스에서 재감지가 막히지 않도록).
_UNMARKED = object()
_marked_for = _UNMARKED   # 마지막으로 기록한 시점의 쿠키 파일 mtime


class YDLLogger:
    """
    yt-dlp `logger` 옵션 어댑터. FR19.2

    모든 메시지를 base 로거로 그대로 넘기면서, warning/error 메시지에서
    쿠키 무효 패턴을 감지하면 상태 파일에 기록한다.
    yt-dlp는 `[debug]` 접두 메시지도 debug()로 보내므로 base 로거의
    debug 레벨로만 흘려 조용함(config.YTDLP_COMMON["quiet"])을 유지한다.
    """

    def __init__(self, base_logger=None):
        self.log = base_logger or log

    # ── yt-dlp가 호출하는 인터페이스 ────────────────────────────────────────
    def debug(self, msg):
        self.log.debug(str(msg))

    def info(self, msg):
        self.log.debug(str(msg))

    def warning(self, msg):
        text = str(msg)
        self._detect(text)
        self.log.warning(text)

    def error(self, msg):
        text = str(msg)
        self._detect(text)
        self.log.error(text)

    # ── 감지 ────────────────────────────────────────────────────────────────
    @staticmethod
    def is_invalid_message(text: str) -> bool:
        low = (text or "").lower()
        return any(p in low for p in INVALID_PATTERNS)

    def _detect(self, text: str):
        if self.is_invalid_message(text):
            mark_invalid(text)


def _cookie_mtime() -> "datetime.datetime | None":
    """쿠키 파일의 수정 시각. 없거나 조회 실패면 None."""
    try:
        if not config.COOKIE_FILE.exists():
            return None
        return datetime.datetime.fromtimestamp(config.COOKIE_FILE.stat().st_mtime)
    except Exception:
        return None


def _first_detected_at() -> str:
    """
    기록할 detected_at 결정. FR19.3의 자동 해제 비교는 "최초 감지" 기준이어야
    의미가 맞으므로, 아직 해제되지 않은 기존 경고가 있으면 그 시각을 보존한다.
    쿠키 파일을 그 이후에 갱신했다면(=경고가 자동 해제된 상태) 새 시각을 쓴다.
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    prev = _read_status()
    if not prev.get("invalid"):
        return now
    prev_dt = _parse_iso(prev.get("detected_at"))
    if prev_dt is None:
        return now
    ck = _cookie_mtime()
    # 쿠키가 경고 이후에 갱신됐으면 이전 경고는 해제된 것 → 새로운 감지로 본다
    if ck is not None and ck > prev_dt:
        return now
    return prev.get("detected_at")


def mark_invalid(message: str):
    """
    쿠키 무효 경고를 상태 파일에 기록. 실패해도 예외를 전파하지 않는다.
    같은 쿠키 파일에 대해서는 프로세스당 1회만 기록한다
    (F-3: yt-dlp가 요청마다 같은 경고를 반복 발생시킨다).
    """
    global _marked_for
    ck = _cookie_mtime()
    if _marked_for is not _UNMARKED and _marked_for == ck:
        return
    _marked_for = ck
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "invalid": True,
            "message": str(message)[:500],
            "detected_at": _first_detected_at(),
        }
        STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        log.warning("  🍪 쿠키 무효 경고 감지 → .cookie_status.json 기록")
    except Exception as exc:      # 상태 기록 실패가 추출을 죽이면 안 된다
        log.debug(f"cookie status 기록 실패: {exc}")


def clear():
    """쿠키 경고 상태를 제거 (수동 해제용)."""
    global _marked_for
    _marked_for = _UNMARKED
    try:
        STATUS_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.debug(f"cookie status 삭제 실패: {exc}")


def _read_status() -> dict:
    """상태 파일 로드. 없거나 깨졌으면 빈 dict."""
    try:
        if not STATUS_FILE.exists():
            return {}
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except Exception:
        return None


def get_status() -> dict:
    """
    쿠키 상태 조회. FR19.3

    반환: {present, mtime, warning, warning_message, detected_at}
    쿠키 파일을 경고 감지 이후에 갱신했으면 경고를 자동 해제한다.
    """
    present = False
    source = None
    mtime = None
    cookie_mtime_dt = None
    try:
        # Firefox 프로필이 우선 (FR13.6) — mtime은 cookies.sqlite 기준.
        # 브라우저가 세션을 갱신할 때마다 mtime이 앞으로 가므로,
        # 과거의 무효 경고는 아래 비교로 자연히 자동 해제된다.
        ff = config.firefox_profile_dir()
        if ff:
            present = True
            source = "firefox"
            ts = (config.FIREFOX_PROFILE / "cookies.sqlite").stat().st_mtime
        elif config.COOKIE_FILE.exists():
            present = True
            source = "file"
            ts = config.COOKIE_FILE.stat().st_mtime
        if present:
            cookie_mtime_dt = datetime.datetime.fromtimestamp(ts)
            mtime = cookie_mtime_dt.isoformat(timespec="seconds")
    except Exception:
        present = False
        source = None
        mtime = None
        cookie_mtime_dt = None

    status = _read_status()
    detected_at = status.get("detected_at")
    warning_message = status.get("message")

    warning = False
    if status.get("invalid"):
        if not present:
            # 쿠키가 아예 없으면 갱신으로 해제될 수 없다 → 경고 유지
            warning = True
        else:
            detected_dt = _parse_iso(detected_at)
            if detected_dt is None or cookie_mtime_dt is None:
                warning = True
            else:
                # 쿠키를 경고 이후에 갱신했으면 자동 해제 (FR19.3)
                warning = detected_dt >= cookie_mtime_dt

    return {
        "present": present,
        "source": source,          # "firefox" | "file" | None (FR13.6)
        "mtime": mtime,
        "warning": warning,
        "warning_message": warning_message if warning else None,
        "detected_at": detected_at if warning else None,
    }

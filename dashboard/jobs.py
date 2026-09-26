"""대시보드 추출 작업 관리 — URL 분류·조건 필터·단일 작업 실행. FR17·FR18."""
from __future__ import annotations

import re
import sys
import json
import uuid
import time
import logging
import datetime
import importlib
import threading
from pathlib import Path
from urllib.parse import unquote, quote_plus, unquote_plus

import config
import video_access
from channel_registry import ChannelRegistry

log = logging.getLogger("jobs")

_APP_ROOT = str(Path(__file__).resolve().parent.parent)


def _app_extractor():
    """앱 루트의 `extractor` 모듈을 확정 로드.

    yt-dlp 실행이 legacy 플러그인 탐색으로 site-packages의 `ytdlp_plugins` 경로를
    등록하면, 같은 프로세스의 이후 `import extractor`가 그 패키지의 `extractor`
    서브패키지로 섀도잉된다 (V-D4 실검증에서 발견 — 첫 스캔 성공 후 두 번째 스캔이
    ImportError). sys.modules 캐시에 올바른 모듈이 있으면 그대로 쓰고, 오염됐으면
    앱 루트를 sys.path 최우선으로 되돌려 재임포트한다.
    """
    mod = sys.modules.get("extractor")
    if mod is not None and hasattr(mod, "Extractor"):
        return mod
    sys.modules.pop("extractor", None)
    if _APP_ROOT in sys.path:
        sys.path.remove(_APP_ROOT)
    sys.path.insert(0, _APP_ROOT)
    mod = importlib.import_module("extractor")
    if not hasattr(mod, "Extractor"):       # pragma: no cover - 이중 방어
        raise ImportError(f"extractor 모듈이 앱 모듈이 아님: {mod.__file__}")
    return mod

# ─── 상수 ────────────────────────────────────────────────────────────────────
SCAN_TTL_SEC = 600          # 스캔 캐시 TTL 10분 (DQ-13)

# 영상 URL 패턴 — 프론트(index.html:575)와 동일 (FR17.1)
_VIDEO_RE   = re.compile(r"(?:watch\?v=|youtu\.be/|/shorts/|/live/)([\w-]{11})")
_PLAYLIST_RE = re.compile(r"/playlist\?list=([\w-]+)")    # FR24.1
_SEARCH_RE  = re.compile(r"/results\?\S*search_query=([^&\s]*)")   # FR34.1
_HANDLE_RE  = re.compile(r"@[^/?&\s]+")
_CHANNEL_RE = re.compile(r"/channel/(UC[\w-]+)")

# ─── 검색 추출 (FR34) ────────────────────────────────────────────────────────
# `sp`는 YouTube 검색 필터의 base64url protobuf다 (_workspace/17b 실측 레시피).
#   outer field2(0x12) = 필터 그룹 · inner field1(0x08) = 업로드 날짜 · field2(0x10) = 유형
# `type=video`(inner field2=1)를 **항상** 포함한다 — 재생목록·채널 엔트리 혼입이 사라져
# uploader_id·duration 커버리지가 100%가 된다. 6종 전부 실측 확인됨 (2026-09-20).
SP_PRESETS = {
    "all":   "EgIQAQ",       # 동영상 필터만 (기간 무관)
    "hour":  "EgQIARAB",     # 지난 1시간 + 동영상
    "today": "EgQIAhAB",     # 오늘 + 동영상
    "week":  "EgQIAxAB",     # 이번 주 + 동영상
    "month": "EgQIBBAB",     # 이번 달 + 동영상
    "year":  "EgQIBRAB",     # 올해 + 동영상
}
SEARCH_LIMIT_DEFAULT = 20        # ⓐ 개수 상한 기본 (FR34.2)
SEARCH_LIMIT_MAX = 50
SEARCH_MIN_DURATION_DEFAULT = 180   # ⓑ N초 미만 제외 기본 (FR34.3 — "쇼츠"가 아니다)

# 멤버십 판정은 `video_access` 한 곳에서만 정의한다 — 추출 경로와 공유 (DQ-38)
_MEMBERS_AVAILABILITY = video_access.MEMBERS_AVAILABILITY    # FR17.6 (역호환 별칭)

_STAT_KEYS = ("new", "updated", "skip", "no_sub", "members_only", "error",
              "date_skip", "live_wait")


def _migration_locked() -> bool:
    """CLI 마이그레이션 락 존재 여부 (FR35.10). 조회 실패는 '락 없음'으로 본다."""
    try:
        import folder_ops
        return folder_ops.is_locked()
    except Exception:                     # pragma: no cover - 락 조회 실패는 무시
        return False


class JobBusyError(Exception):
    """실행 중인 작업이 있을 때 (409). FR17.7"""

    def __init__(self, job: dict = None, message: str = "이미 실행 중인 작업이 있습니다."):
        super().__init__(message)
        self.job = job
        self.message = message


# ─── URL 분류 (FR17.1) ───────────────────────────────────────────────────────
def classify_url(url: str) -> tuple:
    """
    URL을 영상/재생목록/검색/채널로 분류. 판별 불가 시 ValueError.
      ("video", video_id) | ("playlist", url) | ("search", url) | ("channel", url)
    우선순위: 영상 → 재생목록 → 검색 → 채널 (FR24.1·FR34.1)
    — `watch?v=…&list=…`는 단일 영상으로 처리 (기존 동작 유지).
    — **순수 텍스트는 검색으로 승격하지 않는다.** 검색 진입은 요청 본문의 `q` 필드
      전용이다 — 오타 URL이 조용히 검색으로 둔갑하면 엉뚱한 채널 수십 개가 등록된다 (DQ-27).
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL이 비어 있습니다.")
    decoded = unquote(raw)

    m = _VIDEO_RE.search(decoded)
    if m:
        return ("video", m.group(1))
    if _PLAYLIST_RE.search(decoded):
        return ("playlist", raw)
    if _SEARCH_RE.search(raw) or _SEARCH_RE.search(decoded):     # FR34.1
        return ("search", raw)
    if _HANDLE_RE.search(decoded) or _CHANNEL_RE.search(decoded):
        return ("channel", raw)
    raise ValueError(f"영상·재생목록·검색·채널 URL로 판별할 수 없습니다: {raw[:80]}")


def search_query_from_url(url: str) -> str:
    """`/results?search_query=…` URL에서 검색어 복원. FR34.1"""
    m = _SEARCH_RE.search((url or "").strip())
    q = unquote_plus(m.group(1)) if m else ""
    if not q.strip():
        raise ValueError("검색 URL에서 검색어를 찾을 수 없습니다.")
    return q.strip()


def _build_search_url(q: str, period: str = "all") -> str:
    """
    검색 URL 조립 — yt-dlp `youtube:search_url` 추출기가 받는다. FR34.1·34.4

    `ytsearchN:` 구문도 동작하지만 `sp` 필터를 실을 수 없어 쓰지 않는다.
    알 수 없는 period는 400이 아니라 `all` 폴백 (조건 완화는 안전 방향).
    """
    sp = SP_PRESETS.get(str(period or "all").strip().lower(), SP_PRESETS["all"])
    return f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp={sp}"


def _search_opts(limit: int) -> dict:
    """
    검색 flat 스캔용 옵션. FR34.2

    `playlist_items: "1-N"`으로 ⓐ개수 상한을 **스캔 단계에서** 절단한다.
    `_flat_opts()`를 반드시 경유해야 `extractor_args.youtube.lang`(DQ-20)이 적용된다.
    """
    return {**_flat_opts(), "playlist_items": f"1-{int(limit)}"}


def normalize_search_params(limit=None, min_duration=None, period=None,
                            folder=None, q: str = "") -> dict:
    """검색 조건 정규화·검증 (위반은 ValueError → 400). FR34.2~34.6"""
    limit = SEARCH_LIMIT_DEFAULT if limit is None else int(limit)
    if not 1 <= limit <= SEARCH_LIMIT_MAX:
        raise ValueError(f"개수 상한(limit)은 1~{SEARCH_LIMIT_MAX} 사이여야 합니다.")
    min_duration = (SEARCH_MIN_DURATION_DEFAULT if min_duration is None
                    else int(min_duration))
    if min_duration < 0:
        raise ValueError("최소 길이(min_duration)는 0 이상이어야 합니다.")
    period = str(period or "all").strip().lower()
    if period not in SP_PRESETS:            # 미지 값은 400이 아니라 all 폴백
        period = "all"
    folder = (folder or "").strip() or (q or "").strip()
    return {"limit": limit, "min_duration": min_duration,
            "period": period, "folder": folder}


# ─── 조건 필터 (FR17.4) ──────────────────────────────────────────────────────
def apply_filters(videos: list, f: dict) -> list:
    """
    스캔 결과에 추출 조건을 적용. 프론트 applyFilters()(index.html:672-680)와
    **동일한 순서**여야 미리보기 = 실제 처리 수가 성립한다 (V-D11).
      ⓒ카테고리(OR·완전일치) → ⓓ멤버십 → ⓔ키워드(부분일치) → 마지막에 ⓐ[:latest]
    ⓑ기간(since/until)은 여기서 적용하지 않는다 — 처리 시 full info로 확정 (DQ-12).
    """
    f = f or {}
    cats = list(f.get("categories") or [])
    include_members = bool(f.get("include_members"))
    keyword = (f.get("keyword") or "").strip().lower()

    out = []
    for v in videos:
        # ⓒ 카테고리: 선택된 것 중 하나라도 포함 (OR, 재생목록 제목 완전일치)
        if cats and not any(p in cats for p in (v.get("playlists") or [])):
            continue
        # ⓓ 멤버십 제외 (사용자 조건 우선 — FR19.1 재시도보다 우선)
        if not include_members and v.get("members_only"):
            continue
        # ⓔ 제목 검색어 (대소문자 무시 부분일치)
        if keyword and keyword not in (v.get("title") or "").lower():
            continue
        out.append(v)

    # ⓐ 최신 N — 필터 적용 뒤 배열 앞에서 slice (스캔 배열 순서 = 최신순)
    latest = f.get("latest")
    if latest:
        out = out[:int(latest)]
    return out


def _is_members_availability(availability) -> bool:
    """스캔 엔트리의 availability로 멤버십 전용 판별. FR17.6 (공유 규칙, DQ-38)"""
    return video_access.is_members_availability(availability)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _probe_opts() -> dict:
    """
    단일영상 사전 조회용 yt-dlp 옵션.
    Extractor._ydl_opts는 self를 쓰지 않는 정적 로직(쿠키·로거 주입)이므로 재사용해
    옵션 이중 관리를 피한다. 시그니처가 바뀌면 config 기반으로 폴백.
    """
    Extractor = _app_extractor().Extractor
    try:
        return Extractor._ydl_opts(None, skip_download=True)
    except Exception:                       # pragma: no cover - 방어적 폴백
        opts = {**config.YTDLP_COMMON, "skip_download": True}
        cookiefile = config.resolve_cookiefile()
        if cookiefile:
            opts["cookiefile"] = cookiefile
        return opts


def _flat_opts() -> dict:
    """재생목록 flat 스캔용 옵션 — _probe_opts와 같은 재사용 패턴 (FR24.2)."""
    return {**_probe_opts(), "extract_flat": True}


def _entry_channel(e: dict, reg: "ChannelRegistry" = None):
    """
    flat 엔트리에서 (채널명, 채널 URL) 해석. FR24.3
    uploader_id(@핸들) 우선 → channel_id(UC…) 폴백 → 해석 불가 시 None.
    이미 등록된 채널이면 등록명을 쓴다 (FR32.3, DQ-19).
    """
    uid = (e.get("uploader_id") or "").strip()
    cid = (e.get("channel_id") or "").strip()
    if uid.startswith("@"):
        url = f"https://www.youtube.com/{uid}"
        name = uid[1:]
    elif cid.startswith("UC"):
        url = f"https://www.youtube.com/channel/{cid}"
        name = cid
    else:
        return None
    if reg is not None:
        name = reg.resolve_name(url)
    return name, url


def _merged_pl_map(channel: str, vids: list, title: str) -> dict:
    """
    재생목록 제목을 채널 카테고리 맵에 병합한 full-map 반환 + playlists.json 갱신 (FR24.4).
    DQ-17: _backfill_meta는 맵에 없는 vid의 meta.playlists를 []로 덮어쓰므로
    부분 맵을 만들지 않는다 — 기존 playlists.json(없으면 기존 meta에서 재구성)에 병합.
    """
    mapping = {}
    pl_path = config.channel_dir(channel) / "playlists.json"
    if pl_path.exists():
        try:
            mapping = json.loads(pl_path.read_text(encoding="utf-8"))
        except Exception:
            mapping = {}
    if not mapping:
        meta_dir = config.channel_subdirs(channel)["meta"]
        if meta_dir.exists():
            for f in meta_dir.glob("*.json"):
                try:
                    m = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if m.get("id") and m.get("playlists"):
                    mapping[m["id"]] = list(m["playlists"])
    for vid in vids:
        lst = mapping.setdefault(vid, [])
        if title not in lst:
            lst.append(title)
    try:
        pl_path.parent.mkdir(parents=True, exist_ok=True)
        pl_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    except Exception:                       # pragma: no cover - 저장 실패해도 추출 계속
        pass
    return mapping


def _group_flat_entries(entries: list, min_duration: int = None) -> tuple:
    """
    flat 엔트리 목록 → (videos_view, by_channel). 재생목록(FR24.2)·검색(FR34.5) 공용.

    - 진행 중/예약 라이브 제외 (FR16.3 준용). 검색 flat에는 `live_status` 키 자체가
      없어(실측 0/15) 이 분기가 동작하지 않는다 — 처리 시 FR16.5 가드가 최종 방어선 (DQ-26).
    - `min_duration`이 주어지면 **`duration`이 있고 임계 미만인 것만** 제외한다.
      **결측은 통과시킨다** — 판정 불가를 제외 근거로 쓰면 신호 없는 영상이 조용히 사라진다 (DQ-23).
    - 채널 해석은 등록명 역조회(FR32.3, DQ-19), `extracted`는 원채널 state 기준 (DQ-18).
    """
    from state_manager import StateManager
    videos_view, by_channel, states = [], {}, {}
    bad_channels = set()                   # 폴더명으로 쓸 수 없는 채널 (경고 1회 후 제외)
    reg = ChannelRegistry()
    for e in entries:
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        dur = e.get("duration")
        dur = dur if isinstance(dur, (int, float)) else None
        if min_duration and dur is not None and dur < min_duration:
            continue                       # ⓑ N초 미만 제외 (FR34.3)
        ch = _entry_channel(e, reg)
        if not ch:
            log.warning(f"  ⚠ 채널 불명 → 제외: {e.get('id')}")
            continue
        name, ch_url = ch
        if name in bad_channels:
            continue
        # 디렉터리로 쓸 수 없는 채널명(예약어 핸들 `@con`·`@nul`·`@com1` 등)은
        # `config.channel_dir()`가 ValueError를 던진다. 그 한 건 때문에 스캔 전체를
        # 400으로 죽이지 말고 **해당 채널만 제외**한다 — 위의 "채널 불명 → 제외"와 같은 패턴.
        if name not in states:
            try:
                ch_dir = config.channel_dir(name)
            except ValueError as exc:
                bad_channels.add(name)
                log.warning(f"  ⚠ 폴더로 쓸 수 없는 채널명 → 제외: {name!r} — {exc}")
                continue
            states[name] = StateManager(name).state if ch_dir.exists() else {}
        e["content_type"] = e.get("content_type") or "video"
        by_channel.setdefault(name, {"url": ch_url, "entries": []})["entries"].append(e)
        st = states[name].get(e["id"]) or {}
        sub_type = st.get("sub_type")
        videos_view.append({
            "id": e["id"],
            "title": e.get("title") or e["id"],
            "channel": name,
            "content_type": e["content_type"],
            "playlists": [],
            "members_only": bool(_is_members_availability(e.get("availability"))
                                 or sub_type == "members_only"),
            "extracted": sub_type in ("manual", "auto", "whisper"),   # DQ-18
            "duration": int(dur) if dur is not None else None,        # FR34.5·FR20.5
        })
    return videos_view, by_channel


# ─── 작업 관리자 (FR17.7·FR18) ───────────────────────────────────────────────
class JobManager:
    """단일 uvicorn 프로세스 전제의 모듈 싱글턴. 동시 1작업."""

    def __init__(self):
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._busy = False          # 스캔·추출 공통 점유 플래그
        self._job = None            # 마지막 job dict (idle은 기동 후 무작업일 때만)
        self._scans = {}            # scan_id → 캐시 항목

    # ── 점유 제어 ────────────────────────────────────────────────────────────
    def _acquire(self):
        # FR35.10 — 마이그레이션 락도 본다. `is_busy()`만 락을 합산하면 삭제·이름 변경은
        # 막히는데 **스캔·추출은 그대로 진행**돼(진입점이 여기다) 이동 중인 채널의 평면
        # 경로를 `mkdir(parents=True)`가 다시 만들어 데이터가 두 곳으로 갈라질 수 있다.
        if _migration_locked():
            raise JobBusyError(None, "마이그레이션이 진행 중입니다"
                                     "(output/.migration.lock) — 끝난 뒤 다시 시도하세요.")
        with self._lock:
            if self._busy:
                raise JobBusyError(self._snapshot())
            self._busy = True

    def _release(self):
        with self._lock:
            self._busy = False
            self._thread = None

    def _snapshot(self) -> dict:
        with self._lock:
            if self._job is None:
                return None
            return _copy(self._job)

    # ── 스캔 캐시 ────────────────────────────────────────────────────────────
    def _prune_scans(self):
        now = time.time()
        with self._lock:
            for sid in [s for s, e in self._scans.items()
                        if now - e["created_at"] > SCAN_TTL_SEC]:
                self._scans.pop(sid, None)

    def _get_scan(self, scan_id: str) -> dict:
        self._prune_scans()
        with self._lock:
            entry = self._scans.get(scan_id)
        if not entry:
            raise ValueError("scan_id가 만료되었습니다. 다시 스캔하세요.")
        return entry

    def invalidate_scans(self, channel: str = None) -> int:
        """
        채널을 참조하는 스캔 캐시를 **폐기**한다. FR36.8 (DQ-41)

        캐시에는 스캔 시점의 채널명이 박혀 있고 `_run_channel`이 그것을 끝까지 쓴다.
        이름이 바뀌거나(rename) 등록이 사라진(delete) 뒤 옛 `scan_id`로 추출하면
        `reg.get(옛이름)`이 실패해 폴백 cfg로 진행하거나(→ `config.channel_dir(옛이름)`
        = 레지스트리에 없는 **유령 폴더**), `reg.add()`가 삭제한 채널을 **되살린다**.
        캐시를 새 이름으로 고쳐 쓰지 않는 이유는 `channel`·`by_channel`·`url`·`entries`가
        얽혀 부분 갱신이 새 불일치를 만들기 때문이다 — 폐기하면 기존 400
        ("scan_id가 만료되었습니다. 다시 스캔하세요.")에 그대로 착지한다.

        판정은 **정확 일치**로 충분하다(캐시에 들어간 이름은 이미 레지스트리 표기다).
        `channel=None`이면 전체를 비운다. 반환값은 삭제 건수(로그용).
        """
        with self._lock:
            if channel is None:
                dropped = len(self._scans)
                self._scans.clear()
            else:
                drop = [sid for sid, e in self._scans.items()
                        if e.get("channel") == channel
                        or channel in (e.get("by_channel") or {})]
                for sid in drop:
                    self._scans.pop(sid, None)
                dropped = len(drop)
        if dropped:
            log.info(f"🗑 스캔 캐시 {dropped}건 폐기 (채널: {channel or '전체'})")
        return dropped

    # ── 사전 스캔 (FR17.3 채널 · FR24.2 재생목록) ────────────────────────────
    def scan(self, url: str) -> dict:
        kind, _ = classify_url(url)          # 판별 불가 → ValueError(400)
        if kind == "video":
            raise ValueError("영상 URL은 /extract 로 바로 추출하세요.")
        if kind == "search":                 # 검색 결과 페이지 URL 붙여넣기 (FR34.1)
            # URL의 `sp`는 그대로 쓰지 않고 기본 조건으로 다시 조립한다 —
            # 조건은 대시보드 UI가 단일 출처여야 미리보기와 실제가 어긋나지 않는다.
            return self.scan_search(search_query_from_url(url))
        self._acquire()
        try:
            if kind == "playlist":
                return self._do_scan_playlist(url)
            return self._do_scan(url)
        finally:
            self._release()

    def _do_scan(self, url: str) -> dict:
        Extractor = _app_extractor().Extractor

        # 등록은 하지 않되, 이미 등록된 채널이면 그 등록명·lang을 쓴다 (FR32.2, DQ-19).
        # 핸들을 재추출하면 등록명≠핸들인 채널에서 없는 폴더의 state를 읽어
        # extracted가 전부 false가 되고, 추출이 새 폴더에 중복 저장된다.
        reg = ChannelRegistry()
        name = reg.resolve_name(url)
        lang = (reg.list().get(name) or {}).get("lang") or config.DEFAULT_LANG
        ch_cfg = {"name": name,
                  "url": ChannelRegistry.normalize_url(url),
                  "lang": lang}
        log.info(f"🔍 스캔: {name}")
        ext = Extractor(ch_cfg)
        entries = ext.scan_channel()
        pl_map = ext.scan_playlists()
        state = ext.state.state

        videos_view = []
        for e in entries:
            vid = e.get("id")
            st = state.get(vid) or {}
            sub_type = st.get("sub_type")
            videos_view.append({
                "id": vid,
                "title": e.get("title") or vid,
                "content_type": e.get("content_type", "video"),
                "playlists": pl_map.get(vid, []),
                # 스캔 availability OR state.sub_type 합집합
                "members_only": bool(_is_members_availability(e.get("availability"))
                                     or sub_type == "members_only"),
                "extracted": sub_type in ("manual", "auto", "whisper"),   # DQ-18
            })

        playlists = sorted({p for lst in pl_map.values() for p in lst})
        scan_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._scans[scan_id] = {
                "scan_id": scan_id,
                "channel": name,
                "url": url,
                "videos_view": videos_view,
                "entries": entries,          # 원본 flat 엔트리 (추출 시 재스캔 방지)
                "pl_map": pl_map,
                "created_at": time.time(),
            }
        log.info(f"  ✅ 후보 {len(videos_view)}개 · 재생목록 {len(playlists)}개 (scan_id={scan_id})")
        return {"scan_id": scan_id, "channel": name,
                "videos": videos_view, "playlists": playlists}

    # ── 재생목록 사전 스캔 (FR24.2) ──────────────────────────────────────────
    def _do_scan_playlist(self, url: str) -> dict:
        import yt_dlp
        _app_extractor()                     # sys.path 정상화 (섀도잉 방어)

        log.info(f"🔍 재생목록 스캔: {url[:70]}")
        with yt_dlp.YoutubeDL(_flat_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
        title = (info.get("title") or "").strip() or "재생목록"
        entries = [e for e in (info.get("entries") or []) if e.get("id")]
        videos_view, by_channel = _group_flat_entries(entries)

        scan_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._scans[scan_id] = {
                "scan_id": scan_id,
                "kind": "playlist",
                "playlist_title": title,
                "channel": title,            # 표시용 (프론트 condChannel)
                "url": url,
                "videos_view": videos_view,
                "by_channel": by_channel,
                "created_at": time.time(),
            }
        log.info(f"  ✅ 재생목록 '{title}' 후보 {len(videos_view)}개 · "
                 f"채널 {len(by_channel)}개 (scan_id={scan_id})")
        return {"scan_id": scan_id, "kind": "playlist", "playlist": title,
                "channel": title, "videos": videos_view, "playlists": []}

    # ── 검색 사전 스캔 (FR34.1~34.5) ─────────────────────────────────────────
    def scan_search(self, q: str, limit: int = None, min_duration: int = None,
                    period: str = None, folder: str = None) -> dict:
        """검색어 → 후보 목록 + scan_id. 조건 위반은 ValueError(400). FR34.1"""
        q = (q or "").strip()
        if not q:
            raise ValueError("검색어가 비어 있습니다.")
        p = normalize_search_params(limit, min_duration, period, folder, q)
        self._acquire()
        try:
            return self._do_scan_search(q, **p)
        finally:
            self._release()

    def _do_scan_search(self, q: str, limit: int, min_duration: int,
                        period: str, folder: str) -> dict:
        """
        `_do_scan_playlist`와 같은 골격 — 소스만 검색으로 치환 (FR34.5).

        차이 ① ⓑduration 필터(결측 통과, DQ-23) ② `live_status` 선제외가 동작하지
        않음(검색 flat에 키 부재 — DQ-26) ③ 캐시가 `kind:"search"`·`query`·`folder`를 갖는다.
        """
        import yt_dlp
        _app_extractor()                     # sys.path 정상화 (섀도잉 방어)

        url = _build_search_url(q, period)
        log.info(f"🔍 검색 스캔: '{q}' · 최대 {limit}개 · "
                 f"{min_duration}초 미만 제외 · 기간 {period}")
        with yt_dlp.YoutubeDL(_search_opts(limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = [e for e in (info.get("entries") or []) if e.get("id")]
        videos_view, by_channel = _group_flat_entries(entries,
                                                      min_duration=min_duration)

        scan_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._scans[scan_id] = {
                "scan_id": scan_id,
                "kind": "search",
                "query": q,
                "folder": folder,
                "channel": q,                # 표시용 (프론트 condChannel)
                "url": url,
                "videos_view": videos_view,
                "by_channel": by_channel,
                "created_at": time.time(),
            }
        log.info(f"  ✅ 검색 '{q}' 후보 {len(videos_view)}개 · "
                 f"채널 {len(by_channel)}개 (scan_id={scan_id})")
        return {"scan_id": scan_id, "kind": "search", "query": q,
                "folder": folder, "channel": q,
                "videos": videos_view, "playlists": []}

    # ── 추출 시작 (FR17.2·17.4·17.7) ─────────────────────────────────────────
    def start(self, req: dict) -> dict:
        req = req or {}
        url = (req.get("url") or "").strip() or None
        scan_id = (req.get("scan_id") or "").strip() or None
        filters = req.get("filters") or {}
        index = req.get("index", True)

        # 요청 형태 판정 — 위반은 모두 ValueError(400)
        if url and scan_id:
            raise ValueError("url과 scan_id는 함께 지정할 수 없습니다.")
        if not url and not scan_id:
            raise ValueError("url 또는 scan_id 중 하나가 필요합니다.")

        if url:
            kind, vid = classify_url(url)
            if kind == "channel":
                raise ValueError("채널 URL은 /extract/scan을 먼저 호출하세요.")
            job = self._new_job("single_video", "", url)
            worker, args = self._run_single, (job, url, vid, index)
        else:
            entry = self._get_scan(scan_id)            # 만료·부재 → ValueError(400)
            if entry.get("kind") == "search":          # FR34.9
                job = self._new_job("search_run", entry["channel"], entry["url"])
                worker, args = self._run_search, (job, entry, filters, index)
            elif entry.get("kind") == "playlist":      # FR24.3
                job = self._new_job("playlist_run", entry["channel"], entry["url"])
                worker, args = self._run_playlist, (job, entry, filters, index)
            else:
                job = self._new_job("channel_run", entry["channel"], entry["url"])
                worker, args = self._run_channel, (job, entry, filters, index)

        self._acquire()
        try:
            with self._lock:
                self._cancel.clear()
                self._job = job
                self._thread = threading.Thread(target=self._wrap, args=(worker, args),
                                                daemon=True)
                self._thread.start()
        except Exception:
            self._release()
            raise
        return _copy(job)

    def _wrap(self, worker, args):
        try:
            worker(*args)
        finally:
            self._release()

    def _new_job(self, kind: str, channel: str, url: str) -> dict:
        return {
            "job_id": datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
            "kind": kind,
            "channel": channel,
            "url": url,
            "status": "running",
            "phase": "registering",
            "total": 0,
            "done": 0,
            "current_title": None,
            # 인덱싱 진행율 — 추출용 done/total과 분리한다 (FR33.3, DQ-22)
            "index_stage": None,
            "index_done": 0,
            "index_total": 0,
            "stats": {k: 0 for k in _STAT_KEYS},
            "events": [],           # 영상별 결과 이벤트 (FR26.2, 캡 1000)
            "warnings": [],         # 작업 경고 (폴더 자동 지정 건너뜀 등, FR35.13)
            # 429 연속 차단으로 남은 채널을 건너뛰었는가 (FR37.9) — stats 카운터가 아니다
            "aborted_429": False,
            "error": None,
            "started_at": _now_iso(),
            "finished_at": None,
        }

    # ── 상태·취소 (FR18.3·FR17.8) ────────────────────────────────────────────
    def status(self) -> dict:
        snap = self._snapshot()
        return snap if snap is not None else {"status": "idle"}

    def is_busy(self) -> bool:
        """
        추출·스캔 점유 여부 — 삭제(FR21)가 작업 중 파일 정리와 겹치지 않도록 가드.

        FR35.10: `folder_ops.is_locked()`를 **OR로 합산**한다. CLI 마이그레이션
        컨테이너와 serve 컨테이너는 job 상태를 공유할 수 없어 `output/.migration.lock`
        파일을 통해서만 서로를 인지한다.
        """
        with self._lock:
            if self._busy:
                return True
        return _migration_locked()

    def cancel(self) -> bool:
        with self._lock:
            job = self._job
            if self._busy and job and job.get("status") == "running":
                self._cancel.set()
                log.info("🛑 취소 요청 — 현재 영상 완료 후 중단합니다.")
                return True
            return False

    # ── job 갱신 헬퍼 ────────────────────────────────────────────────────────
    def _update(self, job: dict, **fields):
        with self._lock:
            job.update(fields)

    def _merge_stats(self, job: dict, stats: dict):
        if not stats:
            return
        with self._lock:
            for k in _STAT_KEYS:
                if k in stats:
                    job["stats"][k] = stats[k]

    def _finish(self, job: dict, status: str, error: str = None):
        with self._lock:
            job["status"] = status
            job["phase"] = "finishing"
            job["current_title"] = None
            job["error"] = error
            job["finished_at"] = _now_iso()

    def _index_cb(self, job: dict):
        """KLIndexer.index_all에 넘길 진행 콜백 (FR33.3)."""
        def cb(stage, done, total, title):
            self._update(job, index_stage=stage, index_done=done,
                         index_total=total, current_title=title)
        return cb

    def _maybe_index(self, job: dict, channel: str, index: bool, cancelled: bool):
        """완료 후 자동 인덱싱 (FR17.9). 취소 시에는 생략한다."""
        if not index or cancelled:
            return
        with self._lock:
            changed = job["stats"]["new"] + job["stats"]["updated"]
        if changed <= 0:
            return
        from kl_indexer import KLIndexer
        self._update(job, phase="indexing", current_title=None,
                     index_stage=None, index_done=0, index_total=0)
        KLIndexer(channel).index_all(on_progress=self._index_cb(job))
        self._update(job, current_title=None)

    # ── 채널 워커 ────────────────────────────────────────────────────────────
    def _run_channel(self, job: dict, entry: dict, filters: dict, index: bool):
        channel = entry["channel"]
        try:
            _ext_mod = _app_extractor()
            Extractor = _ext_mod.Extractor

            # 대상 선정 — 캐시된 view에 조건 적용 후 원본 entries를 같은 순서로 필터
            selected = apply_filters(entry["videos_view"], filters)
            ids = {v["id"] for v in selected}
            target_entries = [e for e in entry["entries"] if e.get("id") in ids]
            self._update(job, total=len(target_entries), phase="registering")

            # 채널 등록은 추출 시점에
            reg = ChannelRegistry()
            if channel not in reg.names():
                reg.add(entry["url"], lang=config.DEFAULT_LANG)
                log.info(f"✅ 채널 등록: {channel}")
            try:
                ch_cfg = reg.get(channel)
            except KeyError:
                ch_cfg = {"name": channel,
                          "url": ChannelRegistry.normalize_url(entry["url"]),
                          "lang": config.DEFAULT_LANG}

            since = (filters or {}).get("since") or None
            until = (filters or {}).get("until") or None
            date_range = {"since": since, "until": until} if (since or until) else None

            self._update(job, phase="extracting")
            ext = Extractor(ch_cfg)
            # 단일 채널은 run() 1회라 휴식 누적은 문제없다. 취소 중 긴 휴식에
            # 갇히지 않도록 cancel_check만 붙인다 (FR14.2 · FR18.2)
            stats = ext.run(entries=target_entries, pl_map=entry["pl_map"],
                            date_range=date_range, progress=self._make_cb(job),
                            rest_state=_ext_mod.BatchRest(
                                cancel_check=self._cancel.is_set))
            self._merge_stats(job, stats)

            cancelled = bool((stats or {}).get("cancelled")) or self._cancel.is_set()
            self._maybe_index(job, channel, index, cancelled)
            self._finish(job, "cancelled" if cancelled else "done")
        except Exception as exc:
            log.error(f"✗ 작업 실패: {exc}")
            self._finish(job, "error", error=str(exc)[:300])

    # ── 재생목록 워커 (FR24.3~24.5) ──────────────────────────────────────────
    def _run_playlist(self, job: dict, entry: dict, filters: dict, index: bool):
        """재생목록 추출 — 제목을 카테고리로 병합한다 (FR24.4)."""
        self._run_grouped(job, entry, filters, index,
                          group_title=entry["playlist_title"],
                          merge_categories=True, auto_run=True)

    # ── 검색 워커 (FR34.9) ───────────────────────────────────────────────────
    def _run_search(self, job: dict, entry: dict, filters: dict, index: bool):
        """
        검색 추출 — 재생목록 워커와 **동일 계약**이고 두 가지만 다르다 (FR34.6·34.10).
          ① 신규 등록 채널을 `folder`로 묶고 `auto_run: false`를 기록한다 (FR34.7)
          ② 검색어를 카테고리로 병합하지 않는다 (`merge_categories=False`, DQ-28)
        """
        self._run_grouped(job, entry, filters, index,
                          group_title=entry.get("folder") or entry.get("query"),
                          merge_categories=False, auto_run=False)

    # ── 스케줄 워커 (FR37.7) ─────────────────────────────────────────────────
    def start_schedule(self, plan: dict) -> dict:
        """
        주기 자동 추출 시작 — 호출자는 `scheduler.run_cycle` 하나다 (FR37.7).

        점유 실패(`JobBusyError`)는 **호출자가 삼킨다** — 사용자에게 보일 오류가 아니라
        "이번 틱은 미룬다"는 신호다 (FR37.12).
        """
        entry = {"channel": "", "url": "",
                 "by_channel": plan["by_channel"],
                 "videos_view": plan["videos_view"]}
        job = self._new_job("schedule_run", "자동 추출", "")
        self._acquire()
        try:
            with self._lock:
                self._cancel.clear()
                self._job = job
                self._thread = threading.Thread(
                    target=self._wrap, args=(self._run_schedule, (job, entry)),
                    daemon=True)
                self._thread.start()
        except Exception:
            self._release()
            raise
        return _copy(job)

    def _run_schedule(self, job: dict, entry: dict):
        """
        스케줄 주기 추출 — 재생목록·검색과 **같은 그룹 워커**를 쓴다 (FR37.7).

        고정 인자: `include_members=True`(RSS 엔트리에는 availability가 없어 사전 제외가
        불가능하다 — 빼면 `apply_filters` 기본값이 멤버십 영상을 조용히 지운다) ·
        `group_title=None`(폴더 자동 지정 없음 — 대상이 전부 기등록 채널) ·
        `merge_categories=False`(pl_map={} → 재생목록 스캔 요청 0) · `index=True`.
        """
        self._run_grouped(job, entry, filters={"include_members": True}, index=True,
                          group_title=None, merge_categories=False, auto_run=True)

    def _run_grouped(self, job: dict, entry: dict, filters: dict, index: bool,
                     group_title: str = None, merge_categories: bool = True,
                     auto_run: bool = True):
        """채널별 그룹 순차 실행 — 결과물은 각 영상의 원채널 폴더에 저장."""
        pl_title = group_title
        try:
            _ext_mod = _app_extractor()
            Extractor = _ext_mod.Extractor

            selected = apply_filters(entry["videos_view"], filters)
            ids = {v["id"] for v in selected}
            groups = []                      # (채널명, 채널URL, 대상 엔트리) — 스캔 순서 유지
            for name, grp in entry["by_channel"].items():
                g = [e for e in grp["entries"] if e.get("id") in ids]
                if g:
                    groups.append((name, grp["url"], g))
            total = sum(len(g) for _, _, g in groups)
            self._update(job, total=total, phase="registering")

            since = (filters or {}).get("since") or None
            until = (filters or {}).get("until") or None
            date_range = {"since": since, "until": until} if (since or until) else None

            reg = ChannelRegistry()
            # 배치 휴식 상태는 그룹 경계를 넘어 공유한다 (FR14.2).
            # 그룹마다 run()을 새로 부르므로 run() 지역 카운터로 두면 매번 0으로
            # 리셋돼 휴식이 오지 않는다 — 검색은 영상당 채널이 달라 특히 치명적이다.
            rest_state = _ext_mod.BatchRest(cancel_check=self._cancel.is_set)
            agg = {k: 0 for k in _STAT_KEYS}     # 완료 그룹 누계 (진행 콜백이 합산)
            base_done = 0
            changed = []                          # new+updated>0 채널 → 인덱싱 대상
            cancelled = False
            aborted = False                       # 429 연속 차단 (FR37.9)
            for gi, (name, ch_url, g_entries) in enumerate(groups):
                if self._cancel.is_set():
                    cancelled = True
                    break
                # 미등록 채널은 추출 시점에 자동 등록 + 폴더 지정 (FR25.7·FR34.6)
                # (기존 등록 채널의 group·auto_run은 둘 다 건드리지 않는다)
                if name not in reg.names():
                    reg.add(ch_url, lang=config.DEFAULT_LANG)
                    if not auto_run:            # 검색 유입 채널 (FR34.7, DQ-25)
                        try:
                            reg.set_auto_run(name, False)
                        except KeyError:        # pragma: no cover - 이름 해석 불일치
                            log.warning(f"⚠️ auto_run 기록 생략 — 등록명 불일치: {name}")
                    if pl_title:
                        # FR35.13: 충돌 시 409로 작업 전체를 죽이지 않고 이 채널만 건너뛴다
                        import folder_ops
                        try:
                            res = folder_ops.set_channel_group(name, pl_title,
                                                               on_conflict="skip")
                        except (KeyError, ValueError, folder_ops.MoveError) as exc:
                            res = {"skipped": str(exc)}
                            log.warning(f"⚠️ 폴더 자동 지정 실패 — {name}: {exc}")
                        if res.get("skipped"):
                            self._append_warning(
                                job, f"폴더 '{pl_title}' 자동 지정 건너뜀 — "
                                     f"{name}: {res['skipped']}")
                        # folder_ops가 yaml을 갱신했으므로 지역 레지스트리를 재적재한다
                        reg = ChannelRegistry()
                    log.info(f"✅ 채널 등록: {name} (폴더: {pl_title})"
                             + ("" if auto_run else " · run 전체 순회 제외"))
                try:
                    ch_cfg = reg.get(name)
                except KeyError:                 # pragma: no cover - 방어적 폴백
                    ch_cfg = {"name": name,
                              "url": ChannelRegistry.normalize_url(ch_url),
                              "lang": config.DEFAULT_LANG}

                # 검색어는 카테고리로 병합하지 않는다 (FR34.10·DQ-28).
                # `{}`를 넘기는 이유: `run(pl_map=None)`은 scan_playlists()를 **호출**해
                # 채널마다 추가 요청이 나가고 _backfill_meta까지 돈다 — DQ-28이 말하는
                # "병합 없음"은 빈 맵으로만 성립한다. 빈 맵은 `if pl_map:`이 거짓이라
                # 백필도 생략되고, playlists는 다음 전체 run 백필(FR15.5)로 채워진다.
                pl_map = (_merged_pl_map(name, [e["id"] for e in g_entries], pl_title)
                          if merge_categories and pl_title else {})
                self._update(job, phase="extracting")
                ext = Extractor(ch_cfg)
                stats = ext.run(entries=g_entries, pl_map=pl_map,
                                date_range=date_range, rest_state=rest_state,
                                progress=self._make_group_cb(job, agg, base_done,
                                                             total, channel=name)
                                ) or {}
                for k in _STAT_KEYS:
                    agg[k] += stats.get(k, 0)
                base_done += len(g_entries)
                if stats.get("new", 0) + stats.get("updated", 0) > 0:
                    changed.append(name)
                if stats.get("cancelled") or self._cancel.is_set():
                    cancelled = True
                    break
                # FR37.9 — 한 채널이 연속 429로 끊겼으면 남은 채널을 두드리지 않는다.
                # 사용자가 취소한 게 아니므로 status는 `done`이고, 사유만 경고로 남긴다.
                if stats.get("aborted_429"):
                    aborted = True
                    remain = len(groups) - gi - 1
                    self._update(job, aborted_429=True)
                    self._append_warning(
                        job, f"429 연속 차단 — 남은 채널 {remain}개 건너뜀 "
                             f"(차단된 채널: {name})")
                    log.warning(f"⛔ 429 연속 차단 — 남은 채널 {remain}개 건너뜀")
                    break

            with self._lock:
                for k in _STAT_KEYS:
                    job["stats"][k] = agg[k]
                if not cancelled and not aborted:
                    job["done"] = base_done

            # 변경 있는 채널만 각각 인덱싱, 취소 시 생략 (FR24.5 · FR17.9 준용)
            if index and not cancelled and changed:
                from kl_indexer import KLIndexer
                self._update(job, phase="indexing", current_title=None,
                             index_stage=None, index_done=0, index_total=0)
                for name in changed:
                    KLIndexer(name).index_all(on_progress=self._index_cb(job))
                self._update(job, current_title=None)
            self._finish(job, "cancelled" if cancelled else "done")
        except Exception as exc:
            log.error(f"✗ 작업 실패: {exc}")
            self._finish(job, "error", error=str(exc)[:300])

    def _make_group_cb(self, job: dict, agg: dict, base_done: int, total: int,
                       channel: str = None):
        """재생목록 그룹용 진행 콜백 — done·stats를 그룹 경계에서 연속 합산 (FR24.5)."""
        def cb(payload: dict) -> bool:
            payload = payload or {}
            with self._lock:
                if payload.get("phase"):
                    job["phase"] = payload["phase"]
                job["total"] = total                 # run()의 그룹 total로 덮어쓰지 않음
                if payload.get("done") is not None:
                    job["done"] = base_done + payload["done"]
                if "current_title" in payload:
                    job["current_title"] = payload["current_title"]
                st = payload.get("stats") or {}
                for k in _STAT_KEYS:
                    if k in st:
                        job["stats"][k] = agg[k] + st[k]
                if payload.get("event"):
                    # 재생목록 이벤트에는 원채널 표기 (FR26.2)
                    self._append_event_locked(job, dict(payload["event"],
                                                        channel=channel))
            return not self._cancel.is_set()
        return cb

    def _make_cb(self, job: dict):
        """progress 콜백 — 락 하에 job 갱신 + 취소 여부 반환. FR18.1·18.2"""
        def cb(payload: dict) -> bool:
            payload = payload or {}
            with self._lock:
                if payload.get("phase"):
                    job["phase"] = payload["phase"]
                if payload.get("total") is not None:
                    job["total"] = payload["total"]
                if payload.get("done") is not None:
                    job["done"] = payload["done"]
                if "current_title" in payload:
                    job["current_title"] = payload["current_title"]
                for k in _STAT_KEYS:
                    st = payload.get("stats") or {}
                    if k in st:
                        job["stats"][k] = st[k]
                if payload.get("event"):
                    self._append_event_locked(job, payload["event"])   # FR26.2
            return not self._cancel.is_set()
        return cb

    # ── 단일영상 워커 (FR17.2) ───────────────────────────────────────────────
    def _run_single(self, job: dict, url: str, vid: str, index: bool):
        try:
            import yt_dlp
            Extractor = _app_extractor().Extractor

            self._update(job, total=1, phase="registering")
            with yt_dlp.YoutubeDL(_probe_opts()) as ydl:      # full info 1회
                info = ydl.extract_info(url, download=False)

            channel_url = self._channel_url_from_info(info)
            reg = ChannelRegistry()
            name = reg.resolve_name(channel_url)              # 등록명 우선 (FR32.3)
            if name not in reg.names():                       # 기존 항목 덮어쓰기 금지
                reg.add(channel_url, lang=config.DEFAULT_LANG)
                log.info(f"✅ 채널 등록: {name}")
            ch_cfg = reg.get(name)
            self._update(job, channel=name)

            title = info.get("title") or vid
            content_type = "live" if info.get("live_status") == "was_live" else "video"
            ext = Extractor(ch_cfg)

            if self._cancel.is_set():
                self._finish(job, "cancelled")
                return

            self._update(job, phase="extracting", current_title=title)
            _REASONS = {"new": "신규 추출", "date_skip": "기간 조건 밖",
                        "live_wait": "라이브 종료 대기 — 다음 run에서 재시도",
                        "no_sub": "자막 없음"}
            try:
                result = ext.process_video(vid, "new", content_type=content_type, info=info)
                if result == "ok":
                    self._bump(job, "new")
                    kind = "new"
                elif result in _STAT_KEYS:
                    self._bump(job, result)
                    kind = result
                else:
                    self._bump(job, "no_sub")
                    kind = "no_sub"
                self._append_event(job, {"id": vid, "title": title, "kind": kind,
                                         "reason": _REASONS.get(kind, kind)})
            except Exception as exc:
                msg = str(exc)
                # full info의 availability가 1차 신호, 메시지는 폴백 (DQ-38)
                if Extractor._is_members_only(msg, info.get("availability")):
                    log.info(f"  🔒 멤버십 전용 (스킵): {vid}")
                    ext._mark_skip(vid, "members_only")
                    self._bump(job, "members_only")
                    self._append_event(job, {"id": vid, "title": title,
                                             "kind": "members_only",
                                             "reason": "멤버십 전용 — 접근 불가"})
                else:
                    log.error(f"  ✗ 오류 {vid}: {msg[:80]}")
                    self._bump(job, "error")
                    self._update(job, error=msg[:300])
                    self._append_event(job, {"id": vid, "title": title,
                                             "kind": "error", "reason": msg[:120]})
            ext.state.save()
            self._update(job, done=1, current_title=None)

            cancelled = self._cancel.is_set()
            self._maybe_index(job, name, index, cancelled)
            self._finish(job, "cancelled" if cancelled else "done",
                         error=job.get("error"))
        except Exception as exc:
            log.error(f"✗ 작업 실패: {exc}")
            self._finish(job, "error", error=str(exc)[:300])

    def _bump(self, job: dict, key: str, n: int = 1):
        with self._lock:
            job["stats"][key] = job["stats"].get(key, 0) + n

    _EVENTS_CAP = 1000

    def _append_warning(self, job: dict, message: str):
        """작업 경고 축적 (FR35.13) — 예외로 작업 전체를 죽이지 않고 노출만 한다."""
        with self._lock:
            job.setdefault("warnings", []).append(message)

    def _append_event(self, job: dict, ev: dict):
        """영상별 결과 이벤트 축적 (FR26.2). 호출자가 락을 잡지 않았을 때 사용."""
        with self._lock:
            self._append_event_locked(job, ev)

    def _append_event_locked(self, job: dict, ev: dict):
        events = job.setdefault("events", [])
        events.append(ev)
        if len(events) > self._EVENTS_CAP:
            del events[:len(events) - self._EVENTS_CAP]

    @staticmethod
    def _channel_url_from_info(info: dict) -> str:
        """
        info에서 채널 등록용 URL 조립.
        uploader_id('@handle')를 그대로 add()에 넘기면 깨진 URL이 저장되므로
        반드시 https://www.youtube.com/@handle 형태로 만든다.
        """
        uploader_id = (info.get("uploader_id") or "").strip()
        if uploader_id.startswith("@"):
            return f"https://www.youtube.com/{uploader_id}"
        for key in ("channel_url", "uploader_url"):
            val = (info.get(key) or "").strip()
            if val:
                return val
        raise ValueError("영상 정보에서 채널 URL을 찾을 수 없습니다.")


def _copy(obj):
    """job dict 복사 — 응답 중 워커가 갱신해도 스냅샷이 흔들리지 않도록."""
    if isinstance(obj, dict):
        return {k: _copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return list(obj)
    return obj


# 모듈 싱글턴 (단일 uvicorn 프로세스 전제)
MANAGER = JobManager()

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
from urllib.parse import unquote

import config
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
_HANDLE_RE  = re.compile(r"@[^/?&\s]+")
_CHANNEL_RE = re.compile(r"/channel/(UC[\w-]+)")

# 스캔 엔트리의 availability 중 멤버십 전용으로 볼 값 (FR17.6)
_MEMBERS_AVAILABILITY = ("subscriber_only", "needs_auth", "premium_only")

_STAT_KEYS = ("new", "updated", "skip", "no_sub", "members_only", "error",
              "date_skip", "live_wait")


class JobBusyError(Exception):
    """실행 중인 작업이 있을 때 (409). FR17.7"""

    def __init__(self, job: dict = None, message: str = "이미 실행 중인 작업이 있습니다."):
        super().__init__(message)
        self.job = job
        self.message = message


# ─── URL 분류 (FR17.1) ───────────────────────────────────────────────────────
def classify_url(url: str) -> tuple:
    """
    URL을 영상/재생목록/채널로 분류. 판별 불가 시 ValueError.
      ("video", video_id) | ("playlist", url) | ("channel", url)
    우선순위: 영상 → 재생목록 → 채널 (FR24.1)
    — `watch?v=…&list=…`는 단일 영상으로 처리 (기존 동작 유지).
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
    if _HANDLE_RE.search(decoded) or _CHANNEL_RE.search(decoded):
        return ("channel", raw)
    raise ValueError(f"영상·재생목록·채널 URL로 판별할 수 없습니다: {raw[:80]}")


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
    """스캔 엔트리의 availability로 멤버십 전용 판별. FR17.6"""
    av = str(availability or "").lower()
    return any(k in av for k in _MEMBERS_AVAILABILITY)


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


def _entry_channel(e: dict):
    """
    flat 엔트리에서 (채널명, 채널 URL) 해석. FR24.3
    uploader_id(@핸들) 우선 → channel_id(UC…) 폴백 → 해석 불가 시 None.
    """
    uid = (e.get("uploader_id") or "").strip()
    if uid.startswith("@"):
        return uid[1:], f"https://www.youtube.com/{uid}"
    cid = (e.get("channel_id") or "").strip()
    if cid.startswith("UC"):
        return cid, f"https://www.youtube.com/channel/{cid}"
    return None


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

    # ── 사전 스캔 (FR17.3 채널 · FR24.2 재생목록) ────────────────────────────
    def scan(self, url: str) -> dict:
        kind, _ = classify_url(url)          # 판별 불가 → ValueError(400)
        if kind == "video":
            raise ValueError("영상 URL은 /extract 로 바로 추출하세요.")
        self._acquire()
        try:
            if kind == "playlist":
                return self._do_scan_playlist(url)
            return self._do_scan(url)
        finally:
            self._release()

    def _do_scan(self, url: str) -> dict:
        Extractor = _app_extractor().Extractor

        name = ChannelRegistry.extract_handle(url)     # 등록은 하지 않는다
        ch_cfg = {"name": name,
                  "url": ChannelRegistry.normalize_url(url),
                  "lang": config.DEFAULT_LANG}
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
        from state_manager import StateManager

        log.info(f"🔍 재생목록 스캔: {url[:70]}")
        with yt_dlp.YoutubeDL(_flat_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
        title = (info.get("title") or "").strip() or "재생목록"
        entries = [e for e in (info.get("entries") or []) if e.get("id")]

        videos_view, by_channel, states = [], {}, {}
        for e in entries:
            # 진행 중/예약 라이브는 자막 미완성 → 제외 (FR16.3 준용)
            if e.get("live_status") in ("is_live", "is_upcoming"):
                continue
            ch = _entry_channel(e)
            if not ch:
                log.warning(f"  ⚠ 채널 불명 → 제외: {e.get('id')}")
                continue
            name, ch_url = ch
            e["content_type"] = e.get("content_type") or "video"
            by_channel.setdefault(name, {"url": ch_url, "entries": []})["entries"].append(e)
            if name not in states:
                states[name] = (StateManager(name).state
                                if config.channel_dir(name).exists() else {})
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
            })

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
            if entry.get("kind") == "playlist":        # FR24.3
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
            "stats": {k: 0 for k in _STAT_KEYS},
            "events": [],           # 영상별 결과 이벤트 (FR26.2, 캡 1000)
            "error": None,
            "started_at": _now_iso(),
            "finished_at": None,
        }

    # ── 상태·취소 (FR18.3·FR17.8) ────────────────────────────────────────────
    def status(self) -> dict:
        snap = self._snapshot()
        return snap if snap is not None else {"status": "idle"}

    def is_busy(self) -> bool:
        """추출·스캔 점유 여부 — 삭제(FR21)가 작업 중 파일 정리와 겹치지 않도록 가드."""
        with self._lock:
            return self._busy

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

    def _maybe_index(self, job: dict, channel: str, index: bool, cancelled: bool):
        """완료 후 자동 인덱싱 (FR17.9). 취소 시에는 생략한다."""
        if not index or cancelled:
            return
        with self._lock:
            changed = job["stats"]["new"] + job["stats"]["updated"]
        if changed <= 0:
            return
        from kl_indexer import KLIndexer
        self._update(job, phase="indexing", current_title=None)
        KLIndexer(channel).index_all()

    # ── 채널 워커 ────────────────────────────────────────────────────────────
    def _run_channel(self, job: dict, entry: dict, filters: dict, index: bool):
        channel = entry["channel"]
        try:
            Extractor = _app_extractor().Extractor

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
            stats = ext.run(entries=target_entries, pl_map=entry["pl_map"],
                            date_range=date_range, progress=self._make_cb(job))
            self._merge_stats(job, stats)

            cancelled = bool((stats or {}).get("cancelled")) or self._cancel.is_set()
            self._maybe_index(job, channel, index, cancelled)
            self._finish(job, "cancelled" if cancelled else "done")
        except Exception as exc:
            log.error(f"✗ 작업 실패: {exc}")
            self._finish(job, "error", error=str(exc)[:300])

    # ── 재생목록 워커 (FR24.3~24.5) ──────────────────────────────────────────
    def _run_playlist(self, job: dict, entry: dict, filters: dict, index: bool):
        """채널별 그룹 순차 실행 — 결과물은 각 영상의 원채널 폴더에 저장."""
        pl_title = entry["playlist_title"]
        try:
            Extractor = _app_extractor().Extractor

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
            agg = {k: 0 for k in _STAT_KEYS}     # 완료 그룹 누계 (진행 콜백이 합산)
            base_done = 0
            changed = []                          # new+updated>0 채널 → 인덱싱 대상
            cancelled = False
            for name, ch_url, g_entries in groups:
                if self._cancel.is_set():
                    cancelled = True
                    break
                # 미등록 채널은 추출 시점에 자동 등록 + 재생목록 폴더 지정 (FR25.7)
                # (기존 등록 채널의 폴더는 건드리지 않는다)
                if name not in reg.names():
                    reg.add(ch_url, lang=config.DEFAULT_LANG)
                    reg.set_group(name, pl_title)
                    log.info(f"✅ 채널 등록: {name} (폴더: {pl_title})")
                try:
                    ch_cfg = reg.get(name)
                except KeyError:                 # pragma: no cover - 방어적 폴백
                    ch_cfg = {"name": name,
                              "url": ChannelRegistry.normalize_url(ch_url),
                              "lang": config.DEFAULT_LANG}

                pl_map = _merged_pl_map(name, [e["id"] for e in g_entries], pl_title)
                self._update(job, phase="extracting")
                ext = Extractor(ch_cfg)
                stats = ext.run(entries=g_entries, pl_map=pl_map,
                                date_range=date_range,
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

            with self._lock:
                for k in _STAT_KEYS:
                    job["stats"][k] = agg[k]
                if not cancelled:
                    job["done"] = base_done

            # 변경 있는 채널만 각각 인덱싱, 취소 시 생략 (FR24.5 · FR17.9 준용)
            if index and not cancelled and changed:
                from kl_indexer import KLIndexer
                self._update(job, phase="indexing", current_title=None)
                for name in changed:
                    KLIndexer(name).index_all()
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
            name = ChannelRegistry.extract_handle(channel_url)
            reg = ChannelRegistry()
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
                if Extractor._is_members_only(msg):
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

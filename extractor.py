"""자막·메타·설명 추출 핵심. FR1, FR2."""
import re
import csv
import json
import time
import random
import logging
import urllib.request
import http.cookiejar

import yt_dlp

import config
import cookie_health
import subtitle_utils as su
from state_manager import StateManager
from meta_collector import MetaCollector

log = logging.getLogger("extractor")


class Extractor:

    def __init__(self, channel_config: dict):
        self.channel = channel_config["name"]
        self.url = channel_config["url"]
        self.lang = channel_config.get("lang", config.DEFAULT_LANG)
        self.dirs = config.channel_subdirs(self.channel)
        self.state = StateManager(self.channel)
        self.meta = MetaCollector(self.channel)
        self._log_path = config.channel_dir(self.channel) / "extract_log.csv"
        self._init_dirs()

    def _ydl_opts(self, **extra) -> dict:
        """공통 yt-dlp 옵션 + 쿠키(있으면) + 추가 옵션을 병합."""
        opts = {**config.YTDLP_COMMON, **extra}
        ff = config.firefox_profile_dir()
        if ff:
            # Firefox 프로필에서 매 실행 최신 쿠키를 직접 읽는다 (FR13.6).
            # ro 마운트여도 yt-dlp가 DB를 임시 복사해 읽으므로 안전하다.
            opts["cookiesfrombrowser"] = ("firefox", ff, None, None)
        else:
            # 쿠키 파일이 존재하면 인증에 사용 (FR13.3: 없으면 비로그인 폴백)
            # 원본은 읽기전용일 수 있어 쓰기 가능한 작업본 경로를 사용
            cookiefile = config.resolve_cookiefile()
            if cookiefile:
                opts["cookiefile"] = cookiefile
        # 쿠키 무효 경고는 stderr 직행이라 logging 핸들러로 못 잡는다 →
        # logger 어댑터 주입이 유일한 감지 수단 (FR19.2)
        opts["logger"] = cookie_health.YDLLogger(log)
        return opts

    def _init_dirs(self):
        for d in ("srt", "txt"):
            self.dirs[d].mkdir(parents=True, exist_ok=True)
        if not self._log_path.exists():
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(
                    ["video_id", "upload_date", "title", "action", "sub_type", "status", "basename"]
                )

    def _log_row(self, row: list):
        with open(self._log_path, "a", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(row)

    # ── 채널 영상 목록 ───────────────────────────────────────────────────────
    def _channel_base(self) -> str:
        """등록 URL에서 탭 접미사를 제거한 채널 루트 URL."""
        return re.sub(r"/(videos|streams|shorts)/?$", "", self.url)

    def _scan_tab(self, url: str) -> list:
        opts = self._ydl_opts(extract_flat=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return [e for e in (info.get("entries") or []) if e.get("id")]

    @staticmethod
    def _is_no_tab(msg: str) -> bool:
        """채널에 해당 탭이 없을 때의 yt-dlp 에러 판별. FR16.2"""
        m = msg.lower()
        return "does not have" in m and "tab" in m

    def scan_channel(self) -> list:
        """videos + streams 두 탭 병합 스캔. FR16.1"""
        base = self._channel_base()
        merged = {}
        for content_type, tab_url in (("video", f"{base}/videos"),
                                      ("live", f"{base}/streams")):
            try:
                entries = self._scan_tab(tab_url)
            except Exception as exc:
                if content_type == "live" and self._is_no_tab(str(exc)):
                    log.info("  ℹ streams 탭 없음 → 라이브 스캔 생략")
                    continue
                raise
            for e in entries:
                vid = e["id"]
                # 진행 중/예약 라이브는 자막 미완성 → 제외 (FR16.3)
                if e.get("live_status") in ("is_live", "is_upcoming"):
                    continue
                if vid in merged:
                    continue
                e["content_type"] = content_type
                merged[vid] = e
        return list(merged.values())

    # ── 재생목록 → 영상 매핑 (FR15.1) ────────────────────────────────────────
    def scan_playlists(self) -> dict:
        """채널 재생목록을 스캔해 video_id → [재생목록 제목] 매핑 생성."""
        base = self._channel_base()
        try:
            playlists = self._scan_tab(f"{base}/playlists")
        except Exception as exc:
            if self._is_no_tab(str(exc)):
                log.info("  ℹ playlists 탭 없음 → 카테고리 매핑 생략")
                return {}
            raise
        log.info(f"재생목록: {len(playlists)}개 스캔")

        mapping = {}
        for pl in playlists:
            title = pl.get("title") or pl["id"]
            pl_url = pl.get("url") or f"https://www.youtube.com/playlist?list={pl['id']}"
            try:
                entries = self._scan_tab(pl_url)
            except Exception as exc:
                log.warning(f"  ⚠ 재생목록 스캔 실패 '{title}': {str(exc)[:60]}")
                continue
            for e in entries:
                lst = mapping.setdefault(e["id"], [])
                if title not in lst:
                    lst.append(title)

        # 디버깅·재사용용 저장
        out_path = config.channel_dir(self.channel) / "playlists.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        return mapping

    # ── 기존 meta 백필 (FR15.5) ──────────────────────────────────────────────
    def _backfill_meta(self, mapping: dict) -> int:
        """이미 추출된 meta/*.json에 playlists·content_type 필드 갱신."""
        meta_dir = self.dirs["meta"]
        if not meta_dir.exists():
            return 0
        updated = 0
        for meta_file in meta_dir.glob("*.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            new_pls = mapping.get(meta.get("id"), [])
            changed = False
            if meta.get("playlists") != new_pls:
                meta["playlists"] = new_pls
                changed = True
            if "content_type" not in meta:
                meta["content_type"] = "video"
                changed = True
            if changed:
                meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                updated += 1
        if updated:
            log.info(f"  🔁 기존 meta 백필: {updated}건 (playlists·content_type)")
        return updated

    # ── 자막 URL 선택 (수동 우선 → 자동) FR1.2 ────────────────────────────────
    def _pick_subtitle(self, info: dict):
        subs = info.get("subtitles", {})
        autos = info.get("automatic_captions", {})
        for lang in (self.lang, config.FALLBACK_LANG):
            if lang in subs and subs[lang]:
                return self._vtt_url(subs[lang]), "manual"
        for lang in (self.lang, config.FALLBACK_LANG):
            if lang in autos and autos[lang]:
                return self._vtt_url(autos[lang]), "auto"
        return None, "none"

    @staticmethod
    def _vtt_url(fmt_list: list):
        for fmt in fmt_list:
            if fmt.get("ext") == "vtt":
                return fmt.get("url")
        return fmt_list[0].get("url") if fmt_list else None

    # ── 단일 영상 처리 ───────────────────────────────────────────────────────
    def process_video(self, vid: str, action: str = "new",
                      content_type: str = "video",
                      playlists_map: dict = None,
                      info: dict = None,
                      date_range: dict = None) -> str:
        url = f"https://www.youtube.com/watch?v={vid}"
        # info가 주어지면 조회를 생략 (FR17.2: 단일영상 워커가 이미 받은 info 재사용)
        if info is None:
            opts = self._ydl_opts(skip_download=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

        # 진행 중·예약 라이브는 자막이 완성되지 않았으므로 추출하지 않는다 (FR16.5).
        # state에 기록하지 않는 것이 핵심 — 여기서 no_sub로 기록하면 종료 후에도
        # decide()가 영원히 skip해 같은 링크 재추출이 막힌다 (채널 스캔의 is_live
        # 제외(FR16.3)와 달리 단일 URL 경로에는 이 가드가 없었다)
        if info.get("live_status") in ("is_live", "is_upcoming") or info.get("is_live"):
            title = info.get("title", vid)
            self._log_row([vid, "-", title, action, "-", "live_wait", "-"])
            log.info(f"  📡 라이브 진행/예약 중 (종료 후 추출 가능): {title[:40]}")
            return "live_wait"

        # flat 스캔이 못 잡은 라이브 다시보기 보정 (FR16.4)
        if info.get("live_status") == "was_live":
            content_type = "live"

        title = info.get("title", vid)
        upload_date = info.get("upload_date", "00000000")
        basename = su.make_basename(upload_date, title)

        # 기간 조건은 처리 시 full info 날짜로 확정 (FR17.5, DQ-12).
        # 범위 밖이면 자막을 받지 않고 종료하며 state에는 기록하지 않는다
        # (조건을 바꾼 다음 실행에서 다시 대상이 되어야 하므로)
        if self._out_of_range(upload_date, date_range):
            self._log_row([vid, upload_date, title, action, "-", "date_skip", "-"])
            log.info(f"  ⏭ 기간 외 (스킵): {title[:40]}")
            return "date_skip"

        sub_url, sub_type = self._pick_subtitle(info)

        if not sub_url:
            self._log_row([vid, upload_date, title, action, "none", "no_subtitle", "-"])
            self.state.mark_done(vid, {"upload_date": upload_date,
                                       "modified_date": info.get("modified_date"),
                                       "sub_type": "none", "basename": basename})
            log.warning(f"  ⚠ 자막 없음: {title[:40]}")
            return "no_sub"

        # VTT → SRT → TXT
        vtt = self._fetch_vtt(sub_url)
        srt = su.vtt_to_srt(vtt)
        txt = su.srt_to_txt(srt)

        (self.dirs["srt"] / f"{basename}.srt").write_text(srt, encoding="utf-8")
        (self.dirs["txt"] / f"{basename}.txt").write_text(txt, encoding="utf-8")

        # 메타·설명 저장
        meta = self.meta.save(info, basename, sub_type,
                              playlists=(playlists_map or {}).get(vid, []),
                              content_type=content_type)
        meta["basename"] = basename
        self.state.mark_done(vid, meta)

        self._log_row([vid, upload_date, title, action, sub_type, "ok", basename])
        icon = "📝" if sub_type == "manual" else "🤖"
        log.info(f"  {icon} [{action:7s}][{sub_type}] {basename}")
        return "ok"

    # ── 자막 VTT 다운로드 (쿠키+브라우저 UA, FR13.4) ─────────────────────────
    def _fetch_vtt(self, sub_url: str) -> str:
        # 자막 엔드포인트는 yt-dlp의 sleep 옵션이 적용되지 않는 별도 요청이므로
        # 자체 딜레이 + 쿠키 + 브라우저 UA로 429를 방어한다
        time.sleep(config.SUB_FETCH_SLEEP_SEC)
        jar = None
        ff = config.firefox_profile_dir()
        if ff:
            try:
                # 자막 직접 다운로드에도 Firefox의 현재 쿠키를 사용 (FR13.6·13.4)
                from yt_dlp.cookies import extract_cookies_from_browser
                jar = extract_cookies_from_browser("firefox", profile=ff)
            except Exception:
                jar = None   # 읽기 실패 시 cookies.txt → 비로그인 순 폴백
        if jar is None:
            jar = http.cookiejar.MozillaCookieJar()
            cookiefile = config.resolve_cookiefile()
            if cookiefile:
                try:
                    jar.load(cookiefile, ignore_discard=True, ignore_expires=True)
                except Exception:
                    pass   # 쿠키 파싱 실패 시 비로그인 폴백 (FR13.3)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req = urllib.request.Request(sub_url, headers={"User-Agent": config.SUB_FETCH_UA})
        with opener.open(req) as resp:
            return resp.read().decode("utf-8")

    # ── 진행 보고 (FR18.1, FR18.2) ───────────────────────────────────────────
    @staticmethod
    def _report(progress, phase: str, done: int, total: int,
                current_title, stats: dict) -> bool:
        """
        진행 콜백 호출. 반환 False = 취소 요청.
        progress가 None이면 아무 것도 하지 않고 True (CLI 경로 무영향, FR18.1).
        콜백 내부 예외는 추출을 죽이지 않고 '계속 진행'으로 간주한다.
        """
        if progress is None:
            return True
        try:
            return bool(progress({
                "phase": phase,
                "done": done,
                "total": total,
                "current_title": current_title,
                "stats": dict(stats),   # 스냅샷 — 폴링 중 동시 변경 방지
            }))
        except Exception as exc:
            log.warning(f"  ⚠ 진행 콜백 오류(무시): {str(exc)[:60]}")
            return True

    # ── 채널 전체 실행 ───────────────────────────────────────────────────────
    def run(self, force_vid: str = None, limit: int = None,
            progress=None, entries: list = None, pl_map: dict = None,
            date_range: dict = None) -> dict:
        """
        채널 증분 추출. 신규 인자가 모두 None이면 기존 CLI 동작과 완전 동일 (FR18.1).

        progress   : progress(payload:dict) -> bool. False 반환 시 우아한 취소 (FR18.2)
        entries    : 주어지면 scan_channel() 생략 (대시보드 스캔 캐시 재사용, DQ-13)
        pl_map     : 주어지면(빈 dict 포함) scan_playlists() 생략
        date_range : {"since": "YYYYMMDD"|None, "until": "YYYYMMDD"|None} (DQ-12)
        """
        log.info(f"━━━ 채널: {self.channel} ━━━")

        stats = {"new": 0, "updated": 0, "skip": 0, "no_sub": 0,
                 "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0}
        cancelled = False

        if entries is None:
            if self._report(progress, "scanning", 0, 0, None, stats):
                entries = self.scan_channel()
            else:
                cancelled = True
                entries = []
        total = len(entries)
        log.info(f"총 영상: {total}개")

        if pl_map is None:
            # 재생목록 카테고리 매핑 (FR15.1)
            if not cancelled and self._report(progress, "playlists", 0, total, None, stats):
                pl_map = self.scan_playlists()
            else:
                cancelled = True
                pl_map = {}

        consecutive_429 = 0   # 연속 429 카운터 (FR13.5)
        aborted = False
        # --limit은 429 방어를 위한 "요청 예산" 가드다 (FR14.5).
        # 따라서 extract_info 요청을 1회 소비한 모든 경로(성공·무자막·기간외·
        # 멤버십·429·기타 오류)가 예산을 소비해야 한다 → 시도 직전에 증가시킨다
        processed = 0         # extract_info 요청을 소비한 시도 수 — --limit 기준
        since_rest = 0        # 마지막 배치 휴식 이후 시도 수 (실패 포함) — 휴식 기준
        done = 0              # 처리 완료 수 (스킵 포함) — 진행률 기준
        # 배치 크기는 매 배치 재추첨 — 고정 주기는 차단 탐지의 기계 서명 (FR14.2)
        batch_size = random.randint(*config.BATCH_SIZE_RANGE)

        for i, entry in enumerate(entries, 1):
            if cancelled:
                break
            vid = entry["id"]

            # 강제 재처리 대상이면 상태 삭제
            if force_vid and vid == force_vid:
                self.state.remove(vid)

            # 메타데이터 일부만 빠르게 확인 (수정 감지용)
            mod_date = entry.get("modified_date")
            up_date = entry.get("upload_date")
            action = self.state.decide(vid, mod_date, up_date)

            if action == "skip":
                stats["skip"] += 1
                done += 1
                continue

            # 카나리아 실행 (FR14.5): 처리 수가 limit에 도달하면 안전하게 종료
            if limit and processed >= limit:
                log.info(f"  🐤 --limit {limit} 도달 → 카나리아 종료 (나머지는 다음 run에서 이어받기)")
                break

            # 배치 휴식 (FR14.2): 랜덤 개수 시도마다 랜덤 시간 쉼
            if since_rest >= batch_size:
                rest = random.randint(*config.BATCH_REST_RANGE)
                log.info(f"  💤 {since_rest}개 처리 → {rest}초 휴식 (차단 예방)")
                time.sleep(rest)
                since_rest = 0
                batch_size = random.randint(*config.BATCH_SIZE_RANGE)

            # 영상 처리 직전 진행 보고 — False면 우아한 취소 (FR18.2)
            if not self._report(progress, "extracting", done, total,
                                entry.get("title"), stats):
                cancelled = True
                break

            since_rest += 1
            processed += 1                   # 요청 예산 소비 (성공·실패 무관)
            retried_429 = False              # 429 재시도는 영상당 1회 (FR14.3)
            while True:
                try:
                    result = self.process_video(
                        vid, action,
                        content_type=entry.get("content_type", "video"),
                        playlists_map=pl_map,
                        date_range=date_range)
                    consecutive_429 = 0          # 성공 시 카운터 리셋
                    if result == "ok":
                        stats[action] += 1
                    elif result in ("date_skip", "live_wait"):
                        stats[result] += 1
                    else:
                        stats["no_sub"] += 1
                except Exception as exc:
                    msg = str(exc)
                    # 멤버십 전용 영상은 오류가 아니라 접근 불가로 분류
                    if self._is_members_only(msg):
                        log.info(f"  🔒 멤버십 전용 (스킵): {vid}")
                        self._log_row([vid, "-", "-", action, "-", "members_only", "-"])
                        self._mark_skip(vid, "members_only")
                        stats["members_only"] += 1
                        consecutive_429 = 0
                    elif self._is_429(msg):
                        consecutive_429 += 1
                        log.warning(f"  ⏳ 429 차단 ({consecutive_429}/{config.CONSECUTIVE_429_LIMIT}): {vid}")
                        self._log_row([vid, "-", "-", action, "-", "error:429", "-"])
                        # 연속 429가 임계 초과하면 추출 중단 (차단 악화 방지)
                        if consecutive_429 >= config.CONSECUTIVE_429_LIMIT:
                            stats["error"] += 1
                            aborted = True
                            break
                        # 지수 백오프 (FR14.3): 429가 연속될수록 더 오래 대기
                        backoff = config.BACKOFF_BASE_SEC * consecutive_429
                        log.warning(f"  ⏸  {backoff}초 대기 후 재개 (백오프)")
                        time.sleep(backoff)
                        # 일시적 429가 대부분이므로 같은 영상을 1회 재시도 (FR14.3)
                        # 재시도도 extract_info 1회를 쓰므로 예산·휴식 카운터 소비 (DQ-16)
                        if not retried_429:
                            retried_429 = True
                            processed += 1
                            since_rest += 1
                            log.info(f"  🔁 같은 영상 재시도: {vid}")
                            continue
                        stats["error"] += 1      # 재시도도 실패 → 이번 run에서 포기
                    else:
                        log.error(f"  ✗ 오류 {vid}: {msg[:80]}")
                        self._log_row([vid, "-", "-", action, "-", f"error:{msg[:60]}", "-"])
                        stats["error"] += 1
                        consecutive_429 = 0
                break
            if aborted:
                break

            done += 1
            # 영상 처리 직후 진행 보고 (done·stats 갱신)
            if not self._report(progress, "extracting", done, total,
                                entry.get("title"), stats):
                cancelled = True
                break

        # 취소여도 state 저장·백필은 정상 수행 (FR18.2)
        self._report(progress, "finishing", done, total, None, stats)
        self.state.save()
        # 매핑이 비면(탭 없음 등) 기존 값을 지우지 않도록 백필 생략 (FR15.5)
        if pl_map:
            self._backfill_meta(pl_map)
        summary = (f"  ✅ 신규 {stats['new']} · 🔄 수정 {stats['updated']} · "
                   f"⏭ 스킵 {stats['skip']} · ⚠ 무자막 {stats['no_sub']} · "
                   f"🔒 멤버십 {stats['members_only']} · ✗ 오류 {stats['error']}")
        if stats["date_skip"]:
            summary += f" · ⏭ 기간외 {stats['date_skip']}"
        if stats["live_wait"]:
            summary += f" · 📡 라이브 대기 {stats['live_wait']}"
        log.info(summary)
        if aborted:
            log.warning("━" * 50)
            log.warning(f"  ⛔ 연속 429 차단 {config.CONSECUTIVE_429_LIMIT}회 → 추출을 중단했습니다.")
            log.warning("  YouTube가 요청을 일시 차단한 상태입니다.")
            log.warning("  💡 30분~1시간 후 './yt.sh run' 으로 이어받기 하세요.")
            log.warning("  💡 cookies.txt 를 추가하면 차단이 크게 줄어듭니다.")
            log.warning("━" * 50)
        if cancelled:
            stats["cancelled"] = True
        return stats

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _is_429(msg: str) -> bool:
        """HTTP 429 (요청 과다) 에러 판별."""
        return "429" in msg or "too many requests" in msg.lower()

    @staticmethod
    def _out_of_range(upload_date: str, date_range: dict) -> bool:
        """기간 조건(YYYYMMDD) 범위 밖인지 판정. 날짜 불명이면 판정 불가 → 통과."""
        if not date_range:
            return False
        ud = str(upload_date or "").strip()
        if not ud or ud == "00000000":
            return False
        since = date_range.get("since")
        until = date_range.get("until")
        if since and ud < str(since):
            return True
        if until and ud > str(until):
            return True
        return False

    @staticmethod
    def _is_members_only(msg: str) -> bool:
        """멤버십 전용 영상 에러 판별."""
        keywords = ["members-only", "members only", "channel's members",
                    "Join this channel", "available to this channel"]
        return any(k.lower() in msg.lower() for k in keywords)

    def _mark_skip(self, vid: str, reason: str):
        """접근 불가 영상을 state에 기록해 다음 실행 시 재시도 방지."""
        self.state.state[vid] = {
            "upload_date": "00000000",
            "modified_date": reason,   # 사유를 modified_date에 기록 (수정감지 시 동일 처리)
            "sub_type": reason,
            "extracted_at": "",
            "basename": "",
        }

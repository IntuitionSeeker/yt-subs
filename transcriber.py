"""무자막 영상 Whisper 전사 폴백. FR30 — CLI 전용(transcribe 명령)."""
import time
import shutil
import logging
import tempfile
from pathlib import Path

import yt_dlp

import config
import subtitle_utils as su
from extractor import Extractor

log = logging.getLogger("transcriber")


def _srt_ts(sec: float) -> str:
    """초 → SRT 타임스탬프 (HH:MM:SS,mmm)."""
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments) -> str:
    """faster-whisper 세그먼트([{start,end,text}] 호환) → SRT 문자열."""
    blocks = []
    for i, seg in enumerate(segments, 1):
        start = seg.start if hasattr(seg, "start") else seg["start"]
        end = seg.end if hasattr(seg, "end") else seg["end"]
        text = (seg.text if hasattr(seg, "text") else seg["text"]).strip()
        if not text:
            continue
        blocks.append(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    return "\n".join(blocks)


# ─── 진행률 (FR30.6) ─────────────────────────────────────────────────────────
def progress_percent(t, duration) -> int:
    """처리 시각(초) / 전체 길이(초) → 0~100 정수. 비정상 입력은 0."""
    try:
        tt, d = float(t), float(duration)
    except (TypeError, ValueError):
        return 0
    if d <= 0 or tt <= 0:
        return 0
    return max(0, min(100, round((tt / d) * 100)))


def _clock(sec) -> str:
    """초 → 분:초 (시가 있으면 시:분:초). 진행 로그 표기용."""
    try:
        sec = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        sec = 0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _seg_end(seg):
    """세그먼트의 end (faster-whisper 객체 / dict 양쪽 지원)."""
    if hasattr(seg, "end"):
        return seg.end
    return seg.get("end") if isinstance(seg, dict) else None


def with_progress(segments, duration, on_progress=None, log_step: int = 10):
    """세그먼트를 흘려보내며 진행률을 보고한다.

    faster-whisper의 segments는 **지연 생성자**라 소비하는 동안 실제 전사가 진행된다 —
    여기서 통과시키는 것 외에 따로 순회하면 안 된다(두 번 돌거나 비어버린다).
    진행률은 세그먼트 end 기준이라 단조 증가하지만, VAD가 건너뛴 무음 구간 때문에
    경과 시간과 정비례하지는 않는다(자막 시각 기준 진척도).
    duration을 모르면(0) 로그는 생략하고 콜백만 0으로 흘린다.
    """
    next_log = log_step
    for seg in segments:
        yield seg
        pct = progress_percent(_seg_end(seg), duration)
        if on_progress:
            on_progress(pct)
        if duration and pct >= next_log:
            log.info(f"    … 전사 {pct}% ({_clock(_seg_end(seg))} / {_clock(duration)})")
            next_log = (pct // log_step) * log_step + log_step


class Transcriber:
    """채널의 sub_type=none(무자막) 영상을 전사해 기존 파이프라인 산출물로 저장."""

    def __init__(self, channel_cfg: dict):
        # Extractor 재사용: dirs·state·meta·쿠키 옵션·로그 경로 (FR30.2)
        self.ext = Extractor(channel_cfg)
        self.channel = channel_cfg["name"]
        self.lang = channel_cfg.get("lang", config.DEFAULT_LANG)
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info(f"🎤 Whisper 모델 로드: {config.WHISPER_MODEL} (CPU {config.WHISPER_COMPUTE})")
            self._model = WhisperModel(config.WHISPER_MODEL, device="cpu",
                                       compute_type=config.WHISPER_COMPUTE)
        return self._model

    def targets(self) -> list:
        """state에서 sub_type=none 영상 vid 목록 (기록 순)."""
        return [vid for vid, st in self.ext.state.state.items()
                if (st or {}).get("sub_type") == "none"]

    def _download_audio(self, vid: str, tmpdir: str):
        """bestaudio 다운로드 → (오디오 경로, full info). 쿠키·딜레이는 _ydl_opts 재사용."""
        opts = self.ext._ydl_opts(
            format="bestaudio/best",
            outtmpl=str(Path(tmpdir) / "%(id)s.%(ext)s"),
        )
        url = f"https://www.youtube.com/watch?v={vid}"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = sorted(Path(tmpdir).glob(f"{vid}.*"))
        if not files:
            raise RuntimeError("오디오 파일 다운로드 실패")
        return files[0], info

    def transcribe_video(self, vid: str, on_progress=None) -> str:
        tmpdir = tempfile.mkdtemp(prefix="ytsub_audio_")
        try:
            audio_path, info = self._download_audio(vid, tmpdir)
            title = info.get("title", vid)
            upload_date = info.get("upload_date", "00000000")
            basename = su.make_basename(upload_date, title)
            log.info(f"  🎤 전사 시작: {title[:40]} (영상당 수 분 소요)")

            model = self._load_model()
            segments, winfo = model.transcribe(str(audio_path), language=self.lang,
                                               vad_filter=True)
            # info.duration 을 알아야 진행률을 낼 수 있다 (FR30.6)
            duration = getattr(winfo, "duration", 0) or 0
            srt = segments_to_srt(with_progress(segments, duration, on_progress))
            if not srt.strip():
                raise RuntimeError("전사 결과가 비어 있음")
            txt = su.srt_to_txt(srt)

            (self.ext.dirs["srt"] / f"{basename}.srt").write_text(srt, encoding="utf-8")
            (self.ext.dirs["txt"] / f"{basename}.txt").write_text(txt, encoding="utf-8")
            # 무자막 영상은 process_video가 meta를 쓰지 않으므로 **여기서 처음
            # 만들어진다** — 출처는 `transcribe` (FR39.2)
            meta = self.ext.meta.save(info, basename, "whisper",
                                      origin_entry={"kind": "transcribe"})
            meta["basename"] = basename
            self.ext.state.mark_done(vid, meta)
            self.ext._log_row([vid, upload_date, title, "transcribe", "whisper",
                               "ok", basename])
            log.info(f"  ✅ 전사 완료: {basename}")
            return "ok"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def run(self, limit: int = None, on_progress=None) -> dict:
        vids = self.targets()
        if limit:
            vids = vids[:limit]
        stats = {"ok": 0, "error": 0, "total": len(vids)}
        if not vids:
            log.info(f"━━━ {self.channel}: 무자막 영상 없음 ━━━")
            return stats
        log.info(f"━━━ Whisper 전사: {self.channel} — 대상 {len(vids)}개 ━━━")
        for i, vid in enumerate(vids, 1):
            try:
                self.transcribe_video(vid, on_progress=on_progress)
                stats["ok"] += 1
            except Exception as exc:
                stats["error"] += 1
                msg = str(exc)
                log.error(f"  ✗ 전사 실패 {vid}: {msg[:80]}")
                self.ext._log_row([vid, "-", "-", "transcribe", "whisper",
                                   f"error:{msg[:60]}", "-"])
                if Extractor._is_429(msg):
                    log.warning("  ⛔ 429 차단 — 전사를 중단합니다. 나중에 다시 시도하세요.")
                    break
            if i < len(vids):
                time.sleep(5)     # 영상 간 소휴식 (오디오 다운로드 부하 분산)
        self.ext.state.save()
        log.info(f"  ✅ 전사 {stats['ok']} · ✗ 실패 {stats['error']} / 대상 {stats['total']}")
        return stats

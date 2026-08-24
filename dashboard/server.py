"""웹 대시보드 백엔드 — FastAPI. FR11·FR17~21."""
import sys
import json
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from channel_registry import ChannelRegistry
from state_manager import StateManager
from kl_query import KLQuery
from kl_harness import KLHarness

from jobs import MANAGER, JobBusyError

app = FastAPI(title="YouTube KL Dashboard")

_DASH_DIR = Path(__file__).parent

# F-8: 이 프로세스가 어느 이미지 빌드로 떠 있는지 기동 로그에 남긴다.
# `dashboard/`는 serve 컨테이너에 라이브 마운트되지만 루트 .py(extractor.py 등)는
# 이미지에 구워진 채로 남기 때문에, 코드를 고쳐 재빌드해도 실행 중인 컨테이너를
# 재기동하지 않으면 구코드가 계속 서빙된다 — 이 로그가 `docker logs`에서 그 상태를 드러낸다.
_build_time_file = config.BASE_DIR / ".build_time"
_build_time = _build_time_file.read_text().strip() if _build_time_file.exists() else "알 수 없음(로컬 실행)"
print(f"🐳 이미지 빌드 시각: {_build_time} — 코드 재빌드 후에는 컨테이너도 재기동해야 반영됩니다.")


# ─── 요청 모델 ───────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    channel: str
    question: str


class SearchRequest(BaseModel):
    channel: str
    query: str
    top_k: int = 5
    since: str | None = None
    until: str | None = None
    category: str | None = None   # 재생목록 부분일치 필터 (FR15.4)


class SummaryRequest(BaseModel):
    channel: str
    video_id: str


class ScanRequest(BaseModel):
    url: str


class Filters(BaseModel):
    latest: int | None = None
    since: str | None = None          # YYYYMMDD
    until: str | None = None          # YYYYMMDD
    categories: list[str] = []
    include_members: bool = False
    keyword: str | None = None


class ExtractRequest(BaseModel):
    url: str | None = None
    scan_id: str | None = None
    filters: Filters | None = None
    index: bool = True


class VideoDeleteRequest(BaseModel):
    channel: str
    basename: str


class ChannelDeleteRequest(BaseModel):
    channel: str
    purge: bool = False


class ChannelGroupRequest(BaseModel):
    channel: str
    group: str | None = None      # 트림 후 빈 값이면 폴더 해제 (FR25.2)


class ChannelRenameRequest(BaseModel):
    channel: str
    new_name: str


class VideoRenameRequest(BaseModel):
    channel: str
    basename: str
    new_title: str


class CategoryRenameRequest(BaseModel):
    channels: list[str]
    old: str
    new: str


class FolderRenameRequest(BaseModel):
    old: str
    new: str


def _reject_path_traversal(*values: str):
    """경로 파라미터 1차 검증 — 자막 조회(FR20.3)·삭제(FR21) 엔드포인트 공통."""
    for value in values:
        if (not value or ".." in value or "/" in value or "\\" in value
                or Path(value).is_absolute()):
            raise HTTPException(status_code=400, detail="잘못된 경로 파라미터입니다.")


# ─── 엔드포인트 (FR11.2) ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return (_DASH_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/version")
def version():
    """이 컨테이너가 서빙 중인 이미지의 빌드 시각 (F-8: 재기동 누락 감지용)."""
    return {"build_time": _build_time}


@app.get("/channels")
def channels():
    reg = ChannelRegistry()
    return {"channels": list(reg.names())}


@app.get("/videos")
def videos(channel: str, since: str = None, until: str = None):
    kl = KLQuery(channel)
    return {"videos": kl.list_videos(since=since, until=until)}


@app.post("/search")
def search(req: SearchRequest):
    kl = KLQuery(req.channel)
    results = kl.search(req.query, top_k=req.top_k, since=req.since,
                        until=req.until, category=req.category)
    return {"results": results}


@app.post("/ask")
def ask(req: AskRequest):
    """멀티스텝 하네스 호출. FR11.3"""
    harness = KLHarness(req.channel)
    result = harness.run(req.question)
    return result


@app.post("/summary")
def summary(req: SummaryRequest):
    kl = KLQuery(req.channel)
    return {"summary": kl.summarize(video_id=req.video_id)}


# ─── 추출 (FR17·FR18) ────────────────────────────────────────────────────────
@app.post("/extract/scan")
def extract_scan(req: ScanRequest):
    """채널 사전 스캔 → 후보 목록·재생목록 + scan_id (FR17.3)."""
    try:
        return MANAGER.scan(req.url)
    except JobBusyError as exc:
        return JSONResponse(status_code=409,
                            content={"detail": exc.message, "job": exc.job})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/extract")
def extract(req: ExtractRequest):
    """추출 시작 — 단일 영상({url}) 또는 채널({scan_id, filters}). FR17.2·17.4·17.7"""
    try:
        job = MANAGER.start({
            "url": req.url,
            "scan_id": req.scan_id,
            "filters": req.filters.model_dump() if req.filters else None,
            "index": req.index,
        })
    except JobBusyError as exc:
        return JSONResponse(status_code=409,
                            content={"detail": exc.message, "job": exc.job})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(status_code=202, content={"job": job})


@app.get("/extract/status")
def extract_status():
    """진행 폴링 (FR18.3) — 마지막 job 유지, idle은 기동 후 무작업일 때만."""
    return {"job": MANAGER.status()}


@app.post("/extract/cancel")
def extract_cancel():
    """우아한 취소 — 현재 영상 완료 후 중단 (FR17.8)."""
    return {"cancelled": MANAGER.cancel()}


# ─── 쿠키 상태 (FR19.3) ──────────────────────────────────────────────────────
@app.get("/cookies")
def cookies():
    import cookie_health           # 지연 임포트 (선택 모듈)
    return cookie_health.get_status()


# ─── 라이브러리 (FR20) ───────────────────────────────────────────────────────
@app.get("/channels/stats")
def channels_stats():
    """채널 목록(registry 기준) + state.json 집계 통계. FR20.1"""
    reg = ChannelRegistry()
    out = []
    for name, ch in reg.list().items():
        ch = ch or {}
        state = StateManager(name).state
        extracted = members_only = no_sub = 0
        last = ""
        for item in state.values():
            sub_type = item.get("sub_type")
            if sub_type in ("manual", "auto", "whisper"):    # DQ-18
                extracted += 1
            elif sub_type == "members_only":
                members_only += 1
            elif sub_type == "none":
                no_sub += 1
            at = item.get("extracted_at") or ""
            if at and at > last:
                last = at
        out.append({
            "name": name,
            "url": ch.get("url", ""),
            "lang": ch.get("lang", config.DEFAULT_LANG),
            "added_at": ch.get("added_at", ""),
            "group": ch.get("group", ""),          # 채널 폴더 (FR25.3)
            "extracted": extracted,
            "members_only": members_only,
            "no_sub": no_sub,
            "total_known": len(state),
            "last_extracted": last,
        })
    return {"channels": out}


@app.get("/channels/new")
def channels_new():
    """RSS로 등록 채널의 새 영상 감지 (수동 트리거 전용). FR29.2"""
    import rss_monitor            # 지연 임포트
    return rss_monitor.check_new_videos()


@app.post("/channels/group")
def channels_group(req: ChannelGroupRequest):
    """채널 폴더 지정/변경/해제. FR25.2"""
    reg = ChannelRegistry()
    try:
        group = reg.set_group(req.channel, req.group)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 채널: {req.channel}")
    return {"ok": True, "channel": req.channel, "group": group}


# ─── 이름 변경 (FR31) ────────────────────────────────────────────────────────
def _reject_if_busy():
    """추출/스캔 작업 중 이름 변경 금지 — 파일 경합 방지. FR31.5"""
    if MANAGER.is_busy():
        raise HTTPException(status_code=409,
                            detail="추출/스캔 작업 중에는 이름을 변경할 수 없습니다.")


@app.post("/channels/rename")
def channels_rename(req: ChannelRenameRequest):
    """채널 이름 변경 — 레지스트리 + output 폴더. FR31.1"""
    _reject_if_busy()
    _reject_path_traversal(req.channel, req.new_name.strip() or req.new_name)
    import renamer
    try:
        renamer.rename_channel(req.channel, req.new_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 채널: {req.channel}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "channel": req.new_name.strip()}


@app.post("/videos/rename")
def videos_rename(req: VideoRenameRequest):
    """영상 제목 변경 — meta.title + 인덱스 metadata (파일명 유지). FR31.2"""
    _reject_if_busy()
    _reject_path_traversal(req.channel, req.basename)
    import renamer
    try:
        result = renamer.rename_video_title(req.channel, req.basename, req.new_title)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@app.post("/categories/rename")
def categories_rename(req: CategoryRenameRequest):
    """카테고리 이름 변경 — 채널 목록 일괄. FR31.3"""
    _reject_if_busy()
    _reject_path_traversal(*req.channels)
    import renamer
    try:
        result = renamer.rename_category(req.channels, req.old, req.new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@app.post("/folders/rename")
def folders_rename(req: FolderRenameRequest):
    """폴더(그룹) 이름 변경. FR31.4"""
    _reject_if_busy()
    import renamer
    try:
        count = renamer.rename_folder(req.old, req.new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "channels": count}


@app.post("/videos/delete")
def delete_video(req: VideoDeleteRequest):
    """단일 영상 삭제 — 원본 파일·state·인덱스를 함께 정리한다. FR21.1"""
    if MANAGER.is_busy():
        raise HTTPException(status_code=409, detail="추출 작업 중에는 삭제할 수 없습니다.")
    _reject_path_traversal(req.channel, req.basename)

    dirs = config.channel_subdirs(req.channel)
    meta_path = (dirs["meta"] / f"{req.basename}.json").resolve()
    if not meta_path.is_relative_to(dirs["meta"].resolve()) or not meta_path.exists():
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    video_id = json.loads(meta_path.read_text(encoding="utf-8")).get("id", req.basename)

    for key, ext in (("srt", "srt"), ("txt", "txt"), ("meta", "json"), ("desc", "txt")):
        (dirs[key] / f"{req.basename}.{ext}").unlink(missing_ok=True)

    state = StateManager(req.channel)
    state.remove(video_id)
    state.save()

    pl_path = config.channel_dir(req.channel) / "playlists.json"
    if pl_path.exists():
        pl_map = json.loads(pl_path.read_text(encoding="utf-8"))
        if pl_map.pop(video_id, None) is not None:
            pl_path.write_text(json.dumps(pl_map, ensure_ascii=False, indent=2), encoding="utf-8")

    from kl_indexer import KLIndexer
    KLIndexer(req.channel).delete_video(video_id)

    return {"deleted": True, "video_id": video_id}


@app.post("/channels/delete")
def delete_channel(req: ChannelDeleteRequest):
    """채널 등록 해제. `purge=true`면 output 폴더까지 완전 삭제한다(되돌릴 수 없음). FR21.2"""
    if MANAGER.is_busy():
        raise HTTPException(status_code=409, detail="추출 작업 중에는 삭제할 수 없습니다.")
    _reject_path_traversal(req.channel)

    reg = ChannelRegistry()
    if not reg.remove(req.channel):
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")

    purged = False
    if req.purge:
        ch_dir = config.channel_dir(req.channel).resolve()
        if ch_dir.is_relative_to(config.OUTPUT_BASE.resolve()) and ch_dir.exists():
            shutil.rmtree(ch_dir)
            purged = True
    return {"deleted": True, "purged": purged}


def _load_meta(channel: str, basename: str) -> dict:
    """meta/*.json 로드 (경로 검증 포함). 없거나 파싱 실패 시 {}."""
    meta_dir = config.channel_subdirs(channel)["meta"].resolve()
    path = (meta_dir / f"{basename}.json").resolve()
    if not path.is_relative_to(meta_dir) or not path.exists():
        return {}
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.get("/subtitle")
def subtitle(channel: str, basename: str):
    """자막 전문(txt) + 챕터·원본 링크 반환. 경로 탈출 2중 검증. FR20.3·FR27.2"""
    _reject_path_traversal(channel, basename)
    txt_dir = config.channel_subdirs(channel)["txt"].resolve()
    path = (txt_dir / f"{basename}.txt").resolve()
    if not path.is_relative_to(txt_dir):                 # resolve 후 재확인
        raise HTTPException(status_code=400, detail="잘못된 경로 파라미터입니다.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="자막 파일이 없습니다.")
    meta = _load_meta(channel, basename)
    return {"basename": basename, "text": path.read_text(encoding="utf-8"),
            "chapters": meta.get("chapters") or [],
            "url": meta.get("webpage_url")}


@app.get("/export/markdown")
def export_markdown(channel: str, basename: str):
    """영상 1개를 Markdown 문서로 조립. FR28.1"""
    _reject_path_traversal(channel, basename)
    txt_dir = config.channel_subdirs(channel)["txt"].resolve()
    path = (txt_dir / f"{basename}.txt").resolve()
    if not path.is_relative_to(txt_dir):
        raise HTTPException(status_code=400, detail="잘못된 경로 파라미터입니다.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="자막 파일이 없습니다.")
    meta = _load_meta(channel, basename)
    url = meta.get("webpage_url") or ""
    ud = str(meta.get("upload_date") or "")
    date_str = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}" if len(ud) == 8 else ud

    lines = [f"# {meta.get('title') or basename}", ""]
    info_bits = []
    if url:
        info_bits.append(f"[원본 영상]({url})")
    if date_str:
        info_bits.append(f"업로드 {date_str}")
    info_bits.append(f"채널 {meta.get('channel') or channel}")
    if meta.get("playlists"):
        info_bits.append("카테고리 " + " · ".join(meta["playlists"]))
    if meta.get("tickers"):
        info_bits.append("종목 " + " ".join(meta["tickers"]))
    lines += ["> " + " | ".join(info_bits), ""]

    chapters = meta.get("chapters") or []
    if chapters and url:
        lines += ["## 챕터", ""]
        for ch in chapters:
            s = int(ch.get("start") or 0)
            ts = f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 \
                else f"{s // 60:02d}:{s % 60:02d}"
            lines.append(f"- [{ts}]({url}&t={s}s) {ch.get('title') or ''}")
        lines.append("")

    lines += ["## 자막 전문", "", path.read_text(encoding="utf-8")]
    return {"filename": f"{basename}.md", "markdown": "\n".join(lines)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)

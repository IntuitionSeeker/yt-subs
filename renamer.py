"""이름 변경 — 채널·영상 제목·카테고리·폴더. FR31.

파일(meta·playlists.json·output 폴더)과 ChromaDB metadata를 함께 갱신한다.
ChromaDB 갱신은 재임베딩 없는 metadata 병합(KLIndexer.update_video_metadata)."""
import json
import logging

import config
from channel_registry import ChannelRegistry

log = logging.getLogger("renamer")


def _indexer(channel: str):
    from kl_indexer import KLIndexer     # 지연 임포트 (chromadb 무거움)
    return KLIndexer(channel)


def rename_channel(old: str, new: str):
    """채널 이름 변경 — channels.yaml 키 + output 폴더 이동. FR31.1"""
    new = (new or "").strip()
    if not new:
        raise ValueError("새 이름이 비어 있습니다.")
    old_dir = config.channel_dir(old)
    new_dir = config.channel_dir(new)
    if new_dir.exists():
        raise ValueError(f"이미 존재하는 폴더: {new}")
    ChannelRegistry().rename(old, new)   # 미등록 KeyError · 중복 ValueError
    if old_dir.exists():
        old_dir.rename(new_dir)
    log.info(f"✏️ 채널 이름 변경: {old} → {new}")


def rename_video_title(channel: str, basename: str, new_title: str) -> dict:
    """영상 제목 변경 — meta.title + chroma metadata. 파일명은 유지. FR31.2"""
    new_title = (new_title or "").strip()
    if not new_title:
        raise ValueError("새 제목이 비어 있습니다.")
    meta_path = config.channel_subdirs(channel)["meta"] / f"{basename}.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"메타 파일 없음: {basename}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["title"] = new_title
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    chunks = 0
    if meta.get("id"):
        chunks = _indexer(channel).update_video_metadata(meta["id"],
                                                         {"title": new_title})
    log.info(f"✏️ 영상 제목 변경: {basename} → {new_title[:40]} (청크 {chunks})")
    return {"title": new_title, "chunks": chunks}


def rename_category(channels: list, old: str, new: str) -> dict:
    """카테고리(재생목록 태그) 이름 변경 — 채널 목록에 일괄 적용. FR31.3"""
    new = (new or "").strip()
    if not new:
        raise ValueError("새 이름이 비어 있습니다.")
    videos = chunks = 0
    for channel in channels:
        subdirs = config.channel_subdirs(channel)
        # playlists.json 값 치환
        pl_path = config.channel_dir(channel) / "playlists.json"
        if pl_path.exists():
            try:
                mapping = json.loads(pl_path.read_text(encoding="utf-8"))
                changed = False
                for vid, lst in mapping.items():
                    if old in (lst or []):
                        mapping[vid] = [new if p == old else p for p in lst]
                        changed = True
                if changed:
                    pl_path.write_text(json.dumps(mapping, ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
            except Exception:                 # pragma: no cover
                pass
        # meta/*.json playlists 배열 치환 + chroma 동기화
        meta_dir = subdirs["meta"]
        if not meta_dir.exists():
            continue
        idx = None
        for meta_file in meta_dir.glob("*.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:                 # pragma: no cover
                continue
            pls = meta.get("playlists") or []
            if old not in pls:
                continue
            meta["playlists"] = [new if p == old else p for p in pls]
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            videos += 1
            if meta.get("id"):
                if idx is None:
                    idx = _indexer(channel)
                chunks += idx.update_video_metadata(
                    meta["id"], {"playlists": ", ".join(meta["playlists"])})
    log.info(f"✏️ 카테고리 변경: {old} → {new} (영상 {videos} · 청크 {chunks})")
    return {"videos": videos, "chunks": chunks}


def rename_folder(old: str, new: str) -> int:
    """폴더(그룹) 이름 변경 — group=old인 채널 전체. FR31.4"""
    new = (new or "").strip()
    if not new:
        raise ValueError("새 이름이 비어 있습니다.")
    reg = ChannelRegistry()
    count = 0
    for name, ch in reg.list().items():
        if (ch or {}).get("group") == old:
            reg.set_group(name, new)
            count += 1
    log.info(f"✏️ 폴더 이름 변경: {old} → {new} (채널 {count})")
    return count

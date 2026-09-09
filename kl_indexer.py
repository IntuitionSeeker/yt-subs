"""ChromaDB 인덱싱 — 2개 컬렉션. FR6."""
import json
import logging

import config
import subtitle_utils as su

log = logging.getLogger("indexer")


class KLIndexer:

    def __init__(self, channel: str):
        self.channel = channel
        self.dirs = config.channel_subdirs(channel)
        self._client = None
        self._embed = None

    # ── 지연 로딩 (무거운 의존성) ────────────────────────────────────────────
    def _get_client(self):
        if self._client is None:
            import chromadb
            self.dirs["chroma"].mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.dirs["chroma"]))
        return self._client

    def _get_embedder(self):
        if self._embed is None:
            from sentence_transformers import SentenceTransformer
            log.info(f"임베딩 모델 로드: {config.EMBED_MODEL}")
            self._embed = SentenceTransformer(config.EMBED_MODEL)
        return self._embed

    def embed(self, texts: list) -> list:
        return self._get_embedder().encode(texts, normalize_embeddings=True).tolist()

    def _collection(self, name: str):
        return self._get_client().get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    # ── 이름 변경 메타 동기화 (FR31.2·31.3) ──────────────────────────────────
    def update_video_metadata(self, video_id: str, fields: dict) -> int:
        """해당 영상의 모든 청크 metadata에 fields를 병합 (재임베딩 없음).
        인덱스가 없거나 청크가 없으면 0 — 이름 변경을 막지 않는다."""
        if not self.dirs["chroma"].exists():
            return 0
        updated = 0
        for col_name in (config.COL_SUBTITLE, config.COL_DESC):
            try:
                col = self._collection(col_name)
                got = col.get(where={"video_id": video_id})
                ids = got.get("ids") or []
                if not ids:
                    continue
                metas = [{**m, **fields} for m in got["metadatas"]]
                col.update(ids=ids, metadatas=metas)
                updated += len(ids)
            except Exception as exc:      # pragma: no cover - 인덱스 손상 방어
                log.warning(f"  ⚠ chroma 메타 갱신 실패({col_name}): {str(exc)[:60]}")
        return updated

    # ── 증분 판정 (FR33.1~33.2, DQ-21) ───────────────────────────────────────
    @staticmethod
    def _unchanged(col, vid: str, ids: list, docs: list, metas: list) -> bool:
        """
        이 영상이 이미 같은 내용으로 인덱싱돼 있으면 True → 임베딩·upsert 생략.

        해시 필드를 새로 심지 않고 **저장된 문서 본문·메타를 그대로 대조**한다.
        스키마를 바꾸지 않으므로 기존 인덱스가 마이그레이션 없이 즉시 혜택을 받고,
        판정이 근사가 아니라 정확하다 (청크 수가 같은 다른 자막도 잡아낸다).
        조회 실패는 "변경됨"으로 보수적으로 처리한다.
        """
        try:
            got = col.get(where={"video_id": vid}, include=["documents", "metadatas"])
        except Exception:                 # pragma: no cover - 인덱스 손상 방어
            return False
        old_ids = got.get("ids") or []
        if len(old_ids) != len(ids):
            return False
        old_docs = dict(zip(old_ids, got.get("documents") or []))
        old_metas = dict(zip(old_ids, got.get("metadatas") or []))
        for _id, doc, meta in zip(ids, docs, metas):
            if old_docs.get(_id) != doc:
                return False
            om = old_metas.get(_id) or {}
            if any(om.get(k) != v for k, v in meta.items()):
                return False
        return True

    # ── 메타 로드 헬퍼 ───────────────────────────────────────────────────────
    def _load_meta(self, basename: str) -> dict:
        meta_path = self.dirs["meta"] / f"{basename}.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
        return {}

    # ── 자막 인덱싱 (FR6.2: SRT 120초 윈도우) ────────────────────────────────
    def index_subtitles(self, on_progress=None):
        col = self._collection(config.COL_SUBTITLE)
        srt_dir = self.dirs["srt"]
        if not srt_dir.exists():
            return 0

        files = sorted(srt_dir.glob("*.srt"))
        total, skipped, n = 0, 0, len(files)
        for fi, srt_file in enumerate(files, 1):
            basename = srt_file.stem
            meta = self._load_meta(basename)
            vid = meta.get("id", basename)
            title = meta.get("title", basename)
            upload_date = meta.get("upload_date", "00000000")
            sub_type = meta.get("sub_type", "unknown")
            # ChromaDB 메타데이터는 리스트 불가 → 쉼표 join 문자열 (FR15.3)
            playlists = ", ".join(meta.get("playlists") or [])
            content_type = meta.get("content_type", "video")

            chunks = su.chunk_by_srt(srt_file.read_text(encoding="utf-8"))
            if not chunks:
                continue

            docs, ids, metas = [], [], []
            for i, c in enumerate(chunks):
                docs.append(c["text"])
                ids.append(f"{vid}_{i}")
                metas.append({
                    "video_id": vid,
                    "title": title,
                    "upload_date": upload_date,
                    "sub_type": sub_type,
                    "playlists": playlists,
                    "content_type": content_type,
                    "chunk_index": i,
                    "start_seconds": c["start_sec"],
                    "source_url": f"https://youtube.com/watch?v={vid}&t={c['start_sec']}s",
                })

            if on_progress:
                on_progress("subtitle", fi, n, title)
            if self._unchanged(col, vid, ids, docs, metas):   # FR33.1
                skipped += 1
                continue

            embs = self.embed(docs)
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
            total += len(docs)
            log.info(f"  📑 {basename}: {len(docs)}청크")
        if skipped:
            log.info(f"  ⏭ 변경 없어 건너뜀: {skipped}/{n}개")   # FR33.5
        return total

    # ── 설명 인덱싱 (FR6.5: desc_chunks) ─────────────────────────────────────
    def index_descriptions(self, on_progress=None):
        col = self._collection(config.COL_DESC)
        desc_dir = self.dirs["desc"]
        if not desc_dir.exists():
            return 0

        files = sorted(desc_dir.glob("*.txt"))
        total, skipped, n = 0, 0, len(files)
        for fi, desc_file in enumerate(files, 1):
            basename = desc_file.stem
            meta = self._load_meta(basename)
            vid = meta.get("id", basename)
            title = meta.get("title", basename)
            upload_date = meta.get("upload_date", "00000000")
            playlists = ", ".join(meta.get("playlists") or [])
            content_type = meta.get("content_type", "video")

            chunks = su.chunk_text(desc_file.read_text(encoding="utf-8"))
            if not chunks:
                continue

            docs, ids, metas = [], [], []
            for i, text in enumerate(chunks):
                docs.append(text)
                ids.append(f"{vid}_desc_{i}")
                metas.append({
                    "video_id": vid,
                    "title": title,
                    "upload_date": upload_date,
                    "playlists": playlists,
                    "content_type": content_type,
                    "chunk_index": i,
                    "source_url": f"https://youtube.com/watch?v={vid}",
                })

            if on_progress:
                on_progress("desc", fi, n, title)
            if self._unchanged(col, vid, ids, docs, metas):   # FR33.1
                skipped += 1
                continue

            embs = self.embed(docs)
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
            total += len(docs)
        if skipped:
            log.info(f"  ⏭ 설명 변경 없어 건너뜀: {skipped}/{n}개")   # FR33.5
        return total

    # ── 삭제 (FR21.1) ────────────────────────────────────────────────────────
    def delete_video(self, video_id: str):
        """단일 영상의 청크를 두 컬렉션에서 제거 (영상 삭제 시 인덱스 정리)."""
        for col_name in (config.COL_SUBTITLE, config.COL_DESC):
            self._collection(col_name).delete(where={"video_id": video_id})

    def index_all(self, on_progress=None) -> dict:
        """on_progress(stage, done, total, title) — 선택 인자. CLI는 전달하지 않는다 (FR33.4)."""
        log.info(f"━━━ KL 인덱싱: {self.channel} ━━━")
        sub_n = self.index_subtitles(on_progress)
        desc_n = self.index_descriptions(on_progress)
        log.info(f"  ✅ 자막 {sub_n}청크 · 설명 {desc_n}청크")
        return {"subtitle": sub_n, "desc": desc_n}

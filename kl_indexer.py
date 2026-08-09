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

    # ── 메타 로드 헬퍼 ───────────────────────────────────────────────────────
    def _load_meta(self, basename: str) -> dict:
        meta_path = self.dirs["meta"] / f"{basename}.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
        return {}

    # ── 자막 인덱싱 (FR6.2: SRT 120초 윈도우) ────────────────────────────────
    def index_subtitles(self):
        col = self._collection(config.COL_SUBTITLE)
        srt_dir = self.dirs["srt"]
        if not srt_dir.exists():
            return 0

        total = 0
        for srt_file in sorted(srt_dir.glob("*.srt")):
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

            embs = self.embed(docs)
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
            total += len(docs)
            log.info(f"  📑 {basename}: {len(docs)}청크")
        return total

    # ── 설명 인덱싱 (FR6.5: desc_chunks) ─────────────────────────────────────
    def index_descriptions(self):
        col = self._collection(config.COL_DESC)
        desc_dir = self.dirs["desc"]
        if not desc_dir.exists():
            return 0

        total = 0
        for desc_file in sorted(desc_dir.glob("*.txt")):
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

            embs = self.embed(docs)
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
            total += len(docs)
        return total

    # ── 삭제 (FR21.1) ────────────────────────────────────────────────────────
    def delete_video(self, video_id: str):
        """단일 영상의 청크를 두 컬렉션에서 제거 (영상 삭제 시 인덱스 정리)."""
        for col_name in (config.COL_SUBTITLE, config.COL_DESC):
            self._collection(col_name).delete(where={"video_id": video_id})

    def index_all(self) -> dict:
        log.info(f"━━━ KL 인덱싱: {self.channel} ━━━")
        sub_n = self.index_subtitles()
        desc_n = self.index_descriptions()
        log.info(f"  ✅ 자막 {sub_n}청크 · 설명 {desc_n}청크")
        return {"subtitle": sub_n, "desc": desc_n}

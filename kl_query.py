"""KL 질의 인터페이스 — 검색·요약·RAG 답변. FR9, FR12."""
import os
import json
import logging

import config

log = logging.getLogger("query")


class KLQuery:
    """
    KL 질의 도구 모음. CLI·대시보드·하네스가 공통 사용. FR9.6
    """

    def __init__(self, channel: str):
        self.channel = channel
        self.dirs = config.channel_subdirs(channel)
        self._client = None
        self._embed = None

    # ── 지연 로딩 ────────────────────────────────────────────────────────────
    def _get_client(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(self.dirs["chroma"]))
        return self._client

    def _get_embedder(self):
        if self._embed is None:
            from sentence_transformers import SentenceTransformer
            self._embed = SentenceTransformer(config.EMBED_MODEL)
        return self._embed

    # ── 벡터 검색 (FR9.3, FR9.4: 날짜 필터) ──────────────────────────────────
    def search(self, query: str, top_k: int = 5,
               since: str = None, until: str = None,
               collection: str = None, category: str = None) -> list:
        """
        벡터 검색. 반환: [{text, title, upload_date, source_url, ...}]
        since/until: 'YYYYMMDD' 날짜 필터 (FR12.3)
        category: 재생목록 부분일치 필터 (FR15.4)
        """
        col_name = collection or config.COL_SUBTITLE
        col = self._get_client().get_or_create_collection(col_name)

        # 날짜 필터 (ChromaDB where 절)
        where = None
        conds = []
        if since:
            conds.append({"upload_date": {"$gte": since}})
        if until:
            conds.append({"upload_date": {"$lte": until}})
        if len(conds) == 1:
            where = conds[0]
        elif len(conds) > 1:
            where = {"$and": conds}

        emb = self._get_embedder().encode([query], normalize_embeddings=True).tolist()
        # ChromaDB where는 문자열 contains 미지원 → 카테고리는 여유분 조회 후
        # 클라이언트 측 부분일치 필터 (FR15.4)
        fetch_k = top_k * 4 if category else top_k
        res = col.query(query_embeddings=emb, n_results=fetch_k, where=where)

        out = []
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            if category and category not in (meta.get("playlists") or ""):
                continue
            out.append({
                "text": doc,
                "score": round(1 - dist, 3),
                **meta,
            })
        return out[:top_k]

    # ── 전체 자막 로드 (FR9.2) ───────────────────────────────────────────────
    def get_full(self, video_id: str = None, basename: str = None) -> str:
        """영상 전체 자막 텍스트 로드 (RAG 미사용)."""
        txt_dir = self.dirs["txt"]
        if basename:
            path = txt_dir / f"{basename}.txt"
            return path.read_text(encoding="utf-8") if path.exists() else ""
        # video_id로 찾기: meta 역참조
        for meta_file in self.dirs["meta"].glob("*.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("id") == video_id:
                path = txt_dir / f"{meta_file.stem}.txt"
                return path.read_text(encoding="utf-8") if path.exists() else ""
        return ""

    # ── 영상 목록 (FR11.4) ───────────────────────────────────────────────────
    def list_videos(self, since: str = None, until: str = None) -> list:
        """영상 목록 + 메타 반환 (날짜순)."""
        videos = []
        for meta_file in sorted(self.dirs["meta"].glob("*.json")):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            ud = meta.get("upload_date", "00000000")
            if since and ud < since:
                continue
            if until and ud > until:
                continue
            videos.append({
                "video_id": meta.get("id"),
                "title": meta.get("title"),
                "upload_date": ud,
                "basename": meta_file.stem,
                "tickers": meta.get("tickers", []),
                "playlists": meta.get("playlists", []),
                "content_type": meta.get("content_type", "video"),
                "sub_type": meta.get("sub_type"),   # 📝/🤖 뱃지용 (FR20.2)
                "url": meta.get("webpage_url"),
            })
        return sorted(videos, key=lambda v: v["upload_date"], reverse=True)

    # ── LLM 헬퍼 ─────────────────────────────────────────────────────────────
    @staticmethod
    def _llm(prompt: str, max_tokens: int = config.LLM_MAX_TOKENS) -> str:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    # ── RAG 답변 (FR9.1, FR9.5) ──────────────────────────────────────────────
    def ask(self, query: str, top_k: int = 5,
            since: str = None, until: str = None) -> dict:
        """검색 → 컨텍스트 주입 → LLM 답변 + 출처."""
        chunks = self.search(query, top_k=top_k, since=since, until=until)
        if not chunks:
            return {"answer": "관련 내용을 찾지 못했습니다.", "sources": []}

        context = "\n\n".join(
            f"[{c['title']} | {c['upload_date']} | {c['source_url']}]\n{c['text']}"
            for c in chunks
        )
        prompt = (
            "아래는 YouTube 영상 자막 발췌입니다. 이를 근거로 질문에 답하세요. "
            "답변에 사용한 정보의 출처(영상 제목·날짜)를 명시하세요.\n\n"
            f"=== 자막 발췌 ===\n{context}\n\n"
            f"=== 질문 ===\n{query}"
        )
        answer = self._llm(prompt)
        return {
            "answer": answer,
            "sources": [{"title": c["title"], "upload_date": c["upload_date"],
                         "url": c["source_url"], "score": c["score"]} for c in chunks],
        }

    # ── 전체 요약 (FR9.2) ────────────────────────────────────────────────────
    def summarize(self, video_id: str = None, basename: str = None) -> str:
        full = self.get_full(video_id=video_id, basename=basename)
        if not full:
            return "자막을 찾을 수 없습니다."
        prompt = (
            "다음 YouTube 영상의 전체 자막입니다. 핵심 내용을 구조적으로 요약하세요. "
            "주식/투자 관련 영상이면 언급된 종목·전망·핵심 논거를 정리하세요.\n\n"
            f"{full[:20000]}"
        )
        return self._llm(prompt)

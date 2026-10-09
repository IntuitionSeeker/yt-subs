"""KL 질의 인터페이스 — 검색·요약·RAG 답변. FR9, FR12."""
import os
import json
import logging

import config
import corrector

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
        """영상 전체 자막 텍스트 로드 (RAG 미사용).

        본문은 **교정본이 신선하면 교정본**이다(FR40.13 — 소비 단일 통로
        `corrector.pick_source`). 요약·하네스가 읽는 분석 입력이므로 인덱싱과 같은
        본문을 봐야 한다. 교정본이 없거나 stale이면 조용히 원본으로 돌아간다.
        """
        if basename:
            return corrector.read_source(self.channel, basename, kind="txt")
        # video_id로 찾기: meta 역참조
        for meta_file in self.dirs["meta"].glob("*.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("id") == video_id:
                return corrector.read_source(self.channel, meta_file.stem, kind="txt")
        return ""

    # ── 영상 목록 (FR11.4) ───────────────────────────────────────────────────
    def list_videos(self, since: str = None, until: str = None) -> list:
        """영상 목록 + 메타 반환 (날짜순)."""
        videos = []
        # 교정 상태(FR40.16) — 정본은 `fix/state.json`이고 `meta/*.json` 스키마는
        # 건드리지 않는다(FR40.24ⓔ). 교정본이 없는 채널에서는 빈 맵이라 비용이 0이다.
        fix_status = corrector.video_status(self.channel)
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
                # 영상 길이 (FR20.5) — meta.json에 이미 저장된 값을 그대로 통과시킨다.
                # 결측은 None/"" — 0으로 채우면 0초 영상과 구분 불가 (FR20.6, DQ-29).
                "duration": meta.get("duration"),
                "duration_string": meta.get("duration_string") or "",
                # 출처 (FR39.8) — meta의 값을 그대로 통과. 키가 없는 과거 meta는
                # **`[]`**(null이 아니다 — 프론트가 분기 없이 순회한다). 백필 없음.
                "origin": meta.get("origin") or [],
                # 교정 배지·토글용 (FR40.16). 교정본이 없으면 corrected=False·0건이고
                # 프론트는 배지를 그리지 않는다. stale이면 본문은 원본이 쓰인다.
                "corrected": bool(fix_status.get(meta_file.stem, {}).get("corrected")),
                "corrections": int(fix_status.get(meta_file.stem, {}).get("corrections", 0)),
                "correction_stale": bool(fix_status.get(meta_file.stem, {}).get("stale")),
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

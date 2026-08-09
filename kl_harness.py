"""멀티스텝 에이전트 하네스 — tool_use 루프. FR10."""
import re
import json
import logging

import config
from kl_query import KLQuery

log = logging.getLogger("harness")


# ─── 도구 스펙 (FR10.2) ──────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "search",
        "description": "자막 벡터 검색. 키워드/질문으로 관련 영상 구간을 찾는다. "
                       "날짜 필터(since/until, YYYYMMDD) 지원.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 또는 질문"},
                "top_k": {"type": "integer", "description": "결과 개수 (기본 5)"},
                "since": {"type": "string", "description": "시작 날짜 YYYYMMDD (선택)"},
                "until": {"type": "string", "description": "끝 날짜 YYYYMMDD (선택)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_full",
        "description": "특정 영상의 전체 자막을 로드한다. 영상 전체 내용이 필요할 때 사용.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "영상 ID"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "summarize",
        "description": "특정 영상의 전체 자막을 요약한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "영상 ID"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "list_videos",
        "description": "채널의 영상 목록(제목·날짜·종목)을 날짜순으로 가져온다. "
                       "날짜 필터 지원.",
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "시작 날짜 YYYYMMDD (선택)"},
                "until": {"type": "string", "description": "끝 날짜 YYYYMMDD (선택)"},
            },
        },
    },
]


SYSTEM_PROMPT = (
    "당신은 YouTube 자막 지식층(KL)을 검색·분석하는 어시스턴트입니다. "
    "도구를 사용해 정보를 수집하고, 멀티스텝 작업(영상 비교 → 표 생성 → 검증)을 수행합니다.\n\n"
    "원칙:\n"
    "1. 표를 만들 때는 반드시 도구로 근거 자막을 먼저 수집한다.\n"
    "2. 표 생성 후, 핵심 수치/주장은 search를 한 번 더 호출해 원본과 대조하여 검증한다(셀프 검증).\n"
    "3. 검증에서 불일치가 발견되면 해당 항목에 ⚠ 표시한다.\n"
    "4. 모든 정보에 출처(영상 제목·날짜·타임스탬프 링크)를 명시한다.\n"
    "5. 주식/투자 내용은 영상 제작자의 의견임을 명시하고, 투자 자문을 제공하지 않는다.\n"
)


class KLHarness:
    """tool_use 루프 기반 멀티스텝 에이전트. FR10.6"""

    def __init__(self, channel: str):
        self.channel = channel
        self.kl = KLQuery(channel)
        self.trace = []   # FR10.5: 도구 호출 추적

    # ── 도구 디스패처 ────────────────────────────────────────────────────────
    def _dispatch(self, name: str, args: dict):
        log.info(f"  🔧 {name}({args})")
        self.trace.append({"tool": name, "args": args})

        if name == "search":
            return self.kl.search(
                args["query"], top_k=args.get("top_k", 5),
                since=args.get("since"), until=args.get("until"),
            )
        if name == "get_full":
            return {"text": self.kl.get_full(video_id=args["video_id"])[:15000]}
        if name == "summarize":
            return {"summary": self.kl.summarize(video_id=args["video_id"])}
        if name == "list_videos":
            return self.kl.list_videos(since=args.get("since"), until=args.get("until"))
        return {"error": f"알 수 없는 도구: {name}"}

    # ── 멀티스텝 실행 (FR10.1, FR10.3) ───────────────────────────────────────
    def run(self, request: str, max_steps: int = config.HARNESS_MAX_STEPS) -> dict:
        import anthropic
        client = anthropic.Anthropic()
        self.trace = []

        messages = [{"role": "user", "content": request}]

        for step in range(max_steps):
            resp = client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=config.LLM_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if resp.stop_reason != "tool_use":
                # 최종 답변
                text = "".join(b.text for b in resp.content if b.type == "text")
                return {"answer": text, "trace": self.trace, "steps": step + 1}

            # 도구 호출 처리
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = self._dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            messages.append({"role": "user", "content": tool_results})

        return {"answer": "최대 단계 초과. 부분 결과만 수집됨.",
                "trace": self.trace, "steps": max_steps}

    # ── 코드 기반 검증 헬퍼 (FR10.4 하이브리드) ──────────────────────────────
    def verify_numbers(self, claim_text: str, video_id: str) -> dict:
        """
        표/답변의 숫자가 원본 자막에 실제 존재하는지 코드로 대조.
        반환: {number: found_bool}
        """
        full = self.kl.get_full(video_id=video_id)
        numbers = re.findall(r'\d[\d,]*\.?\d*%?', claim_text)
        result = {}
        for num in set(numbers):
            clean = num.replace(',', '')
            result[num] = (num in full) or (clean in full.replace(',', ''))
        return result

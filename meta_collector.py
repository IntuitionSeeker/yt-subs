"""영상 메타데이터·설명 수집·저장. FR3, FR12.2."""
import re
import json
import logging
import datetime

import config

log = logging.getLogger("meta")


# ─── 종목코드/티커 추출 (FR12.2) ─────────────────────────────────────────────
# 구 규칙 `\b(\d{6})\b`(6자리 숫자면 무조건 채택)은 실측 441개 meta에서 **오탐률
# 100%** 였다(날짜 22종 + 계좌번호 1종, `_workspace/34_ticker_bug.md`). 6자리 숫자는
# 날짜(YYMMDD)·계좌·전화·사업자번호·URL 조각과 형태가 같으므로 **숫자 모양만으로는
# 종목코드를 구별할 수 없다**. 따라서 "종목코드로 볼 문맥 근거"가 있을 때만 채택한다
# (DQ-43). 근거가 없으면 빈 값이며, **빈 값이 틀린 값보다 정확하다**.

_DIGIT6 = re.compile(r"(?<!\d)(\d{6})(?!\d)")          # 정확히 6자리인 숫자 덩어리
_TICKER_US = re.compile(r"\$([A-Z]{1,5})\b")            # 미국 티커 $AAPL (`$` 접두 = 근거)

# 강한 근거 ① 종목 전용 라벨이 숫자 바로 앞 (예: "종목코드: 005930", "티커 005930")
_LABEL_STRONG = re.compile(
    r"(?:종목\s?코드|단축\s?코드|종목\s?번호|티커|ticker|stock\s?code)"
    r"\s*(?:는|은|이|가)?\s*[:：=\-]?\s*$", re.I)
# 강한 근거 ② 거래소 한정 표기 (KRX:005930 / 005930.KS / 005930.KQ)
_EX_PREFIX = re.compile(r"(?:KRX|KOSPI|KOSDAQ)\s*[:：]\s*$", re.I)
_EX_SUFFIX = re.compile(r"^\.(?:KS|KQ)\b", re.I)
# 약한 근거 ① 일반 "코드" 라벨 — 쿠폰코드·바코드·인증코드는 앞 글자 때문에 매칭되지 않는다
_LABEL_WEAK = re.compile(r"(?<![가-힣A-Za-z])(?:코드|code)"
                         r"\s*(?:는|은|이|가)?\s*[:：=\-]?\s*$", re.I)
# 약한 근거 ② 괄호 안에 숫자만 (예: "삼성전자(005930)")
_PAREN_OPEN = re.compile(r"[(\[]\s*$")
_PAREN_CLOSE = re.compile(r"^\s*[)\]]")
# 약한 근거 ③ 직전에 채택된 코드와 구분자만 사이에 둔 나열 (예: "종목코드 005930, 000660")
_LIST_SEP = re.compile(r"^\s*[,·/·]?\s*(?:및|and)?\s*$")

# 배제 ① 다른 숫자 덩어리와 인접 — 계좌·전화·사업자번호 (예: "1002 763 241686")
_NUM_BEFORE = re.compile(r"(\d+)[\s/\-.,·]{1,2}$")
_NUM_AFTER = re.compile(r"^[\s/\-.,·]{1,2}(\d+)")
# 배제 ② URL 안의 숫자 조각
_URL_HINT = re.compile(r"https?://|www\.", re.I)

_CTX = 24        # 문맥 판정에 쓰는 앞뒤 문자 수


def _is_date_like(code: str) -> bool:
    """YYMMDD로 해석 가능하면 True. FR12.2 — 실측 오탐의 대부분이 제목 앞머리 날짜다."""
    mm, dd = int(code[2:4]), int(code[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31


def _numeric_neighbor(before: str, after: str) -> bool:
    """앞뒤에 6자리가 아닌 다른 숫자 덩어리가 붙어 있으면 True(숫자 나열 = 코드 아님)."""
    for m in (_NUM_BEFORE.search(before), _NUM_AFTER.match(after)):
        if m and len(m.group(1)) != 6:      # 6자리 이웃은 코드 나열일 수 있어 허용
            return True
    return False


def _token_at(text: str, start: int, end: int) -> str:
    """매치를 포함하는 공백 구분 토큰(URL 판정용)."""
    ls, le = start, end
    while ls > 0 and not text[ls - 1].isspace():
        ls -= 1
    while le < len(text) and not text[le].isspace():
        le += 1
    return text[ls:le]


def extract_tickers(text: str) -> list:
    """
    텍스트에서 종목코드/티커 추출. FR12.2 (DQ-43)

    한국 종목코드는 **문맥 근거가 있는 6자리만** 채택한다 —
      · 강한 근거: 종목 전용 라벨(`종목코드`·`단축코드`·`티커`)·거래소 표기(`KRX:`·`.KS`)
      · 약한 근거: 일반 `코드:` 라벨 · 괄호 안 숫자 단독 표기 · 채택된 코드에 이어진 나열
    강·약 공통으로 URL 조각·숫자 나열(계좌/전화)은 배제하고, **약한 근거는 YYMMDD로
    읽히는 값을 추가 배제**한다. 미국 티커는 `$` 접두 자체가 근거라 그대로 둔다.
    근거가 없으면 빈 리스트 — 그것이 정확한 결과다(느슨하게 되돌리지 말 것).
    """
    if not text:
        return []
    seen, result = set(), []
    prev_end, prev_strong = -1, False      # 나열 이어받기용 (약한 근거 ③)
    for m in _DIGIT6.finditer(text):
        code = m.group(1)
        before = text[max(0, m.start() - _CTX):m.start()]
        after = text[m.end():m.end() + _CTX]
        if _URL_HINT.search(_token_at(text, m.start(), m.end())):
            continue                                   # 배제 ②
        if _numeric_neighbor(before, after):
            continue                                   # 배제 ①
        strong = bool(_LABEL_STRONG.search(before) or _EX_PREFIX.search(before)
                      or _EX_SUFFIX.match(after))
        listed = (prev_end >= 0 and _LIST_SEP.match(text[prev_end:m.start()]) is not None)
        if listed and prev_strong:
            strong = True                              # 나열의 근거는 선두가 제공한다
        weak = listed or bool(_LABEL_WEAK.search(before)
                              or (_PAREN_OPEN.search(before)
                                  and _PAREN_CLOSE.match(after)))
        if not (strong or weak):
            continue                                   # 근거 없음 → 채택하지 않는다
        if not strong and _is_date_like(code):
            continue                                   # 날짜 배제 (약한 근거일 때)
        prev_end, prev_strong = m.end(), strong
        if code in seen:                               # 중복은 첫 등장만 남긴다
            continue
        seen.add(code)
        result.append(code)
    for t in _TICKER_US.findall(text):
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def tickers_from_meta(meta: dict, description: str = "") -> list:
    """meta dict + 설명 원문으로 `tickers` 재계산. FR12.2 — save()와 동일 입력 구성."""
    return extract_tickers(" ".join(filter(None, [
        meta.get("title") or "",
        description or "",
        " ".join(meta.get("tags") or []),
    ])))


# FR3.2 수집 필드
META_FIELDS = [
    "id", "title", "upload_date", "modified_date",
    "duration", "duration_string", "view_count",
    "like_count", "comment_count", "tags", "categories",
    "thumbnail", "webpage_url", "channel",
]


class MetaCollector:

    def __init__(self, channel: str):
        self.dirs = config.channel_subdirs(channel)

    @staticmethod
    def _normalize_chapters(chapters) -> list:
        """yt-dlp chapters → [{start:int초, end:int초, title}] (FR27.1). 없으면 []."""
        out = []
        for ch in chapters or []:
            if not isinstance(ch, dict):
                continue
            try:
                out.append({"start": int(ch.get("start_time") or 0),
                            "end": int(ch.get("end_time") or 0),
                            "title": str(ch.get("title") or "").strip()})
            except (TypeError, ValueError):
                continue
        return out

    def save(self, info: dict, basename: str, sub_type: str,
             playlists: list = None, content_type: str = "video") -> dict:
        """
        meta/*.json + desc/*.txt 저장.
        반환: 저장된 meta dict.
        """
        meta = {k: info.get(k) for k in META_FIELDS}
        meta["sub_type"] = sub_type
        meta["playlists"] = playlists or []      # 재생목록 카테고리 (FR15.2)
        meta["content_type"] = content_type      # "video" | "live" (FR16.4)
        meta["chapters"] = self._normalize_chapters(info.get("chapters"))  # FR27.1
        meta["extracted_at"] = datetime.datetime.now().isoformat(timespec="seconds")

        # 종목/티커 추출 (제목 + 설명 + 태그). 백필과 같은 함수를 쓴다 (FR12.2)
        meta["tickers"] = tickers_from_meta(meta, info.get("description") or "")

        # meta json 저장
        self.dirs["meta"].mkdir(parents=True, exist_ok=True)
        meta_path = self.dirs["meta"] / f"{basename}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 설명 저장 (FR3.3, FR3.4: 없으면 스킵)
        desc = (info.get("description") or "").strip()
        if desc:
            self.dirs["desc"].mkdir(parents=True, exist_ok=True)
            desc_path = self.dirs["desc"] / f"{basename}.txt"
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(desc)

        return meta


# ─── 기존 meta 백필 (FR12.2) ────────────────────────────────────────────────
def backfill_tickers(channel: str, apply: bool = False) -> dict:
    """
    이미 저장된 `meta/*.json`의 `tickers`를 현재 규칙으로 재계산. FR12.2 (DQ-43)

    제목·태그는 meta에, 설명은 `desc/*.txt`에 남아 있으므로 **네트워크 없이** 결정적으로
    다시 계산된다(재추출 불필요). `apply=False`(기본)면 **한 글자도 쓰지 않고** 차분만
    돌려준다 — 사용자 데이터이므로 쓰기는 명시적 플래그를 요구한다.

    반환: {scanned, changed, removed, added, samples[{basename,title,before,after}]}
    """
    dirs = config.channel_subdirs(channel)
    meta_dir, desc_dir = dirs["meta"], dirs["desc"]
    stat = {"scanned": 0, "changed": 0, "removed": 0, "added": 0, "samples": []}
    if not meta_dir.exists():
        return stat
    for meta_file in sorted(meta_dir.glob("*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:          # 깨진 파일 하나로 멈추지 않는다
            log.warning(f"  ⚠️ meta 읽기 실패: {meta_file.name} ({exc})")
            continue
        stat["scanned"] += 1
        desc_file = desc_dir / f"{meta_file.stem}.txt"
        desc = ""
        if desc_file.exists():
            try:
                desc = desc_file.read_text(encoding="utf-8")
            except OSError:
                desc = ""
        before = list(meta.get("tickers") or [])
        after = tickers_from_meta(meta, desc)
        if before == after:
            continue
        stat["changed"] += 1
        stat["removed"] += len([t for t in before if t not in after])
        stat["added"] += len([t for t in after if t not in before])
        if len(stat["samples"]) < 20:
            stat["samples"].append({"basename": meta_file.stem,
                                    "title": meta.get("title") or "",
                                    "before": before, "after": after})
        if apply:
            meta["tickers"] = after
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return stat

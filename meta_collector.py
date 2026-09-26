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


# ─── 출처 기록 (FR39) ────────────────────────────────────────────────────────
# `origin` = "이 영상의 자막·메타를 이 저장소에 들여온 추출 run의 출처". 리스트이고
# 기록은 **최초 1회**다(FR39.4). 열거 밖 kind는 조용히 섞이면 필터가 침묵으로 틀리므로
# ValueError로 거부한다(FR39.2).
ORIGIN_KINDS = ("channel", "playlist", "search", "video", "scheduler", "transcribe")
ORIGIN_MAX = 10                      # FR39.4ⓓ — 규칙상 도달 불가한 방어 상한
# kind별 **허용 부가 키**. 열거 밖 키는 저장하지 않는다(V-U37ⓕ) — 진입점이 디버그용
# 잡동사니를 실어 보내도 코퍼스에 남지 않는다.
ORIGIN_EXTRA_KEYS = {
    "channel": ("via",),                            # "cli" | "dashboard"
    "playlist": ("playlist_title", "playlist_id"),   # id는 얻어지면만 (FR39.2)
    "search": ("query", "folder"),
    "video": (),
    "scheduler": (),
    "transcribe": (),
}

# ─── 필드 소유권 (FR39.6 · DQ-59) ────────────────────────────────────────────
# `META_SCHEMA` = `meta/*.json`에 쓰는 키 **전체**(정본). 아래 두 상수가 그 전체를
# 빠짐없이 분담해야 하며, 그렇지 않으면 **모듈 임포트가 실패한다**.
#   · DERIVED   : info·규칙에서 매번 새로 계산 → 덮어써도 무손실
#   · PRESERVED : **기존 파일에서 이어받는다** — 재추출이 지우면 안 되는 필드
# 이 프로젝트는 "새로 조립해 통째로 쓰는 코드가 남의 필드를 지우는" 사고를 세 번
# 겪었다(FR7.7 `add()` · FR37 QA F1 `_finish_cycle` · DQ-42 `_save()`). 세 번 다
# "조심하자"로는 막히지 않았고, 실제로 막은 것은 F1의 임포트 시 assert였다.
META_SCHEMA = tuple(META_FIELDS) + (
    "sub_type", "playlists", "content_type", "chapters", "tickers",
    "extracted_at", "origin",
)
DERIVED_FIELDS = tuple(META_FIELDS) + (
    "sub_type", "playlists", "content_type", "chapters", "tickers", "extracted_at",
)
PRESERVED_FIELDS = ("origin",)


def _check_field_ownership(schema, derived, preserved) -> bool:
    """저장 스키마 전체가 DERIVED/PRESERVED로 **빠짐없이** 분류됐는지 (FR39.6).

    임포트 시점에 `assert`로 호출한다 — 분류하지 않은 필드를 `META_SCHEMA`에 추가하면
    `ImportError`가 난다. 문서는 읽지 않아도 되지만 임포트 실패는 무시할 수 없다.
    """
    s, d, p = set(schema), set(derived), set(preserved)
    if s - d - p:
        raise AssertionError(f"meta 필드 소유가 분류되지 않았다: {sorted(s - d - p)} "
                             f"— DERIVED_FIELDS 또는 PRESERVED_FIELDS에 넣어라 (FR39.6)")
    if d & p:
        raise AssertionError(f"필드가 두 집합에 동시에 있다: {sorted(d & p)}")
    if (d | p) - s:
        raise AssertionError(f"META_SCHEMA에 없는 필드를 분류했다: {sorted((d | p) - s)}")
    return True


assert _check_field_ownership(META_SCHEMA, DERIVED_FIELDS, PRESERVED_FIELDS)


def _origin_entry(entry: dict, at: str) -> dict:
    """출처 서술자 1개를 **저장 형태**로 정규화 (FR39.1~39.2).

    `kind` 검증 + kind별 허용 부가 키만 남기고 `at`을 채운다. `at`을 저장 계층이
    채우는 이유는 `extracted_at`과 같은 시계를 쓰기 위해서다 — 워커가 채우면 그룹
    추출에서 job 시작 시각으로 고정돼 영상별 시각이 사라진다.
    """
    kind = (entry.get("kind") or "").strip()
    if kind not in ORIGIN_KINDS:
        raise ValueError(f"알 수 없는 origin kind: {entry.get('kind')!r} "
                         f"(허용: {', '.join(ORIGIN_KINDS)})")
    out = {"kind": kind, "at": at}
    for key in ORIGIN_EXTRA_KEYS[kind]:
        val = entry.get(key)
        if val is None:
            continue
        val = str(val).strip()
        if val:                       # 빈 문자열은 키를 만들지 않는다 (FR39.2 · P3)
            out[key] = val
    return out


def _merge_origin(prev_origin, entry: dict = None, at: str = None) -> list:
    """`origin` 병합 — **최초 1회**만 기록한다 (FR39.4).

    ⓐ 기존이 비었고(키 부재·`[]`) 서술자가 있으면 원소 1개 ⓑ 기존에 원소가 있으면
    **그대로**(추가·수정·`at` 갱신 없음) ⓒ 서술자가 None이면 보존만 ⓓ 상한 10개.

    "다른 출처면 추가"를 기각한 이유(DQ-60): `StateManager.decide()`가 수정 감지(FR2.2)와
    **멤버십 영상 매 run 재시도**(FR19.1)로 `updated`를 상시 내므로, 그 규칙이면 검색으로
    들어온 영상이 채널 run에 재취득될 때마다 `channel` 원소가 붙어 출처가 희석된다.
    손상 값(리스트 아님·dict 아닌 원소)은 예외 없이 보수적으로 버린다.
    """
    prev = [e for e in prev_origin if isinstance(e, dict)] \
        if isinstance(prev_origin, list) else []
    if prev:
        return prev[:ORIGIN_MAX]                  # ⓑ 이미 있으면 손대지 않는다
    if not entry or not isinstance(entry, dict):
        return prev                               # ⓒ 보존만 (= [])
    at = at or datetime.datetime.now().isoformat(timespec="seconds")
    return [_origin_entry(entry, at)]             # ⓐ 최초 1회


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

    @staticmethod
    def _read_prev(meta_path) -> dict:
        """기존 meta 읽기 (FR39.5). 부재·손상은 **빈 dict** — 추출을 실패로 뒤집지 않는다."""
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning(f"  ⚠️ 기존 meta 읽기 실패 — 보존 없이 진행: "
                        f"{meta_path.name} ({exc})")
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, info: dict, basename: str, sub_type: str,
             playlists: list = None, content_type: str = "video",
             origin_entry: dict = None) -> dict:
        """
        meta/*.json + desc/*.txt 저장.
        반환: 저장된 meta dict.

        `origin_entry`: 이 run의 출처 서술자 dict 또는 None (FR39.5). 병합·판정은
        **이 함수 한 곳**에서만 일어난다 — 진입점은 "내가 누구인지"만 말한다.
        """
        self.dirs["meta"].mkdir(parents=True, exist_ok=True)
        meta_path = self.dirs["meta"] / f"{basename}.json"
        # ① 기존 파일 읽기 → ② PRESERVED 이어받기 → ③ DERIVED 새로 계산 → ④ origin 병합
        # ②가 없으면 ③이 파일을 통째로 대체해 `origin`이 첫 재추출에 사라진다(FR39.5).
        prev = self._read_prev(meta_path)

        meta = {k: info.get(k) for k in META_FIELDS}
        meta["sub_type"] = sub_type
        meta["playlists"] = playlists or []      # 재생목록 카테고리 (FR15.2)
        meta["content_type"] = content_type      # "video" | "live" (FR16.4)
        meta["chapters"] = self._normalize_chapters(info.get("chapters"))  # FR27.1
        meta["extracted_at"] = datetime.datetime.now().isoformat(timespec="seconds")

        # 종목/티커 추출 (제목 + 설명 + 태그). 백필과 같은 함수를 쓴다 (FR12.2)
        meta["tickers"] = tickers_from_meta(meta, info.get("description") or "")

        # ② 보존 필드 — 기존 파일 값을 그대로 이어받는다 (FR39.6 PRESERVED_FIELDS).
        # **보존은 이 루프가 유일한 경로다** — `origin`을 PRESERVED_FIELDS에서 빼면
        # (임포트 assert를 뚫더라도) 아래 병합이 이어받을 값을 못 보고 덮어쓴다.
        for key in PRESERVED_FIELDS:
            if key in prev:
                meta[key] = prev[key]
        # ④ 출처 병합 — `at`은 `extracted_at`과 같은 시계·같은 값 (FR39.4)
        meta["origin"] = _merge_origin(meta.get("origin"), origin_entry,
                                       at=meta["extracted_at"])

        # meta json 저장
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

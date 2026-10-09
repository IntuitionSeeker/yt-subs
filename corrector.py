"""자막 용어 교정 — A 정규화 + C 결정적 치환. FR40.1·40.7~40.15·40.21.

**원본은 절대 바뀌지 않는다**(FR40.3): 이 모듈은 `srt/`·`txt/`에 쓰기 호출을 갖지 않고
산출물은 채널 디렉터리 안 `fix/` 한 곳에만 만든다. 되돌리기는 `fix/` 삭제로 완결된다
(다음 인덱싱이 `pick_source` 폴백으로 원본 본문에 복귀한다 — DQ-67).

설계의 무게중심은 기능이 아니라 **데이터 손상 예방**이다. `tickers`는 빈 필드라 무시하면
그만이었지만 교정은 **본문을 바꾸고 그 위에서 검색·분석이 돈다**. 그래서 네 겹으로 막는다:
ⓐ 원본 불변 ⓑ 사전 스키마가 무방비 치환을 금지(`glossary._validate`) ⓒ 판정은 문장 창·
쓰기는 큐(DQ-66) ⓓ 검증·큐 원복·**파일 단위 회로차단**(FR40.10).

D(LLM 문맥 교정)는 이번 범위 밖이다(FR40.1·40.24ⓐ) — `changes.jsonl`의 `stage` 자리와
"후보 탐색 → 문맥 판정 → 적용/배제" 분리만 남긴다. 프롬프트·호출 경로는 없다.
"""
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import config
import glossary
import subtitle_utils as su

log = logging.getLogger("corrector")

# ─── 엔진 상수 (C3 — 값은 이 한 곳에만 둔다) ─────────────────────────────────
ENGINE_VERSION = 1

WINDOW_SPAN = 2                 # 문장 창 = 앞뒤 N큐 (FR40.8 — 실측 큐 808/809 분리)
WINDOW_MAX_CHARS = 600          # 창 길이 상한 (긴 큐에서 판정 문맥이 번지는 것 방지)

MAX_LEN_DELTA = 0.30            # 큐별 글자 수 변화율 상한 (FR40.10ⓑ)
LEN_DELTA_FLOOR = 12            # 짧은 큐 허용 절대치 — "레그를"(3자) → "RAG를"(5자)은
                                # 비율로는 +66%다. 이 바닥이 없으면 정상 교정이 원복된다.

FILE_CHANGE_RATIO_MAX = 0.05    # 파일 회로차단 — 변경 큐 비율 상한 (FR40.10)
FILE_CHANGE_ABS_MAX = 200       # 절대 상한
FILE_CHANGE_FLOOR = 10          # 짧은 파일에서 비율만으로 걸리지 않게 하는 바닥

EXCLUDED_LOG_CAP = 200          # 배제 기록 상한(파일당) — 통계는 전량 센다

# A 정규화 화이트리스트 (FR40.7) — **열거된 것만** 손댄다.
# 임의 `[...]` 제거는 금지다: 대괄호 포함 파일이 실측 171편이고 그중에 발화 내용·
# 자막 제작자 주석이 섞여 있다.
HTML_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&apos;", "'"), ("&nbsp;", " "), ("&amp;", "&"))
SOUND_TAGS = ("[음악]", "[박수]", "[웃음]", "[Music]", "[Applause]", "[Laughter]")

_SPEAKER_RE = re.compile(r"\s*>>+\s*")
_WS_RE = re.compile(r"[ \t]{2,}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_NUM_RE = re.compile(r"\d+")
_TIMECODE_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_HANGUL_SYL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_WORDCHAR_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9]")
_TIME_LINE_RE = re.compile(r"-->")


class CorrectionError(Exception):
    """파일 1개의 교정을 포기해야 하는 상태(구조 불변식 위반). 원본은 그대로 쓰인다."""


# ─── 큐 파싱·직렬화 (타임스탬프 문자열을 그대로 보존한다) ────────────────────
@dataclass
class Cue:
    number: int          # 판정·로그용 큐 번호 (SRT 번호가 없으면 순번)
    num_raw: str         # 원본 번호 줄 문자열
    time: str            # 원본 타임스탬프 줄 문자열 (**변경 금지**)
    text: str            # 본문 (여러 줄이면 "\n" 포함)

    def clone(self) -> "Cue":
        return Cue(self.number, self.num_raw, self.time, self.text)


def parse_cues(srt_text: str) -> list:
    """SRT → `Cue` 목록. `subtitle_utils.parse_srt`와 달리 **시각 문자열을 보존**한다.

    타임스탬프를 초로 바꾸면 되돌려 쓸 때 반드시 흔들린다(FR40.10ⓐ는 문자열 동일을
    요구한다) — 그래서 파서를 따로 둔다.
    """
    cues = []
    for block in re.split(r"\n\s*\n", srt_text.strip("\ufeff").strip()):
        lines = block.splitlines()
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if _TIME_LINE_RE.search(l)), None)
        if ti is None:
            continue
        num_raw = lines[ti - 1].strip() if ti > 0 else ""
        try:
            number = int(num_raw)
        except ValueError:
            number = len(cues) + 1
        text = "\n".join(l.rstrip("\r") for l in lines[ti + 1:]).strip("\n")
        cues.append(Cue(number=number, num_raw=num_raw or str(len(cues) + 1),
                        time=lines[ti].strip(), text=text))
    return cues


def render_cues(cues) -> str:
    """`Cue` 목록 → SRT 문자열. `subtitle_utils.vtt_to_srt`와 **같은 형식**이다."""
    out = []
    for cue in cues:
        out += [cue.num_raw, cue.time] + cue.text.split("\n") + [""]
    return "\n".join(out)


# ─── 경로 (FR40.4·DQ-67) ─────────────────────────────────────────────────────
def fix_dirs(channel: str) -> dict:
    """`fix/` 하위 경로 조립. **`config.channel_subdirs()`를 확장하지 않는다** —
    `Extractor.__init__`이 그 딕셔너리를 통째로 `mkdir`하므로(`extractor.py:118`)
    확장하면 교정과 무관한 116채널 전부에 빈 `fix/`가 생긴다. 생성은 **쓸 때만**."""
    base = config.channel_dir(channel) / "fix"
    return {"base": base, "srt": base / "srt", "txt": base / "txt",
            "changes": base / "changes", "state": base / "state.json"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─── A 정규화 (FR40.7) ───────────────────────────────────────────────────────
def _record(cue, stage, before, after, rule, status="applied", **extra) -> dict:
    rec = {"cue": cue.number, "stage": stage, "from": before, "to": after,
           "rule": rule, "status": status}
    rec.update(extra)
    return rec


def normalize(cues) -> tuple:
    """A 단계 — HTML 엔티티 복원 · `>>` 화자 마커 정리 · **열거된 사운드 태그만** 제거.

    큐 삭제·병합·빈 큐 제거는 하지 않는다(타임스탬프 무손실). 정규화 결과가 빈 문자열이
    되는 큐는 **원본을 유지한다** — 빈 본문 큐를 만들면 큐 구조가 깨진다.
    """
    out, changes = [], []
    for cue in cues:
        new = cue.clone()
        for rule, fn in (("normalize/entity", _fix_entities),
                         ("normalize/speaker", _fix_speaker),
                         ("normalize/soundtag", _fix_soundtags)):
            before = new.text
            after = fn(before)
            if after == before:
                continue
            if not after.strip():                 # 빈 큐를 만들지 않는다
                continue
            new.text = after
            changes.append(_record(cue, "A", before, after, rule))
        out.append(new)
    return out, changes


def _fix_entities(text: str) -> str:
    for src, dst in HTML_ENTITIES:
        text = text.replace(src, dst)
    return text


def _fix_speaker(text: str) -> str:
    if ">>" not in text:
        return text
    return _WS_RE.sub(" ", _SPEAKER_RE.sub(" ", text)).strip()


def _fix_soundtags(text: str) -> str:
    low = text
    for tag in SOUND_TAGS:
        if tag in low:
            low = low.replace(tag, " ")
        elif tag.lower() in low.lower():           # 영문 태그는 대소문자 무시
            low = re.sub(re.escape(tag), " ", low, flags=re.IGNORECASE)
    if low == text:
        return text
    return _WS_RE.sub(" ", low).strip()


# ─── 문장 창 (FR40.8·DQ-66) ──────────────────────────────────────────────────
def windows(cues, span: int = WINDOW_SPAN, max_chars: int = WINDOW_MAX_CHARS) -> list:
    """큐별 판정용 창(앞뒤 `span`큐를 이어 붙인 문자열). 창은 **산출물이 아니다**.

    실측 근거: "하네스는"(큐 808) / "강아지 용품에서 온 말 아니었나요?"(큐 809)가
    **분리**돼 있어 큐 단위로는 배제 문맥이 보이지 않는다.
    """
    texts = [c.text.replace("\n", " ") for c in cues]
    out = []
    for i in range(len(texts)):
        lo, hi = max(0, i - span), min(len(texts), i + span + 1)
        parts = texts[lo:hi]
        win = " ".join(parts)
        while len(win) > max_chars and hi - lo > 1:   # 바깥쪽부터 잘라 상한을 지킨다
            if hi - 1 > i:
                hi -= 1
            elif lo < i:
                lo += 1
            else:
                break
            win = " ".join(texts[lo:hi])
        out.append(win)
    return out


# ─── C 결정적 치환 (FR40.9) ──────────────────────────────────────────────────
def _hangul_run(text: str) -> str:
    run = ""
    for ch in text:
        if _HANGUL_SYL_RE.match(ch):
            run += ch
        else:
            break
    return run


def judge(text: str, window: str, variant, start: int, end: int) -> tuple:
    """후보 1건의 적용 여부 — `(ok, reason)`. **순수 함수**(난수·시각·LLM 없음).

    순서: 큐 지역 경계(좌우 배제·`word_exact`·`josa`) → 창 문맥(`window_exclude`·
    `require`). 배제 이유 문자열은 `changes.jsonl`의 `reason`이 되므로 계약이다.
    """
    left, right = text[:start], text[end:]
    for s in variant.left_exclude:
        if left.endswith(s):
            return False, f"left_exclude:{s}"
    for s in variant.right_exclude:
        if right.startswith(s):
            return False, f"right_exclude:{s}"
    if variant.word_exact:
        if left and _WORDCHAR_RE.match(left[-1]):
            return False, "word_exact:left"
        tail = _hangul_run(right)
        if tail and tail not in glossary.JOSA:
            return False, f"word_exact:right:{tail}"
    if not variant.josa and _hangul_run(right):
        return False, "josa:false"
    for s in variant.window_exclude:
        if s in window:
            return False, f"window_exclude:{s}"
    if variant.require and not any(s in window for s in variant.require):
        return False, "require"
    return True, ""


def _snippet(text: str, start: int, end: int) -> tuple:
    """로그용 발췌 — 매치가 든 어절 + 앞 어절 1개(DESIGN §5.12 예시 형태)."""
    lo = text.rfind(" ", 0, start) + 1
    lo2 = text.rfind(" ", 0, max(lo - 1, 0)) + 1 if lo > 0 else 0
    hi = text.find(" ", end)
    hi = len(text) if hi < 0 else hi
    return lo2, hi


def apply_rules(cues, rules, wins=None, span: int = WINDOW_SPAN) -> tuple:
    """C 단계 — 후보 탐색 → 창 기준 문맥 판정 → 통과 시 치환(**조사는 그대로 남는다**).

    같은 입력·같은 사전이면 항상 같은 출력이다(결정성 = 골든 샘플 회귀의 전제).
    `confidence: high`만 적용하고 `medium`/`low`는 후보 리포트 소관이다(FR40.9).
    """
    if wins is None:
        wins = windows(cues, span)
    out, changes = [], []
    stats = {r.rule_id: {"applied": 0, "excluded": 0} for r in rules}
    excluded_logged = 0
    for i, cue in enumerate(cues):
        new = cue.clone()
        win = wins[i]
        for rule in rules:
            if rule.confidence != "high":
                continue
            for variant in rule.variants:
                pos = 0
                while True:
                    idx = new.text.find(variant.text, pos)
                    if idx < 0:
                        break
                    end = idx + len(variant.text)
                    ok, reason = judge(new.text, win, variant, idx, end)
                    if ok:
                        lo, hi = _snippet(new.text, idx, end)
                        before = new.text[lo:hi]
                        new.text = new.text[:idx] + rule.canonical + new.text[end:]
                        after = new.text[lo:hi + len(rule.canonical) - len(variant.text)]
                        stats[rule.rule_id]["applied"] += 1
                        changes.append(_record(cue, "C", before, after, rule.rule_id,
                                               confidence=rule.confidence))
                        pos = idx + len(rule.canonical)
                    else:
                        stats[rule.rule_id]["excluded"] += 1
                        if excluded_logged < EXCLUDED_LOG_CAP:
                            excluded_logged += 1
                            lo, hi = _snippet(new.text, idx, end)
                            frag = new.text[lo:hi]
                            changes.append(_record(cue, "C", frag, frag, rule.rule_id,
                                                   status="excluded", reason=reason,
                                                   confidence=rule.confidence))
                        pos = end
        out.append(new)
    return out, changes, stats


# ─── E 검증 + 큐 원복 (FR40.10) ──────────────────────────────────────────────
def _violation(old: str, new: str, fixed) -> str:
    """금지 변경·길이 변화율 판정. 위반 사유 문자열(없으면 "")."""
    allowed = max(LEN_DELTA_FLOOR, MAX_LEN_DELTA * len(old))
    if abs(len(new) - len(old)) > allowed:
        return f"length_delta>{MAX_LEN_DELTA}"
    if _NUM_RE.findall(old) != _NUM_RE.findall(new):
        return "digit_changed"
    if _URL_RE.findall(old) != _URL_RE.findall(new):
        return "url_changed"
    if _TIMECODE_RE.findall(old) != _TIMECODE_RE.findall(new):
        return "timecode_changed"
    for term in fixed:
        if old.count(term) > new.count(term):
            return f"fixed_changed:{term}"
    return ""


def verify(orig_cues, new_cues, changes, fixed=()) -> int:
    """⚠ 산출 직전 검증 — 위반한 **큐를 원복**하고 로그를 `reverted`로 바꾼다.

    ⓐ 큐 수·타임스탬프 문자열 동일(위반은 구조 파괴이므로 `CorrectionError`)
    ⓑ 큐별 글자 수 변화율 상한 ⓒ 숫자·URL·타임코드 불변 ⓓ `fixed`(확정 고유명사) 미변경.
    """
    if len(orig_cues) != len(new_cues):
        raise CorrectionError(f"큐 수가 달라졌습니다: {len(orig_cues)} → {len(new_cues)}")
    reverted = 0
    for i, (old, new) in enumerate(zip(orig_cues, new_cues)):
        if old.time != new.time or old.num_raw != new.num_raw:
            raise CorrectionError(f"큐 {old.number}의 타임스탬프가 변했습니다.")
        if old.text == new.text:
            continue
        reason = _violation(old.text, new.text, fixed)
        if not reason:
            continue
        new.text = old.text                       # 원복
        reverted += 1
        for rec in changes:
            if rec["cue"] == old.number and rec["status"] == "applied":
                rec["status"] = "reverted"
                rec["reason"] = reason
    return reverted


# ─── 채널 1개 교정 (FR40.10·40.14) ───────────────────────────────────────────
def _change_limit(n_cues: int) -> int:
    """파일 회로차단 임계 — 잘못된 광역 규칙 하나가 코퍼스를 한 번에 망치는 것을 막는다."""
    return min(FILE_CHANGE_ABS_MAX,
               max(FILE_CHANGE_FLOOR, math.ceil(FILE_CHANGE_RATIO_MAX * n_cues)))


def correct_text(srt_text: str, rules, fixed=()) -> dict:
    """SRT 문자열 1개 → 교정 결과(쓰기 없음). A → 창 → C → E 순서.

    반환: `{srt, cues, changed, reverted, applied, excluded, changes, stats, held, reason}`
    """
    orig = parse_cues(srt_text)
    if not orig:
        return {"srt": srt_text, "cues": 0, "changed": 0, "reverted": 0,
                "applied": 0, "excluded": 0, "changes": [], "stats": {},
                "held": False, "reason": ""}
    stage_a, a_changes = normalize(orig)
    wins = windows(stage_a)
    stage_c, c_changes, stats = apply_rules(stage_a, rules, wins=wins)
    changes = a_changes + c_changes
    reverted = verify(orig, stage_c, changes, fixed=fixed)
    # 원복된 큐의 적용 건수는 규칙 통계에서도 빼야 한다 — `applied == 0`이 죽은 규칙
    # 판정의 유일한 근거이므로(FR40.21ⓑ) 원복분을 남기면 규칙이 살아 있는 것처럼 보인다.
    for rec in changes:
        if rec["status"] == "reverted" and rec["rule"] in stats:
            stats[rec["rule"]]["applied"] -= 1
    changed = sum(1 for o, n in zip(orig, stage_c) if o.text != n.text)
    limit = _change_limit(len(orig))
    held, reason = False, ""
    if changed > limit:
        held = True
        reason = f"회로차단: 변경 큐 {changed}개 > 상한 {limit}개 (큐 {len(orig)})"
    applied = sum(1 for c in changes if c["status"] == "applied")
    excluded = sum(1 for c in changes if c["status"] == "excluded")
    return {"srt": render_cues(stage_c), "cues": len(orig), "changed": changed,
            "reverted": reverted, "applied": applied, "excluded": excluded,
            "changes": changes, "stats": stats, "held": held, "reason": reason}


def fixed_terms(rules) -> tuple:
    """`fixed`(확정 고유명사) 목록 — 사전의 `canonical`이 정본이다(FR40.10ⓓ).
    이미 올바르게 적힌 `RAG`를 다른 규칙이 먹어 치우는 것을 막는다."""
    return tuple(sorted({r.canonical for r in rules}))


def correct_channel(channel: str, apply: bool = False, rules=None, domains=None,
                    on_progress=None) -> dict:
    """채널 1개 처리 — 원본 `srt/` 순회(**읽기만**) → A → C → E → 회로차단 → (선택) 산출.

    `apply=False`가 **기본**이고 그때는 **한 바이트도 쓰지 않는다**(FR40.14 —
    `migrate-groups`·`backfill-tickers` 선례).
    """
    if domains is None:
        domains = glossary.resolve(channel)
    if rules is None:
        rules = glossary.load(domains)
    rsha = glossary.rules_hash(rules)
    fixed = fixed_terms(rules)

    dirs = config.channel_subdirs(channel)
    fx = fix_dirs(channel)
    plan = {"channel": channel, "domains": list(domains), "rules_sha256": rsha,
            "rules": len(rules), "files": [], "holds": [], "cleaned": [],
            "rule_stats": {r.rule_id: {"applied": 0, "excluded": 0} for r in rules},
            "totals": {"videos": 0, "corrected": 0, "changes": 0, "reverted": 0,
                       "excluded": 0, "chunks": 0, "held": 0},
            "applied": bool(apply)}
    srt_dir = dirs["srt"]
    if not srt_dir.exists():
        return plan

    state = _read_state(fx["state"])
    files = sorted(srt_dir.glob("*.srt"))
    plan["totals"]["videos"] = len(files)
    for fi, srt_file in enumerate(files, 1):
        basename = srt_file.stem
        if on_progress:
            on_progress(fi, len(files), basename)
        src = srt_file.read_text(encoding="utf-8")        # ← 읽기만 한다 (FR40.3)
        try:
            res = correct_text(src, rules, fixed=fixed)
        except CorrectionError as exc:                     # 구조 불변식 위반 → 원본 유지
            plan["holds"].append({"basename": basename, "reason": str(exc)})
            plan["totals"]["held"] += 1
            continue
        for rid, st in res["stats"].items():           # 규칙별 적용·배제 누계
            if rid in plan["rule_stats"]:
                plan["rule_stats"][rid]["applied"] += st["applied"]
                plan["rule_stats"][rid]["excluded"] += st["excluded"]
        if res["held"]:
            plan["holds"].append({"basename": basename, "reason": res["reason"],
                                  "changed": res["changed"], "cues": res["cues"]})
            plan["totals"]["held"] += 1
            continue
        if res["changed"] == 0:
            if basename in state:                          # 이전 교정본이 남아 있다
                plan["cleaned"].append(basename)
            continue
        chunks = len(su.chunk_by_srt(res["srt"]))
        plan["files"].append({"basename": basename, "cues": res["cues"],
                              "changed": res["changed"], "applied": res["applied"],
                              "reverted": res["reverted"], "excluded": res["excluded"],
                              "chunks": chunks})
        plan["totals"]["corrected"] += 1
        plan["totals"]["changes"] += res["applied"]
        plan["totals"]["reverted"] += res["reverted"]
        plan["totals"]["excluded"] += res["excluded"]
        plan["totals"]["chunks"] += chunks
        if not apply:
            continue
        # ── 여기서부터만 쓴다. 대상은 `fix/` 하위뿐이다 ──
        for key in ("srt", "txt", "changes"):
            fx[key].mkdir(parents=True, exist_ok=True)
        (fx["srt"] / f"{basename}.srt").write_text(res["srt"], encoding="utf-8")
        (fx["txt"] / f"{basename}.txt").write_text(su.srt_to_txt(res["srt"]),
                                                   encoding="utf-8")
        (fx["changes"] / f"{basename}.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in res["changes"]),
            encoding="utf-8")
        state[basename] = {
            "src_sha256": sha256_text(src), "rules_sha256": rsha,
            "engine_version": ENGINE_VERSION, "applied_at": _now(),
            "changes": res["applied"], "reverted": res["reverted"],
            "excluded": res["excluded"],
        }
    if apply:
        for basename in plan["cleaned"]:                   # 재실행으로 변경이 0이 된 편
            _remove_fix_files(channel, basename)
            state.pop(basename, None)
        if state or fx["state"].exists():
            _write_state(fx["state"], state)
        _invalidate_state_cache()
    plan["dead_rules"] = sorted(rid for rid, st in plan["rule_stats"].items()
                                if st["applied"] == 0)
    return plan


def reembed_estimate(plan: dict) -> int:
    """재임베딩 예고(FR40.14) — 본문이 바뀐 영상의 자막 청크 수 합.

    **전량 재임베딩은 발생하지 않는다**: FR33 `_unchanged`가 본문을 대조하므로 본문이
    바뀐 영상만 다시 임베딩된다(실측 기준선 57편 / 1,151청크 = 자막 청크 6,546의 17.6%).
    청크 메타에 교정 표식을 넣으면 **그 순간 전량**이 된다(DQ-68).
    """
    return int(plan.get("totals", {}).get("chunks", 0))


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _remove_fix_files(channel: str, basename: str) -> int:
    """`fix/` 산출물 3종 삭제 — 영상 삭제(FR40.17ⓐ)·재실행 정리 공용."""
    fx = fix_dirs(channel)
    n = 0
    for key, ext in (("srt", "srt"), ("txt", "txt"), ("changes", "jsonl")):
        path = fx[key] / f"{basename}.{ext}"
        if path.exists():
            path.unlink()
            n += 1
    return n


def forget_video(channel: str, basename: str) -> int:
    """영상 삭제 경로용 — `fix/` 파일 3종 + `state.json` 항목을 함께 지운다.

    상태 항목까지 지우는 이유: 남겨 두면 `status()`가 **원본 없는 고아**로 보고한다
    (그 보고는 FR21.1 누락의 증거여야 하는데, 정상 삭제가 섞이면 신호가 죽는다).
    """
    n = _remove_fix_files(channel, basename)
    fx = fix_dirs(channel)
    state = _read_state(fx["state"])
    if state.pop(basename, None) is not None:
        _write_state(fx["state"], state)
        _invalidate_state_cache()
    return n


# ─── 상태 파일 · stale 판정 (FR40.15·DQ-69) ──────────────────────────────────
_state_cache: dict = {}
_rules_cache: dict = {}


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                               # pragma: no cover - 손상 방어
        log.warning(f"⚠️ 교정 상태 파일을 읽지 못했습니다: {path}")
        return {}


def _write_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _invalidate_state_cache():
    _state_cache.clear()


def load_state(channel: str) -> dict:
    """`fix/state.json`(교정 상태 정본). mtime 기반 캐시 — 인덱싱이 파일마다 부른다."""
    path = fix_dirs(channel)["state"]
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _state_cache.pop(str(path), None)
        return {}
    hit = _state_cache.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    data = _read_state(path)
    _state_cache[str(path)] = (key, data)
    return data


def _glossary_fingerprint() -> tuple:
    """`glossary/`의 (파일명, mtime, 크기) — 사전을 고치면 즉시 stale이 된다."""
    out = []
    for path in sorted(glossary.GLOSSARY_DIR.glob("*.yaml")):
        try:
            st = path.stat()
            out.append((path.name, st.st_mtime_ns, st.st_size))
        except OSError:                             # pragma: no cover
            continue
    return tuple(out)


def rules_sha_for(channel: str) -> str:
    """채널의 현행 사전 해시 — 소비 경로가 채널마다 1회만 계산하도록 캐시한다."""
    fp = _glossary_fingerprint()
    hit = _rules_cache.get(channel)
    if hit and hit[0] == fp:
        return hit[1]
    sha = glossary.rules_hash(glossary.load(glossary.resolve(channel)))
    _rules_cache[channel] = (fp, sha)
    return sha


def is_fresh(channel: str, basename: str, state=None, rules_sha=None) -> bool:
    """교정본이 **신선한가** — 원본 sha256 + 사전 해시 + 엔진 버전이 전부 일치할 때만 True.

    재추출(FR2.2 수정 감지 · **FR19.1 멤버십 매 run 재시도** · `reextract`)은 정상
    운영이고 FR37 스케줄러를 켜면 **무인으로** 일어난다. 이 함수가 False를 내면
    소비 측은 조용히 원본으로 돌아간다 — "조용히 낡은 본문"이 최악이므로 폴백이 기본값이다.
    """
    st = (state if state is not None else load_state(channel)).get(basename)
    if not st:
        return False
    if st.get("engine_version") != ENGINE_VERSION:
        return False
    src = config.channel_subdirs(channel)["srt"] / f"{basename}.srt"
    if not src.exists():
        return False
    if st.get("src_sha256") != sha256_text(src.read_text(encoding="utf-8")):
        return False
    want = rules_sha if rules_sha is not None else rules_sha_for(channel)
    return st.get("rules_sha256") == want


def pick_source(channel: str, basename: str, kind: str = "srt",
                state=None, rules_sha=None) -> Path:
    """**소비 측 단일 통로**(FR40.13·40.15) — 신선한 교정본이 있으면 `fix/`, 없거나
    stale이면 원본.

    `KLIndexer.index_subtitles`·`GET /subtitle`·`/export/markdown`·`kl_query.get_full`이
    **같은 함수**를 쓴다 — 두 곳에 규칙이 있으면 한쪽이 반드시 stale을 쓴다(DQ-68).
    **글롭 대상은 바꾸지 않는다**: 교정본 디렉터리를 순회하면 미교정 504편이 색인에서
    사라지고 `doctor.index-coverage`가 504건 경고로 터진다.
    """
    if kind not in ("srt", "txt"):
        raise ValueError(f"kind는 'srt'|'txt'여야 합니다: {kind!r}")
    orig = config.channel_subdirs(channel)[kind] / f"{basename}.{kind}"
    fx = fix_dirs(channel)
    st = state if state is not None else load_state(channel)
    if not st or basename not in st:               # 교정본 없음 = 흔한 경로(비용 0)
        return orig
    fixed = fx[kind] / f"{basename}.{kind}"
    if not fixed.exists():
        return orig
    if not is_fresh(channel, basename, state=st, rules_sha=rules_sha):
        return orig
    return fixed


def read_source(channel: str, basename: str, kind: str = "srt", **kw) -> str:
    """`pick_source`가 고른 본문 문자열. 파일이 없으면 빈 문자열."""
    path = pick_source(channel, basename, kind=kind, **kw)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ─── 소비 UI용 요약 (FR40.16) ────────────────────────────────────────────────
def video_status(channel: str) -> dict:
    """`{basename: {corrected, corrections, stale}}` — `GET /videos`가 쓴다.

    `meta/*.json` 스키마는 건드리지 않는다(FR40.24ⓔ) — 교정 사실의 정본은
    `fix/state.json`뿐이다. 교정본이 없는 채널에서는 **즉시 빈 맵**(비용 0).
    """
    state = load_state(channel)
    if not state:
        return {}
    rsha = rules_sha_for(channel)
    fx = fix_dirs(channel)
    out = {}
    for basename, st in state.items():
        if not (fx["srt"] / f"{basename}.srt").exists():
            continue
        fresh = is_fresh(channel, basename, state=state, rules_sha=rsha)
        out[basename] = {"corrected": True,
                         "corrections": int(st.get("changes") or 0),
                         "stale": not fresh}
    return out


# ─── 상태 점검 (FR40.22 — `doctor` 대신 여기 둔다, DQ-72) ────────────────────
def status(channels) -> dict:
    """`correct --status` — stale 교정본 · **고아 교정본** · 죽은 규칙 보고.

    `doctor` 검사를 지금 만들지 않는 이유는 FR38.19ⓓ(현행 발견 건수 실측)를 충족할 수
    없기 때문이다(교정본 0편 → 전부 0건 = 죽은 검사). 판정 자체는 여기 살아 있다.
    """
    out = {"channels": [], "stale": [], "orphans": [], "dead_rules": [],
           "corrected": 0, "scanned": 0}
    dead_candidates, applied_seen = {}, set()
    for channel in channels:
        fx = fix_dirs(channel)
        state = load_state(channel)
        if not fx["base"].exists() and not state:
            continue
        rsha = rules_sha_for(channel)
        srt_dir = config.channel_subdirs(channel)["srt"]
        n_stale = 0
        for basename, st in sorted(state.items()):
            out["scanned"] += 1
            if not (srt_dir / f"{basename}.srt").exists():
                out["orphans"].append({"channel": channel, "basename": basename,
                                       "reason": "원본 srt가 없다(FR21.1 삭제 누락)"})
                continue
            out["corrected"] += 1
            if not is_fresh(channel, basename, state=state, rules_sha=rsha):
                n_stale += 1
                out["stale"].append({
                    "channel": channel, "basename": basename,
                    "reason": ("사전 변경" if st.get("rules_sha256") != rsha
                               else "원본 재추출")})
        for key, ext in (("srt", "srt"), ("txt", "txt"), ("changes", "jsonl")):
            if not fx[key].exists():
                continue
            for path in sorted(fx[key].glob(f"*.{ext}")):
                if path.stem not in state:
                    out["orphans"].append({"channel": channel, "basename": path.stem,
                                           "reason": f"state.json에 없는 {key} 산출물"})
        # 죽은 규칙 판정은 **교정 이력이 있는 채널에서만** 한다(근거 없는 0건 보고 금지)
        if state:
            for rule in glossary.load(glossary.resolve(channel)):
                dead_candidates[rule.rule_id] = True
            if fx["changes"].exists():
                for path in fx["changes"].glob("*.jsonl"):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            rec = json.loads(line)
                        except Exception:           # pragma: no cover
                            continue
                        if rec.get("status") == "applied":
                            applied_seen.add(rec.get("rule"))
        out["channels"].append({"channel": channel, "corrected": len(state),
                                "stale": n_stale})
    out["dead_rules"] = sorted(rid for rid in dead_candidates
                               if rid not in applied_seen and "/" in rid
                               and not rid.startswith("normalize/"))
    return out

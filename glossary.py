"""용어사전 로드·검증·도메인 매핑·후보 계량. FR40.5~40.6·40.18~40.19.

이 모듈의 **핵심은 로드 실패**다(FR40.5·DQ-65). 한국어는 어절 내부에 경계가 없어
`\\b`(정규식 단어 경계)가 의미 경계와 무관하다 — `\\b디어\\b`는 `아이디어`를 막아 주지만
정상 대상인 조사 결합형 `레그를`도 함께 잃는다. 그래서 안전장치를 **개별 항목의 책임**으로
내리고, 조건이 하나도 없으면 **사전 로드가 `ValueError`로 실패**한다.

실측 근거(2026-09-27 · 561편): 가이드가 확실(○)로 분류한 `디어`→DEER가 **617건/192편 전량
오탐**(`아이디어` 377·`소셜 미디어`·`드디어`·`옵시디어`)이고 `레그`→RAG도 249건 중 **152건이
`텔레그램`**이었다. 즉 무방비 부분문자열 치환은 성립하지 않는다(DQ-64).
"""
import json
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import config

log = logging.getLogger("glossary")

# ─── 경로 ────────────────────────────────────────────────────────────────────
GLOSSARY_DIR = config.BASE_DIR / "glossary"
PROFILES_NAME = "profiles.yaml"
HOLD_DIRNAME = "hold"

# ─── 스키마 (FR40.5) ─────────────────────────────────────────────────────────
CONFIDENCES = ("high", "medium", "low")

# 항목·표기의 허용 키. **미열거 키는 `ValueError`** — `left_exclud` 같은 오타가
# 조용히 안전장치를 끄는 것이 이 기능의 최악 실패 모드이기 때문이다(FR39.6 선례).
ENTRY_KEYS = frozenset({"canonical", "variants", "domain", "confidence",
                        "note", "source", "measured"})
VARIANT_KEYS = frozenset({"text", "word_exact", "left_exclude", "right_exclude",
                          "require", "window_exclude", "josa", "note"})
# 셋 중 **하나는 반드시** 있어야 한다 (FR40.5ⓐⓑⓒ).
# `window_exclude`는 여기 없다 — 창 안 배제는 어절 경계를 제약하지 않으므로
# `아이디어` 오탐을 막지 못한다(그것만 적어 두면 무방비 치환과 같다).
GUARD_KEYS = ("word_exact", "left_exclude", "right_exclude", "require")


# ─── 조사 화이트리스트 (FR40.9ⓒ) ─────────────────────────────────────────────
# `word_exact`에서 "어절 전체 일치 — 조사만 허용"을 판정한다. 열거에 없는 어미는
# **적용하지 않는다**(미교정 = 안전 방향). 오적용 1건이 미교정 100건보다 나쁘다(FR40.20).
JOSA = frozenset("""
을 를 이 가 은 는 의 에 와 과 로 도 만 나 랑 야 라 고 며 서 들 께 밖
에서 에게 에도 에는 에선 에만 에게서 으로 으로는 으로서 으로써 로서 로써 로는 로도
이나 이란 이라 이랑 이고 이며 이든 이라는 이라고 이라도 이지만 이야 이었 였
라는 라고 라도 라서 이라서 와의 과의 와는 과는 처럼 보다 조차 마저 밖에 뿐
까지 까지는 부터 부터는 만을 만은 만이 하고 한테 한테서 대로 마다 마는 든지
""".split())


# ─── 자료구조 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Variant:
    """사전 항목의 STT 표기 1개 + **오적용 방지 조건**."""
    text: str
    word_exact: bool = False
    left_exclude: tuple = ()
    right_exclude: tuple = ()
    require: tuple = ()
    window_exclude: tuple = ()
    josa: bool = True          # False면 뒤에 한글이 붙은 형태를 아예 적용하지 않는다
    note: str = ""

    def as_dict(self) -> dict:
        return {"text": self.text, "word_exact": self.word_exact,
                "left_exclude": list(self.left_exclude),
                "right_exclude": list(self.right_exclude),
                "require": list(self.require),
                "window_exclude": list(self.window_exclude),
                "josa": self.josa}


@dataclass(frozen=True)
class Rule:
    canonical: str
    domain: str
    variants: tuple = ()
    confidence: str = "high"
    note: str = ""
    source: str = ""
    measured: dict = field(default_factory=dict)

    @property
    def rule_id(self) -> str:
        """`changes.jsonl`·통계의 키. 도메인/표기 정본 — 한 번 공개하면 바꾸지 않는다."""
        return f"{self.domain}/{self.canonical}"

    def as_dict(self) -> dict:
        """`rules_hash` 입력 — 교정 결과에 영향을 주는 필드만 싣는다."""
        return {"canonical": self.canonical, "domain": self.domain,
                "confidence": self.confidence,
                "variants": [v.as_dict() for v in self.variants]}


# ─── 검증 (FR40.5 — 이 함수가 무방비 치환을 금지한다) ────────────────────────
def _strlist(value, where: str, key: str) -> tuple:
    """문자열 1개 또는 문자열 목록 → 튜플. 빈 문자열은 조건으로 인정하지 않는다."""
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError(f"{where}: `{key}`는 문자열 또는 문자열 목록이어야 합니다.")
    out = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"{where}: `{key}` 원소가 문자열이 아닙니다: {item!r}")
        s = unicodedata.normalize("NFC", item).strip()
        if s:
            out.append(s)
    return tuple(out)


def _validate_variant(raw, where: str) -> Variant:
    if isinstance(raw, str):                       # 축약 표기 — 조건이 없으므로 곧 실패한다
        raw = {"text": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: `variants` 원소가 매핑이 아닙니다: {raw!r}")
    unknown = set(raw) - VARIANT_KEYS
    if unknown:
        raise ValueError(f"{where}: `variants`에 알 수 없는 키가 있습니다 "
                         f"{sorted(unknown)} — 오타는 안전장치를 조용히 끕니다. "
                         f"허용 키: {sorted(VARIANT_KEYS)}")
    text = unicodedata.normalize("NFC", str(raw.get("text") or "")).strip()
    if not text:
        raise ValueError(f"{where}: `variants[].text`가 비어 있습니다.")
    word_exact = raw.get("word_exact", False)
    if not isinstance(word_exact, bool):
        raise ValueError(f"{where}: `word_exact`는 true/false여야 합니다.")
    josa = raw.get("josa", True)
    if not isinstance(josa, bool):
        raise ValueError(f"{where}: `josa`는 true/false여야 합니다.")
    var = Variant(
        text=text, word_exact=word_exact,
        left_exclude=_strlist(raw.get("left_exclude"), where, "left_exclude"),
        right_exclude=_strlist(raw.get("right_exclude"), where, "right_exclude"),
        require=_strlist(raw.get("require"), where, "require"),
        window_exclude=_strlist(raw.get("window_exclude"), where, "window_exclude"),
        josa=josa, note=str(raw.get("note") or ""),
    )
    # ⚠ 이 검사가 이 모듈의 존재 이유다 (FR40.5·DQ-65)
    if not (var.word_exact or var.left_exclude or var.right_exclude or var.require):
        raise ValueError(
            f"{where}: 표기 {text!r}에 오적용 방지 조건이 하나도 없습니다 — "
            f"{list(GUARD_KEYS)} 중 하나는 반드시 적어야 합니다. "
            f"(무방비 부분문자열 치환은 실측에서 `디어` 617건 전량 오탐 · "
            f"`레그` 152건 오탐을 냈습니다 — DQ-64)")
    return var


def _validate(raw, domain: str, where: str) -> Rule:
    """항목 1개 검증 → `Rule`. 위반은 전부 `ValueError`(로드 자체를 실패시킨다)."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: 항목이 매핑이 아닙니다: {raw!r}")
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        raise ValueError(f"{where}: 알 수 없는 키 {sorted(unknown)} — "
                         f"허용 키: {sorted(ENTRY_KEYS)}")
    canonical = unicodedata.normalize("NFC", str(raw.get("canonical") or "")).strip()
    if not canonical:
        raise ValueError(f"{where}: `canonical`이 비어 있습니다.")
    confidence = str(raw.get("confidence") or "high").strip()
    if confidence not in CONFIDENCES:
        raise ValueError(f"{where}: `confidence`가 열거({list(CONFIDENCES)}) 밖입니다: "
                         f"{confidence!r}")
    entry_domain = unicodedata.normalize("NFC", str(raw.get("domain") or domain)).strip()
    if entry_domain != domain:
        raise ValueError(f"{where}: `domain`({entry_domain!r})이 파일 도메인"
                         f"({domain!r})과 다릅니다 — `rule_id`가 어긋납니다.")
    variants = raw.get("variants")
    if not variants:
        raise ValueError(f"{where}: `variants`가 비어 있습니다.")
    if isinstance(variants, (str, dict)):
        variants = [variants]
    if not isinstance(variants, (list, tuple)):
        raise ValueError(f"{where}: `variants`는 목록이어야 합니다.")
    seen = set()
    parsed = []
    for i, v in enumerate(variants):
        var = _validate_variant(v, f"{where}.variants[{i}]")
        if var.text in seen:
            raise ValueError(f"{where}: 같은 항목 안에 표기 {var.text!r}가 중복 등록됐습니다.")
        seen.add(var.text)
        parsed.append(var)
    measured = raw.get("measured") or {}
    if not isinstance(measured, dict):
        raise ValueError(f"{where}: `measured`는 매핑이어야 합니다.")
    return Rule(canonical=canonical, domain=domain, variants=tuple(parsed),
                confidence=confidence, note=str(raw.get("note") or ""),
                source=str(raw.get("source") or ""), measured=measured)


# ─── 로드 ────────────────────────────────────────────────────────────────────
def domain_path(domain: str) -> Path:
    return GLOSSARY_DIR / f"{domain}.yaml"


def load(domains) -> list:
    """`glossary/<도메인>.yaml` 병합 로드 → `Rule` 목록. **로드가 검증 지점이다**(FR40.5).

    없는 파일은 경고 후 건너뛴다 — 빈 사전이 정상 상태이고(FR40.6 `역배열1`),
    사전 부재로 파이프라인이 죽으면 안 된다. 반면 **파일이 있는데 조건이 없는 항목**은
    반드시 `ValueError`다(그쪽이 데이터 손상 경로다).
    """
    import yaml                                    # 지연 임포트 (audit 경로는 yaml 없이 돈다)
    rules, seen = [], {}
    for domain in list(domains or []):
        path = domain_path(domain)
        if not path.exists():
            log.warning(f"⚠️ 사전 파일이 없습니다: {path.name} (빈 사전으로 처리)")
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data is None:
            continue                               # 주석만 있는 파일 = 빈 사전
        if not isinstance(data, list):
            raise ValueError(f"{path.name}: 최상위는 항목 목록(YAML 배열)이어야 합니다.")
        for i, entry in enumerate(data):
            rule = _validate(entry, domain, f"{path.name}[{i}]")
            for var in rule.variants:
                if var.text in seen:
                    raise ValueError(f"{path.name}[{i}]: 표기 {var.text!r}가 이미 "
                                     f"{seen[var.text]}에 등록돼 있습니다 — "
                                     f"치환 순서가 사전 순서에 의존하게 됩니다.")
                seen[var.text] = rule.rule_id
            rules.append(rule)
    return rules


def rules_hash(rules) -> str:
    """적용 사전의 정규화 해시 — 규칙이 바뀐 교정본을 stale로 만든다(FR40.15)."""
    payload = sorted((r.as_dict() for r in rules),
                     key=lambda d: (d["domain"], d["canonical"]))
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ─── 도메인 매핑 3층 (FR40.6·DQ-70) ──────────────────────────────────────────
_profile_cache_key = None
_profile_cache: dict = {}


def _profiles_path() -> Path:
    return GLOSSARY_DIR / PROFILES_NAME


def load_profiles() -> dict:
    """`glossary/profiles.yaml` → `{default, groups, channels}`. 부재·손상은 기본값."""
    global _profile_cache_key, _profile_cache
    path = _profiles_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), None, None)
    if key == _profile_cache_key:
        return _profile_cache
    prof = {"default": ["common"], "groups": {}, "channels": {}}
    if key[1] is not None:
        import yaml
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:                    # pragma: no cover - 수동 편집 방어
            log.warning(f"⚠️ {PROFILES_NAME} 파싱 실패({exc}) — 기본값(common)만 씁니다.")
            data = {}
        if isinstance(data.get("default"), list):
            prof["default"] = [str(d) for d in data["default"]]
        for axis in ("groups", "channels"):
            got = data.get(axis) or {}
            if isinstance(got, dict):
                prof[axis] = {unicodedata.normalize("NFC", str(k)):
                              [str(d) for d in (v or [])] for k, v in got.items()}
    _profile_cache_key, _profile_cache = key, prof
    return prof


def _group_of(channel: str) -> str:
    """채널의 폴더(`group`) — `channels.yaml`은 **읽기만** 한다(DQ-70)."""
    try:
        from channel_registry import ChannelRegistry
        cfg = ChannelRegistry().get(channel) or {}
        return (cfg.get("group") or "").strip()
    except Exception:                               # yaml 부재·파싱 실패 → 폴더 없음
        return ""


def resolve(channel: str, group=None) -> list:
    """적용 사전 결정 — `channels[채널]` → `groups[폴더]` → `default` **3층**(FR40.6).

    실측 근거: 영향 영상 57편 중 **38편이 무그룹 채널 23개**에 있어 폴더만으로는
    2/3가 사전을 못 받는다. 어느 층에도 없으면 `default`(=`common`)이고
    **빈 사전이 정상 상태**다(`역배열1`).
    """
    prof = load_profiles()
    name = unicodedata.normalize("NFC", channel or "")
    if name in prof["channels"]:
        return list(prof["channels"][name])
    g = _group_of(name) if group is None else (group or "")
    g = unicodedata.normalize("NFC", g)
    if g and g in prof["groups"]:
        return list(prof["groups"][g])
    return list(prof["default"])


def load_for(channel: str, group=None):
    """`(도메인 목록, Rule 목록)` — 채널 1개의 적용 사전."""
    domains = resolve(channel, group=group)
    return domains, load(domains)


# ─── 후보 계량 (FR40.18 — 사전은 가이드를 베끼지 않고 실측으로 만든다) ───────
EXACT_RATIO_MIN = 0.5          # 어절 완전일치 비율이 이 미만이면 `reject` 제안
_WORD_SPLIT_RE = re.compile(r"\s+")


def _tokens(text: str):
    return [t for t in _WORD_SPLIT_RE.split(text) if t]


# 어절 꼬리에서 떼어내는 문장부호 — `레그,` `레그"`도 어절 완전일치로 센다
_TAIL_PUNCT = "\"'“”‘’()[]{}.,!?…·:;~-–—/"


def is_exact_token(token: str, cand: str) -> bool:
    """어절이 `후보` 또는 `후보+조사` 형태인가 — `terms` verdict의 판정 기준.

    엔진의 `word_exact`(corrector.judge)와 **같은 원칙**이다: 뒤에 붙을 수 있는 것은
    열거된 조사뿐이고, 그 밖의 한글이 붙으면 다른 단어다(`텔레그램`·`인체스트`).
    """
    if not token.startswith(cand):
        return False
    tail = token[len(cand):].strip(_TAIL_PUNCT)
    if not tail:
        return True
    return tail in JOSA


def parse_candidates(text: str) -> list:
    """후보 파일 파싱 — 한 줄에 후보 1개. `#` 주석·빈 줄 무시.

    `후보` 또는 `후보 -> 제안표기`(`=`·탭도 허용). 가이드 §7 시드도 **이 경로로
    가설로서만** 들어온다(FR40.18) — 파일이 아니라 코드에 시드를 박지 않는다.
    """
    out, seen = [], set()
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        for sep in ("->", "=>", "=", "\t"):
            if sep in line:
                cand, _, sug = line.partition(sep)
                cand, sug = cand.strip(), sug.strip()
                break
        else:
            cand, sug = line, ""
        cand = unicodedata.normalize("NFC", cand)
        if cand and cand not in seen:
            seen.add(cand)
            out.append({"text": cand, "suggest": sug})
    return out


def survey(channels, candidates, min_hits: int = 1, top: int = 8,
           samples: int = 3) -> list:
    """후보 계량(FR40.18) — 읽기 전용·네트워크 0. 원본 `srt/`만 읽는다.

    후보별로 ⓐ 총 건수·영상 수·채널 수 ⓑ **후보를 포함한 어절 분포 상위 N**(= 오탐의
    직접 증거) ⓒ 문맥 샘플 ⓓ 자동 판정 제안(`reject`/`review`)과 **좌측 문맥 상위**
    (`left_exclude` 후보)를 낸다. 후보 자동 발굴은 이 도구의 일이 **아니다**.
    """
    import corrector                               # 큐 파서 재사용 (지연 임포트)
    stat = {c["text"]: {"candidate": c["text"], "suggest": c.get("suggest", ""),
                        "hits": 0, "videos": 0, "channels": set(), "exact": 0,
                        "tokens": {}, "left": {}, "samples": []}
            for c in candidates}
    for channel in channels:
        srt_dir = config.channel_subdirs(channel)["srt"]
        if not srt_dir.exists():
            continue
        for srt_file in sorted(srt_dir.glob("*.srt")):
            try:
                cues = corrector.parse_cues(srt_file.read_text(encoding="utf-8"))
            except Exception:                       # pragma: no cover - 손상 파일 방어
                continue
            texts = [c.text for c in cues]
            joined = "\n".join(texts)
            for cand, st in stat.items():
                if cand not in joined:
                    continue
                st["videos"] += 1
                st["channels"].add(channel)
                for ci, text in enumerate(texts):
                    if cand not in text:
                        continue
                    for token in _tokens(text):
                        if cand not in token:
                            continue
                        st["hits"] += 1
                        st["tokens"][token] = st["tokens"].get(token, 0) + 1
                        if is_exact_token(token, cand):
                            st["exact"] += 1
                        else:
                            pos = token.find(cand)
                            left = token[max(0, pos - 2):pos]
                            if left:
                                st["left"][left] = st["left"].get(left, 0) + 1
                    if len(st["samples"]) < samples:
                        st["samples"].append({
                            "channel": channel, "basename": srt_file.stem,
                            "cue": cues[ci].number,
                            "text": text.replace("\n", " ")[:120]})
    out = []
    for cand in (c["text"] for c in candidates):
        st = stat[cand]
        if st["hits"] < min_hits:
            continue
        ratio = st["exact"] / st["hits"] if st["hits"] else 0.0
        top_tokens = sorted(st["tokens"].items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        top_left = sorted(st["left"].items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        out.append({
            "candidate": cand, "suggest": st["suggest"],
            "hits": st["hits"], "videos": st["videos"],
            "channels": len(st["channels"]), "exact": st["exact"],
            "exact_ratio": round(ratio, 3),
            "verdict": "reject" if ratio < EXACT_RATIO_MIN else "review",
            "tokens": [{"token": t, "n": n} for t, n in top_tokens],
            "left_context": [{"text": t, "n": n} for t, n in top_left],
            "samples": st["samples"],
        })
    return sorted(out, key=lambda d: -d["hits"])


def hold_report(domain: str, surveys, limit: int = 50) -> str:
    """도메인별 후보 리포트 1개(FR40.19ⓐ·상한 50). 영상 단위 리뷰를 만들지 않는다."""
    import datetime
    lines = [f"# 교정 후보 리포트 — {domain}", "",
             f"측정: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · "
             f"`./yt.sh terms` 실측 · 후보 {len(surveys)}건(표시 상한 {limit})", "",
             "> **hold는 규칙 단위다**(DQ-71) — 영상마다 보지 않는다. 한 규칙을 확정하면 "
             "관련 영상이 한 번에 처리된다.", "",
             "> `verdict: reject`는 **어절 완전일치 비율이 "
             f"{int(EXACT_RATIO_MIN * 100)}% 미만**이라는 뜻이다(= 대부분이 다른 단어의 "
             "일부). 채택하려면 `left_exclude`·`require`로 문맥을 좁혀야 한다.", "",
             "> ⚠ **`verdict`는 제안일 뿐이다 — 판단 근거는 어절 분포다.** 후보 뒤 1음절이 "
             "우연히 조사와 같으면 완전일치가 부풀려진다(실측: `오케` 100건 중 `오케이`"
             "60여 건이 `오케`+조사 `이`로 세어져 73%가 나왔다 — 실제로는 대부분 \"okay\"다). "
             "반드시 어절 분포를 눈으로 보고 채택·배제 조건을 정한다.", ""]
    for s in surveys[:limit]:
        lines += [f"## `{s['candidate']}`"
                  + (f" → `{s['suggest']}`(제안)" if s["suggest"] else ""), "",
                  f"- 건수 **{s['hits']}** · 영상 {s['videos']}편 · 채널 {s['channels']}개",
                  f"- 어절 완전일치 **{s['exact']}/{s['hits']}** "
                  f"({s['exact_ratio']:.0%}) → **{s['verdict']}**"]
        if s["tokens"]:
            lines.append("- 어절 분포: " + " · ".join(
                f"`{t['token']}` {t['n']}" for t in s["tokens"]))
        if s["left_context"]:
            lines.append("- 좌측 문맥(= `left_exclude` 후보): " + " · ".join(
                f"`{t['text']}` {t['n']}" for t in s["left_context"]))
        for smp in s["samples"]:
            lines.append(f"  - 예 `{smp['basename'][:40]}` 큐 {smp['cue']}: "
                         f"{smp['text']}")
        lines.append("")
    return "\n".join(lines) + "\n"

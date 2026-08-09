"""자막 품질 검토 — 규칙 기반 + LLM. FR4."""
import os
import csv
import re
import logging

import config

log = logging.getLogger("quality")


# ─── 규칙 기반 지표 ──────────────────────────────────────────────────────────
def korean_ratio(text: str) -> float:
    if not text:
        return 0.0
    ko = len(re.findall(r'[가-힣]', text))
    total = len(re.sub(r'\s', '', text))
    return ko / total if total else 0.0


def special_ratio(text: str) -> float:
    if not text:
        return 0.0
    special = len(re.findall(r'[^\w\s가-힣.,!?]', text))
    total = len(re.sub(r'\s', '', text))
    return special / total if total else 0.0


def repeat_ratio(text: str) -> float:
    """동일 줄 반복 비율."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return 0.0
    unique = len(set(lines))
    return 1.0 - (unique / len(lines))


def word_count(text: str) -> int:
    return len(text.split())


def check_rules(text: str) -> tuple:
    """
    규칙 기반 검토. 반환: (verdict, reason, metrics)
    verdict: 'OK' | 'SUSPECT'
    """
    wc = word_count(text)
    ko = korean_ratio(text)
    rep = repeat_ratio(text)
    sp = special_ratio(text)
    metrics = {"word_count": wc, "ko_ratio": round(ko, 3),
               "repeat_ratio": round(rep, 3), "special_ratio": round(sp, 3)}

    reasons = []
    if wc < config.MIN_WORD_COUNT:
        reasons.append(f"단어수 부족({wc})")
    if rep > config.MAX_REPEAT_RATIO:
        reasons.append(f"반복과다({rep:.0%})")
    if ko < config.MIN_KO_RATIO:
        reasons.append(f"한국어비율낮음({ko:.0%})")
    if sp > config.MAX_SPECIAL_RATIO:
        reasons.append(f"특수문자과다({sp:.0%})")

    verdict = "SUSPECT" if reasons else "OK"
    return verdict, "; ".join(reasons), metrics


# ─── LLM 검토 (FR4.3) ────────────────────────────────────────────────────────
def check_llm(text: str) -> str:
    """Claude API로 자막 내용 검토. 영상당 1회. 장편은 앞 N단어 샘플."""
    try:
        import anthropic
    except ImportError:
        return "anthropic 미설치"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "API키 없음"

    words = text.split()
    sample = " ".join(words[:config.LLM_REVIEW_SAMPLE])

    prompt = (
        "다음은 YouTube 자막입니다. 내용이 자연스럽고 의미가 통하는지, "
        "전사 오류나 깨진 부분이 있는지 한 줄로 평가하세요.\n\n"
        f"자막:\n{sample}\n\n"
        "형식: [정상|이상] 사유"
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"LLM오류:{exc}"


# ─── 채널 검토 ───────────────────────────────────────────────────────────────
class QualityChecker:

    def __init__(self, channel: str):
        self.channel = channel
        self.dirs = config.channel_subdirs(channel)
        self.report_path = config.channel_dir(channel) / "review_report.csv"

    def review(self, use_llm: bool = False) -> list:
        txt_dir = self.dirs["txt"]
        if not txt_dir.exists():
            log.warning(f"txt 폴더 없음: {self.channel}")
            return []

        rows = []
        suspects = []
        for txt_file in sorted(txt_dir.glob("*.txt")):
            text = txt_file.read_text(encoding="utf-8")
            verdict, reason, metrics = check_rules(text)
            llm_comment = ""
            if verdict == "SUSPECT" and use_llm:
                llm_comment = check_llm(text)
            row = {
                "basename": txt_file.stem,
                "verdict": verdict,
                "reason": reason,
                **metrics,
                "llm_comment": llm_comment,
            }
            rows.append(row)
            if verdict == "SUSPECT":
                suspects.append(txt_file.stem)

        self._write_report(rows)

        log.info(f"━━━ 품질검토: {self.channel} ━━━")
        log.info(f"  전체 {len(rows)} · SUSPECT {len(suspects)}")
        for s in suspects:
            log.info(f"  ⚠ {s}")
        return suspects

    def _write_report(self, rows: list):
        fields = ["basename", "verdict", "reason", "word_count",
                  "ko_ratio", "repeat_ratio", "special_ratio", "llm_comment"]
        with open(self.report_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    def get_suspects(self) -> list:
        """review_report.csv에서 SUSPECT basename 목록 로드."""
        if not self.report_path.exists():
            return []
        suspects = []
        with open(self.report_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("verdict") == "SUSPECT":
                    suspects.append(row["basename"])
        return suspects

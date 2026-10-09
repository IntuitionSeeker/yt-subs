"""정합 감사(`audit`) · 데이터 건전성 점검(`doctor`) — FR38 (DESIGN §2.14·§3.12·§5.11).

**고치지 않는다.** 두 명령은 이상 신호를 보고하고 종료코드로만 말한다 (FR38.1·DQ-52) —
문서가 낡은 것인지 코드가 틀린 것인지는 기계가 정할 수 없다. `--fix` 류는 없다.

**읽기 전용 · 네트워크 0 · 부작용 0** (FR38.3). 파일 생성·수정·삭제가 없고 **디렉터리 생성도 없다** —
그래서 ChromaDB는 `chroma.sqlite3`를 `mode=ro` sqlite로 직접 읽는다.
`KLIndexer._get_client()`는 `mkdir(parents=True, exist_ok=True)`를 하고 `chromadb.PersistentClient`는
스키마를 쓰므로, 평소 경로로 점검하면 `chroma/`가 없는 채널에 빈 디렉터리가 생긴다 (FR38.15·DQ-55).
**`chromadb`·`sentence_transformers`·`yt_dlp`를 임포트하지 않는다.**

CLI는 둘로 나뉘지만(실행 환경이 갈린다 — audit은 `output/` 없이 완결, doctor는 `output/`이 전부)
모듈은 하나다: 발견 표현·심각도·예외·출력·종료코드가 두 명령에서 한 글자도 달라서는 안 된다 (DQ-51).
"""
import io
import csv
import json
import os
import re
import sqlite3
import datetime
import unicodedata
from pathlib import Path

import config

# ─── 계약 (FR38.4·38.7) ──────────────────────────────────────────────────────


class Severity:
    """발견의 심각도 3단. 검사 함수가 새 등급을 만들지 않는다 (FR38.7).

    배정 기준은 "중요도"가 아니라 **기계가 확신할 수 있는가**다:
    ERROR = 모순 확정 · WARN = 신호는 확실하나 조치는 사람 · INFO = 문서화된 제약·건너뜀.
    """
    ERROR = "error"
    WARN = "warn"
    INFO = "info"

    ORDER = {"error": 0, "warn": 1, "info": 2}
    MARK = {"error": "E", "warn": "W", "info": "I"}


class Finding:
    """발견 1건. `check`·`target`·`severity` 3필드가 계약이다 (FR38.4).

    `target`은 waiver 매칭 키가 되는 **안정 문자열**이어야 한다 — 가변 메시지를 키로 쓰면
    문구를 다듬는 순간 waiver가 조용히 풀린다.
    """

    __slots__ = ("check", "target", "message", "severity", "evidence", "skip")

    def __init__(self, check, target, message, severity, evidence=None, skip=False):
        self.check = check
        self.target = str(target)
        self.message = message
        self.severity = severity
        self.evidence = evidence or {}
        # `skip=True` = "이 대상은 판정하지 못했다" — `--json`의 `checks_skipped`에 실린다.
        # "이상 없음"과 "확인 못 함"을 섞지 않기 위한 표시다 (FR38.6)
        self.skip = skip

    # 정렬 키 — 심각도 → 검사ID → 대상
    def _key(self):
        return (Severity.ORDER.get(self.severity, 9), self.check, self.target)

    def to_dict(self) -> dict:
        d = {"check": self.check, "target": self.target,
             "severity": self.severity, "message": self.message}
        if self.evidence:
            d["evidence"] = self.evidence
        return d

    def line(self) -> str:
        mark = Severity.MARK.get(self.severity, "?")
        ev = ""
        if self.evidence:
            ev = " (" + " · ".join(f"{k}={v}" for k, v in self.evidence.items()) + ")"
        return f"[{mark}] {self.check} {self.target}: {self.message}{ev}"


class CheckFailure(Exception):
    """점검 자체 실패 — 종료코드 **3**. FR38.6

    "이상 없음"(0)과 "확인 못 함"(3)을 같은 코드로 내면 도구가 거짓말을 한다
    (`migrate-groups --rollback`이 무동작인데 "롤백 완료"를 출력했던 F-1과 같은 실패 유형).
    """


# 검사 **전체**를 판정하지 못했을 때 쓰는 대상 이름. 이 표시가 있으면 그 검사의 waiver는
# stale 판정에서 빠진다 — 돌지 않은 검사 때문에 예외가 "썩었다"고 말하면 그것이 오탐이다.
WHOLE_CHECK = "(전체)"

# 한 검사가 쏟아낼 수 있는 발견 상한 — 넘으면 마지막에 생략 건수를 INFO로 남긴다.
# 무한정 쏟는 도구는 아무도 읽지 않는다(FR38.4). 조용히 잘라내지도 않는다.
FINDING_CAP = 50


def _capped(check: str, findings: list) -> list:
    if len(findings) <= FINDING_CAP:
        return findings
    kept = findings[:FINDING_CAP]
    kept.append(Finding(check, "(생략)", f"같은 검사의 발견이 많아 {FINDING_CAP}건까지만 출력했다",
                        Severity.INFO, {"total": len(findings),
                                        "omitted": len(findings) - FINDING_CAP}))
    return kept


# ─── 예외 2차 — audit_waivers.yaml (FR38.10 · DESIGN §5.11) ──────────────────

WAIVERS_FILE = config.BASE_DIR / "audit_waivers.yaml"
_WAIVER_KEYS = {"check", "target", "reason", "added", "expires"}


def _parse_waivers_text(text: str) -> list:
    """`audit_waivers.yaml` 파싱. PyYAML이 있으면 그것을, 없으면 최소 파서를 쓴다.

    audit은 `output/`·Docker·의존성 없이 성립해야 하므로(FR38.12) PyYAML 부재가
    점검 실패가 되지 않게 한다. 최소 파서는 DESIGN §5.11이 규정한 모양
    (`waivers:` + `- key: value` 평면 항목)만 받아들이고, 그 밖은 **파싱 불가로 올린다**
    (조용히 0건으로 넘기면 예외가 전부 풀린 것을 아무도 모른다).
    """
    try:
        import yaml                                    # 지연 임포트
    except ImportError:
        return _parse_waivers_minimal(text)
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        raise CheckFailure(f"{WAIVERS_FILE.name} 파싱 실패: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("waivers") or [], list):
        raise CheckFailure(f"{WAIVERS_FILE.name} 형식 오류 — 최상위 `waivers:` 목록이어야 한다")
    return list(data.get("waivers") or [])


def _parse_waivers_minimal(text: str) -> list:
    items, cur = [], None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"waivers:\s*", line):
            continue
        m = re.fullmatch(r"\s*-\s*([a-z_]+):\s*(.*)", line)
        if m:
            cur = {}
            items.append(cur)
            cur[m.group(1)] = _unquote(m.group(2))
            continue
        m = re.fullmatch(r"\s+([a-z_]+):\s*(.*)", line)
        if m and cur is not None:
            cur[m.group(1)] = _unquote(m.group(2))
            continue
        raise CheckFailure(f"{WAIVERS_FILE.name} 파싱 실패(PyYAML 없음, 최소 파서 범위 밖): {raw!r}")
    return items


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def load_waivers(path=None) -> list:
    """waiver 목록 적재. 파일 부재는 **예외 0건**이며 오류가 아니다 (DESIGN §5.11)."""
    p = Path(path) if path else WAIVERS_FILE
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckFailure(f"{p.name} 읽기 실패: {exc}")
    out = []
    for i, item in enumerate(_parse_waivers_text(text), 1):
        if not isinstance(item, dict):
            raise CheckFailure(f"{p.name} {i}번째 항목이 매핑이 아니다")
        check = str(item.get("check") or "").strip()
        target = str(item.get("target") or "").strip()
        w = {"check": check, "target": target,
             "reason": str(item.get("reason") or ""),
             "added": str(item.get("added") or ""),
             "expires": str(item.get("expires") or "") or None,
             "invalid": None}
        # 규약 위반은 **면제하지 않고 무효로 보고**한다(`waiver.invalid` = 오류).
        # 파일 자체를 못 읽는 것(→ 3)과 달리, 한 항목의 규약 위반으로 점검 전체를
        # 멈추면 나머지 검사 결과를 못 보게 되고 그것이 은폐와 다를 바 없다.
        if not check or not target:
            w["invalid"] = "check·target이 모두 필요하다"
        elif "*" in check or "*" in target or "?" in check or "?" in target:
            # ⓐ 와일드카드 금지 — 검사 한 종류를 통째로 끌 수 있으면 그것이 첫 은폐 수단이 된다
            w["invalid"] = "와일드카드·정규식을 쓸 수 없다(정확 일치만)"
        else:
            unknown = set(item) - _WAIVER_KEYS
            if unknown:
                w["invalid"] = f"알 수 없는 키: {sorted(unknown)}"
        out.append(w)
    return out


def _expired(waiver: dict, today=None) -> bool:
    """`expires`는 **선택** 필드다 — 없으면 만료되지 않는다(만료 강제는 새 수작업이 된다)."""
    exp = waiver.get("expires")
    if not exp:
        return False
    try:
        d = datetime.date.fromisoformat(exp)
    except ValueError:
        raise CheckFailure(f"{WAIVERS_FILE.name}: expires 날짜 형식 오류 {exp!r}")
    return (today or datetime.date.today()) > d


def apply_waivers(findings: list, waivers: list, checks_run=None, today=None):
    """`(check, target)` **정확 일치**로 면제. 와일드카드·정규식은 지원하지 않는다 (FR38.10).

    반환 `(남은 발견, 면제된 발견, stale waiver Finding 목록)`.
    대응 발견이 없는 waiver는 `WARN`으로 **승격**된다 — 이 승격이 예외 파일이 썩지 않게 하는
    유일한 장치다(`note`·`tickers`가 죽어 있던 이유는 "아무도 읽지 않아서"였다).
    """
    index = {(w["check"], w["target"]): w for w in waivers
             if not w.get("invalid")}
    used = set()
    kept, waived = [], []
    for f in findings:
        key = (f.check, f.target)
        w = index.get(key)
        if w is not None and not _expired(w, today):
            used.add(key)
            waived.append((f, w))
            continue
        if w is not None:                       # 만료된 waiver → 원래 심각도로 보고
            used.add(key)
        kept.append(f)
    stale = []
    run = set(checks_run) if checks_run is not None else None
    for w in waivers:
        if w.get("invalid"):
            stale.append(Finding("waiver.invalid", f"{w['check']}/{w['target']}",
                                 f"예외 선언이 규약 위반이라 적용하지 않았다 — {w['invalid']}",
                                 Severity.ERROR))
            continue
        key = (w["check"], w["target"])
        if key in used:
            continue
        if run is not None and w["check"] not in run:
            continue                            # 실행하지 않은 검사는 stale 판정 대상이 아니다
        stale.append(Finding("waiver.stale", f"{w['check']}/{w['target']}",
                             "대응하는 발견이 없는 예외 — 해소됐으면 지운다",
                             Severity.WARN, {"added": w.get("added") or "?"}))
    return kept, waived, stale


# ─── 출력 (FR38.4·38.5) ──────────────────────────────────────────────────────


def _counts(findings: list) -> dict:
    c = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        if f.severity in c:
            c[f.severity] += 1
    return c


def exit_code(findings: list, strict: bool = False) -> int:
    """`0` 이상 없음 · `1` 경고만 · `2` 오류 1건+ (FR38.6).

    `strict`면 경고도 실패(2)로 올린다. **점검 자체 실패(3)는 이 함수가 내지 않는다** —
    `CheckFailure`가 `main`까지 올라가 3으로 변환된다.
    """
    c = _counts(findings)
    if c["error"]:
        return 2
    if c["warn"]:
        return 2 if strict else 1
    return 0


def render_text(command: str, findings: list, waived: list, checks_run: list,
                skipped: list, elapsed: float, code: int) -> str:
    """발견 1건 = 1줄. 0건이면 **조용하다**(요약 1줄만) — FR38.4"""
    lines = [f.line() for f in sorted(findings, key=lambda x: x._key())]
    c = _counts(findings)
    lines.append(f"검사 {len(checks_run)}개 · 오류 {c['error']} · 경고 {c['warn']} "
                 f"· 정보 {c['info']} · 예외적용 {len(waived)} · 소요 {elapsed:.1f}s "
                 f"· 종료코드 {code}")
    if skipped:
        lines.append(f"  (건너뜀 {len(skipped)}건 — --json 으로 상세)")
    return "\n".join(lines)


def render_json(command: str, findings: list, waived: list, checks_run: list,
                skipped: list, elapsed: float, code: int, stale=None) -> str:
    """사람용 출력과 **같은 발견 집합**이며 형식만 다르다 (FR38.5 · DESIGN §5.11)."""
    c = _counts(findings)
    payload = {
        "command": command,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(elapsed, 3),
        "checks_run": list(checks_run),
        "checks_skipped": list(skipped),
        "findings": [f.to_dict() for f in sorted(findings, key=lambda x: x._key())],
        "waived": [dict(f.to_dict(), reason=w.get("reason", "")) for f, w in waived],
        "stale_waivers": [f.to_dict() for f in (stale or [])],
        # exit_code를 payload에도 싣는다 — 파이프로 받는 쪽이 프로세스 코드를 잃어도
        # 판정이 가능해야 한다 (DESIGN §5.11)
        "summary": {"error": c["error"], "warn": c["warn"], "info": c["info"],
                    "waived": len(waived), "exit_code": code},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ─── 공통 유틸 ───────────────────────────────────────────────────────────────


def _read_text(path: Path, what: str) -> str:
    """문서 적재 — 부재·읽기 실패는 **점검 실패(3)** 다 (FR38.6)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckFailure(f"{what} 읽기 실패: {path} ({exc})")


def _section(lines: list, start_prefix: str, end_prefix: str) -> list:
    """`start_prefix`로 시작하는 줄부터 `end_prefix` 직전까지. 스코프 규칙의 구현체.

    스코프를 나누지 않으면 §6 트레이서빌리티 행(`| FR13.6 |`)이 §3 FR 정의로 세어져
    **중복 15건을 허위 보고**한다(실측, DQ-53).
    """
    s = e = None
    for i, line in enumerate(lines):
        if s is None:
            if line.startswith(start_prefix):
                s = i
        elif line.startswith(end_prefix):
            e = i
            break
    if s is None:
        return []
    return lines[s:e if e is not None else len(lines)]


_BASELINE_RE = re.compile(r"(\d+)\s*passed\s*/\s*(\d+)\s*skipped")


def _baselines(text: str) -> list:
    return [f"{m.group(1)} passed / {m.group(2)} skipped" for m in _BASELINE_RE.finditer(text)]


# ─── AuditContext (FR38.11~38.12) ────────────────────────────────────────────

_SRC_GLOBS = ("*.py", "dashboard/*.py")
_SRC_FILES = ("dashboard/index.html", "yt.sh")
_WALK_SKIP = {".git", "__pycache__", "output", "firefox_profile", "node_modules",
              ".pytest_cache", ".venv"}


class AuditContext:
    """입력을 **한 번만 읽어** 검사들이 공유한다. `output/`을 보지 않는다 (FR38.2·38.12)."""

    DOCS = {
        "REQUIREMENTS.md": "REQUIREMENTS.md",
        "DESIGN.md": "DESIGN.md",
        "CLAUDE.md": "CLAUDE.md",
        "qa-verifier": ".claude/agents/qa-verifier.md",
        "pipeline-verify": ".claude/skills/pipeline-verify/SKILL.md",
        "spec-sync": ".claude/skills/spec-sync/SKILL.md",
        "tests": "tests/test_unit.py",
        "main": "main.py",
    }

    def __init__(self, root=None, baseline=None):
        self.root = Path(root or config.BASE_DIR)
        self.baseline = baseline
        self.text, self.lines = {}, {}
        for key, rel in self.DOCS.items():
            t = _read_text(self.root / rel, rel)
            self.text[key] = t
            self.lines[key] = t.splitlines()
        self.rel = dict(self.DOCS)

        # 소스 단어 색인 — §6 식별자 토큰의 실재 판정용 (FR38.11ⓑ)
        src_paths = []
        for pat in _SRC_GLOBS:
            src_paths += sorted(self.root.glob(pat))
        for rel in _SRC_FILES:
            p = self.root / rel
            if p.exists():
                src_paths.append(p)
        self.source_paths = src_paths
        blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in src_paths)
        self.words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", blob))
        server = self.root / "dashboard" / "server.py"
        self.server_text = server.read_text(encoding="utf-8") if server.exists() else ""

        # 저장소 파일 경로 색인 (경로 토큰 실재 판정)
        self.repo_files = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames
                           if d not in _WALK_SKIP and not d.startswith("output_backup")]
            for fn in filenames:
                rel = Path(dirpath, fn).relative_to(self.root).as_posix()
                self.repo_files.add(rel)

        # main.py 서브파서 — 텍스트 대조(임포트 부작용 없이)
        self.cli_code = set(re.findall(r'add_parser\(\s*"([^"]+)"', self.text["main"]))

    # ── 스코프 헬퍼 ─────────────────────────────────────────────────────────
    def req_section(self, start, end):
        return _section(self.lines["REQUIREMENTS.md"], start, end)

    def design_section(self, start, end):
        return _section(self.lines["DESIGN.md"], start, end)

    def has_file(self, token: str) -> bool:
        if (self.root / token).exists():
            return True
        tok = token.lstrip("./")
        return any(rel == tok or rel.endswith("/" + tok) for rel in self.repo_files)


# ─── audit 검사 ①  pytest 기준선 5곳 (FR38.11) ───────────────────────────────


def check_pytest_baseline(ctx: AuditContext) -> list:
    """기준선 값이 **5곳에서 동일**한지. 불일치 = 오류 (DQ-54).

    한 세션에 4번 어긋났다(124 → 140 vs 163 → 180 → 185). `CLAUDE.md`는 이력 표에
    과거 값이 **정당하게** 남으므로 `기준선 X→Y`의 **마지막 행 오른쪽 값**만 본다.
    """
    out = []
    found = {}

    def put(where, values):
        uniq = sorted(set(values))
        if not uniq:
            out.append(Finding("audit.pytest-baseline", where,
                               "기준선 값을 찾을 수 없다(문서가 이 위치에 있다고 선언한다)",
                               Severity.ERROR))
            return
        if len(uniq) > 1:
            out.append(Finding("audit.pytest-baseline", where,
                               "한 위치 안에서 값이 서로 다르다", Severity.ERROR,
                               {"values": " | ".join(uniq)}))
        found[where] = uniq[0]

    put("REQUIREMENTS.md §7", _baselines("\n".join(ctx.req_section("## 7. ", "## 8. "))))
    put("DESIGN.md §9.1", _baselines("\n".join(ctx.design_section("### 9.1 ", "### 9.2 "))))
    put(ctx.rel["qa-verifier"], _baselines(ctx.text["qa-verifier"]))
    put(ctx.rel["pipeline-verify"], _baselines(ctx.text["pipeline-verify"]))

    # CLAUDE.md — 이력 표의 **마지막** `기준선 …→…` 오른쪽만 (과거 값 31·33·60·115·140·163은 정당)
    arrows = re.findall(r"기준선\s*[^|→]*→\s*([^|]*)", ctx.text["CLAUDE.md"])
    put(ctx.rel["CLAUDE.md"] + " 이력 최신 행", _baselines(arrows[-1]) if arrows else [])

    values = set(found.values())
    if len(values) > 1:
        for where, val in sorted(found.items()):
            out.append(Finding("audit.pytest-baseline", where,
                               "5곳의 기준선 값이 일치하지 않는다", Severity.ERROR,
                               {"value": val}))
    elif ctx.baseline:
        want = _baselines(ctx.baseline)
        if not want:
            raise CheckFailure(f"--baseline 형식 오류: {ctx.baseline!r} "
                               f'(예: "185 passed / 1 skipped")')
        if want[0] not in values:
            out.append(Finding("audit.pytest-baseline", "--baseline",
                               "문서의 기준선이 실측값과 다르다", Severity.ERROR,
                               {"문서": sorted(values)[0] if values else "?",
                                "실측": want[0]}))
    return out


# ─── audit 검사 ②  V-U 번호 (FR38.11) ────────────────────────────────────────

_VU_ROW_RE = re.compile(r"^\|\s*V-U(\d+[a-z]?)\s*\|")
_VU_MARKER_RE = re.compile(r"^#.*\bV-U(\d+[a-z]?)\b")


def _vu_doc_rows(ctx: AuditContext) -> list:
    """DESIGN §9.1a 표의 `(ID, 행 전체)` 목록."""
    rows = []
    for line in ctx.design_section("#### 9.1a", "#### 9.1b"):
        m = _VU_ROW_RE.match(line)
        if m:
            rows.append((m.group(1), line))
    return rows


def _vu_code_ids(ctx: AuditContext) -> set:
    """정본 = `tests/test_unit.py`의 **섹션 헤더 주석**(행 머리의 `#`)."""
    ids = set()
    for line in ctx.lines["tests"]:
        m = _VU_MARKER_RE.match(line)
        if m:
            ids.add(m.group(1))
    return ids


def check_vu_numbers(ctx: AuditContext) -> list:
    """문서 §9.1a ↔ 테스트 섹션 헤더. 유령·누락 모두 오류 (정본은 테스트 코드).

    면제(FR38.9): `**테스트 미구현**`(V-U3 = 정상 상태) · `(구현 예정)`(문서 선행) ·
    **위치 열이 `tests/test_unit.py`가 아닌 항목**(V-U8·V-U9 = mock 스크립트 소관 —
    마커가 없는 것이 현재 정상이며 파일 실재만 본다).
    """
    out = []
    code = _vu_code_ids(ctx)
    doc = set()
    for vid, row in _vu_doc_rows(ctx):
        doc.add(vid)
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        where = cells[-1] if cells else ""
        if "테스트 미구현" in where:
            continue                                     # V-U3 — 정상 상태
        if "구현 예정" in where:
            out.append(Finding("audit.vu-numbers", f"V-U{vid}",
                               "문서 선행 항목((구현 예정) 표기) — 코드 마커를 요구하지 않는다",
                               Severity.INFO))
            continue
        if "tests/test_unit.py" not in where:
            # 위치 열이 다른 파일이면 마커 대신 **파일 실재**만 확인한다
            files = re.findall(r"`([^`]+\.py)`", where)
            for f in files:
                if not ctx.has_file(f):
                    out.append(Finding("audit.vu-numbers", f"V-U{vid}",
                                       f"위치로 적힌 파일이 없다: {f}", Severity.ERROR))
            continue
        if vid not in code:
            out.append(Finding("audit.vu-numbers", f"V-U{vid}",
                               "문서에만 있다(테스트에 섹션 헤더 주석이 없다)", Severity.ERROR))
    for vid in sorted(code - doc):
        out.append(Finding("audit.vu-numbers", f"V-U{vid}",
                           "테스트에만 있다(DESIGN §9.1a에 행이 없다)", Severity.ERROR))
    return out


# ─── audit 검사 ③  "다음 번호" 포인터 (FR38.11 · DQ-54) ──────────────────────


def _max_num(values) -> int:
    nums = [int(v) for v in values if str(v).isdigit()]
    return max(nums) if nums else 0


def check_next_pointers(ctx: AuditContext) -> list:
    """손으로 들고 있는 "다음 신규 번호"가 **정본 최대값+1**과 같은지 (DQ-54).

    V-U의 정본 최대값은 **문서 §9.1a ∪ 테스트 마커**다 — 번호 선점(문서 선행)이
    정상 절차이므로 코드만 보면 선점 직후에 반드시 틀린다.
    """
    out = []
    vu = max(_max_num(v for v, _ in _vu_doc_rows(ctx)), _max_num(_vu_code_ids(ctx)))
    vi = _max_num(re.findall(r"V-I(\d+)", "\n".join(ctx.design_section("### 9.2", "### 9.3"))))
    vd = _max_num(re.findall(r"V-D(\d+)", "\n".join(ctx.design_section("### 9.3", "## 10."))))
    dq = _max_num(re.findall(r"^\|\s*DQ-(\d+)\s*\|", "\n".join(
        ctx.design_section("## 10.", "## 11.")), re.M))

    def cmp(where, label, got, want):
        if got is None:
            out.append(Finding("audit.next-pointers", where,
                               f"{label} 포인터 문장을 찾을 수 없다", Severity.ERROR))
        elif got != want:
            out.append(Finding("audit.next-pointers", where,
                               f"{label} 포인터가 정본 최대값+1과 다르다", Severity.ERROR,
                               {"문서": got, "계산": want}))

    m = re.search(r"다음 신규 번호는\s*\*{0,2}V-U(\d+)", ctx.text["DESIGN.md"])
    cmp("DESIGN.md §9.1", "V-U", int(m.group(1)) if m else None, vu + 1)

    sync = ctx.text["spec-sync"]
    m = re.search(r"다음 번호:\s*\*{0,2}V-U(\d+)\*{0,2}\s*·\s*\*{0,2}V-I(\d+)\*{0,2}"
                  r"\s*·\s*\*{0,2}V-D(\d+)", sync)
    base = ctx.rel["spec-sync"]
    cmp(base + " V-U", "V-U", int(m.group(1)) if m else None, vu + 1)
    cmp(base + " V-I", "V-I", int(m.group(2)) if m else None, vi + 1)
    cmp(base + " V-D", "V-D", int(m.group(3)) if m else None, vd + 1)
    m = re.search(r"DQ 다음 번호는\s*\*{0,2}DQ-(\d+)", sync)
    cmp(base + " DQ", "DQ", int(m.group(1)) if m else None, dq + 1)
    return out


# ─── audit 검사 ④  FR 번호 (FR38.11) ─────────────────────────────────────────

_FR_ROW_RE = re.compile(r"^\|\s*FR(\d+)\.(\d+)\s*\|")


def _fr_defs(ctx: AuditContext) -> list:
    """§3 FR 표의 `(major, sub)` 목록. 스코프는 `## 3.`~`## 4.` **사이만**."""
    return [(int(m.group(1)), int(m.group(2)))
            for line in ctx.req_section("## 3. ", "## 4. ")
            for m in [_FR_ROW_RE.match(line)] if m]


def _gaps_report(check, label, ids, out):
    """결번 = 경고(의도적 폐기일 수 있다) · 중복 = 오류."""
    seen = {}
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
    for i, n in sorted(seen.items()):
        if n > 1:
            out.append(Finding(check, f"{label}{_fmt_id(i)}", "번호가 중복이다",
                               Severity.ERROR, {"count": n}))


def _fmt_id(i):
    return f"{i[0]}.{i[1]}" if isinstance(i, tuple) else str(i)


def check_fr_numbers(ctx: AuditContext) -> list:
    out = []
    defs = _fr_defs(ctx)
    _gaps_report("audit.fr-numbers", "FR", defs, out)
    majors = sorted({m for m, _ in defs})
    if majors:
        for n in range(1, majors[-1] + 1):
            if n not in majors:
                out.append(Finding("audit.fr-numbers", f"FR{n}", "결번(§3에 정의가 없다)",
                                   Severity.WARN))
        for maj in majors:
            subs = sorted({s for m, s in defs if m == maj})
            for n in range(1, subs[-1] + 1):
                if n not in subs:
                    out.append(Finding("audit.fr-numbers", f"FR{maj}.{n}", "결번",
                                       Severity.WARN))
    return _capped("audit.fr-numbers", out)


# ─── audit 검사 ⑤  DQ 번호 (FR38.11) ─────────────────────────────────────────

_DQ_ROW_RE = re.compile(r"^\|\s*DQ-(\d+)\s*\|")


def check_dq_numbers(ctx: AuditContext) -> list:
    """정본은 **DESIGN §10**. REQUIREMENTS §8은 초기 결정의 역사 표라 §10과 겹치는 것이
    정상이므로 **중복 판정에서 제외**하고 "§10에 존재하는지"만 본다(실측 허위 6건 회귀).
    """
    out = []
    ids = [int(m.group(1)) for line in ctx.design_section("## 10.", "## 11.")
           for m in [_DQ_ROW_RE.match(line)] if m]
    _gaps_report("audit.dq-numbers", "DQ-", ids, out)
    if ids:
        for n in range(1, max(ids) + 1):
            if n not in ids:
                out.append(Finding("audit.dq-numbers", f"DQ-{n}", "결번(§10에 정의가 없다)",
                                   Severity.WARN))
    known = set(ids)
    for line in ctx.req_section("## 8. ", "## 9. ") or ctx.req_section("## 8. ", "￿"):
        m = _DQ_ROW_RE.match(line)
        if m and int(m.group(1)) not in known:
            out.append(Finding("audit.dq-numbers", f"DQ-{int(m.group(1))}",
                               "REQUIREMENTS §8이 참조하는데 DESIGN §10에 없다", Severity.ERROR))
    return _capped("audit.dq-numbers", out)


# ─── audit 검사 ⑥  트레이서빌리티 (FR38.11 · DQ-53) ──────────────────────────

_PATH_TOKEN_RE = re.compile(r"[\w./\-]+\.(?:py|html|sh)$")
_ROUTE_TOKEN_RE = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}\-]*)$")
_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def check_traceability(ctx: AuditContext) -> list:
    """§6의 백틱 토큰 중 **판정 가능한 것만** 실재 확인 + FR 번호 양방향 대조.

    토큰 분류 — ⓐ `*.py`/`*.html`/`*.sh` 경로 → 실재 ⓑ 식별자 → 소스 단어 경계 일치
    ⓒ `GET|POST /경로` → `dashboard/server.py`의 라우트 문자열. **그 밖은 침묵**한다
    (CSS 선택자 `.chan-card`·필드명·`""`·런타임 산출물 `.scheduler.json` 등 실측 140건).
    판정 불가를 "의심"으로 올리면 그 순간 아무도 이 도구를 보지 않는다(FR38.8).
    """
    out = []
    sec = ctx.req_section("## 6. ", "## 7. ")
    tokens = {}
    for line in sec:
        for tok in re.findall(r"`([^`]+)`", line):
            tokens.setdefault(tok, []).append(line)

    def miss(target, message):
        """토큰이 **오직** `(구현 예정)` 행에만 나오면 부재를 면제한다(문서 선행 = 정상)."""
        rows = tokens.get(target) or tokens.get(target + "()") or []
        if rows and all("구현 예정" in r for r in rows):
            out.append(Finding("audit.traceability", target,
                               "문서 선행((구현 예정) 표기) — 아직 없는 것이 정상",
                               Severity.INFO))
        else:
            out.append(Finding("audit.traceability", target, message, Severity.ERROR))

    for tok in sorted(tokens):
        t = tok.strip()
        if not t:
            continue
        if _PATH_TOKEN_RE.fullmatch(t):
            if not ctx.has_file(t):
                miss(t, "문서가 가리키는 파일이 저장소에 없다")
            continue
        m = _ROUTE_TOKEN_RE.fullmatch(t)
        if m:
            route = m.group(1)
            if f'"{route}"' not in ctx.server_text and f"'{route}'" not in ctx.server_text:
                miss(t, "dashboard/server.py에 해당 라우트가 없다")
            continue
        core = t[:-2] if t.endswith("()") else t
        if _IDENT_TOKEN_RE.fullmatch(core):
            last = core.split(".")[-1]
            if last not in ctx.words:
                miss(core, "소스(*.py · dashboard/*.py · index.html · yt.sh)에서 "
                           "찾을 수 없는 식별자")
            continue
        # 판정 불가 → 침묵 (FR38.8)

    # FR 번호 양방향 — §3에 있으나 §6에 없는 FR = 경고
    covered = set()
    for line in sec:
        if not line.startswith("|"):
            continue
        cell = line.split("|")[1].strip()
        if not cell.startswith("FR"):
            continue
        covered |= _expand_fr_cell(cell)
    for maj, sub in sorted(set(_fr_defs(ctx)) - covered):
        out.append(Finding("audit.traceability", f"FR{maj}.{sub}",
                           "§3에 정의됐으나 §6 트레이서빌리티에 없다", Severity.WARN))
    return _capped("audit.traceability", out)


def _expand_fr_cell(cell: str) -> set:
    """`FR38.16·38.18~38.20` 같은 표기를 `{(38,16),(38,18),(38,19),(38,20)}`으로 전개."""
    out = set()
    for part in re.split(r"[·,]", cell.replace("FR", "")):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\.(\d+)\s*~\s*(?:(\d+)\.)?(\d+)", part)
        if m:
            maj, a = int(m.group(1)), int(m.group(2))
            maj2 = int(m.group(3)) if m.group(3) else maj
            b = int(m.group(4))
            if maj2 == maj:
                out |= {(maj, x) for x in range(a, b + 1)}
            else:
                out |= {(maj, a), (maj2, b)}
            continue
        m = re.fullmatch(r"(\d+)\.(\d+)", part)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


# ─── audit 검사 ⑦  버전 헤더 (FR38.11) ───────────────────────────────────────


def check_doc_version(ctx: AuditContext) -> list:
    """두 문서의 버전 헤더 ↔ `연계 문서` 표기 ↔ 실제 최대 FR 번호."""
    out = []

    def one(text, pat, where, label):
        m = re.search(pat, text)
        if not m:
            out.append(Finding("audit.doc-version", where, f"{label} 표기를 찾을 수 없다",
                               Severity.ERROR))
            return None
        return m.group(1)

    rv = one(ctx.text["REQUIREMENTS.md"], r"\*\*버전:\*\*\s*v?([\d.]+)",
             "REQUIREMENTS.md", "버전")
    dv = one(ctx.text["DESIGN.md"], r"\*\*버전:\*\*\s*v?([\d.]+)", "DESIGN.md", "버전")
    r_link = one(ctx.text["REQUIREMENTS.md"], r"연계 문서:\*\*\s*DESIGN\.md v?([\d.]+)",
                 "REQUIREMENTS.md 연계 문서", "연계 버전")
    d_link = one(ctx.text["DESIGN.md"], r"연계 문서:\*\*\s*REQUIREMENTS\.md v?([\d.]+)",
                 "DESIGN.md 연계 문서", "연계 버전")
    vals = {"REQUIREMENTS.md 버전": rv, "DESIGN.md 버전": dv,
            "REQUIREMENTS.md → DESIGN": r_link, "DESIGN.md → REQUIREMENTS": d_link}
    present = {k: v for k, v in vals.items() if v}
    if len(set(present.values())) > 1:
        for k, v in sorted(present.items()):
            out.append(Finding("audit.doc-version", k, "버전 표기가 서로 다르다",
                               Severity.ERROR, {"value": f"v{v}"}))

    m = re.search(r"연계 문서:\*\*\s*REQUIREMENTS\.md v?[\d.]+\s*\(FR1~FR(\d+)\)",
                  ctx.text["DESIGN.md"])
    majors = sorted({mj for mj, _ in _fr_defs(ctx)})
    if not m:
        out.append(Finding("audit.doc-version", "DESIGN.md (FR1~FRn)",
                           "FR 범위 표기를 찾을 수 없다", Severity.ERROR))
    elif majors and int(m.group(1)) != majors[-1]:
        out.append(Finding("audit.doc-version", "DESIGN.md (FR1~FRn)",
                           "FR 범위 표기가 실제 최대 FR과 다르다", Severity.ERROR,
                           {"문서": f"FR{m.group(1)}", "실제": f"FR{majors[-1]}"}))
    return out


# ─── audit 검사 ⑧  CLI 명령 (FR38.11) ────────────────────────────────────────


def check_cli_commands(ctx: AuditContext) -> list:
    """문서 정본은 **REQUIREMENTS §5 ∪ DESIGN §8**이다 — 한쪽만 보면 오탐이 난다
    (§5에 없는 `ask`·`search`·`summarize`·`serve`·`test`는 §8에 있다).
    `(구현 예정)` 표기 행은 코드 부재를 **정보**로 낮춘다 (FR38.9).
    """
    out = []
    doc, planned = {}, set()
    for lines in (ctx.req_section("## 5. ", "## 6. "), ctx.design_section("## 8. ", "## 9. ")):
        for line in lines:
            for cmd in re.findall(r"\./yt\.sh\s+([a-z][a-z\-]*)", line):
                doc.setdefault(cmd, set()).add(line)
                if "구현 예정" in line:
                    planned.add(cmd)
    for cmd in sorted(doc):
        if cmd in ctx.cli_code:
            continue
        if cmd in planned:
            out.append(Finding("audit.cli-commands", cmd,
                               "문서 선행((구현 예정) 표기) — 서브파서가 아직 없다",
                               Severity.INFO))
        else:
            out.append(Finding("audit.cli-commands", cmd,
                               "문서에만 있다(main.py 서브파서가 없다)", Severity.ERROR))
    for cmd in sorted(ctx.cli_code - set(doc)):
        out.append(Finding("audit.cli-commands", cmd,
                           "코드에만 있다(REQUIREMENTS §5·DESIGN §8 어디에도 없다)",
                           Severity.ERROR))
    return out


AUDIT_CHECKS = {
    "audit.pytest-baseline": check_pytest_baseline,
    "audit.vu-numbers": check_vu_numbers,
    "audit.next-pointers": check_next_pointers,
    "audit.fr-numbers": check_fr_numbers,
    "audit.dq-numbers": check_dq_numbers,
    "audit.traceability": check_traceability,
    "audit.doc-version": check_doc_version,
    "audit.cli-commands": check_cli_commands,
}


# ─── ChromaDB 읽기 전용 조회 (FR38.15 · DQ-55) ───────────────────────────────


def _read_chroma_ids(chroma_dir):
    """`chroma.sqlite3`의 `video_id` 집합과 청크 수. 실패는 **`None`** (호출부가 INFO 건너뜀).

    `chromadb.PersistentClient`를 쓰지 않는 이유: **디렉터리·스키마를 쓴다.**
    `KLIndexer._get_client()`도 `mkdir(parents=True, exist_ok=True)`를 하므로 평소 경로로
    점검하면 `chroma/`가 없는 채널에 빈 디렉터리가 생긴다 — 읽기 전용이 코드 수준에서 깨진다.
    """
    path = Path(chroma_dir) / "chroma.sqlite3"
    if not path.exists():
        return None
    con = None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT string_value, COUNT(*) FROM embedding_metadata "
            "WHERE key = 'video_id' GROUP BY string_value").fetchall()
        ids = {r[0] for r in rows if r[0]}
        chunks = sum(int(r[1] or 0) for r in rows)
        return ids, chunks
    except Exception:
        # 테이블·컬럼이 기대와 다르면 오류가 아니라 "건너뜀" — 라이브러리 업그레이드가
        # 감사 실패로 나타나면 도구가 신뢰를 잃는다 (FR38.15)
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


# ─── DoctorContext (FR38.13~38.15) ───────────────────────────────────────────

SUBTITLED = ("manual", "auto", "whisper")       # 자막 보유 판정 (DQ-18)
_LOG_COLUMNS = ("video_id", "upload_date", "title", "action", "sub_type", "status", "basename")
# 열거형·불리언 성격 필드는 distinct 1이 정상이라 `meta-fields` ⓐ에서 면제한다
_ENUM_META_FIELDS = ("content_type", "sub_type")


def _key(name: str) -> str:
    return unicodedata.normalize("NFC", str(name)).casefold()


class ChannelData:
    __slots__ = ("name", "dir", "state", "metas", "txts", "log_rows", "log_header",
                 "log_bom_offsets", "chroma", "chroma_present")

    def __init__(self, name, path):
        self.name = name
        self.dir = path
        self.state = {}
        self.metas = {}          # basename → dict
        self.txts = set()        # basename
        self.log_rows = []
        self.log_header = None
        self.log_bom_offsets = []
        self.chroma = None       # (ids, chunks) | None
        self.chroma_present = False


class DoctorContext:
    """입력을 **한 번만 읽어** 검사들이 공유한다. 파일을 두 번 읽으면 전수 예산이 무너진다."""

    def __init__(self, channel=None, now=None, cookie_grace_days=7):
        self.now = now or datetime.datetime.now()
        self.cookie_grace_days = cookie_grace_days
        self.output = config.OUTPUT_BASE
        if not self.output.is_dir():
            # `output/`이 없으면 doctor는 아무것도 못 한다 → "이상 없음"이라 말하지 않는다
            raise CheckFailure(f"output/ 이 없다: {self.output} (doctor는 실데이터가 전부다)")

        from channel_registry import ChannelRegistry
        try:
            self.reg = ChannelRegistry()
            self.registered = dict(self.reg.list() or {})
        except Exception as exc:
            raise CheckFailure(f"channels.yaml 적재 실패: {exc}")

        self.only = channel
        if channel and channel not in self.registered:
            raise CheckFailure(f"등록되지 않은 채널: {channel}")

        # 디스크에서 본 채널형 디렉터리 (등록 여부와 무관) — orphan 판정용
        self.disk_dirs = self._walk_channel_dirs()

        names = [channel] if channel else list(self.registered)
        self.channels = []
        for name in names:
            try:
                path = config.channel_dir(name)
            except ValueError:
                continue                       # 이름이 경로로 쓸 수 없는 값 → registry-paths 소관
            cd = ChannelData(name, path)
            self._load_channel(cd)
            self.channels.append(cd)

        # 스케줄러 — 판정은 `load_state()` 재사용, 열거 위반은 **원본 파일**로만 보인다
        # (`_sanitize`가 잘못된 값을 조용히 교정하므로 sanitize된 값만 보면 영원히 못 잡는다)
        import scheduler
        self.sched = scheduler.load_state()
        self.sched_raw = {}
        self.sched_file = scheduler.state_file()
        if self.sched_file.exists():
            try:
                raw = json.loads(self.sched_file.read_text(encoding="utf-8"))
                self.sched_raw = raw if isinstance(raw, dict) else {}
            except Exception as exc:
                raise CheckFailure(f".scheduler.json 파싱 실패: {exc}")
        self.sched_consts = {
            "intervals": scheduler.INTERVAL_CHOICES,
            "budget": (scheduler.BUDGET_MIN, scheduler.BUDGET_MAX),
            "skip_max": scheduler.SKIP_CYCLES_MAX,
        }

        # 쿠키 — **반드시 `cookie_health.get_status()` 를 경유한다** (FR19.3의 자동 해제가
        # 적용된 값이다). 상태 파일의 `invalid: true`를 직접 읽으면 이미 해제된 경고를
        # 48일 방치로 오탐한다 — DQ-53이 경고한 바로 그 실패다.
        import cookie_health
        self.cookie = cookie_health.get_status()

    # ── 적재 ────────────────────────────────────────────────────────────────
    def _walk_channel_dirs(self) -> list:
        import folder_ops
        found = []
        try:
            tops = sorted(p for p in self.output.iterdir() if p.is_dir())
        except OSError as exc:
            raise CheckFailure(f"output/ 열거 실패: {exc}")
        for top in tops:
            if folder_ops.is_channel_like_dir(top):
                found.append(top)
                continue
            try:
                subs = sorted(p for p in top.iterdir() if p.is_dir())
            except OSError:
                continue
            for sub in subs:
                if folder_ops.is_channel_like_dir(sub):
                    found.append(sub)
        return found

    def _load_channel(self, cd: ChannelData):
        sp = cd.dir / "state.json"
        if sp.exists():
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                cd.state = data if isinstance(data, dict) else {}
            except Exception as exc:
                raise CheckFailure(f"{sp} 파싱 실패: {exc}")
        meta_dir = cd.dir / "meta"
        if meta_dir.is_dir():
            for p in sorted(meta_dir.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise CheckFailure(f"{p} 파싱 실패: {exc}")
                cd.metas[p.stem] = d if isinstance(d, dict) else {}
        txt_dir = cd.dir / "txt"
        if txt_dir.is_dir():
            cd.txts = {p.stem for p in txt_dir.glob("*.txt")}
        self._load_log(cd)
        chroma_dir = cd.dir / "chroma"
        cd.chroma_present = (chroma_dir / "chroma.sqlite3").exists()
        cd.chroma = _read_chroma_ids(chroma_dir)

    def _load_log(self, cd: ChannelData):
        path = cd.dir / "extract_log.csv"
        if not path.exists():
            return
        raw = path.read_bytes()
        # 선두 BOM은 `extractor.py`가 `encoding="utf-8-sig"`로 **의도적으로** 쓴다(엑셀 호환).
        # 중간에 나타난 BOM만 이상 신호다 → 위치를 기록해 둔다.
        offsets = []
        start = 0
        while True:
            i = raw.find(b"\xef\xbb\xbf", start)
            if i < 0:
                break
            offsets.append(i)
            start = i + 3
        cd.log_bom_offsets = [o for o in offsets if o != 0]
        text = raw.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        if rows:
            cd.log_header = rows[0]
            cd.log_rows = rows[1:]


# ─── doctor 검사 ①  state ↔ 파일 (FR38.13) ───────────────────────────────────


def check_state_files(ctx: DoctorContext) -> list:
    """자막 보유 레코드는 `txt/<basename>.txt`·`meta/<basename>.json`이 있어야 한다.
    대응 레코드 없는 `txt/` 파일도 본다 — 양방향 불일치 = 오류.

    실측 0건이지만 넣는다: 이 불일치는 대시보드에서 "빈 채널"·전량 재추출로 나타나는
    사고급 증상이고(FR35 런북 F-3 계열) **발생하면 반드시 알아야** 한다.
    """
    out = []
    for cd in ctx.channels:
        known = set()
        for vid, rec in (cd.state or {}).items():
            if not isinstance(rec, dict) or rec.get("sub_type") not in SUBTITLED:
                continue
            base = rec.get("basename") or ""
            if not base:
                out.append(Finding("doctor.state-files", f"{cd.name}/{vid}",
                                   "자막 보유 레코드에 basename이 없다", Severity.ERROR))
                continue
            known.add(base)
            if base not in cd.txts:
                out.append(Finding("doctor.state-files", f"{cd.name}/{vid}",
                                   f"레코드는 있는데 txt가 없다: txt/{base}.txt",
                                   Severity.ERROR))
            if base not in cd.metas:
                out.append(Finding("doctor.state-files", f"{cd.name}/{vid}",
                                   f"레코드는 있는데 meta가 없다: meta/{base}.json",
                                   Severity.ERROR))
        for base in sorted(cd.txts - known):
            out.append(Finding("doctor.state-files", f"{cd.name}/txt/{base}",
                               "txt 파일에 대응하는 state 레코드가 없다", Severity.ERROR))
    return _capped("doctor.state-files", out)


# ─── doctor 검사 ②  basename 충돌 (FR38.13) ──────────────────────────────────


def check_basename_collision(ctx: DoctorContext) -> list:
    """두 `video_id`가 같은 basename을 공유하면 srt·txt·meta가 **조용히 덮어써진다** —
    파일 수는 맞아 보이므로 다른 검사로는 잡히지 않는다.
    """
    out = []
    for cd in ctx.channels:
        owners = {}
        for vid, rec in (cd.state or {}).items():
            if not isinstance(rec, dict):
                continue
            base = rec.get("basename") or ""
            if base:
                owners.setdefault(base, []).append(vid)
        for base, vids in sorted(owners.items()):
            if len(vids) > 1:
                out.append(Finding("doctor.basename-collision", f"{cd.name}/{base}",
                                   "두 영상이 같은 basename을 쓴다(파일이 덮어써진다)",
                                   Severity.ERROR, {"video_ids": ",".join(sorted(vids))}))
    return _capped("doctor.basename-collision", out)


# ─── doctor 검사 ③  미등록 잔존 폴더 (FR38.13) ───────────────────────────────


def check_orphan_dirs(ctx: DoctorContext) -> list:
    """`channels.yaml` 미등록 채널형 디렉터리 — 지울지 등록할지는 사람 판단이라 **경고**."""
    if ctx.only:
        return []                       # 채널 한정 모드에서는 전역 판정을 하지 않는다
    known = {_key(n) for n in ctx.registered}
    out = []
    for path in ctx.disk_dirs:
        if _key(path.name) in known:
            continue
        rel = path.relative_to(ctx.output).as_posix()
        out.append(Finding("doctor.orphan-dirs", f"output/{rel}",
                           "channels.yaml에 없는 채널형 폴더가 남아 있다", Severity.WARN))
    return _capped("doctor.orphan-dirs", out)


# ─── doctor 검사 ④  레지스트리 경로 (FR38.13) ────────────────────────────────


def check_registry_paths(ctx: DoctorContext) -> list:
    """`group`이 함의하는 경로(`config.channel_dir`) ↔ 디렉터리 실제 위치.

    그룹 지정 채널의 폴더가 평면 위치에 남아 있으면 대시보드에 **빈 채널로 보이고
    전량 재추출**로 이어진다(FR35 런북에 명시된 사고). 미추출(디렉터리 없음)은 정보.
    """
    out = []
    by_key = {}
    for path in ctx.disk_dirs:
        by_key.setdefault(_key(path.name), []).append(path)
    for cd in ctx.channels:
        if cd.dir.is_dir():
            continue
        elsewhere = [p for p in by_key.get(_key(cd.name), []) if p != cd.dir]
        if elsewhere:
            rels = ", ".join(p.relative_to(ctx.output).as_posix() for p in elsewhere)
            out.append(Finding("doctor.registry-paths", cd.name,
                               f"폴더가 설정이 함의하는 위치에 없다 — 실제: output/{rels}",
                               Severity.ERROR,
                               {"기대": cd.dir.relative_to(ctx.output).as_posix()}))
        else:
            out.append(Finding("doctor.registry-paths", cd.name,
                               "등록됐지만 아직 추출된 폴더가 없다", Severity.INFO))
    return _capped("doctor.registry-paths", out)


# ─── doctor 검사 ⑤  인덱스 커버리지 (FR38.13 · DQ-55) ────────────────────────


def check_index_coverage(ctx: DoctorContext) -> list:
    """자막 보유 영상 집합 ↔ ChromaDB `video_id` 집합.

    자막은 있으니 라이브러리에는 보이고 **검색에서만 조용히 빠진다** — 실측으로
    아무도 모르던 미인덱싱을 잡아낸 검사다. sqlite 스키마 상이는 오류가 아니라 건너뜀(정보).
    """
    out = []
    for cd in ctx.channels:
        subs = {vid for vid, rec in (cd.state or {}).items()
                if isinstance(rec, dict) and rec.get("sub_type") in SUBTITLED}
        if cd.chroma is None and cd.chroma_present:
            # 파일은 있는데 읽지 못했다 = 스키마 상이 → 건너뜀
            out.append(Finding("doctor.index-coverage", cd.name,
                               "chroma.sqlite3를 읽지 못해 이 채널을 건너뛴다(스키마 상이)",
                               Severity.INFO, skip=True))
            continue
        indexed, chunks = (cd.chroma if cd.chroma else (set(), 0))
        missing = subs - indexed
        if missing:
            why = "chroma/ 미생성" if not cd.chroma_present else "인덱싱 누락"
            out.append(Finding("doctor.index-coverage", cd.name,
                               f"자막 {len(missing)}편이 ChromaDB에 없다 ({why})",
                               Severity.WARN,
                               {"subtitled": len(subs), "indexed": len(indexed),
                                "missing": len(missing),
                                "ids": ",".join(sorted(missing)[:5])}))
        orphan = indexed - subs
        if orphan:
            out.append(Finding("doctor.index-coverage", f"{cd.name}/index-orphan",
                               f"인덱스에만 있는 영상 {len(orphan)}편(자막 레코드 없음)",
                               Severity.WARN,
                               {"chunks": chunks, "ids": ",".join(sorted(orphan)[:5])}))
    return _capped("doctor.index-coverage", out)


# ─── doctor 검사 ⑥  meta 필드 (FR38.13 · DQ-56) ──────────────────────────────


def check_meta_fields(ctx: DoctorContext) -> list:
    """meta 필드의 **일반화된 이상 신호** — 특정 필드를 하드코딩하지 않는다.

    ⓐ 코퍼스 전체에서 "값이 있는 레코드가 있는데 distinct가 1" 또는 **전량 빈 값** = 경고
      (기록 경로 고장·상수 오염 — `note`가 죽어 있던 모양이 이것이고, `tickers` 오탐을
      필드 하드코딩 없이 잡는 형태로 일반화한 것이다).
    ⓑ 형식 계약 위반 = 오류. 단 `upload_date == "00000000"`은 FR2.6·DQ-12 문서화 제약이라 정보.
    """
    out = []
    total = 0
    seen = {}            # field → [present, {값}, 전체 등장 수]
    # ⓐ "전 코퍼스가 같은 모양"은 **채널을 가로지를 때만** 의미가 있다. 한 채널만 보면
    # `channel`·`categories`처럼 채널 안에서 균일한 것이 정상인 필드가 전부 걸린다(실측:
    # `doctor 호두감자`에서 `categories/single-value`·`tags/all-empty` = 오탐 2건).
    corpus_signal = len(ctx.channels) >= 2
    for cd in ctx.channels:
        for base, meta in cd.metas.items():
            total += 1
            for k, v in meta.items():
                rec = seen.setdefault(k, [0, set(), 0])
                rec[2] += 1
                if v in (None, "", [], {}, ()):
                    continue
                rec[0] += 1
                try:
                    rec[1].add(json.dumps(v, ensure_ascii=False, sort_keys=True))
                except (TypeError, ValueError):
                    pass
            out += _meta_format_findings(cd, base, meta)

    if not corpus_signal:
        out.append(Finding("doctor.meta-fields", WHOLE_CHECK,
                           "채널 1개 범위에서는 코퍼스 신호(distinct 1·전량 빈 값)를 건너뛴다"
                           " — 채널 안의 균일함은 정상이다", Severity.INFO, skip=True))
    if total and corpus_signal:
        for field in sorted(seen):
            present, values, appears = seen[field]
            if field in _ENUM_META_FIELDS:
                continue                         # 열거형은 distinct 1이 정상 → 면제
            if appears < total:
                continue                         # 일부 레코드에만 있는 필드는 판정하지 않는다
            if present == 0:
                out.append(Finding("doctor.meta-fields", f"{field}/all-empty",
                                   "모든 레코드에 있지만 값이 한 번도 채워지지 않았다",
                                   Severity.WARN, {"records": total, "with_value": 0}))
            elif len(values) == 1:
                out.append(Finding("doctor.meta-fields", f"{field}/single-value",
                                   "값이 있는데 distinct가 1이다(기록 경로 고장·상수 오염 신호)",
                                   Severity.WARN, {"records": total, "with_value": present}))

    # `upload_date == "00000000"` — 문서화된 제약이라 정보 1줄로 집계한다 (FR2.6·DQ-12)
    zeros = 0
    for cd in ctx.channels:
        for rec in (cd.state or {}).values():
            if isinstance(rec, dict) and str(rec.get("upload_date")) == "00000000":
                zeros += 1
        for meta in cd.metas.values():
            if str(meta.get("upload_date")) == "00000000":
                zeros += 1
    if zeros:
        out.append(Finding("doctor.meta-fields", "upload_date/00000000",
                           "flat 스캔이 날짜를 주지 않은 레코드(FR2.6·DQ-12 문서화 제약)",
                           Severity.INFO, {"records": zeros}))
    return _capped("doctor.meta-fields", out)


def _meta_format_findings(cd, base, meta) -> list:
    out = []
    target = f"{cd.name}/{base}"
    ud = meta.get("upload_date")
    if ud is not None and str(ud) != "00000000" and not re.fullmatch(r"\d{8}", str(ud)):
        out.append(Finding("doctor.meta-fields", target,
                           f"upload_date가 8자리 숫자가 아니다: {ud!r}", Severity.ERROR))
    du = meta.get("duration")
    if du is not None:
        if isinstance(du, bool) or not isinstance(du, (int, float)):
            out.append(Finding("doctor.meta-fields", target,
                               f"duration이 수치가 아니다: {du!r}", Severity.ERROR))
        elif du < 0:
            out.append(Finding("doctor.meta-fields", target,
                               f"duration이 음수다: {du}", Severity.ERROR))
    ch = meta.get("chapters")
    if isinstance(ch, list):
        for i, item in enumerate(ch):
            if not isinstance(item, dict) or "start" not in item or "title" not in item:
                out.append(Finding("doctor.meta-fields", target,
                                   f"chapters[{i}]에 start·title이 없다", Severity.ERROR))
                break
    return out


# ─── doctor 검사 ⑦  감지 규칙 화석 (FR38.13 · DQ-56) ─────────────────────────


def check_detector_fossils(ctx: DoctorContext) -> list:
    """`extract_log.csv`의 `error:` 사유를 **현행 판정 규칙으로 재판정**한다.

    "`sub_type` 분포 급변 감지"를 대체하는 검사다 — 로그에 timestamp 열이 없고
    `members_only` 레코드의 `extracted_at`이 비어 있어 시계열 판정이 애초에 불가능하다.
    대신 **같은 데이터 안의 모순**을 본다: 현행 `video_access.is_members_message()`가
    참이면 그 시점의 감지 규칙이 그것을 놓친 것이 **확정**된다(기준선·스냅샷 불필요).
    """
    import video_access
    out = []
    c429 = 0
    for cd in ctx.channels:
        hdr = cd.log_header or list(_LOG_COLUMNS)
        try:
            si = hdr.index("status")
            vi = hdr.index("video_id")
        except ValueError:
            continue
        for row in cd.log_rows:
            if len(row) <= max(si, vi):
                continue
            status = row[si] or ""
            if not status.startswith("error:"):
                continue
            msg = status[len("error:"):]
            if "429" in msg:
                c429 += 1
                continue                        # 429는 멤버십과 무관 — 호출부가 먼저 판정한다
            if video_access.is_members_message(msg) or video_access.is_members_availability(msg):
                out.append(Finding("doctor.detector-fossils", f"{cd.name}/{row[vi]}",
                                   "오류로 기록됐지만 현행 규칙은 멤버십으로 판정한다"
                                   "(감지 규칙이 놓친 화석)", Severity.ERROR,
                                   {"reason": msg[:80]}))
    if c429:
        out.append(Finding("doctor.detector-fossils", "error:429",
                           "로그에 남은 429 오류(차단 이력)", Severity.INFO, {"rows": c429}))
    return _capped("doctor.detector-fossils", out)


# ─── doctor 검사 ⑧  extract_log 위생 (FR38.13) ───────────────────────────────


def check_extract_log(ctx: DoctorContext) -> list:
    """완전 동일 행 중복 · 헤더 열 수 불일치 · 중간 BOM.

    **중복 행은 정보다**(경고가 아니다). `extract_log.csv`는 append-only 시도 기록이고
    같은 영상을 다시 돌리면 같은 행이 또 쌓이는 것이 **설계상 정상 귀결**이다 —
    기록을 지우는 것 말고는 해소 수단이 없으므로 경고로 두면 **영원히 사라지지 않는**
    상시 경고가 된다(DQ-53: 상시 경고가 깔린 도구는 아무도 보지 않는다).
    구조 손상(헤더 계약 위반·열 수 불일치 = 오류, 중간 BOM = 경고)은 **그대로 유지**한다 —
    그것은 파싱·append 경로가 깨졌다는 뜻이고 사람의 조치가 실제로 가능하다.

    선두 BOM은 `extractor.py`가 `utf-8-sig`로 의도적으로 쓰는 것이라 **정상 파싱하고 침묵**한다
    (98/98 파일에 있다 — 경고로 올리면 그것만으로 도구가 무시된다, FR38.8).
    """
    out = []
    for cd in ctx.channels:
        if cd.log_header is None:
            continue
        if [c.strip() for c in cd.log_header] != list(_LOG_COLUMNS):
            out.append(Finding("doctor.extract-log", f"{cd.name}/header",
                               "헤더 열 구성이 계약과 다르다", Severity.ERROR,
                               {"header": ",".join(cd.log_header)}))
        bad = sum(1 for r in cd.log_rows if len(r) != len(cd.log_header))
        if bad:
            out.append(Finding("doctor.extract-log", f"{cd.name}/columns",
                               "헤더와 열 수가 다른 행이 있다", Severity.ERROR,
                               {"rows": bad}))
        counts = {}
        for r in cd.log_rows:
            k = tuple(r)
            counts[k] = counts.get(k, 0) + 1
        dup = sum(v - 1 for v in counts.values() if v > 1)
        if dup:
            # 심각도 = 정보. 재실행이 같은 행을 또 남기는 것은 append-only 설계의 정상
            # 귀결이다(위 docstring). 건수는 **세어서 보여준다** — 침묵시키지는 않는다.
            out.append(Finding("doctor.extract-log", f"{cd.name}/duplicate-rows",
                               "완전 동일한 행이 누적돼 있다(append-only 시도 기록 — 설계상 정상)",
                               Severity.INFO,
                               {"duplicate_rows": dup, "total_rows": len(cd.log_rows)}))
        if cd.log_bom_offsets:
            out.append(Finding("doctor.extract-log", f"{cd.name}/bom",
                               "파일 중간에 BOM이 있다(append 경로 손상)", Severity.WARN,
                               {"offsets": ",".join(str(o) for o in cd.log_bom_offsets[:5])}))
    return _capped("doctor.extract-log", out)


# ─── doctor 검사 ⑨  스케줄러 (FR38.13) ───────────────────────────────────────


def check_scheduler(ctx: DoctorContext) -> list:
    """FR37이 켜지면 **아무도 안 보는 사이 멈춰 있을 수 있다.**

    적체·정지·백오프 = 경고 · 열거 밖 값 = 오류. 열거 위반은 `load_state()`의
    `_sanitize`가 조용히 교정하므로 **원본 파일 값**과 대조해야만 보인다.
    """
    out = []
    st, raw = ctx.sched, ctx.sched_raw
    intervals = ctx.sched_consts["intervals"]
    bmin, bmax = ctx.sched_consts["budget"]

    if "interval_days" in raw and raw.get("interval_days") not in intervals:
        out.append(Finding("doctor.scheduler", "interval_days",
                           f"주기가 열거 밖이다(허용 {list(intervals)}) — 실행은 "
                           f"{st['interval_days']}일로 교정되고 있다", Severity.ERROR,
                           {"file": raw.get("interval_days")}))
    budget = raw.get("max_videos_per_cycle")
    if "max_videos_per_cycle" in raw:
        ok = isinstance(budget, int) and not isinstance(budget, bool) and bmin <= budget <= bmax
        if not ok:
            out.append(Finding("doctor.scheduler", "max_videos_per_cycle",
                               f"주기 예산이 범위 밖이다({bmin}~{bmax}) — 실행은 "
                               f"{st['max_videos_per_cycle']}로 교정되고 있다", Severity.ERROR,
                               {"file": budget}))

    if not st.get("enabled"):
        return out                       # 꺼져 있으면 적체·백오프는 정상 상태다

    if st.get("paused_reason"):
        out.append(Finding("doctor.scheduler", "paused_reason",
                           f"스케줄이 정지 상태다: {st['paused_reason']}", Severity.WARN))
    skip = int(st.get("skip_cycles") or 0)
    if skip > 0:
        out.append(Finding("doctor.scheduler", "skip_cycles",
                           "429 백오프로 주기를 건너뛰는 중이다", Severity.WARN,
                           {"skip_cycles": skip}))
    last = st.get("last_run_at")
    if last:
        try:
            last_dt = datetime.datetime.fromisoformat(str(last))
        except ValueError:
            last_dt = None
        if last_dt is not None:
            limit = int(st.get("interval_days") or 3) * (skip + 2)
            age = (ctx.now - last_dt).days
            if age > limit:
                out.append(Finding("doctor.scheduler", "last_run_at",
                                   "켜져 있는데 주기가 오래 돌지 않았다(적체)", Severity.WARN,
                                   {"days": age, "limit": limit}))
    return out


# ─── doctor 검사 ⑩  쿠키 상태 (FR38.13 · FR19.3) ─────────────────────────────


def check_cookie_status(ctx: DoctorContext) -> list:
    """무효 경고가 N일(기본 7) 이상 방치됐는지 — 판정은 **`cookie_health.get_status()`**.

    상태 파일의 `invalid: true`를 직접 읽으면 안 된다: `get_status()`는 쿠키를 경고 이후에
    갱신했으면 경고를 **자동 해제**한다(FR19.3). 날것의 기록을 읽으면 이미 해소된 경고를
    "48일 방치"로 오탐하고, 그 한 건이 도구 전체의 신뢰를 깎는다(DQ-53).
    쿠키 경고는 FR37.11에서 스케줄을 영구 정지시키는 조건이라 무인 운영의 가장 조용한 실패 경로다.
    """
    st = ctx.cookie
    if not st.get("warning"):
        return []
    detected = st.get("detected_at")
    try:
        dt = datetime.datetime.fromisoformat(str(detected))
    except (TypeError, ValueError):
        return [Finding("doctor.cookie-status", "cookie",
                        "쿠키 무효 경고가 있다(감지 시각을 읽을 수 없다)", Severity.WARN)]
    days = (ctx.now - dt).days
    if days < ctx.cookie_grace_days:
        return []
    return [Finding("doctor.cookie-status", "cookie",
                    f"쿠키 무효 경고가 {days}일 방치됐다(FR37.11에서 스케줄 정지 조건)",
                    Severity.WARN, {"detected_at": detected, "days": days,
                                    "source": st.get("source")})]


DOCTOR_CHECKS = {
    "doctor.state-files": check_state_files,
    "doctor.basename-collision": check_basename_collision,
    "doctor.orphan-dirs": check_orphan_dirs,
    "doctor.registry-paths": check_registry_paths,
    "doctor.index-coverage": check_index_coverage,
    "doctor.meta-fields": check_meta_fields,
    "doctor.detector-fossils": check_detector_fossils,
    "doctor.extract-log": check_extract_log,
    "doctor.scheduler": check_scheduler,
    "doctor.cookie-status": check_cookie_status,
}


# ─── 실행 (FR38.5~38.6) ──────────────────────────────────────────────────────


def _select(registry: dict, only=None) -> list:
    if not only:
        return list(registry)
    unknown = [c for c in only if c not in registry]
    if unknown:
        raise CheckFailure(f"알 수 없는 검사 ID: {', '.join(unknown)} "
                           f"(가능: {', '.join(sorted(registry))})")
    return [c for c in registry if c in set(only)]


def run(command: str, ctx, only=None, strict: bool = False, waivers=None, started=None):
    """검사 실행 → 예외 적용 → 종료코드. 사람용·JSON 출력이 **같은 발견 집합**을 쓴다.

    `started`는 **입력 적재를 포함한** 시작 시각이다 — 비용의 대부분이 컨텍스트 적재이므로
    검사 루프만 재면 성능 상한(audit 1초·doctor 10초, FR38.12·38.14)을 감시할 수 없다.
    """
    registry = AUDIT_CHECKS if command == "audit" else DOCTOR_CHECKS
    names = _select(registry, only)
    started = started or datetime.datetime.now()
    findings, skipped = [], []
    fully_run = set(names)
    for name in names:
        produced = registry[name](ctx) or []
        for f in produced:
            if f.skip:
                skipped.append({"check": f.check, "target": f.target, "why": f.message})
                if f.target == WHOLE_CHECK:
                    fully_run.discard(name)
        findings += produced
    if waivers is None:
        waivers = load_waivers()
    # 부분 범위 실행(`doctor <채널>`)은 **예외의 수명을 판정할 근거가 없다** —
    # 다른 채널의 발견을 보지 못한 채 "대응 발견이 없다"고 하면 그것이 오탐이다(FR38.8).
    # 채널 한정 실행이 `waiver.stale` 경고를 만들면 전수 실행에서 0인 기준선이 깨진다.
    if getattr(ctx, "only", None):
        fully_run = set()
    kept, waived, stale = apply_waivers(findings, waivers, checks_run=fully_run)
    kept += stale
    elapsed = (datetime.datetime.now() - started).total_seconds()
    code = exit_code(kept, strict)
    return {"command": command, "findings": kept, "waived": waived, "stale": stale,
            "checks_run": names, "skipped": skipped, "elapsed": elapsed, "exit_code": code}

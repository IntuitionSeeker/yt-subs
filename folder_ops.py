"""폴더(그룹) 디렉터리 — 충돌 검사·이동·마이그레이션. FR35.6~35.13.

`ChannelRegistry.set_group`은 yaml 전용 순수 함수로 남기고(V-U12 계약 보존)
디스크 부작용은 전부 여기로 모은다. 의존은 `config`·`channel_registry`뿐이다(순환 없음).

핵심 원칙 (DQ-35):
  · 이동은 **`os.rename` 단일 호출**만 — `shutil.move` 금지(FS 경계에서 복사+삭제로
    떨어져 중단 시 자막이 유실된다). EXDEV면 복사 폴백 없이 실패로 끝낸다.
  · 순서는 **디스크 먼저 → yaml 나중**, yaml 실패 시 **역방향 rename 보상 롤백**.
  · 정리는 **빈 디렉터리 `rmdir`만** — `rmtree`는 이 파일에 존재하지 않는다.
"""
import os
import json
import time
import errno
import logging
import datetime
import unicodedata
from pathlib import Path

import config
from channel_registry import ChannelRegistry

log = logging.getLogger("folder_ops")

STALE_LOCK_SEC = 6 * 3600          # 6시간 초과 락 = stale (FR35.10)


class ConflictError(Exception):
    """이름공간 충돌·경합 — HTTP 409. FR35.6"""


class MoveError(Exception):
    """디렉터리 이동 실패 — HTTP 500. FR35.7"""


# ─── 경로 헬퍼 (OUTPUT_BASE는 테스트에서 monkeypatch되므로 항상 함수로 읽는다) ──
def _lock_path() -> Path:
    return config.OUTPUT_BASE / ".migration.lock"


def _journal_path() -> Path:
    return config.OUTPUT_BASE / ".migration_journal.json"


def _journal_done_path() -> Path:
    return config.OUTPUT_BASE / ".migration_journal.done.json"


def _key(name: str) -> str:
    """이름공간 비교 키 — NFC + casefold (U-2 확정).

    macOS APFS는 대소문자·정규화 비민감이라 `abc`/`ABC`, NFC/NFD가 **같은 디렉터리**다.
    yaml에는 둘인데 디스크는 하나인 상태를 막으려면 비교를 casefold로 해야 한다.
    저장은 언제나 원문(NFC)이다. 케이스 구분 FS로 옮겨가도 더 엄격할 뿐 틀리지 않는다.
    """
    return unicodedata.normalize("NFC", name or "").casefold()


def _dev(path) -> int:
    """`st_dev` 조회 — 테스트가 EXDEV를 주입할 수 있도록 분리한다."""
    return os.stat(path).st_dev


def _nearest_existing(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _rmdir_if_empty(path: Path):
    """비어 있을 때만 제거. OUTPUT_BASE 자신과 그 바깥은 절대 건드리지 않는다 (U-3)."""
    path = Path(path)
    base = config.OUTPUT_BASE
    try:
        if path == base or base not in path.parents:
            return
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            log.info(f"🧹 빈 폴더 제거: {path}")
    except OSError:
        pass


def prune_empty_group_dir(path) -> bool:
    """
    채널 폴더 삭제(FR21.2 `purge`) 후 **비어 있을 때만** 그룹 폴더를 제거한다 (U-3).

    `rmtree`는 쓰지 않는다 — 비어 있지 않으면 그대로 남긴다.
    """
    path = Path(path)
    if path == config.OUTPUT_BASE:
        return False
    before = path.is_dir()
    _rmdir_if_empty(path)
    return before and not path.exists()


def _iter_toplevel():
    try:
        return [p for p in config.OUTPUT_BASE.iterdir() if p.is_dir()]
    except OSError:
        return []


def _find_toplevel(name: str):
    """`output/` 최상위에서 casefold 일치하는 디렉터리. 없으면 None."""
    k = _key(name)
    for p in _iter_toplevel():
        if _key(p.name) == k:
            return p
    return None


# ─── 이름공간 (FR35.6 · DQ-34) ───────────────────────────────────────────────
def is_channel_like_dir(path) -> bool:
    """채널 폴더로 보이는가 — 등록 해제 후 남은 잔존 폴더 식별용. FR35.6ⓐ"""
    p = Path(path)
    return any((p / n).exists() for n in ("state.json", "srt", "meta"))


def _namespace_view(reg=None):
    """(그룹 키→원문, 미지정 채널 키→원문) — 레지스트리 기준."""
    reg = reg or ChannelRegistry()
    groups, ungrouped = {}, {}
    for name, cfg in (reg.list() or {}).items():
        group = ((cfg or {}).get("group") or "").strip()
        if group:
            groups.setdefault(_key(group), group)
        else:
            ungrouped.setdefault(_key(name), name)
    return groups, ungrouped


def check_namespace(new_group: str = None, new_channel: str = None, reg=None):
    """
    `output/` 최상위 이름공간 유일성 검사. FR35.6 ⓐ~ⓓ (DQ-34)

    · `new_group`   — ⓐ 그룹 지정·변경: 미지정 채널명과 충돌하거나 동명 최상위
                      디렉터리가 **채널형**이면 거부
    · `new_channel` — ⓑ 그룹 해제 / ⓒ 채널 개명·신규 등록: 기존 그룹명과 충돌하거나
                      `output/<채널>/`가 이미 존재하면 거부
    · ⓓ `output/G/G/`(그룹 G 안의 동명 채널)는 중첩 레벨이 달라 **허용**된다 —
      이 함수는 최상위만 본다.
    위반 시 `ConflictError`(→ HTTP 409). 판정은 전부 casefold 기준이다.
    """
    groups, ungrouped = _namespace_view(reg)
    if new_group:
        k = _key(new_group)
        if k in ungrouped:
            raise ConflictError(
                f"'{new_group}'는 이미 폴더 미지정 채널의 이름입니다 — "
                f"같은 이름의 폴더를 만들면 output/{ungrouped[k]}/ 가 채널이면서 폴더가 됩니다.")
        found = _find_toplevel(new_group)
        if found is not None and is_channel_like_dir(found):
            raise ConflictError(
                f"output/{found.name}/ 는 채널 폴더입니다(state.json·srt·meta 보유) — "
                f"'{new_group}' 폴더를 만들 수 없습니다.")
    if new_channel:
        k = _key(new_channel)
        if k in groups:
            raise ConflictError(
                f"'{new_channel}'는 이미 폴더 이름입니다 — output/ 최상위 채널 이름으로 쓸 수 없습니다.")
        found = _find_toplevel(new_channel)
        if found is not None:
            raise ConflictError(f"output/{found.name}/ 가 이미 존재합니다.")


# ─── 이동 (FR35.7 · DQ-35) ───────────────────────────────────────────────────
def _is_within(inner: Path, outer: Path) -> bool:
    try:
        return outer == inner or outer in inner.parents
    except Exception:                       # pragma: no cover - 방어적
        return False


def move_channel_dir(src, dst) -> bool:
    """
    채널 폴더 이동 — **`os.rename` 단일 호출**. FR35.7

    반환: 실제로 옮겼으면 True, 원본이 없으면(미추출 채널) False(no-op).
    거부 조건: 목적지 존재(POSIX rename은 빈 디렉터리를 무음 교체한다) ·
    자기 자신 하위/상위로의 이동 · `st_dev` 불일치(EXDEV — 복사 폴백 금지).
    """
    src, dst = Path(src), Path(dst)
    if src == dst:
        return False
    if not src.exists():
        return False                        # 미추출 채널 → no-op
    if dst.exists():
        raise ConflictError(f"목적지가 이미 존재합니다: {dst}")
    if _is_within(dst, src) or _is_within(src, dst):
        raise ConflictError(f"자기 자신의 하위/상위로는 옮길 수 없습니다: {src} → {dst}")

    anchor = _nearest_existing(dst.parent)
    try:
        if _dev(src) != _dev(anchor):
            raise MoveError(
                f"파일시스템이 달라 이동할 수 없습니다(EXDEV): {src} → {dst}. "
                f"복사 폴백은 중단 시 자막 유실 위험이 있어 하지 않습니다.")
    except OSError as exc:                  # pragma: no cover - stat 실패
        raise MoveError(f"경로 확인 실패: {exc}") from exc

    created_parent = not dst.parent.exists()
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)                 # ← 원자적 단일 호출
    except OSError as exc:
        if created_parent:
            _rmdir_if_empty(dst.parent)
        hint = " (EXDEV: 파일시스템 경계)" if exc.errno == errno.EXDEV else ""
        raise MoveError(f"이동 실패{hint}: {src} → {dst} ({exc})") from exc
    _rmdir_if_empty(src.parent)             # 비워진 원본 그룹 폴더만
    log.info(f"📁 이동: {src} → {dst}")
    return True


def _compensate(dst: Path, src: Path, cause: Exception):
    """yaml 기록 실패 시 역방향 rename 보상 롤백. 실패하면 수동 복구 명령을 남긴다."""
    try:
        src.parent.mkdir(parents=True, exist_ok=True)
        os.rename(dst, src)
        _rmdir_if_empty(dst.parent)
        log.warning(f"↩️ 보상 롤백 완료: {dst} → {src} (원인: {cause})")
    except OSError as exc:
        raise MoveError(
            f"이동 후 yaml 기록에 실패했고 롤백도 실패했습니다({cause} / {exc}). "
            f"수동 복구: mv '{dst}' '{src}'") from exc


# ─── 그룹 지정·해제 (FR35.8 · FR35.13) ───────────────────────────────────────
def set_channel_group(name: str, group: str = None, on_conflict: str = "reject") -> dict:
    """
    채널 폴더 지정/변경/해제 — 검증 → 충돌검사 → 이동 → yaml. FR35.8

    `on_conflict="skip"`이면 충돌 시 예외 대신 `{"skipped": 사유}`를 돌려준다
    (자동 폴더 지정 전용 — 배치 추출 전체를 죽이지 않는다, FR35.13).
    """
    reg = ChannelRegistry()
    current = reg.get(name)                          # 미등록 → KeyError
    cname = config.validate_path_segment(name)
    cur_group = ((current.get("group") or "")).strip()
    group = (group or "").strip()
    if group:
        group = config.validate_path_segment(group)  # ValueError → 400 (FR35.4)

    if _key(group) == _key(cur_group):               # 변화 없음
        return {"channel": name, "group": group or cur_group, "moved": False}

    try:
        if group:
            check_namespace(new_group=group, reg=reg)          # ⓐ
        else:
            check_namespace(new_channel=cname, reg=reg)        # ⓑ 해제 후 최상위 충돌
    except ConflictError as exc:
        if on_conflict == "skip":                              # FR35.13
            log.warning(f"⚠️ 폴더 자동 지정 건너뜀 — {name}: {exc}")
            return {"channel": name, "group": cur_group, "moved": False,
                    "skipped": str(exc)}
        raise

    src = config.channel_dir(name)                   # 현재 해석 경로 (yaml 기준)
    dst = (config.OUTPUT_BASE / group / cname) if group else (config.OUTPUT_BASE / cname)
    try:
        moved = move_channel_dir(src, dst)
    except ConflictError as exc:
        if on_conflict == "skip":
            log.warning(f"⚠️ 폴더 자동 지정 건너뜀 — {name}: {exc}")
            return {"channel": name, "group": cur_group, "moved": False,
                    "skipped": str(exc)}
        raise

    try:
        reg.set_group(name, group)                   # ③ yaml (순수 함수 유지)
    except Exception as exc:
        if moved:
            _compensate(dst, src, exc)
        raise
    return {"channel": name, "group": group, "moved": moved,
            "src": str(src), "dst": str(dst)}


def rename_group(old: str, new: str) -> int:
    """
    폴더(그룹) 이름 변경 — `output/<old>/` → `output/<new>/` **단일 rename** + yaml 일괄. FR35.9

    반환: 갱신된 채널 수. 그룹 폴더 안의 미등록 잔존 폴더도 같은 폴더이므로 함께 따라간다.
    """
    old = (old or "").strip()
    if not old:
        raise ValueError("이전 폴더 이름이 비어 있습니다.")
    new = config.validate_path_segment(new)          # FR35.4
    reg = ChannelRegistry()
    members = [n for n, c in (reg.list() or {}).items()
               if ((c or {}).get("group") or "").strip() == old]
    case_only = _key(old) == _key(new)               # 대소문자·정규화만 다른 변경
    if not case_only:
        check_namespace(new_group=new, reg=reg)      # ⓐ

    src = config.OUTPUT_BASE / old
    dst = config.OUTPUT_BASE / new
    moved = False
    if src.exists() and src != dst:
        if dst.exists() and not case_only:
            raise ConflictError(f"목적지가 이미 존재합니다: {dst}")
        anchor = _nearest_existing(dst.parent)
        if _dev(src) != _dev(anchor):
            raise MoveError(f"파일시스템이 달라 이동할 수 없습니다(EXDEV): {src} → {dst}")
        try:
            os.rename(src, dst)
            moved = True
        except OSError as exc:
            raise MoveError(f"폴더 이동 실패: {src} → {dst} ({exc})") from exc

    try:
        for n in members:
            reg.set_group(n, new)
    except Exception as exc:
        if moved:
            _compensate(dst, src, exc)
        raise
    log.info(f"✏️ 폴더 이름 변경: {old} → {new} (채널 {len(members)} · 이동 {moved})")
    return len(members)


# ─── 락 (FR35.10) ────────────────────────────────────────────────────────────
def is_locked() -> bool:
    """마이그레이션 락 존재 여부. mtime 6시간 초과는 stale로 보고 무시(경고)."""
    path = _lock_path()
    try:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
    except OSError:                          # pragma: no cover
        return False
    if age > STALE_LOCK_SEC:
        log.warning(f"⚠️ stale 마이그레이션 락({int(age/3600)}시간 경과) 무시 — "
                    f"제거하려면 ./yt.sh migrate-groups --unlock")
        return False
    return True


def read_lock_info() -> dict:
    """락 파일 내용(pid·started_at). 없거나 손상이면 {}. 안내 문구용."""
    try:
        return json.loads(_lock_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _lock_write():
    """락 파일 기록 (존재 여부를 보지 않는다 — 탈취 경로 공용)."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(),
                                "started_at": datetime.datetime.now().isoformat()}),
                    encoding="utf-8")


def lock():
    if is_locked():
        raise ConflictError(
            "이미 마이그레이션이 진행 중입니다(output/.migration.lock). "
            "중단된 마이그레이션이라면 `./yt.sh migrate-groups --rollback` 으로 되돌리세요"
            "(롤백은 락이 있어도 확인 후 진행됩니다). 락만 지우려면 `--unlock`.")
    _lock_write()


def unlock() -> bool:
    path = _lock_path()
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


# ─── 저널 (FR35.12) ──────────────────────────────────────────────────────────
def _read_journal_file(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")) or []
    except Exception:                        # pragma: no cover - 손상 저널
        log.error(f"⚠️ 저널을 읽을 수 없습니다: {path}")
        raise


def read_journal() -> list:
    """진행 중(pending) 저널만 읽는다 — 사전 검증 ①의 "미완료 감지"용."""
    return _read_journal_file(_journal_path())


def read_done_journal() -> list:
    """성공 완료 후 보관된 저널(`.migration_journal.done.json`). FR35.12"""
    return _read_journal_file(_journal_done_path())


def has_pending_journal() -> bool:
    return _journal_path().exists()


def _journal_append(entries: list, record: dict):
    """append + flush + fsync — 프로세스가 죽어도 어디까지 옮겼는지 디스크에 남는다."""
    entries.append(record)
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


# ─── 마이그레이션 (FR35.11~35.12 · DQ-36) ────────────────────────────────────
def plan_migration(reg=None) -> dict:
    """
    평면 `output/<C>/` → 중첩 `output/<G>/<C>/` 이동 계획. **읽기 전용**. FR35.11

    반환: `{moves, skipped, unregistered}`. 미등록 잔존 폴더·`.cookie_status.json`은
    **건드리지 않고 보고만** 한다. 이미 이동된 채널은 제외되므로 재실행이 멱등이다.
    """
    reg = reg or ChannelRegistry()
    moves, skipped = [], []
    for name, cfg in (reg.list() or {}).items():
        group = ((cfg or {}).get("group") or "").strip()
        if not group:
            skipped.append({"channel": name, "reason": "폴더 미지정 — 최상위 유지"})
            continue
        try:
            cname = config.validate_path_segment(name)
            gname = config.validate_path_segment(group)
        except ValueError as exc:
            skipped.append({"channel": name, "reason": f"이름 부적합: {exc}"})
            continue
        src = config.OUTPUT_BASE / cname
        dst = config.OUTPUT_BASE / gname / cname
        if dst.exists():
            skipped.append({"channel": name, "reason": "이미 이동됨"})
            continue
        if not src.exists():
            skipped.append({"channel": name, "reason": "출력 폴더 없음(미추출)"})
            continue
        moves.append({"channel": name, "group": group,
                      "src": str(src), "dst": str(dst)})

    known = set()
    for name, cfg in (reg.list() or {}).items():
        known.add(_key(name))
        group = ((cfg or {}).get("group") or "").strip()
        if group:
            known.add(_key(group))
    unregistered = sorted(p.name for p in _iter_toplevel()
                          if _key(p.name) not in known)
    return {"moves": moves, "skipped": skipped, "unregistered": unregistered}


def _precheck(plan: dict, reg) -> list:
    """사전 검증 ①~⑤ — 하나라도 실패하면 한 건도 옮기지 않는다. FR35.11"""
    problems = []
    if is_locked():                                                     # ①
        problems.append("마이그레이션 락이 존재합니다(output/.migration.lock) — "
                        "중단된 실행이라면 `--rollback`(락이 있어도 확인 후 진행), "
                        "락만 지우려면 `--unlock`.")
    if has_pending_journal():
        problems.append("미완료 마이그레이션 저널이 있습니다 — "
                        "먼저 `./yt.sh migrate-groups --rollback` 하세요.")
    groups = {}
    for name, cfg in (reg.list() or {}).items():                        # ②
        try:
            config.validate_path_segment(name)
        except ValueError as exc:
            problems.append(f"채널명 부적합: {name!r} — {exc}")
        group = ((cfg or {}).get("group") or "").strip()
        if group:
            try:
                config.validate_path_segment(group)
                groups.setdefault(_key(group), group)
            except ValueError as exc:
                problems.append(f"폴더명 부적합: {group!r} — {exc}")
    _, ungrouped = _namespace_view(reg)                                 # ③
    for k, group in groups.items():
        if k in ungrouped:
            problems.append(f"이름 충돌: 폴더 '{group}' ↔ 폴더 미지정 채널 '{ungrouped[k]}'")
        found = _find_toplevel(group)
        if found is not None and is_channel_like_dir(found):
            problems.append(f"이름 충돌: output/{found.name}/ 가 채널 폴더인데 폴더 '{group}'가 필요합니다.")
    base_dev = None
    try:
        base_dev = _dev(config.OUTPUT_BASE)
    except OSError:                                                     # pragma: no cover
        problems.append("output/ 을 읽을 수 없습니다.")
    for mv in plan["moves"]:
        if Path(mv["dst"]).exists():                                    # ④
            problems.append(f"목적지가 이미 존재합니다: {mv['dst']}")
        if base_dev is not None:                                        # ⑤
            try:
                if _dev(mv["src"]) != base_dev:
                    problems.append(f"파일시스템이 다릅니다(EXDEV): {mv['src']}")
            except OSError:                                             # pragma: no cover
                problems.append(f"경로를 읽을 수 없습니다: {mv['src']}")
    return problems


def apply_migration(plan: dict = None, reg=None) -> dict:
    """
    계획 실행 — 사전 검증 전부 통과 시에만 시작. 실패 시 저널 역순 자동 롤백. FR35.11~35.12

    **`channels.yaml`을 한 글자도 바꾸지 않는다** — `group` 값은 그대로이고 해석 규칙만
    달라지므로 순수 디렉터리 이동이며, 그래서 롤백이 디렉터리 되돌리기만으로 완결된다.
    """
    reg = reg or ChannelRegistry()
    plan = plan or plan_migration(reg)
    problems = _precheck(plan, reg)
    if problems:
        raise ConflictError("사전 검증 실패 — 아무것도 옮기지 않았습니다:\n  - "
                            + "\n  - ".join(problems))
    lock()
    entries = []
    try:
        for mv in plan["moves"]:
            move_channel_dir(Path(mv["src"]), Path(mv["dst"]))
            _journal_append(entries, {"src": mv["src"], "dst": mv["dst"],
                                      "at": datetime.datetime.now().isoformat()})
        path = _journal_path()
        if path.exists():
            os.replace(path, _journal_done_path())     # 저널 보관 (단일 파일)
        log.info(f"✅ 마이그레이션 완료 — {len(entries)}건 이동")
        return {"moved": len(entries), "journal": str(_journal_done_path())}
    except Exception as exc:
        log.error(f"❌ 마이그레이션 실패 — 저널 역순 롤백 시작: {exc}")
        try:
            restored = _rollback_entries(entries)
            _journal_path().unlink(missing_ok=True)
            log.error(f"↩️ 전량 원복 완료({restored}건). 원인: {exc}")
        except Exception as rexc:
            raise MoveError(
                f"마이그레이션 실패 후 롤백까지 실패했습니다: {rexc}\n"
                f"저널: {_journal_path()} — 저널의 dst→src 를 수동으로 되돌리세요.") from rexc
        raise
    finally:
        unlock()


def _rollback_entries(entries: list) -> int:
    # 사전 점검 — 하나라도 되돌릴 수 없으면 **한 건도 움직이지 않는다**.
    # (완료 저널을 뒤늦게 되돌릴 때 그 사이 폴더 지정·개명이 있었으면 여기서 걸린다)
    for rec in entries:
        src, dst = Path(rec["src"]), Path(rec["dst"])
        if dst.exists() and src.exists():
            raise MoveError(f"롤백 목적지가 이미 존재합니다: {src} "
                            f"(저널: {dst} → {src}) — 수동 확인이 필요합니다.")
    restored = 0
    for rec in reversed(entries):
        src, dst = Path(rec["src"]), Path(rec["dst"])
        if not dst.exists():
            continue
        if src.exists():
            raise MoveError(f"롤백 목적지가 이미 존재합니다: {src}")
        src.parent.mkdir(parents=True, exist_ok=True)
        os.rename(dst, src)
        _rmdir_if_empty(dst.parent)
        restored += 1
    return restored


def _pick_rollback_journal() -> tuple:
    """
    되돌릴 저널을 고른다 — **pending 우선 · done 폴백**. FR35.12

    우선순위 근거: pending(`.migration_journal.json`)은 **중단된** 마이그레이션이 방금
    옮긴 것들이고 done(`.migration_journal.done.json`)은 **그 이전에 성공 완료된** 실행의
    보관본이다. 둘 다 있을 때 done을 먼저 되돌리면 pending의 `dst`가 통째로 사라져
    두 번째 롤백이 어긋난다. 따라서 **최근 것(pending)부터 한 번에 하나씩** 되돌리고,
    남은 저널이 있으면 `remaining`으로 알려 한 번 더 실행하게 한다.

    (결함 F-1: 예전에는 pending만 읽어, `--apply` 성공 후 `--rollback`이 무동작이면서
     "롤백 완료"를 출력했다. 완료된 마이그레이션도 되돌릴 수 있어야 한다.)

    반환: `(경로, entries, "pending"|"done"|None)`
    """
    path = _journal_path()
    entries = _read_journal_file(path)
    if path.exists() and not entries:        # 빈 저널은 --apply만 막을 뿐 되돌릴 게 없다
        path.unlink(missing_ok=True)
        log.info("빈 pending 저널을 제거했습니다.")
        entries = []
    if entries:
        return path, entries, "pending"
    path = _journal_done_path()
    entries = _read_journal_file(path)
    if entries:
        return path, entries, "done"
    return None, [], None


def rollback_migration(takeover: bool = False) -> dict:
    """
    저널을 역순으로 되돌린다(평면 구조 복귀). FR35.12

    - **완료된 마이그레이션도 되돌린다** — pending이 없으면 `.done.json`을 쓴다(F-1).
    - **되돌릴 것이 없으면 `restored: 0`** 을 그대로 돌려준다. 호출부(`main.cmd_migrate_groups`)는
      이때 성공 문구를 쓰지 않고 종료코드 2로 구분한다.
    - 락이 있어도 진행 가능하다(F-2) — 롤백이야말로 급사한 마이그레이션의 복구 수단이므로
      락에 갇히면 `--unlock` → `--rollback` 순환에 빠진다. 다만 **살아 있는 락(6시간 이내)**
      을 넘겨받는 것은 `takeover=True`(CLI `--yes` 또는 대화형 확인)로만 허용한다.
    """
    path, entries, source = _pick_rollback_journal()
    if not entries:
        return {"restored": 0, "journal": None, "source": None, "remaining": False,
                "detail": "되돌릴 저널이 없습니다 "
                          "(output/.migration_journal.json · .done.json 둘 다 없음)."}
    if is_locked() and not takeover:
        info = read_lock_info()
        raise ConflictError(
            f"마이그레이션 락이 있습니다(output/.migration.lock · pid={info.get('pid')} "
            f"시작={info.get('started_at')}). 다른 마이그레이션이 정말 진행 중이 아니라면 "
            f"`--yes`를 붙여 락을 넘겨받아 롤백하세요.")
    if source == "done":
        log.warning(f"↩️ 완료된 마이그레이션을 되돌립니다 — {len(entries)}건 "
                    f"(마지막 이동: {entries[-1].get('at')}). "
                    f"이후 대시보드에서 폴더를 바꿨다면 사전 점검에서 중단됩니다.")
    if is_locked():
        log.warning("⚠️ 기존 마이그레이션 락을 넘겨받아 롤백합니다 "
                    f"(이전 보유자: {read_lock_info()}).")
    _lock_write()                            # 탈취 포함 — 롤백 중 다른 이동을 막는다
    try:
        restored = _rollback_entries(entries)
        path.unlink(missing_ok=True)
        remaining = bool(_pick_rollback_journal()[1])
        log.info(f"↩️ 롤백 완료 — {restored}건 원복 ({source} 저널)")
        return {"restored": restored, "journal": str(path),
                "source": source, "remaining": remaining}
    finally:
        unlock()

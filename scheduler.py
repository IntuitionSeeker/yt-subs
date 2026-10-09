"""주기 자동 추출 — 판정·계획·상태. FR37 (DESIGN §2.13·§3.11·§5.10)

`dashboard.jobs`를 **임포트하지 않는다** — `start(manager)`로 주입받는 잎 모듈이라야
`jobs → scheduler → jobs` 순환이 생기지 않고, 판정 로직을 네트워크 없이 단위 검증할 수
있다(V-U33). 의존은 `config`·`channel_registry`·`rss_monitor`·`cookie_health`뿐이다.
"""
import os
import json
import logging
import datetime
import threading

import config
from channel_registry import ChannelRegistry

log = logging.getLogger("scheduler")

# ── 상수 (FR37.3·37.8·37.10) ────────────────────────────────────────────────
STATE_FILENAME = ".scheduler.json"
INTERVAL_CHOICES = (3, 7, 14, 28)        # FR37.3 — 하한 3일이 429 방어선
BUDGET_MIN, BUDGET_MAX = 1, 200          # FR37.8
SKIP_CYCLES_MAX = 4                      # FR37.10 — 1→2→4 (3일 주기 기준 최대 12일)
STARTUP_GRACE_SEC = 300                  # FR37.2 — 기동 후 5분 유예
TICK_SEC = 60                            # FR37.1 — 틱 주기 (판정은 파일·시계뿐)
RSS_FEED_LIMIT = 15                      # FR37.6·DQ-47 — YouTube 피드 상한
_JOB_POLL_SEC = 2

# ── 필드 소유권 (NFR3 ⓓ·DQ-49) ──────────────────────────────────────────────
# **사용자 소유**: 오직 `update()`(= POST /schedule)만 기록한다. 주기 실행·틱은 이 값을
# 읽기만 하고 **절대 쓰지 않는다** — 쓰면 주기 시작 시점 스냅샷이 주기 중의 설정 변경을
# 덮어(lost update) "끄기가 듣지 않는" 사고가 된다(DQ-46이 channels.yaml을 기각한 바로 그 이유).
USER_FIELDS = ("enabled", "interval_days", "max_videos_per_cycle")
# **스케줄러 소유**: 기계가 쓰는 런타임 상태. 사용자 조작으로는 바뀌지 않는다
# (예외: `request_now()`의 `last_run_at` 당기기 — 그것도 스케줄러 필드 경로로 쓴다).
SCHEDULER_FIELDS = ("last_run_at", "skip_cycles", "cursor", "paused_reason",
                    "last_result", "last_skip_at")
# 새 필드를 추가할 때는 위 둘 중 하나에 **반드시** 넣는다 — 어느 쪽도 아니면 저장되지 않는다.

DEFAULTS = {
    "enabled": False,                    # FR37.1 — 배포만으로는 아무 일도 없다
    "interval_days": 3,                  # FR37.3 — 기본 3일 (고정값 아님)
    "max_videos_per_cycle": 30,          # FR37.8
    "last_run_at": None,
    "skip_cycles": 0,
    "cursor": None,                      # FR37.8 — 기아 방지 회전 시작점
    "paused_reason": None,               # "cookie" (FR37.11) — 상태이지 설정이 아니다
    "last_result": None,                 # FR37.19ⓓ — 직전 1회분만
    "last_skip_at": None,                # 백오프로 건너뛴 마지막 시각 (last_result를 덮지 않는다)
}
assert set(DEFAULTS) == set(USER_FIELDS) | set(SCHEDULER_FIELDS)

# 스레드 제어 — `_WAKE`는 틱 대기 중단(run-now·종료)용
_WAKE = threading.Event()
_STOP = threading.Event()
_THREAD = None
_MANAGER = None


# ─── 상태 파일 (FR37.13·DQ-46) ───────────────────────────────────────────────
def state_file():
    """`output/.scheduler.json`. config.OUTPUT_BASE를 **호출 시점에** 읽는다
    (테스트가 경로를 격리할 수 있어야 한다 — 실데이터 보호)."""
    return config.OUTPUT_BASE / STATE_FILENAME


def load_state() -> dict:
    """상태 로드. 부재·손상·키 누락은 전부 기본값 병합으로 폴백한다 (FR37.13)."""
    st = dict(DEFAULTS)
    try:
        path = state_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in DEFAULTS:
                    if k in data:
                        st[k] = data[k]
            else:
                log.warning("⏰ .scheduler.json 형식 오류 — 기본값으로 폴백")
    except Exception as exc:      # 상태 파일 하나 때문에 대시보드가 죽지 않는다
        log.warning(f"⏰ .scheduler.json 읽기 실패 — 기본값으로 폴백: {exc}")
    return _sanitize(st)


def _sanitize(st: dict) -> dict:
    """손상된 값 교정 — 잘못된 주기·예산이 파일에 있어도 동작을 보장한다."""
    if st.get("interval_days") not in INTERVAL_CHOICES:
        st["interval_days"] = DEFAULTS["interval_days"]
    try:
        budget = int(st.get("max_videos_per_cycle"))
    except (TypeError, ValueError):
        budget = DEFAULTS["max_videos_per_cycle"]
    st["max_videos_per_cycle"] = min(max(budget, BUDGET_MIN), BUDGET_MAX)
    try:
        st["skip_cycles"] = max(0, min(int(st.get("skip_cycles") or 0), SKIP_CYCLES_MAX))
    except (TypeError, ValueError):
        st["skip_cycles"] = 0
    # `enabled`는 **JSON 불리언만** 인정한다 — `bool("no")`·`bool("false")`는 True라서
    # 손상된 파일이 사용자가 켠 적 없는 스케줄을 켤 수 있다. 모호하면 꺼짐(보수적)
    st["enabled"] = st.get("enabled") is True
    # `last_run_at`이 손상되면 `next_due_at`이 None = "즉시 도래"가 되어 무인 실행이
    # 곧바로 터진다. 파싱 불가 값은 **지금 막 돈 것처럼** 취급해 한 주기를 벌어 둔다
    if st.get("last_run_at") is not None and _parse(st.get("last_run_at")) is None:
        log.warning("⏰ last_run_at 손상 — 지금 시각으로 교정(즉시 실행 방지)")
        st["last_run_at"] = _iso(datetime.datetime.now())
    if st.get("cursor") is not None and not isinstance(st.get("cursor"), str):
        st["cursor"] = None
    if not isinstance(st.get("last_result"), (dict, type(None))):
        st["last_result"] = None
    return st


def save_state(st: dict):
    """`.tmp` 기록 → `os.replace` 원자 교체 (FR37.13). 실패해도 예외를 전파하지 않는다."""
    path = state_file()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: st.get(k, DEFAULTS[k]) for k in DEFAULTS}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        log.warning(f"⏰ .scheduler.json 저장 실패: {exc}")
        try:
            tmp.unlink()
        except Exception:
            pass


def save_scheduler_state(st: dict) -> dict:
    """
    **스케줄러 소유 필드만** 병합 저장한다 (NFR3 ⓓ·DQ-49).

    기계 쪽 쓰기는 전부 이 함수를 거친다. 저장 직전 `load_state()`로 재적재하므로
    주기 시작 시점에 읽은 스냅샷이 **주기 도중 `POST /schedule`로 바뀐 사용자 설정을
    되돌려 쓰지 않는다** — 주기는 수십 분이 걸릴 수 있어 그 창이 곧 "끄기가 듣지 않는
    구간"이었다(비상 정지는 바로 그 순간에 눌린다).
    """
    merged = load_state()
    for k in SCHEDULER_FIELDS:
        if k in st:
            merged[k] = st[k]
    save_state(merged)
    return merged


# ─── 시각 헬퍼 ───────────────────────────────────────────────────────────────
def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except Exception:
        return None


def next_due_at(st: dict):
    """다음 예정 시각 — 저장하지 않는 파생값 (FR37.14ⓐ)."""
    last = _parse(st.get("last_run_at"))
    if last is None:
        return None                      # 기록 없음 = 즉시 도래
    return _iso(last + datetime.timedelta(days=int(st.get("interval_days") or 3)))


# ─── 조회·갱신 API (FR37.14) ────────────────────────────────────────────────
def _is_running(manager) -> bool:
    """현재 job이 스케줄 작업이고 running인가 — 파생값."""
    try:
        snap = manager.status() if manager else None
    except Exception:
        return False
    return bool(snap and snap.get("kind") == "schedule_run"
                and snap.get("status") == "running")


def get_view(manager=None) -> dict:
    st = load_state()
    view = {k: st.get(k) for k in ("enabled", "interval_days", "max_videos_per_cycle",
                                   "last_run_at", "skip_cycles", "paused_reason",
                                   "last_result", "last_skip_at")}
    view["next_due_at"] = next_due_at(st)
    view["running"] = _is_running(manager if manager is not None else _MANAGER)
    return view


def update(**fields) -> dict:
    """
    `POST /schedule` 부분 갱신. 검증 위반은 `ValueError`(→400).

    **사용자 소유 필드를 쓰는 유일한 경로**다(USER_FIELDS 주석 참조).

    `enabled`를 **켤 때마다 `last_run_at = now`** 로 갱신한다 — 비어 있을 때만 채우면
    "껐다가 몇 주 뒤 다시 켜기"에서 도래 조건이 이미 참이라 **토글하자마자 전 채널
    추출**이 시작된다(옵트인의 취지를 배반한다). 첫 실행을 당기는 건 `run-now`의 몫이다.
    """
    st = load_state()
    if "interval_days" in fields:
        try:
            iv = int(fields["interval_days"])
        except (TypeError, ValueError):
            raise ValueError(f"주기는 {list(INTERVAL_CHOICES)} 중 하나여야 합니다.")
        if iv not in INTERVAL_CHOICES:
            raise ValueError(f"주기는 {list(INTERVAL_CHOICES)}일 중 하나여야 합니다 "
                             f"(입력 {iv}).")
        st["interval_days"] = iv
    if "max_videos_per_cycle" in fields:
        try:
            budget = int(fields["max_videos_per_cycle"])
        except (TypeError, ValueError):
            raise ValueError(f"주기당 상한은 {BUDGET_MIN}~{BUDGET_MAX} 사이여야 합니다.")
        if not (BUDGET_MIN <= budget <= BUDGET_MAX):
            raise ValueError(f"주기당 상한은 {BUDGET_MIN}~{BUDGET_MAX} 사이여야 합니다 "
                             f"(입력 {budget}).")
        st["max_videos_per_cycle"] = budget
    turning_on = False
    if "enabled" in fields:
        turning_on = bool(fields["enabled"]) and not st["enabled"]
        st["enabled"] = bool(fields["enabled"])
        if turning_on:
            st["last_run_at"] = _iso(datetime.datetime.now())
    # 반대 방향의 lost update도 막는다 — 검증하는 동안 주기가 마감을 기록했을 수 있다.
    # 사용자 소유 필드(+켤 때의 기준 시각)만 최신 상태 위에 얹는다
    merged = load_state()
    for key in USER_FIELDS:
        merged[key] = st[key]
    if turning_on:
        merged["last_run_at"] = st["last_run_at"]
    save_state(merged)
    return get_view()


def request_now() -> dict:
    """
    `POST /schedule/run-now` — "지금 도래시키기"(DQ-50).

    `last_run_at`을 간격만큼 과거로 당기고 틱을 깨운다. 실행 경로는 평상시와 동일해
    busy·쿠키·백오프·예산 검사가 한 벌로 유지된다. `skip_cycles`는 소모하지 않는다.
    """
    st = load_state()
    st["last_run_at"] = _iso(datetime.datetime.now()
                             - datetime.timedelta(days=int(st["interval_days"])))
    save_scheduler_state(st)      # `last_run_at`은 스케줄러 소유 필드다
    _WAKE.set()
    return get_view()


# ─── 주기 판정 (순수 함수 — FR37.2·37.10~37.12) ─────────────────────────────
def decide_cycle(st: dict, now: datetime.datetime, *, busy: bool,
                 cookie_warning: bool, started_at: datetime.datetime) -> tuple:
    """
    `("run"|"skip"|"idle", 사유, 갱신된 st)`. 부수효과 없음 — 저장은 호출자 몫이다.

    **판정 순서가 계약이다** (DESIGN §2.13):
      ① enabled ② 기동 유예 ③ 도래 ④ skip_cycles 감소 ⑤ 쿠키 ⑥ busy ⑦ run
    ④를 ⑤·⑥보다 뒤에 두면 쿠키가 만료된 동안 백오프가 소모되지 않아
    차단이 회복된 뒤에도 계속 쉰다.
    """
    st = dict(st)
    if not st.get("enabled"):
        st["paused_reason"] = None
        return "idle", "disabled", st
    if started_at is not None and (now - started_at).total_seconds() < STARTUP_GRACE_SEC:
        # 절전 복귀·Docker 자동 시작 직후에는 네트워크·DNS가 아직 안 붙었을 수 있다
        return "idle", "startup_grace", st

    last = _parse(st.get("last_run_at"))
    interval = datetime.timedelta(days=int(st.get("interval_days") or 3))
    if last is not None and now < last + interval:
        st["paused_reason"] = None
        return "idle", "not_due", st

    # ④ 백오프 소모 — 밀린 주기는 몇 번을 건너뛰었든 1회만 (cron식 누적 금지, DQ-45)
    if int(st.get("skip_cycles") or 0) > 0:
        st["skip_cycles"] = int(st["skip_cycles"]) - 1
        st["last_run_at"] = _iso(now)
        st["paused_reason"] = None
        return "skip", "backoff", st

    # ⑤ 쿠키 경고 — last_run_at 미갱신 → 쿠키를 고치면 다음 틱에 스스로 재개 (FR37.11)
    if cookie_warning:
        st["paused_reason"] = "cookie"
        return "idle", "cookie", st

    # ⑥ 사용자 작업 경합 — 건너뛰지 않고 미룬다 (미갱신 → 60초 뒤 재시도, FR37.12)
    if busy:
        st["paused_reason"] = None
        return "idle", "busy", st

    st["paused_reason"] = None
    return "run", "due", st


# ─── 실행 계획 (FR37.7·37.8·DQ-47) ──────────────────────────────────────────
def build_plan(new_by_channel: dict, st: dict, reg) -> dict:
    """
    RSS 결과 → `_run_grouped`가 기대하는 스캔 캐시와 **같은 모양**의 계획.

    ⓐ 레지스트리 순서에 `cursor`부터 회전(기아 방지) ⓑ `max_videos_per_cycle`까지 절단
    ⓒ 새 영상 수가 피드 상한(15)에 도달한 채널은 `truncated` 표시.
    RSS의 `published`는 `upload_date`로 넘기지 않는다 — 형식이 다르고
    "날짜 미제공" 취급이 안전하다(FR2.6 보수 원칙).
    """
    new_by_channel = new_by_channel or {}
    st = st or {}
    budget = int(st.get("max_videos_per_cycle") or DEFAULTS["max_videos_per_cycle"])
    order = list(reg.names(auto_only=True))
    cursor = st.get("cursor")
    if cursor in order:
        i = order.index(cursor)
        order = order[i:] + order[:i]

    registry = reg.list()
    truncated = sorted(n for n, e in new_by_channel.items()
                       if len(e or []) >= RSS_FEED_LIMIT)

    by_channel, videos_view = {}, []
    remaining = budget
    next_cursor = st.get("cursor")
    for idx, name in enumerate(order):
        if remaining <= 0:
            break
        entries = [e for e in (new_by_channel.get(name) or []) if e.get("id")]
        if not entries:
            continue
        take = entries[:remaining]
        url = ((registry.get(name) or {}).get("url")
               or f"https://www.youtube.com/@{name}/videos")
        by_channel[name] = {
            "url": url,
            "entries": [{"id": e["id"], "title": e.get("title") or e["id"]}
                        for e in take],
        }
        for e in take:
            videos_view.append({
                "id": e["id"],
                "title": e.get("title") or e["id"],
                "channel": name,
                "content_type": "video",
                "playlists": [],          # 카테고리는 다음 전체 run 백필이 채운다 (FR15.5)
                "members_only": False,    # RSS에는 availability가 없다 → 추출 시 확정
                "extracted": False,
                "duration": None,
            })
        remaining -= len(take)
        next_cursor = order[(idx + 1) % len(order)] if order else None

    return {
        "by_channel": by_channel,
        "videos_view": videos_view,
        "truncated": truncated,
        "planned": len(videos_view),
        "channels_with_new": len(new_by_channel),
        "cursor": next_cursor,
    }


# ─── 주기 실행 (FR37.4·37.7·37.10·37.12) ────────────────────────────────────
def _cookie_warning() -> bool:
    try:
        import cookie_health
        return bool(cookie_health.get_status().get("warning"))
    except Exception as exc:      # 쿠키 상태를 못 읽는다고 스케줄을 멈추지 않는다
        log.debug(f"쿠키 상태 조회 실패: {exc}")
        return False


def _empty_result(now: datetime.datetime, outcome: str) -> dict:
    ts = _iso(now)
    return {"started_at": ts, "finished_at": ts, "channels_checked": 0,
            "channels_with_new": 0, "videos_planned": 0, "videos_done": 0,
            "stats": {}, "rss_errors": {}, "truncated_channels": [],
            "aborted_429": False, "outcome": outcome}


def _finish_cycle(result: dict, *, aborted: bool, cursor=None) -> dict:
    """
    주기 마감 — `last_run_at`은 **취소·429 중단으로 끝난 주기에도 갱신**한다
    (갱신을 빠뜨리면 60초 뒤 같은 작업이 되살아나 사용자와 싸운다, FR37.12).
    백오프 리셋 조건은 "429 없이 끝난 주기"다 (FR37.10).

    **주기 시작 시점의 st를 받지 않는다** — 상태는 여기서 다시 읽는다(NFR3 ⓓ).
    수십 분 전 스냅샷을 저장하면 그 사이의 설정 변경(특히 "끄기")이 조용히 원복된다.
    """
    st = load_state()                       # 주기 중 바뀐 값이 반영된 최신 상태
    st["last_run_at"] = _iso(datetime.datetime.now())
    if cursor is not None:
        st["cursor"] = cursor
    if aborted:
        prev = int(st.get("skip_cycles") or 0)
        st["skip_cycles"] = min(max(1, prev * 2), SKIP_CYCLES_MAX)
    else:
        st["skip_cycles"] = 0
    st["paused_reason"] = None
    st["last_result"] = result
    return save_scheduler_state(st)         # 사용자 소유 필드는 쓰지 않는다


def _wait_job(manager, job_id: str, stop_event=None) -> dict:
    """스케줄 job 완료 대기 — 폴링(2초). 종료 신호가 오면 그 시점 스냅샷을 돌려준다."""
    ev = stop_event if stop_event is not None else threading.Event()
    while True:
        snap = manager.status() or {}
        if snap.get("job_id") != job_id or snap.get("status") != "running":
            return snap
        if ev.wait(_JOB_POLL_SEC):      # 프로세스 종료 신호 → 대기 중단
            return manager.status() or {}


def run_cycle(manager, st: dict = None, stop_event=None) -> dict:
    """
    한 주기 실행. 반환: `last_result` (점유 실패로 미실행이면 `None` — `last_run_at` 미갱신).

    RSS 선행(FR37.4) → 계획(FR37.8) → `_run_grouped` 위임(FR37.7) → 결과·백오프 기록.

    `st`는 **계획에만 쓰는 스냅샷**이다(예산·커서). 저장에는 절대 쓰지 않는다 —
    마감은 `_finish_cycle`이 상태를 다시 읽어 스케줄러 소유 필드만 병합한다.
    """
    st = dict(st or load_state())
    now = datetime.datetime.now()
    result = _empty_result(now, "no_new")

    reg = ChannelRegistry()
    names = list(reg.names(auto_only=True))          # FR37.5 — 검색 유입 채널 제외
    result["channels_checked"] = len(names)

    new_by_channel, rss_errors = {}, {}
    if names:
        import rss_monitor                            # 지연 임포트 (FR29)
        try:
            res = rss_monitor.check_new_videos(names=names) or {}
            new_by_channel = res.get("channels") or {}
            rss_errors = res.get("errors") or {}
        except Exception as exc:
            rss_errors = {"*": f"RSS 조회 실패: {str(exc)[:80]}"}
            log.warning(f"⏰ RSS 조회 실패: {exc}")
    result["rss_errors"] = rss_errors
    result["channels_with_new"] = len(new_by_channel)

    plan = build_plan(new_by_channel, st, reg)
    result["videos_planned"] = plan["planned"]
    result["truncated_channels"] = plan["truncated"]

    if not plan["by_channel"]:
        # 새 영상 0 → job을 만들지 않고 주기 종료 (요청 0, FR37.4)
        log.info(f"⏰ 자동 추출 — 새 영상 없음 (채널 {len(names)}개 확인)")
        result["finished_at"] = _iso(datetime.datetime.now())
        _finish_cycle(result, aborted=False, cursor=plan["cursor"])
        return result

    log.info(f"⏰ 자동 추출 시작 — {len(plan['by_channel'])}채널 {plan['planned']}영상")
    try:
        job = manager.start_schedule(plan)
    except Exception as exc:
        # 점유 실패(JobBusyError)는 삼킨다 — 사용자에게 보일 오류가 아니다.
        # last_run_at을 갱신하지 않으므로 다음 틱이 다시 시도한다 (FR37.12)
        log.info(f"⏰ 자동 추출 보류 — 다른 작업이 점유 중: {str(exc)[:80]}")
        return None

    # 대기 중 예외가 나도 **마감은 반드시 한다** — 마감을 건너뛰면 `last_run_at`이
    # 그대로라 다음 틱이 같은 주기를 또 돌린다(무인이라 아무도 못 본다)
    snap, wait_failed = {}, False
    try:
        snap = _wait_job(manager, job.get("job_id"), stop_event) or {}
    except Exception as exc:
        wait_failed = True
        log.warning(f"⏰ 작업 상태 확인 실패 — 주기는 마감한다: {exc}")
        try:
            snap = manager.status() or {}
        except Exception:
            snap = {}
    finally:
        aborted = bool(snap.get("aborted_429"))
        status = snap.get("status") or ("error" if wait_failed else "done")
        result.update({
            "finished_at": _iso(datetime.datetime.now()),
            "videos_done": int(snap.get("done") or 0),
            "stats": dict(snap.get("stats") or {}),
            "aborted_429": aborted,
            "outcome": ("aborted_429" if aborted else
                        status if status in ("cancelled", "error") else "done"),
        })
        _finish_cycle(result, aborted=aborted, cursor=plan["cursor"])
        log.info(f"⏰ 자동 추출 종료 — {result['outcome']} "
                 f"(처리 {result['videos_done']}/{result['videos_planned']})")
    return result


# ─── 틱 스레드 (FR37.1) ─────────────────────────────────────────────────────
class SchedulerThread(threading.Thread):
    """60초 틱 데몬. 루프 본문을 전부 감싼다 — 예외가 스레드를 죽이면
    무인 기능이 조용히 영원히 멈춘다(최악의 실패 모드)."""

    def __init__(self, manager):
        super().__init__(daemon=True, name="scheduler")
        self.manager = manager
        self.started_at = datetime.datetime.now()

    def tick(self):
        st = load_state()
        action, reason, new_st = decide_cycle(
            st, datetime.datetime.now(), busy=self.manager.is_busy(),
            cookie_warning=_cookie_warning(), started_at=self.started_at)
        if new_st != st:
            if action == "skip":
                # `last_result`는 **건드리지 않는다** — 덮이는 값은 십중팔구 이 백오프를
                # 유발한 `aborted_429` 주기의 기록이고(백오프는 429로만 생긴다),
                # 사용자가 "왜 멈췄나"를 확인해야 할 3~12일 동안 그 증거가 필요하다.
                # "건너뜀"은 `skip_cycles` 배너가 더 정확히 말한다
                new_st["last_skip_at"] = _iso(datetime.datetime.now())
                log.info(f"⏰ 429 백오프 — 이번 주기 건너뜀 "
                         f"(남은 {new_st['skip_cycles']}주기)")
            save_scheduler_state(new_st)      # 사용자 설정은 기계가 쓰지 않는다
        if action == "run":
            run_cycle(self.manager, new_st, stop_event=_STOP)

    def run(self):
        log.info("⏰ 스케줄러 스레드 기동 (60초 틱 · 기본 꺼짐)")
        while not _STOP.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.warning(f"⏰ 틱 처리 실패(무시하고 계속): {exc}")
            _WAKE.wait(TICK_SEC)
            _WAKE.clear()
        log.info("⏰ 스케줄러 스레드 종료")


def start(manager):
    """FastAPI startup 훅에서 호출. `SCHEDULER_DISABLED=1`이면 기동하지 않는다."""
    global _THREAD, _MANAGER
    _MANAGER = manager
    if os.environ.get("SCHEDULER_DISABLED") == "1":
        log.info("⏰ 스케줄러 비활성 (SCHEDULER_DISABLED=1)")
        return None
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _WAKE.clear()
    _THREAD = SchedulerThread(manager)
    _THREAD.start()
    return _THREAD


def stop():
    """스레드 종료 요청 (테스트·종료 훅용)."""
    global _THREAD
    _STOP.set()
    _WAKE.set()
    t = _THREAD
    if t is not None and t.is_alive():
        t.join(timeout=5)
    _THREAD = None

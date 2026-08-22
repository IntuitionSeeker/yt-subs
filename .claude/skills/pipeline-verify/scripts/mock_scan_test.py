"""scan_channel 병합·live 필터 + scan_playlists 매핑 + 백필 로직 mock 단위 검증 (네트워크 없음)."""
import sys, json, tempfile, unittest.mock as mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]   # scripts/ → pipeline-verify/ → skills/ → .claude/ → repo root
sys.path.insert(0, str(REPO_ROOT))

# 호스트에 yt_dlp 미설치 (Docker 전용) → import용 스텁
sys.modules["yt_dlp"] = mock.MagicMock()

import config

tmp = Path(tempfile.mkdtemp())
config.OUTPUT_BASE = tmp  # 출력 격리

from extractor import Extractor

ext = Extractor({"name": "mockch", "url": "https://www.youtube.com/@mockch/videos"})

# ── 1. scan_channel: 두 탭 병합 + live 필터 ──────────────────────────────────
tabs = {
    "https://www.youtube.com/@mockch/videos": [
        {"id": "v1", "title": "일반영상1"},
        {"id": "v2", "title": "일반영상2"},
    ],
    "https://www.youtube.com/@mockch/streams": [
        {"id": "s1", "title": "라이브다시보기", "live_status": "was_live"},
        {"id": "s2", "title": "진행중라이브", "live_status": "is_live"},
        {"id": "s3", "title": "예약라이브", "live_status": "is_upcoming"},
        {"id": "v1", "title": "중복영상"},
    ],
}
with mock.patch.object(Extractor, "_scan_tab", side_effect=lambda self, url: [dict(e) for e in tabs[url]], autospec=True):
    entries = ext.scan_channel()

by_id = {e["id"]: e for e in entries}
assert set(by_id) == {"v1", "v2", "s1"}, f"병합 결과 오류: {set(by_id)}"
assert by_id["v1"]["content_type"] == "video", "중복 시 videos 탭 우선이어야 함"
assert by_id["s1"]["content_type"] == "live"
print("✓ scan_channel: 병합 + is_live/is_upcoming 제외 + 중복 시 video 우선")

# ── 2. streams 탭 없는 채널 graceful ─────────────────────────────────────────
def no_streams(self, url):
    if url.endswith("/streams"):
        raise Exception("This channel does not have a streams tab")
    return [{"id": "v1", "title": "t"}]

with mock.patch.object(Extractor, "_scan_tab", no_streams):
    entries = ext.scan_channel()
assert [e["id"] for e in entries] == ["v1"]
print("✓ scan_channel: streams 탭 없음 → graceful 계속")

# ── 3. scan_playlists: 매핑 + 복수 소속 + 파일 저장 ──────────────────────────
def pl_tabs(self, url):
    if url.endswith("/playlists"):
        return [{"id": "PL1", "title": "국내주식", "url": "https://youtube.com/playlist?list=PL1"},
                {"id": "PL2", "title": "바이브코딩", "url": "https://youtube.com/playlist?list=PL2"}]
    if "PL1" in url:
        return [{"id": "v1"}, {"id": "v2"}]
    if "PL2" in url:
        return [{"id": "v2"}, {"id": "v9"}]
    raise AssertionError(url)

with mock.patch.object(Extractor, "_scan_tab", pl_tabs):
    mapping = ext.scan_playlists()

assert mapping == {"v1": ["국내주식"], "v2": ["국내주식", "바이브코딩"], "v9": ["바이브코딩"]}, mapping
saved = json.loads((config.channel_dir("mockch") / "playlists.json").read_text())
assert saved == mapping
print("✓ scan_playlists: 매핑 + 복수 소속 보존 + playlists.json 저장")

# ── 4. playlists 탭 없는 채널 → 빈 매핑 ──────────────────────────────────────
def no_pl(self, url):
    raise Exception("This channel does not have a playlists tab")
with mock.patch.object(Extractor, "_scan_tab", no_pl):
    assert ext.scan_playlists() == {}
print("✓ scan_playlists: 탭 없음 → 빈 매핑")

# ── 5. 백필: 필드 추가·갱신, 빈 매핑 시 보존은 run()에서 생략 ────────────────
meta_dir = ext.dirs["meta"]
meta_dir.mkdir(parents=True, exist_ok=True)
(meta_dir / "a.json").write_text(json.dumps({"id": "v1", "title": "t1"}), encoding="utf-8")
(meta_dir / "b.json").write_text(json.dumps({"id": "v2", "title": "t2", "playlists": ["옛값"], "content_type": "video"}), encoding="utf-8")
n = ext._backfill_meta(mapping)
assert n == 2, n
a = json.loads((meta_dir / "a.json").read_text())
b = json.loads((meta_dir / "b.json").read_text())
assert a["playlists"] == ["국내주식"] and a["content_type"] == "video"
assert b["playlists"] == ["국내주식", "바이브코딩"]
n2 = ext._backfill_meta(mapping)
assert n2 == 0, "변경 없으면 재쓰기 없어야 함"
print("✓ _backfill_meta: 신규 필드 추가·갱신·무변경 스킵")

# ═══ FR17~FR20 (대시보드 백엔드) ══════════════════════════════════════════════
import logging

import cookie_health
from state_manager import StateManager

logging.getLogger().addHandler(logging.NullHandler())   # lastResort 출력 억제

ENTRIES = [{"id": "e1", "title": "영상1"},
           {"id": "e2", "title": "영상2"},
           {"id": "e3", "title": "영상3"}]


def fresh(name):
    """빈 state를 가진 새 채널 Extractor (모든 entry가 'new'로 판정됨)."""
    return Extractor({"name": name, "url": f"https://www.youtube.com/@{name}/videos"})


def boom(*a, **k):
    raise AssertionError("호출되면 안 되는 함수가 호출됨")


# ── 6. progress=None → 기존 CLI 동작 불변 (FR18.1 / V-D2) ────────────────────
calls = []
ext6 = fresh("prog_none")
with mock.patch.object(Extractor, "scan_channel", lambda self: [dict(e) for e in ENTRIES]), \
     mock.patch.object(Extractor, "scan_playlists", lambda self: {"e1": ["PL"]}), \
     mock.patch.object(Extractor, "process_video",
                       lambda self, vid, action="new", **kw: calls.append((vid, action, kw)) or "ok"):
    stats6 = ext6.run()

assert [c[0] for c in calls] == ["e1", "e2", "e3"], calls
assert all(c[2].get("date_range") is None for c in calls), "CLI 경로는 date_range=None"
assert stats6 == {"new": 3, "updated": 0, "skip": 0, "no_sub": 0,
                  "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0}, stats6
assert "cancelled" not in stats6
print("✓ run(progress=None): 스캔·처리·stats 기존과 동일 (cancelled 키 없음)")

# ── 7. progress False → 우아한 취소 (FR18.2) ─────────────────────────────────
seen, done_calls = [], []
ext7 = fresh("prog_cancel")


def cb(payload):
    seen.append(payload)
    # 1번째 영상 처리 직후(done=1)에 취소 신호
    return not (payload["phase"] == "extracting" and payload["done"] >= 1)


with mock.patch.object(Extractor, "scan_channel", lambda self: [dict(e) for e in ENTRIES]), \
     mock.patch.object(Extractor, "scan_playlists", lambda self: {}), \
     mock.patch.object(Extractor, "process_video",
                       lambda self, vid, action="new", **kw: done_calls.append(vid) or "ok"):
    stats7 = ext7.run(progress=cb)

assert done_calls == ["e1"], done_calls
assert stats7["cancelled"] is True and stats7["new"] == 1, stats7
phases = [p["phase"] for p in seen]
assert phases[0] == "scanning" and phases[1] == "playlists" and phases[-1] == "finishing", phases
assert seen[2] == {"phase": "extracting", "done": 0, "total": 3,
                   "current_title": "영상1",
                   "stats": {"new": 0, "updated": 0, "skip": 0, "no_sub": 0,
                             "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0}}, seen[2]
assert ext7.state.path.exists(), "취소여도 state.save()는 수행돼야 함 (FR18.2)"
print("✓ run(progress): 취소 시 1건만 처리·stats.cancelled·state 저장 + payload 스키마")

# ── 8. 콜백 예외는 추출을 죽이지 않는다 ──────────────────────────────────────
ext8 = fresh("prog_raise")
with mock.patch.object(Extractor, "scan_channel", lambda self: [dict(e) for e in ENTRIES]), \
     mock.patch.object(Extractor, "scan_playlists", lambda self: {}), \
     mock.patch.object(Extractor, "process_video", lambda self, vid, action="new", **kw: "ok"):
    stats8 = ext8.run(progress=lambda payload: (_ for _ in ()).throw(RuntimeError("x")))
assert stats8["new"] == 3 and "cancelled" not in stats8, stats8
print("✓ run(progress): 콜백 예외 시 계속 진행")

# ── 9. entries·pl_map 주입 → 스캔 생략 (DQ-13) ───────────────────────────────
ext9 = fresh("inject")
got = []
with mock.patch.object(Extractor, "scan_channel", boom), \
     mock.patch.object(Extractor, "scan_playlists", boom), \
     mock.patch.object(Extractor, "process_video",
                       lambda self, vid, action="new", **kw: got.append(kw.get("playlists_map")) or "ok"):
    stats9 = ext9.run(entries=[dict(ENTRIES[0])], pl_map={})
assert stats9["new"] == 1 and got == [{}], (stats9, got)

# pl_map만 None이면 재생목록 스캔은 수행 (부분 주입)
ext9b = fresh("inject2")
with mock.patch.object(Extractor, "scan_channel", boom), \
     mock.patch.object(Extractor, "scan_playlists", lambda self: {"e1": ["PL"]}), \
     mock.patch.object(Extractor, "process_video", lambda self, vid, action="new", **kw: "ok"):
    ext9b.run(entries=[dict(ENTRIES[0])])
print("✓ run(entries=, pl_map=): 스캔 캐시 재사용 시 재스캔 없음")

# ── 10. date_range 범위 밖 → date_skip (DQ-12, 자막 미다운로드·state 미기록) ──
ext10 = fresh("dates")
INFO = {"title": "기간테스트", "upload_date": "20250101", "id": "d1",
        "subtitles": {}, "automatic_captions": {}}
with mock.patch.object(Extractor, "_fetch_vtt", boom):
    r = ext10.process_video("d1", "new", info=dict(INFO),
                            date_range={"since": "20250601", "until": None})
    assert r == "date_skip", r
    assert "d1" not in ext10.state.state, "date_skip은 state에 기록하지 않는다"
    r2 = ext10.process_video("d1", "new", info=dict(INFO),
                             date_range={"since": None, "until": "20241231"})
    assert r2 == "date_skip", r2
    # 경계 포함 + 날짜 불명은 통과 → 자막 없음 경로로 진행
    r3 = ext10.process_video("d1", "new", info=dict(INFO),
                             date_range={"since": "20250101", "until": "20250101"})
    assert r3 == "no_sub", r3
    r4 = ext10.process_video("d1", "new", info={"title": "날짜없음", "id": "d2"},
                             date_range={"since": "20250601", "until": None})
    assert r4 == "no_sub", r4
rows = (config.channel_dir("dates") / "extract_log.csv").read_text(encoding="utf-8-sig")
assert rows.count("date_skip") == 2, rows
print("✓ process_video(date_range=): 범위 밖 date_skip·자막 미다운로드·state 미기록·경계 포함")

# ── 11. run() 집계: date_skip은 stats·limit·429 리셋에 반영 (모호점 #5) ──────
ext11 = fresh("dateagg")
with mock.patch.object(Extractor, "process_video",
                       lambda self, vid, action="new", **kw: "date_skip" if vid != "e2" else "ok"):
    stats11 = ext11.run(entries=[dict(e) for e in ENTRIES], pl_map={},
                        date_range={"since": "20990101", "until": None})
assert stats11["date_skip"] == 2 and stats11["new"] == 1, stats11
assert stats11["skip"] == 0 and stats11["no_sub"] == 0
# V-D11 등식: total == stats 합계
assert sum(v for k, v in stats11.items() if k != "cancelled") == 3, stats11
print("✓ run(): date_skip 집계 + 대상 수 == stats 합계 (V-D11 등식)")

# ── 12. decide(): members_only 쿠키 재시도 (FR19.1, DQ-10) ───────────────────
sm = StateManager("statech")
sm.state["m1"] = {"upload_date": "00000000", "modified_date": "members_only",
                  "sub_type": "members_only", "extracted_at": "", "basename": ""}
sm.state["v1"] = {"upload_date": "20250101", "modified_date": "20250101",
                  "sub_type": "auto", "extracted_at": "2025-01-01T00:00:00",
                  "basename": "b"}
_orig_cookie = config.COOKIE_FILE
cookie = tmp / "cookies.txt"
config.COOKIE_FILE = cookie
try:
    assert not cookie.exists()
    assert sm.decide("m1", None, None) == "skip", "쿠키 없으면 재시도하지 않는다"
    cookie.write_text("# netscape cookie file\n", encoding="utf-8")
    assert sm.decide("m1", None, None) == "updated", "쿠키 있으면 매 run 재시도"
    assert sm.decide("v1", None, None) == "skip", "일반 항목은 영향 없음"
    assert sm.decide("new1", None, None) == "new", "신규 판정 우선"
finally:
    config.COOKIE_FILE = _orig_cookie
print("✓ decide(): members_only는 쿠키 존재 시에만 updated 재시도")

# ── 13. cookie_health: 경고 감지 + 갱신 시 자동 해제 (FR19.2·19.3) ───────────
import os, time as _time

cookie_health.clear()
config.COOKIE_FILE = tmp / "absent_cookies.txt"      # 쿠키 없는 상태로 시작
ylog = cookie_health.YDLLogger(logging.getLogger("mocktest"))
ylog.warning("[debug] nothing to see here")
assert cookie_health.get_status()["warning"] is False
ylog.warning("WARNING: The provided YouTube account cookies are no longer valid. "
             "They have likely been rotated in the browser as a result of usage.")
st = cookie_health.get_status()
assert st["warning"] is True and "no longer valid" in st["warning_message"], st
assert st["present"] is False and st["mtime"] is None
saved = json.loads(cookie_health.STATUS_FILE.read_text(encoding="utf-8"))
assert saved["invalid"] is True and saved["detected_at"], saved

config.COOKIE_FILE = cookie          # 위 12번에서 만든 파일 재사용
try:
    # 쿠키가 경고보다 오래됨 → 경고 유지
    old = _time.time() - 3600
    os.utime(cookie, (old, old))
    st_old = cookie_health.get_status()
    assert st_old["warning"] is True and st_old["present"] is True and st_old["mtime"], st_old
    # 쿠키를 경고 이후에 갱신 → 자동 해제 (FR19.3)
    new = _time.time() + 60
    os.utime(cookie, (new, new))
    st_new = cookie_health.get_status()
    assert st_new["warning"] is False and st_new["warning_message"] is None, st_new
    assert st_new["present"] is True and st_new["mtime"], st_new
    # 상태 파일이 깨져도 안전 폴백
    cookie_health.STATUS_FILE.write_text("{broken", encoding="utf-8")
    assert cookie_health.get_status()["warning"] is False
finally:
    config.COOKIE_FILE = _orig_cookie
cookie_health.clear()
assert not cookie_health.STATUS_FILE.exists()
print("✓ cookie_health: 경고 감지·상태 영속·쿠키 갱신 시 자동 해제·깨진 파일 폴백")

# ── 14. _ydl_opts: YDLLogger 주입 (FR19.2) ───────────────────────────────────
config.COOKIE_FILE = tmp / "absent_cookies.txt"   # 실제 쿠키 복사 부작용 방지
opts = fresh("optsch")._ydl_opts(skip_download=True)
assert isinstance(opts["logger"], cookie_health.YDLLogger), opts.get("logger")
assert opts["sleep_requests"] == config.YTDLP_COMMON["sleep_requests"], "429 보호 옵션 유지"
print("✓ _ydl_opts: YDLLogger 주입 + 기존 429 보호 옵션 보존")

# ── 15. --limit 요청 예산: 요청을 쓴 모든 경로가 소비 (F-2, FR14.5) ──────────
ENTRIES5 = [{"id": f"x{i}", "title": f"영상{i}"} for i in range(1, 6)]


def run_limit(name, effect, limit=2):
    """process_video를 effect로 대체하고 run(limit=)을 실행 → (호출된 vid, stats)"""
    e = fresh(name)
    seen_vids = []

    def pv(self, vid, action="new", **kw):
        seen_vids.append(vid)
        return effect(vid)

    with mock.patch.object(Extractor, "process_video", pv):
        st = e.run(entries=[dict(x) for x in ENTRIES5], pl_map={}, limit=limit)
    return seen_vids, st


def raise_members(vid):
    raise Exception("Join this channel to get access to members-only content")


def raise_error(vid):
    raise Exception("HTTP Error 403: Forbidden")


# 멤버십 재시도(FR19.1)도 extract_info 1회를 쓰므로 예산을 소비해야 한다
v15a, s15a = run_limit("limit_mem", raise_members)
assert v15a == ["x1", "x2"], f"멤버십 경로가 예산을 소비하지 않았다: {v15a}"
assert s15a["members_only"] == 2, s15a
# 기타 오류 경로도 동일
v15b, s15b = run_limit("limit_err", raise_error)
assert v15b == ["x1", "x2"], f"오류 경로가 예산을 소비하지 않았다: {v15b}"
assert s15b["error"] == 2, s15b
# 성공·무자막·기간외 혼합 (기존 동작 유지)
v15c, s15c = run_limit("limit_mix",
                       lambda vid: {"x1": "ok", "x2": "date_skip"}.get(vid, "no_sub"))
assert v15c == ["x1", "x2"], v15c
assert s15c["new"] == 1 and s15c["date_skip"] == 1 and s15c["no_sub"] == 0, s15c
# 429 경로: 백오프 후 같은 영상 1회 재시도(FR14.3), 재시도도 예산 소비(DQ-16)
# → limit=2는 x1의 시도 2회로 소진되고, error는 최종 포기 영상 수(1) 기준
with mock.patch("extractor.time.sleep") as slept:
    v15d, s15d = run_limit("limit_429",
                           lambda vid: (_ for _ in ()).throw(
                               Exception("HTTP Error 429: Too Many Requests")))
assert v15d == ["x1", "x1"], f"429 후 같은 영상을 재시도해야 함: {v15d}"
assert s15d["error"] == 1, s15d
assert [c.args[0] for c in slept.call_args_list] == [config.BACKOFF_BASE_SEC,
                                                     config.BACKOFF_BASE_SEC * 2], \
    "지수 백오프가 유지돼야 함"
# limit 미지정이면 전부 처리 (회귀 방지)
e15 = fresh("limit_none")
seen15 = []
with mock.patch.object(Extractor, "process_video",
                       lambda self, vid, action="new", **kw: seen15.append(vid) or "ok"):
    e15.run(entries=[dict(x) for x in ENTRIES5], pl_map={})
assert len(seen15) == 5, seen15
print("✓ run(limit=): 성공·무자막·기간외·멤버십·오류·429 모든 경로가 요청 예산 소비")

# ── 16. mark_invalid: 프로세스당 1회 기록 + 최초 detected_at 보존 (F-3) ──────
config.COOKIE_FILE = tmp / "absent_cookies.txt"


def read_status():
    return json.loads(cookie_health.STATUS_FILE.read_text(encoding="utf-8"))


cookie_health.clear()
cookie_health.mark_invalid("cookies are no longer valid (first)")
first = read_status()
for _ in range(10):
    cookie_health.mark_invalid("cookies are no longer valid (repeat)")
assert read_status() == first, "프로세스 내 중복 호출이 상태 파일을 재기록했다"

# 새 프로세스(=플래그 리셋): 기존 경고가 살아있으면 최초 감지 시각을 보존한다
cookie_health.STATUS_FILE.write_text(json.dumps(
    {"invalid": True, "message": "old", "detected_at": "2020-01-01T00:00:00"}),
    encoding="utf-8")
cookie_health._marked_for = cookie_health._UNMARKED
cookie_health.mark_invalid("cookies are no longer valid (next run)")
st16 = read_status()
assert st16["detected_at"] == "2020-01-01T00:00:00", f"최초 감지 시각이 갱신됐다: {st16}"
assert st16["message"] == "cookies are no longer valid (next run)", st16

# 쿠키를 교체하면 같은 프로세스(serve 컨테이너)에서도 재감지·재기록한다.
# 갱신 시각이 이전 경고(2020) 이후이므로 detected_at도 새로 찍힌다
config.COOKIE_FILE = cookie
_refreshed = _time.time() - 5          # 경고(2020) 이후·재감지 이전에 갱신됨
os.utime(cookie, (_refreshed, _refreshed))
cookie_health.mark_invalid("cookies are no longer valid (after refresh)")
st16b = read_status()
assert st16b["detected_at"] > "2020-01-01T00:00:00", st16b
assert st16b["message"].endswith("(after refresh)"), st16b
assert cookie_health.get_status()["warning"] is True, "재감지 후에는 경고가 살아있어야 함"
# 같은 쿠키에 대한 반복 호출은 다시 기록하지 않는다
cookie_health.mark_invalid("cookies are no longer valid (dup)")
assert read_status() == st16b, "같은 쿠키에 대해 재기록됐다"

# YDLLogger가 같은 경고를 반복해도 기록은 1회 (68회 재기록 재발 방지)
cookie_health.clear()
config.COOKIE_FILE = tmp / "absent_cookies.txt"
ylog16 = cookie_health.YDLLogger(logging.getLogger("mocktest"))
for i in range(5):
    ylog16.warning(f"The provided YouTube account cookies are no longer valid. #{i}")
assert read_status()["message"].endswith("#0"), read_status()
cookie_health.clear()
config.COOKIE_FILE = _orig_cookie
print("✓ mark_invalid: 프로세스당 1회 기록 · 최초 detected_at 보존 · 쿠키 갱신 후 재감지")

print("\n모든 mock 단위 검증 통과")

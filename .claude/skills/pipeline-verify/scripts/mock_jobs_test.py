"""dashboard/jobs.py 네트워크 없는 로직 검증 — classify_url·apply_filters·JobManager 동시성."""
import sys, time, types, threading, tempfile, unittest.mock as mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]   # scripts/ → pipeline-verify/ → skills/ → .claude/ → repo root
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "dashboard"))

# 호스트에 yt_dlp 미설치 (Docker 전용) → import용 스텁
sys.modules["yt_dlp"] = mock.MagicMock()
try:                                   # PyYAML도 호스트 미설치 → 최소 스텁
    import yaml
except ImportError:
    _y = types.ModuleType("yaml")
    _y.safe_load = lambda *a, **k: {"channels": {}}
    _y.safe_dump = lambda *a, **k: None
    sys.modules["yaml"] = _y

import config
tmp = Path(tempfile.mkdtemp())
config.OUTPUT_BASE = tmp                       # 출력 격리

import jobs
from jobs import classify_url, apply_filters, JobManager, JobBusyError

# ── 1. classify_url 7케이스 (FR17.1) ─────────────────────────────────────────
cases = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",        ("video", "dQw4w9WgXcQ")),
    ("https://youtu.be/abcdefghijk",                        ("video", "abcdefghijk")),
    ("https://www.youtube.com/shorts/ABCDEFGHIJK",          ("video", "ABCDEFGHIJK")),
    ("https://www.youtube.com/live/A1b2C3d4E5f",            ("video", "A1b2C3d4E5f")),
    ("https://www.youtube.com/@%EB%91%90%EB%91%90%EA%B0%90%EC%9E%90",
     ("channel", "https://www.youtube.com/@%EB%91%90%EB%91%90%EA%B0%90%EC%9E%90")),
    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstu",
     ("channel", "https://www.youtube.com/channel/UCabcdefghijklmnopqrstu")),
]
for url, expect in cases:
    got = classify_url(url)
    assert got == expect, f"{url} → {got} (기대 {expect})"
for bad in ("https://example.com/foo", "그냥문자열", ""):
    try:
        classify_url(bad)
        raise AssertionError(f"판별 불가여야 함: {bad!r}")
    except ValueError:
        pass
# 채널 URL에 /videos 접미사·핸들 뒤 탭이 붙어도 채널
assert classify_url("https://youtube.com/@handle/videos")[0] == "channel"
print("✓ classify_url: watch/youtu.be/shorts/live/@핸들/channel-UC + 판별불가 ValueError")

# ── 2. apply_filters — 프론트 applyFilters()와 동일 순서 (모호점 #2) ─────────
def V(i, title, pls=(), mem=False):
    return {"id": f"v{i}", "title": title, "playlists": list(pls), "members_only": mem,
            "content_type": "video", "extracted": False}

# 전체 10개 중 카테고리 '주식' 매치 5개 (v0,v2,v4,v6,v8)
vids = [V(i, f"영상 {i}", ["주식"] if i % 2 == 0 else ["요리"]) for i in range(10)]
f = {"latest": 3, "categories": ["주식"], "include_members": False, "keyword": None}
out = apply_filters(vids, f)
assert [v["id"] for v in out] == ["v0", "v2", "v4"], out
print("✓ apply_filters: 카테고리 5개로 거른 뒤 latest=3 slice → 매치 앞 3개 (필터 후 slice)")

# 잘못된 순서(먼저 slice)면 v0,v2만 남았을 것 → 결과 수로 구분됨
assert len(out) == 3, "slice가 필터보다 먼저 적용되면 안 된다"

# 카테고리 OR 결합 + 재생목록 없는 영상 제외
mixed = [V(0, "a", ["주식"]), V(1, "b", ["요리"]), V(2, "c", []), V(3, "d", ["주식", "요리"])]
got = [v["id"] for v in apply_filters(mixed, {"categories": ["주식", "코딩"]})]
assert got == ["v0", "v3"], got
print("✓ apply_filters: 카테고리 OR·완전일치, 재생목록 없는 영상 제외")

# 멤버십 (ⓓ)
mem_vids = [V(0, "a"), V(1, "b", mem=True), V(2, "c")]
assert [v["id"] for v in apply_filters(mem_vids, {})] == ["v0", "v2"]
assert [v["id"] for v in apply_filters(mem_vids, {"include_members": True})] == ["v0", "v1", "v2"]
print("✓ apply_filters: include_members=false 기본 제외 / true 포함")

# 키워드 (ⓔ) 대소문자 무시 부분일치
kw_vids = [V(0, "Python 기초"), V(1, "요리"), V(2, "파이썬 pyTHON 심화")]
assert [v["id"] for v in apply_filters(kw_vids, {"keyword": "python"})] == ["v0", "v2"]
print("✓ apply_filters: 키워드 부분일치·대소문자 무시")

# since/until은 여기서 적용하지 않는다 (DQ-12)
assert len(apply_filters(vids, {"since": "20250101", "until": "20250102"})) == 10
print("✓ apply_filters: since/until 미적용 (처리 시 확정)")

# latest가 대상 수보다 크면 전량
assert len(apply_filters(vids, {"latest": 99})) == 10

# ── 3. 프론트 applyFilters()와 동일 결과인지 로직 대조 ───────────────────────
def front_apply(videos, f):        # index.html:672-680 그대로 옮긴 참조 구현
    out = [v for v in videos
           if (f.get("include_members") or not v.get("members_only"))
           and (not f.get("keyword") or f.get("keyword").lower() in (v.get("title") or "").lower())
           and (not f.get("categories")
                or any(p in f["categories"] for p in (v.get("playlists") or [])))]
    if f.get("latest"):
        out = out[:f["latest"]]
    return out

combos = [
    {"latest": 3, "categories": ["주식"], "include_members": False, "keyword": None},
    {"latest": None, "categories": [], "include_members": True, "keyword": "영상 1"},
    {"latest": 2, "categories": ["요리"], "include_members": True, "keyword": "영상"},
    {"latest": 5, "categories": [], "include_members": False, "keyword": None},
]
pool = vids + [V(90, "멤버십 전용", ["주식"], mem=True)]
for c in combos:
    assert [v["id"] for v in apply_filters(pool, c)] == [v["id"] for v in front_apply(pool, c)], c
print("✓ apply_filters: 프론트 참조 구현과 4개 조건 조합 결과 일치 (V-D11 전제)")

# ── 4. JobManager 동시성·취소 (FR17.7·17.8) ─────────────────────────────────
mgr = JobManager()
assert mgr.status() == {"status": "idle"}, "기동 후 무작업이면 idle"

gate = threading.Event()
started = threading.Event()

def dummy_worker(job, *_):
    started.set()
    mgr._update(job, phase="extracting", total=5)
    while not gate.is_set():
        if mgr._cancel.is_set():
            mgr._finish(job, "cancelled")
            return
        time.sleep(0.01)
    mgr._finish(job, "done")

def fake_start(**over):
    job = mgr._new_job("channel_run", "테스트채널", "https://youtube.com/@t")
    mgr._acquire()
    with mgr._lock:
        mgr._cancel.clear()
        mgr._job = job
        mgr._thread = threading.Thread(target=mgr._wrap, args=(dummy_worker, (job,)), daemon=True)
        mgr._thread.start()
    return job

fake_start()
started.wait(2)
assert mgr.status()["status"] == "running"

# 실행 중 start → JobBusyError
try:
    mgr.start({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    raise AssertionError("실행 중에는 JobBusyError여야 함")
except JobBusyError as e:
    assert e.job and e.job["status"] == "running"
# 실행 중 scan → JobBusyError
try:
    mgr.scan("https://www.youtube.com/@handle")
    raise AssertionError("실행 중 스캔도 JobBusyError여야 함")
except JobBusyError:
    pass
print("✓ JobManager: 실행 중 start/scan → JobBusyError (409, 모호점 #9)")

# 취소
assert mgr.cancel() is True
for _ in range(200):
    if mgr.status()["status"] != "running":
        break
    time.sleep(0.01)
assert mgr.status()["status"] == "cancelled", mgr.status()
assert mgr.status()["finished_at"], "종료 시각 기록"
print("✓ JobManager: cancel → status=cancelled, 마지막 job 유지 (모호점 #19)")

# 종료 후에는 다시 시작 가능 + 요청 판정 (모호점 #1)
gate.set()
for bad, why in [({}, "url·scan_id 둘 다 없음"),
                 ({"url": "https://youtu.be/abcdefghijk", "scan_id": "x"}, "둘 다 있음"),
                 ({"url": "https://www.youtube.com/@handle"}, "채널 URL 직접 요청"),
                 ({"url": "https://example.com/x"}, "판별 불가"),
                 ({"scan_id": "없는아이디"}, "scan_id 만료·부재")]:
    try:
        mgr.start(bad)
        raise AssertionError(f"400이어야 함: {why}")
    except ValueError as e:
        assert str(e), why
assert mgr._busy is False, "400 요청이 점유를 남기면 안 된다"
print("✓ JobManager.start: 요청 형태 위반 5종 모두 ValueError(400), 점유 누수 없음")

# ── 5. 스캔 캐시 TTL·멤버십 합집합 판정 (모호점 #10·#14) ─────────────────────
class FakeExtractor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = mock.MagicMock()
        self.state.state = {
            "v1": {"sub_type": "auto"},
            "v3": {"sub_type": "members_only"},
            "v4": {"sub_type": "none"},
        }
    def scan_channel(self):
        return [{"id": "v1", "title": "영상1", "content_type": "video"},
                {"id": "v2", "title": "영상2", "content_type": "video",
                 "availability": "subscriber_only"},
                {"id": "v3", "title": "영상3", "content_type": "live"},
                {"id": "v4", "title": "영상4", "content_type": "video"}]
    def scan_playlists(self):
        return {"v1": ["주식"], "v3": ["주식", "라이브"]}

import extractor as _extractor_mod
with mock.patch.object(_extractor_mod, "Extractor", FakeExtractor):
    res = mgr.scan("https://www.youtube.com/@테스트채널")

assert res["channel"] == "테스트채널"
assert [v["id"] for v in res["videos"]] == ["v1", "v2", "v3", "v4"], "스캔 순서 그대로 유지"
by = {v["id"]: v for v in res["videos"]}
assert by["v2"]["members_only"] is True, "availability=subscriber_only → 멤버십"
assert by["v3"]["members_only"] is True, "state.sub_type=members_only → 멤버십"
assert by["v1"]["members_only"] is False
assert by["v1"]["extracted"] is True and by["v4"]["extracted"] is False
assert by["v1"]["playlists"] == ["주식"]
assert res["playlists"] == ["라이브", "주식"]
entry = mgr._get_scan(res["scan_id"])
assert entry["entries"] and entry["pl_map"], "원본 entries·pl_map 캐시 보관 (모호점 #14)"
assert mgr._busy is False, "스캔 종료 후 점유 해제"
print("✓ scan: 순서 보존 · members_only 합집합 · extracted · 원본 entries/pl_map 캐시")

# TTL 만료 → start 시 400(ValueError)
entry["created_at"] -= jobs.SCAN_TTL_SEC + 1
try:
    mgr.start({"scan_id": res["scan_id"], "filters": {}})
    raise AssertionError("만료된 scan_id는 ValueError(400)")
except ValueError as e:
    assert "만료" in str(e), e
print("✓ scan 캐시 TTL 10분 만료 → start ValueError(400, 모호점 #8)")

# ── 6. 채널 워커: 캐시 재사용·필터 전달·인덱싱 정책 ──────────────────────────
calls = {}

class RunExtractor(FakeExtractor):
    def run(self, force_vid=None, limit=None, progress=None,
            entries=None, pl_map=None, date_range=None):
        calls["entries"] = entries
        calls["pl_map"] = pl_map
        calls["date_range"] = date_range
        assert progress({"phase": "extracting", "done": 0, "total": len(entries),
                         "current_title": "영상1", "stats": {}}) is True
        return {"new": 1, "updated": 0, "skip": 0, "no_sub": 0,
                "members_only": 0, "error": 0, "date_skip": 1}

with mock.patch.object(_extractor_mod, "Extractor", RunExtractor):
    res2 = mgr.scan("https://www.youtube.com/@테스트채널")
    entry2 = mgr._get_scan(res2["scan_id"])
    reg = mock.MagicMock()
    reg.names.return_value = ["테스트채널"]
    reg.get.return_value = {"name": "테스트채널", "url": "https://www.youtube.com/@테스트채널/videos",
                            "lang": "ko"}
    idx = mock.MagicMock()
    with mock.patch.object(jobs, "ChannelRegistry", mock.MagicMock(return_value=reg,
                                                                   extract_handle=str)), \
         mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=idx)}):
        job = mgr._new_job("channel_run", "테스트채널", res2["scan_id"])
        mgr._job = job
        mgr._cancel.clear()
        mgr._run_channel(job, entry2,
                         {"latest": 2, "categories": [], "include_members": False,
                          "keyword": None, "since": "20250101", "until": None}, True)

assert [e["id"] for e in calls["entries"]] == ["v1", "v4"], calls["entries"]  # v2·v3은 멤버십 제외
assert calls["pl_map"] == {"v1": ["주식"], "v3": ["주식", "라이브"]}, "캐시 pl_map 재사용"
assert calls["date_range"] == {"since": "20250101", "until": None}
assert job["status"] == "done" and job["total"] == 2
assert job["stats"]["new"] == 1 and job["stats"]["date_skip"] == 1
assert idx.called and idx.return_value.index_all.called, "index=True·신규>0 → 인덱싱"
print("✓ _run_channel: 멤버십 사전 제외 · 캐시 entries/pl_map 재사용 · date_range 전달 · 인덱싱")

# 취소면 인덱싱 생략 (사용자 확정 ①)
class CancelExtractor(FakeExtractor):
    def run(self, force_vid=None, limit=None, progress=None,
            entries=None, pl_map=None, date_range=None):
        mgr._cancel.set()
        assert progress({"phase": "extracting", "done": 1, "total": 2,
                         "stats": {"new": 1}}) is False, "취소 시 콜백 False"
        return {"new": 1, "updated": 0, "skip": 0, "no_sub": 0, "members_only": 0,
                "error": 0, "date_skip": 0, "cancelled": True}

idx2 = mock.MagicMock()
with mock.patch.object(_extractor_mod, "Extractor", CancelExtractor), \
     mock.patch.object(jobs, "ChannelRegistry", mock.MagicMock(return_value=reg, extract_handle=str)), \
     mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=idx2)}):
    job2 = mgr._new_job("channel_run", "테스트채널", "u")
    mgr._job = job2
    mgr._cancel.clear()
    mgr._run_channel(job2, entry2, {"include_members": True}, True)

assert job2["status"] == "cancelled", job2
assert not idx2.called, "취소 시 인덱싱 생략 (사용자 확정 ①)"
assert job2["stats"]["new"] == 1 and job2["done"] == 1
print("✓ _run_channel: 취소 → status=cancelled, 인덱싱 생략, 진행분 stats 유지")

# ── 7. 단일영상 채널 URL 조립 (모호점 #16) ──────────────────────────────────
assert JobManager._channel_url_from_info({"uploader_id": "@두두감자"}) == \
    "https://www.youtube.com/@두두감자"
assert JobManager._channel_url_from_info(
    {"uploader_id": "UCxxxx", "channel_url": "https://www.youtube.com/channel/UCxxxx"}) == \
    "https://www.youtube.com/channel/UCxxxx"
try:
    JobManager._channel_url_from_info({})
    raise AssertionError("채널 URL 없으면 오류")
except ValueError:
    pass
# 조립된 URL이 normalize_url을 거쳐도 깨지지 않는지
from channel_registry import ChannelRegistry as CR
u = JobManager._channel_url_from_info({"uploader_id": "@두두감자"})
assert CR.normalize_url(u) == "https://www.youtube.com/@두두감자/videos", CR.normalize_url(u)
assert CR.extract_handle(u) == "두두감자"
print("✓ _channel_url_from_info: @핸들 → 정상 채널 URL (normalize_url 검증 포함)")

# ── 8. 단일영상 워커 (FR17.2) ───────────────────────────────────────────────
config.COOKIE_FILE = tmp / "no_cookies.txt"      # resolve_cookiefile 부작용 차단

INFO = {"uploader_id": "@기존채널", "title": "단일 영상", "live_status": None}

class FakeYDL:
    def __init__(self, opts): self.opts = opts
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False): return INFO

sys.modules["yt_dlp"].YoutubeDL = FakeYDL

pv_calls = []

class SingleExtractor(FakeExtractor):
    def process_video(self, vid, action="new", content_type="video",
                      playlists_map=None, info=None, date_range=None):
        pv_calls.append({"vid": vid, "action": action, "info": info,
                         "content_type": content_type})
        return "ok"

class FakeReg:
    added = []
    extract_handle = staticmethod(CR.extract_handle)
    normalize_url = staticmethod(CR.normalize_url)
    def names(self): return ["기존채널"]
    def add(self, url, lang=None, note=""):
        FakeReg.added.append(url); return CR.extract_handle(url)
    def get(self, name):
        return {"name": name, "url": f"https://www.youtube.com/@{name}/videos", "lang": "ko"}

idx3 = mock.MagicMock()
with mock.patch.object(_extractor_mod, "Extractor", SingleExtractor), \
     mock.patch.object(jobs, "ChannelRegistry", FakeReg), \
     mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=idx3)}):
    job3 = mgr._new_job("single_video", "", "https://youtu.be/abcdefghijk")
    mgr._job = job3; mgr._cancel.clear()
    mgr._run_single(job3, "https://youtu.be/abcdefghijk", "abcdefghijk", True)

assert FakeReg.added == [], "이미 등록된 채널이면 add() 호출 금지 (모호점 #16)"
assert pv_calls[0] == {"vid": "abcdefghijk", "action": "new", "info": INFO,
                       "content_type": "video"}, pv_calls
assert job3["channel"] == "기존채널" and job3["status"] == "done"
assert job3["total"] == 1 and job3["done"] == 1 and job3["stats"]["new"] == 1
assert idx3.return_value.index_all.called
print("✓ _run_single: info 재사용 process_video · 기존 채널 add 금지 · stats/인덱싱")

# 미등록 채널 → add(조립 URL) 1회
INFO = {"uploader_id": "@새채널", "title": "새 영상", "live_status": "was_live"}
pv_calls.clear()
with mock.patch.object(_extractor_mod, "Extractor", SingleExtractor), \
     mock.patch.object(jobs, "ChannelRegistry", FakeReg), \
     mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=mock.MagicMock())}):
    job4 = mgr._new_job("single_video", "", "https://youtu.be/abcdefghijk")
    mgr._job = job4; mgr._cancel.clear()
    mgr._run_single(job4, "https://youtu.be/abcdefghijk", "abcdefghijk", False)

assert FakeReg.added == ["https://www.youtube.com/@새채널"], FakeReg.added
assert pv_calls[0]["content_type"] == "live", "was_live → live"
assert job4["channel"] == "새채널" and job4["status"] == "done"
print("✓ _run_single: 미등록 채널 자동 등록(조립 URL) · was_live → content_type=live")

# ── 9. 재생목록 스캔·워커 (FR24) ────────────────────────────────────────────
# classify_url: 재생목록 인식 + watch?v=…&list=…는 영상 우선 (FR24.1)
PL_URL = "https://www.youtube.com/playlist?list=PLnDn1H0jzj2irPsp9sy5HJZ-435yMOXy_"
assert classify_url(PL_URL) == ("playlist", PL_URL)
assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz")[0] == "video"
print("✓ classify_url: playlist 인식 · watch+list는 영상 우선 (FR24.1)")

# _merged_pl_map: 기존 태그 보존 + 병합 + 멱등 (FR24.4·DQ-17)
from jobs import _merged_pl_map
ch_dir = tmp / "chanA"
ch_dir.mkdir(parents=True, exist_ok=True)
(ch_dir / "playlists.json").write_text('{"p1": ["기존카테고리"]}', encoding="utf-8")
m9 = _merged_pl_map("chanA", ["p1", "p2"], "퀀트 강의")
assert m9["p1"] == ["기존카테고리", "퀀트 강의"] and m9["p2"] == ["퀀트 강의"], m9
m9b = _merged_pl_map("chanA", ["p1", "p2"], "퀀트 강의")   # 재실행해도 중복 없음
assert m9b == m9, m9b
import json as _json
saved9 = _json.loads((ch_dir / "playlists.json").read_text(encoding="utf-8"))
assert saved9 == m9, "병합 결과가 playlists.json에 저장돼야 함"
# playlists.json 없으면 기존 meta에서 재구성 (부분 맵 wipe 방지)
ch_dir2 = tmp / "chanB"
(ch_dir2 / "meta").mkdir(parents=True, exist_ok=True)
(ch_dir2 / "meta" / "x.json").write_text('{"id": "q1", "playlists": ["옛태그"]}', encoding="utf-8")
m9c = _merged_pl_map("chanB", ["q2"], "퀀트 강의")
assert m9c == {"q1": ["옛태그"], "q2": ["퀀트 강의"]}, m9c
print("✓ _merged_pl_map: 기존 태그 보존·병합·멱등·meta 재구성 (DQ-17)")

# _do_scan_playlist: flat 스캔 → 채널 해석·라이브 제외·멤버십 판정 (FR24.2)
pl_info = {"title": "퀀트 강의", "entries": [
    {"id": "p1", "title": "영상A", "uploader_id": "@chanA"},
    {"id": "p2", "title": "영상B", "uploader_id": "@chanA", "availability": "subscriber_only"},
    {"id": "p3", "title": "영상C", "channel_id": "UCzzzzzzzzzzzzzzzzzzzzzz"},
    {"id": "p4", "title": "라이브중", "uploader_id": "@chanA", "live_status": "is_live"},
    {"id": "p5", "title": "채널불명"},
]}
class FakePlYDL(FakeYDL):                     # 섹션 8의 FakeYDL 재사용
    def extract_info(self, url, download=False): return pl_info

sys.modules["yt_dlp"].YoutubeDL = FakePlYDL
res9 = mgr.scan(PL_URL)
assert res9["kind"] == "playlist" and res9["playlist"] == "퀀트 강의"
assert res9["channel"] == "퀀트 강의" and res9["playlists"] == []
assert [v["id"] for v in res9["videos"]] == ["p1", "p2", "p3"], \
    "라이브 진행중(p4)·채널불명(p5) 제외"
by9 = {v["id"]: v for v in res9["videos"]}
assert by9["p1"]["channel"] == "chanA" and by9["p3"]["channel"].startswith("UC")
assert by9["p2"]["members_only"] is True and by9["p1"]["members_only"] is False
entry9 = mgr._get_scan(res9["scan_id"])
assert entry9["kind"] == "playlist" and set(entry9["by_channel"]) == \
    {"chanA", "UCzzzzzzzzzzzzzzzzzzzzzz"}
print("✓ _do_scan_playlist: kind·제목·채널 해석·라이브/불명 제외·멤버십 판정 (FR24.2)")

# _run_playlist: 채널 그룹 순차 실행·진행율 합산·병합 pl_map·조건부 인덱싱 (FR24.3~24.5)
runs9 = []

class PlaylistExtractor(FakeExtractor):
    def run(self, force_vid=None, limit=None, progress=None,
            entries=None, pl_map=None, date_range=None):
        runs9.append({"name": self.cfg["name"],
                      "ids": [e["id"] for e in entries], "pl_map": pl_map})
        if self.cfg["name"] == "chanA":
            assert progress({"phase": "extracting", "done": 1, "total": len(entries),
                             "current_title": "영상A", "stats": {"new": 1}}) is True
            st = mgr._job
            assert st["total"] == 3 and st["done"] == 1, \
                f"진행율은 전체 기준 연속 합산: total={st['total']} done={st['done']}"
            return {"new": 2, "updated": 0, "skip": 0, "no_sub": 0,
                    "members_only": 0, "error": 0, "date_skip": 0}
        return {"new": 0, "updated": 0, "skip": 1, "no_sub": 0,
                "members_only": 0, "error": 0, "date_skip": 0}

reg9 = mock.MagicMock()
reg9.names.return_value = ["chanA"]                 # UC채널은 미등록 → 자동 등록
reg9.get.side_effect = lambda n: {"name": n,
                                  "url": f"https://www.youtube.com/@{n}/videos",
                                  "lang": "ko"}
idx9 = mock.MagicMock()
with mock.patch.object(_extractor_mod, "Extractor", PlaylistExtractor), \
     mock.patch.object(jobs, "ChannelRegistry",
                       mock.MagicMock(return_value=reg9, extract_handle=str,
                                      normalize_url=lambda u: u)), \
     mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=idx9)}):
    job9 = mgr._new_job("playlist_run", "퀀트 강의", PL_URL)
    mgr._job = job9
    mgr._cancel.clear()
    mgr._run_playlist(job9, entry9, {"include_members": True}, True)

assert [r["name"] for r in runs9] == ["chanA", "UCzzzzzzzzzzzzzzzzzzzzzz"], runs9
assert runs9[0]["ids"] == ["p1", "p2"] and runs9[1]["ids"] == ["p3"]
assert runs9[0]["pl_map"]["p1"] == ["기존카테고리", "퀀트 강의"], \
    "병합 full-map 전달 (DQ-17)"
assert "퀀트 강의" in runs9[0]["pl_map"]["p2"]
assert reg9.add.called, "미등록 채널(UC…) 자동 등록"
assert job9["status"] == "done" and job9["total"] == 3 and job9["done"] == 3
assert job9["stats"]["new"] == 2 and job9["stats"]["skip"] == 1
assert [c.args[0] for c in idx9.call_args_list] == ["chanA"], \
    "변경(new+updated>0) 있는 채널만 인덱싱"
print("✓ _run_playlist: 그룹 순차·진행율 합산·병합 pl_map·자동 등록·조건부 인덱싱 (FR24.3~24.5)")

print("\n모든 dashboard/jobs.py 로직 검증 통과")

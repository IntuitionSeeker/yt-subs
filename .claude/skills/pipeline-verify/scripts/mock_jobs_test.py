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
config.CHANNELS_YAML = tmp / "channels.yaml"   # 레지스트리 격리 (실데이터 보호, FR35)
config.invalidate_group_cache()

import folder_ops

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
            entries=None, pl_map=None, date_range=None, rest_state=None):
        calls["entries"] = entries
        calls["pl_map"] = pl_map
        calls["date_range"] = date_range
        assert progress({"phase": "extracting", "done": 0, "total": len(entries),
                         "current_title": "영상1", "stats": {},
                         "event": {"id": "v1", "title": "영상1",
                                   "kind": "new", "reason": "신규 추출"}}) is True
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
assert job["events"] == [{"id": "v1", "title": "영상1", "kind": "new",
                          "reason": "신규 추출"}], "이벤트 축적 (FR26.2)"
assert idx.called and idx.return_value.index_all.called, "index=True·신규>0 → 인덱싱"
# 인덱싱 진행 콜백 전달 + 추출 결과(done/total) 보존 (FR33.3, DQ-22)
_cb = idx.return_value.index_all.call_args.kwargs.get("on_progress")
assert callable(_cb), "index_all(on_progress=) 전달"
_before = (job["done"], job["total"])
_cb("subtitle", 7, 100, "인덱싱 중인 영상")
assert job["index_stage"] == "subtitle" and job["index_done"] == 7 \
    and job["index_total"] == 100, job
assert (job["done"], job["total"]) == _before, "인덱싱 진행이 추출 done/total을 덮지 않는다"
print("✓ _run_channel: 멤버십 사전 제외 · 캐시 entries/pl_map 재사용 · date_range 전달 · 인덱싱")
print("✓ _maybe_index: on_progress 콜백 → index_stage/done/total, 추출 done/total 보존 (FR33.3)")

# 취소면 인덱싱 생략 (사용자 확정 ①)
class CancelExtractor(FakeExtractor):
    def run(self, force_vid=None, limit=None, progress=None,
            entries=None, pl_map=None, date_range=None, rest_state=None):
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
    resolve_name = CR.resolve_name          # 실제 역조회 로직 검증 (FR32.2)
    def names(self): return ["기존채널"]
    def list(self):
        return {"기존채널": {"url": "https://www.youtube.com/@기존채널/videos",
                          "lang": "ko"}}
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
            entries=None, pl_map=None, date_range=None, rest_state=None):
        runs9.append({"name": self.cfg["name"],
                      "ids": [e["id"] for e in entries], "pl_map": pl_map,
                      "rest": rest_state})
        if self.cfg["name"] == "chanA":
            assert progress({"phase": "extracting", "done": 1, "total": len(entries),
                             "current_title": "영상A", "stats": {"new": 1},
                             "event": {"id": "p1", "title": "영상A",
                                       "kind": "new", "reason": "신규 추출"}}) is True
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
grp9 = mock.MagicMock(return_value={"moved": True})
with mock.patch.object(_extractor_mod, "Extractor", PlaylistExtractor), \
     mock.patch.object(jobs, "ChannelRegistry",
                       mock.MagicMock(return_value=reg9, extract_handle=str,
                                      normalize_url=lambda u: u)), \
     mock.patch.object(folder_ops, "set_channel_group", grp9), \
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
assert not reg9.set_group.called, \
    "폴더 지정은 folder_ops를 경유해야 한다 — yaml만 쓰면 디렉터리가 따라오지 않는다 (FR35.8)"
assert [c.args for c in grp9.call_args_list] == \
    [("UCzzzzzzzzzzzzzzzzzzzzzz", "퀀트 강의")], \
    "신규 등록 채널만 재생목록 폴더 자동 지정 (FR25.7)"
assert all(c.kwargs.get("on_conflict") == "skip" for c in grp9.call_args_list), \
    "자동 폴더 지정은 충돌 시 거부가 아니라 건너뛰기 (FR35.13)"
assert job9["status"] == "done" and job9["total"] == 3 and job9["done"] == 3
assert job9["stats"]["new"] == 2 and job9["stats"]["skip"] == 1
assert [c.args[0] for c in idx9.call_args_list] == ["chanA"], \
    "변경(new+updated>0) 있는 채널만 인덱싱"
assert job9["events"] == [{"id": "p1", "title": "영상A", "kind": "new",
                           "reason": "신규 추출", "channel": "chanA"}], \
    "재생목록 이벤트에 원채널 부가 (FR26.2)"
# 배치 휴식 상태는 그룹 경계를 넘어 **같은 인스턴스**로 공유된다 (FR14.2)
assert all(r["rest"] is runs9[0]["rest"] for r in runs9) and runs9[0]["rest"] is not None, \
    "그룹마다 새 rest_state면 휴식 카운터가 0으로 리셋된다"
assert isinstance(runs9[0]["rest"], _extractor_mod.BatchRest)
print("✓ _run_playlist: 그룹 순차·진행율 합산·병합 pl_map·자동 등록·조건부 인덱싱 (FR24.3~24.5)")

print("\n모든 dashboard/jobs.py 로직 검증 통과")

# ── 10. 검색 스캔·워커 (FR34) ───────────────────────────────────────────────
from jobs import _build_search_url, _search_opts, SP_PRESETS, search_query_from_url

# classify_url: 검색 결과 URL 인식 · 순수 텍스트는 승격하지 않음 (DQ-27)
SEARCH_URL = "https://www.youtube.com/results?search_query=AI+%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8"
assert classify_url(SEARCH_URL) == ("search", SEARCH_URL)
assert search_query_from_url(SEARCH_URL) == "AI 에이전트"
for bad in ("AI 에이전트", "그냥문자열"):
    try:
        classify_url(bad)
        raise AssertionError(f"검색으로 승격하면 안 됨: {bad!r}")
    except ValueError:
        pass
assert _build_search_url("AI 에이전트", "month").endswith("&sp=EgQIBBAB")
assert _build_search_url("q", "없는프리셋").endswith("&sp=EgIQAQ"), "미지 period → all 폴백"
assert SP_PRESETS["week"] == "EgQIAxAB" and SP_PRESETS["hour"] == "EgQIARAB"
print("✓ classify_url/search: results URL 인식 · 순수 텍스트 ValueError · sp 프리셋 6종 (FR34.1·34.4)")

# _do_scan_search: playlist_items 상한 · duration 필터(결측 통과) · 캐시 kind/query/folder
search_info = {"entries": [
    {"id": "g1", "title": "긴 영상", "uploader_id": "@chanA", "duration": 600},
    {"id": "g2", "title": "짧은 영상", "uploader_id": "@chanA", "duration": 60},
    {"id": "g3", "title": "길이 결측", "uploader_id": "@chanC"},
    {"id": "g4", "title": "타채널", "channel_id": "UCyyyyyyyyyyyyyyyyyyyyyy", "duration": 900},
]}
scan_opts = {}

class FakeSearchYDL(FakeYDL):
    def __init__(self, opts):
        scan_opts.update(opts)
        super().__init__(opts)
    def extract_info(self, url, download=False):
        scan_opts["_url"] = url
        return search_info

sys.modules["yt_dlp"].YoutubeDL = FakeSearchYDL
res10 = mgr.scan_search("AI 에이전트", limit=4, min_duration=180,
                        period="month", folder="AI 묶음")
assert scan_opts["playlist_items"] == "1-4", scan_opts
assert scan_opts["extract_flat"] is True and scan_opts["_url"].endswith("&sp=EgQIBBAB")
assert res10["kind"] == "search" and res10["query"] == "AI 에이전트"
assert res10["folder"] == "AI 묶음" and res10["playlists"] == []
assert [v["id"] for v in res10["videos"]] == ["g1", "g3", "g4"], "60초만 제외, 결측 통과"
assert [v["duration"] for v in res10["videos"]] == [600, None, 900]
entry10 = mgr._get_scan(res10["scan_id"])
assert entry10["kind"] == "search" and entry10["folder"] == "AI 묶음"
assert set(entry10["by_channel"]) == {"chanA", "chanC", "UCyyyyyyyyyyyyyyyyyyyyyy"}
assert mgr._busy is False, "스캔 종료 후 점유 해제"
print("✓ _do_scan_search: 1-N 상한 · sp 실림 · duration 필터(결측 통과) · 캐시 kind/query/folder")

# 조건 위반은 ValueError(400)이고 점유를 남기지 않는다
for bad_kw in ({"q": "  "}, {"q": "x", "limit": 0}, {"q": "x", "limit": 51},
               {"q": "x", "min_duration": -1}):
    try:
        mgr.scan_search(**bad_kw)
        raise AssertionError(f"400이어야 함: {bad_kw}")
    except ValueError:
        pass
assert mgr._busy is False, "400 요청이 점유를 남기면 안 된다"
print("✓ scan_search: q 공백·limit 범위 밖·min_duration 음수 → ValueError(400), 점유 누수 없음")

# start(): 검색 캐시 → search_run 워커 배선
runs10 = []

class SearchExtractor(FakeExtractor):
    def run(self, force_vid=None, limit=None, progress=None,
            entries=None, pl_map=None, date_range=None, rest_state=None):
        runs10.append({"name": self.cfg["name"],
                       "ids": [e["id"] for e in entries], "pl_map": pl_map,
                       "date_range": date_range, "rest": rest_state})
        if self.cfg["name"] == "chanA":
            assert progress({"phase": "extracting", "done": 1, "total": len(entries),
                             "current_title": "긴 영상", "stats": {"new": 1},
                             "event": {"id": "g1", "title": "긴 영상",
                                       "kind": "new", "reason": "신규 추출"}}) is True
            return {"new": 1, "updated": 0, "skip": 0, "no_sub": 0,
                    "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0}
        return {"new": 0, "updated": 0, "skip": 0, "no_sub": 0, "members_only": 0,
                "error": 0, "date_skip": 0, "live_wait": 1}

reg10 = mock.MagicMock()
reg10.names.return_value = ["chanA"]          # chanC·UC…는 미등록 → 자동 등록
reg10.get.side_effect = lambda n: {"name": n,
                                   "url": f"https://www.youtube.com/@{n}/videos",
                                   "lang": "ko"}
idx10 = mock.MagicMock()
# 첫 채널은 이름 충돌로 건너뛰기(FR35.13) — 예외 없이 job 경고로만 노출돼야 한다
grp10 = mock.MagicMock(side_effect=[{"moved": False, "skipped": "이름 충돌"},
                                    {"moved": True}])
with mock.patch.object(_extractor_mod, "Extractor", SearchExtractor), \
     mock.patch.object(jobs, "ChannelRegistry",
                       mock.MagicMock(return_value=reg10, extract_handle=str,
                                      normalize_url=lambda u: u)), \
     mock.patch.object(folder_ops, "set_channel_group", grp10), \
     mock.patch.dict(sys.modules, {"kl_indexer": mock.MagicMock(KLIndexer=idx10)}):
    job10 = mgr._new_job("search_run", "AI 에이전트", SEARCH_URL)
    mgr._job = job10
    mgr._cancel.clear()
    mgr._run_search(job10, entry10,
                    {"include_members": True, "since": "20260101"}, True)

assert [r["name"] for r in runs10] == ["chanA", "chanC", "UCyyyyyyyyyyyyyyyyyyyyyy"]
assert runs10[0]["ids"] == ["g1"] and runs10[1]["ids"] == ["g3"]
assert all(r["pl_map"] == {} for r in runs10), \
    "검색어는 카테고리로 병합하지 않는다 — 빈 맵이어야 scan_playlists()도 백필도 돌지 않는다 (DQ-28)"
assert runs10[0]["date_range"] == {"since": "20260101", "until": None}, "ⓒ 2층 전달 (DQ-24)"
# 신규 등록 채널만 폴더 + auto_run:false (기존 chanA는 불변, FR25.7·FR34.6~34.7)
assert not reg10.set_group.called, "폴더 지정은 folder_ops 경유 (FR35.8)"
assert sorted(c.args[0] for c in grp10.call_args_list) == \
    ["UCyyyyyyyyyyyyyyyyyyyyyy", "chanC"]
assert all(c.args[1] == "AI 묶음" for c in grp10.call_args_list)
assert len(job10["warnings"]) == 1 and "건너뜀" in job10["warnings"][0], \
    f"충돌은 job 경고로만 노출되고 배치 추출은 계속된다 (FR35.13): {job10['warnings']}"
assert sorted(c.args for c in reg10.set_auto_run.call_args_list) == \
    [("UCyyyyyyyyyyyyyyyyyyyyyy", False), ("chanC", False)]
assert job10["status"] == "done" and job10["total"] == 3 and job10["done"] == 3
# 등식: total(3) == new(1) + live_wait(2) — 검색 경로는 live_wait를 포함한다 (DQ-26)
assert job10["stats"]["new"] == 1 and job10["stats"]["live_wait"] == 2, job10["stats"]
assert job10["total"] == sum(job10["stats"][k] for k in jobs._STAT_KEYS)
assert [c.args[0] for c in idx10.call_args_list] == ["chanA"]
assert job10["events"] == [{"id": "g1", "title": "긴 영상", "kind": "new",
                            "reason": "신규 추출", "channel": "chanA"}]
# 429 방어: 검색은 영상당 채널이 달라 그룹이 잘게 쪼개진다 → 휴식 상태 공유가 필수
assert all(r["rest"] is runs10[0]["rest"] for r in runs10) and runs10[0]["rest"] is not None, \
    "검색 그룹마다 rest_state가 새로 생기면 배치 휴식이 영영 오지 않는다 (FR14.2)"
assert runs10[0]["rest"]._cancel_check == mgr._cancel.is_set, "취소 중 휴식 조기 종료 배선"
print("✓ _run_search: 그룹 순차·진행율 합산·pl_map={} ·신규만 폴더+auto_run:false·조건부 인덱싱 (FR34.6~34.10)")

# start()가 검색 캐시를 search_run 워커로 라우팅하는지 (배선 확인)
entry10["created_at"] = time.time()
with mock.patch.object(mgr, "_run_search") as spy:
    job11 = mgr.start({"scan_id": res10["scan_id"], "filters": {}})
    for _ in range(200):
        if mgr.status()["status"] != "running" and not mgr._busy:
            break
        time.sleep(0.01)
assert job11["kind"] == "search_run" and spy.called, job11
print("✓ start(): kind=search 캐시 → search_run job + _run_search 워커 (FR34.9)")

print("\n검색 추출(FR34) 로직 검증 통과")

# ── 11. 배치 휴식이 그룹 경계를 넘어 누적된다 (FR14.2 · QA 결함 B) ───────────
# 검색 결과는 "영상 1개 = 채널 1개"로 흩어진다. 그룹마다 run()을 새로 부르므로
# 휴식 카운터가 run() 지역 변수면 batch_size(8~12)에 영영 도달하지 못한다.
config.BATCH_SIZE_RANGE = (3, 3)        # 결정적 검증용 (실제 기본값 (8,12))
config.BATCH_REST_RANGE = (45, 45)      #              (실제 기본값 (45,90))

def _mk_search_entry(n):
    vids = [{"id": f"s{i}", "title": f"영상{i}", "content_type": "video"} for i in range(n)]
    return {"kind": "search", "query": "휴식테스트", "folder": None,
            "videos_view": [dict(v, playlists=[], members_only=False, extracted=False)
                            for v in vids],
            "by_channel": {f"rch{i}": {"url": f"https://www.youtube.com/@rch{i}/videos",
                                       "entries": [vids[i]]} for i in range(n)}}

sleep11 = []
reg11 = mock.MagicMock()
reg11.names.return_value = [f"rch{i}" for i in range(9)]   # 전부 등록 → 등록 부작용 배제
reg11.get.side_effect = lambda n: {"name": n,
                                   "url": f"https://www.youtube.com/@{n}/videos",
                                   "lang": "ko"}
with mock.patch.object(_extractor_mod.Extractor, "process_video",
                       lambda self, vid, action="new", **kw: "ok"), \
     mock.patch.object(_extractor_mod.time, "sleep", lambda s: sleep11.append(s)), \
     mock.patch.object(jobs, "ChannelRegistry",
                       mock.MagicMock(return_value=reg11, extract_handle=str,
                                      normalize_url=lambda u: u)):
    job12 = mgr._new_job("search_run", "휴식테스트", "u")
    mgr._job = job12
    mgr._cancel.clear()
    mgr._run_grouped(job12, _mk_search_entry(9), {"include_members": True}, False,
                     group_title=None, merge_categories=False, auto_run=False)

assert job12["status"] == "done" and job12["stats"]["new"] == 9, job12
# 9영상·배치 3 → 4·7번째 영상 직전에 2회 휴식(45초). 취소 감시 때문에 1초씩 쪼개 잔다
assert set(sleep11) == {1}, f"휴식은 1초 단위로 쪼개져야 취소에 응답한다: {set(sleep11)}"
assert sum(sleep11) == 90, f"그룹이 9개로 쪼개져도 휴식 2회(45×2)가 와야 한다: {sum(sleep11)}"

# 대조군: rest_state를 공유하지 않으면(=결함 상태) 휴식이 한 번도 오지 않는다
sleep11b = []
with mock.patch.object(_extractor_mod.Extractor, "process_video",
                       lambda self, vid, action="new", **kw: "ok"), \
     mock.patch.object(_extractor_mod.time, "sleep", lambda s: sleep11b.append(s)):
    for i in range(9):
        _extractor_mod.Extractor(
            {"name": f"nrch{i}", "url": f"https://www.youtube.com/@nrch{i}/videos"}
        ).run(entries=[{"id": f"n{i}", "title": f"영상{i}"}], pl_map={})
assert sleep11b == [], f"대조군(비공유)은 휴식 0회여야 결함 재현이 성립한다: {sleep11b}"

# 취소 중에는 남은 휴식을 끊는다 (FR18.2 응답성)
cancelled_rest = _extractor_mod.BatchRest(cancel_check=lambda: True)
sleep11c = []
with mock.patch.object(_extractor_mod.time, "sleep", lambda s: sleep11c.append(s)):
    assert cancelled_rest.take() == 0
assert sleep11c == [], "취소 상태에서 45초를 자면 안 된다"
print("✓ 배치 휴식: 그룹 경계를 넘어 누적(45×2) · 비공유 대조군 0회 · 취소 시 즉시 중단 (FR14.2)")

print("\n429 배치 휴식(FR14.2) 크로스 그룹 검증 통과")


# ── 12. is_busy()가 마이그레이션 락을 OR 합산한다 (FR35.10) ──────────────────
# CLI 마이그레이션 컨테이너와 serve 컨테이너는 job 상태를 공유할 수 없다.
# output/.migration.lock 파일이 유일한 통로다 (DQ-11과 같은 수법).
mgr12 = JobManager()
assert mgr12.is_busy() is False
folder_ops.lock()
assert mgr12.is_busy() is True, "마이그레이션 중에는 추출·삭제·이름 변경이 전부 409여야 한다"
import os as _os
_stale = time.time() - (folder_ops.STALE_LOCK_SEC + 60)
_os.utime(folder_ops._lock_path(), (_stale, _stale))
assert mgr12.is_busy() is False, "6시간 초과 락은 stale로 무시(경고)"
folder_ops.unlock()
assert mgr12.is_busy() is False

# QA F-5: is_busy()만 락을 보면 삭제·이름 변경만 막히고 **스캔·추출은 그대로 통과**한다.
# 진입점은 _acquire()이므로 여기서도 락을 봐야 마이그레이션 중 평면 경로 재생성을 막는다.
folder_ops.lock()
try:
    try:
        mgr12._acquire()
        raise AssertionError("마이그레이션 락 중에는 _acquire()가 JobBusyError여야 한다")
    except JobBusyError:
        pass
    try:
        mgr12.scan("https://www.youtube.com/@ch/videos")
        raise AssertionError("마이그레이션 락 중에는 스캔이 409여야 한다")
    except JobBusyError:
        pass
finally:
    folder_ops.unlock()
mgr12._acquire(); mgr12._release()      # 락 해제 후에는 정상 취득 (점유 누수 없음)
print("✓ JobManager.is_busy/_acquire: 락 OR 합산 · stale 6h 무시 · 스캔 진입 차단 (FR35.10)")


# ── 13. 스캔 캐시 무효화 (FR36.8 · DQ-41) ────────────────────────────────────
# 이름 변경·삭제 뒤 옛 scan_id로 추출하면 `_run_channel`이 캐시의 옛 이름을 끝까지 써서
# `output/<옛이름>/` 유령 폴더를 만들거나(rename) 채널을 되살린다(delete).
# 캐시를 고쳐 쓰지 않고 **폐기**해 기존 400("만료")에 착지시킨다.
mgr13 = JobManager()


def _scan13(sid, channel=None, by_channel=None):
    e = {"scan_id": sid, "channel": channel, "url": "https://youtube.com/@x/videos",
         "videos_view": [], "entries": [], "pl_map": {}, "created_at": time.time()}
    if by_channel is not None:
        e["kind"] = "playlist"
        e["by_channel"] = {n: {"url": "", "entries": []} for n in by_channel}
    mgr13._scans[sid] = e


_scan13("s_ch", channel="대상")                                   # 채널 스캔
_scan13("s_pl", channel="재생목록제목", by_channel=["대상", "다른채널"])   # 재생목록/검색 스캔
_scan13("s_other", channel="다른채널")
assert mgr13.invalidate_scans(channel="대상") == 2, "channel·by_channel 양쪽 판정"
assert set(mgr13._scans) == {"s_other"}, "다른 채널 캐시는 남는다"
assert mgr13.invalidate_scans(channel="다른") == 0, "정확 일치 — 부분 문자열 아님"
try:
    mgr13.start({"scan_id": "s_ch", "filters": {}, "index": False})
    raise AssertionError("폐기된 scan_id는 400(만료)이어야 한다")
except ValueError as e:
    assert "만료" in str(e), e
assert mgr13.is_busy() is False, "400 경로에서 점유 누수 없음"
assert mgr13.invalidate_scans() == 1 and mgr13._scans == {}, "channel=None이면 전체 비움"
print("✓ JobManager.invalidate_scans: channel/by_channel 정확 일치 · 전체 비움 · 폐기 후 start 400 (FR36.8)")

print("\n스캔 캐시 무효화(FR36) 검증 통과")

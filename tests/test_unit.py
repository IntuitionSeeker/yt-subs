"""단위 검증 — V-U1~V-U11. 외부 네트워크 불필요."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

import pytest

import subtitle_utils as su
from channel_registry import ChannelRegistry
from state_manager import StateManager
import quality_checker as qc


# ─── V-U1: 파일명 형식·srt/txt 동일성 ────────────────────────────────────────
def test_make_basename():
    name = su.make_basename("20240315", "오늘의 분석!")
    assert name.startswith("20240315_")
    assert "/" not in name and ":" not in name
    # srt·txt 동일 베이스명 보장
    assert name == su.make_basename("20240315", "오늘의 분석!")


def test_sanitize_special_chars():
    out = su.sanitize("채널명/특수:문자*테스트?")
    assert all(c not in out for c in '/\\:*?"<>|')


# ─── V-U2: VTT → SRT 변환 ────────────────────────────────────────────────────
def test_vtt_to_srt():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
첫 번째 자막

00:00:03.000 --> 00:00:05.000
두 번째 자막
"""
    srt = su.vtt_to_srt(vtt)
    assert "1\n" in srt
    assert "00:00:01,000 --> 00:00:03,000" in srt
    assert "첫 번째 자막" in srt


def test_vtt_dedup():
    """자동생성 누적 중복 제거."""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
안녕

00:00:01.500 --> 00:00:03.500
안녕 반갑습니다
"""
    srt = su.vtt_to_srt(vtt)
    # "안녕"만 있는 불완전 블록은 제거되어야 함
    assert "안녕 반갑습니다" in srt


def test_vtt_dedup_sliding():
    """슬라이딩 롤링 캡션(F-7): 블록 꼬리 줄 == 다음 블록 머리 줄 → 각 줄이 한 번만 남는다."""
    vtt = """WEBVTT

00:00:03.280 --> 00:00:06.030
분석실입니다. 오늘은 클로드 코드에서
히든 세팅이라고 해서 얼마만큼 내가

00:00:06.040 --> 00:00:08.230
히든 세팅이라고 해서 얼마만큼 내가
지금 토큰을 사용하고 어떤

00:00:08.240 --> 00:00:10.030
지금 토큰을 사용하고 어떤
프로젝트인지 모델은 어떤 걸
"""
    txt = su.srt_to_txt(su.vtt_to_srt(vtt))
    # 슬라이딩 겹침 줄이 한 번씩만 남고 (F-7), 문장 단위로 재배치된다 (FR23)
    assert txt.count("히든 세팅이라고 해서 얼마만큼 내가") == 1
    assert txt.count("지금 토큰을 사용하고 어떤") == 1
    assert txt.splitlines()[0] == "분석실입니다."


def test_srt_to_txt():
    srt = "1\n00:00:01,000 --> 00:00:03,000\n자막 내용\n"
    txt = su.srt_to_txt(srt)
    assert txt == "자막 내용"
    assert "00:00" not in txt


# ─── FR23: 문장 단위 줄바꿈 (reflow) ─────────────────────────────────────────
def test_reflow_korean():
    """한국어: 문장 중간 줄바꿈 제거, 문장부호 뒤에서만 개행."""
    srt = ("1\n00:00:01,000 --> 00:00:03,000\n안녕하세요. 오늘은 클로드\n"
           "\n2\n00:00:03,000 --> 00:00:05,000\n코드에서 히든 세팅을 살펴봅니다. 시작하죠!\n")
    txt = su.srt_to_txt(srt)
    assert txt.splitlines() == [
        "안녕하세요.",
        "오늘은 클로드 코드에서 히든 세팅을 살펴봅니다.",
        "시작하죠!",
    ]


def test_reflow_english():
    """영어: 마침표·물음표 뒤 개행, 문장 중간 줄바꿈 제거."""
    srt = ("1\n00:00:01,000 --> 00:00:03,000\nWelcome back. Today we\n"
           "\n2\n00:00:03,000 --> 00:00:05,000\nlook at hidden settings. Ready?\n")
    txt = su.srt_to_txt(srt)
    assert txt.splitlines() == [
        "Welcome back.",
        "Today we look at hidden settings.",
        "Ready?",
    ]


def test_reflow_edge_cases():
    """무공백 한글 연결은 개행, 소수점·버전 표기는 보존."""
    assert su.reflow_sentences("소개하겠습니다.이 기능은 좋아요") == \
        "소개하겠습니다.\n이 기능은 좋아요"
    assert su.reflow_sentences("오퍼스 4.5 모델이 3.5배 빠릅니다") == \
        "오퍼스 4.5 모델이 3.5배 빠릅니다"


# ─── V-U6: SRT 120초 윈도우 청킹 ─────────────────────────────────────────────
def test_chunk_by_srt():
    srt = """1
00:00:00,000 --> 00:00:30,000
구간 A

2
00:01:00,000 --> 00:01:30,000
구간 B

3
00:02:30,000 --> 00:03:00,000
구간 C
"""
    chunks = su.chunk_by_srt(srt, window_sec=120)
    assert len(chunks) >= 2
    assert chunks[0]["start_sec"] == 0
    # start_seconds가 URL 링크 생성에 쓰임
    assert all("start_sec" in c for c in chunks)


def test_chunk_text():
    text = "첫 문장입니다. 두 번째 문장. 세 번째 문장."
    chunks = su.chunk_text(text, max_chars=1000)
    assert len(chunks) == 1
    assert "첫 문장" in chunks[0]


# ─── V-U7: URL → 채널명 추출 ─────────────────────────────────────────────────
def test_extract_handle():
    assert ChannelRegistry.extract_handle("https://youtube.com/@두두감자") == "두두감자"
    assert ChannelRegistry.extract_handle("https://youtube.com/@handle/videos") == "handle"


def test_extract_handle_encoded():
    # %EB%91%90%EB%91%90%EA%B0%90%EC%9E%90 = 두두감자
    url = "https://www.youtube.com/@%EB%91%90%EB%91%90%EA%B0%90%EC%9E%90"
    assert ChannelRegistry.extract_handle(url) == "두두감자"


def test_normalize_url():
    assert ChannelRegistry.normalize_url("https://youtube.com/@ch").endswith("/videos")
    assert ChannelRegistry.normalize_url("https://youtube.com/@ch/videos").endswith("/videos")


# ─── V-U4: 수정 감지 (mock state) ────────────────────────────────────────────
def test_is_updated(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    sm = StateManager("testch")
    sm.mark_done("vid1", {"upload_date": "20240101", "modified_date": "20240101",
                          "sub_type": "manual", "basename": "x"})
    # 동일 → skip
    assert sm.decide("vid1", "20240101", "20240101") == "skip"
    # 수정됨 → updated
    assert sm.decide("vid1", "20240202", "20240101") == "updated"
    # 신규 → new
    assert sm.decide("vid2", "20240101", "20240101") == "new"


# ─── FR16.5: 진행 중 라이브 가드 (단일 URL 경로) ─────────────────────────────
def test_live_in_progress_guard(tmp_path, monkeypatch):
    """진행/예약 라이브는 live_wait 반환 + state 미기록 → 종료 후 재추출 가능."""
    import sys
    import unittest.mock as mock
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    monkeypatch.setitem(sys.modules, "yt_dlp", mock.MagicMock())
    from extractor import Extractor

    ext = Extractor({"name": "livech", "url": "https://www.youtube.com/@livech/videos"})
    for status in ("is_live", "is_upcoming"):
        result = ext.process_video("LIVEVID0001", "new",
                                   info={"id": "LIVEVID0001", "title": "진행중 라이브",
                                         "live_status": status})
        assert result == "live_wait"
        assert "LIVEVID0001" not in ext.state.state   # 기록 없음 → 다음에 new로 재시도
    # 종료된 라이브(was_live)는 정상 경로로 진행되어야 함 — 자막 없음이면 no_sub
    result = ext.process_video("LIVEVID0001", "new",
                               info={"id": "LIVEVID0001", "title": "끝난 라이브",
                                     "live_status": "was_live", "upload_date": "20260101"})
    assert result == "no_sub"
    assert ext.state.state["LIVEVID0001"]["sub_type"] == "none"


# ─── V-U10: 멤버십 재시도 (FR19.1, DQ-10) ────────────────────────────────────
def test_members_only_retry(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    cookie = tmp_path / "cookies.txt"
    monkeypatch.setattr(config, "COOKIE_FILE", cookie)
    ff_dir = tmp_path / "firefox_profile"
    monkeypatch.setattr(config, "FIREFOX_PROFILE", ff_dir)

    sm = StateManager("testch")
    sm.state["m1"] = {"upload_date": "00000000", "modified_date": "members_only",
                      "sub_type": "members_only", "extracted_at": "", "basename": ""}
    sm.mark_done("v1", {"upload_date": "20240101", "modified_date": "20240101",
                        "sub_type": "auto", "basename": "x"})

    # 인증 수단 없음 → 기존대로 skip
    assert sm.decide("m1", None, None) == "skip"
    # 쿠키 파일 있음 → 매 run 재시도
    cookie.write_text("# netscape\n", encoding="utf-8")
    assert sm.decide("m1", None, None) == "updated"
    # 일반 항목은 영향 없음
    assert sm.decide("v1", None, None) == "skip"
    # 쿠키 파일 대신 Firefox 프로필만 있어도 재시도 (FR13.6 → FR19.1)
    cookie.unlink()
    assert sm.decide("m1", None, None) == "skip"
    ff_dir.mkdir()
    (ff_dir / "cookies.sqlite").write_bytes(b"")
    assert sm.decide("m1", None, None) == "updated"


# ─── FR13.6: Firefox 쿠키 직접 읽기 ──────────────────────────────────────────
def test_ydl_opts_firefox_priority(tmp_path, monkeypatch):
    """Firefox 프로필이 있으면 cookiesfrombrowser 사용, 없으면 cookiefile 폴백."""
    import sys
    import unittest.mock as mock
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    monkeypatch.setitem(sys.modules, "yt_dlp", mock.MagicMock())
    ff_dir = tmp_path / "firefox_profile"
    monkeypatch.setattr(config, "FIREFOX_PROFILE", ff_dir)
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# netscape\n", encoding="utf-8")
    monkeypatch.setattr(config, "COOKIE_FILE", cookie)
    monkeypatch.setattr(config, "COOKIE_WORKFILE", tmp_path / "work.txt")
    from extractor import Extractor

    ext = Extractor({"name": "ffch", "url": "https://www.youtube.com/@ffch/videos"})
    # Firefox 없음 → cookiefile 폴백
    opts = ext._ydl_opts(skip_download=True)
    assert "cookiesfrombrowser" not in opts and "cookiefile" in opts
    # Firefox 프로필 존재 → cookiesfrombrowser 우선, cookiefile 미사용
    ff_dir.mkdir()
    (ff_dir / "cookies.sqlite").write_bytes(b"")
    opts = ext._ydl_opts(skip_download=True)
    assert opts["cookiesfrombrowser"] == ("firefox", str(ff_dir), None, None)
    assert "cookiefile" not in opts


# ─── FR32.1: 제목 언어 고정 (DQ-20) ──────────────────────────────────────────
def test_ydl_opts_pins_title_language(tmp_path, monkeypatch):
    """채널 lang을 extractor_args.youtube.lang으로 고정 — 스캔·추출 제목 언어 일치."""
    import sys
    import unittest.mock as mock
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    monkeypatch.setattr(config, "FIREFOX_PROFILE", tmp_path / "no_ff")
    monkeypatch.setattr(config, "COOKIE_FILE", tmp_path / "no_cookie.txt")
    monkeypatch.setitem(sys.modules, "yt_dlp", mock.MagicMock())
    from extractor import Extractor

    ext = Extractor({"name": "langch", "url": "https://www.youtube.com/@langch/videos",
                     "lang": "ja"})
    assert ext._ydl_opts(extract_flat=True)["extractor_args"]["youtube"]["lang"] == ["ja"]
    # 채널 lang 미지정 → 기본 언어
    ext2 = Extractor({"name": "langch2", "url": "https://www.youtube.com/@langch2/videos"})
    assert (ext2._ydl_opts()["extractor_args"]["youtube"]["lang"]
            == [config.DEFAULT_LANG])
    # self 없는 호출 경로(jobs._probe_opts)도 기본 언어로 동작
    opts = Extractor._ydl_opts(None, skip_download=True)
    assert opts["extractor_args"]["youtube"]["lang"] == [config.DEFAULT_LANG]
    # 공유 상수를 오염시키지 않는다
    assert "extractor_args" not in config.YTDLP_COMMON


# ─── FR32.2: URL → 등록 채널명 역조회 (DQ-19) ────────────────────────────────
def test_resolve_name_prefers_registry(tmp_path):
    """등록명≠URL핸들이어도 등록명을 돌려준다 — 없는 폴더 조회·중복 추출 방지."""
    yml = tmp_path / "channels.yaml"
    reg = ChannelRegistry(yaml_path=yml)
    reg.data = {"channels": {
        "소수몽키": {"url": "https://www.youtube.com/@sosumonkey/videos", "lang": "ko"},
        "호두감자": {"url": "https://youtube.com/@두두감자/videos", "lang": "ko"},
    }}
    # 등록된 URL의 핸들로 조회 → 등록명
    assert reg.resolve_name("https://www.youtube.com/@sosumonkey/streams/videos") == "소수몽키"
    assert reg.resolve_name("https://youtube.com/@두두감자") == "호두감자"
    # 등록명 자체로 조회해도 등록명 (대소문자 무시)
    assert reg.resolve_name("https://www.youtube.com/@소수몽키") == "소수몽키"
    # 미등록 채널 → 기존 규칙(핸들) 폴백
    assert reg.resolve_name("https://www.youtube.com/@신규채널/videos") == "신규채널"
    # 핸들 추출 불가 URL은 기존대로 ValueError
    with pytest.raises(ValueError):
        reg.resolve_name("https://example.com/foo")


# ─── FR33.1~33.2: 증분 인덱싱 판정 (DQ-21) ───────────────────────────────────
class _FakeCol:
    """col.get(where=, include=)만 흉내내는 최소 스텁."""
    def __init__(self, rows):        # rows: {id: (doc, meta)}
        self.rows = rows
    def get(self, where=None, include=None):
        vid = (where or {}).get("video_id")
        items = [(i, d, m) for i, (d, m) in self.rows.items()
                 if m.get("video_id") == vid]
        return {"ids": [i for i, _, _ in items],
                "documents": [d for _, d, _ in items],
                "metadatas": [m for _, _, m in items]}


def test_unchanged_detects_identical_and_changes():
    from kl_indexer import KLIndexer
    ids = ["v1_0", "v1_1"]
    docs = ["첫 청크", "둘째 청크"]
    metas = [{"video_id": "v1", "title": "제목", "chunk_index": 0},
             {"video_id": "v1", "title": "제목", "chunk_index": 1}]
    same = _FakeCol({i: (d, m) for i, d, m in zip(ids, docs, metas)})
    assert KLIndexer._unchanged(same, "v1", ids, docs, metas) is True

    # 본문이 바뀌면 재임베딩 (청크 수가 같아도 잡아낸다)
    moved = _FakeCol({"v1_0": ("첫 청크", metas[0]),
                      "v1_1": ("다른 내용", metas[1])})
    assert KLIndexer._unchanged(moved, "v1", ids, docs, metas) is False

    # 메타(제목·카테고리)가 바뀌면 재임베딩 — 이름 변경 반영
    retitled = _FakeCol({"v1_0": (docs[0], {**metas[0], "title": "새 제목"}),
                         "v1_1": (docs[1], metas[1])})
    assert KLIndexer._unchanged(retitled, "v1", ids, docs, metas) is False

    # 청크 수가 다르면 재임베딩
    partial = _FakeCol({"v1_0": (docs[0], metas[0])})
    assert KLIndexer._unchanged(partial, "v1", ids, docs, metas) is False

    # 미인덱싱(빈 컬렉션) → 재임베딩
    assert KLIndexer._unchanged(_FakeCol({}), "v1", ids, docs, metas) is False

    # 조회 실패는 보수적으로 "변경됨"
    class Boom:
        def get(self, **kw): raise RuntimeError("chroma 손상")
    assert KLIndexer._unchanged(Boom(), "v1", ids, docs, metas) is False


# ─── V-U5: 품질 규칙 검토 ────────────────────────────────────────────────────
def test_quality_normal():
    text = "오늘은 삼성전자 주가 전망에 대해 분석해보겠습니다. " * 10
    verdict, reason, metrics = qc.check_rules(text)
    assert verdict == "OK"


def test_quality_too_short():
    verdict, reason, _ = qc.check_rules("짧은 자막")
    assert verdict == "SUSPECT"
    assert "단어수" in reason


def test_quality_repeated():
    text = "\n".join(["같은 문장입니다"] * 20)
    verdict, reason, _ = qc.check_rules(text)
    assert verdict == "SUSPECT"
    assert "반복" in reason


def test_korean_ratio():
    assert qc.korean_ratio("안녕하세요") > 0.9
    assert qc.korean_ratio("hello world") < 0.1


# ─── 종목 추출 (FR12.2) ──────────────────────────────────────────────────────
def test_extract_tickers():
    from meta_collector import extract_tickers
    text = "삼성전자 005930 와 $AAPL 그리고 $TSLA 분석"
    tickers = extract_tickers(text)
    assert "005930" in tickers
    assert "AAPL" in tickers
    assert "TSLA" in tickers


# ─── V-U11: URL 분류 (FR17.1) ────────────────────────────────────────────────
@pytest.mark.parametrize("url,kind,ident", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ"),
])
def test_classify_url_video(url, kind, ident):
    from jobs import classify_url
    assert classify_url(url) == (kind, ident)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@두두감자",
    "https://www.youtube.com/@handle/videos",
    "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
])
def test_classify_url_channel(url):
    from jobs import classify_url
    kind, value = classify_url(url)
    assert kind == "channel"
    assert value == url


def test_classify_url_invalid():
    from jobs import classify_url
    with pytest.raises(ValueError):
        classify_url("https://example.com/videos")


# ─── V-U11b: 재생목록 URL 분류 (FR24.1) ─────────────────────────────────────
def test_classify_url_playlist():
    from jobs import classify_url
    url = "https://www.youtube.com/playlist?list=PLnDn1H0jzj2irPsp9sy5HJZ-435yMOXy_"
    assert classify_url(url) == ("playlist", url)


def test_classify_url_watch_with_list_is_video():
    """watch?v=…&list=…는 재생목록이 아니라 단일 영상으로 처리한다 (FR24.1)."""
    from jobs import classify_url
    assert classify_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz"
    ) == ("video", "dQw4w9WgXcQ")


# ─── V-U16: 이름 변경 (FR31) ────────────────────────────────────────────────
def test_registry_rename(tmp_path):
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@옛채널")
    reg.set_group("옛채널", "폴더A")
    reg.rename("옛채널", "새채널")
    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert "옛채널" not in reg2.names()
    assert reg2.get("새채널")["group"] == "폴더A", "설정 보존"
    with pytest.raises(KeyError):
        reg2.rename("없는채널", "x")
    reg2.add("https://youtube.com/@다른채널")
    with pytest.raises(ValueError):
        reg2.rename("다른채널", "새채널")     # 중복 금지


def test_rename_channel_moves_folder(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "OUTPUT_BASE", tmp_path / "out")
    monkeypatch.setattr(cfg, "CHANNELS_YAML", tmp_path / "channels.yaml")
    import renamer
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@무브채널")
    old_dir = cfg.channel_dir("무브채널")
    (old_dir / "txt").mkdir(parents=True)
    (old_dir / "txt" / "a.txt").write_text("자막", encoding="utf-8")
    renamer.rename_channel("무브채널", "이동됨")
    assert not old_dir.exists()
    assert (cfg.channel_dir("이동됨") / "txt" / "a.txt").read_text(encoding="utf-8") == "자막"


def test_rename_video_and_category(tmp_path, monkeypatch):
    import json as _json
    import config as cfg
    monkeypatch.setattr(cfg, "OUTPUT_BASE", tmp_path / "out")
    import renamer
    monkeypatch.setattr(renamer, "_indexer",
                        lambda ch: type("Idx", (), {
                            "update_video_metadata": staticmethod(lambda vid, f: 3)})())
    meta_dir = cfg.channel_subdirs("ch1")["meta"]
    meta_dir.mkdir(parents=True)
    (meta_dir / "v.json").write_text(_json.dumps(
        {"id": "vid1", "title": "옛제목", "playlists": ["옛카테고리", "유지"]},
        ensure_ascii=False), encoding="utf-8")
    (cfg.channel_dir("ch1") / "playlists.json").write_text(
        _json.dumps({"vid1": ["옛카테고리", "유지"]}, ensure_ascii=False), encoding="utf-8")
    # 영상 제목
    res = renamer.rename_video_title("ch1", "v", "새제목")
    assert res == {"title": "새제목", "chunks": 3}
    meta = _json.loads((meta_dir / "v.json").read_text(encoding="utf-8"))
    assert meta["title"] == "새제목"
    # 카테고리 (다른 태그는 유지)
    res2 = renamer.rename_category(["ch1"], "옛카테고리", "새카테고리")
    assert res2["videos"] == 1
    meta = _json.loads((meta_dir / "v.json").read_text(encoding="utf-8"))
    assert meta["playlists"] == ["새카테고리", "유지"]
    pl = _json.loads((cfg.channel_dir("ch1") / "playlists.json").read_text(encoding="utf-8"))
    assert pl["vid1"] == ["새카테고리", "유지"]


# ─── V-U13: 챕터 정규화 (FR27.1) ────────────────────────────────────────────
def test_normalize_chapters():
    from meta_collector import MetaCollector
    raw = [{"start_time": 0.0, "end_time": 62.5, "title": "인트로"},
           {"start_time": 62.5, "end_time": 300, "title": " 본론 "},
           {"start_time": None, "end_time": None, "title": "무시안됨"},
           "깨진 항목"]
    got = MetaCollector._normalize_chapters(raw)
    assert got[0] == {"start": 0, "end": 62, "title": "인트로"}
    assert got[1] == {"start": 62, "end": 300, "title": "본론"}
    assert got[2] == {"start": 0, "end": 0, "title": "무시안됨"}
    assert len(got) == 3                       # dict 아닌 항목만 걸러짐
    assert MetaCollector._normalize_chapters(None) == []


# ─── V-U14: Whisper SRT 조립 (FR30.2) ───────────────────────────────────────
def test_segments_to_srt():
    from transcriber import segments_to_srt, _srt_ts
    assert _srt_ts(0) == "00:00:00,000"
    assert _srt_ts(3661.5) == "01:01:01,500"
    segs = [{"start": 0.0, "end": 2.5, "text": " 안녕하세요 "},
            {"start": 2.5, "end": 5.0, "text": ""},          # 빈 텍스트 제외
            {"start": 5.0, "end": 8.0, "text": "본문입니다"}]
    srt = segments_to_srt(segs)
    blocks = [b for b in srt.split("\n\n") if b.strip()]
    assert len(blocks) == 2
    assert "00:00:00,000 --> 00:00:02,500" in blocks[0]
    assert "안녕하세요" in blocks[0] and "본문입니다" in blocks[1]


# ─── V-U17: Whisper 전사 진행률 (FR30.6) ────────────────────────────────────
def test_transcribe_progress_percent():
    from transcriber import progress_percent
    assert progress_percent(30, 60) == 50
    assert progress_percent(60, 60) == 100
    assert progress_percent(90, 60) == 100          # 상한 클램프
    assert progress_percent(10, 0) == 0             # duration 미상 → 0 (나눗셈 금지)
    assert progress_percent(None, 60) == 0
    assert progress_percent("x", 60) == 0


def test_transcribe_progress_stream():
    """진행률을 내면서도 SRT 조립이 그대로 되는지 (세그먼트 1회 소비)."""
    from transcriber import with_progress, segments_to_srt
    segs = [{"start": 0.0, "end": 30.0, "text": "앞부분"},
            {"start": 30.0, "end": 60.0, "text": "뒷부분"}]

    seen = []
    srt = segments_to_srt(with_progress(segs, 60.0, seen.append))
    assert seen == [50, 100]
    assert "앞부분" in srt and "뒷부분" in srt

    # duration 미상이어도 전사는 정상 진행 (콜백은 0, 로그만 생략)
    seen2 = []
    srt2 = segments_to_srt(with_progress(segs, 0, seen2.append))
    assert seen2 == [0, 0]
    assert "앞부분" in srt2


def test_transcribe_progress_consumes_generator_once():
    """faster-whisper 세그먼트는 지연 생성자 — 1회 소비가 계약이다 (두 번 돌면 재전사)."""
    from transcriber import with_progress
    consumed = []

    def lazy():
        for end in (10.0, 20.0):
            consumed.append(end)
            yield {"start": end - 10, "end": end, "text": f"seg{end}"}

    out = list(with_progress(lazy(), 20.0))
    assert len(out) == 2
    assert consumed == [10.0, 20.0]


# ─── V-U15: RSS 피드 파싱 (FR29.2) ──────────────────────────────────────────
def test_rss_fetch_feed(monkeypatch):
    import rss_monitor
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <entry><yt:videoId>abc123def45</yt:videoId>
        <title>새 영상</title><published>2026-08-22T01:00:00+00:00</published></entry>
      <entry><yt:videoId>xyz987uvw65</yt:videoId>
        <title>둘째 영상</title><published>2026-08-21T01:00:00+00:00</published></entry>
    </feed>"""
    monkeypatch.setattr(rss_monitor, "_http_get", lambda url: xml)
    got = rss_monitor.fetch_feed("UCxxxx")
    assert got == [
        {"id": "abc123def45", "title": "새 영상", "published": "2026-08-22"},
        {"id": "xyz987uvw65", "title": "둘째 영상", "published": "2026-08-21"},
    ]


def test_rss_resolve_channel_id(monkeypatch):
    import rss_monitor
    # /channel/UC… URL은 요청 없이 즉시
    assert rss_monitor.resolve_channel_id(
        "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/videos"
    ) == "UCabcdefghijklmnopqrstuv"
    # @핸들은 HTML에서 해석
    monkeypatch.setattr(rss_monitor, "_http_get",
                        lambda url: '..."channelId":"UCzzzzzzzzzzzzzzzzzzzzzz"...')
    assert rss_monitor.resolve_channel_id(
        "https://www.youtube.com/@handle/videos") == "UCzzzzzzzzzzzzzzzzzzzzzz"


# ─── V-U12: 채널 폴더 (FR25.1) ──────────────────────────────────────────────
def test_registry_set_group(tmp_path):
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@폴더채널")
    # 지정 → yaml 재로드해도 유지
    assert reg.set_group("폴더채널", "AI 강의") == "AI 강의"
    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert reg2.get("폴더채널")["group"] == "AI 강의"
    # 빈 값 → 해제 (필드 제거)
    assert reg2.set_group("폴더채널", "  ") == ""
    reg3 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert "group" not in reg3.get("폴더채널")
    # 미등록 채널 → KeyError
    with pytest.raises(KeyError):
        reg3.set_group("없는채널", "x")

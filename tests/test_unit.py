"""단위 검증 — V-U1~V-U11. 외부 네트워크 불필요."""
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

import unittest.mock as mock

import pytest
import yaml

import config
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


# ─── V-U18: 검색 스캔 조립·필터 (FR34.1~34.4) ───────────────────────────────
def test_classify_url_search():
    """검색 결과 URL은 search로, 순수 텍스트는 계속 ValueError (DQ-27)."""
    from jobs import classify_url, search_query_from_url
    url = "https://www.youtube.com/results?search_query=AI+%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8"
    assert classify_url(url) == ("search", url)
    assert search_query_from_url(url) == "AI 에이전트"
    # 검색어가 @핸들이어도 채널로 오분류하지 않는다 (우선순위: …→검색→채널)
    assert classify_url(
        "https://www.youtube.com/results?search_query=@두두감자")[0] == "search"
    # 순수 텍스트는 검색으로 승격하지 않는다
    for bad in ("AI 에이전트", "그냥 검색어", "https://example.com/results"):
        with pytest.raises(ValueError):
            classify_url(bad)


def test_build_search_url_sp_presets():
    """sp 프리셋 매핑(6종 실측값) + quote_plus + 미지 period는 all 폴백 (FR34.4)."""
    from jobs import _build_search_url, SP_PRESETS
    assert SP_PRESETS == {"all": "EgIQAQ", "hour": "EgQIARAB", "today": "EgQIAhAB",
                          "week": "EgQIAxAB", "month": "EgQIBBAB", "year": "EgQIBRAB"}
    u = _build_search_url("AI 에이전트", "month")
    assert u == ("https://www.youtube.com/results"
                 "?search_query=AI+%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8&sp=EgQIBBAB")
    # 미지 값·None → all 폴백 (400이 아니다)
    assert _build_search_url("q", "지난주").endswith("sp=EgIQAQ")
    assert _build_search_url("q", None).endswith("sp=EgIQAQ")


def test_search_opts_and_params(monkeypatch):
    """playlist_items 상한 + _flat_opts 경유(lang 유지) + 조건 검증 (FR34.2·34.6)."""
    import jobs
    monkeypatch.setattr(jobs, "_flat_opts",
                        lambda: {"extract_flat": True,
                                 "extractor_args": {"youtube": {"lang": ["ko"]}}})
    opts = jobs._search_opts(7)
    assert opts["playlist_items"] == "1-7"
    assert opts["extract_flat"] is True
    assert opts["extractor_args"]["youtube"]["lang"] == ["ko"]   # DQ-20 유지

    # 기본값·폴더 기본값(=검색어)
    p = jobs.normalize_search_params(None, None, None, None, q="AI")
    assert p == {"limit": 20, "min_duration": 180, "period": "all", "folder": "AI"}
    assert jobs.normalize_search_params(5, 0, "year", " 강의 ", q="AI")["folder"] == "강의"
    for bad in ((0, None), (51, None), (None, -1)):
        with pytest.raises(ValueError):
            jobs.normalize_search_params(bad[0], bad[1], "all", None, q="AI")


def test_search_duration_filter(tmp_path, monkeypatch):
    """ⓑ N초 미만 제외 — 미만 제외 / 이상 통과 / **결측 통과** (FR34.3·DQ-23)."""
    import config
    import jobs
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    monkeypatch.setattr(jobs, "ChannelRegistry",
                        lambda *a, **k: ChannelRegistry(yaml_path=tmp_path / "c.yaml"))
    entries = [
        {"id": "s1", "title": "짧은 영상", "uploader_id": "@chanA", "duration": 60},
        {"id": "s2", "title": "긴 영상", "uploader_id": "@chanA", "duration": 600},
        {"id": "s3", "title": "길이 없음", "uploader_id": "@chanA"},
        {"id": "s4", "title": "경계값", "uploader_id": "@chanA", "duration": 180},
    ]
    view, by_ch = jobs._group_flat_entries(entries, min_duration=180)
    assert [v["id"] for v in view] == ["s2", "s3", "s4"], "60초만 제외, 결측·경계는 통과"
    assert view[0]["duration"] == 600 and view[1]["duration"] is None
    assert view[0]["channel"] == "chanA" and view[0]["extracted"] is False
    # 임계 0(끔)이면 전량 통과
    assert len(jobs._group_flat_entries(entries, min_duration=0)[0]) == 4
    assert set(by_ch) == {"chanA"}


# ─── V-U19: auto_run 플래그 (FR34.7) ────────────────────────────────────────
def test_registry_auto_run(tmp_path):
    """False는 기록·True는 필드 제거·부재는 True 간주 + names() 회귀 (DQ-25)."""
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@검색유입")
    reg.add("https://youtube.com/@평범채널")
    assert "auto_run" not in reg.get("검색유입"), "기본은 필드 없음"

    assert reg.set_auto_run("검색유입", False) is False
    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert reg2.get("검색유입")["auto_run"] is False
    # names()의 기본 동작은 불변 (jobs.py 등록 여부 확인이 이 계약에 의존)
    assert reg2.names() == ["검색유입", "평범채널"]
    assert reg2.names(auto_only=True) == ["평범채널"]

    # True → 필드 제거 (기본값을 yaml에 남기지 않는다)
    assert reg2.set_auto_run("검색유입", True) is True
    reg3 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert "auto_run" not in reg3.get("검색유입")
    assert reg3.names(auto_only=True) == ["검색유입", "평범채널"]
    with pytest.raises(KeyError):
        reg3.set_auto_run("없는채널", False)


def test_registry_rename_keeps_auto_run(tmp_path):
    """이름 변경이 auto_run을 잃어버리면 검색 유입 채널이 run 순회로 복귀한다 (FR31.1)."""
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@old")
    reg.set_group("old", "검색묶음")
    reg.set_auto_run("old", False)
    reg.rename("old", "new")
    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert reg2.get("new")["auto_run"] is False
    assert reg2.get("new")["group"] == "검색묶음"


def test_cmd_run_targets(tmp_path):
    """대상 산출: 인자 없으면 제외 / 채널 명시하면 포함. run·transcribe 공용 (FR34.7)."""
    import main
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@검색유입")
    reg.add("https://youtube.com/@평범채널")
    reg.set_auto_run("검색유입", False)
    assert main.bulk_targets(reg, None) == ["평범채널"]
    assert main.bulk_targets(reg, "검색유입") == ["검색유입"], "명시 지정은 플래그 무시"


# ─── V-U20: 영상 길이 노출 (FR20.5~20.6) ────────────────────────────────────
def test_list_videos_exposes_duration(tmp_path, monkeypatch):
    """meta의 duration/duration_string 통과 · 키 없으면 None/""(0 아님, DQ-29)."""
    import json
    import config
    from kl_query import KLQuery
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    meta_dir = config.channel_subdirs("ch")["meta"]
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "a.json").write_text(json.dumps(
        {"id": "v1", "title": "길이 있음", "upload_date": "20260102",
         "duration": 610, "duration_string": "10:10"}), encoding="utf-8")
    (meta_dir / "b.json").write_text(json.dumps(
        {"id": "v2", "title": "옛 meta", "upload_date": "20260101"}), encoding="utf-8")
    mtimes = {p.name: p.stat().st_mtime_ns for p in meta_dir.glob("*.json")}

    vids = {v["video_id"]: v for v in KLQuery("ch").list_videos()}
    assert vids["v1"]["duration"] == 610 and vids["v1"]["duration_string"] == "10:10"
    assert vids["v2"]["duration"] is None, "결측을 0으로 채우면 0초 영상과 구분 불가"
    assert vids["v2"]["duration_string"] == ""
    # 백필·재기록이 일어나지 않는다 (DQ-29, V-D16 전제)
    assert {p.name: p.stat().st_mtime_ns for p in meta_dir.glob("*.json")} == mtimes


# 프론트 fmtDuration의 케이스표 — (duration, duration_string, 기대 출력).
# 기대값은 node로 실제 JS를 실행해 확정한 값이다(아래 두 테스트가 각각 지킨다).
FMT_DURATION_CASES = [
    (0, None, "0:00"), (5, None, "0:05"), (61, None, "1:01"), (610, None, "10:10"),
    (3600, None, "1:00:00"), (3725, None, "1:02:05"), (86399, None, "23:59:59"),
    (None, None, ""), ("", None, ""), (-1, None, ""),
    (999, "12:34", "12:34"), (None, "1:02:03", "1:02:03"),
]

# index.html의 fmtDuration 고정 사본. 프론트가 드리프트하면 아래 테스트가 깨지고,
# 그때 node 교차 실행 테스트로 케이스표를 다시 확정해야 한다 (자기 검증 방지).
FMT_DURATION_SRC = """function fmtDuration(sec, str) {
  if (str) return String(str);
  if (sec === null || sec === undefined || sec === "") return "";
  const s = Math.floor(Number(sec));
  if (!isFinite(s) || s < 0) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = n => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`;
}"""


def _fmt_duration_source() -> str:
    """index.html에서 실제 fmtDuration 정의를 그대로 떼어 온다."""
    html = (Path(__file__).parent.parent / "dashboard" / "index.html").read_text(
        encoding="utf-8")
    m = re.search(r"^function fmtDuration\(.*?^\}", html, re.S | re.M)
    assert m, "index.html에서 fmtDuration 정의를 찾지 못했다"
    return m.group(0)


def test_fmt_duration_frontend_pinned():
    """프론트 구현이 케이스표가 검증한 바로 그 코드인지 고정 (FR20.6).

    파이썬 참조 구현을 테스트 안에 다시 쓰면 자기 자신을 검증할 뿐 index.html의
    드리프트를 못 잡는다 → 실제 원문을 읽어 대조한다.
    """
    assert _fmt_duration_source() == FMT_DURATION_SRC, (
        "index.html의 fmtDuration이 바뀌었다. "
        "test_fmt_duration_runs_in_node(node 필요)로 케이스표를 재확정한 뒤 갱신하라.")


def test_fmt_duration_runs_in_node():
    """실제 JS를 실행해 케이스표와 대조 (node 없는 이미지에서는 위 고정 테스트가 가드)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 미설치 — test_fmt_duration_frontend_pinned가 드리프트를 막는다")
    script = (_fmt_duration_source() +
              "\nconst cs = JSON.parse(process.argv[1]);"
              "\nconsole.log(JSON.stringify(cs.map(c => fmtDuration(c[0], c[1]))));")
    args = json.dumps([[c[0], c[1]] for c in FMT_DURATION_CASES])
    out = subprocess.run([node, "-e", script, args],
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [c[2] for c in FMT_DURATION_CASES], out.stdout


# ─── V-U21: 배치 휴식 상태 공유 (FR14.2) ────────────────────────────────────
def _batch_rest(monkeypatch, tmp_path):
    """extractor 모듈을 yt_dlp 스텁으로 로드."""
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    monkeypatch.setitem(sys.modules, "yt_dlp", mock.MagicMock())
    import extractor as ex
    return ex


def test_batch_rest_randomizes_every_time(tmp_path, monkeypatch):
    """배치 크기·휴식 시간을 매번 재추첨한다 — 고정 주기는 차단 탐지의 기계 서명."""
    ex = _batch_rest(monkeypatch, tmp_path)
    slept = []
    monkeypatch.setattr(ex.time, "sleep", lambda s: slept.append(s))
    r = ex.BatchRest()
    assert config.BATCH_SIZE_RANGE[0] <= r.size <= config.BATCH_SIZE_RANGE[1]
    sizes = set()
    for _ in range(30):
        while not r.due():
            r.count()
        assert r.since == r.size
        r.take()
        assert r.since == 0, "휴식 후 카운터 리셋"
        sizes.add(r.size)
    rests = set(slept)
    assert len(slept) == 30 and len(rests) > 1, "휴식 시간이 고정됐다"
    assert all(config.BATCH_REST_RANGE[0] <= s <= config.BATCH_REST_RANGE[1] for s in rests)
    assert len(sizes) > 1, "배치 크기가 고정됐다"
    assert r.rests == 30


def test_batch_rest_cancel_interrupts_sleep(tmp_path, monkeypatch):
    """취소 중에는 남은 휴식을 끊는다 (FR18.2 응답성). CLI 기본값은 단일 sleep."""
    ex = _batch_rest(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "BATCH_REST_RANGE", (60, 60))
    slept = []
    monkeypatch.setattr(ex.time, "sleep", lambda s: slept.append(s))

    # cancel_check 없음(CLI) → 기존과 동일하게 한 번에 60초
    ex.BatchRest().take()
    assert slept == [60], slept

    slept.clear()
    ticks = {"n": 0}

    def cancel_after_3():
        ticks["n"] += 1
        return ticks["n"] > 3          # 3틱 뒤 취소

    assert ex.BatchRest(cancel_check=cancel_after_3).take() == 3
    assert slept == [1, 1, 1], slept


def test_rest_state_accumulates_across_run_calls(tmp_path, monkeypatch):
    """그룹마다 run()을 새로 불러도 휴식 카운터가 누적된다 (FR14.2 · 검색 추출).

    run() 지역 변수로 두면 '영상 1개 = 채널 1개'인 검색 결과에서 배치 크기에
    영영 도달하지 못해 배치 휴식이 통째로 사라진다.
    """
    ex = _batch_rest(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "BATCH_SIZE_RANGE", (3, 3))
    monkeypatch.setattr(config, "BATCH_REST_RANGE", (45, 45))
    monkeypatch.setattr(ex.Extractor, "process_video",
                        lambda self, vid, action="new", **kw: "ok")
    slept = []
    monkeypatch.setattr(ex.time, "sleep", lambda s: slept.append(s))

    def run_group(i, rest_state):
        ex.Extractor({"name": f"ch{i}",
                      "url": f"https://www.youtube.com/@ch{i}/videos"}).run(
            entries=[{"id": f"v{i}", "title": f"영상{i}"}], pl_map={},
            rest_state=rest_state)

    shared = ex.BatchRest()
    for i in range(9):
        run_group(i, shared)
    assert slept == [45, 45], f"9영상·배치 3 → 휴식 2회: {slept}"
    assert shared.rests == 2

    # 대조군(공유 없음) = 결함 상태: 휴식이 한 번도 오지 않는다
    slept.clear()
    for i in range(100, 109):
        run_group(i, None)
    assert slept == [], f"비공유 경로는 휴식 0회(결함 재현): {slept}"


def test_run_without_rest_state_keeps_cli_behaviour(tmp_path, monkeypatch):
    """단일 채널 CLI 경로는 기존과 동일 — 배치 크기 도달 시 한 번에 휴식 (FR18.1)."""
    ex = _batch_rest(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "BATCH_SIZE_RANGE", (3, 3))
    monkeypatch.setattr(config, "BATCH_REST_RANGE", (45, 45))
    monkeypatch.setattr(ex.Extractor, "process_video",
                        lambda self, vid, action="new", **kw: "ok")
    slept = []
    monkeypatch.setattr(ex.time, "sleep", lambda s: slept.append(s))
    entries = [{"id": f"c{i}", "title": f"영상{i}"} for i in range(9)]
    stats = ex.Extractor({"name": "clich",
                          "url": "https://www.youtube.com/@clich/videos"}).run(
        entries=entries, pl_map={})
    assert stats["new"] == 9
    assert slept == [45, 45], f"쪼개지 않은 단일 sleep 2회여야 한다: {slept}"


# ════════════════════════════════════════════════════════════════════════════
# FR35 — 폴더(그룹)의 실제 디렉터리 승격 (V-U22~V-U27)
# ════════════════════════════════════════════════════════════════════════════
def _isolate(tmp_path, monkeypatch):
    """config 경로를 tmp로 격리 — 실데이터(output/·channels.yaml)를 절대 건드리지 않는다."""
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "OUTPUT_BASE", out)
    monkeypatch.setattr(config, "CHANNELS_YAML", tmp_path / "channels.yaml")
    config.invalidate_group_cache()
    return out


def _mkchannel(base: Path, *parts, files=("state.json",)) -> Path:
    d = base.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("{}", encoding="utf-8")
    (d / "srt").mkdir(exist_ok=True)
    (d / "srt" / "a.srt").write_text("자막", encoding="utf-8")
    return d


# ─── V-U22: channel_dir 그룹 해석·캐시 (FR35.1~35.3, DQ-32) ──────────────────
def test_channel_dir_resolves_group(tmp_path, monkeypatch):
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@chanA")
    reg.add("https://youtube.com/@chanB")
    # 그룹 없음 → 평면
    assert config.channel_dir("chanA") == out / "chanA"
    # 그룹 지정 → 1단계 중첩, _save()가 캐시를 즉시 무효화한다 (2차 안전망)
    reg.set_group("chanA", "역배열1")
    assert config.channel_dir("chanA") == out / "역배열1" / "chanA"
    assert config.channel_dir("chanB") == out / "chanB"
    # 하위 구조는 전혀 바뀌지 않는다
    assert config.channel_subdirs("chanA")["srt"] == out / "역배열1" / "chanA" / "srt"
    # 미등록 채널 → 평면
    assert config.channel_dir("미등록채널") == out / "미등록채널"
    # yaml 부재 → 빈 맵 폴백 (예외 없음)
    (tmp_path / "channels.yaml").unlink()
    config.invalidate_group_cache()
    assert config.channel_dir("chanA") == out / "chanA"


def test_group_cache_follows_yaml_mtime(tmp_path, monkeypatch):
    """1차 무효화는 mtime+size 자동 감지 — CLI와 serve가 별개 프로세스라 필수 (DQ-32)."""
    out = _isolate(tmp_path, monkeypatch)
    yml = tmp_path / "channels.yaml"
    yml.write_text("channels:\n  chanA:\n    group: 폴더하나\n", encoding="utf-8")
    assert config.channel_dir("chanA") == out / "폴더하나" / "chanA"
    # invalidate_group_cache()를 호출하지 않고 외부에서 yaml을 바꾼다 (다른 프로세스 모사)
    yml.write_text("channels:\n  chanA:\n    group: 폴더둘둘둘\n", encoding="utf-8")
    import os as _os
    st = yml.stat()                       # 저해상도 mtime FS에서도 결정적이도록 1초 전진
    _os.utime(yml, (st.st_atime + 1, st.st_mtime + 1))
    assert config.channel_dir("chanA") == out / "폴더둘둘둘" / "chanA", "mtime 자동 감지 실패"


def test_group_cache_hit_does_not_reparse(tmp_path, monkeypatch):
    """캐시 히트 경로의 I/O는 stat() 1회 — 호출마다 yaml 파싱 금지 (FR35.3)."""
    import yaml as _yaml
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "channels.yaml").write_text("channels:\n  c:\n    group: G\n",
                                            encoding="utf-8")
    calls = []
    orig = _yaml.safe_load
    monkeypatch.setattr(_yaml, "safe_load", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    for _ in range(5):
        config.channel_dir("c")
    assert len(calls) == 1, f"yaml 재파싱 {len(calls)}회 — 캐시가 동작하지 않는다"


def test_channel_dir_bad_group_falls_back_flat(tmp_path, monkeypatch):
    """읽기 경로에서 불량 group은 예외가 아니라 평면 폴백 — 라이브러리가 죽으면 안 된다 (FR35.5)."""
    out = _isolate(tmp_path, monkeypatch)
    (tmp_path / "channels.yaml").write_text(
        'channels:\n  chanA:\n    group: "../탈출"\n', encoding="utf-8")
    assert config.channel_dir("chanA") == out / "chanA"
    # 채널 세그먼트는 폴백할 곳이 없으므로 ValueError (FR7.9가 선행 차단하는 백스톱)
    with pytest.raises(ValueError):
        config.channel_dir("../탈출")


# ─── V-U23: validate_path_segment 거부표 (FR35.4·FR7.9, DQ-33) ───────────────
@pytest.mark.parametrize("bad", [
    "", "   ", ".", "..", "../탈출", "a/b", "a\\b", "/절대경로", "C:", "C:\\temp",
    "\\\\서버\\공유", ".숨김", "끝점.", "제어\x00문자", "탭\t포함", "벨\x07",
    "삭제\x7f", "x" * 65, "CON", "con", "com1", "LPT9", "NUL", "aux",
])
def test_validate_path_segment_rejects(bad):
    with pytest.raises(ValueError):
        config.validate_path_segment(bad)


def test_validate_path_segment_accepts_and_normalizes():
    assert config.validate_path_segment("  역배열1  ") == "역배열1"
    assert config.validate_path_segment("AI LLM Wiki") == "AI LLM Wiki"
    assert config.validate_path_segment("김민겸(퀀트)") == "김민겸(퀀트)"
    assert config.validate_path_segment("CONNECT-AI-LAB") == "CONNECT-AI-LAB"
    assert config.validate_path_segment("a.b.c") == "a.b.c"      # 중간 점은 허용
    # NFC 정규화 — NFD 입력도 같은 값으로 수렴한다 (디스크 1:1 불변식)
    import unicodedata
    nfd = unicodedata.normalize("NFD", "역배열")
    assert nfd != "역배열"
    assert config.validate_path_segment(nfd) == "역배열"


def test_rejected_group_creates_nothing_on_disk(tmp_path, monkeypatch):
    """거부 시 디스크에 아무것도 만들지 않는다 (FR35.4) — output/ 밖도 안도."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@chanA")
    _mkchannel(out, "chanA")
    before = sorted(p.name for p in out.iterdir())
    outside_before = sorted(p.name for p in tmp_path.iterdir())
    for bad in ("../탈출", "/etc", "a/b", ".숨김", "CON", "제어\x01문자"):
        with pytest.raises(ValueError):
            folder_ops.set_channel_group("chanA", bad)
    assert sorted(p.name for p in out.iterdir()) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == outside_before
    assert "group" not in reg.get("chanA") if "chanA" in reg.names() else True


# ─── V-U24: 최상위 이름공간 유일성 (FR35.6, DQ-34) ───────────────────────────
def test_check_namespace_four_directions(tmp_path, monkeypatch):
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@평면채널")
    reg.add("https://youtube.com/@그룹채널")
    reg.set_group("그룹채널", "묶음")
    # ⓐ 새 그룹명 ∩ 미지정 채널명 → 충돌
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.check_namespace(new_group="평면채널")
    # ⓒ 새 채널명 ∩ 기존 그룹명 → 충돌
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.check_namespace(new_channel="묶음")
    # ⓐ 동명 최상위 디렉터리가 채널형(state.json/srt/meta)이면 충돌 (미등록 잔존 폴더)
    _mkchannel(out, "잔존폴더")
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.check_namespace(new_group="잔존폴더")
    # 채널형이 아닌 빈 디렉터리는 그룹명으로 허용
    (out / "빈폴더").mkdir()
    folder_ops.check_namespace(new_group="빈폴더")
    # 충돌 없음
    folder_ops.check_namespace(new_group="새묶음")
    folder_ops.check_namespace(new_channel="새채널")


def test_check_namespace_casefold_and_nfc(tmp_path, monkeypatch):
    """APFS는 대소문자·정규화 비민감 — yaml엔 둘, 디스크엔 하나인 상태를 막는다 (U-2)."""
    import unicodedata
    import folder_ops
    _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@abc")
    reg.add("https://youtube.com/@other")
    reg.set_group("other", "역배열1")
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.check_namespace(new_group="ABC")          # casefold 충돌
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.check_namespace(new_group="AbC")
    with pytest.raises(folder_ops.ConflictError):            # NFD 충돌
        folder_ops.check_namespace(new_channel=unicodedata.normalize("NFD", "역배열1"))


def test_group_same_name_as_member_channel_allowed(tmp_path, monkeypatch):
    """ⓓ output/G/G — 중첩 레벨이 달라 실제 충돌이 아니다 (FR35.6ⓓ)."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@AILLMWiki")
    reg.add("https://youtube.com/@other")
    reg.set_group("other", "AILLMWiki")                      # 그룹 AILLMWiki 생성
    reg.set_group("AILLMWiki", "임시")                        # 동명 채널을 다른 그룹에
    _mkchannel(out, "임시", "AILLMWiki")
    res = folder_ops.set_channel_group("AILLMWiki", "AILLMWiki")
    assert res["moved"] is True
    assert (out / "AILLMWiki" / "AILLMWiki" / "state.json").exists()
    assert not (out / "임시").exists(), "비워진 원본 그룹 폴더는 rmdir"


# ─── V-U25: 이동 원자성·보상 롤백 (FR35.7~35.8, DQ-35) ───────────────────────
def test_move_channel_dir_rejects_existing_dst(tmp_path, monkeypatch):
    """POSIX rename은 빈 디렉터리를 무음 교체한다 → 목적지 존재 시 시도조차 하지 않는다."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    src = _mkchannel(out, "chanA")
    dst = out / "G" / "chanA"
    dst.mkdir(parents=True)
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.move_channel_dir(src, dst)
    assert (src / "state.json").exists(), "원본 무변경"


def test_move_channel_dir_rejects_exdev(tmp_path, monkeypatch):
    """파일시스템 경계(EXDEV)는 복사 폴백 없이 실패 — 반쯤 옮긴 상태가 최악이다."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    src = _mkchannel(out, "chanA")
    monkeypatch.setattr(folder_ops, "_dev",
                        lambda p: 1 if Path(p) == src else 2)
    with pytest.raises(folder_ops.MoveError):
        folder_ops.move_channel_dir(src, out / "G" / "chanA")
    assert (src / "state.json").exists() and not (out / "G").exists()


def test_move_channel_dir_no_op_when_missing(tmp_path, monkeypatch):
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    assert folder_ops.move_channel_dir(out / "없음", out / "G" / "없음") is False
    assert not (out / "G").exists()


def test_set_channel_group_moves_and_releases(tmp_path, monkeypatch):
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@chanA")
    _mkchannel(out, "chanA")
    # 지정
    res = folder_ops.set_channel_group("chanA", "역배열1")
    assert res["moved"] is True
    assert (out / "역배열1" / "chanA" / "srt" / "a.srt").read_text(encoding="utf-8") == "자막"
    assert not (out / "chanA").exists()
    assert config.channel_dir("chanA") == out / "역배열1" / "chanA"
    # 변경
    folder_ops.set_channel_group("chanA", "새폴더")
    assert (out / "새폴더" / "chanA" / "state.json").exists()
    assert not (out / "역배열1").exists(), "비워진 원본 그룹 폴더만 rmdir"
    # 해제
    res = folder_ops.set_channel_group("chanA", "")
    assert res["moved"] is True and res["group"] == ""
    assert (out / "chanA" / "state.json").exists() and not (out / "새폴더").exists()
    assert "group" not in ChannelRegistry().get("chanA")
    # 미추출 채널 → 이동 no-op, yaml만 기록
    reg2 = ChannelRegistry()
    reg2.add("https://youtube.com/@미추출")
    res = folder_ops.set_channel_group("미추출", "폴더X")
    assert res["moved"] is False
    assert ChannelRegistry().get("미추출")["group"] == "폴더X"
    with pytest.raises(KeyError):
        folder_ops.set_channel_group("없는채널", "폴더X")


def test_set_channel_group_compensating_rollback(tmp_path, monkeypatch):
    """yaml 기록이 실패하면 역방향 rename으로 원상 복구한다 (DQ-35)."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@chanA")
    _mkchannel(out, "chanA")

    def boom(self, name, group=None):
        raise RuntimeError("yaml 기록 실패 주입")
    monkeypatch.setattr(ChannelRegistry, "set_group", boom)
    with pytest.raises(RuntimeError):
        folder_ops.set_channel_group("chanA", "역배열1")
    assert (out / "chanA" / "srt" / "a.srt").exists(), "보상 롤백으로 원상 복구"
    assert not (out / "역배열1").exists()


def test_set_channel_group_conflict_and_skip(tmp_path, monkeypatch):
    """자동 폴더 지정 경로는 거부 대신 건너뛴다 (FR35.13)."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@평면채널")
    reg.add("https://youtube.com/@chanB")
    _mkchannel(out, "chanB")
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.set_channel_group("chanB", "평면채널")
    res = folder_ops.set_channel_group("chanB", "평면채널", on_conflict="skip")
    assert res["skipped"] and res["moved"] is False
    assert (out / "chanB" / "state.json").exists(), "건너뛴 채널은 최상위에 남는다"


def test_rename_group_single_rename(tmp_path, monkeypatch):
    import folder_ops
    import renamer
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    for n in ("c1", "c2"):
        reg.add(f"https://youtube.com/@{n}")
        reg.set_group(n, "옛폴더")
        _mkchannel(out, "옛폴더", n)
    (out / "옛폴더" / "미등록잔존").mkdir()          # 같은 폴더이므로 함께 따라간다
    assert renamer.rename_folder("옛폴더", "새폴더") == 2
    assert not (out / "옛폴더").exists()
    assert (out / "새폴더" / "c1" / "state.json").exists()
    assert (out / "새폴더" / "미등록잔존").exists()
    assert ChannelRegistry().get("c2")["group"] == "새폴더"
    assert config.channel_dir("c1") == out / "새폴더" / "c1"
    with pytest.raises(ValueError):
        renamer.rename_folder("새폴더", "../탈출")


def test_rename_channel_stays_inside_group(tmp_path, monkeypatch):
    """개명 목적지는 old_dir.parent/new — channel_dir(new)는 평면 경로다 (FR35.9 함정)."""
    import renamer
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@옛이름")
    reg.set_group("옛이름", "역배열1")
    _mkchannel(out, "역배열1", "옛이름")
    renamer.rename_channel("옛이름", "새이름")
    assert (out / "역배열1" / "새이름" / "srt" / "a.srt").exists()
    assert not (out / "새이름").exists(), "그룹 밖으로 튀어나가면 안 된다"
    assert config.channel_dir("새이름") == out / "역배열1" / "새이름"


def test_rename_channel_rejects_group_name(tmp_path, monkeypatch):
    """ⓒ 그룹 미지정 채널의 새 이름이 기존 그룹명과 충돌 → ConflictError (FR35.6ⓒ)."""
    import folder_ops
    import renamer
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@평면채널")
    reg.add("https://youtube.com/@그룹채널")
    reg.set_group("그룹채널", "역배열1")
    _mkchannel(out, "평면채널")
    with pytest.raises(folder_ops.ConflictError):
        renamer.rename_channel("평면채널", "역배열1")
    assert (out / "평면채널" / "state.json").exists()
    assert "평면채널" in ChannelRegistry().names()


# ─── V-U26: add() upsert (FR7.7~7.9, DQ-37) ──────────────────────────────────
def test_add_upsert_preserves_settings(tmp_path):
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@업서트", lang="ko", note="메모")
    reg.set_group("업서트", "역배열1")
    reg.set_auto_run("업서트", False)
    reg.set_channel_id("업서트", "UCabcdefghijklmnopqrstu")
    added_at = reg.get("업서트")["added_at"]

    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    name = reg2.add("https://youtube.com/@업서트/videos", lang="en")
    assert name == "업서트"
    ch = ChannelRegistry(yaml_path=tmp_path / "channels.yaml").get("업서트")
    assert ch["group"] == "역배열1", "group 소실 = 채널 디스크 경로가 바뀌는 사고 (FR35)"
    assert ch["auto_run"] is False and ch["channel_id"] == "UCabcdefghijklmnopqrstu"
    assert ch["added_at"] == added_at and ch["note"] == "메모", "빈 note는 덮지 않는다"
    assert ch["lang"] == "en" and ch["url"].endswith("/videos")


def test_add_uses_resolve_name_for_renamed_channel(tmp_path):
    """등록명≠URL핸들 채널을 다시 add해도 두 번째 항목이 생기지 않는다 (FR7.8)."""
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@oldhandle")
    reg.rename("oldhandle", "표시이름")
    before = len(reg.names())
    name = ChannelRegistry(yaml_path=tmp_path / "channels.yaml").add(
        "https://youtube.com/@oldhandle")
    assert name == "표시이름"
    reg2 = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    assert len(reg2.names()) == before and "oldhandle" not in reg2.names()


def test_add_and_rename_reject_bad_names(tmp_path):
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@정상채널")
    with pytest.raises(ValueError):
        reg.add("https://youtube.com/@..")               # 경로 탈출 핸들 (FR7.9)
    with pytest.raises(ValueError):
        reg.rename("정상채널", "../탈출")
    assert ChannelRegistry(yaml_path=tmp_path / "channels.yaml").names() == ["정상채널"]


def test_add_new_channel_creates_all_fields(tmp_path):
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@신규", lang="ko", note="비고")
    ch = reg.get("신규")
    assert set(ch) == {"name", "url", "lang", "added_at", "note"}
    assert ch["note"] == "비고"


# ─── V-U27: 마이그레이션 저널·롤백·락 (FR35.11~35.12, DQ-36) ─────────────────
def _migration_fixture(tmp_path, monkeypatch):
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    for n, g in (("c1", "역배열1"), ("c2", "역배열1"), ("c3", "AI LLM Wiki")):
        reg.add(f"https://youtube.com/@{n}")
        reg.set_group(n, g)
        _mkchannel(out, n)                       # 평면 구조 (마이그레이션 전)
    reg.add("https://youtube.com/@평면")          # 그룹 미지정 → 유지
    _mkchannel(out, "평면")
    reg.add("https://youtube.com/@미추출")         # 출력 폴더 없음
    reg.set_group("미추출", "역배열1")
    _mkchannel(out, "잔존")                       # 미등록 잔존 폴더 → 보고만
    (out / ".cookie_status.json").write_text("{}", encoding="utf-8")
    return out, reg


def test_plan_migration_is_read_only(tmp_path, monkeypatch):
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    snapshot = sorted(p.name for p in out.iterdir())
    yaml_bytes = (tmp_path / "channels.yaml").read_bytes()
    plan = folder_ops.plan_migration(reg)
    assert sorted(m["channel"] for m in plan["moves"]) == ["c1", "c2", "c3"]
    assert plan["unregistered"] == ["잔존"]
    reasons = {s["channel"]: s["reason"] for s in plan["skipped"]}
    assert "평면" in reasons and "미추출" in reasons
    # dry-run은 아무것도 옮기지 않는다
    assert sorted(p.name for p in out.iterdir()) == snapshot
    assert (tmp_path / "channels.yaml").read_bytes() == yaml_bytes


def test_apply_migration_and_idempotent(tmp_path, monkeypatch):
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    yaml_bytes = (tmp_path / "channels.yaml").read_bytes()
    result = folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    assert result["moved"] == 3
    assert (out / "역배열1" / "c1" / "srt" / "a.srt").read_text(encoding="utf-8") == "자막"
    assert (out / "AI LLM Wiki" / "c3" / "state.json").exists()
    assert (out / "평면" / "state.json").exists(), "미지정 채널은 최상위 그대로"
    assert (out / "잔존" / "state.json").exists(), "미등록 잔존 폴더는 건드리지 않는다"
    assert (out / ".cookie_status.json").exists()
    assert (tmp_path / "channels.yaml").read_bytes() == yaml_bytes, "yaml 바이트 불변"
    assert not folder_ops.is_locked() and not folder_ops.has_pending_journal()
    assert config.channel_dir("c1") == out / "역배열1" / "c1"
    # 재실행 멱등 — 이동 0
    assert folder_ops.plan_migration(ChannelRegistry())["moves"] == []


def test_apply_migration_rolls_back_on_failure(tmp_path, monkeypatch):
    """중간 실패 → 저널 역순 전량 원복 (FR35.12)."""
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    before = {p.name for p in out.iterdir()}
    real = folder_ops.move_channel_dir
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("이동 실패 주입")
        return real(src, dst)
    monkeypatch.setattr(folder_ops, "move_channel_dir", flaky)
    with pytest.raises(OSError):
        folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    assert {p.name for p in out.iterdir()} == before, "전량 원복"
    assert (out / "c1" / "srt" / "a.srt").exists()
    assert not folder_ops.has_pending_journal() and not folder_ops.is_locked()


def test_rollback_migration_restores_flat(tmp_path, monkeypatch):
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    before = {p.name for p in out.iterdir()}
    folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    # pending 저널 경로(중단된 마이그레이션) — 완료분(.done.json) 경로는 V-U28에서 별도 검증
    import shutil as _sh
    _sh.copy(out / ".migration_journal.done.json", out / ".migration_journal.json")
    result = folder_ops.rollback_migration()
    assert result["restored"] == 3
    names = {p.name for p in out.iterdir()}
    assert names >= before and not (out / "역배열1").exists()
    assert (out / "c1" / "srt" / "a.srt").exists()


def test_migration_blocked_by_lock_and_journal(tmp_path, monkeypatch):
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    plan = folder_ops.plan_migration(reg)
    folder_ops.lock()
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.apply_migration(plan, reg)
    assert (out / "c1" / "state.json").exists(), "사전 검증 실패 → 이동 0"
    folder_ops.unlock()
    (out / ".migration_journal.json").write_text("[]", encoding="utf-8")
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.apply_migration(plan, reg)
    (out / ".migration_journal.json").unlink()


def test_migration_lock_stale_after_6h(tmp_path, monkeypatch):
    """stale 락(6시간 초과)은 무시하되 경고 — 수동 --unlock 안내 (FR35.10)."""
    import os as _os
    import time as _time
    import folder_ops
    out, _ = _migration_fixture(tmp_path, monkeypatch)
    folder_ops.lock()
    assert folder_ops.is_locked() is True
    old = _time.time() - (folder_ops.STALE_LOCK_SEC + 60)
    _os.utime(out / ".migration.lock", (old, old))
    assert folder_ops.is_locked() is False
    assert folder_ops.unlock() is True and folder_ops.unlock() is False


def test_migration_precheck_rejects_namespace_conflict(tmp_path, monkeypatch):
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@역배열1")        # 미지정 채널명 == 그룹명
    _mkchannel(out, "역배열1")
    reg.add("https://youtube.com/@c1")
    reg.set_group("c1", "역배열1")
    _mkchannel(out, "c1")
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    assert (out / "c1" / "state.json").exists(), "한 건도 옮기지 않는다"


def test_job_is_busy_ors_migration_lock(tmp_path, monkeypatch):
    """마이그레이션 중에는 대시보드 추출·삭제·이름 변경이 모두 409 (FR35.10)."""
    import folder_ops
    from jobs import JobManager
    _isolate(tmp_path, monkeypatch)
    mgr = JobManager()
    assert mgr.is_busy() is False
    folder_ops.lock()
    try:
        assert mgr.is_busy() is True
    finally:
        folder_ops.unlock()
    assert mgr.is_busy() is False


def test_prune_empty_group_dir_after_purge(tmp_path, monkeypatch):
    """purge 후 남은 빈 그룹 폴더는 **빈 경우만** rmdir — rmtree는 이 경로에 없다 (U-3)."""
    import folder_ops
    out = _isolate(tmp_path, monkeypatch)
    _mkchannel(out, "G", "c1")
    _mkchannel(out, "G2", "c1")
    _mkchannel(out, "G2", "c2")
    import shutil as _sh
    _sh.rmtree(out / "G" / "c1")
    assert folder_ops.prune_empty_group_dir(out / "G") is True
    assert not (out / "G").exists()
    _sh.rmtree(out / "G2" / "c1")
    assert folder_ops.prune_empty_group_dir(out / "G2") is False
    assert (out / "G2" / "c2" / "state.json").exists(), "비어 있지 않으면 남긴다"
    # OUTPUT_BASE 자신은 절대 건드리지 않는다
    assert folder_ops.prune_empty_group_dir(out) is False and out.exists()


# ─── V-U28: FR35 QA 결함 회귀 (F-1~F-6, `_workspace/28_fr35_qa.md`) ──────────
def test_rollback_after_completed_migration(tmp_path, monkeypatch):
    """
    F-1 — **완료된** 마이그레이션도 `--rollback`으로 되돌아간다.

    예전에는 성공 시 저널이 `.done.json`이 되는데 롤백이 pending만 읽어
    `restored: 0`이면서 "롤백 완료"를 출력했다(조용한 실패).
    """
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    before = {p.name for p in out.iterdir()}
    folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    assert (out / ".migration_journal.done.json").exists()
    result = folder_ops.rollback_migration()          # 수동 개명 없이 그대로
    assert result["restored"] == 3 and result["source"] == "done"
    assert result["remaining"] is False
    assert (out / "c1" / "srt" / "a.srt").read_text(encoding="utf-8") == "자막"
    assert not (out / "역배열1").exists() and not (out / "AI LLM Wiki").exists()
    assert {p.name for p in out.iterdir()} >= before
    # 저널은 소비됐다 → 다음 --apply가 "미완료 감지"로 막히지 않는다
    assert not (out / ".migration_journal.done.json").exists()
    assert not folder_ops.is_locked()


def test_rollback_reports_nothing_to_restore(tmp_path, monkeypatch):
    """F-1 — 되돌릴 저널이 없으면 restored 0 + 사유. 성공이라고 말하지 않는다."""
    import folder_ops
    _isolate(tmp_path, monkeypatch)
    result = folder_ops.rollback_migration()
    assert result["restored"] == 0 and result["source"] is None
    assert result["detail"]


def test_rollback_prefers_pending_journal_over_done(tmp_path, monkeypatch):
    """
    F-1 — pending·done이 둘 다 있으면 **pending(최근 중단분)을 먼저** 되돌리고
    남은 저널을 `remaining`으로 알린다. 두 번째 호출이 done을 처리한다.
    """
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)   # done 저널 생성
    # 이후 별도 이동 1건을 pending 저널로 남긴 채 "급사"한 상황을 모사
    _mkchannel(out, "새채널")
    (out / "G9").mkdir()
    (out / "새채널").rename(out / "G9" / "새채널")
    (out / ".migration_journal.json").write_text(json.dumps(
        [{"src": str(out / "새채널"), "dst": str(out / "G9" / "새채널"), "at": "x"}]),
        encoding="utf-8")
    first = folder_ops.rollback_migration()
    assert first["source"] == "pending" and first["restored"] == 1
    assert first["remaining"] is True
    assert (out / "새채널" / "state.json").exists()
    assert (out / "역배열1" / "c1").exists(), "done 저널분은 아직 그대로"
    second = folder_ops.rollback_migration()
    assert second["source"] == "done" and second["restored"] == 3
    assert (out / "c1" / "state.json").exists()


def test_rollback_under_live_lock_requires_takeover(tmp_path, monkeypatch):
    """
    F-2 — 락이 살아 있어도 롤백은 **가능해야** 한다(락 상황의 복구 수단이므로).
    다만 무단 탈취는 막고 `takeover=True`(CLI `--yes`/대화형 y)로만 진행한다.
    """
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    folder_ops.lock()                                  # 급사한 마이그레이션의 잔존 락
    with pytest.raises(folder_ops.ConflictError):
        folder_ops.rollback_migration()
    assert (out / "역배열1" / "c1").exists(), "거부 시 한 건도 움직이지 않는다"
    result = folder_ops.rollback_migration(takeover=True)
    assert result["restored"] == 3
    assert (out / "c1" / "state.json").exists()
    assert not folder_ops.is_locked(), "롤백 종료 시 락 해제"


def test_acquire_blocked_by_migration_lock(tmp_path, monkeypatch):
    """F-5 — 마이그레이션 락 중에는 스캔·추출 진입점(`_acquire`)도 409."""
    import folder_ops
    from jobs import JobManager, JobBusyError
    _isolate(tmp_path, monkeypatch)
    mgr = JobManager()
    mgr._acquire(); mgr._release()                     # 락 없으면 정상 취득
    folder_ops.lock()
    try:
        with pytest.raises(JobBusyError):
            mgr._acquire()
        with pytest.raises(JobBusyError):              # 스캔 진입점 (네트워크 도달 전)
            mgr.scan("https://www.youtube.com/@ch/videos")
    finally:
        folder_ops.unlock()
    mgr._acquire(); mgr._release()


def test_group_flat_entries_skips_unusable_channel_name(tmp_path, monkeypatch):
    """
    F-6 — 예약어 핸들(`@con`) 한 건이 섞여도 스캔 전체를 죽이지 않고
    **그 채널만** 제외한다 (채널 불명 → 제외와 같은 패턴).
    """
    import jobs
    _isolate(tmp_path, monkeypatch)
    entries = [
        {"id": "v1", "title": "정상", "uploader_id": "@good"},
        {"id": "v2", "title": "예약어", "uploader_id": "@con"},
        {"id": "v3", "title": "예약어2", "uploader_id": "@con"},
    ]
    videos, by_channel = jobs._group_flat_entries(entries)
    assert [v["id"] for v in videos] == ["v1"]
    assert list(by_channel) == ["good"]


def test_delete_channel_purges_grouped_dir(tmp_path, monkeypatch):
    """
    F-3 — 그룹 지정 채널의 `purge=true`가 **실제로** `output/<G>/<C>/`를 지운다.
    회귀 원인: `reg.remove()`로 group을 지운 뒤 경로를 계산해 평면 경로가 나왔다.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import folder_ops                                   # noqa: F401 (경로 가드 동반 확인)
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@ch1")
    reg.set_group("ch1", "묶음")
    config.invalidate_group_cache()
    _mkchannel(out, "묶음", "ch1")
    import server
    client = TestClient(server.app)
    r = client.post("/channels/delete", json={"channel": "ch1", "purge": True})
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "purged": True}
    assert not (out / "묶음" / "ch1").exists()
    assert not (out / "묶음").exists(), "빈 그룹 폴더는 rmdir (U-3)"
    assert "ch1" not in ChannelRegistry().list()


def test_rename_channel_validation_is_400(tmp_path, monkeypatch):
    """F-4 — 이름 검증 실패는 409가 아니라 400 (`/channels/group`·`/folders/rename`과 동일)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@베타")
    _mkchannel(out, "베타")
    import server
    client = TestClient(server.app)
    r = client.post("/channels/rename", json={"channel": "베타", "new_name": "CON"})
    assert r.status_code == 400
    assert (out / "베타" / "state.json").exists(), "거부 시 디스크 무변경"


def test_rollback_aborts_when_restore_target_occupied(tmp_path, monkeypatch):
    """
    F-1 보강 — 마이그레이션 뒤 그 자리에 다른 폴더가 생겼다면 롤백은
    **한 건도 움직이지 않고** 중단한다(절반만 되돌리는 것이 최악).
    """
    import folder_ops
    out, reg = _migration_fixture(tmp_path, monkeypatch)
    folder_ops.apply_migration(folder_ops.plan_migration(reg), reg)
    _mkchannel(out, "c1")                              # 평면 자리를 누군가 다시 점유
    with pytest.raises(folder_ops.MoveError):
        folder_ops.rollback_migration()
    assert (out / "역배열1" / "c2" / "state.json").exists(), "한 건도 되돌리지 않는다"
    assert not folder_ops.is_locked()


# ════════════════════════════════════════════════════════════════════════════
# V-U29 — 멤버십 감지 언어 비의존 (FR13·FR17.6·FR19.1, DQ-38)
#   `_workspace/30_members_detect_bug.md` 실측 회귀:
#   DQ-20(`extractor_args.youtube.lang=ko`)이 YouTube가 주는 reason 문구까지
#   한국어로 바꿔, 영어 전용 키워드 판정이 멤버십 영상을 전부 "오류"로 분류했다.
# ════════════════════════════════════════════════════════════════════════════
# 실측 문구 (2026-09-24, 같은 영상 aetOCkgzurM)
MSG_KO = ("ERROR: [youtube] aetOCkgzurM: 이 동영상은 변곡점주식VIP 회원 등급 이상의 "
          "채널 회원에게 제공됩니다. 채널에 가입하여 혜택을 누려보세요.")
MSG_EN = ("ERROR: [youtube] aetOCkgzurM: This video is available to this channel's "
          "members on level: VIP. Join this channel to get access to members-only "
          "content and other exclusive perks.")


def test_members_message_ko_and_en():
    """한국어·영어 실측 문구 둘 다 판정 — 과잉 확장(오탐)은 없어야 한다."""
    import video_access as va
    assert va.is_members_message(MSG_KO), "lang=ko 실측 문구를 놓쳤다 (회귀)"
    assert va.is_members_message(MSG_EN)
    assert va.is_members_message("이 콘텐츠는 회원 전용입니다")
    assert va.is_members_message("멤버십 전용 콘텐츠입니다")
    # 오탐 방지 — `회원` 단독·일반 오류는 멤버십이 아니다
    for benign in ("HTTP Error 403: Forbidden",
                   "HTTP Error 429: Too Many Requests",
                   "ERROR: [youtube] xxx: Private video. Sign in if you've been granted access",
                   "이 동영상은 비공개 동영상입니다",
                   "회원님의 요청을 처리할 수 없습니다",
                   "", None):
        assert not va.is_members_message(benign), benign


def test_members_availability_is_language_independent():
    """1차 신호는 구조화 필드 — 로케일과 무관하다 (FR17.6)."""
    import video_access as va
    for av in ("subscriber_only", "needs_auth", "premium_only"):
        assert va.is_members_availability(av)
        assert va.is_members_only("HTTP Error 403: Forbidden", av), "1차 신호 무시됨"
    for av in ("public", "unlisted", "private", "", None):
        assert not va.is_members_availability(av), av
    assert va.is_members_only(MSG_KO, None), "availability 없으면 메시지 폴백"
    assert not va.is_members_only("HTTP Error 403: Forbidden", "public")


def test_members_rule_is_shared_by_jobs_and_extractor(tmp_path, monkeypatch):
    """대시보드 스캔과 추출이 **같은 규칙**을 쓴다 — 중복 정의는 드리프트를 만든다."""
    import video_access as va
    import jobs
    ex = _batch_rest(monkeypatch, tmp_path)
    assert jobs._MEMBERS_AVAILABILITY is va.MEMBERS_AVAILABILITY
    assert jobs._is_members_availability("subscriber_only") is True
    assert jobs._is_members_availability("public") is False
    assert ex.Extractor._is_members_only(MSG_KO) is True
    assert ex.Extractor._is_members_only("HTTP Error 403: Forbidden",
                                         "subscriber_only") is True


def _run_one(ex, monkeypatch, name, entry, exc_msg):
    """process_video가 exc_msg로 실패하는 run() 1영상 실행 → (stats, state)."""
    def boom(self, vid, action="new", **kw):
        raise Exception(exc_msg)
    monkeypatch.setattr(ex.Extractor, "process_video", boom)
    e = ex.Extractor({"name": name, "url": f"https://www.youtube.com/@{name}/videos"})
    stats = e.run(entries=[dict(entry)], pl_map={})
    return stats, e.state.state


def test_run_classifies_members_by_availability_and_ko_message(tmp_path, monkeypatch):
    """추출 경로 회귀 — availability 1차·한국어 메시지 2차 모두 members_only."""
    ex = _batch_rest(monkeypatch, tmp_path)
    # ⓐ availability 있음 + 메시지는 한국어(영어 키워드 없음)
    stats, state = _run_one(ex, monkeypatch, "mem_av",
                            {"id": "a1", "title": "멤버십영상",
                             "availability": "subscriber_only"}, MSG_KO)
    assert stats["members_only"] == 1 and stats["error"] == 0, stats
    assert state["a1"]["sub_type"] == "members_only", "FR19.1 재시도 대상 기록 누락"
    # ⓑ availability 없음(단일영상·검색 경로) → 한국어 메시지 폴백
    stats, state = _run_one(ex, monkeypatch, "mem_msg",
                            {"id": "b1", "title": "멤버십영상"}, MSG_KO)
    assert stats["members_only"] == 1 and stats["error"] == 0, stats
    assert state["b1"]["sub_type"] == "members_only"
    # ⓒ 영어 문구도 그대로 동작 (lang 미지정 경로 회귀)
    stats, _ = _run_one(ex, monkeypatch, "mem_en",
                        {"id": "c1", "title": "members video"}, MSG_EN)
    assert stats["members_only"] == 1, stats


def test_429_takes_precedence_over_members_availability(tmp_path, monkeypatch):
    """멤버십 영상의 429는 429다 — 멤버십으로 오분류하면 영구 스킵된다 (FR14.3)."""
    ex = _batch_rest(monkeypatch, tmp_path)
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    stats, state = _run_one(ex, monkeypatch, "mem_429",
                            {"id": "d1", "title": "멤버십영상",
                             "availability": "subscriber_only"},
                            "ERROR: unable to download: HTTP Error 429: Too Many Requests")
    assert stats["members_only"] == 0 and stats["error"] == 1, stats
    assert "d1" not in state, "429는 state에 기록하지 않는다 — 다음 run에서 재시도"


# ════════════════════════════════════════════════════════════════════════════
# V-U30 — 채널 메모 계약 (FR36.1~36.2, DQ-39)
#   `note`는 `add()`가 쓰기만 하고 읽는 곳이 0이던 필드다. `set_note` 하나를
#   쓰기 통로로 삼고, 빈 값도 **필드를 지우지 않고** `note: ""`로 남긴다.
# ════════════════════════════════════════════════════════════════════════════
def _reg(tmp_path) -> ChannelRegistry:
    """tmp yaml 격리 레지스트리 — 실 channels.yaml을 절대 건드리지 않는다."""
    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    reg.add("https://youtube.com/@메모채널")
    return reg


def test_set_note_normalizes_control_chars_and_trims(tmp_path):
    """제어문자(개행·탭·U+007F)는 공백 치환 후 트림 — 카드 1행 표시가 계약이다."""
    reg = _reg(tmp_path)
    got = reg.set_note("메모채널", "  장투 관점\n요약\t위주\x7f  ")
    assert got == "장투 관점 요약 위주", got
    assert "\n" not in got and "\t" not in got
    # 반환값 = 저장값 (API가 그대로 응답 → 프론트 표시 불일치 차단)
    assert ChannelRegistry(yaml_path=tmp_path / "channels.yaml").get("메모채널")["note"] == got


def test_set_note_length_limit_rejects_not_truncates(tmp_path):
    """200자 통과 · 201자는 `ValueError` — 조용한 절삭은 사용자 텍스트 소실이다."""
    from channel_registry import NOTE_MAX_LEN
    assert NOTE_MAX_LEN == 200
    reg = _reg(tmp_path)
    assert reg.set_note("메모채널", "가" * 200) == "가" * 200
    with pytest.raises(ValueError):
        reg.set_note("메모채널", "가" * 201)
    # 거부 시 기존 값 유지 (부분 반영 없음)
    assert reg.get("메모채널")["note"] == "가" * 200
    # 트림 후 길이로 판정한다 — 공백 패딩만으로 400이 나지 않는다
    assert reg.set_note("메모채널", "  " + "나" * 200 + "  ") == "나" * 200


def test_set_note_empty_keeps_field_as_empty_string(tmp_path):
    """빈 값은 **필드 제거가 아니라 `note: ""`** — group·auto_run식 pop을 쓰지 않는다."""
    reg = _reg(tmp_path)
    reg.set_note("메모채널", "지울 메모")
    assert reg.set_note("메모채널", "   ") == ""
    raw = yaml.safe_load((tmp_path / "channels.yaml").read_text(encoding="utf-8"))
    assert "note" in raw["channels"]["메모채널"], "필드를 pop하면 yaml이 불균일해진다"
    assert raw["channels"]["메모채널"]["note"] == ""


def test_set_note_unknown_channel(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(KeyError):
        reg.set_note("없는채널", "메모")


def test_note_survives_add_upsert_and_rename(tmp_path):
    """회귀 — `add()` 재등록이 note를 보존하고(FR7.7) `rename()`이 note를 옮긴다."""
    reg = _reg(tmp_path)
    reg.set_note("메모채널", "보존되어야 함")
    reg.add("https://youtube.com/@메모채널", lang="en")        # note 인자 없음 = 보존
    assert reg.get("메모채널")["note"] == "보존되어야 함"
    assert reg.get("메모채널")["lang"] == "en", "url·lang은 갱신된다"
    reg.rename("메모채널", "새메모채널")
    assert reg.get("새메모채널")["note"] == "보존되어야 함"


def test_channels_note_api_contract(tmp_path, monkeypatch):
    """
    `POST /channels/note` + `GET /channels/stats.note` 응답 shape (FR36.3~36.4·36.11).

    프론트는 `{ok, channel, note}`의 **서버 정규화 값**을 그대로 렌더하고,
    카드는 `/channels/stats` 한 곳에서만 메모를 읽는다.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    _isolate(tmp_path, monkeypatch)
    ChannelRegistry().add("https://youtube.com/@메모채널")
    import server
    import jobs
    client = TestClient(server.app)

    r = client.post("/channels/note", json={"channel": "메모채널", "note": " 첫 메모\n둘째 "})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "channel": "메모채널", "note": "첫 메모 둘째"}

    stats = {c["name"]: c for c in client.get("/channels/stats").json()["channels"]}
    assert stats["메모채널"]["note"] == "첫 메모 둘째", "카드는 stats 하나만 읽는다 (FR36.3)"

    assert client.post("/channels/note",
                       json={"channel": "없는채널", "note": "x"}).status_code == 404
    assert client.post("/channels/note",
                       json={"channel": "메모채널", "note": "가" * 201}).status_code == 400
    assert client.post("/channels/note",
                       json={"channel": "../etc", "note": "x"}).status_code == 400
    # note 생략 = 삭제 (필드는 남는다)
    assert client.post("/channels/note", json={"channel": "메모채널"}).json()["note"] == ""
    assert ChannelRegistry().get("메모채널")["note"] == ""

    # 작업 중에는 409 — 파일 경합이 아니라 channels.yaml lost update 때문이다 (DQ-42)
    jobs.MANAGER._busy = True
    try:
        assert client.post("/channels/note",
                           json={"channel": "메모채널", "note": "x"}).status_code == 409
    finally:
        jobs.MANAGER._busy = False


# ════════════════════════════════════════════════════════════════════════════
# V-U31 — 스캔 캐시 무효화 (FR36.8, DQ-41)
#   캐시에 박힌 **스캔 시점 채널명**을 `_run_channel`이 끝까지 쓴다. 이름 변경·삭제
#   뒤 옛 scan_id로 추출하면 유령 폴더(`output/<옛이름>/`)가 생기거나 삭제한 채널이
#   되살아난다 → 캐시를 고쳐 쓰지 않고 **폐기**해 기존 400에 착지시킨다.
# ════════════════════════════════════════════════════════════════════════════
def _mkscan(mgr, sid: str, *, channel: str = None, by_channel: list = None):
    """스캔 캐시 항목 주입 — 채널 스캔(channel)·재생목록/검색 스캔(by_channel)."""
    import time as _t
    entry = {"scan_id": sid, "channel": channel, "url": "https://youtube.com/@x/videos",
             "videos_view": [], "entries": [], "pl_map": {}, "created_at": _t.time()}
    if by_channel is not None:
        entry["kind"] = "playlist"
        entry["by_channel"] = {n: {"url": "", "entries": []} for n in by_channel}
    mgr._scans[sid] = entry
    return sid


def test_invalidate_scans_targets_only_referencing_entries(tmp_path, monkeypatch):
    """대상 채널을 참조하는 항목만 삭제 — 다른 채널 캐시는 남는다."""
    _isolate(tmp_path, monkeypatch)
    import jobs
    mgr = jobs.JobManager()
    _mkscan(mgr, "s_ch", channel="대상")                        # 채널 스캔
    _mkscan(mgr, "s_pl", channel="재생목록제목", by_channel=["대상", "다른채널"])
    _mkscan(mgr, "s_other", channel="다른채널")
    _mkscan(mgr, "s_other_pl", channel="검색어", by_channel=["다른채널"])
    assert mgr.invalidate_scans(channel="대상") == 2
    assert set(mgr._scans) == {"s_other", "s_other_pl"}
    # 정확 일치 — 부분 문자열로 남의 캐시를 지우지 않는다
    assert mgr.invalidate_scans(channel="다른") == 0
    assert set(mgr._scans) == {"s_other", "s_other_pl"}
    assert mgr.invalidate_scans() == 2, "channel=None이면 전체 비움"
    assert mgr._scans == {}


def test_rename_invalidates_scan_and_extract_is_400(tmp_path, monkeypatch):
    """`POST /channels/rename` 성공 → 캐시 폐기 → 옛 scan_id는 **기존 400**(신규 코드 없음)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@옛이름")
    _mkchannel(out, "옛이름")
    import server
    import jobs
    jobs.MANAGER._scans.clear()
    try:
        _mkscan(jobs.MANAGER, "stale", channel="옛이름")
        _mkscan(jobs.MANAGER, "keep", channel="남는채널")
        client = TestClient(server.app)
        r = client.post("/channels/rename", json={"channel": "옛이름", "new_name": "새이름"})
        assert r.status_code == 200, r.text
        assert set(jobs.MANAGER._scans) == {"keep"}, "대상 캐시만 폐기"
        r = client.post("/extract", json={"scan_id": "stale"})
        assert r.status_code == 400
        assert "만료" in r.json()["detail"], r.json()
    finally:
        jobs.MANAGER._scans.clear()


def test_rename_failure_keeps_scan_cache(tmp_path, monkeypatch):
    """400/409면 캐시를 건드리지 않는다 — 무효화는 **성공 후에만**."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@옛이름")
    _mkchannel(out, "옛이름")
    import server
    import jobs
    jobs.MANAGER._scans.clear()
    try:
        _mkscan(jobs.MANAGER, "stale", channel="옛이름")
        client = TestClient(server.app)
        r = client.post("/channels/rename", json={"channel": "옛이름", "new_name": "CON"})
        assert r.status_code == 400, r.text
        assert set(jobs.MANAGER._scans) == {"stale"}, "거부 시 캐시 무변경"
    finally:
        jobs.MANAGER._scans.clear()


def test_delete_channel_invalidates_scan_cache(tmp_path, monkeypatch):
    """삭제도 같은 계열의 구멍 — 옛 scan_id로 추출하면 `reg.add()`가 채널을 되살린다."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    out = _isolate(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@지울채널")
    _mkchannel(out, "지울채널")
    import server
    import jobs
    jobs.MANAGER._scans.clear()
    try:
        _mkscan(jobs.MANAGER, "gone", channel="지울채널")
        _mkscan(jobs.MANAGER, "keep", channel="남는채널")
        client = TestClient(server.app)
        r = client.post("/channels/delete", json={"channel": "지울채널"})
        assert r.status_code == 200, r.text
        assert set(jobs.MANAGER._scans) == {"keep"}
        r = client.post("/extract", json={"scan_id": "gone"})
        assert r.status_code == 400 and "만료" in r.json()["detail"]
        assert "지울채널" not in ChannelRegistry().list(), "되살아나지 않는다"
    finally:
        jobs.MANAGER._scans.clear()


def test_stale_scan_cache_targets_ghost_dir_without_invalidation(tmp_path, monkeypatch):
    """
    **결함 재현 대조군** — 무효화가 없으면 `_run_channel`은 옛 이름을 끝까지 쓴다.

    `reg.add()`는 `resolve_name`으로 새 이름을 돌려주지만 워커가 반환값을 받지
    않으므로 지역 변수는 옛 이름 그대로 → `reg.get(옛이름)` KeyError → 폴백 cfg →
    `config.channel_dir(옛이름)` = 그룹 밖 **평면 유령 경로**.
    """
    import types
    out = _isolate(tmp_path, monkeypatch)
    import jobs
    reg = ChannelRegistry()
    reg.add("https://youtube.com/@옛이름")
    reg.set_group("옛이름", "묶음")
    reg.rename("옛이름", "새이름")                 # 레지스트리만 변경(폴더 이동은 renamer)
    config.invalidate_group_cache()

    captured = {}

    class FakeExtractor:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def run(self, **kw):
            return {"new": 0}

    monkeypatch.setattr(jobs, "_app_extractor",
                        lambda: types.SimpleNamespace(
                            Extractor=FakeExtractor,
                            BatchRest=lambda **kw: None))
    mgr = jobs.JobManager()
    entry = {"channel": "옛이름", "url": "https://youtube.com/@옛이름/videos",
             "videos_view": [{"id": "v1", "title": "영상"}],
             "entries": [{"id": "v1"}], "pl_map": {}}
    job = mgr._new_job("channel_run", "옛이름", entry["url"])
    mgr._job = job
    mgr._cancel.clear()
    mgr._run_channel(job, entry, {"include_members": True}, False)

    assert captured["cfg"]["name"] == "옛이름", "워커는 캐시의 옛 이름을 끝까지 쓴다"
    assert config.channel_dir("옛이름") == out / "옛이름", "레지스트리에 없는 평면 유령 경로"
    assert config.channel_dir("새이름") == out / "묶음" / "새이름", "실제 채널은 그룹 안"
    # 그래서 폐기가 유일한 해 — 폐기하면 이 경로에 애초에 도달하지 않는다
    mgr._scans["stale"] = {"channel": "옛이름", "created_at": 0}
    assert mgr.invalidate_scans(channel="옛이름") == 1

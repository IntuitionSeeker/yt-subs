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

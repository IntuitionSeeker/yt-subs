"""단위 검증 — V-U1~V-U11. 외부 네트워크 불필요."""
import os
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


# ─── V-U32: 종목 추출 — 문맥 근거 (FR12.2 · DQ-43) ──────────────────────────
# 실측 오탐(_workspace/34_ticker_bug.md: 26/441건 = 100% 오탐)을 그대로 고정한다.
@pytest.mark.parametrize("text", [
    # ① 제목 앞머리 날짜 YYMMDD — 오탐 22종의 실제 문자열
    "[주식] 260819 코스닥 - 코스피 - 다시 코스닥으로",
    "[주식]  260703 슈퍼 변동성 시장, 들고 갈 것들",
    "[주식] 260706 핵심만 빠르게 / 다른 건 필요 없다",
    # ② 설명란 계좌번호 — 숫자 나열 인접
    "🎁 후원 계좌 : 우리은행 /1002 763 241686 / 박 * *",
    "사업자 등록번호 안내 : 123-45-67890",
    "문의 010-1234-5678",
    # ③ URL 안의 숫자 조각
    "https://blog.naver.com/supersell201/221927411687",
    "https://contents.premium.naver.com/kiwoom/thestock/contents/220531171207062rn",
    # ④ 근거 없는 맨 6자리 (구 규칙이 채택하던 형태)
    "삼성전자 005930 와 분석",
    "조회수 123456 돌파",
    # ⑤ 종목과 무관한 라벨 · 약한 근거 + 날짜
    "쿠폰코드 123456 입력하세요",
    "인증코드: 260819",
    "(260819) 방송분",
])
def test_extract_tickers_rejects_noise(text):
    """근거 없는 6자리는 채택하지 않는다 — 빈 값이 정확한 결과다 (FR12.2)."""
    from meta_collector import extract_tickers
    assert extract_tickers(text) == []


@pytest.mark.parametrize("text,expected", [
    ("종목코드: 005930 삼성전자 분석", ["005930"]),          # 강한 라벨
    ("단축코드 035720", ["035720"]),
    ("티커 373220 LG에너지솔루션", ["373220"]),
    ("종목코드는 010130 고려아연", ["010130"]),              # 날짜형이어도 강한 라벨이면 채택
    ("KRX:005930 vs $AAPL", ["005930", "AAPL"]),
    ("005930.KS 차트", ["005930"]),
    ("삼성전자(005930) 목표가 상향", ["005930"]),            # 괄호 단독 표기
    ("코드: 042700 한미반도체", ["042700"]),
    ("종목코드 005930, 000660 두 종목", ["005930", "000660"]),  # 나열 이어받기
    ("$TSLA $NVDA 실적", ["TSLA", "NVDA"]),                  # 미국 티커는 $ 접두가 근거
])
def test_extract_tickers_accepts_with_context(text, expected):
    from meta_collector import extract_tickers
    assert extract_tickers(text) == expected


def test_backfill_tickers_dry_run_then_apply(tmp_path, monkeypatch):
    """백필은 기본 dry-run(쓰기 없음), --apply에서만 meta를 갱신한다 (FR12.2)."""
    import json as _json
    import config as cfg
    import meta_collector
    monkeypatch.setattr(cfg, "OUTPUT_BASE", tmp_path)
    monkeypatch.setattr(cfg, "CHANNELS_YAML", tmp_path / "channels.yaml")
    dirs = cfg.channel_subdirs("테스트채널")
    dirs["meta"].mkdir(parents=True); dirs["desc"].mkdir(parents=True)
    meta = {"id": "v1", "title": "[주식] 260819 코스닥", "tags": [],
            "tickers": ["260819"]}
    mp = dirs["meta"] / "20260819_test.json"
    mp.write_text(_json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (dirs["desc"] / "20260819_test.txt").write_text(
        "🎁 후원 계좌 : 우리은행 /1002 763 241686 / 박 * *", encoding="utf-8")

    dry = meta_collector.backfill_tickers("테스트채널")
    assert dry["scanned"] == 1 and dry["changed"] == 1 and dry["removed"] == 1
    assert _json.loads(mp.read_text(encoding="utf-8"))["tickers"] == ["260819"]  # 미기록

    applied = meta_collector.backfill_tickers("테스트채널", apply=True)
    assert applied["changed"] == 1
    assert _json.loads(mp.read_text(encoding="utf-8"))["tickers"] == []
    # 멱등: 두 번째 호출은 변경 0
    assert meta_collector.backfill_tickers("테스트채널", apply=True)["changed"] == 0


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


# ════════════════════════════════════════════════════════════════════════════
# V-U33 — 주기 자동 추출: 판정·계획·상태 파일 (FR37.1~37.16, DQ-44~50)
#
# 일 단위 동작을 초 단위로 검증하려면 판정이 순수 함수여야 한다 — `decide_cycle`은
# 시계·busy·쿠키를 전부 인자로 받는다(DESIGN §2.13). 네트워크 0.
# ════════════════════════════════════════════════════════════════════════════
def _sched(tmp_path, monkeypatch):
    """scheduler를 tmp로 격리 — 실 output/·channels.yaml을 절대 건드리지 않는다."""
    out = _isolate(tmp_path, monkeypatch)
    import scheduler
    return out, scheduler


def _now():
    import datetime
    return datetime.datetime(2026, 9, 24, 12, 0, 0)


def _ago(days=0, minutes=0):
    import datetime
    return (_now() - datetime.timedelta(days=days, minutes=minutes)).isoformat(
        timespec="seconds")


def test_schedule_defaults_and_corrupt_state_fallback(tmp_path, monkeypatch):
    """기본값 = 꺼짐·3일·30 (FR37.1·37.3·37.8). 손상 파일은 기본값 폴백 (FR37.13)."""
    out, sch = _sched(tmp_path, monkeypatch)
    st = sch.load_state()
    assert st["enabled"] is False and st["interval_days"] == 3
    assert st["max_videos_per_cycle"] == 30 and st["skip_cycles"] == 0

    sch.state_file().write_text("{깨진 JSON", encoding="utf-8")
    assert sch.load_state()["interval_days"] == 3, "손상 시 기본값 폴백 (죽지 않는다)"
    sch.state_file().write_text('{"interval_days": 5, "max_videos_per_cycle": 9999}',
                                encoding="utf-8")
    st = sch.load_state()
    assert st["interval_days"] == 3, "허용 집합 밖 값은 기본값으로 교정"
    assert st["max_videos_per_cycle"] == sch.BUDGET_MAX


def test_schedule_save_is_atomic_and_leaves_no_tmp(tmp_path, monkeypatch):
    """`.tmp` + os.replace 원자 교체 — 임시 파일이 남지 않는다 (FR37.13)."""
    out, sch = _sched(tmp_path, monkeypatch)
    st = sch.load_state()
    st["enabled"] = True
    sch.save_state(st)
    assert sch.state_file().exists()
    assert not list(out.glob(".scheduler.json.tmp")), "임시 파일 잔재 없음"
    assert sch.load_state()["enabled"] is True
    assert "next_due_at" not in json.loads(sch.state_file().read_text()), \
        "파생값은 저장하지 않는다"


def test_schedule_toggle_does_not_run_immediately(tmp_path, monkeypatch):
    """
    `enabled`를 처음 켜면 `last_run_at`을 now로 채운다 (FR37.2·DESIGN §2.13).

    비워 두면 "간격 경과"가 즉시 참이 되어 **토글하자마자 전 채널 추출**이 시작된다.
    """
    out, sch = _sched(tmp_path, monkeypatch)
    view = sch.update(enabled=True)
    assert view["enabled"] is True and view["last_run_at"], "켤 때 기준 시각을 박는다"
    st = sch.load_state()
    import datetime
    action, reason, _ = sch.decide_cycle(
        st, datetime.datetime.now(), busy=False, cookie_warning=False,
        started_at=datetime.datetime.now() - datetime.timedelta(hours=1))
    assert (action, reason) == ("idle", "not_due"), "토글 직후에는 돌지 않는다"


def test_schedule_overdue_runs_once_not_per_missed_cycle(tmp_path, monkeypatch):
    """밀린 주기는 1회만 — `last_run_at = now`(cron식 += interval 누적 금지, DQ-45)."""
    out, sch = _sched(tmp_path, monkeypatch)
    st = dict(sch.load_state(), enabled=True, last_run_at=_ago(days=30))
    sch.save_state(st)          # 마감은 파일을 다시 읽는다(F1) — 사용자 설정은 파일이 정본
    started = _now() - __import__("datetime").timedelta(hours=1)
    action, reason, st2 = sch.decide_cycle(st, _now(), busy=False,
                                           cookie_warning=False, started_at=started)
    assert (action, reason) == ("run", "due")
    st3 = sch._finish_cycle(sch._empty_result(_now(), "done"), aborted=False)
    action2, reason2, _ = sch.decide_cycle(st3, _now(), busy=False,
                                           cookie_warning=False, started_at=started)
    assert action2 == "idle" and reason2 == "not_due", "30일치를 10번 돌지 않는다"


def test_schedule_startup_grace(tmp_path, monkeypatch):
    """기동 후 5분은 돌지 않는다 — 절전 복귀 직후 DNS 미비 (FR37.2)."""
    out, sch = _sched(tmp_path, monkeypatch)
    st = dict(sch.load_state(), enabled=True, last_run_at=_ago(days=10))
    import datetime
    action, reason, _ = sch.decide_cycle(
        st, _now(), busy=False, cookie_warning=False,
        started_at=_now() - datetime.timedelta(seconds=10))
    assert (action, reason) == ("idle", "startup_grace")


def test_schedule_busy_and_cookie_keep_last_run_at(tmp_path, monkeypatch):
    """
    busy·쿠키 경고는 **`last_run_at`을 갱신하지 않는다** (FR37.11·37.12).

    갱신하면 사용자 작업 때문에 한 주기를 통째로 잃고, 쿠키 자가 치유도 깨진다.
    """
    out, sch = _sched(tmp_path, monkeypatch)
    import datetime
    started = _now() - datetime.timedelta(hours=1)
    st = dict(sch.load_state(), enabled=True, last_run_at=_ago(days=10))

    action, reason, st_busy = sch.decide_cycle(st, _now(), busy=True,
                                               cookie_warning=False, started_at=started)
    assert (action, reason) == ("idle", "busy")
    assert st_busy["last_run_at"] == st["last_run_at"], "도래 상태 유지 → 60초 뒤 재시도"

    action, reason, st_ck = sch.decide_cycle(st, _now(), busy=False,
                                             cookie_warning=True, started_at=started)
    assert (action, reason) == ("idle", "cookie")
    assert st_ck["paused_reason"] == "cookie"
    assert st_ck["last_run_at"] == st["last_run_at"], "쿠키를 고치면 다음 틱에 재개"
    assert st_ck["enabled"] is True, "기계는 사용자 설정을 되돌려 쓰지 않는다 (DQ-49)"
    # 쿠키가 나으면 paused_reason은 스스로 사라진다
    action, reason, st_ok = sch.decide_cycle(st_ck, _now(), busy=False,
                                             cookie_warning=False, started_at=started)
    assert action == "run" and st_ok["paused_reason"] is None


def test_schedule_backoff_is_consumed_before_cookie_and_busy(tmp_path, monkeypatch):
    """
    판정 순서 계약: skip_cycles 감소가 쿠키·busy보다 **먼저**다 (DESIGN §2.13).

    뒤에 두면 쿠키가 만료된 기간 동안 백오프가 소모되지 않아 차단 회복 후에도 쉰다.
    """
    out, sch = _sched(tmp_path, monkeypatch)
    import datetime
    started = _now() - datetime.timedelta(hours=1)
    st = dict(sch.load_state(), enabled=True, last_run_at=_ago(days=10), skip_cycles=2)
    action, reason, st2 = sch.decide_cycle(st, _now(), busy=True, cookie_warning=True,
                                           started_at=started)
    assert (action, reason) == ("skip", "backoff")
    assert st2["skip_cycles"] == 1
    assert st2["last_run_at"] == sch._iso(_now()), "건너뛴 주기도 시계는 전진 (요청 0)"


def test_schedule_backoff_resets_only_on_clean_cycle(tmp_path, monkeypatch):
    """
    429로 끝난 주기 → 1→2→4(상한 4). 리셋은 **429 없이 끝난 주기**에만 (FR37.10).

    취소·429 중단으로 끝난 주기도 `last_run_at`은 갱신한다 (FR37.12).
    """
    out, sch = _sched(tmp_path, monkeypatch)
    st = dict(sch.load_state(), enabled=True, last_run_at=_ago(days=10), skip_cycles=0)
    sch.save_state(st)
    for expected in (1, 2, 4, 4):
        st = sch._finish_cycle(sch._empty_result(_now(), "aborted_429"), aborted=True)
        assert st["skip_cycles"] == expected
    assert st["last_run_at"], "429 중단 주기도 last_run_at 갱신 (사용자와 싸우지 않는다)"
    assert st["enabled"] is True, "마감이 사용자 설정을 되돌려 쓰지 않는다 (DQ-49)"
    st = sch._finish_cycle(sch._empty_result(_now(), "done"), aborted=False)
    assert st["skip_cycles"] == 0, "'한 번 쉬었으니 괜찮다'를 가정하지 않는다 (DQ-48)"


def test_schedule_update_validation(tmp_path, monkeypatch):
    """`interval_days ∈ {3,7,14,28}` · 예산 1~200 — 위반은 ValueError(→400). FR37.3·37.8"""
    out, sch = _sched(tmp_path, monkeypatch)
    for bad in (1, 2, 5, 30, 0, -3):
        with pytest.raises(ValueError):
            sch.update(interval_days=bad)
    for bad in (0, 201, -1):
        with pytest.raises(ValueError):
            sch.update(max_videos_per_cycle=bad)
    view = sch.update(interval_days=7, max_videos_per_cycle=50)
    assert view["interval_days"] == 7 and view["max_videos_per_cycle"] == 50
    assert sch.load_state()["enabled"] is False, "검증 대상 외 필드는 건드리지 않는다"


def test_schedule_request_now_makes_due_without_consuming_backoff(tmp_path, monkeypatch):
    """`run-now` = 지금 도래시키기. 백오프는 그대로 둔다 (FR37.15·DQ-50)."""
    out, sch = _sched(tmp_path, monkeypatch)
    sch.save_state(dict(sch.load_state(), enabled=True, skip_cycles=3,
                        last_run_at=_ago(minutes=1)))
    view = sch.request_now()
    assert view["skip_cycles"] == 3, "사용자가 눌러도 백오프는 소모하지 않는다"
    import datetime
    st = sch.load_state()
    action, reason, st2 = sch.decide_cycle(
        st, datetime.datetime.now(), busy=False, cookie_warning=False,
        started_at=datetime.datetime.now() - datetime.timedelta(hours=1))
    assert action == "skip", "도래는 했고, 안전장치(백오프)는 한 벌 그대로 탄다"
    assert st2["skip_cycles"] == 2


def test_schedule_build_plan_budget_cursor_and_truncated(tmp_path, monkeypatch):
    """예산 절단 · 커서 회전(기아 방지) · RSS 15개 상한 노출 (FR37.6·37.8·DQ-47)."""
    out, sch = _sched(tmp_path, monkeypatch)
    reg = ChannelRegistry()
    for n in ("aa", "bb", "cc"):
        reg.add(f"https://youtube.com/@{n}")
    reg.add("https://youtube.com/@zz")
    reg.set_auto_run("zz", False)                    # 검색 유입 채널은 제외 (FR37.5)
    reg = ChannelRegistry()

    new = {n: [{"id": f"{n}{i}", "title": f"{n} 영상{i}", "published": "2026-09-20"}
               for i in range(4)] for n in ("aa", "bb", "cc")}
    new["zz"] = [{"id": "zz0", "title": "검색 유입", "published": "2026-09-20"}]

    st = dict(sch.load_state(), max_videos_per_cycle=6)
    plan = sch.build_plan(new, st, reg)
    assert plan["planned"] == 6, "예산 초과분은 계획 단계에서 절단"
    assert list(plan["by_channel"]) == ["aa", "bb"]
    assert "zz" not in plan["by_channel"], "auto_run:false 채널은 스케줄 대상이 아니다"
    assert plan["cursor"] == "cc", "다음 주기는 담지 못한 채널부터 (기아 방지)"
    entry = plan["by_channel"]["aa"]["entries"][0]
    assert set(entry) == {"id", "title"}, "published를 upload_date로 넘기지 않는다"
    view = plan["videos_view"][0]
    assert view["members_only"] is False and view["playlists"] == []
    assert view["content_type"] == "video" and view["channel"] == "aa"

    # 다음 주기는 cursor부터 회전 → 굶주리던 cc가 먼저
    plan2 = sch.build_plan(new, dict(st, cursor=plan["cursor"]), reg)
    assert list(plan2["by_channel"]) == ["cc", "aa"]

    # RSS 상한 도달 감지 (무시하되 노출)
    big = {"aa": [{"id": f"x{i}", "title": "t"} for i in range(sch.RSS_FEED_LIMIT)]}
    assert sch.build_plan(big, st, reg)["truncated"] == ["aa"]


def test_schedule_run_cycle_no_new_makes_no_job(tmp_path, monkeypatch):
    """새 영상 0이면 job을 만들지 않고 주기를 끝낸다 — 요청 0 (FR37.4)."""
    out, sch = _sched(tmp_path, monkeypatch)
    ChannelRegistry().add("https://youtube.com/@aa")
    import rss_monitor
    seen = {}

    def fake_check(names=None):
        seen["names"] = names
        return {"channels": {}, "errors": {"aa": "RSS 조회 실패: timeout"}}

    monkeypatch.setattr(rss_monitor, "check_new_videos", fake_check)

    class NoManager:
        def is_busy(self): return False
        def status(self): return {}
        def start_schedule(self, plan): raise AssertionError("job을 만들면 안 된다")

    result = sch.run_cycle(NoManager(), sch.load_state())
    assert seen["names"] == ["aa"], "대상 채널만 RSS 조회 (FR37.4)"
    assert result["outcome"] == "no_new" and result["rss_errors"]
    assert sch.load_state()["last_run_at"], "무동작 주기도 시계는 전진"


def test_schedule_run_cycle_records_result_and_backoff(tmp_path, monkeypatch):
    """주기 결과 기록 + 429면 백오프 승급, busy 경합은 삼키고 미룬다 (FR37.10·37.12)."""
    out, sch = _sched(tmp_path, monkeypatch)
    ChannelRegistry().add("https://youtube.com/@aa")
    import rss_monitor
    monkeypatch.setattr(rss_monitor, "check_new_videos",
                        lambda names=None: {"channels": {
                            "aa": [{"id": "v1", "title": "새 영상"}]}, "errors": {}})

    class FakeManager:
        def __init__(self, snap, busy_error=False):
            self.snap, self.busy_error, self.plans = snap, busy_error, []

        def is_busy(self): return False

        def status(self): return self.snap

        def start_schedule(self, plan):
            if self.busy_error:
                raise RuntimeError("이미 실행 중인 작업이 있습니다.")
            self.plans.append(plan)
            return {"job_id": "J1"}

    snap = {"job_id": "J1", "status": "done", "done": 1, "aborted_429": True,
            "stats": {"new": 0, "error": 5}}
    st0 = dict(sch.load_state(), enabled=True)
    result = sch.run_cycle(FakeManager(snap), st0)
    assert result["outcome"] == "aborted_429" and result["videos_planned"] == 1
    assert sch.load_state()["skip_cycles"] == 1, "429로 끝난 주기 → 백오프 승급"

    # 점유 실패는 삼키고 last_run_at을 갱신하지 않는다 (다음 틱 재시도)
    before = sch.load_state()
    assert sch.run_cycle(FakeManager(snap, busy_error=True), before) is None
    assert sch.load_state()["last_run_at"] == before["last_run_at"]


def test_schedule_api_contract(tmp_path, monkeypatch):
    """`GET/POST /schedule` · `POST /schedule/run-now` 응답 shape·400 (FR37.14)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SCHEDULER_DISABLED", "1")     # 테스트가 스레드를 띄우지 않는다
    out, sch = _sched(tmp_path, monkeypatch)
    import server
    client = TestClient(server.app)

    body = client.get("/schedule").json()
    for key in ("enabled", "interval_days", "max_videos_per_cycle", "last_run_at",
                "next_due_at", "skip_cycles", "paused_reason", "running", "last_result",
                "last_skip_at"):
        assert key in body, f"index.html이 읽는 필드 누락: {key}"
    assert body["enabled"] is False and body["interval_days"] == 3

    r = client.post("/schedule", json={"interval_days": 5})
    assert r.status_code == 400 and "주기" in r.json()["detail"]
    r = client.post("/schedule", json={"max_videos_per_cycle": 0})
    assert r.status_code == 400

    r = client.post("/schedule", json={"enabled": True, "interval_days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["interval_days"] == 7
    assert body["last_run_at"], "켜는 순간 폭주하지 않도록 기준 시각을 박는다"
    assert body["next_due_at"] > body["last_run_at"]

    r = client.post("/schedule/run-now")
    assert r.status_code == 202
    assert r.json()["last_run_at"] < body["last_run_at"], "간격만큼 과거로 당긴다"
    # 설정 API는 작업 중에도 409를 내지 않는다 (전용 상태 파일 하나만 쓴다)
    import jobs
    jobs.MANAGER._busy = True
    try:
        assert client.get("/schedule").status_code == 200
        assert client.post("/schedule", json={"enabled": False}).status_code == 200
    finally:
        jobs.MANAGER._busy = False


def test_schedule_cycle_never_reverts_user_settings(tmp_path, monkeypatch):
    """
    **주기 도중 바꾼 설정이 마감으로 원복되면 안 된다** (NFR3 ⓓ·DQ-49·DQ-46).

    주기는 수십 분이 걸릴 수 있고, 사용자가 "끄기"를 누르는 순간은 대개
    "지금 뭔가 잘못 돌고 있다"는 순간이다 — 그때 비상 정지가 조용히 무효가 된다.
    원복 방향도 하필 *더 자주·더 많이* 도는 쪽이었다(28일→3일, 5개→30개).
    """
    out, sch = _sched(tmp_path, monkeypatch)
    assert not (set(sch.USER_FIELDS) & set(sch.SCHEDULER_FIELDS)), "소유가 겹치면 안 된다"
    assert set(sch.USER_FIELDS) | set(sch.SCHEDULER_FIELDS) == set(sch.DEFAULTS), \
        "모든 필드는 사용자 소유/스케줄러 소유 중 하나여야 한다 (새 필드 추가 시 분류 필수)"

    ChannelRegistry().add("https://youtube.com/@aa")
    import rss_monitor
    monkeypatch.setattr(rss_monitor, "check_new_videos",
                        lambda names=None: {"channels": {
                            "aa": [{"id": "v1", "title": "새 영상"}]}, "errors": {}})
    sch.update(enabled=True)
    st0 = sch.load_state()           # 틱이 주기 시작 시점에 들고 가는 스냅샷

    class MidCycleManager:
        """주기가 도는 동안 사용자가 `POST /schedule`로 끄고 값을 줄인다."""

        def is_busy(self): return False

        def status(self):
            return {"job_id": "J1", "status": "done", "done": 1, "stats": {"new": 1}}

        def start_schedule(self, plan):
            sch.update(enabled=False, interval_days=28, max_videos_per_cycle=5)
            return {"job_id": "J1"}

    result = sch.run_cycle(MidCycleManager(), st0)
    st = sch.load_state()
    assert st["enabled"] is False, "마감이 '끄기'를 덮으면 비상 정지가 실패한다"
    assert st["interval_days"] == 28 and st["max_videos_per_cycle"] == 5, \
        "주기·예산도 스냅샷으로 되돌아가면 안 된다"
    # 스케줄러 소유 필드는 정상 기록된다 (병합이 기록을 빠뜨리지 않는지)
    assert result["outcome"] == "done" and st["last_result"]["outcome"] == "done"
    assert st["last_run_at"] >= st0["last_run_at"] and st["skip_cycles"] == 0
    assert st["cursor"] == "aa", "커서(스케줄러 소유)는 병합 저장으로 기록된다"


def test_schedule_backoff_skip_keeps_last_result(tmp_path, monkeypatch):
    """
    백오프로 건너뛴 주기는 `last_result`를 **덮지 않는다**.

    덮이는 값은 십중팔구 이 백오프를 유발한 `aborted_429` 기록이다(백오프는 429로만
    생긴다). 사용자가 "왜 멈췄나"를 봐야 하는 3~12일 동안 증거가 0으로 채워진
    레코드로 교체됐다. "건너뜀"은 `skip_cycles` 배너가 더 정확히 말한다.
    """
    import datetime
    out, sch = _sched(tmp_path, monkeypatch)
    prev = dict(sch._empty_result(_now(), "aborted_429"), aborted_429=True,
                channels_with_new=4, videos_planned=12, videos_done=5,
                stats={"new": 5, "error": 5}, truncated_channels=["aa"])
    sch.save_state(dict(sch.load_state(), enabled=True, last_run_at=_ago(days=10),
                        skip_cycles=2, last_result=prev))
    monkeypatch.setattr(sch, "_cookie_warning", lambda: False)

    class IdleManager:
        def is_busy(self): return False

        def status(self): return {}

        def start_schedule(self, plan):
            raise AssertionError("백오프 주기는 요청을 내지 않는다")

    th = sch.SchedulerThread(IdleManager())
    th.started_at = datetime.datetime.now() - datetime.timedelta(hours=1)
    th.tick()

    st = sch.load_state()
    assert st["skip_cycles"] == 1, "건너뛰며 백오프를 소모한다"
    assert st["last_result"] == prev, "429 증거를 지우면 사후 확인이 불가능하다"
    assert st["last_skip_at"], "건너뜀 사실은 별도 필드에 남긴다"
    assert st["enabled"] is True, "틱의 저장 경로도 사용자 설정을 쓰지 않는다"


def test_schedule_re_enable_resets_the_clock(tmp_path, monkeypatch):
    """껐다가 한참 뒤 다시 켜도 **켜자마자 돌지 않는다** (FR37.2 옵트인 취지)."""
    import datetime
    out, sch = _sched(tmp_path, monkeypatch)
    sch.update(enabled=True)
    sch.save_state(dict(sch.load_state(), enabled=False, last_run_at=_ago(days=40)))

    view = sch.update(enabled=True)      # 40일 만에 다시 켠다
    assert view["last_run_at"] > _ago(days=1), "켤 때마다 기준 시각을 박는다"
    action, reason, _ = sch.decide_cycle(
        sch.load_state(), datetime.datetime.now(), busy=False, cookie_warning=False,
        started_at=datetime.datetime.now() - datetime.timedelta(hours=1))
    assert (action, reason) == ("idle", "not_due"), "다시 켠 직후에도 폭주하지 않는다"


def test_schedule_sanitize_guards_enabled_and_last_run_at(tmp_path, monkeypatch):
    """
    손상 값 교정 — `enabled`는 JSON 불리언만, 깨진 `last_run_at`은 "즉시 도래"가 아니다.

    `bool("no")`는 True이고, `last_run_at`이 읽히지 않으면 `next_due_at=None`(즉시)이라
    손상 파일 하나가 무인 전체 추출을 촉발할 수 있다 — 양쪽 다 보수적으로 막는다.
    """
    import datetime
    out, sch = _sched(tmp_path, monkeypatch)
    sch.state_file().write_text(json.dumps(
        {"enabled": "yes", "last_run_at": "언젠가", "cursor": 5,
         "last_result": "망가짐", "skip_cycles": "x"}), encoding="utf-8")
    st = sch.load_state()
    assert st["enabled"] is False, "문자열은 '켜짐'이 아니다 (모호하면 꺼짐)"
    assert sch._parse(st["last_run_at"]) is not None, "깨진 시각은 지금으로 교정"
    assert st["cursor"] is None and st["last_result"] is None and st["skip_cycles"] == 0

    action, reason, _ = sch.decide_cycle(
        dict(st, enabled=True), datetime.datetime.now(), busy=False,
        cookie_warning=False, started_at=datetime.datetime.now()
        - datetime.timedelta(hours=1))
    assert (action, reason) == ("idle", "not_due"), "손상 파일이 즉시 실행을 부르면 안 된다"


def test_schedule_cycle_is_finished_even_if_wait_raises(tmp_path, monkeypatch):
    """
    작업 대기 중 예외가 나도 **마감(`last_run_at` 갱신)은 한다**.

    마감을 못 하면 도래 상태가 그대로라 다음 틱이 같은 주기를 다시 돌린다 — 무인이라
    아무도 보지 못한 채 요청이 두 배가 된다.
    """
    import datetime
    out, sch = _sched(tmp_path, monkeypatch)
    ChannelRegistry().add("https://youtube.com/@aa")
    import rss_monitor
    monkeypatch.setattr(rss_monitor, "check_new_videos",
                        lambda names=None: {"channels": {
                            "aa": [{"id": "v1", "title": "새 영상"}]}, "errors": {}})
    sch.update(enabled=True)
    st0 = dict(sch.load_state(), last_run_at=_ago(days=10))
    sch.save_state(st0)

    class BoomManager:
        def is_busy(self): return False

        def status(self): raise RuntimeError("도커 소켓이 끊겼다")

        def start_schedule(self, plan): return {"job_id": "J1"}

    result = sch.run_cycle(BoomManager(), st0)
    assert result["outcome"] == "error", "실패를 done으로 보고하지 않는다"
    st = sch.load_state()
    assert st["last_result"]["outcome"] == "error"
    action, reason, _ = sch.decide_cycle(
        st, datetime.datetime.now(), busy=False, cookie_warning=False,
        started_at=datetime.datetime.now() - datetime.timedelta(hours=1))
    assert (action, reason) == ("idle", "not_due"), "같은 주기를 한 번 더 돌지 않는다"


# ════════════════════════════════════════════════════════════════════════════
# V-U34 — 429 회로차단 전파 (FR37.9, DQ-48)
#
# `run()`이 연속 429로 중단해도 호출자가 그 사실을 **구별할 수 없어서**
# `_run_grouped`가 다음 채널로 넘어가 계속 두드리던 기존 결함의 회귀 시험이다
# (재생목록 FR24·검색 FR34에도 있던 결함 — 스케줄러 이전의 문제).
# ════════════════════════════════════════════════════════════════════════════
def test_run_flags_aborted_429_without_touching_stat_keys(tmp_path, monkeypatch):
    """연속 429 중단 → `stats["aborted_429"]`. 카운터(_STAT_KEYS)에는 넣지 않는다."""
    _isolate(tmp_path, monkeypatch)
    sys.modules.setdefault("yt_dlp", mock.MagicMock())
    import extractor as ext_mod
    import jobs

    ext = ext_mod.Extractor({"name": "ch429",
                             "url": "https://www.youtube.com/@ch429/videos"})
    entries = [{"id": f"v{i}", "title": f"영상{i}"} for i in range(5)]

    def always_429(self, vid, action="new", **kw):
        raise Exception("HTTP Error 429: Too Many Requests")

    with mock.patch.object(ext_mod.Extractor, "process_video", always_429), \
            mock.patch("extractor.time.sleep"):
        stats = ext.run(entries=entries, pl_map={})

    assert stats.get("aborted_429") is True, "호출자가 구별할 수 있어야 한다"
    assert "cancelled" not in stats, "사용자 취소가 아니다"
    assert "aborted_429" not in jobs._STAT_KEYS, \
        "카운터에 섞으면 job stats 등식(V-D11: 미리보기 수 = 처리 수)이 깨진다"


def _grouped_fixture(tmp_path, monkeypatch, abort_on=None):
    """채널 3개 그룹 추출 — `abort_on` 채널에서 429 중단 표식을 낸다."""
    import types
    _isolate(tmp_path, monkeypatch)
    import jobs
    reg = ChannelRegistry()
    for n in ("c1", "c2", "c3"):
        reg.add(f"https://youtube.com/@{n}")
    called = []

    class FakeExtractor:
        def __init__(self, cfg):
            self.name = cfg["name"]

        def run(self, **kw):
            called.append(self.name)
            base = {"new": 1, "updated": 0, "skip": 0, "no_sub": 0,
                    "members_only": 0, "error": 0, "date_skip": 0, "live_wait": 0}
            if self.name == abort_on:
                base["error"] = 1
                base["aborted_429"] = True
            return base

    monkeypatch.setattr(jobs, "_app_extractor",
                        lambda: types.SimpleNamespace(
                            Extractor=FakeExtractor, BatchRest=lambda **kw: None))
    entry = {"channel": "", "url": "",
             "by_channel": {n: {"url": f"https://youtube.com/@{n}/videos",
                                "entries": [{"id": f"{n}v", "title": "t"}]}
                            for n in ("c1", "c2", "c3")},
             "videos_view": [{"id": f"{n}v", "title": "t", "channel": n,
                              "content_type": "video", "playlists": [],
                              "members_only": False, "extracted": False}
                             for n in ("c1", "c2", "c3")]}
    mgr = jobs.JobManager()
    job = mgr._new_job("schedule_run", "자동 추출", "")
    mgr._job = job
    mgr._cancel.clear()
    mgr._run_grouped(job, entry, {"include_members": True}, False,
                     group_title=None, merge_categories=False, auto_run=True)
    return job, called


def test_aborted_429_breaks_group_loop(tmp_path, monkeypatch):
    """
    429 중단 표식을 보면 **남은 채널을 돌지 않는다** — 작업은 `done`, 사유는 경고.

    대조군: 표식이 없으면 세 채널을 전부 호출한다(= 수정 전의 동작).
    """
    job, called = _grouped_fixture(tmp_path, monkeypatch, abort_on="c1")
    assert called == ["c1"], f"차단 뒤에도 계속 두드리면 안 된다: {called}"
    assert job["status"] == "done", "사용자가 취소한 게 아니다 (cancelled 아님)"
    assert job["aborted_429"] is True
    assert any("429" in w and "2개" in w for w in job["warnings"]), job["warnings"]

    job2, called2 = _grouped_fixture(tmp_path, monkeypatch, abort_on=None)
    assert called2 == ["c1", "c2", "c3"], "정상 주기는 전 채널 순회 (회귀 방지)"
    assert job2["aborted_429"] is False and job2["status"] == "done"
    assert job2["stats"]["new"] == 3


def test_start_schedule_fixed_arguments(tmp_path, monkeypatch):
    """스케줄 워커 고정 인자 — include_members·group_title=None·index (FR37.7)."""
    import time as _time
    _isolate(tmp_path, monkeypatch)
    import jobs
    mgr = jobs.JobManager()
    captured = {}

    def fake_grouped(job, entry, filters, index, group_title=None,
                     merge_categories=True, auto_run=True):
        captured.update(filters=filters, index=index, group_title=group_title,
                        merge_categories=merge_categories, entry=entry, job=job)

    mgr._run_grouped = fake_grouped
    plan = {"by_channel": {"c1": {"url": "u", "entries": [{"id": "v1", "title": "t"}]}},
            "videos_view": [{"id": "v1", "title": "t"}]}
    job = mgr.start_schedule(plan)
    for _ in range(100):
        if not mgr.is_busy():
            break
        _time.sleep(0.02)
    assert job["kind"] == "schedule_run"
    assert captured["filters"] == {"include_members": True}, \
        "빼면 apply_filters 기본값이 멤버십 영상을 조용히 지운다"
    assert captured["index"] is True and captured["group_title"] is None
    assert captured["merge_categories"] is False
    assert captured["entry"]["by_channel"] == plan["by_channel"]


# ════════════════════════════════════════════════════════════════════════════
# V-U35 — `audit` 검사 계약 (FR38.1~38.12, DQ-52~54)
#
# 합성 문서 픽스처(tmp에 REQUIREMENTS/DESIGN/CLAUDE/qa-verifier/SKILL/테스트 축소판)로
# **양성·음성 양쪽**을 고정한다. "검사가 있다"가 아니라 "그 이상을 실제로 잡고,
# 정상 상태를 오탐하지 않는다"를 증명해야 한다 — 시제품이 스코프 없이 냈던
# 허위 15건(FR 중복)·6건(DQ 중복)·140건(토큰 잡음)의 회귀 시험이 여기 들어 있다.
# ════════════════════════════════════════════════════════════════════════════
import selfcheck

_AUDIT_BASELINE = "10 passed / 1 skipped"


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _audit_fixture(tmp_path: Path) -> Path:
    """감사가 **0건**을 내는 정상 저장소 축소판. 음성 대조군의 기준선이다."""
    root = tmp_path / "repo"
    _write(root / "REQUIREMENTS.md", """# REQUIREMENTS

> **버전:** v1.0
> **연계 문서:** DESIGN.md v1.0

## 3. 기능 요구사항 (FR)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR1.1 | 가 | 필수 |
| FR1.2 | 나 | 필수 |
| FR2.1 | 다 | 필수 |

## 4. 비기능 요구사항 (NFR)

## 5. 사용자 명령어 (CLI 사양)

| 명령 | 동작 | 비고 |
|---|---|---|
| `./yt.sh add URL` | 등록 | FR1.1 |
| `./yt.sh audit` | 감사 | FR2.1 |

## 6. 트레이서빌리티 매트릭스

| FR | 구현 컴포넌트 | 출력 아티팩트 |
|---|---|---|
| FR1.1~1.2 | `Extractor` · `alpha.py` · `.chan-card` · `""` · `.scheduler.json` · `cp -a` | srt/ |
| FR2.1 | `helper_fn` · `GET /ping` | (API) |

## 7. 검증 방법

기준선 `./yt.sh test` = **10 passed / 1 skipped**

## 8. 설계 결정 사항 (확정)

| ID | 결정 |
|---|---|
| DQ-1 | 가 |
""")
    _write(root / "DESIGN.md", """# DESIGN

> **버전:** v1.0
> **연계 문서:** REQUIREMENTS.md v1.0 (FR1~FR2)

## 8. 실행 방법

```bash
./yt.sh add https://youtube.com/@채널
./yt.sh audit
```

## 9. 검증 설계 (Verification Design)

### 9.1 단위 검증 (V-U)

> 다음 신규 번호는 **V-U3**이다.

#### 9.1a 번호 부여 항목 (V-U1~V-U2)

| ID | 대상 | FR·DQ | 위치 |
|---|---|---|---|
| V-U1 | 가 | FR1.1 | `tests/test_unit.py` §V-U1 |
| V-U2 | 나 | FR1.2 | `mock_scan_test.py` ① (pytest 아님) |

기준선: `./yt.sh test` = **10 passed / 1 skipped**

#### 9.1b 번호 미부여 항목

### 9.2 통합 테스트 (네트워크 필요)

V-I1 등록 · V-I2 스킵

### 9.3 대시보드·기능 검증 (V-D)

| ID | 절차 |
|---|---|
| V-D1 | 가 |

## 10. 설계 결정 사항

| ID | 결정 | 근거 |
|---|---|---|
| DQ-1 | 가 | 나 |

## 11. 개발 하네스
""")
    # CLAUDE.md — 이력 표에는 **과거 값이 정당하게** 남는다(5→8). 마지막 행 오른쪽만 본다.
    _write(root / "CLAUDE.md", """# CLAUDE

| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-01-01 | 초기 · pytest 기준선 5→8 | 전체 | 가 |
| 2026-01-02 | 다음 · pytest 기준선 8→**10 passed / 1 skipped** | 전체 | 나 |
""")
    _write(root / ".claude/agents/qa-verifier.md",
           "단위 테스트 기준선은 **10 passed / 1 skipped**이다.\n")
    _write(root / ".claude/skills/pipeline-verify/SKILL.md",
           "- `./yt.sh test`(pytest **10 passed / 1 skipped**)도 병행\n")
    _write(root / ".claude/skills/pipeline-verify/scripts/mock_scan_test.py", "# mock\n")
    _write(root / ".claude/skills/spec-sync/SKILL.md",
           "- 다음 번호: **V-U3** · V-I3 · **V-D2**. "
           "DQ 다음 번호는 **DQ-2**이다\n")
    _write(root / "tests/test_unit.py", "# ─── V-U1: 가 ───\ndef test_a(): pass\n")
    _write(root / "main.py",
           'sub.add_parser("add")\nsub.add_parser("audit")\n')
    _write(root / "alpha.py", "class Extractor: pass\n\n\ndef helper_fn(): pass\n")
    _write(root / "dashboard/server.py", '@app.get("/ping")\ndef ping(): pass\n')
    return root


def _audit_run(root, baseline=None, strict=False, waivers=None, only=None):
    ctx = selfcheck.AuditContext(root=root, baseline=baseline)
    return selfcheck.run("audit", ctx, only=only, strict=strict,
                         waivers=waivers if waivers is not None else [])


def _snapshot(root: Path) -> dict:
    """전체 파일의 바이트·mtime_ns — 읽기 전용 실증용 (FR38.3)."""
    snap = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        snap[str(p.relative_to(root))] = (p.is_dir(), st.st_size, st.st_mtime_ns)
    return snap


def _codes(result, check=None):
    return sorted((f.check, f.target, f.severity) for f in result["findings"]
                  if check is None or f.check == check)


def test_audit_clean_fixture_is_silent(tmp_path):
    """ⓐ음성 대조군 — 정상 저장소에서는 **0건·종료코드 0**이고 요약 1줄만 낸다."""
    root = _audit_fixture(tmp_path)
    r = _audit_run(root, baseline=_AUDIT_BASELINE)
    assert r["findings"] == [], f"오탐: {_codes(r)}"
    assert r["exit_code"] == 0
    text = selfcheck.render_text("audit", r["findings"], r["waived"], r["checks_run"],
                                 r["skipped"], r["elapsed"], r["exit_code"])
    assert text.count("\n") == 0 and text.startswith("검사 8개 · 오류 0")


def test_audit_is_read_only(tmp_path):
    """ⓜ 실행 전후 픽스처 전체 바이트·mtime **불변**(디렉터리 생성 포함 금지, FR38.3)."""
    root = _audit_fixture(tmp_path)
    before = _snapshot(root)
    _audit_run(root, baseline=_AUDIT_BASELINE)
    assert _snapshot(root) == before


def test_audit_pytest_baseline_catches_one_stale_place(tmp_path):
    """ⓐ 5곳 중 1곳만 낡아도 오류 — 이 프로젝트가 한 세션에 4번 겪은 어긋남이다."""
    root = _audit_fixture(tmp_path)
    p = root / ".claude/agents/qa-verifier.md"
    p.write_text(p.read_text(encoding="utf-8").replace("10 passed", "9 passed"),
                 encoding="utf-8")
    r = _audit_run(root, only=["audit.pytest-baseline"])
    assert r["exit_code"] == 2
    assert {f.severity for f in r["findings"]} == {"error"}
    assert len(r["findings"]) == 5, "불일치한 5곳을 모두 보여준다"


def test_audit_pytest_baseline_vs_measured(tmp_path):
    """ⓐ `--baseline` 실측값과 다르면 오류. 같으면 조용하다."""
    root = _audit_fixture(tmp_path)
    r = _audit_run(root, baseline="11 passed / 1 skipped",
                   only=["audit.pytest-baseline"])
    assert _codes(r) == [("audit.pytest-baseline", "--baseline", "error")]
    assert _audit_run(root, baseline=_AUDIT_BASELINE,
                      only=["audit.pytest-baseline"])["findings"] == []


def test_audit_pytest_baseline_ignores_claude_history(tmp_path):
    """ⓑ `CLAUDE.md` 이력의 과거 값(5→8)은 **오탐이 되지 않는다** — 마지막 행 오른쪽만 본다."""
    root = _audit_fixture(tmp_path)
    claude = root / "CLAUDE.md"
    claude.write_text(claude.read_text(encoding="utf-8") +
                      "| 2026-01-03 | 또 · pytest 기준선 10→**12 passed / 1 skipped** | 전체 | 다 |\n",
                      encoding="utf-8")
    r = _audit_run(root, only=["audit.pytest-baseline"])
    targets = [f.target for f in r["findings"]]
    assert any("CLAUDE.md" in t for t in targets), "마지막 행이 달라지면 잡아야 한다"
    assert all("5→8" not in str(f.evidence) for f in r["findings"])


def test_audit_vu_numbers_ghost_and_missing(tmp_path):
    """ⓒ 유령 V-U(문서에만)·누락 V-U(테스트에만) 각각 오류. 정본은 테스트 코드다."""
    root = _audit_fixture(tmp_path)
    d = root / "DESIGN.md"
    d.write_text(d.read_text(encoding="utf-8").replace(
        "| V-U2 | 나 | FR1.2 | `mock_scan_test.py` ① (pytest 아님) |",
        "| V-U2 | 나 | FR1.2 | `mock_scan_test.py` ① (pytest 아님) |\n"
        "| V-U3 | 유령 | FR2.1 | `tests/test_unit.py` §V-U3 |"), encoding="utf-8")
    t = root / "tests/test_unit.py"
    t.write_text(t.read_text(encoding="utf-8") + "# ─── V-U9: 코드에만 ───\n",
                 encoding="utf-8")
    r = _audit_run(root, only=["audit.vu-numbers"])
    assert _codes(r) == [("audit.vu-numbers", "V-U3", "error"),
                         ("audit.vu-numbers", "V-U9", "error")]


def test_audit_vu_numbers_exemptions(tmp_path):
    """ⓒ `**테스트 미구현**`(V-U3 = 정상 상태)와 **mock 위치 항목**(V-U2)은 면제된다."""
    root = _audit_fixture(tmp_path)
    d = root / "DESIGN.md"
    d.write_text(d.read_text(encoding="utf-8").replace(
        "#### 9.1b",
        "| V-U3 | 미구현 | FR2.1 | **테스트 미구현** (검증 의도만 보존) |\n\n#### 9.1b"),
        encoding="utf-8")
    sync = root / ".claude/skills/spec-sync/SKILL.md"
    sync.write_text(sync.read_text(encoding="utf-8").replace("V-U3", "V-U4"),
                    encoding="utf-8")
    d.write_text(d.read_text(encoding="utf-8").replace(
        "다음 신규 번호는 **V-U3**", "다음 신규 번호는 **V-U4**"), encoding="utf-8")
    r = _audit_run(root, only=["audit.vu-numbers", "audit.next-pointers"])
    assert r["findings"] == [], f"정상 상태를 오탐했다: {_codes(r)}"
    # 위치 열의 파일이 실제로 없으면 그때는 오류다
    (root / ".claude/skills/pipeline-verify/scripts/mock_scan_test.py").unlink()
    r = _audit_run(root, only=["audit.vu-numbers"])
    assert _codes(r) == [("audit.vu-numbers", "V-U2", "error")]


def test_audit_next_pointers_four_families(tmp_path):
    """ⓓ V-U·V-I·V-D·DQ 포인터가 정본 최대값+1과 다르면 오류(손으로 든 값은 어긋난다)."""
    root = _audit_fixture(tmp_path)
    sync = root / ".claude/skills/spec-sync/SKILL.md"
    sync.write_text("- 다음 번호: **V-U9** · V-I9 · **V-D9**. "
                    "DQ 다음 번호는 **DQ-9**이다\n", encoding="utf-8")
    r = _audit_run(root, only=["audit.next-pointers"])
    got = {f.target.split()[-1] for f in r["findings"]}
    assert got == {"V-U", "V-I", "V-D", "DQ"}
    assert all(f.severity == "error" for f in r["findings"])
    # 선점(문서 선행)은 정상 — 문서에만 있는 V-U 번호도 최대값에 포함된다
    assert _audit_run(root, only=["audit.next-pointers"])["exit_code"] == 2


def test_audit_fr_numbers_scope_and_duplicates(tmp_path):
    """ⓔ 중복 = 오류 · 결번 = 경고. **§6 행이 §3 정의로 세어지지 않는다**(허위 15건 회귀)."""
    root = _audit_fixture(tmp_path)
    assert _audit_run(root, only=["audit.fr-numbers"])["findings"] == []
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "| FR2.1 | 다 | 필수 |", "| FR2.1 | 다 | 필수 |\n| FR2.1 | 또 다 | 필수 |\n"
                                 "| FR2.4 | 결번 뒤 | 필수 |"), encoding="utf-8")
    r = _audit_run(root, only=["audit.fr-numbers"])
    assert ("audit.fr-numbers", "FR2.1", "error") in _codes(r)
    assert ("audit.fr-numbers", "FR2.2", "warn") in _codes(r)
    assert ("audit.fr-numbers", "FR2.3", "warn") in _codes(r)


def test_audit_dq_numbers_excludes_requirements_history(tmp_path):
    """ⓕ 중복 판정에서 **REQUIREMENTS §8 역사 표 제외**(허위 6건 회귀) · 미존재 참조는 오류."""
    root = _audit_fixture(tmp_path)
    assert _audit_run(root, only=["audit.dq-numbers"])["findings"] == []
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "| DQ-1 | 가 |", "| DQ-1 | 가 |\n| DQ-7 | §10에 없는 결정 |"), encoding="utf-8")
    r = _audit_run(root, only=["audit.dq-numbers"])
    assert _codes(r) == [("audit.dq-numbers", "DQ-7", "error")]


def test_audit_traceability_token_classification(tmp_path):
    """ⓖ 없는 파일·식별자·라우트는 오류 · **CSS 선택자·`""`·런타임 경로는 침묵**(잡음 140건 회귀)."""
    root = _audit_fixture(tmp_path)
    assert [f for f in _audit_run(root, only=["audit.traceability"])["findings"]
            if f.severity == "error"] == []
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "| FR2.1 | `helper_fn` · `GET /ping` | (API) |",
        "| FR2.1 | `helper_fn` · `GET /ping` · `ghost.py` · `noSuchFn` · `GET /nope` | (API) |"),
        encoding="utf-8")
    r = _audit_run(root, only=["audit.traceability"])
    errs = sorted(f.target for f in r["findings"] if f.severity == "error")
    assert errs == ["GET /nope", "ghost.py", "noSuchFn"], f"분류 실패: {_codes(r)}"


def test_audit_traceability_fr_coverage_warns(tmp_path):
    """§3에 있으나 §6에 없는 FR = **경고**(조치 방향은 사람이 정한다)."""
    root = _audit_fixture(tmp_path)
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "| FR2.1 | 다 | 필수 |", "| FR2.1 | 다 | 필수 |\n| FR2.2 | §6에 없다 | 필수 |"),
        encoding="utf-8")
    r = _audit_run(root, only=["audit.traceability"])
    assert ("audit.traceability", "FR2.2", "warn") in _codes(r)


def test_audit_planned_marker_exempts_absence(tmp_path):
    """ⓗ `(구현 예정)` 표기 행은 CLI·트레이서빌리티 부재를 **정보**로 낮춘다(문서 선행 = 정상)."""
    root = _audit_fixture(tmp_path)
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8")
                   .replace("| `./yt.sh audit` | 감사 | FR2.1 |",
                            "| `./yt.sh audit` | 감사 | FR2.1 |\n"
                            "| `./yt.sh doctor` | 점검 | FR2.1 **(구현 예정)** |")
                   .replace("| FR2.1 | `helper_fn` · `GET /ping` | (API) |",
                            "| FR2.1 | `helper_fn` · `GET /ping` · `future.py` | "
                            "(API) **(구현 예정)** |"), encoding="utf-8")
    r = _audit_run(root, only=["audit.cli-commands", "audit.traceability"])
    assert r["exit_code"] == 0
    assert ("audit.cli-commands", "doctor", "info") in _codes(r)
    assert ("audit.traceability", "future.py", "info") in _codes(r)


def test_audit_cli_commands_both_directions(tmp_path):
    """문서 정본은 **§5 ∪ DESIGN §8**이다 — 한쪽만 보면 오탐이 난다. 양방향 오류."""
    root = _audit_fixture(tmp_path)
    # DESIGN §8에만 있는 명령은 오탐이 아니다(합집합)
    d = root / "DESIGN.md"
    d.write_text(d.read_text(encoding="utf-8").replace("./yt.sh audit",
                                                       "./yt.sh audit\n./yt.sh serve"),
                 encoding="utf-8")
    m = root / "main.py"
    m.write_text(m.read_text(encoding="utf-8") + 'sub.add_parser("serve")\n',
                 encoding="utf-8")
    assert _audit_run(root, only=["audit.cli-commands"])["findings"] == []
    # 코드에만 있는 명령 · 문서에만 있는 명령 양쪽
    m.write_text(m.read_text(encoding="utf-8") + 'sub.add_parser("secret")\n',
                 encoding="utf-8")
    d.write_text(d.read_text(encoding="utf-8").replace("./yt.sh serve",
                                                       "./yt.sh serve\n./yt.sh ghostcmd"),
                 encoding="utf-8")
    r = _audit_run(root, only=["audit.cli-commands"])
    assert _codes(r) == [("audit.cli-commands", "ghostcmd", "error"),
                         ("audit.cli-commands", "secret", "error")]


def test_audit_doc_version_header_sync(tmp_path):
    """버전 헤더 3곳 ↔ `(FR1~FRn)` ↔ 실제 최대 FR."""
    root = _audit_fixture(tmp_path)
    assert _audit_run(root, only=["audit.doc-version"])["findings"] == []
    d = root / "DESIGN.md"
    d.write_text(d.read_text(encoding="utf-8")
                 .replace("**버전:** v1.0", "**버전:** v1.1")
                 .replace("(FR1~FR2)", "(FR1~FR9)"), encoding="utf-8")
    r = _audit_run(root, only=["audit.doc-version"])
    assert any(f.target == "DESIGN.md (FR1~FRn)" for f in r["findings"])
    assert sum(1 for f in r["findings"] if "버전" in f.target) >= 2
    assert all(f.severity == "error" for f in r["findings"])


def test_audit_waivers_exact_match_and_wildcard(tmp_path):
    """ⓘ 정확 일치 면제 → 종료코드 0 + `waived` 집계 · **와일드카드는 무효**(면제되지 않는다)."""
    root = _audit_fixture(tmp_path)
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "`helper_fn`", "`helper_fn` · `Reprocessor`"), encoding="utf-8")
    exact = [{"check": "audit.traceability", "target": "Reprocessor",
              "reason": "개념명", "added": "2026-09-26", "expires": None, "invalid": None}]
    r = _audit_run(root, only=["audit.traceability"], waivers=exact)
    assert r["exit_code"] == 0 and len(r["waived"]) == 1
    assert [f.target for f, _ in r["waived"]] == ["Reprocessor"]
    wild = [{"check": "audit.traceability", "target": "*", "reason": "은폐",
             "added": "2026-09-26", "expires": None,
             "invalid": "와일드카드·정규식을 쓸 수 없다(정확 일치만)"}]
    r = _audit_run(root, only=["audit.traceability"], waivers=wild)
    assert r["waived"] == [], "와일드카드로 면제되면 안 된다"
    assert ("audit.traceability", "Reprocessor", "error") in _codes(r)
    assert any(f.check == "waiver.invalid" for f in r["findings"])


def test_audit_waiver_stale_and_expired(tmp_path):
    """ⓙ 대응 발견 없는 waiver = `waiver.stale` 경고 · 만료된 waiver는 원래 심각도로 돌아온다."""
    root = _audit_fixture(tmp_path)
    stale = [{"check": "audit.traceability", "target": "없는대상", "reason": "옛 예외",
              "added": "2026-01-01", "expires": None, "invalid": None}]
    r = _audit_run(root, only=["audit.traceability"], waivers=stale)
    assert _codes(r) == [("waiver.stale", "audit.traceability/없는대상", "warn")]
    assert r["exit_code"] == 1
    # 만료 — `expires`는 선택 필드이며 지난 날짜면 면제하지 않는다
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "`helper_fn`", "`helper_fn` · `Reprocessor`"), encoding="utf-8")
    expired = [{"check": "audit.traceability", "target": "Reprocessor", "reason": "개념명",
                "added": "2026-01-01", "expires": "2026-01-02", "invalid": None}]
    r = _audit_run(root, only=["audit.traceability"], waivers=expired)
    assert r["waived"] == []
    assert ("audit.traceability", "Reprocessor", "error") in _codes(r)


def test_audit_waiver_file_parses_without_pyyaml(tmp_path, monkeypatch):
    """실제 `audit_waivers.yaml`이 PyYAML **없이도** 읽힌다(audit은 의존성 없이 성립한다)."""
    real = selfcheck.load_waivers()
    assert {(w["check"], w["target"]) for w in real} >= {
        ("audit.traceability", "Reprocessor"),
        ("doctor.meta-fields", "tickers/all-empty")}
    assert all(not w.get("invalid") for w in real), "정본 waiver 파일이 규약 위반이면 안 된다"
    import builtins
    orig = builtins.__import__

    def no_yaml(name, *a, **kw):
        if name == "yaml":
            raise ImportError("no yaml")
        return orig(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    fallback = selfcheck.load_waivers()
    assert [(w["check"], w["target"]) for w in fallback] == \
           [(w["check"], w["target"]) for w in real]


def test_audit_missing_document_is_exit_3(tmp_path):
    """ⓚ 문서 부재·파싱 불가는 **3**이다 — "이상 없음"(0)이라고 말하지 않는다."""
    root = _audit_fixture(tmp_path)
    (root / "DESIGN.md").unlink()
    with pytest.raises(selfcheck.CheckFailure):
        _audit_run(root)
    # 알 수 없는 검사 ID도 점검 실패다(조용히 건너뛰면 "이상 없음"으로 읽힌다)
    root2 = _audit_fixture(tmp_path / "b")
    with pytest.raises(selfcheck.CheckFailure):
        _audit_run(root2, only=["audit.no-such-check"])


def test_audit_json_schema_and_strict(tmp_path):
    """ⓛ `--json` 계약 3필드 + `summary.exit_code` · ⓖ `--strict`가 경고를 실패로 승격."""
    root = _audit_fixture(tmp_path)
    req = root / "REQUIREMENTS.md"
    req.write_text(req.read_text(encoding="utf-8").replace(
        "| FR2.1 | 다 | 필수 |", "| FR2.1 | 다 | 필수 |\n| FR2.2 | §6에 없다 | 필수 |"),
        encoding="utf-8")
    r = _audit_run(root, only=["audit.traceability"])
    assert r["exit_code"] == 1
    payload = json.loads(selfcheck.render_json(
        "audit", r["findings"], r["waived"], r["checks_run"], r["skipped"],
        r["elapsed"], r["exit_code"], stale=r["stale"]))
    assert payload["command"] == "audit"
    assert payload["summary"]["exit_code"] == 1
    assert payload["checks_run"] == ["audit.traceability"]
    for item in payload["findings"]:
        assert {"check", "target", "severity"} <= set(item)
    # 사람용 출력과 **같은 발견 집합**이어야 한다(형식만 다르다)
    assert len(payload["findings"]) == len(r["findings"])
    rs = _audit_run(root, only=["audit.traceability"], strict=True)
    assert rs["exit_code"] == 2 and len(rs["findings"]) == len(r["findings"])


# ════════════════════════════════════════════════════════════════════════════
# V-U36 — `doctor` 검사 계약 (FR38.13~38.15, DQ-55~56)
#
# 합성 `output/` 픽스처(채널 2개·그룹 1개·state·txt·meta·extract_log·가짜 chroma.sqlite3).
# **실데이터는 절대 건드리지 않는다** — `_isolate`로 config 경로를 tmp에 묶고,
# 실행 전후 픽스처 바이트·mtime 불변을 함께 고정한다(FR38.3 · DQ-55의 `mkdir` 부작용 금지).
# ════════════════════════════════════════════════════════════════════════════
def _fake_chroma(chroma_dir: Path, video_ids, *, broken=False):
    """`chroma.sqlite3` 축소판. chromadb를 쓰지 않고 우리가 읽는 스키마만 만든다."""
    import sqlite3 as _sq
    chroma_dir.mkdir(parents=True, exist_ok=True)
    con = _sq.connect(chroma_dir / "chroma.sqlite3")
    if broken:                      # 테이블 이름이 다르면 "건너뜀(정보)"이어야 한다
        con.execute("CREATE TABLE other_table (a TEXT)")
    else:
        con.execute("CREATE TABLE IF NOT EXISTS embedding_metadata "
                    "(id INTEGER, key TEXT, string_value TEXT)")
        for i, vid in enumerate(video_ids):
            con.execute("INSERT INTO embedding_metadata VALUES (?,?,?)", (i, "video_id", vid))
    con.commit()
    con.close()


def _mkvideo(cd: Path, vid, base, *, upload="20260101", duration=100, sub_type="auto",
             extra=None):
    for sub in ("txt", "meta", "srt", "desc"):
        (cd / sub).mkdir(parents=True, exist_ok=True)
    (cd / "txt" / f"{base}.txt").write_text("본문", encoding="utf-8")
    # `tickers: []`·`modified_date: None`은 실데이터의 모양이다(전 코퍼스가 빈 값) —
    # `meta-fields` ⓐ가 그것을 잡고 waiver가 침묵시키는 경로를 함께 고정한다
    meta = {"id": vid, "title": base, "upload_date": upload, "duration": duration,
            "sub_type": sub_type, "content_type": "video", "tickers": [],
            "modified_date": None}
    meta.update(extra or {})
    (cd / "meta" / f"{base}.json").write_text(json.dumps(meta, ensure_ascii=False),
                                              encoding="utf-8")
    return {"upload_date": upload, "modified_date": None, "sub_type": sub_type,
            "extracted_at": "2026-09-01T00:00:00", "basename": base}


def _write_log(cd: Path, rows, *, header=None, bom=True, mid_bom=False):
    cols = header or ["video_id", "upload_date", "title", "action", "sub_type",
                      "status", "basename"]
    body = ",".join(cols) + "\n" + "".join(",".join(r) + "\n" for r in rows)
    data = ("﻿" if bom else "") + body
    raw = data.encode("utf-8")
    if mid_bom:
        raw = raw + "﻿".encode("utf-8") + b"x,y,z,w,v,u,t\n"
    (cd / "extract_log.csv").write_bytes(raw)


def _doctor_fixture(tmp_path, monkeypatch):
    """발견 **0건**인 정상 `output/`. 모든 음성 대조군의 기준선이다."""
    out = _isolate(tmp_path, monkeypatch)
    import cookie_health
    monkeypatch.setattr(config, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(config, "FIREFOX_PROFILE", tmp_path / "ff")
    monkeypatch.setattr(cookie_health, "STATUS_FILE", out / ".cookie_status.json")

    reg = ChannelRegistry()
    reg.add("https://youtube.com/@ch1")
    reg.add("https://youtube.com/@ch2")
    reg.set_group("ch2", "G1")

    ch1 = out / "ch1"
    st1 = {"v1": _mkvideo(ch1, "v1", "20260101_가"),
           "v2": _mkvideo(ch1, "v2", "20260102_나", upload="20260102", duration=200)}
    (ch1 / "state.json").write_text(json.dumps(st1, ensure_ascii=False), encoding="utf-8")
    _write_log(ch1, [["v1", "20260101", "가", "new", "auto", "ok", "20260101_가"],
                     ["v2", "20260102", "나", "new", "auto", "ok", "20260102_나"]])
    _fake_chroma(ch1 / "chroma", ["v1", "v2"])

    ch2 = out / "G1" / "ch2"
    st2 = {"v3": _mkvideo(ch2, "v3", "20260103_다", upload="20260103", duration=300),
           "v4": _mkvideo(ch2, "v4", "20260104_라", upload="20260104", duration=400,
                          sub_type="manual")}
    (ch2 / "state.json").write_text(json.dumps(st2, ensure_ascii=False), encoding="utf-8")
    _write_log(ch2, [["v3", "20260103", "다", "new", "auto", "ok", "20260103_다"]])
    _fake_chroma(ch2 / "chroma", ["v3", "v4"])
    return out


_TICKER_WAIVER = [{"check": "doctor.meta-fields", "target": "tickers/all-empty",
                   "reason": "DQ-43", "added": "2026-09-26", "expires": None,
                   "invalid": None},
                  {"check": "doctor.meta-fields", "target": "modified_date/all-empty",
                   "reason": "FR2.6", "added": "2026-09-26", "expires": None,
                   "invalid": None}]


def _doctor_run(only=None, channel=None, strict=False, now=None, waivers=None,
                grace=7):
    ctx = selfcheck.DoctorContext(channel=channel, now=now, cookie_grace_days=grace)
    return selfcheck.run("doctor", ctx, only=only, strict=strict,
                         waivers=_TICKER_WAIVER if waivers is None else waivers)


def test_doctor_clean_fixture_is_silent(tmp_path, monkeypatch):
    """음성 대조군 — 정상 `output/`에서는 0건·종료코드 0(waiver 2건은 세어서 노출)."""
    _doctor_fixture(tmp_path, monkeypatch)
    r = _doctor_run()
    assert r["findings"] == [], f"오탐: {_codes(r)}"
    assert r["exit_code"] == 0 and len(r["waived"]) == 2


def test_doctor_is_read_only(tmp_path, monkeypatch):
    """ⓝ 실행 전후 픽스처 전체 바이트·mtime 불변 · **디렉터리도 만들지 않는다**.

    `chroma/`를 `chromadb.PersistentClient`나 `KLIndexer`로 열면 이 시험이 깨진다
    (`_get_client()`가 `mkdir(parents=True, exist_ok=True)`를 한다 — DQ-55).
    """
    out = _doctor_fixture(tmp_path, monkeypatch)
    ch3 = out / "G1" / "ch3-no-chroma"        # chroma/가 없는 채널 (실측 6채널의 모양)
    (ch3 / "txt").mkdir(parents=True)
    (ch3 / "state.json").write_text("{}", encoding="utf-8")
    before = _snapshot(out)
    _doctor_run()
    after = _snapshot(out)
    assert after == before, "읽기 전용 위반"
    assert not (ch3 / "chroma").exists(), "chroma/ 디렉터리를 만들면 안 된다"


def test_doctor_state_files_both_directions(tmp_path, monkeypatch):
    """ⓐ 레코드 있고 txt 없음 / txt 있고 레코드 없음 — **양방향 오류**."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    (out / "ch1" / "txt" / "20260101_가.txt").unlink()
    (out / "ch1" / "txt" / "떠돌이.txt").write_text("x", encoding="utf-8")
    r = _doctor_run(only=["doctor.state-files"])
    assert _codes(r) == [("doctor.state-files", "ch1/txt/떠돌이", "error"),
                         ("doctor.state-files", "ch1/v1", "error")]
    assert r["exit_code"] == 2


def test_doctor_basename_collision(tmp_path, monkeypatch):
    """ⓑ 같은 basename을 두 video_id가 쓰면 srt·txt·meta가 조용히 덮어써진다 → 오류."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    st = json.loads((out / "ch1" / "state.json").read_text(encoding="utf-8"))
    st["v2"]["basename"] = st["v1"]["basename"]
    (out / "ch1" / "state.json").write_text(json.dumps(st, ensure_ascii=False),
                                            encoding="utf-8")
    r = _doctor_run(only=["doctor.basename-collision"])
    assert _codes(r) == [("doctor.basename-collision", "ch1/20260101_가", "error")]


def test_doctor_orphan_dirs_and_unextracted(tmp_path, monkeypatch):
    """ⓒ 미등록 채널형 폴더 = 경고(지울지 등록할지는 사람) · 등록됐지만 미추출 = 정보."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    (out / "G1" / "잔존채널" / "srt").mkdir(parents=True)
    ChannelRegistry().add("https://youtube.com/@ch9")
    r = _doctor_run(only=["doctor.orphan-dirs", "doctor.registry-paths"])
    assert ("doctor.orphan-dirs", "output/G1/잔존채널", "warn") in _codes(r)
    assert ("doctor.registry-paths", "ch9", "info") in _codes(r)
    assert r["exit_code"] == 1, "미등록 폴더는 경고 상한이다"


def test_doctor_registry_paths_flat_leftover_is_error(tmp_path, monkeypatch):
    """ⓓ 그룹 지정 채널이 평면 위치에 남아 있으면 **오류** — 대시보드에 빈 채널로 보이고
    전량 재추출로 이어진 FR35 런북의 사고다."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    shutil.move(str(out / "G1" / "ch2"), str(out / "ch2"))
    r = _doctor_run(only=["doctor.registry-paths"])
    assert _codes(r) == [("doctor.registry-paths", "ch2", "error")]
    assert r["findings"][0].evidence["기대"] == "G1/ch2"


def test_doctor_index_coverage_missing_and_orphan(tmp_path, monkeypatch):
    """ⓔ 자막이 chroma에 없으면 경고(자막은 있으니 **검색에서만 조용히 빠진다**) ·
    인덱스에만 있어도 경고."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    shutil.rmtree(out / "ch1" / "chroma")                    # chroma/ 자체가 없는 채널
    _fake_chroma(out / "G1" / "ch2" / "chroma", ["v3", "v4", "유령"])
    r = _doctor_run(only=["doctor.index-coverage"])
    codes = _codes(r)
    assert ("doctor.index-coverage", "ch1", "warn") in codes
    assert ("doctor.index-coverage", "ch2/index-orphan", "warn") in codes
    ev = [f.evidence for f in r["findings"] if f.target == "ch1"][0]
    assert ev == {"subtitled": 2, "indexed": 0, "missing": 2, "ids": "v1,v2"}
    assert "chroma/ 미생성" in [f.message for f in r["findings"] if f.target == "ch1"][0]


def test_doctor_index_coverage_schema_mismatch_is_skip(tmp_path, monkeypatch):
    """ⓕ `embedding_metadata`가 없으면 **오류가 아니라 건너뜀(정보)** 이고 파일을 만들지 않는다
    — 라이브러리 업그레이드가 감사 실패로 나타나면 도구가 신뢰를 잃는다(DQ-55)."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    shutil.rmtree(out / "ch1" / "chroma")
    _fake_chroma(out / "ch1" / "chroma", [], broken=True)
    before = _snapshot(out)
    r = _doctor_run(only=["doctor.index-coverage"])
    assert _codes(r) == [("doctor.index-coverage", "ch1", "info")]
    assert r["exit_code"] == 0
    assert r["skipped"] and r["skipped"][0]["check"] == "doctor.index-coverage"
    assert _snapshot(out) == before


def test_doctor_extract_log_hygiene(tmp_path, monkeypatch):
    """ⓖ 완전 동일 행 = 경고 · **선두 BOM은 정상 파싱하고 침묵** · 열 수 불일치 = 오류."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    row = ["v1", "20260101", "가", "new", "auto", "ok", "20260101_가"]
    _write_log(out / "ch1", [row, row, row], bom=True)       # BOM + 중복 2건
    r = _doctor_run(only=["doctor.extract-log"])
    assert _codes(r) == [("doctor.extract-log", "ch1/duplicate-rows", "warn")], \
        "선두 BOM(utf-8-sig)은 extractor가 의도적으로 쓴다 — 경고로 올리면 98/98 파일이 시끄럽다"
    assert r["findings"][0].evidence["duplicate_rows"] == 2
    # 열 수 불일치·헤더 손상은 오류
    _write_log(out / "ch1", [row[:3]], header=["a", "b", "c", "d", "e", "f", "g"])
    r = _doctor_run(only=["doctor.extract-log"])
    sev = {f.target: f.severity for f in r["findings"]}
    assert sev["ch1/header"] == "error" and sev["ch1/columns"] == "error"


def test_doctor_extract_log_mid_file_bom_is_warn(tmp_path, monkeypatch):
    """중간 BOM(append 경로 손상)만 경고다 — 정상과 이상을 구분한다."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    _write_log(out / "ch1", [["v1", "20260101", "가", "new", "auto", "ok", "20260101_가"]],
               bom=True, mid_bom=True)
    r = _doctor_run(only=["doctor.extract-log"])
    assert ("doctor.extract-log", "ch1/bom", "warn") in _codes(r)


def test_doctor_detector_fossils_reappraises_reasons(tmp_path, monkeypatch):
    """ⓗ 한국어·영어 멤버십 문구 양쪽이 오류 · `429`는 정보 · **무관한 오류 사유는 침묵**.

    시계열이 아니라 **같은 데이터 안의 모순**을 본다(로그에 timestamp 열이 없고
    `members_only` 레코드의 `extracted_at`이 비어 있다 — DQ-56).
    """
    out = _doctor_fixture(tmp_path, monkeypatch)
    ko = "ERROR: [youtube] aetOCkgzurM: 이 동영상은 변곡점주식VIP 회원 등급 이상의 채널 회"
    en = "ERROR: [youtube] xx: Join this channel to get access to members-only content"
    _write_log(out / "ch1", [
        ["aetOCkgzurM", "0", "화석ko", "new", "none", "error:" + ko, ""],
        ["xx", "0", "화석en", "new", "none", "error:" + en, ""],
        ["yy", "0", "차단", "new", "none", "error:HTTP Error 429: Too Many Requests", ""],
        ["zz", "0", "무관", "new", "none", "error:ERROR: unable to download webpage", ""],
    ])
    r = _doctor_run(only=["doctor.detector-fossils"])
    codes = _codes(r)
    assert ("doctor.detector-fossils", "ch1/aetOCkgzurM", "error") in codes
    assert ("doctor.detector-fossils", "ch1/xx", "error") in codes
    assert ("doctor.detector-fossils", "error:429", "info") in codes
    assert not any(t == "ch1/zz" for _, t, _ in codes), "멤버십과 무관한 사유는 침묵해야 한다"
    assert [f.evidence["rows"] for f in r["findings"] if f.target == "error:429"] == [1]


def test_doctor_meta_fields_generalized_signals(tmp_path, monkeypatch):
    """ⓘ "값이 있는데 distinct 1"·전량 빈 값 = 경고 · 열거형 면제 · `tickers`는 waiver로 침묵."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    # 전 코퍼스가 같은 값 = 기록 경로 고장·상수 오염 신호 (필드 이름을 하드코딩하지 않는다)
    for ch, names in ((out / "ch1", ["20260101_가", "20260102_나"]),
                      (out / "G1" / "ch2", ["20260103_다", "20260104_라"])):
        for n in names:
            p = ch / "meta" / f"{n}.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["uploader"] = "같은값"
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    r = _doctor_run(only=["doctor.meta-fields"])
    assert ("doctor.meta-fields", "uploader/single-value", "warn") in _codes(r)
    # `sub_type`은 열거형이라 distinct 1이 정상 → 면제되어야 한다
    assert not any("sub_type" in t for _, t, _ in _codes(r))
    # `tickers` 전량 빈 값은 waiver로 침묵하고 **세어서** 노출된다
    assert ("doctor.meta-fields", "tickers/all-empty") in \
           {(f.check, f.target) for f, _ in r["waived"]}


def test_doctor_meta_fields_format_contract(tmp_path, monkeypatch):
    """ⓘ `upload_date == "00000000"`은 정보(FR2.6) · 다른 비8자리·음수 duration은 오류."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    p = out / "ch1" / "meta" / "20260101_가.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["upload_date"] = "00000000"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    p2 = out / "ch1" / "meta" / "20260102_나.json"
    d2 = json.loads(p2.read_text(encoding="utf-8"))
    d2["upload_date"] = "2026-01-02"
    d2["duration"] = -5
    d2["chapters"] = [{"start": 0, "title": "가"}, {"start": 10}]
    p2.write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
    r = _doctor_run(only=["doctor.meta-fields"])
    sev = {}
    for f in r["findings"]:
        sev.setdefault(f.target, []).append(f.severity)
    assert sev["upload_date/00000000"] == ["info"]
    assert sev["ch1/20260102_나"].count("error") == 3


def test_doctor_scheduler_states(tmp_path, monkeypatch):
    """ⓙ 적체·백오프 = 경고 · 열거 밖 = 오류 · **기본 상태(`enabled:false`)에서는 0건**.

    열거 위반은 `load_state()`의 `_sanitize`가 조용히 교정하므로 **원본 파일**과
    대조해야만 보인다 — 교정된 값만 보면 영원히 잡히지 않는다.
    """
    out = _doctor_fixture(tmp_path, monkeypatch)
    assert _doctor_run(only=["doctor.scheduler"])["findings"] == []
    path = out / ".scheduler.json"
    now = __import__("datetime").datetime(2026, 9, 26, 12, 0, 0)
    path.write_text(json.dumps({"enabled": True, "interval_days": 5,
                                "max_videos_per_cycle": 9999,
                                "last_run_at": "2026-08-01T00:00:00",
                                "skip_cycles": 2, "paused_reason": "cookie"}),
                    encoding="utf-8")
    r = _doctor_run(only=["doctor.scheduler"], now=now)
    got = {f.target: f.severity for f in r["findings"]}
    assert got == {"interval_days": "error", "max_videos_per_cycle": "error",
                   "paused_reason": "warn", "skip_cycles": "warn",
                   "last_run_at": "warn"}
    # 껐으면 적체·백오프는 정상 상태다(상시 빨간 게이트는 꺼진 게이트다)
    path.write_text(json.dumps({"enabled": False, "interval_days": 3,
                                "last_run_at": "2026-01-01T00:00:00",
                                "skip_cycles": 3}), encoding="utf-8")
    assert _doctor_run(only=["doctor.scheduler"], now=now)["findings"] == []


def test_doctor_cookie_status_uses_get_status(tmp_path, monkeypatch):
    """ⓚ N일 방치 경고(가짜 시계) · **쿠키 갱신으로 자동 해제된 경고는 오탐이 되지 않는다**.

    상태 파일의 `invalid: true`를 직접 읽으면 이미 해소된 경고를 "48일 방치"로 보고한다
    — 실측으로 확인된 오탐이며 `cookie_health.get_status()`(FR19.3) 경유가 유일한 정답이다.
    """
    out = _doctor_fixture(tmp_path, monkeypatch)
    import cookie_health
    now = __import__("datetime").datetime(2026, 9, 26, 12, 0, 0)
    ck = tmp_path / "cookies.txt"
    ck.write_text("# cookies", encoding="utf-8")
    os.utime(ck, (1754600000, 1754600000))          # 2026-08-08 이전
    (out / ".cookie_status.json").write_text(json.dumps(
        {"invalid": True, "message": "no longer valid",
         "detected_at": "2026-08-08T08:11:05"}), encoding="utf-8")
    r = _doctor_run(only=["doctor.cookie-status"], now=now)
    assert _codes(r) == [("doctor.cookie-status", "cookie", "warn")]
    assert r["findings"][0].evidence["days"] == 49
    # 유예 기간 안이면 침묵
    assert _doctor_run(only=["doctor.cookie-status"], now=now, grace=90)["findings"] == []
    # 쿠키를 경고 이후에 갱신 → get_status()가 자동 해제 → **0건**
    os.utime(ck, None)
    assert _doctor_run(only=["doctor.cookie-status"], now=now)["findings"] == []


def test_doctor_channel_scope_and_missing_output(tmp_path, monkeypatch):
    """ⓜ 채널 인자로 범위 한정 · ⓛ `output/` 부재·미등록 채널은 **종료코드 3**."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    shutil.rmtree(out / "ch1" / "chroma")
    assert [f.target for f in _doctor_run(only=["doctor.index-coverage"])["findings"]] == ["ch1"]
    r = _doctor_run(only=["doctor.index-coverage"], channel="ch2")
    assert r["findings"] == [], "다른 채널의 이상은 범위 밖이다"
    with pytest.raises(selfcheck.CheckFailure):
        _doctor_run(channel="없는채널")
    shutil.rmtree(out)
    with pytest.raises(selfcheck.CheckFailure):
        _doctor_run()


def test_doctor_registry_and_state_parse_failure_is_exit_3(tmp_path, monkeypatch):
    """손상된 `state.json`은 **확인 못 함(3)** 이다 — 0건("이상 없음")으로 넘기지 않는다."""
    out = _doctor_fixture(tmp_path, monkeypatch)
    (out / "ch1" / "state.json").write_text("{깨진 JSON", encoding="utf-8")
    with pytest.raises(selfcheck.CheckFailure):
        _doctor_run()


def test_doctor_channel_scope_skips_corpus_signal(tmp_path, monkeypatch):
    """채널 1개 범위에서는 `meta-fields` **코퍼스 신호를 건너뛴다**(채널 안의 균일함은 정상).

    실측 회귀: `./yt.sh doctor 호두감자`가 `categories/single-value`·`tags/all-empty`를
    오탐 2건으로 냈다 — 한 채널의 58편이 같은 카테고리인 것은 정상이다.
    건너뛴 검사의 waiver는 **stale로 올리지 않는다**(돌지 않은 검사로 예외를 썩었다고 하면 그것도 오탐).
    """
    out = _doctor_fixture(tmp_path, monkeypatch)
    for ch, names in ((out / "ch1", ["20260101_가", "20260102_나"]),
                      (out / "G1" / "ch2", ["20260103_다", "20260104_라"])):
        for base in names:
            p = ch / "meta" / f"{base}.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["categories"] = ["주식"]                  # 채널 안에서 균일 = 정상
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    full = _doctor_run(only=["doctor.meta-fields"])
    assert any(f.target == "categories/single-value" for f in full["findings"]), \
        "채널을 가로지르면 신호가 살아 있어야 한다"
    one = _doctor_run(only=["doctor.meta-fields"], channel="ch1")
    assert [f.target for f in one["findings"]] == [selfcheck.WHOLE_CHECK]
    assert one["findings"][0].severity == "info" and one["exit_code"] == 0
    assert one["skipped"] and one["skipped"][0]["target"] == selfcheck.WHOLE_CHECK
    assert not any(f.check == "waiver.stale" for f in one["findings"])

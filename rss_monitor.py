"""RSS 기반 새 영상 감지. FR29 — 요청이 가벼워 429 예산과 무관."""
import re
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config
from channel_registry import ChannelRegistry
from state_manager import StateManager

log = logging.getLogger("rss")

_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
# 채널 페이지에서 channel_id를 찾는 패턴들 — 페이지 구성에 따라 위치가 달라
# 여러 패턴을 순서대로 시도한다 (externalId가 가장 안정적)
_CHANNEL_ID_RES = (
    re.compile(r'"externalId"\s*:\s*"(UC[\w-]+)"'),
    re.compile(r'"channelId"\s*:\s*"(UC[\w-]+)"'),
    re.compile(r'feeds/videos\.xml\?channel_id=(UC[\w-]+)'),
    re.compile(r'youtube\.com/channel/(UC[\w-]+)'),
)
_NS = {"a": "http://www.w3.org/2005/Atom",
       "yt": "http://www.youtube.com/xml/schemas/2015"}
_TIMEOUT = 10   # 초 (FR29.4)


def _http_get(url: str) -> str:
    # 한글 핸들(@두두감자 등) — 경로의 비ASCII 문자를 percent-encode (urllib 요구)
    url = urllib.parse.quote(url, safe=":/?&=@%")
    req = urllib.request.Request(url, headers={"User-Agent": config.SUB_FETCH_UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def resolve_channel_id(channel_url: str) -> str:
    """채널 페이지 HTML에서 channel_id(UC…) 해석 (FR29.1). 실패 시 ValueError."""
    # /channel/UC… URL이면 즉시 추출
    m = re.search(r"/channel/(UC[\w-]+)", channel_url)
    if m:
        return m.group(1)
    html = _http_get(re.sub(r"/videos/?$", "", channel_url))
    for pattern in _CHANNEL_ID_RES:
        m = pattern.search(html)
        if m:
            return m.group(1)
    raise ValueError(f"channel_id를 찾을 수 없습니다: {channel_url[:60]}")


def fetch_feed(channel_id: str) -> list:
    """RSS 피드 → [{id, title, published}] (최신순, 최대 15개 — YouTube 제공 한계)."""
    root = ET.fromstring(_http_get(_FEED_URL.format(cid=channel_id)))
    out = []
    for entry in root.findall("a:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS)
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": entry.findtext("a:title", default=vid, namespaces=_NS),
            "published": (entry.findtext("a:published", default="", namespaces=_NS))[:10],
        })
    return out


def check_new_videos() -> dict:
    """
    등록 채널 전체의 RSS를 순차 조회해 state에 없는 새 영상을 채널별로 반환 (FR29.2).
    반환: {"channels": {채널명: [{id,title,published}]}, "errors": {채널명: 사유}}
    channel_id는 최초 1회 해석 후 channels.yaml에 캐시된다 (FR29.1).
    """
    reg = ChannelRegistry()
    new_by_channel, errors = {}, {}
    for name, ch in reg.list().items():
        ch = ch or {}
        cid = ch.get("channel_id")
        if not cid:
            try:
                cid = resolve_channel_id(ch.get("url", ""))
                reg.set_channel_id(name, cid)
            except Exception as exc:
                errors[name] = f"channel_id 해석 실패: {str(exc)[:60]}"
                continue
        try:
            entries = fetch_feed(cid)
        except Exception as exc:
            errors[name] = f"RSS 조회 실패: {str(exc)[:60]}"
            continue
        state = StateManager(name).state
        fresh = [e for e in entries if e["id"] not in state]
        if fresh:
            new_by_channel[name] = fresh
    return {"channels": new_by_channel, "errors": errors}

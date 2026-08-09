"""영상 메타데이터·설명 수집·저장. FR3, FR12.2."""
import re
import json
import datetime

import config


# 한국 종목코드(6자리) / 미국 티커($AAPL, 대문자 1~5자) 패턴
_TICKER_KR = re.compile(r'\b(\d{6})\b')
_TICKER_US = re.compile(r'\$([A-Z]{1,5})\b')


def extract_tickers(text: str) -> list:
    """텍스트에서 종목코드/티커 추출. FR12.2 (best-effort)."""
    if not text:
        return []
    kr = _TICKER_KR.findall(text)
    us = _TICKER_US.findall(text)
    seen, result = set(), []
    for t in kr + us:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# FR3.2 수집 필드
META_FIELDS = [
    "id", "title", "upload_date", "modified_date",
    "duration", "duration_string", "view_count",
    "like_count", "comment_count", "tags", "categories",
    "thumbnail", "webpage_url", "channel",
]


class MetaCollector:

    def __init__(self, channel: str):
        self.dirs = config.channel_subdirs(channel)

    def save(self, info: dict, basename: str, sub_type: str,
             playlists: list = None, content_type: str = "video") -> dict:
        """
        meta/*.json + desc/*.txt 저장.
        반환: 저장된 meta dict.
        """
        meta = {k: info.get(k) for k in META_FIELDS}
        meta["sub_type"] = sub_type
        meta["playlists"] = playlists or []      # 재생목록 카테고리 (FR15.2)
        meta["content_type"] = content_type      # "video" | "live" (FR16.4)
        meta["extracted_at"] = datetime.datetime.now().isoformat(timespec="seconds")

        # 종목/티커 추출 (제목 + 설명 + 태그)
        text_for_ticker = " ".join(filter(None, [
            info.get("title", ""),
            info.get("description", ""),
            " ".join(info.get("tags") or []),
        ]))
        meta["tickers"] = extract_tickers(text_for_ticker)

        # meta json 저장
        self.dirs["meta"].mkdir(parents=True, exist_ok=True)
        meta_path = self.dirs["meta"] / f"{basename}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 설명 저장 (FR3.3, FR3.4: 없으면 스킵)
        desc = (info.get("description") or "").strip()
        if desc:
            self.dirs["desc"].mkdir(parents=True, exist_ok=True)
            desc_path = self.dirs["desc"] / f"{basename}.txt"
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(desc)

        return meta

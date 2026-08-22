"""채널 등록·조회·삭제 (channels.yaml 관리). FR7."""
import re
import datetime
from urllib.parse import unquote

import yaml
import config


class ChannelRegistry:

    def __init__(self, yaml_path=None):
        self.path = yaml_path or config.CHANNELS_YAML
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"channels": {}}
        with open(self.path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"channels": {}}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)

    @staticmethod
    def extract_handle(url: str) -> str:
        """
        URL에서 채널명 추출. FR7.6
        https://youtube.com/@두두감자        → 두두감자
        https://youtube.com/@handle/videos   → handle
        %EB.. 인코딩된 핸들도 디코드 처리
        """
        url = unquote(url)
        m = re.search(r'@([^/?&\s]+)', url)
        if m:
            return m.group(1)
        # /channel/UC... 형태 폴백
        m = re.search(r'/channel/([^/?&\s]+)', url)
        if m:
            return m.group(1)
        raise ValueError(f"채널명을 URL에서 추출할 수 없습니다: {url}")

    @staticmethod
    def normalize_url(url: str) -> str:
        """채널 영상 목록 URL로 정규화 (디코드 + /videos 부착)."""
        url = unquote(url).rstrip('/')
        if not url.endswith('/videos'):
            url = url + '/videos'
        return url

    def add(self, url: str, lang: str = config.DEFAULT_LANG, note: str = "") -> str:
        """채널 등록. 반환: 채널명."""
        name = self.extract_handle(url)
        self.data.setdefault("channels", {})[name] = {
            "url": self.normalize_url(url),
            "lang": lang,
            "added_at": datetime.date.today().isoformat(),
            "note": note,
        }
        self._save()
        return name

    def set_channel_id(self, name: str, channel_id: str):
        """RSS용 channel_id(UC…) 캐시. FR29.1"""
        ch = self.data.get("channels", {}).get(name)
        if ch is None:
            raise KeyError(f"등록되지 않은 채널: {name}")
        ch["channel_id"] = channel_id
        self._save()

    def set_group(self, name: str, group: str = None) -> str:
        """채널 폴더(그룹) 지정. FR25.1 — 빈 값/None이면 필드 제거(해제)."""
        ch = self.data.get("channels", {}).get(name)
        if ch is None:
            raise KeyError(f"등록되지 않은 채널: {name}")
        group = (group or "").strip()
        if group:
            ch["group"] = group
        else:
            ch.pop("group", None)
        self._save()
        return group

    def remove(self, name: str) -> bool:
        if name in self.data.get("channels", {}):
            del self.data["channels"][name]
            self._save()
            return True
        return False

    def list(self) -> dict:
        return self.data.get("channels", {})

    def get(self, name: str) -> dict:
        ch = self.data.get("channels", {}).get(name)
        if not ch:
            raise KeyError(f"등록되지 않은 채널: {name}")
        return {"name": name, **ch}

    def names(self) -> list:
        return list(self.data.get("channels", {}).keys())

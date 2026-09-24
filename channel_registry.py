"""채널 등록·조회·삭제 (channels.yaml 관리). FR7."""
import re
import datetime
from urllib.parse import unquote

import yaml
import config

NOTE_MAX_LEN = 200          # 채널 메모 상한 (FR36.1·DQ-39)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")   # 제어문자(개행·탭 포함) → 공백


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
        # FR35.3 2차 안전망 — 같은 프로세스의 channel_dir()가 즉시 새 group을 반영한다.
        # (1차는 config 쪽 mtime+size 자동 감지라 이 호출을 빠뜨려도 조용히 깨지지 않는다)
        config.invalidate_group_cache()

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

    def resolve_name(self, url: str) -> str:
        """
        URL → **등록된** 채널명 역조회. FR32.2 (DQ-19)

        `extract_handle`은 신규 등록용 규칙(DQ-07)이라 등록명과 핸들이 다른
        채널(개명·핸들 변경)에서 없는 폴더를 가리킨다. 조회 경로는 레지스트리를
        진실로 삼고, 매칭 실패 시에만 핸들로 폴백한다.
        """
        try:
            handle = self.extract_handle(url).lower()
        except ValueError:
            handle = None
        if handle:
            for name, cfg in self.list().items():
                if name.lower() == handle:
                    return name
                try:
                    if self.extract_handle(cfg.get("url") or "").lower() == handle:
                        return name
                except ValueError:
                    continue
        return self.extract_handle(url)      # 미등록 → 기존 규칙 유지

    @staticmethod
    def normalize_url(url: str) -> str:
        """채널 영상 목록 URL로 정규화 (디코드 + /videos 부착)."""
        url = unquote(url).rstrip('/')
        if not url.endswith('/videos'):
            url = url + '/videos'
        return url

    def add(self, url: str, lang: str = config.DEFAULT_LANG, note: str = "") -> str:
        """
        채널 등록 — **upsert**. 반환: 채널명. FR7.7~7.9 (DQ-37)

        기존 항목을 통째로 덮어쓰지 않는다: `url`·`lang`만 갱신하고
        `group`(FR25.1)·`auto_run`(FR34.7)·`channel_id`(FR29.1)·`added_at`은 보존한다
        (`note`는 인자가 비어 있지 않을 때만). `rename()`이 약속한 "설정 보존"과 같은 계약이다.
        이름 해석은 `resolve_name`(FR7.8) — 개명 채널에 두 번째 항목을 만들지 않는다.
        이름은 경로 세그먼트 검증(FR7.9)을 통과해야 한다.
        """
        name = self.resolve_name(url)                 # FR7.8 (미등록이면 extract_handle 폴백)
        checked = config.validate_path_segment(name)  # FR7.9
        channels = self.data.setdefault("channels", {})
        if name not in channels:
            name = checked                            # 신규는 NFC 정규화 이름으로 등록
        existing = channels.get(name)
        if isinstance(existing, dict):                # ── upsert (기존 설정 보존)
            existing["url"] = self.normalize_url(url)
            existing["lang"] = lang
            if note:
                existing["note"] = note
        else:                                         # ── 신규 등록
            channels[name] = {
                "url": self.normalize_url(url),
                "lang": lang,
                "added_at": datetime.date.today().isoformat(),
                "note": note,
            }
        self._save()
        return name

    def rename(self, old: str, new: str):
        """채널 이름 변경 — 설정(group·channel_id·auto_run 포함) 보존. FR31.1
        레지스트리만 변경하며 output 폴더 이동은 호출자(renamer) 책임."""
        channels = self.data.get("channels", {})
        if old not in channels:
            raise KeyError(f"등록되지 않은 채널: {old}")
        new = config.validate_path_segment(new)      # FR7.9
        if new in channels:
            raise ValueError(f"이미 존재하는 채널 이름: {new}")
        channels[new] = channels.pop(old)
        self._save()

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

    def set_note(self, name: str, note: str) -> str:
        """
        채널 메모 — 한 줄 평문. FR36.1 (DQ-39)

        제어문자(U+0000~U+001F·U+007F)를 **공백으로 치환**한 뒤 트림하고,
        200자를 넘으면 `ValueError`다 — **잘라내지 않는다**(조용한 절삭은 사용자 텍스트 소실).

        빈 값이어도 필드를 제거하지 않고 **`note: ""`로 되돌린다**: `add()`가 신규 등록 시
        항상 `note: ""`를 쓰고 기존 채널도 전부 그 형태라, `set_group`·`set_auto_run`식
        "기본값이면 pop"을 흉내 내면 yaml이 불균일해지고 무의미한 대량 diff가 난다.

        `add()`는 손대지 않는다 — FR7.7의 "note는 인자가 비어 있지 않을 때만 갱신"이
        그대로 유효하며, 메모 쓰기 통로는 이 메서드 하나다 (FR36.12ⓑ).
        """
        ch = self.data.get("channels", {}).get(name)
        if ch is None:
            raise KeyError(f"등록되지 않은 채널: {name}")
        cleaned = _CTRL_RE.sub(" ", note or "").strip()
        if len(cleaned) > NOTE_MAX_LEN:
            raise ValueError(
                f"메모는 {NOTE_MAX_LEN}자까지 입력할 수 있습니다 (현재 {len(cleaned)}자).")
        ch["note"] = cleaned
        self._save()
        return cleaned

    def set_auto_run(self, name: str, flag: bool) -> bool:
        """
        `./yt.sh run`·`transcribe` 전체 순회 대상 여부. FR34.7 (DQ-25)

        기본값이 True이므로 **True면 필드를 제거**한다 (set_group의 빈 값 처리와 동일 패턴).
        필드 부재 = True → 기존 channels.yaml은 무변경으로 종전과 동일하게 동작한다.
        """
        ch = self.data.get("channels", {}).get(name)
        if ch is None:
            raise KeyError(f"등록되지 않은 채널: {name}")
        if flag:
            ch.pop("auto_run", None)
        else:
            ch["auto_run"] = False
        self._save()
        return bool(flag)

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

    def names(self, auto_only: bool = False) -> list:
        """
        등록 채널명 목록.

        `auto_only=True`면 `auto_run: false` 채널을 제외한다 (FR34.7).
        **기본 동작(전체 반환)은 바꾸지 않는다** — `names()`는 jobs.py의 등록 여부
        확인(`name not in reg.names()`)에도 쓰이므로, 기본값을 바꾸면 검색 유입 채널이
        매번 "미등록"으로 오판돼 재등록·폴더 재지정된다 (DQ-25).
        """
        channels = self.data.get("channels", {})
        if not auto_only:
            return list(channels.keys())
        return [n for n, c in channels.items()
                if (c or {}).get("auto_run", True) is not False]

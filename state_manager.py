"""채널별 state.json 관리 — 신규·수정 감지. FR2."""
import json
import datetime
from pathlib import Path

import config


class StateManager:

    def __init__(self, channel: str):
        self.channel = channel
        self.path = config.channel_dir(channel) / "state.json"
        self.state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def is_new(self, vid: str) -> bool:
        return vid not in self.state

    def is_updated(self, vid: str, modified_date, upload_date=None) -> bool:
        """
        수정 여부 판단. FR2.2, FR2.3
        modified_date 우선, 없으면 upload_date로 폴백 비교.
        """
        if vid not in self.state:
            return False
        prev = self.state[vid]
        new_mod = modified_date or upload_date
        # 채널 스캔(extract_flat)은 날짜를 제공하지 않는다 — 정보가 없으면
        # 변경 판단이 불가하므로 무변경으로 간주 (FR2.6, 재추출 폭주 방지)
        if new_mod is None:
            return False
        old_mod = prev.get("modified_date") or prev.get("upload_date")
        return str(new_mod) != str(old_mod)

    def decide(self, vid: str, modified_date, upload_date=None) -> str:
        """처리 액션 결정: 'new' | 'updated' | 'skip'."""
        if self.is_new(vid):
            return "new"
        # 멤버십 전용으로 스킵된 영상은 인증 수단(Firefox 프로필 또는 쿠키 파일)이
        # 있으면 매 run 재시도 (FR19.1, DQ-10). has_auth()는 부작용 없는 존재
        # 확인만 한다 — resolve_cookiefile()은 /tmp 복사 부작용이 있어 부적합
        if (self.state[vid].get("sub_type") == "members_only"
                and config.has_auth()):
            return "updated"
        if self.is_updated(vid, modified_date, upload_date):
            return "updated"
        return "skip"

    def mark_done(self, vid: str, meta: dict):
        self.state[vid] = {
            "upload_date": meta.get("upload_date", "00000000"),
            "modified_date": meta.get("modified_date"),
            "sub_type": meta.get("sub_type", "none"),
            "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "basename": meta.get("basename", ""),
        }

    def remove(self, vid: str):
        """강제 재처리용 상태 삭제. FR5.2."""
        self.state.pop(vid, None)

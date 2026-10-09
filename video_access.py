"""영상 접근성 판정 — 멤버십 전용 여부. FR13 · FR17.6 · FR19.1 (DQ-38).

추출 경로(`extractor.Extractor.run`)와 대시보드 스캔(`dashboard/jobs.py`)이 **같은
규칙**을 쓰도록 판정을 이 한 곳에 모은다. 양쪽에 따로 두면 드리프트가 생긴다.

의존성이 없는 잎(leaf) 모듈로 둔 이유: `jobs.py`는 섀도잉 방어 때문에 `extractor`를
지연 임포트한다(`_app_extractor`). 판정을 `extractor`에 두면 jobs가 모듈 로드 시점에
yt-dlp까지 끌어와야 하므로, 둘 다 모듈 최상단에서 안전하게 임포트할 수 있는
독립 모듈로 분리한다.

판정 우선순위
  1차 `availability` — 스캔 flat 엔트리/full info의 구조화 필드. **언어 비의존.**
  2차 오류 메시지 키워드 — availability가 없는 경로(단일영상 등)의 안전망.
       YouTube가 돌려주는 `reason` 문구는 `extractor_args.youtube.lang`(DQ-20)에
       따라 번역되므로 **영어·한국어 패턴을 모두** 둔다 (DQ-38).
"""

# 스캔 엔트리·full info의 availability 중 멤버십 전용으로 볼 값 (FR17.6)
MEMBERS_AVAILABILITY = ("subscriber_only", "needs_auth", "premium_only")

# YouTube가 주는 오류 문구 — 로케일에 따라 번역된다 (DQ-38).
# 영어: lang 미지정 실측 / 한국어: lang=ko 실측 (2026-09-24, `_workspace/30`).
#   "이 동영상은 …VIP 회원 등급 이상의 채널 회원에게 제공됩니다…"
# 오탐을 막기 위해 `회원` 단독 같은 넓은 패턴은 쓰지 않는다.
MEMBERS_MESSAGE_KEYWORDS = (
    # 영어
    "members-only",
    "members only",
    "channel's members",
    "join this channel",
    "available to this channel",
    # 한국어
    "채널 회원",
    "회원 전용",
    "회원 등급",
    "멤버십",
)


def is_members_availability(availability) -> bool:
    """`availability` 필드로 멤버십 전용 판별 (언어 비의존). FR17.6"""
    av = str(availability or "").lower()
    return any(k in av for k in MEMBERS_AVAILABILITY)


def is_members_message(msg) -> bool:
    """오류 메시지로 멤버십 전용 판별 (영어·한국어 폴백). DQ-38"""
    m = str(msg or "").lower()
    return any(k in m for k in MEMBERS_MESSAGE_KEYWORDS)


def is_members_only(msg=None, availability=None) -> bool:
    """멤버십 전용 종합 판정 — availability(1차) 또는 메시지(2차).

    429 차단처럼 멤버십과 무관한 실패가 섞이는 경로에서는 **호출부가** 429를 먼저
    판정해야 한다 (멤버십으로 오분류하면 `_mark_skip`으로 영구 스킵된다).
    """
    return is_members_availability(availability) or is_members_message(msg)

#!/bin/bash
# ─────────────────────────────────────────────────────────────
# yt.sh — YouTube 자막 KL 파이프라인 Docker 래퍼. FR8
# 사용: ./yt.sh add https://youtube.com/@채널
#       ./yt.sh run
#       ./yt.sh review [--llm]
#       ./yt.sh index
#       ./yt.sh ask 채널 "질문" [--multistep]
#       ./yt.sh serve        (대시보드)
# ─────────────────────────────────────────────────────────────
set -e

IMAGE="youtube-subs"
DIR="$(cd "$(dirname "$0")" && pwd)"

# 이미지 없으면 자동 빌드 (FR8.5)
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "▢ Docker 이미지 빌드 중 (최초 1회)..."
  docker build -t "$IMAGE" "$DIR"
fi

# ── channels.yaml 파일 보장 (폴더 오생성 근본 방지) ──
# Docker는 마운트 대상이 없으면 빈 폴더를 만든다. 미리 파일로 만들어 차단.
if [ -d "$DIR/channels.yaml" ]; then
  rm -rf "$DIR/channels.yaml"
fi
if [ ! -f "$DIR/channels.yaml" ]; then
  echo "channels: {}" > "$DIR/channels.yaml"
fi
mkdir -p "$DIR/output"

# ── Firefox 프로필 마운트 (있을 때만) FR13.6 ──
# Firefox에 로그인만 해두면 매 실행 최신 쿠키를 직접 읽는다 (내보내기 불필요).
# cookies.sqlite가 있는 프로필 중 가장 최근 사용된 것을 자동 선택.
FF_OPT=()
FF_BASE="$HOME/Library/Application Support/Firefox/Profiles"
if [ -d "$FF_BASE" ]; then
  FF_PROFILE=""
  while IFS= read -r d; do
    if [ -f "$d/cookies.sqlite" ]; then FF_PROFILE="$d"; break; fi
  done < <(ls -td "$FF_BASE"/*/ 2>/dev/null)
  if [ -n "$FF_PROFILE" ]; then
    FF_OPT=(-v "$FF_PROFILE:/app/firefox_profile:ro")
    echo "▢ Firefox 쿠키 사용: $(basename "$FF_PROFILE")"
  fi
fi

# ── 쿠키 파일 마운트 (있을 때만, Firefox 없을 때 폴백) FR13.2 ──
COOKIE_OPT=""
if [ -f "$DIR/cookies.txt" ]; then
  COOKIE_OPT="-v $DIR/cookies.txt:/app/cookies.txt:ro"
  if [ ${#FF_OPT[@]} -eq 0 ]; then
    echo "▢ 쿠키 인증 사용: cookies.txt"
  fi
fi

# serve 명령은 포트 노출 필요
PORT_OPT=""
if [ "$1" = "serve" ]; then
  PORT_OPT="-p 8800:8800"
  echo "▢ 대시보드: http://localhost:8800"
fi

# HuggingFace 캐시를 호스트와 공유 (모델 재다운로드 방지)
HF_CACHE="$HOME/.cache/huggingface"
mkdir -p "$HF_CACHE"

docker run --rm -it \
  $PORT_OPT \
  $COOKIE_OPT \
  "${FF_OPT[@]}" \
  -v "$DIR/output:/app/output" \
  -v "$DIR/channels.yaml:/app/channels.yaml" \
  -v "$HF_CACHE:/root/.cache/huggingface" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  "$IMAGE" "$@"

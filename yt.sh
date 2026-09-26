#!/bin/bash
# ─────────────────────────────────────────────────────────────
# yt.sh — YouTube 자막 KL 파이프라인 Docker 래퍼. FR8
# 사용: ./yt.sh add https://youtube.com/@채널
#       ./yt.sh run
#       ./yt.sh review [--llm]
#       ./yt.sh index
#       ./yt.sh ask 채널 "질문" [--multistep]
#       ./yt.sh serve        (대시보드, 포그라운드 — 터미널을 닫으면 종료)
#       ./yt.sh serve --detach   (상시 운용: -d --restart unless-stopped, FR37.18)
#       ./yt.sh migrate-groups [--apply [--yes]] [--rollback] [--unlock] [--no-backup]
#       ./yt.sh backfill-tickers [채널] [--apply]   (기본 dry-run, FR12.2)
# ─────────────────────────────────────────────────────────────
set -e

IMAGE="youtube-subs"
CONTAINER="yt-subs-dashboard"      # serve --detach 전용 이름 (FR37.18)
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

# ── migrate-groups --apply: 호스트측 자동 백업 (FR35.11⑦) ──
# 컨테이너에는 output/·channels.yaml만 마운트돼 내부에서는 형제 경로를 만들 수 없고,
# output/ 안에 두면 채널 열거·purge rmtree와 섞인다 → 반드시 호스트에서 output/ 바깥에.
EXTRA_ARGS=()
if [ "$1" = "migrate-groups" ]; then
  HAS_APPLY=0; HAS_NOBACKUP=0
  for a in "$@"; do
    [ "$a" = "--apply" ] && HAS_APPLY=1
    [ "$a" = "--no-backup" ] && HAS_NOBACKUP=1
  done
  if [ "$HAS_APPLY" = "1" ] && [ "$HAS_NOBACKUP" = "0" ]; then
    BACKUP="$DIR/output_backup_$(date +%Y%m%d_%H%M%S)"
    echo "▢ 백업 생성 중: $BACKUP"
    cp -a "$DIR/output" "$BACKUP"
    echo "▢ 백업 완료 (자동 삭제하지 않습니다 — 확인 후 직접 지우세요: rm -rf '$BACKUP')"
    EXTRA_ARGS=(--backup-path "$BACKUP")
  fi
fi

# serve 명령은 포트 노출 필요
PORT_OPT=""
if [ "$1" = "serve" ]; then
  PORT_OPT="-p 8800:8800"
  echo "▢ 대시보드: http://localhost:8800"
fi

# ── serve --detach: 상시 운용 기동 (FR37.18) ──
# 주기 자동 추출(FR37)은 serve 프로세스 안에서만 살아 있다. 기본(포그라운드)
# 동작은 그대로 두고, --detach일 때만 -d + 재시작 정책 + 고정 이름으로 띄운다.
# 마운트 구성은 아래 docker run 한 곳을 공유하므로 포그라운드와 동일하다.
DETACH=0
ARGS=()
for a in "$@"; do
  if [ "$1" = "serve" ] && [ "$a" = "--detach" ]; then DETACH=1; continue; fi
  ARGS+=("$a")
done
RUN_OPTS=(--rm -it)
if [ "$DETACH" = "1" ]; then
  RUN_OPTS=(-d --restart unless-stopped --name "$CONTAINER")
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "▢ 기존 컨테이너 교체: $CONTAINER"
    docker rm -f "$CONTAINER" >/dev/null
  fi
  echo "▢ 백그라운드 기동 (로그: docker logs -f $CONTAINER · 중지: docker stop $CONTAINER)"
fi

# HuggingFace 캐시를 호스트와 공유 (모델 재다운로드 방지)
HF_CACHE="$HOME/.cache/huggingface"
mkdir -p "$HF_CACHE"

docker run "${RUN_OPTS[@]}" \
  $PORT_OPT \
  $COOKIE_OPT \
  "${FF_OPT[@]}" \
  -v "$DIR/output:/app/output" \
  -v "$DIR/channels.yaml:/app/channels.yaml" \
  -v "$HF_CACHE:/root/.cache/huggingface" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  "$IMAGE" "${ARGS[@]}" "${EXTRA_ARGS[@]}"

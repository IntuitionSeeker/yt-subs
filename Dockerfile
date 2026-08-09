FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성: git(빌드), ffmpeg(자막 포맷), curl/unzip(deno 설치)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# deno 설치 (yt-dlp YouTube 자막 추출에 필요한 JS 런타임)
RUN curl -fsSL https://deno.land/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno
ENV PATH="/root/.deno/bin:${PATH}"

# F-4: /cookies mtime·detected_at 등 시각 표기를 호스트(KST)와 일치시킴
ENV TZ=Asia/Seoul

COPY requirements.txt .

# requirements.txt에 CPU 전용 torch 인덱스가 명시되어 있어
# Mac/CPU 환경에서 불필요한 NVIDIA CUDA 패키지를 받지 않음
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# F-8: 이미지 빌드 시각을 파일로 남긴다. serve 기동 시 이 값을 로그에 찍어,
# "재빌드했지만 컨테이너를 재기동하지 않아 구코드로 계속 실행 중"인 상태를
# `docker logs`만 보고도 즉시 알아챌 수 있게 한다.
RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > /app/.build_time

# 대시보드 포트
EXPOSE 8800

ENTRYPOINT ["python", "main.py"]

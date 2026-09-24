#!/usr/bin/env python3
"""CLI 진입점 — add / run / review / reextract / index / list / remove / ask / summarize / serve / backfill-tickers."""
import sys
import argparse
import logging

import config
from channel_registry import ChannelRegistry

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("main")


def cmd_add(args):
    """채널 등록 + 전체 자막 추출 자동 시작. FR7.2"""
    from extractor import Extractor
    reg = ChannelRegistry()
    existed = reg.resolve_name(args.url) in reg.names()      # FR7.7 upsert 판정
    name = reg.add(args.url, lang=args.lang)
    # add()는 upsert다 — 기존 항목의 group·auto_run·channel_id는 보존된다 (FR7.7)
    log.info(f"✅ 기존 채널 갱신: {name} (폴더·설정 보존)" if existed
             else f"✅ 채널 등록: {name}")
    log.info("자막 추출을 시작합니다...\n")
    Extractor(reg.get(name)).run()


def bulk_targets(reg: ChannelRegistry, channel: str = None) -> list:
    """
    일괄 명령(run·transcribe)의 대상 채널 산출. FR34.7 (DQ-25)

    채널을 명시하면 플래그와 무관하게 그 채널만, 인자가 없으면 `auto_run: false`
    (검색 추출로 유입된 채널)를 제외한 전체를 순회한다.
    **run·transcribe 두 곳이 이 헬퍼를 공유한다** — 한쪽만 고쳐지는 드리프트를 막는다.
    """
    return [channel] if channel else reg.names(auto_only=True)


def cmd_run(args):
    """전체 또는 특정 채널 업데이트. FR7.3"""
    from extractor import Extractor
    reg = ChannelRegistry()
    targets = bulk_targets(reg, args.channel)
    if not targets:
        log.info("등록된 채널이 없습니다. './yt.sh add URL' 로 추가하세요.")
        return
    for name in targets:
        Extractor(reg.get(name)).run(limit=args.limit)


def cmd_transcribe(args):
    """무자막(sub_type=none) 영상 Whisper 전사. FR30.1"""
    from transcriber import Transcriber
    reg = ChannelRegistry()
    targets = bulk_targets(reg, args.channel)
    if not targets:
        log.info("등록된 채널이 없습니다. './yt.sh add URL' 로 추가하세요.")
        return
    for name in targets:
        Transcriber(reg.get(name)).run(limit=args.limit)


def cmd_review(args):
    """품질 검토. FR4"""
    from quality_checker import QualityChecker
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        QualityChecker(name).review(use_llm=args.llm)


def cmd_reextract(args):
    """SUSPECT 영상 재추출. FR5.1"""
    from extractor import Extractor
    from quality_checker import QualityChecker
    from state_manager import StateManager
    import json
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        suspects = QualityChecker(name).get_suspects()
        if not suspects:
            log.info(f"{name}: SUSPECT 없음")
            continue
        # basename → video_id 매핑 (meta 역참조)
        ext = Extractor(reg.get(name))
        state = StateManager(name)
        dirs = config.channel_subdirs(name)
        for basename in suspects:
            meta_path = dirs["meta"] / f"{basename}.json"
            if meta_path.exists():
                vid = json.loads(meta_path.read_text(encoding="utf-8")).get("id")
                if vid:
                    state.remove(vid)
                    ext.state.remove(vid)
                    ext.process_video(vid, action="updated")
        ext.state.save()


def cmd_index(args):
    """KL 인덱싱. FR6"""
    from kl_indexer import KLIndexer
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    for name in targets:
        KLIndexer(name).index_all()


def cmd_list(args):
    """채널 목록. FR7.5"""
    reg = ChannelRegistry()
    chans = reg.list()
    if not chans:
        log.info("등록된 채널이 없습니다.")
        return
    log.info(f"{'채널':<20} {'언어':<6} {'등록일':<12} URL")
    log.info("─" * 70)
    for name, c in chans.items():
        log.info(f"{name:<20} {c.get('lang',''):<6} {c.get('added_at',''):<12} {c.get('url','')}")


def cmd_remove(args):
    """채널 삭제. FR7.5"""
    reg = ChannelRegistry()
    if reg.remove(args.channel):
        log.info(f"✅ 삭제: {args.channel} (출력 폴더는 수동 삭제 필요)")
    else:
        log.info(f"채널을 찾을 수 없습니다: {args.channel}")


def cmd_ask(args):
    """RAG 질의 또는 멀티스텝. FR9.1"""
    if args.multistep:
        from kl_harness import KLHarness
        result = KLHarness(args.channel).run(args.question)
        print(result["answer"])
        print(f"\n[{result['steps']}단계 · 도구 {len(result['trace'])}회]")
    else:
        from kl_query import KLQuery
        result = KLQuery(args.channel).ask(args.question, since=args.since, until=args.until)
        print(result["answer"])
        print("\n출처:")
        for s in result["sources"]:
            print(f"  - {s['title']} ({s['upload_date']}) {s['url']}")


def cmd_summarize(args):
    """영상 요약. FR9.2"""
    from kl_query import KLQuery
    print(KLQuery(args.channel).summarize(video_id=args.video_id))


def cmd_search(args):
    """벡터 검색만. FR9.3"""
    from kl_query import KLQuery
    results = KLQuery(args.channel).search(args.query, since=args.since, until=args.until)
    for r in results:
        print(f"[{r['score']}] {r['title']} ({r['upload_date']})")
        print(f"    {r['text'][:100]}...")
        print(f"    → {r['source_url']}\n")


def cmd_serve(args):
    """대시보드 서버 실행. FR11"""
    import uvicorn
    sys.path.insert(0, str(config.BASE_DIR / "dashboard"))
    uvicorn.run("server:app", host="0.0.0.0", port=args.port,
                app_dir=str(config.BASE_DIR / "dashboard"))


def cmd_migrate_groups(args):
    """
    폴더 디렉터리화 마이그레이션 — 기본 dry-run. FR35.11~35.12

    `output/<채널>/` → `output/<폴더>/<채널>/`. `channels.yaml`은 한 글자도 바꾸지 않는다
    (`group` 값은 그대로이고 해석 규칙만 달라진다) → 롤백이 디렉터리 되돌리기로 완결된다.
    """
    import folder_ops

    if args.unlock:                                     # FR35.10 stale 락 수동 제거
        log.info("✅ 락을 제거했습니다." if folder_ops.unlock()
                 else "락이 없습니다 (output/.migration.lock).")
        return

    if args.rollback:                                   # FR35.12
        # 락이 살아 있어도 롤백은 진행할 수 있어야 한다 (F-2) — 급사한 마이그레이션의
        # 복구 수단이 락에 막히면 `--unlock`↔`--rollback` 순환에 갇힌다. 다만 정말로
        # 다른 마이그레이션이 도는 중일 수 있으므로 **확인 후 탈취**한다.
        takeover = bool(args.yes)
        if not takeover and folder_ops.is_locked():
            info = folder_ops.read_lock_info()
            log.warning(f"⚠️ 마이그레이션 락이 있습니다 (pid={info.get('pid')} · "
                        f"시작={info.get('started_at')}).")
            try:
                answer = input("다른 마이그레이션이 진행 중이 아니라면 락을 넘겨받아 "
                               "롤백합니다. 계속할까요? [y/N] ").strip().lower()
            except EOFError:
                answer = ""
            if answer not in ("y", "yes"):
                log.info("취소했습니다 — 아무것도 되돌리지 않았습니다. "
                         "(비대화형이면 `--rollback --yes`)")
                sys.exit(1)
            takeover = True
        try:
            result = folder_ops.rollback_migration(takeover=takeover)
        except Exception as exc:
            log.error(f"❌ 롤백 실패: {exc}")
            sys.exit(1)
        if not result["restored"]:                      # F-1: 성공이라고 말하지 않는다
            log.error(f"⚠️ 되돌린 것이 없습니다 — {result.get('detail') or ''}\n"
                      f"   저널이 이미 정리됐다면 호스트 백업으로 복원하세요: "
                      f"rm -rf output && mv output_backup_<ts> output")
            sys.exit(2)
        log.info(f"↩️ 롤백 완료 — {result['restored']}건 원복 "
                 f"({result['source']} 저널: {result['journal']})")
        if result.get("remaining"):
            log.info("   되돌릴 저널이 더 남아 있습니다 — 한 번 더 `--rollback` 하세요.")
        return

    reg = ChannelRegistry()
    plan = folder_ops.plan_migration(reg)
    log.info(f"▢ 이동 예정 {len(plan['moves'])}건 · 유지 {len(plan['skipped'])}건 "
             f"· 미등록 잔존 폴더 {len(plan['unregistered'])}건(미이동)\n")
    for mv in plan["moves"]:
        log.info(f"  {mv['channel']:<28} output/{mv['channel']} → "
                 f"output/{mv['group']}/{mv['channel']}")
    if plan["skipped"]:
        log.info("\n[유지]")
        for sk in plan["skipped"]:
            log.info(f"  {sk['channel']:<28} {sk['reason']}")
    if plan["unregistered"]:
        log.info("\n[미등록 잔존 폴더 — 건드리지 않습니다 (FR32.4 수동 정리)]")
        for name in plan["unregistered"]:
            log.info(f"  output/{name}")

    if not args.apply:
        log.info("\n※ dry-run입니다. 아무것도 옮기지 않았습니다. "
                 "실제 이동은 `./yt.sh migrate-groups --apply`")
        return
    if not plan["moves"]:
        log.info("\n옮길 채널이 없습니다 (멱등).")
        return

    if not args.yes:                                    # ⑥ 대화형 확인
        try:
            answer = input(f"\n{len(plan['moves'])}개 채널 폴더를 이동합니다. "
                           f"계속할까요? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            log.info("취소했습니다 — 아무것도 옮기지 않았습니다.")
            return

    try:
        result = folder_ops.apply_migration(plan, reg)
    except Exception as exc:
        log.error(f"\n❌ {exc}")
        sys.exit(1)
    log.info(f"\n✅ 완료 — {result['moved']}건 이동 (저널 보관: {result['journal']})")
    if getattr(args, "backup_path", None):
        log.info(f"   백업: {args.backup_path} (자동 삭제하지 않습니다 — 직접 지우세요)")


def cmd_backfill_tickers(args):
    """
    기존 meta/*.json의 `tickers` 재계산 — 기본 dry-run. FR12.2 (DQ-43)

    구 규칙(6자리 숫자 전부 채택)이 남긴 오탐을 네트워크 없이 걷어낸다.
    제목·태그(meta) + 설명(desc/*.txt)만 쓰므로 재추출이 필요 없다.
    """
    from meta_collector import backfill_tickers
    reg = ChannelRegistry()
    targets = [args.channel] if args.channel else reg.names()
    if not targets:
        log.info("등록된 채널이 없습니다.")
        return
    total = {"scanned": 0, "changed": 0, "removed": 0, "added": 0}
    for name in targets:
        stat = backfill_tickers(name, apply=args.apply)
        for k in total:
            total[k] += stat[k]
        if stat["changed"]:
            log.info(f"▢ {name}: {stat['changed']}/{stat['scanned']}건 변경 "
                     f"(제거 {stat['removed']} · 추가 {stat['added']})")
            for smp in stat["samples"]:
                log.info(f"    {smp['before']} → {smp['after']} | {smp['title'][:50]}")
        else:
            log.info(f"▢ {name}: 변경 없음 ({stat['scanned']}건 확인)")
    log.info(f"\n합계 {total['changed']}/{total['scanned']}건 변경 "
             f"(제거 {total['removed']} · 추가 {total['added']})")
    if not args.apply:
        log.info("※ dry-run입니다. 아무것도 쓰지 않았습니다. "
                 "실제 반영은 `./yt.sh backfill-tickers --apply`")


def cmd_test(args):
    """검증 실행. 섹션 7"""
    import subprocess
    cmd = ["python", "-m", "pytest", "tests/", "-v"]
    if not args.integration:
        cmd += ["-m", "not integration"]
    subprocess.run(cmd, cwd=str(config.BASE_DIR))


def build_parser():
    p = argparse.ArgumentParser(prog="yt", description="YouTube 자막 KL 파이프라인")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add", help="채널 등록 + 추출")
    sp.add_argument("url"); sp.add_argument("--lang", default=config.DEFAULT_LANG)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("run", help="채널 업데이트")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--limit", type=int, help="카나리아 실행: 최대 N개 영상만 처리")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("transcribe", help="무자막 영상 Whisper 전사 (FR30)")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--limit", type=int, help="최대 N개만 전사")
    sp.set_defaults(func=cmd_transcribe)

    sp = sub.add_parser("review", help="품질 검토")
    sp.add_argument("channel", nargs="?"); sp.add_argument("--llm", action="store_true")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("reextract", help="SUSPECT 재추출")
    sp.add_argument("channel", nargs="?"); sp.set_defaults(func=cmd_reextract)

    sp = sub.add_parser("index", help="KL 인덱싱")
    sp.add_argument("channel", nargs="?"); sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("list", help="채널 목록"); sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("remove", help="채널 삭제")
    sp.add_argument("channel"); sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("ask", help="질의 (RAG/멀티스텝)")
    sp.add_argument("channel"); sp.add_argument("question")
    sp.add_argument("--multistep", action="store_true")
    sp.add_argument("--since"); sp.add_argument("--until")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("summarize", help="영상 요약")
    sp.add_argument("channel"); sp.add_argument("video_id")
    sp.set_defaults(func=cmd_summarize)

    sp = sub.add_parser("search", help="벡터 검색")
    sp.add_argument("channel"); sp.add_argument("query")
    sp.add_argument("--since"); sp.add_argument("--until")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("serve", help="대시보드 서버")
    sp.add_argument("--port", type=int, default=8800); sp.set_defaults(func=cmd_serve)

    # FR35.11 — 기본 dry-run, --apply 가 있어야 실제로 움직인다
    sp = sub.add_parser("migrate-groups", help="폴더 디렉터리화 마이그레이션 (기본 dry-run)")
    sp.add_argument("--apply", action="store_true", help="실제 이동 (사전 검증 통과 시)")
    sp.add_argument("--yes", action="store_true",
                    help="대화형 확인 생략 (--rollback 에서는 락 탈취 동의)")
    sp.add_argument("--rollback", action="store_true",
                    help="저널 역순 되돌리기 (완료된 마이그레이션 .done.json 포함)")
    sp.add_argument("--unlock", action="store_true", help="stale 락 수동 제거")
    # --no-backup / --backup-path 는 **호스트 셸(yt.sh)** 이 처리한다 (컨테이너에는
    # output/ 형제 경로를 만들 권한이 없다). 여기서는 인자만 수용한다.
    sp.add_argument("--no-backup", action="store_true",
                    help="호스트 자동 백업 생략 (yt.sh가 처리)")
    sp.add_argument("--backup-path", dest="backup_path",
                    help="yt.sh가 만든 백업 경로 (완료 메시지용)")
    sp.set_defaults(func=cmd_migrate_groups)

    # FR12.2 — 기본 dry-run, --apply 가 있어야 meta/*.json에 쓴다
    sp = sub.add_parser("backfill-tickers",
                        help="기존 meta의 tickers 재계산 (FR12.2, 기본 dry-run)")
    sp.add_argument("channel", nargs="?")
    sp.add_argument("--apply", action="store_true", help="실제 파일 쓰기")
    sp.set_defaults(func=cmd_backfill_tickers)

    sp = sub.add_parser("test", help="검증 실행")
    sp.add_argument("--integration", action="store_true"); sp.set_defaults(func=cmd_test)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)

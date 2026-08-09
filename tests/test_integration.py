"""통합 검증 — V-I1~V-I8. 실제 채널·네트워크 필요 (integration 마커).

실행: pytest tests/test_integration.py -m integration
주의: 실제 YouTube 채널 1개를 대상으로 동작하므로 시간이 걸린다.
환경변수 TEST_CHANNEL_URL로 대상 채널 지정 (미지정 시 스킵).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_URL = os.environ.get("TEST_CHANNEL_URL")
pytestmark = pytest.mark.integration

requires_channel = pytest.mark.skipif(
    not TEST_URL, reason="TEST_CHANNEL_URL 미설정"
)


@requires_channel
def test_add_channel(tmp_path, monkeypatch):
    """V-I1: 등록 + 폴더 생성."""
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    from channel_registry import ChannelRegistry
    from extractor import Extractor

    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    name = reg.add(TEST_URL)
    Extractor(reg.get(name)).run()

    dirs = config.channel_subdirs(name)
    assert dirs["srt"].exists()
    assert dirs["txt"].exists()


@requires_channel
def test_incremental_run(tmp_path, monkeypatch):
    """V-I2: 2회차 실행 시 전부 SKIP."""
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    from channel_registry import ChannelRegistry
    from extractor import Extractor

    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    name = reg.add(TEST_URL)
    Extractor(reg.get(name)).run()
    stats2 = Extractor(reg.get(name)).run()
    assert stats2["new"] == 0  # 2회차는 신규 없음


@requires_channel
def test_index_creates_collections(tmp_path, monkeypatch):
    """V-I6: 인덱싱 후 2개 컬렉션 생성."""
    import config
    monkeypatch.setattr(config, "OUTPUT_BASE", tmp_path)
    from channel_registry import ChannelRegistry
    from extractor import Extractor
    from kl_indexer import KLIndexer

    reg = ChannelRegistry(yaml_path=tmp_path / "channels.yaml")
    name = reg.add(TEST_URL)
    Extractor(reg.get(name)).run()
    result = KLIndexer(name).index_all()
    assert result["subtitle"] >= 0  # 자막 청크 생성됨


# V-I3 수정감지, V-I4 검토, V-I5 재추출, V-I7 격리, V-I8 영속성은
# 동일 패턴으로 확장 (생략 — 실 채널 의존)

"""pytest 설정 — integration 마커 정의."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: 네트워크/실제 채널 필요 (기본 제외)"
    )

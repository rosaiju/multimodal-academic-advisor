"""A missing record must not leak the server's file-system path to the browser."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.mark.parametrize(
    "path",
    [
        "/students/{me}/record",
        "/students/{me}/audit",
        "/students/{me}/audit/summary",
        "/students/{me}/plan",
    ],
)
def test_missing_record_404_names_no_server_path(client, path) -> None:
    response = client.get(path)
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "no confirmed coursework yet" in detail
    record_dir = str(get_settings().student_record_dir)
    assert record_dir not in detail
    assert ".json" not in detail
    assert "\\" not in detail and "/" not in detail

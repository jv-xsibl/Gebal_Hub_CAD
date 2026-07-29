"""Tests for Stage 3 CAD asset downloading and staging."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from gebal_cad_normalizer.assets.downloader import CadAssetDownloader
from gebal_cad_normalizer.exceptions import DownloadError
from gebal_cad_normalizer.models import CadAssetDescriptor


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def _descriptor(**overrides: object) -> CadAssetDescriptor:
    data: dict[str, object] = {
        "url": "https://example.invalid/cad/top.dwg",
        "file_name": "../Unsafe Top View.dwg",
        "file_type": "dwg",
        "media_id": "media-1",
        "vendor_revision": "rev-1",
        "vendor_updated_at": "2026-07-21T10:00:00Z",
        "content_type": "application/acad",
    }
    data.update(overrides)
    return CadAssetDescriptor(**data)


def test_successful_streamed_download_using_mocked_http(tmp_path: Path) -> None:
    content = b"AC1027" + b"dwg-body"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.invalid/cad/top.dwg"
        return httpx.Response(200, headers={"content-type": "application/acad"}, stream=ChunkStream([content[:4], content[4:]]))

    downloader = CadAssetDownloader(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = downloader.stage(_descriptor())

    assert result.staged_path.exists()
    assert result.staged_path.read_bytes() == content
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.size_bytes == len(content)
    assert result.sanitized_filename == "Unsafe_Top_View.dwg"


def test_timeout_error_handling(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    downloader = CadAssetDownloader(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(DownloadError):
        downloader.stage(_descriptor())


def test_oversized_file_rejection(tmp_path: Path) -> None:
    content = b"AC1027" + b"x" * 20
    downloader = CadAssetDownloader(
        tmp_path,
        max_file_size_bytes=10,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))),
    )

    with pytest.raises(DownloadError, match="exceeds"):
        downloader.stage(_descriptor())


def test_empty_file_rejection(tmp_path: Path) -> None:
    downloader = CadAssetDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b""))),
    )

    with pytest.raises(DownloadError, match="empty"):
        downloader.stage(_descriptor())


def test_temporary_file_cleanup_after_failure(tmp_path: Path) -> None:
    downloader = CadAssetDownloader(
        tmp_path,
        max_file_size_bytes=5,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"AC1027-too-large"))),
    )

    with pytest.raises(DownloadError):
        downloader.stage(_descriptor())

    assert list(tmp_path.iterdir()) == []


def test_source_metadata_preserved(tmp_path: Path) -> None:
    content = b"AC1027-source"
    downloader = CadAssetDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/x-dwg"}, content=content))),
    )

    result = downloader.stage(_descriptor(media_id="m-9", vendor_revision="r-9", vendor_updated_at=1776837754032))

    assert result.source_url == "https://example.invalid/cad/top.dwg"
    assert result.media_id == "m-9"
    assert result.vendor_revision == "r-9"
    assert result.vendor_updated_at == 1776837754032
    assert result.content_type == "application/x-dwg"


def test_local_file_staging_without_network(tmp_path: Path) -> None:
    local_source = tmp_path / "source input.dwg"
    local_source.write_bytes(b"AC1027-local")
    descriptor = CadAssetDescriptor(local_path=str(local_source), file_name="source input.dwg", file_type="dwg")

    result = CadAssetDownloader(tmp_path / "staging").stage(descriptor)

    assert result.staged_path.exists()
    assert result.staged_path != local_source
    assert result.staged_path.read_bytes() == b"AC1027-local"
    assert result.source_url is None

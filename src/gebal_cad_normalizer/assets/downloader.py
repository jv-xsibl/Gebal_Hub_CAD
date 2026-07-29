"""Safe staging for vendor CAD source files."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx
from pydantic import Field

from gebal_cad_normalizer.exceptions import DownloadError
from gebal_cad_normalizer.models import CadAssetDescriptor, StrictModel

_DWG_CONTENT_TYPE_TOKENS = ("dwg", "acad", "autocad", "vnd.dwg", "x-dwg", "octet-stream")
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class DownloadResult(StrictModel):
    """A verified staged source file that has not been promoted to current."""

    staged_path: Path
    original_filename: str
    sanitized_filename: str
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(gt=0)
    source_url: str | None = None
    media_id: str | None = None
    vendor_revision: str | None = None
    vendor_updated_at: str | int | None = None
    content_type: str | None = None


class CadAssetDownloader:
    """Download or locally stage a CAD asset without touching managed sources."""

    def __init__(
        self,
        staging_root: Path,
        *,
        max_file_size_bytes: int = 100 * 1024 * 1024,
        timeout: float | httpx.Timeout = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes must be greater than zero")
        self.staging_root = Path(staging_root)
        self.max_file_size_bytes = max_file_size_bytes
        self.timeout = timeout
        self.client = client

    def stage(self, descriptor: CadAssetDescriptor) -> DownloadResult:
        """Stage a remote URL or explicit local file for version management."""

        self.staging_root.mkdir(parents=True, exist_ok=True)
        if descriptor.url:
            return self._download_url(descriptor)
        if descriptor.local_path:
            return self._stage_local_file(descriptor)
        raise DownloadError("CAD asset descriptor has no URL or local path.")

    def _download_url(self, descriptor: CadAssetDescriptor) -> DownloadResult:
        filename = sanitize_filename(descriptor.file_name or _filename_from_url(descriptor.url))
        staged_path = _temporary_path(self.staging_root, filename)
        created_path: Path | None = staged_path
        sha256 = hashlib.sha256()
        content_type: str | None = None
        size = 0

        try:
            if self.client is None:
                with httpx.Client(timeout=self.timeout) as client:
                    content_type, size = self._stream_to_file(client, descriptor.url or "", staged_path, sha256)
            else:
                content_type, size = self._stream_to_file(self.client, descriptor.url or "", staged_path, sha256)
            _verify_staged_file(staged_path, filename, content_type, size)
            created_path = None
            return _result(descriptor, staged_path, filename, sha256.hexdigest(), size, content_type)
        except Exception as exc:
            _cleanup(created_path)
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError(f"Failed to download CAD asset: {exc}") from exc

    def _stream_to_file(
        self,
        client: httpx.Client,
        url: str,
        staged_path: Path,
        sha256: "hashlib._Hash",
    ) -> tuple[str | None, int]:
        size = 0
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            with staged_path.open("wb") as output:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_file_size_bytes:
                        raise DownloadError("Downloaded CAD asset exceeds maximum file size.")
                    sha256.update(chunk)
                    output.write(chunk)
        return content_type, size

    def _stage_local_file(self, descriptor: CadAssetDescriptor) -> DownloadResult:
        source_path = Path(descriptor.local_path or "")
        filename = sanitize_filename(descriptor.file_name or source_path.name)
        staged_path = _temporary_path(self.staging_root, filename)
        created_path: Path | None = staged_path
        sha256 = hashlib.sha256()
        size = 0

        try:
            with source_path.open("rb") as source, staged_path.open("wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_file_size_bytes:
                        raise DownloadError("Local CAD asset exceeds maximum file size.")
                    sha256.update(chunk)
                    output.write(chunk)
            _verify_staged_file(staged_path, filename, descriptor.content_type, size)
            created_path = None
            return _result(descriptor, staged_path, filename, sha256.hexdigest(), size, descriptor.content_type)
        except Exception as exc:
            _cleanup(created_path)
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError(f"Failed to stage local CAD asset: {exc}") from exc


def sanitize_filename(filename: str) -> str:
    """Return a conservative basename suitable for local staging."""

    name = Path(filename.replace("\\", "/")).name.strip()
    name = _SAFE_NAME_PATTERN.sub("_", name).strip("._")
    if not name:
        name = "source.dwg"
    if Path(name).suffix.lower() != ".dwg":
        name = f"{Path(name).stem or 'source'}.dwg"
    return name


def _temporary_path(staging_root: Path, filename: str) -> Path:
    suffix = Path(filename).suffix or ".dwg"
    with tempfile.NamedTemporaryFile(prefix="cad_download_", suffix=suffix, dir=staging_root, delete=False) as handle:
        return Path(handle.name)


def _filename_from_url(url: str | None) -> str:
    if not url:
        return "source.dwg"
    path = urlparse(url).path
    return PurePosixPath(path).name or "source.dwg"


def _verify_staged_file(path: Path, filename: str, content_type: str | None, size: int) -> None:
    if size <= 0 or not path.exists() or path.stat().st_size <= 0:
        raise DownloadError("Downloaded CAD asset is empty.")
    extension_ok = Path(filename).suffix.lower() == ".dwg"
    content_type_ok = _is_dwg_content_type(content_type)
    with path.open("rb") as handle:
        prefix = handle.read(6)
    binary_signature_ok = prefix.startswith(b"AC10")
    if not (extension_ok or content_type_ok or binary_signature_ok):
        raise DownloadError("Downloaded CAD asset is not plausibly a DWG.")


def _is_dwg_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    lowered = content_type.lower()
    return any(token in lowered for token in _DWG_CONTENT_TYPE_TOKENS)


def _result(
    descriptor: CadAssetDescriptor,
    staged_path: Path,
    filename: str,
    sha256: str,
    size: int,
    content_type: str | None,
) -> DownloadResult:
    return DownloadResult(
        staged_path=staged_path,
        original_filename=descriptor.file_name,
        sanitized_filename=filename,
        sha256=sha256,
        size_bytes=size,
        source_url=descriptor.url,
        media_id=descriptor.media_id,
        vendor_revision=descriptor.vendor_revision,
        vendor_updated_at=descriptor.vendor_updated_at,
        content_type=content_type,
    )


def _cleanup(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)

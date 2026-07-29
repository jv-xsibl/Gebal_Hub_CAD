"""Tests for Stage 13 batch orchestration helpers."""

from __future__ import annotations

import json
from pathlib import Path

from tests.live.stage13_batch_qualification import discover_occurrences, filename_matches_sku, select_top_view


def test_select_top_view_ignores_backup_side_model_and_output_files(tmp_path: Path) -> None:
    files = []
    for name in ("side_view.dwg", "product_model.dwg", "output.dwg", "top_view.dwg.bak", "top_view_104512M.dwg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        files.append(path)

    selected, candidates, ignored, issues = select_top_view(files, "104512M")

    assert selected == tmp_path / "top_view_104512M.dwg"
    assert [path.name for path in candidates] == ["top_view_104512M.dwg"]
    assert sorted(path.name for path in ignored) == ["output.dwg", "product_model.dwg", "side_view.dwg", "top_view.dwg.bak"]
    assert issues == []


def test_filename_mismatch_detects_sku_token_conflict() -> None:
    assert filename_matches_sku("top_view.dwg", "175532M") is True
    assert filename_matches_sku("top_view_175532M.dwg", "175532M") is True
    assert filename_matches_sku("top_view_175332M.dwg", "175532M") is False


def test_discover_occurrences_preserves_repeated_skus(tmp_path: Path) -> None:
    for example, folder in (("Example1", "175050"), ("Example4", "175050")):
        product = tmp_path / example / folder
        product.mkdir(parents=True)
        (product / f"{folder}.json").write_text(json.dumps({"sku": "175050"}), encoding="utf-8")
        (product / "top_view.dwg").write_bytes(b"dwg")

    for index in (2, 3, 5):
        (tmp_path / f"Example{index}").mkdir()

    occurrences = discover_occurrences(tmp_path)

    assert len(occurrences) == 2
    assert {item.example for item in occurrences} == {"Example1", "Example4"}
    assert all(item.repeated_sku for item in occurrences)
    assert occurrences[0].cad_path is not None

"""Unit tests for services/pipeline.py."""

from __future__ import annotations

from typing import Any

from sam_trader.services.pipeline import run_pipeline


class TestRunPipeline:
    def test_pipeline_placeholder_logs(self, capsys: Any) -> None:
        run_pipeline("08:30")
        captured = capsys.readouterr()
        assert "pipeline_triggered=true" in captured.out
        assert "schedule=08:30" in captured.out
        assert "placeholder" in captured.out

    def test_pipeline_default_schedule(self, capsys: Any) -> None:
        run_pipeline()
        captured = capsys.readouterr()
        assert "pipeline_triggered=true" in captured.out

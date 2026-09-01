#!/usr/bin/env python3
"""Offline self-test for confirmation-gated production and provider profiles."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from production_tool import (
    ConfirmationRequiredError,
    confirm_job,
    prepare_job,
    run_job,
)
from provider_adapters import (
    compile_gpt_image_2_payload,
    compile_minimax_music_payload,
    compile_seedance_payload,
)

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("selftest.py requires Python 3.9 or newer")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        project = base / "project"
        project.mkdir()
        (project / "short-drama.json").write_text("{}\n", encoding="utf-8")
        job_path = project / "music-job.json"
        job: dict[str, Any] = {
            "job_id": "SELFTEST-MUSIC-001",
            "modality": "music",
            "adapter": "fixture",
            "prompt": "restrained instrumental tension",
            "references": [],
            "outputs": ["剧集/EP001/制作成果/music/cue.wav"],
            "parameters": {"is_instrumental": True},
            "overwrite": False,
        }
        job_path.write_text(json.dumps(job), encoding="utf-8")
        config = base / "adapters.json"
        fixture = Path(__file__).with_name("fixture_adapter.py")
        config.write_text(
            json.dumps(
                {
                    "adapters": {
                        "fixture": {
                            "command": [sys.executable, str(fixture)],
                            "timeout_seconds": 30,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        preview = prepare_job(project, job_path)
        try:
            confirm_job(project, job_id=job["job_id"], confirmation="CONFIRM wrong")
        except ConfirmationRequiredError:
            pass
        else:
            raise AssertionError("a mismatched production confirmation was accepted")
        confirm_job(
            project,
            job_id=job["job_id"],
            confirmation=preview["confirmation"],
        )
        result = run_job(project, job_id=job["job_id"], adapter_config=config)
        require(result["state"] == "succeeded", "fixture production did not succeed")
        require(
            (project / job["outputs"][0]).read_bytes().startswith(b"RIFF"),
            "fixture production did not write WAV media",
        )

    require(
        compile_gpt_image_2_payload(
        {
            "modality": "image",
            "prompt": "portrait",
            "references": [],
            "outputs": ["制作成果/portrait.png"],
            "parameters": {"size": "1024x1024"},
        }
        )["model"]
        == "gpt-image-2",
        "GPT Image 2 profile compiled the wrong model",
    )
    seedance = compile_seedance_payload(
        {
            "modality": "video",
            "prompt": "slow push in",
            "references": [],
            "outputs": ["制作成果/shot.mp4"],
            "parameters": {"duration": 5, "ratio": "9:16"},
        },
        model="configured-endpoint",
        allowed_ratios={"9:16"},
        duration_range=(5, 10),
    )
    require(
        seedance.get("ratio") == "9:16" and seedance.get("duration") == 5,
        "Seedance profile did not compile explicit API parameters",
    )
    require(
        compile_minimax_music_payload(
        {
            "modality": "music",
            "prompt": "restrained score",
            "references": [],
            "outputs": ["制作成果/cue.mp3"],
            "parameters": {"is_instrumental": True},
        }
        )["model"]
        == "music-3.0",
        "MiniMax Music profile compiled the wrong model",
    )

    print("8 self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

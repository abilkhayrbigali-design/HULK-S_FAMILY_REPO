"""Run the frozen synthetic pilot and export; assumes a prepared GPU environment."""

import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from app.diagnostics import log_internal_error
from brev_pilot import (
    FORMAT_VERSION,
    ROOT,
    SYSTEM,
    PilotError,
    SafeParser,
    new_private_directory,
    prepare,
    write_json,
)
from cqa_model_pack import sha_file

logger = logging.getLogger("cqa.training")
CONFIG = ROOT / "tools/cqa-training-v1.json"


def run_stage(command, *, cwd, env, timeout):
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
        return subprocess.CompletedProcess(command, code)
    except BaseException:
        # Converters/quantizers are children of this stage. Kill the whole session's
        # process group, including children that outlive their immediate parent.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise


def run(config_path, output):
    config = json.loads(config_path.read_text())
    if (
        config.get("schema_version") != 1
        or config.get("model") != "google/gemma-4-E4B-it"
    ):
        raise PilotError("INVALID_TRAINING_CONFIG")
    if (
        config.get("format_version") != FORMAT_VERSION
        or config.get("human_ground_truth") is not False
    ):
        raise PilotError("INVALID_TRAINING_CONFIG")
    if not re.fullmatch(r"[a-f0-9]{40}", config.get("revision", "")):
        raise PilotError("MODEL_REVISION_REQUIRED")
    source = (ROOT / config["dataset"]).resolve()
    if (
        not source.is_relative_to(ROOT / "data/fixtures/synthetic")
        or sha_file(source) != config["dataset_sha256"]
    ):
        raise PilotError("DATASET_CHANGED")
    if hashlib.sha256(SYSTEM.encode()).hexdigest() != config["prompt_sha256"]:
        raise PilotError("PROMPT_VERSION_MISMATCH")
    profile = config["training"]
    if not isinstance(profile, dict) or any(
        type(profile.get(k)) is not int
        for k in ("max_steps", "max_length", "max_seconds", "stage_timeout_seconds")
    ):
        raise PilotError("INVALID_TRAINING_CONFIG")
    if any(
        profile.get(k) != value
        for k, value in {
            "rank": 16,
            "alpha": 16,
            "learning_rate": 0.00002,
            "seed": 42,
        }.items()
    ):
        raise PilotError("UNSUPPORTED_TRAINING_PROFILE")
    if (
        not 1 <= profile["max_steps"] <= 200
        or not 128 <= profile["max_length"] <= 16384
        or not 1 <= profile["max_seconds"] <= 7200
    ):
        raise PilotError("INVALID_TRAINING_LIMIT")
    if not 60 <= profile["stage_timeout_seconds"] <= 7200:
        raise PilotError("INVALID_TRAINING_LIMIT")
    output = new_private_directory(output)
    state = {
        "status": "started",
        "training_status": "not_completed",
        "config_sha256": sha_file(config_path),
        "human_ground_truth": False,
    }
    write_json(output / "started.json", state)
    try:
        dataset = output / "dataset"
        prepare(source, dataset, "synthetic_training_candidate")
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        }
        commands = [
            [
                "tools/cqa_model_pack.py",
                "tokenize",
                "--dataset",
                str(dataset),
                "--output",
                str(output / "tokens"),
                "--model",
                config["model"],
                "--revision",
                config["revision"],
                "--max-length",
                str(profile["max_length"]),
            ],
            [
                "tools/brev_pilot.py",
                "train",
                "--dataset",
                str(dataset),
                "--output",
                str(output / "training"),
                "--model",
                config["model"],
                "--revision",
                config["revision"],
                "--steps",
                str(profile["max_steps"]),
                "--max-length",
                str(profile["max_length"]),
                "--max-seconds",
                str(profile["max_seconds"]),
            ],
            [
                "tools/cqa_model_pack.py",
                "export",
                "--run",
                str(output / "training"),
                "--output",
                str(output / "export"),
            ],
        ]
        # Each stage is a fresh process so token-only Transformers cannot precede Unsloth.
        for number, command in enumerate(commands, 1):
            logger.info("event=training_stage_started status=started stage=%d", number)
            result = run_stage(
                [sys.executable, *command],
                cwd=ROOT,
                env=environment,
                timeout=profile["stage_timeout_seconds"],
            )
            if result.returncode != 0:
                raise PilotError("TRAINING_STAGE_FAILED")
        export = json.loads((output / "export/export.json").read_text())
        if export.get("status") != "completed":
            raise PilotError("EXPORT_FAILED")
        state.update(
            status="completed",
            training_status="finetuned_candidate",
            artifact="export/" + export["file"],
            artifact_sha256=export["sha256"],
            local_runtime_verified=False,
            quality_improvement_verified=False,
        )
        write_json(output / "outcome.json", state)
        return state
    except BaseException:
        write_json(output / "outcome.json", {**state, "status": "failed"})
        raise


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = SafeParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        logger.info("event=training_job_started status=started")
        run(args.config, args.output)
        logger.info("event=training_job_completed status=completed")
        return 0
    except (
        PilotError,
        ValueError,
        KeyError,
        FileExistsError,
        subprocess.TimeoutExpired,
    ) as exc:
        code = str(exc) if isinstance(exc, PilotError) else "TRAINING_JOB_REJECTED"
        if code in {"TRAINING_STAGE_FAILED", "EXPORT_FAILED"} or isinstance(
            exc, subprocess.TimeoutExpired
        ):
            logger.error("event=training_job_failed status=failed error=%s", code)
        else:
            logger.warning("event=training_job_rejected status=failed error=%s", code)
        return 2
    except KeyboardInterrupt:
        logger.warning(
            "event=training_job_stopped status=interrupted error=TRAINING_INTERRUPTED"
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary strips private exception text.
        log_internal_error("TRAINING_JOB_FAILED", exc)
        logger.error(
            "event=training_job_failed status=failed error=TRAINING_JOB_FAILED"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

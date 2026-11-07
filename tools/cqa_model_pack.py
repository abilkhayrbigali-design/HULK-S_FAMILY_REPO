"""Token preflight, private local smoke and portable export for a CQA candidate."""

import hashlib
import importlib.metadata
import json
import logging
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path

from app.diagnostics import log_internal_error
from app.evaluation.evidence import EvaluationValidationError
from brev_pilot import (
    FORMAT_VERSION,
    MODELS,
    SYSTEM,
    PilotError,
    SafeParser,
    adapter_manifest,
    chat_messages,
    checked_response,
    encoded,
    features,
    frozen_examples,
    new_private_directory,
    quiet_libraries,
    read_examples,
    response_schema,
    text_tokenizer,
    write_json,
)
from pydantic import ValidationError

logger = logging.getLogger("cqa.training")
LOCAL = "http://127.0.0.1:11439"
BASE_TAG = "gemma4:e4b"
BASE_DIGEST = "dc35e8d9c6061baa6f0fa870975ab6932e2542b579b13ea0f199fa4bb7300c9c"


def sha_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def local_request(endpoint, payload=None, timeout=150):
    request = urllib.request.Request(
        LOCAL + endpoint,
        data=encoded(payload) if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise PilotError("LOCAL_RESPONSE_TOO_LARGE")
    result = json.loads(body)
    if not isinstance(result, dict) or "error" in result:
        raise PilotError("LOCAL_LLM_FAILED")
    return result


def configure_local(name, output):
    if not re.fullmatch(r"cqa-[a-z0-9._:-]{1,100}", name):
        raise PilotError("INVALID_LOCAL_MODEL_NAME")
    tags = local_request("/api/tags").get("models", [])
    base = next((m for m in tags if m.get("name") == BASE_TAG), None)
    if base is None or base.get("digest") != BASE_DIGEST:
        raise PilotError("LOCAL_BASE_DIGEST_MISMATCH")
    if any(m.get("name") in {name, name + ":latest"} for m in tags):
        raise PilotError("LOCAL_MODEL_ALREADY_EXISTS")
    output = new_private_directory(output)
    result = local_request(
        "/api/create",
        {
            "model": name,
            "from": BASE_TAG,
            "system": SYSTEM,
            "stream": False,
            "parameters": {
                "num_ctx": 16384,
                "num_predict": 3072,
                "temperature": 0,
                "seed": 42,
            },
        },
    )
    if result.get("status") != "success":
        raise PilotError("LOCAL_MODEL_CREATE_FAILED")
    state = {
        "status": "configured",
        "training_status": "not_finetuned",
        "implementation_sha256": sha_file(Path(__file__)),
        "base_model": BASE_TAG,
        "base_digest": BASE_DIGEST,
        "model": name,
        "format_version": FORMAT_VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
    }
    write_json(output / "local-model.json", state)
    return state


def token_preflight(args):
    if args.model not in MODELS or not re.fullmatch(r"[a-f0-9]{40}", args.revision):
        raise PilotError("MODEL_REVISION_REQUIRED")
    if not 128 <= args.max_length <= 16384:
        raise PilotError("INVALID_TRAINING_LIMIT")
    examples = [e for e in frozen_examples(args.dataset) if e.split != "holdout"]
    output = new_private_directory(args.output)
    with quiet_libraries():
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["USE_TORCH"] = "0"
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            args.model, revision=args.revision, trust_remote_code=False
        )
        tokenizer = text_tokenizer(tokenizer)
        rows = [features(tokenizer, e, args.max_length) for e in examples]
    lengths = [len(row["input_ids"]) for row in rows]
    state = {
        "status": "completed",
        "model": args.model,
        "revision": args.revision,
        "records": len(rows),
        "max_total_tokens": max(lengths),
        "max_prompt_tokens": max(sum(v == -100 for v in row["labels"]) for row in rows),
        "max_answer_tokens": max(sum(v != -100 for v in row["labels"]) for row in rows),
        "max_length": args.max_length,
        "holdout_tokenized": False,
        "transformers_version": importlib.metadata.version("transformers"),
        "manifest_sha256": sha_file(args.dataset / "manifest.json"),
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "format_version": FORMAT_VERSION,
    }
    write_json(output / "token-preflight.json", state)
    return state


def smoke(args):
    if not 1 <= args.count <= 12:
        raise PilotError("INVALID_SMOKE_LIMIT")
    examples = read_examples(args.input)
    # Only train cases; holdout is never consumed by this preparation smoke.
    training = [e for e in examples if e.split == "train"]
    full = [e for e in training if len(e.criteria) == 8]
    edited = [
        e
        for e in training
        if e.scenario_group and e.scenario_group.startswith("edited_")
    ]
    ninth = [e for e in training if len(e.criteria) == 9]
    attack = [e for e in training if e.scenario_group == "injection_bot"]
    ordered = full[:3] + edited[:1] + ninth[:1] + attack[:1]
    if len(ordered) < args.count:
        ordered.extend(e for e in training if e not in ordered)
    chosen = ordered[: args.count]
    if not chosen:
        raise PilotError("NO_TRAINING_EXAMPLES")
    output = new_private_directory(args.output)
    outcomes = []
    for example in chosen:
        started = time.monotonic()
        try:
            result = local_request(
                "/api/chat",
                {
                    "model": args.model,
                    "messages": chat_messages(example),
                    "format": response_schema(example),
                    "stream": False,
                    "think": False,
                    "keep_alive": "1m",
                    "options": {
                        "num_ctx": 16384,
                        "num_predict": 3072,
                        "temperature": 0,
                        "seed": 42,
                    },
                },
            )
            if result.get("done") is not True or result.get("done_reason") != "stop":
                raise PilotError("LOCAL_GENERATION_INCOMPLETE")
            payload = json.loads(result["message"]["content"])
            actual = checked_response(example, payload)
            expected = {r.criterion_id: r for r in example.criterion_results}
            matched = sum(
                r.applicability == expected[r.criterion_id].applicability
                and r.answer == expected[r.criterion_id].answer
                for r in actual
            )
            row = {
                "case_id": example.case_id,
                "status": "valid",
                "criteria": len(actual),
                "author_matches": matched,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "model": result.get("model"),
                "input_tokens": result.get("prompt_eval_count"),
                "output_tokens": result.get("eval_count"),
            }
            # Response artifacts are private data, never log messages or CI output.
            write_json(output / f"response-{len(outcomes) + 1}.json", payload)
            logger.info("event=model_smoke_case status=valid criteria=%d", len(actual))
        except (
            PilotError,
            EvaluationValidationError,
            ValidationError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ):
            row = {
                "case_id": example.case_id,
                "status": "failed",
                "error": "LOCAL_RESULT_INVALID",
            }
            logger.warning(
                "event=model_smoke_case status=failed error=LOCAL_RESULT_INVALID"
            )
        outcomes.append(row)
    state = {
        "status": "completed"
        if all(r["status"] == "valid" for r in outcomes)
        else "failed",
        "records": len(outcomes),
        "valid": sum(r["status"] == "valid" for r in outcomes),
        "purpose": "training_only_synthetic_smoke",
        "human_ground_truth": False,
        "training_status": "not_asserted_by_smoke",
        "model": args.model,
        "source_sha256": sha_file(args.input),
        "format_version": FORMAT_VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "outcomes": outcomes,
    }
    write_json(output / "smoke-summary.json", state)
    return state


def export(args):
    run = args.run.resolve()
    state = json.loads((run / "outcome.json").read_text())
    if (
        state.get("status") != "completed"
        or state.get("format_version") != FORMAT_VERSION
    ):
        raise PilotError("COMPLETED_TRAINING_REQUIRED")
    if state.get("model") not in MODELS or not re.fullmatch(
        r"[a-f0-9]{40}", state.get("revision", "")
    ):
        raise PilotError("MODEL_REVISION_REQUIRED")
    if state.get("prompt_sha256") != hashlib.sha256(SYSTEM.encode()).hexdigest():
        raise PilotError("PROMPT_VERSION_MISMATCH")
    if not state.get("adapter_sha256") or state["adapter_sha256"] != adapter_manifest(
        run / "adapter"
    ):
        raise PilotError("ADAPTER_CHANGED")
    output = new_private_directory(args.output)
    try:
        with quiet_libraries():
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            from unsloth import FastLanguageModel, FastModel  # isort: skip

            import torch
            from huggingface_hub import snapshot_download

            if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
                raise PilotError("BF16_GPU_REQUIRED")
            base = snapshot_download(
                repo_id=state["model"],
                revision=state["revision"],
                allow_patterns=[
                    "*.safetensors",
                    "*.json",
                    "*.jinja",
                    "*.model",
                    "*.txt",
                    "LICENSE*",
                    "NOTICE*",
                ],
            )
            # Export copy points at an immutable local snapshot, never latest remote weights.
            adapter = output / "adapter-for-export"
            shutil.copytree(run / "adapter", adapter)
            config_path = adapter / "adapter_config.json"
            config = json.loads(config_path.read_text())
            config["base_model_name_or_path"], config["revision"] = base, None
            config_path.write_text(json.dumps(config))
            loader = (
                FastModel
                if state["model"].startswith("google/gemma-4-")
                else FastLanguageModel
            )
            model, processor = loader.from_pretrained(
                model_name=str(adapter),
                max_seq_length=state["max_length"],
                load_in_4bit=False,
                load_in_16bit=True,
                dtype=torch.bfloat16,
                use_exact_model_name=True,
            )
            tokenizer = text_tokenizer(processor)
            model.save_pretrained_gguf(
                str(output / "gguf"), tokenizer, quantization_method="q4_k_m"
            )
        files = [
            p for p in (output / "gguf").rglob("*.gguf") if "q4_k_m" in p.name.lower()
        ]
        if len(files) != 1:
            raise PilotError("SINGLE_Q4_ARTIFACT_REQUIRED")
        artifact = files[0]
        (artifact.parent / "Modelfile").write_text(
            f'FROM ./{artifact.name}\nSYSTEM """{SYSTEM}"""\n'
            "PARAMETER num_ctx 16384\nPARAMETER num_predict 3072\nPARAMETER temperature 0\n"
        )
        result = {
            "status": "completed",
            "training_status": "finetuned_candidate",
            "model": state["model"],
            "revision": state["revision"],
            "file": str(artifact.relative_to(output)),
            "sha256": sha_file(artifact),
            "bytes": artifact.stat().st_size,
            "quantization": "q4_k_m",
            "format_version": FORMAT_VERSION,
            "prompt_sha256": state["prompt_sha256"],
            "local_runtime_verified": False,
            "quality_improvement_verified": False,
        }
        write_json(output / "export.json", result)
        return result
    except BaseException as exc:
        write_json(
            output / "export.json", {"status": "failed", "error": "EXPORT_FAILED"}
        )
        if isinstance(exc, SystemExit):
            raise PilotError("EXPORT_FAILED") from None
        raise


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = SafeParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    local = sub.add_parser("configure-local")
    local.add_argument("--name", required=True)
    local.add_argument("--output", type=Path, required=True)
    token = sub.add_parser("tokenize")
    token.add_argument("--dataset", type=Path, required=True)
    token.add_argument("--output", type=Path, required=True)
    token.add_argument("--model", default=MODELS[-1])
    token.add_argument("--revision", required=True)
    token.add_argument("--max-length", type=int, default=8192)
    test = sub.add_parser("smoke")
    test.add_argument("--input", type=Path, required=True)
    test.add_argument("--model", required=True)
    test.add_argument("--output", type=Path, required=True)
    test.add_argument("--count", type=int, default=6)
    save = sub.add_parser("export")
    save.add_argument("--run", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        logger.info("event=model_pack_started status=started")
        if args.command == "configure-local":
            state = configure_local(args.name, args.output)
        elif args.command == "tokenize":
            state = token_preflight(args)
        elif args.command == "export":
            state = export(args)
        else:
            state = smoke(args)
        if state["status"] == "failed":
            logger.error(
                "event=model_pack_failed status=failed error=MODEL_PACK_FAILED"
            )
            return 2
        logger.info("event=model_pack_completed status=completed")
        return 0
    except (
        PilotError,
        EvaluationValidationError,
        ValidationError,
        ValueError,
        KeyError,
        FileExistsError,
    ) as exc:
        code = str(exc) if isinstance(exc, PilotError) else "INVALID_MODEL_PACK_INPUT"
        logger.warning("event=model_pack_rejected status=failed error=%s", code)
        return 2
    except KeyboardInterrupt:
        logger.warning(
            "event=model_pack_stopped status=interrupted error=MODEL_PACK_INTERRUPTED"
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary must not print private exceptions.
        log_internal_error("MODEL_PACK_FAILED", exc)
        logger.error("event=model_pack_failed status=failed error=MODEL_PACK_FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

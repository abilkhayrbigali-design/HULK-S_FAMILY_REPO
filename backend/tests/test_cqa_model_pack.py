"""Check authored corpus coverage, immutable splits and the portable model workflow."""

import hashlib
import importlib
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
pilot = importlib.import_module("brev_pilot")
corpus = importlib.import_module("build_cqa_corpus")
pack = importlib.import_module("cqa_model_pack")
runner = importlib.import_module("run_cqa_training")

SOURCE = ROOT / "data/fixtures/synthetic/cqa-training-v1.jsonl"


@pytest.fixture
def private(tmp_path, monkeypatch):
    path = tmp_path / "private"
    monkeypatch.setattr(pilot, "PRIVATE", path)
    return path


def test_authored_source_is_reproducible_and_has_all_languages():
    generated = b"".join(pilot.encoded(e.model_dump()) + b"\n" for e in corpus.build_examples())
    assert hashlib.sha256(generated).digest() == hashlib.sha256(SOURCE.read_bytes()).digest()
    examples = pilot.read_examples(SOURCE)
    assert Counter(e.language for e in examples) == {"RU": 59, "KZ": 59, "EN": 59}
    assert Counter(e.split for e in examples) == {"train": 156, "validation": 9, "holdout": 12}


def test_training_part_covers_all_original_answer_classes():
    answers = {}
    for e in pilot.read_examples(SOURCE):
        if e.split != "train":
            continue
        for result in e.criterion_results:
            answers.setdefault(result.criterion_id, set()).add(result.answer)
    assert answers["contact"] == {True, False}
    assert answers["needs"] == {True, False, None}
    assert answers["identification"] == {True, False, None}
    assert answers["consultation"] == {0, 5, 10, 15, 20, None}
    assert answers["empathy"] == {True, False}
    assert answers["management"] == {0, 4, 7, 10}
    assert answers["closing"] == {True, False, None}
    assert answers["gross_errors"] == {True, False}


def test_current_keyed_schema_matches_all_labels_and_round_trips():
    for e in pilot.read_examples(SOURCE):
        payload = pilot.response_payload(e)
        jsonschema.validate(payload, pilot.response_schema(e))
        actual = pilot.checked_response(e, payload)
        assert len(actual) == len(e.criteria)
        assert [r.criterion_id for r in actual] == [c.id for c in e.criteria]


def test_translated_group_cannot_cross_partitions():
    examples = pilot.read_examples(SOURCE)
    train = next(e for e in examples if e.split == "train")
    holdout = next(e for e in examples if e.split == "holdout")
    holdout.scenario_group = train.scenario_group
    with pytest.raises(pilot.PilotError, match="SPLIT_GROUP_LEAKAGE"):
        pilot.check_partition(examples)


def test_authored_lineages_are_isolated_before_variants():
    examples = pilot.read_examples(SOURCE)
    families = {}
    for e in examples:
        assert e.scenario_family
        families.setdefault(e.scenario_family, set()).add(e.split)
    assert families == {
        "source_training_v1": {"train"},
        "document_export": {"validation"},
        "currency_display": {"holdout"},
    }
    variants = [e for e in examples if e.scenario_group == "full_both_missing"]
    assert variants and all(e.split == "train" for e in variants)
    # Renaming the variant does not remove its source family membership.
    variants[0].scenario_group = "renamed_variant"
    variants[0].split = "holdout"
    with pytest.raises(pilot.PilotError, match="SPLIT_FAMILY_LEAKAGE"):
        pilot.check_partition(examples)


def test_same_dialogue_different_checklist_is_allowed_within_train():
    examples = pilot.read_examples(SOURCE)
    # The initial dialogue and edited N/A rules are intentionally paired in train.
    pilot.check_partition(examples)
    edited = next(e for e in examples if e.scenario_group == "edited_identification")
    assert edited.split == "train" and not edited.criteria[0].allow_na
    assert edited.criterion_results[0].answer is False


def test_preparation_records_author_provenance_and_pins_prompt(private, monkeypatch):
    output = private / "dataset"
    manifest = pilot.prepare(SOURCE, output, "synthetic_training_candidate")
    assert manifest["human_ground_truth"] is False
    assert manifest["label_source"] == "synthetic_author"
    assert manifest["format_version"] == pilot.FORMAT_VERSION
    monkeypatch.setattr(pilot, "SYSTEM", "changed prompt")
    with pytest.raises(pilot.PilotError, match="PROMPT_VERSION_MISMATCH"):
        pilot.frozen_examples(output)


@pytest.mark.parametrize("mutation", ["coverage", "override", "unknown_root", "wrong_type"])
def test_unsafe_result_shapes_are_rejected(mutation):
    e = pilot.read_examples(SOURCE)[0]
    payload = pilot.response_payload(e)
    if mutation == "coverage":
        del payload["criterion_results"]["contact"]
    elif mutation == "override":
        payload["criterion_results"]["contact"]["criterion_id"] = "another"
    elif mutation == "unknown_root":
        payload["final_score"] = 100
    else:
        payload["criterion_results"]["contact"]["answer"] = 1
    with pytest.raises((pilot.PilotError, pilot.EvaluationValidationError)):
        pilot.checked_response(e, payload)


def test_schema_links_na_to_null_instead_of_allowing_inconsistent_pairs():
    e = pilot.read_examples(SOURCE)[0]
    payload = pilot.response_payload(e)
    payload["criterion_results"]["needs"]["answer"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, pilot.response_schema(e))


def test_local_configuration_is_explicitly_not_finetuned(private, monkeypatch):
    calls = []

    def request(endpoint, payload=None):
        calls.append(endpoint)
        if endpoint == "/api/tags":
            return {"models": [{"name": pack.BASE_TAG, "digest": pack.BASE_DIGEST}]}
        return {"status": "success"}

    monkeypatch.setattr(pack, "local_request", request)
    state = pack.configure_local("cqa-rubric-test", private / "local")
    assert calls == ["/api/tags", "/api/create"]
    assert state["training_status"] == "not_finetuned"


def test_local_base_mismatch_never_creates_model(private, monkeypatch):
    monkeypatch.setattr(pack, "local_request", lambda endpoint: {"models": []})
    with pytest.raises(pilot.PilotError, match="LOCAL_BASE_DIGEST_MISMATCH"):
        pack.configure_local("cqa-rubric-test", private / "local")
    assert not (private / "local").exists()


def test_export_refuses_failed_training_before_gpu_import(private):
    run = private / "run"
    run.mkdir(parents=True)
    pilot.write_json(run / "outcome.json", {"status": "failed"})
    args = type("Args", (), {"run": run, "output": private / "export"})()
    with pytest.raises(pilot.PilotError, match="COMPLETED_TRAINING_REQUIRED"):
        pack.export(args)
    assert not args.output.exists()


def test_export_refuses_adapter_changed_after_training_before_gpu_import(private):
    run = private / "run"
    adapter = run / "adapter"
    adapter.mkdir(parents=True)
    path = adapter / "adapter_model.safetensors"
    path.write_bytes(b"synthetic adapter original")
    state = {
        "status": "completed",
        "format_version": pilot.FORMAT_VERSION,
        "model": "google/gemma-4-E4B-it",
        "revision": "a" * 40,
        "prompt_sha256": hashlib.sha256(pilot.SYSTEM.encode()).hexdigest(),
        "adapter_sha256": pilot.adapter_manifest(adapter),
    }
    pilot.write_json(run / "outcome.json", state)
    path.write_bytes(b"synthetic adapter changed")
    args = type("Args", (), {"run": run, "output": private / "export"})()
    with pytest.raises(pilot.PilotError, match="ADAPTER_CHANGED"):
        pack.export(args)
    assert not args.output.exists()


def test_model_pack_cli_internal_error_is_private(monkeypatch, private, caplog):
    marker = "SYNTHETIC_PRIVATE_MODEL_PACK_ERROR"

    def failed(*args):
        raise RuntimeError(marker)

    monkeypatch.setattr(pack, "configure_local", failed)
    with caplog.at_level(logging.INFO):
        code = pack.main(
            ["configure-local", "--name", "cqa-test", "--output", str(private / "out")]
        )
    assert code == 1 and marker not in caplog.text
    assert sum(r.name == "cqa.errors" for r in caplog.records) == 1
    assert "MODEL_PACK_FAILED" in caplog.text


def test_corpus_cli_failure_preserves_existing_source(monkeypatch, tmp_path, caplog):
    target = tmp_path / "existing.jsonl"
    target.write_text("synthetic existing content")
    monkeypatch.setattr(corpus, "TARGET", target)
    with caplog.at_level(logging.INFO):
        code = corpus.main()
    assert code == 2
    assert target.read_text() == "synthetic existing content"
    assert "CORPUS_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT" in caplog.text


def test_export_checksum_is_content_based(tmp_path):
    path = tmp_path / "artifact.gguf"
    path.write_bytes(b"abc")
    assert pack.sha_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_training_failure_is_persisted_and_logged_as_error(private, monkeypatch, caplog):
    calls = []

    def failed_stage(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr(runner, "run_stage", failed_stage)
    output = private / "job"
    with caplog.at_level(logging.INFO):
        code = runner.main(["--output", str(output)])
    assert code == 2 and len(calls) == 1
    assert json.loads((output / "outcome.json").read_text())["status"] == "failed"
    assert any(
        r.levelno == logging.ERROR and "TRAINING_STAGE_FAILED" in r.message for r in caplog.records
    )


def test_unsupported_profile_cannot_be_silently_ignored(private, tmp_path):
    config = json.loads(runner.CONFIG.read_text())
    config["training"]["rank"] = 32
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(pilot.PilotError, match="UNSUPPORTED_TRAINING_PROFILE"):
        runner.run(path, private / "job")
    assert not (private / "job").exists()


def test_completed_job_artifact_is_relative_to_job_directory(private, monkeypatch):
    output = private / "job"

    def stage(command, **kwargs):
        if command[2] == "export":
            path = output / "export/gguf/synthetic.gguf"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic artifact")
            pilot.write_json(
                output / "export/export.json",
                {
                    "status": "completed",
                    "file": "gguf/synthetic.gguf",
                    "sha256": pack.sha_file(path),
                },
            )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(runner, "run_stage", stage)
    result = runner.run(runner.CONFIG, output)
    assert result["status"] == "completed"
    assert pack.sha_file(output / result["artifact"]) == result["artifact_sha256"]


@pytest.mark.skipif(sys.platform != "linux", reason="GPU runner uses Linux process groups")
def test_stage_timeout_stops_converter_children(tmp_path):
    marker = tmp_path / "child-pid"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_stage([sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout=1)
    pid = int(marker.read_text())
    status = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 2
    while status.exists() and status.read_text().split()[2] != "Z" and time.monotonic() < deadline:
        time.sleep(0.02)
    # A zombie awaits kernel reaping but cannot keep computing or converting.
    assert not status.exists() or status.read_text().split()[2] == "Z"

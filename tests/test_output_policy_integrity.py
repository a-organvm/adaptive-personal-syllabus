"""CLI regressions for artifact consent, authorship and optional learning use."""

import hashlib
import json

import pytest
from click.testing import CliRunner

from adaptive_personal_syllabus.core import cli
from adaptive_personal_syllabus.corpus import CorpusIngestor
from adaptive_personal_syllabus.ledger import Ledger
from adaptive_personal_syllabus.planner import Planner
from adaptive_personal_syllabus.storage import Storage
from tests.test_planner import _profile, _seed_dir


@pytest.fixture
def prepared(tmp_path):
    storage = Storage(tmp_path / "learning.db")
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "synthetic.txt").write_text("Foundations need inspectable evidence.")
    CorpusIngestor(storage, Ledger(storage)).ingest(source, "synthetic")
    planner = Planner(storage, Ledger(storage), _seed_dir(tmp_path))
    plan = planner.generate(
        _profile(
            tmp_path,
            level="unassessed",
            context={"private": "PRIVATE_CONTEXT_NOT_FOR_OUTPUT"},
            output_policy={"selected_wings": ["academic"]},
        )
    )
    return storage, plan


def artifact_args(storage, plan, output, wing="academic"):
    return [
        "plan",
        "artifact",
        str(plan["db_plan_id"]),
        "--wing",
        wing,
        "--output",
        str(output),
        "--db-path",
        str(storage.db_path),
    ]


def test_missing_artifact_parent_is_actionable_and_has_no_receipt(prepared, tmp_path):
    storage, plan = prepared
    before = storage.iter_ledger_events()
    output = tmp_path / "missing" / "sheet.md"
    result = CliRunner().invoke(cli, artifact_args(storage, plan, output))
    assert result.exit_code == 1
    assert "existing directory" in result.output
    assert "Traceback" not in result.output
    assert not output.exists()
    assert storage.iter_ledger_events() == before


def test_existing_artifact_preserves_exact_learner_words(prepared, tmp_path):
    storage, plan = prepared
    output = tmp_path / "my_words.md"
    original = b"I do not think the result warrants that conclusion.\n"
    output.write_bytes(original)
    before = storage.iter_ledger_events()
    result = CliRunner().invoke(cli, artifact_args(storage, plan, output))
    assert result.exit_code == 1
    assert "choose a new version" in result.output
    assert output.read_bytes() == original
    assert storage.iter_ledger_events() == before


def test_selected_descriptor_does_not_authorize_other_wings(prepared, tmp_path):
    storage, plan = prepared
    output = tmp_path / "social.md"
    before = storage.iter_ledger_events()
    result = CliRunner().invoke(cli, artifact_args(storage, plan, output, wing="social"))
    assert result.exit_code == 1
    assert "Wing is not selected" in result.output
    assert not output.exists()
    assert storage.iter_ledger_events() == before


def test_authorization_requires_explicit_consent_and_destination(prepared, tmp_path):
    storage, _ = prepared
    artifact = tmp_path / "learner.md"
    artifact.write_text("These are the exact supplied words.\n")
    args = [
        "plan",
        "authorize-publication",
        str(artifact),
        "--destination",
        "fixture-only",
        "--db-path",
        str(storage.db_path),
    ]
    before = storage.iter_ledger_events()
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    assert "--authorize" in result.output
    assert storage.iter_ledger_events() == before
    args[args.index("fixture-only")] = " "
    result = CliRunner().invoke(cli, [*args, "--authorize"])
    assert result.exit_code == 1
    assert "intended destination" in result.output
    assert storage.iter_ledger_events() == before


def test_authorization_is_bound_to_bytes_and_never_changes_plan(prepared, tmp_path):
    storage, plan = prepared
    artifact = tmp_path / "academic.md"
    runner = CliRunner()
    result = runner.invoke(cli, artifact_args(storage, plan, artifact))
    assert result.exit_code == 0, result.output
    generated = artifact.read_bytes()
    assert b"Assistant-authored scaffold; not learner-authored work" in generated
    assert b"Learner words: [not supplied]" in generated
    assert plan["modules"][0]["encounter"]["self_contained_example"].encode() in generated
    assert b"independent practice in inspecting a claim" in generated
    assert b"PRIVATE_CONTEXT_NOT_FOR_OUTPUT" not in generated
    args = [
        "plan",
        "authorize-publication",
        str(artifact),
        "--destination",
        "fixture-only",
        "--authorize",
        "--db-path",
        str(storage.db_path),
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    first_receipt = json.loads(first.output)
    assert first_receipt["artifact_sha256"] == hashlib.sha256(generated).hexdigest()
    artifact.write_bytes(generated + b"User-supplied addition, unchanged.\n")
    second = runner.invoke(cli, args)
    assert second.exit_code == 0, second.output
    second_receipt = json.loads(second.output)
    assert first_receipt["artifact_sha256"] != second_receipt["artifact_sha256"]
    assert second_receipt["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert (
        first_receipt["publication_status"]
        == second_receipt["publication_status"]
        == "not_published"
    )
    assert storage.get_plan(plan["db_plan_id"]) == plan
    assert plan["output_policy"]["publication_authorized"] is False


@pytest.mark.parametrize(
    "route",
    [
        "encounter",
        "short_written",
        "spoken_under_two_minutes",
        "worked_explanation",
        "no_response_now",
    ],
)
def test_encounter_route_cannot_fabricate_performance_or_artifacts(prepared, tmp_path, route):
    storage, plan = prepared
    before_ledger = storage.iter_ledger_events()
    before_files = set(tmp_path.rglob("*"))
    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "encounter",
            str(plan["db_plan_id"]),
            "--route",
            route,
            "--format",
            "json",
            "--db-path",
            str(storage.db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    viewed = json.loads(result.output)
    assert viewed["status"] == "prepared_not_started"
    assert viewed["assessment_status"] == "unassessed"
    assert viewed["performance_recorded"] is False
    assert viewed["encounter"]["learner_words"] is None
    assert viewed["publication_status"] == "not_published"
    assert "PRIVATE_CONTEXT_NOT_FOR_OUTPUT" not in result.output
    assert storage.get_plan(plan["db_plan_id"]) == plan
    assert storage.iter_ledger_events() == before_ledger
    assert set(tmp_path.rglob("*")) == before_files


def test_explanation_view_uses_exact_stored_assistant_explanation(prepared):
    storage, plan = prepared
    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "encounter",
            str(plan["db_plan_id"]),
            "--route",
            "worked_explanation",
            "--db-path",
            str(storage.db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        "Assistant explanation: " + plan["modules"][0]["encounter"]["self_contained_example"]
        in result.output
    )
    assert "without assessment" in result.output
    assert "no learner response or result is recorded" in result.output
    assert "independent practice in inspecting a claim" in result.output
    assert "topic-specific instruction remains unverified" in result.output


def test_missing_encounter_module_is_user_error(prepared):
    storage, plan = prepared
    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "encounter",
            str(plan["db_plan_id"]),
            "--module-id",
            "absent",
            "--db-path",
            str(storage.db_path),
        ],
    )
    assert result.exit_code == 1
    assert "No matching module" in result.output


def test_historical_plan_does_not_invent_new_encounter(prepared):
    storage, _ = prepared
    profile_id = storage.insert_profile("Synthetic", "unassessed", [], {})
    plan_id = storage.insert_plan(profile_id, "Historical", 2, 1, 1)
    storage.insert_plan_module(plan_id, 1, "old", "Historical", "I", "unassessed", [], [], 2)
    result = CliRunner().invoke(
        cli, ["plan", "encounter", str(plan_id), "--db-path", str(storage.db_path)]
    )
    assert result.exit_code == 1
    assert "historical plan has no stored encounter" in result.output

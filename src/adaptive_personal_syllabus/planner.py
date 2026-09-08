"""Profile-aware planning that merges learner context with corpus evidence."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .generator import SyllabusGenerator
from .ledger import Ledger
from .models import DifficultyLevel, LearnerProfile, PersonalizationRule
from .storage import Storage

WINGS: list[dict[str, str]] = [
    {"wing_id": "academic", "name": "Academic", "description": "Research summary or formal analysis."},
    {"wing_id": "sop", "name": "SOP", "description": "Operational runbook or procedure."},
    {"wing_id": "business", "name": "Business", "description": "Business framing and value proposition."},
    {"wing_id": "social", "name": "Social", "description": "Public-facing social content."},
    {"wing_id": "community", "name": "Community", "description": "Community prompt and collaboration seed."},
    {"wing_id": "wiki", "name": "Wiki", "description": "Reference documentation artifact."},
    {"wing_id": "web_blog", "name": "Web/Blog", "description": "Long-form narrative publication."},
    {"wing_id": "grants", "name": "Grants", "description": "Grant-aligned research framing."},
]

FINGERPRINT_SCHEMA_VERSION = 2
SOURCE_SELECTION_SCHEMA_VERSION = 1


def _validate_finite_json(value: Any, path: str = "$") -> None:
    """Reject values that JSON accepts but portable JSON and stable hashes do not."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Non-finite JSON number at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Non-string JSON object key at {path}")
            _validate_finite_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_finite_json(child, f"{path}[{index}]")


def _tokens(*values: Any) -> set[str]:
    text = " ".join(str(value) for value in values)
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


DEFAULT_PERSONALIZATION_RULES: list[PersonalizationRule] = [
    PersonalizationRule(
        rule_id="contextual_rewrite",
        description="Rewrite generic recommendations using learner goals and context.",
        profile_fields=["goals", "context"],
    ),
    PersonalizationRule(
        rule_id="difficulty_alignment",
        description="Align module framing with learner difficulty level and completed modules.",
        profile_fields=["level", "completed_modules"],
    ),
]


class Planner:
    """Generate persisted, profile-aware plans with stable JSON output."""

    def __init__(self, storage: Storage, ledger: Ledger, seed_dir: Path | None = None) -> None:
        self.storage = storage
        self.ledger = ledger
        self.seed_dir = seed_dir

    @staticmethod
    def _load_profile(profile_path: Path) -> dict[str, Any]:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        if "name" not in data:
            raise ValueError("Profile must include 'name'")
        return data

    @staticmethod
    def _hash_personalization_rules(rules: list[PersonalizationRule]) -> str:
        blob = json.dumps(
            [
                {
                    "rule_id": r.rule_id,
                    "description": r.description,
                    "profile_fields": r.profile_fields,
                    "module_filters": r.module_filters,
                }
                for r in rules
            ],
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _deterministic_plan_uid(
        profile_data: dict[str, Any],
        modules: list[dict[str, Any]],
        *,
        snapshot_id: int,
        evidence_sha256: list[str],
        personalization_rules_hash: str,
    ) -> str:
        payload = {
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            "profile": profile_data,
            "modules": modules,
            "snapshot_id": snapshot_id,
            "evidence_sha256": evidence_sha256,
            "personalization_rules_hash": personalization_rules_hash,
        }
        _validate_finite_json(payload)
        blob = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def _select_source_candidates(
        self, *, snapshot_id: int, module: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Find local passages worth inspection without claiming they support a claim."""
        query = _tokens(module["title"], module["organ"], *module["readings"], *module["questions"])
        candidates: list[tuple[int, dict[str, Any]]] = []
        for chunk in self.storage.list_document_chunks(snapshot_id=snapshot_id):
            overlap = query & _tokens(chunk["heading_path"], chunk["text"])
            if overlap:
                candidates.append((len(overlap), chunk))
        candidates.sort(key=lambda item: (-item[0], item[1]["canonical_path"], item[1]["chunk_index"]))
        return [
            {
                "document_id": int(chunk["document_id"]),
                "canonical_path": str(chunk["canonical_path"]),
                "rel_path": str(chunk["rel_path"]),
                "sha256": str(chunk["sha256"]),
                "locator": f"chunk:{int(chunk['chunk_index'])}",
                "heading_path": str(chunk["heading_path"]),
                "inspection_status": "text_inspected",
                "selection_status": "lexical_relevance_candidate",
                "source_support": "source_support_unverified",
            }
            for _, chunk in candidates[:3]
        ]

    @staticmethod
    def _adaptation(profile_data: dict[str, Any], module: dict[str, Any]) -> dict[str, Any]:
        purpose = str(profile_data.get("learning_purpose", "understand"))
        medium = str(profile_data.get("medium", "written_or_spoken"))
        access = profile_data.get("access_conditions", {})
        prior = profile_data.get("prior_task_evidence", [])
        route = "worked_explanation" if prior else "first_encounter"
        if module.get("prerequisites"):
            route = "prerequisite_check"
        return {
            "schema_version": "1.0",
            "purpose": purpose,
            "medium": medium,
            "route": route,
            "access_adjustment": "phone_safe" if isinstance(access, dict) and access.get("phone_only") else "none",
            "reason": "Chosen from purpose, prior task evidence, prerequisites, medium, and access conditions.",
        }

    def generate(self, profile_path: Path) -> dict[str, Any]:
        profile_path = profile_path.expanduser().resolve()
        profile_data = self._load_profile(profile_path)
        self.ledger.append(
            "profile.load",
            {
                "profile_path": str(profile_path),
                "name": profile_data.get("name"),
                "organs_of_interest": profile_data.get("organs_of_interest", []),
            },
        )

        level = DifficultyLevel(profile_data.get("level", "beginner"))
        learner = LearnerProfile(
            name=str(profile_data["name"]),
            organs_of_interest=list(profile_data.get("organs_of_interest", [])),
            level=level,
            completed_modules=list(profile_data.get("completed_modules", [])),
        )

        generator = SyllabusGenerator(seed_dir=self.seed_dir)
        path = generator.generate(learner)

        snapshot = self.storage.latest_snapshot()
        if snapshot is None:
            raise ValueError("No corpus snapshot found. Run 'syllabus corpus ingest' first.")
        snapshot_id = int(snapshot["id"])

        evidence_sha256 = self.storage.list_snapshot_sha256(snapshot_id=snapshot_id)
        output_policy = profile_data.get("output_policy", {})
        selected_wings = output_policy.get("selected_wings", []) if isinstance(output_policy, dict) else []
        valid_wings = {wing["wing_id"] for wing in WINGS}
        if not isinstance(selected_wings, list) or any(wing not in valid_wings for wing in selected_wings):
            raise ValueError("output_policy.selected_wings must be a list of known wing IDs")

        module_rows: list[dict[str, Any]] = []
        for seq, m in enumerate(path.modules, start=1):
            module_rows.append(
                {
                    "seq": seq,
                    "module_id": m.module_id,
                    "title": m.title,
                    "organ": m.organ,
                    "difficulty": m.difficulty.value,
                    "estimated_hours": m.estimated_hours,
                    "readings": m.readings,
                    "questions": m.questions,
                    "prerequisites": m.prerequisites,
                    "artifact_descriptors": [wing for wing in WINGS if wing["wing_id"] in selected_wings],
                }
            )
        for row in module_rows:
            row["adaptation"] = self._adaptation(profile_data, row)
            row["source_selection"] = {
                "schema_version": SOURCE_SELECTION_SCHEMA_VERSION,
                "candidates": self._select_source_candidates(snapshot_id=snapshot_id, module=row),
                "claim_support_status": "source_support_unverified",
            }

        personalization_rules_hash = self._hash_personalization_rules(DEFAULT_PERSONALIZATION_RULES)
        plan_uid = self._deterministic_plan_uid(
            profile_data,
            module_rows,
            snapshot_id=snapshot_id,
            evidence_sha256=evidence_sha256,
            personalization_rules_hash=personalization_rules_hash,
        )

        profile_id = self.storage.insert_profile(
            name=learner.name,
            level=learner.level.value,
            goals=list(profile_data.get("goals", [])),
            context=dict(profile_data.get("context", {})),
        )
        db_plan_id = self.storage.insert_plan(
            profile_id=profile_id,
            title=f"Learning Plan {plan_uid}",
            total_hours=path.total_hours,
            module_count=len(module_rows),
            snapshot_id=snapshot_id,
        )

        for row in module_rows:
            self.storage.insert_plan_module(
                plan_id=db_plan_id,
                seq=int(row["seq"]),
                module_id=str(row["module_id"]),
                title=str(row["title"]),
                organ=str(row["organ"]),
                difficulty=str(row["difficulty"]),
                readings=list(row["readings"]),
                questions=list(row["questions"]),
                estimated_hours=float(row["estimated_hours"]),
            )

        plan = {
            "schema_version": "2.0",
            "plan_id": plan_uid,
            "db_plan_id": db_plan_id,
            "title": f"Learning Path: {', '.join(learner.organs_of_interest)}",
            "profile": {
                "name": learner.name,
                "organs_of_interest": learner.organs_of_interest,
                "level": learner.level.value,
                "goals": list(profile_data.get("goals", [])),
                "context": dict(profile_data.get("context", {})),
                "completed_modules": learner.completed_modules,
            },
            "snapshot": snapshot,
            "determinism_inputs": {
                "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "evidence_sha256": evidence_sha256,
                "personalization_rules_hash": personalization_rules_hash,
            },
            "personalization_rules": [
                {
                    "rule_id": r.rule_id,
                    "description": r.description,
                    "profile_fields": r.profile_fields,
                    "module_filters": r.module_filters,
                }
                for r in DEFAULT_PERSONALIZATION_RULES
            ],
            "output_policy": {
                "selected_wings": selected_wings,
                "publication_authorized": False,
                "encounter_only": not selected_wings,
            },
            "modules": module_rows,
            "totals": {
                "module_count": len(module_rows),
                "total_hours": path.total_hours,
            },
        }

        self.ledger.append(
            "plan.generate",
            {
                "plan_id": plan_uid,
                "db_plan_id": db_plan_id,
                "profile_id": profile_id,
                "snapshot_id": snapshot_id,
                "module_count": len(module_rows),
                "total_hours": path.total_hours,
                "evidence_hash_count": len(evidence_sha256),
            },
        )

        return plan

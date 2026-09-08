"""Deterministic instructional choices, without ability inference or source rewriting."""

from typing import Any


def adapt_encounter(profile: dict[str, Any], module: dict[str, Any]) -> tuple[dict, dict]:
    purpose = profile.get("learning_purpose", "understand")
    if purpose not in ("understand", "practice", "evaluate", "enjoy"):
        raise ValueError("learning_purpose must be understand, practice, evaluate, or enjoy")
    medium = profile.get("medium", "written_or_spoken")
    if medium not in ("written_or_spoken", "page", "audio", "practice", "mixed"):
        raise ValueError("Unsupported medium")
    access = profile.get("access_conditions", {})
    prior = profile.get("prior_task_evidence", [])
    if not isinstance(access, dict) or not isinstance(prior, list):
        raise ValueError("access_conditions must be an object; prior_task_evidence must be a list")  # noqa: TRY004
    relevant = [
        e
        for e in prior
        if isinstance(e, dict)
        and e.get("module_id") == module["module_id"]
        and e.get("criterion") == "explain_with_counterexample"
        and isinstance(e.get("response_locator"), str)
        and e["response_locator"]
        and e.get("result") in ("demonstrated", "needs_work")
    ]
    # Conflicting observations require inspection, never optimistic score aggregation.
    results = {e["result"] for e in relevant}
    route = "first_encounter"
    if results == {"demonstrated"}:
        route = "transfer_attempt"
    elif results == {"needs_work"}:
        route = "worked_explanation"
    elif len(results) > 1:
        route = "evidence_review"
    if module.get("prerequisites"):
        route = "prerequisite_check"
    title = module["title"]
    tasks = {
        "understand": f"Choose one idea in {title}. Identify what it says and one case it does not explain.",
        "practice": f"Choose one procedure in {title}. Try one step and inspect its result before continuing.",
        "evaluate": f"Choose one claim about {title}. Separate its reasons from its conclusion; seek a counterexample.",
        "enjoy": f"Spend a moment with {title}. Notice a detail that interests you; no response is required.",
    }
    steps = [tasks[purpose]]
    if route == "transfer_attempt":
        steps.append(
            "Try the idea in a different case. Treat the transfer as untested until observed."
        )
    elif route == "worked_explanation":
        steps.append(
            "Use a worked example first: identify the claim, its reason, and a case where that reason is insufficient. You may request an explanation without assessment."
        )
    elif route == "prerequisite_check":
        steps.append(
            "Inspect these prerequisites before proceeding: "
            + ", ".join(module["prerequisites"])
            + ". Missing access is not a learning deficit."
        )
    elif route == "evidence_review":
        steps.append(
            "Prior task observations disagree. Inspect those observations before choosing a harder task."
        )
    phone = access.get("phone_only") is True
    if phone:
        steps.append(
            "Phone route: focus on one short passage or example. Respond in a note or a voice memo under two minutes, or just read the explanation."
        )
    if access.get("source_available") is False:
        steps.append(
            "The source is unavailable. Use the self-contained example below; source-specific conclusions remain unverified."
        )
    if medium == "audio":
        steps.append(
            "Listen for exposition. Pause and inspect a page if notation, a diagram, code, or literary form matters."
        )
    elif medium in ("page", "mixed"):
        steps.append(
            "Keep the passage or notation visible and inspect the exact wording before evaluating it."
        )
    elif medium == "practice":
        steps.append("Perform one small reversible step, observe it, then revise your procedure.")
    decision = {
        "schema_version": "1.1",
        "purpose": purpose,
        "medium": medium,
        "route": route,
        "access_adjustment": "phone_safe" if phone else "none",
        "reason": f"Purpose={purpose}; relevant task observations={len(relevant)}; route={route}.",
        "assessment_status": "unassessed",
    }
    encounter = {
        "authorship": "assistant_instruction",
        "status": "prepared_not_started",
        "steps": steps,
        "self_contained_example": "Recorded complaints fell from 80 to 52: 28 fewer, or 35%. This describes a decline; it does not establish what caused it.",
        "example_role": "assistant_explanation_not_source_argument",
        "source_argument": None,
        "analogy": None,
        "critique": None,
        "learner_words": None,
        "response_routes": [
            "short_written",
            "spoken_under_two_minutes",
            "worked_explanation",
            "no_response_now",
        ],
        "completion": "Understanding, enjoyment, a better question, or a useful action may end the encounter.",
    }
    return decision, encounter

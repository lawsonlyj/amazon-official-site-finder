from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from finder.text import domain_from_url
from tools.llm_agent_client import AgentClientError, AgentConfigurationError, OpenAIJsonClient
from tools.output_layout import WORKFLOW_VERSION, check_agent_paths, first_existing


CHECK_AGENT_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "candidate_url",
    "candidate_domain",
    "check_agent_decision",
    "manual_decision",
    "manual_url",
    "confidence",
    "evidence_urls",
    "supporting_facts",
    "counter_evidence",
    "reason_for_unsure",
    "suggestions",
    "source_agent_b_decision",
    "source_evidence_score",
    "source_review_reason",
    "source_status",
    "source_confidence",
]

DECISIONS = {"accept", "reject", "replace", "unsure"}

SYSTEM_PROMPT = """You are CheckAgent for a development-only official website workflow.
Review only the structured evidence provided. Do not browse, do not invent facts, and do not change production rules.
Decide whether the candidate is the independent official website for the Amazon provider.
Return a single JSON object with: decision, confidence, supporting_facts, counter_evidence, reason_for_unsure, manual_url, evidence_urls, suggestions.
Allowed decisions are accept, reject, replace, unsure. Use replace only when a clearly better replacement URL is present in the input evidence.
Use unsure when evidence is incomplete, ambiguous, same-name risk exists, or replacement needs human confirmation."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run development-only LLM CheckAgent over high-risk workflow rows.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--input-csv", help="Defaults to check_suggestion/check.csv, agent_b/check.csv, or agent_b_verification_results.csv.")
    parser.add_argument("--output-csv")
    parser.add_argument("--output-jsonl")
    parser.add_argument("--summary-json")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-all", action="store_true", help="Send all input rows to CheckAgent instead of high-risk rows only.")
    parser.add_argument("--model", help="Override FINDER_CHECK_AGENT_MODEL/FINDER_DEV_AGENT_MODEL.")
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    if args.model:
        import os

        os.environ["FINDER_CHECK_AGENT_MODEL"] = args.model
    summary = run_check_agent(
        run_dir=args.run_dir,
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        output_jsonl=args.output_jsonl,
        summary_json=args.summary_json,
        limit=args.limit or None,
        include_all=args.include_all,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "completed" else 2


def run_check_agent(
    *,
    run_dir: str | Path,
    input_csv: str | Path | None = None,
    output_csv: str | Path | None = None,
    output_jsonl: str | Path | None = None,
    summary_json: str | Path | None = None,
    limit: int | None = None,
    include_all: bool = False,
    client: Any | None = None,
) -> dict:
    run_dir = Path(run_dir)
    canonical = check_agent_paths(run_dir)
    output_csv_path = Path(output_csv) if output_csv else canonical["csv"]
    output_jsonl_path = Path(output_jsonl) if output_jsonl else canonical["jsonl"]
    summary_path = Path(summary_json) if summary_json else canonical["summary"]
    source_path = Path(input_csv) if input_csv else (
        first_existing(run_dir, "check_suggestion/check.csv", "agent_b/check.csv", "agent_b_verification_results.csv")
        or run_dir / "check_suggestion/check.csv"
    )
    rows = _read_rows(source_path)
    rows = [row for row in rows if include_all or _is_high_risk(row)]
    if limit:
        rows = rows[:limit]

    try:
        active_client = client or OpenAIJsonClient.from_env(model_env="FINDER_CHECK_AGENT_MODEL")
    except AgentConfigurationError as exc:
        summary = _blocked_summary(
            run_dir=run_dir,
            source_path=source_path,
            summary_path=summary_path,
            reason="missing_openai_api_key",
            detail=str(exc),
            input_rows=len(rows),
        )
        return summary

    output_rows: list[dict[str, str]] = []
    json_rows: list[dict[str, Any]] = []
    try:
        for index, row in enumerate(rows, 1):
            print(f"check-agent {index}/{len(rows)} {row.get('provider_name', '')}", file=sys.stderr)
            result = _review_row(row, active_client)
            output_rows.append(result["row"])
            json_rows.append(result["details"])
            _write_rows(output_csv_path, output_rows, CHECK_AGENT_FIELDS)
            _write_jsonl(output_jsonl_path, json_rows)
    except AgentClientError as exc:
        summary = _blocked_summary(
            run_dir=run_dir,
            source_path=source_path,
            summary_path=summary_path,
            reason="openai_api_error",
            detail=str(exc),
            input_rows=len(rows),
            completed_rows=len(output_rows),
        )
        return summary

    _write_rows(output_csv_path, output_rows, CHECK_AGENT_FIELDS)
    _write_jsonl(output_jsonl_path, json_rows)
    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "status": "completed",
        "input_csv": str(source_path),
        "input_rows": len(rows),
        "output_rows": len(output_rows),
        "decision_counts": dict(Counter(row["check_agent_decision"] for row in output_rows)),
        "outputs": {
            "csv": str(output_csv_path),
            "jsonl": str(output_jsonl_path),
            "summary": str(summary_path),
        },
    }
    _write_summary(summary_path, summary)
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def _review_row(row: dict[str, str], client: Any) -> dict[str, Any]:
    payload = _payload_for_row(row)
    raw = client.complete_json(system_prompt=SYSTEM_PROMPT, user_payload=payload)
    parsed = _normalize_agent_output(raw, row)
    out_row = {
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "candidate_url": row.get("candidate_url") or row.get("official_url", ""),
        "candidate_domain": domain_from_url(row.get("candidate_domain") or row.get("candidate_url") or row.get("official_url", "")),
        "check_agent_decision": parsed["decision"],
        "manual_decision": parsed["decision"],
        "manual_url": parsed["manual_url"],
        "confidence": str(parsed["confidence"]),
        "evidence_urls": _join(parsed["evidence_urls"]),
        "supporting_facts": _join(parsed["supporting_facts"]),
        "counter_evidence": _join(parsed["counter_evidence"]),
        "reason_for_unsure": parsed["reason_for_unsure"],
        "suggestions": _join(parsed["suggestions"]),
        "source_agent_b_decision": row.get("agent_b_decision", ""),
        "source_evidence_score": row.get("evidence_score", ""),
        "source_review_reason": row.get("review_reason", ""),
        "source_status": row.get("source_status") or row.get("status", ""),
        "source_confidence": row.get("source_confidence") or row.get("confidence", ""),
    }
    return {
        "row": out_row,
        "details": {
            "provider_id": out_row["provider_id"],
            "provider_name": out_row["provider_name"],
            "payload": payload,
            "raw_agent_output": raw,
            "normalized_agent_output": parsed,
        },
    }


def _payload_for_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "provider": {
            "provider_id": row.get("provider_id", ""),
            "provider_name": row.get("provider_name", ""),
            "provider_detail_url": row.get("provider_detail_url", ""),
            "locations": row.get("provider_locations", ""),
            "services": row.get("service_apis", "") or row.get("service_types", ""),
        },
        "candidate": {
            "url": row.get("candidate_url") or row.get("official_url", ""),
            "domain": row.get("candidate_domain", ""),
            "source_decision": row.get("agent_b_decision", ""),
            "source_confidence": row.get("source_confidence") or row.get("confidence", ""),
            "evidence_score": row.get("evidence_score", ""),
            "evidence_urls": row.get("evidence_urls", ""),
            "supporting_facts": row.get("supporting_facts", ""),
            "counter_evidence": row.get("counter_evidence", ""),
            "reason_for_unsure": row.get("reason_for_unsure", ""),
            "replacement_url": row.get("replacement_url", ""),
            "replacement_domain": row.get("replacement_domain", ""),
            "review_reason": row.get("review_reason", ""),
            "source_status": row.get("source_status") or row.get("status", ""),
        },
        "decision_rules": [
            "accept only when the candidate is confirmed as an independent official website",
            "replace only when replacement_url is clearly more credible than the candidate",
            "reject when the candidate is clearly not an official site and no credible replacement is present",
            "unsure when identity, country, service, same-name, or evidence strength is ambiguous",
            "do not suggest production rule changes from a single case",
        ],
    }


def _normalize_agent_output(raw: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    decision = str(raw.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        decision = "unsure"
    manual_url = str(raw.get("manual_url") or "").strip()
    replacement_url = row.get("replacement_url", "").strip()
    candidate_url = (row.get("candidate_url") or row.get("official_url", "")).strip()
    reason = str(raw.get("reason_for_unsure") or "").strip()
    if decision == "replace":
        manual_url = manual_url or replacement_url
        if not manual_url:
            decision = "unsure"
            reason = reason or "replace_without_manual_url"
    if decision == "accept" and not candidate_url:
        decision = "unsure"
        reason = reason or "accept_without_candidate_url"
    if decision == "unsure" and not reason:
        reason = "agent_requested_unsure"
    return {
        "decision": decision,
        "manual_url": manual_url if decision == "replace" else "",
        "confidence": _clamp_int(raw.get("confidence"), default=50),
        "supporting_facts": _listify(raw.get("supporting_facts")),
        "counter_evidence": _listify(raw.get("counter_evidence")),
        "evidence_urls": _listify(raw.get("evidence_urls")),
        "reason_for_unsure": reason,
        "suggestions": _listify(raw.get("suggestions")),
    }


def _is_high_risk(row: dict[str, str]) -> bool:
    risk_text = " ".join(
        row.get(key, "")
        for key in (
            "review_reason",
            "source_status",
            "status",
            "counter_evidence",
            "reason_for_unsure",
            "notes",
        )
    ).casefold()
    supporting_text = row.get("supporting_facts", "").casefold()
    if row.get("review_reason", "").strip():
        return True
    if any(marker in risk_text for marker in ["manual_accepted", "needs_review", "unresolved", "same", "generic", "identity", "slug", "platform", "directory", "parked"]):
        return True
    if "logo" in supporting_text and "page_contains_exact_provider_name" not in supporting_text:
        return True
    return _to_int(row.get("source_confidence") or row.get("confidence")) < 83 or _to_int(row.get("evidence_score")) < 75


def _blocked_summary(
    *,
    run_dir: Path,
    source_path: Path,
    summary_path: Path,
    reason: str,
    detail: str,
    input_rows: int,
    completed_rows: int = 0,
) -> dict:
    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "status": "blocked",
        "reason": reason,
        "detail": detail,
        "input_csv": str(source_path),
        "input_rows": input_rows,
        "completed_rows": completed_rows,
        "outputs": {"summary": str(summary_path)},
    }
    _write_summary(summary_path, summary)
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_manifest(path: Path, summary: dict[str, Any]) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["development_check_agent"] = summary
    manifest.setdefault("outputs", {}).update(summary.get("outputs", {}))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(";") if item.strip()]


def _join(values: list[str]) -> str:
    return "; ".join(value for value in values if value)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _clamp_int(value: object, *, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


if __name__ == "__main__":
    raise SystemExit(main())

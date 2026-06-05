from __future__ import annotations

"""Build a visual verification task for uncertain second-pass rows.

This is the deterministic half of the codex-assisted visual verification step. It does
NOT make any judgement. It selects the rows where the rule-based second pass is least
reliable, renders a screenshot of each candidate official site, fetches the Amazon listing
logo, lays them out into review grids, and writes a review-task workbook for the agent to
fill (manual_decision / manual_url / notes) by looking at the screenshots.

The agent's filled verdicts are applied by tools/apply_visual_verification.py, which
overwrites the canonical second-pass outputs (official_sites.csv etc.).

Rendering uses Playwright/chromium when available and degrades gracefully (URL-only task)
when it is not, so the module never hard-fails on machines without a browser installed.
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.scoring import is_excluded_domain, load_config
from finder.text import domain_from_url
from tools.build_linked_workbook import build_workbook
from tools.build_manual_review_task import _ambiguous_provider_name, _has_generic_identity_term
from tools.output_layout import first_existing


TASK_FIELDS = [
    "review_reason",
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "official_url",
    "candidate_1_url",
    "official_domain",
    "status",
    "confidence",
    "evidence_summary",
    "listing_logo_url",
    "screenshot",
    "grid",
    "manual_decision",
    "manual_url",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a visual verification task for uncertain second-pass rows.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--matched-review-below", type=int, default=85, help="Flag accepted rows with confidence below this.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-render", action="store_true", help="Skip screenshots; produce a URL-only task.")
    parser.add_argument("--render-timeout-ms", type=int, default=20000)
    parser.add_argument("--write-xlsx", action="store_true", default=True)
    args = parser.parse_args(argv)

    summary = build_visual_verification_task(
        run_dir=args.run_dir,
        config_path=args.config,
        matched_review_below=args.matched_review_below,
        limit=args.limit or None,
        render=not args.no_render,
        render_timeout_ms=args.render_timeout_ms,
        write_xlsx=args.write_xlsx,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_visual_verification_task(
    *,
    run_dir: str | Path,
    config_path: str | Path = "config/scoring.json",
    matched_review_below: int = 85,
    limit: int | None = None,
    render: bool = True,
    render_timeout_ms: int = 20000,
    write_xlsx: bool = True,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    config = load_config(config_path)
    out_dir = run_dir / "visual_verification"
    shots_dir = out_dir / "screenshots"
    grids_dir = out_dir / "grids"

    final_path = first_existing(run_dir, "official_sites.csv", "provider_final_official_websites_second_pass.csv")
    if not final_path:
        raise FileNotFoundError(f"second-pass final CSV not found in {run_dir}")
    final_rows = _read_rows(final_path)
    second_pass_rows = _index_rows(
        first_existing(run_dir, "details/second_pass/results.csv", "unresolved_second_pass_results.csv")
        or run_dir / "details/second_pass/results.csv"
    )

    selected = []
    for row in final_rows:
        sp = second_pass_rows.get(_row_key(row), {})
        reason = _visual_review_reason(row, sp, config, matched_review_below=matched_review_below)
        if reason:
            selected.append((row, sp, reason))
    if limit:
        selected = selected[:limit]

    rendered = 0
    render_errors = 0
    renderer = _Renderer(render_timeout_ms) if render and selected else None
    task_rows: list[dict[str, str]] = []
    for index, (row, sp, reason) in enumerate(selected, 1):
        candidate_url = _candidate_url(row, sp)
        screenshot_path = ""
        if renderer and candidate_url:
            shot = shots_dir / f"{index:03d}_{_safe(row.get('provider_id') or row.get('provider_name'))}.png"
            ok = renderer.capture(candidate_url, shot)
            if ok:
                screenshot_path = str(shot)
                rendered += 1
            else:
                render_errors += 1
        task_rows.append(
            _task_row(row, sp, reason, candidate_url=candidate_url, screenshot_path=screenshot_path)
        )
    if renderer:
        renderer.close()

    grid_paths = _build_grids(task_rows, grids_dir) if rendered else []
    for grid_index, group in _grouped(task_rows, 5):
        grid_ref = grid_paths[grid_index] if grid_index < len(grid_paths) else ""
        for task_row in group:
            task_row["grid"] = grid_ref

    out_dir.mkdir(parents=True, exist_ok=True)
    task_csv = out_dir / "visual_verification_task.csv"
    _write_rows(task_csv, task_rows, TASK_FIELDS)
    task_xlsx = ""
    if write_xlsx:
        task_xlsx = str(out_dir / "visual_verification_task.xlsx")
        build_workbook([("Visual_Verification", task_csv)], task_xlsx)

    summary = {
        "run_dir": str(run_dir),
        "task_rows": len(task_rows),
        "render_enabled": bool(render),
        "rendered_screenshots": rendered,
        "render_errors": render_errors,
        "renderer_available": renderer.available if renderer else False,
        "reason_counts": _reason_counts(task_rows),
        "output_csv": str(task_csv),
        "output_xlsx": task_xlsx,
        "grids": grid_paths,
        "screenshots_dir": str(shots_dir),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _visual_review_reason(row: dict[str, str], sp: dict[str, str], config: dict, *, matched_review_below: int) -> str:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    official_url = row.get("official_url", "")
    evidence = (row.get("evidence_summary") or sp.get("evidence_summary") or "").casefold()
    name = row.get("provider_name", "")

    if official_url and status in {"matched", "manual_accepted"}:
        # precision lane: confirm the accepted official site really is this provider
        if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
            return "precision_identity_constraint"
        if _ambiguous_provider_name(name) or _has_generic_identity_term(name):
            return "precision_same_name_risk"
        if confidence < matched_review_below:
            return "precision_low_confidence_accept"
        return ""

    # recall lane: an unresolved/rejected row that still has a usable candidate to look at
    candidate = _candidate_url(row, sp)
    if candidate and not is_excluded_domain(candidate, config):
        return "recall_unresolved_candidate"
    return ""


def _candidate_url(row: dict[str, str], sp: dict[str, str]) -> str:
    for value in [
        row.get("official_url", ""),
        sp.get("official_url", ""),
        sp.get("previous_top_candidate_url", ""),
    ]:
        if value:
            return value
    return ""


def _task_row(
    row: dict[str, str],
    sp: dict[str, str],
    reason: str,
    *,
    candidate_url: str,
    screenshot_path: str,
) -> dict[str, str]:
    return {
        "review_reason": reason,
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "official_url": candidate_url,
        "candidate_1_url": candidate_url,
        "official_domain": domain_from_url(row.get("official_domain") or candidate_url),
        "status": row.get("status", ""),
        "confidence": row.get("confidence", ""),
        "evidence_summary": row.get("evidence_summary", "") or sp.get("evidence_summary", ""),
        "listing_logo_url": row.get("listing_logo_url", ""),
        "screenshot": screenshot_path,
        "grid": "",
        "manual_decision": "",
        "manual_url": "",
        "notes": "",
    }


class _Renderer:
    """Lazy Playwright wrapper. Reports available=False instead of raising when chromium is missing."""

    def __init__(self, timeout_ms: int):
        self.timeout_ms = timeout_ms
        self.available = False
        self._pw = None
        self._browser = None
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self.available = True
        except Exception as exc:  # missing package or browser binary
            print(f"visual verification: rendering unavailable ({type(exc).__name__}); writing URL-only task.", file=sys.stderr)
            self.close()

    def capture(self, url: str, out_path: Path) -> bool:
        if not self.available or not self._browser:
            return False
        if "://" not in url:
            url = "https://" + url
        page = None
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page = self._browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 800},
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            _dismiss_cookies(page)
            page.wait_for_timeout(700)
            page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": 1366, "height": 760})
            return out_path.exists()
        except Exception as exc:
            print(f"visual verification: render failed for {url}: {type(exc).__name__}", file=sys.stderr)
            return False
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None


def _dismiss_cookies(page) -> None:
    for sel in [
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        'button:has-text("Got it")',
        'button:has-text("Allow all")',
        'button:has-text("OK")',
        'button:has-text("Akzeptieren")',
        'button:has-text("Accepter")',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=1500)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def _build_grids(task_rows: list[dict[str, str]], grids_dir: Path) -> list[str]:
    try:
        import io
        import urllib.request
        from PIL import Image, ImageDraw
    except Exception:
        return []

    def fetch(url: str) -> bytes:
        if not url:
            return b""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read(700000)
        except Exception:
            return b""

    def load(data: bytes):
        try:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            return Image.alpha_composite(bg, im).convert("RGB")
        except Exception:
            return None

    grids_dir.mkdir(parents=True, exist_ok=True)
    grid_paths: list[str] = []
    row_h = 200
    width = 1180
    for grid_index, group in _grouped([r for r in task_rows if r.get("screenshot")], 5):
        grid = Image.new("RGB", (width, row_h * len(group)), (255, 255, 255))
        draw = ImageDraw.Draw(grid)
        for j, task_row in enumerate(group):
            y = j * row_h
            draw.text((10, y + 4), f"{task_row.get('provider_name', '')[:36]}  conf={task_row.get('confidence', '')}", fill=(0, 0, 0))
            draw.text((10, y + 20), f"[{task_row.get('review_reason', '')}]", fill=(120, 0, 0))
            amz = load(fetch(task_row.get("listing_logo_url", "")))
            if amz:
                amz = amz.copy()
                amz.thumbnail((240, 140))
                grid.paste(amz, (16, y + 44))
            try:
                shot = Image.open(task_row["screenshot"]).convert("RGB")
                shot.thumbnail((880, row_h - 16))
                grid.paste(shot, (290, y + 8))
            except Exception:
                draw.text((290, y + 80), "(screenshot unreadable)", fill=(170, 0, 0))
        out = grids_dir / f"grid_{grid_index + 1:02d}.png"
        grid.save(out)
        grid_paths.append(str(out))
    return grid_paths


def _grouped(rows: list[dict[str, str]], size: int):
    for index, start in enumerate(range(0, len(rows), size)):
        yield index, rows[start : start + size]


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: str | Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _index_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return {_row_key(row): row for row in _read_rows(path) if _row_key(row)}


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    return f"name:{(row.get('provider_name') or '').strip().casefold()}"


def _reason_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("review_reason", "")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _safe(value: object) -> str:
    text = "".join(ch if ch.isalnum() else "_" for ch in str(value or "row"))
    return text[:40] or "row"


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

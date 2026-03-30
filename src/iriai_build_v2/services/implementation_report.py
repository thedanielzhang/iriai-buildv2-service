"""Render an implementation report as a self-contained HTML document.

The output is a complete ``<!DOCTYPE html>`` page with embedded CSS.
It serves as the audit trail for the implementation phase: what was
expected, what was built, what it looks like, with full evidence.

All user-provided text is HTML-escaped to prevent XSS.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from ..models.outputs import (
    BugFixAttempt,
    HandoverDoc,
    ImplementationResult,
    Verdict,
)

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    """Escape text for safe HTML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _embed_image(path: str) -> str:
    """Read an image file and return a base64 data URI, or empty string on failure."""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        data = p.read_bytes()
        suffix = p.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            suffix.lstrip("."), "image/png"
        )
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        logger.warning("Failed to embed image: %s", path)
        return ""


def _verdict_badge(verdict: Verdict) -> str:
    """Return an HTML badge for a verdict."""
    if verdict.approved:
        return '<span class="badge pass">PASS</span>'
    return '<span class="badge fail">FAIL</span>'


def _render_concerns(verdict: Verdict) -> str:
    """Render verdict concerns as an HTML table."""
    if not verdict.concerns:
        return "<p>No concerns reported.</p>"
    rows = []
    for c in verdict.concerns:
        file_ref = f"<code>{_esc(c.file)}</code>" if c.file else ""
        rows.append(
            f"<tr><td><span class='severity {_esc(c.severity)}'>{_esc(c.severity)}</span></td>"
            f"<td>{_esc(c.description)}</td><td>{file_ref}</td></tr>"
        )
    return (
        '<table class="concerns"><thead><tr><th>Severity</th><th>Description</th><th>File</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_gaps(verdict: Verdict) -> str:
    """Render verdict gaps as an HTML table."""
    if not verdict.gaps:
        return ""
    rows = []
    for g in verdict.gaps:
        rows.append(
            f"<tr><td><span class='severity {_esc(g.severity)}'>{_esc(g.severity)}</span></td>"
            f"<td>{_esc(g.category)}</td><td>{_esc(g.description)}</td></tr>"
        )
    return (
        '<h4>Gaps</h4><table class="concerns"><thead><tr><th>Severity</th><th>Category</th>'
        f"<th>Description</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


# ── Main renderer ────────────────────────────────────────────────────────────


def render_implementation_report(
    feature_name: str,
    handover: HandoverDoc,
    verdicts: dict[str, Verdict],
    bug_fix_attempts: list[BugFixAttempt],
    test_result: ImplementationResult | None,
    artifact_urls: dict[str, str],
    screenshot_paths: list[str],
) -> str:
    """Render a self-contained HTML implementation report."""

    sections: list[str] = []

    # ── 1. Scope & Requirements ──────────────────────────────────────
    scope_section = '<section id="scope"><h2>Scope &amp; Requirements</h2>'
    if artifact_urls.get("prd"):
        scope_section += f'<p><a href="{_esc(artifact_urls["prd"])}">View Full PRD</a></p>'
    if artifact_urls.get("design"):
        scope_section += f'<p><a href="{_esc(artifact_urls["design"])}">View Design Decisions</a></p>'
    if artifact_urls.get("plan"):
        scope_section += f'<p><a href="{_esc(artifact_urls["plan"])}">View Technical Plan</a></p>'
    if artifact_urls.get("system-design"):
        scope_section += f'<p><a href="{_esc(artifact_urls["system-design"])}">View System Design</a></p>'
    scope_section += "</section>"
    sections.append(scope_section)

    # ── 2. What Was Built ────────────────────────────────────────────
    built_section = '<section id="built"><h2>What Was Built</h2>'
    if handover.summary_of_prior_work:
        built_section += f"<p>{_esc(handover.summary_of_prior_work)}</p>"

    if handover.completed:
        built_section += '<h3>Completed Tasks</h3><table class="tasks">'
        built_section += "<thead><tr><th>Task</th><th>Summary</th><th>Files</th></tr></thead><tbody>"
        for t in handover.completed:
            files = ", ".join(f"<code>{_esc(f)}</code>" for f in t.files_changed[:5])
            if len(t.files_changed) > 5:
                files += f" (+{len(t.files_changed) - 5} more)"
            built_section += (
                f"<tr><td>{_esc(t.task_id)}</td>"
                f"<td>{_esc(t.summary[:150])}</td>"
                f"<td>{files}</td></tr>"
            )
        built_section += "</tbody></table>"

    if handover.all_files_changed:
        unique_files = sorted(set(handover.all_files_changed))
        built_section += f"<h3>All Files Changed ({len(unique_files)})</h3><ul>"
        for f in unique_files:
            built_section += f"<li><code>{_esc(f)}</code></li>"
        built_section += "</ul>"

    if handover.key_decisions:
        built_section += "<h3>Key Decisions</h3><ul>"
        for d in handover.key_decisions:
            built_section += f"<li>{_esc(d)}</li>"
        built_section += "</ul>"

    built_section += "</section>"
    sections.append(built_section)

    # ── 3. Journey Evidence ──────────────────────────────────────────
    evidence_section = '<section id="evidence"><h2>Journey Evidence</h2>'
    if screenshot_paths:
        evidence_section += f"<p>{len(screenshot_paths)} screenshot(s) captured during verification.</p>"
        for i, path in enumerate(screenshot_paths):
            data_uri = _embed_image(path)
            name = Path(path).stem
            if data_uri:
                evidence_section += (
                    f'<figure><img src="{data_uri}" alt="{_esc(name)}" '
                    f'style="max-width:100%;border:1px solid #ddd;border-radius:4px;" />'
                    f"<figcaption>{_esc(name)}</figcaption></figure>"
                )
            else:
                evidence_section += f"<p>Screenshot not found: <code>{_esc(path)}</code></p>"
    else:
        evidence_section += (
            '<p style="color:#dc2626;font-weight:bold;">'
            "⚠ NO SCREENSHOT EVIDENCE — The verifier did not capture any "
            "Playwright screenshots. This report cannot be approved without "
            "visual evidence of user journeys working end-to-end.</p>"
        )
    evidence_section += "</section>"
    sections.append(evidence_section)

    # ── 4. Quality Gate Results ──────────────────────────────────────
    gates_section = '<section id="gates"><h2>Quality Gate Results</h2>'
    gate_labels = {
        "qa": "QA (Bug Hunting)",
        "integration": "Integration Test",
        "code_review": "Code Review",
        "security": "Security Audit",
        "verifier": "Verifier (Journey Confirmation)",
    }
    for key, verdict in verdicts.items():
        label = gate_labels.get(key, key)
        gates_section += (
            f"<h3>{_esc(label)} {_verdict_badge(verdict)}</h3>"
            f"<p>{_esc(verdict.summary)}</p>"
            f"{_render_concerns(verdict)}"
            f"{_render_gaps(verdict)}"
        )
    gates_section += "</section>"
    sections.append(gates_section)

    # ── 5. Bug Fix History ───────────────────────────────────────────
    bugfix_section = '<section id="bugfixes"><h2>Bug Fix History</h2>'
    if bug_fix_attempts:
        fixed = [a for a in bug_fix_attempts if a.re_verify_result == "PASS"]
        failed = [a for a in bug_fix_attempts if a.re_verify_result != "PASS"]
        bugfix_section += (
            f"<p><strong>{len(fixed)}</strong> fixed, "
            f"<strong>{len(failed)}</strong> unresolved out of "
            f"<strong>{len(bug_fix_attempts)}</strong> total attempts.</p>"
        )
        bugfix_section += '<table class="bugfixes"><thead><tr>'
        bugfix_section += "<th>Bug ID</th><th>Source</th><th>Description</th>"
        bugfix_section += "<th>Root Cause</th><th>Fix</th><th>Result</th></tr></thead><tbody>"
        for a in bug_fix_attempts:
            result_class = "pass" if a.re_verify_result == "PASS" else "fail"
            bugfix_section += (
                f"<tr><td><code>{_esc(a.bug_id)}</code></td>"
                f"<td>{_esc(a.source_verdict)}</td>"
                f"<td>{_esc(a.description[:100])}</td>"
                f"<td>{_esc(a.root_cause[:100])}</td>"
                f"<td>{_esc(a.fix_applied[:100])}</td>"
                f"<td><span class='badge {result_class}'>{_esc(a.re_verify_result)}</span></td></tr>"
            )
        bugfix_section += "</tbody></table>"
    else:
        bugfix_section += "<p>No bug fix cycles were needed.</p>"
    bugfix_section += "</section>"
    sections.append(bugfix_section)

    # ── 6. Test Coverage ─────────────────────────────────────────────
    test_section = '<section id="tests"><h2>Test Coverage</h2>'
    if test_result:
        test_section += f"<p>{_esc(test_result.summary)}</p>"
        if test_result.files_created:
            test_section += "<h3>Test Files Created</h3><ul>"
            for f in test_result.files_created:
                test_section += f"<li><code>{_esc(f)}</code></li>"
            test_section += "</ul>"
        if test_result.files_modified:
            test_section += "<h3>Test Files Modified</h3><ul>"
            for f in test_result.files_modified:
                test_section += f"<li><code>{_esc(f)}</code></li>"
            test_section += "</ul>"
    else:
        test_section += "<p><em>No test authoring results available.</em></p>"
    test_section += "</section>"
    sections.append(test_section)

    # ── 7. Artifact References ───────────────────────────────────────
    refs_section = '<section id="refs"><h2>Artifact References</h2><ul>'
    ref_labels = {
        "prd": "Product Requirements Document",
        "design": "Design Decisions",
        "plan": "Technical Plan",
        "system-design": "System Design",
        "mockup": "UI Mockup",
    }
    for key, url in artifact_urls.items():
        label = ref_labels.get(key, key)
        refs_section += f'<li><a href="{_esc(url)}">{_esc(label)}</a></li>'
    if not artifact_urls:
        refs_section += "<li><em>No hosted artifact URLs available.</em></li>"
    refs_section += "</ul></section>"
    sections.append(refs_section)

    # ── Assemble full HTML ───────────────────────────────────────────
    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Implementation Report — {_esc(feature_name)}</title>
<style>
:root {{ --pass: #16a34a; --fail: #dc2626; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
h2 {{ font-size: 1.4rem; margin: 2rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid var(--border); }}
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; }}
h4 {{ font-size: 1rem; margin: 1rem 0 0.5rem; }}
section {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.9rem; }}
th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--bg); font-weight: 600; }}
code {{ background: #f1f5f9; padding: 0.15rem 0.4rem; border-radius: 3px; font-size: 0.85rem; }}
ul {{ padding-left: 1.5rem; }}
li {{ margin: 0.25rem 0; }}
a {{ color: #2563eb; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
figure {{ margin: 1rem 0; }}
figcaption {{ font-size: 0.85rem; color: #64748b; margin-top: 0.25rem; }}
.badge {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 4px; font-size: 0.8rem; font-weight: 600; color: white; }}
.badge.pass {{ background: var(--pass); }}
.badge.fail {{ background: var(--fail); }}
.severity {{ display: inline-block; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.75rem; font-weight: 600; }}
.severity.blocker {{ background: #fef2f2; color: #dc2626; }}
.severity.major {{ background: #fff7ed; color: #ea580c; }}
.severity.minor {{ background: #fefce8; color: #ca8a04; }}
.severity.nit {{ background: #f0fdf4; color: #16a34a; }}
.header {{ margin-bottom: 2rem; }}
.header .meta {{ color: #64748b; font-size: 0.9rem; }}
</style>
</head>
<body>
<div class="header">
<h1>Implementation Report</h1>
<p class="meta">{_esc(feature_name)}</p>
</div>
{body}
</body>
</html>"""


# ── Validation ───────────────────────────────────────────────────────────────


def validate_report(
    html: str,
    handover: HandoverDoc,
    verdicts: dict[str, Verdict],
) -> list[str]:
    """Validate the implementation report has all required sections.

    Returns a list of error descriptions. Empty list means valid.
    """
    errors: list[str] = []

    required_sections = [
        ("Scope &amp; Requirements", "scope"),
        ("What Was Built", "built"),
        ("Journey Evidence", "evidence"),
        ("Quality Gate Results", "gates"),
        ("Bug Fix History", "bugfixes"),
        ("Test Coverage", "tests"),
        ("Artifact References", "refs"),
    ]
    for section_text, section_id in required_sections:
        if section_text not in html:
            errors.append(f"Missing section: {section_text}")

    for gate_name in verdicts:
        if gate_name.replace("_", " ") not in html.lower() and gate_name not in html.lower():
            errors.append(f"Missing gate result: {gate_name}")

    for outcome in handover.completed:
        if outcome.task_id and outcome.task_id not in html:
            errors.append(f"Missing task reference: {outcome.task_id}")

    # Screenshot evidence is mandatory for projects with a frontend/UI.
    # Pure backend/library projects get a pass.
    has_frontend = any(
        kw in html.lower()
        for kw in ("frontend", "react", "composer", "editor", "canvas", "ui ")
    )
    if has_frontend and ("<img" not in html or "screenshot" not in html.lower()):
        errors.append(
            "Missing screenshot evidence: this project has frontend components but "
            "no Playwright screenshots were found in the Journey Evidence section. "
            "The verifier must capture screenshots for every UI journey step."
        )

    return errors

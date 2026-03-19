"""Render a SystemDesign model as a self-contained interactive HTML document.

The output is a complete ``<!DOCTYPE html>`` page with embedded CSS and
JavaScript.  It is designed to be served directly by the artifact portal or
embedded in the QA overlay (the ``</body>`` tag is preserved so the overlay
can inject scripts before it).

No external dependencies are required beyond an optional Google Fonts import.
All user-provided text is HTML-escaped to prevent XSS.
"""

from __future__ import annotations

from iriai_build_v2.models.outputs import SystemDesign


# ── HTML escaping ────────────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """Escape text for safe HTML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── Tier classification for service topology ─────────────────────────────────

_TIER_ORDER = {"frontend": 0, "service": 1, "external": 1, "queue": 2, "cache": 2, "database": 3}

_KIND_ICONS = {
    "frontend": "&#x1F310;",   # globe
    "service": "&#x2699;&#xFE0F;",    # gear
    "database": "&#x1F5C3;&#xFE0F;",  # card file box
    "queue": "&#x1F4E8;",      # incoming envelope
    "cache": "&#x26A1;",       # high voltage
    "external": "&#x1F517;",   # link
}

_TIER_LABELS = {0: "Frontend", 1: "Services &amp; External", 2: "Queues &amp; Caches", 3: "Databases"}


def _tier(kind: str) -> int:
    return _TIER_ORDER.get(kind, 1)


def _kind_icon(kind: str) -> str:
    return _KIND_ICONS.get(kind, "&#x1F4E6;")


# ── Journey extraction helper ────────────────────────────────────────────────


def _collect_journeys(model: SystemDesign) -> list[str]:
    """Return sorted unique journey IDs from all journey-tagged elements."""
    ids: set[str] = set()
    for svc in model.services:
        ids.update(svc.journeys)
    for conn in model.connections:
        ids.update(conn.journeys)
    for ent in model.entities:
        ids.update(ent.journeys)
    for cp in model.call_paths:
        if cp.journey_id:
            ids.add(cp.journey_id)
    return sorted(ids)


# ── Method badge colour ──────────────────────────────────────────────────────

_METHOD_COLORS = {
    "GET": "#22c55e",
    "POST": "#3b82f6",
    "PUT": "#f59e0b",
    "PATCH": "#a855f7",
    "DELETE": "#ef4444",
}


def _method_badge(method: str) -> str:
    color = _METHOD_COLORS.get(method.upper(), "#6b7280")
    m = _esc(method.upper())
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f"font-size:0.75rem;font-weight:700;font-family:-apple-system,BlinkMacSystemFont,"
        f"'Segoe UI',sans-serif;color:#fff;background:{color};letter-spacing:0.04em;"
        f'line-height:1.6;">{m}</span>'
    )


# ── Public API ───────────────────────────────────────────────────────────────


def render_system_design_html(model: SystemDesign) -> str:
    """Convert a *SystemDesign* Pydantic model into a complete interactive HTML page."""

    title = _esc(model.title)
    journeys = _collect_journeys(model)

    parts: list[str] = []
    _a = parts.append  # shorthand

    # ── Document shell ───────────────────────────────────────────────────────
    _a("<!DOCTYPE html>")
    _a('<html lang="en">')
    _a("<head>")
    _a('<meta charset="UTF-8">')
    _a('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    _a(f"<title>{title} - System Design</title>")
    _a(_CSS)
    _a("</head>")
    _a("<body>")

    # ── Header ───────────────────────────────────────────────────────────────
    _a('<header class="sd-header">')
    _a('<div class="sd-header-inner">')
    _a(f'<h1 class="sd-title">{title}</h1>')

    # Nav links
    _a('<nav class="sd-nav" id="sd-nav">')
    _a('<a href="#overview" class="sd-nav-link">Overview</a>')
    if model.services:
        _a('<a href="#topology" class="sd-nav-link">Topology</a>')
    if model.call_paths:
        _a('<a href="#call-paths" class="sd-nav-link">Call Paths</a>')
    if model.api_endpoints:
        _a('<a href="#endpoints" class="sd-nav-link">Endpoints</a>')
    if model.entities:
        _a('<a href="#entities" class="sd-nav-link">Entities</a>')
    if model.entity_relations:
        _a('<a href="#relations" class="sd-nav-link">Relations</a>')
    if model.risks:
        _a('<a href="#risks" class="sd-nav-link">Risks</a>')
    _a("</nav>")

    # Journey filter pills
    if journeys:
        _a('<div class="sd-filters" id="sd-filters">')
        _a('<span class="sd-filter-label">Filter by journey:</span>')
        _a(
            '<button class="sd-pill sd-pill-active" data-journey="__all__" '
            'onclick="window.__sd_filter(\'__all__\')">All</button>'
        )
        for j in journeys:
            _a(
                f'<button class="sd-pill" data-journey="{_esc(j)}" '
                f"onclick=\"window.__sd_filter('{_esc(j)}')\">{_esc(j)}</button>"
            )
        _a("</div>")

    _a("</div>")
    _a("</header>")

    # ── Main content ─────────────────────────────────────────────────────────
    _a('<main class="sd-main">')

    # ── Overview ─────────────────────────────────────────────────────────────
    _a('<section class="sd-section" id="overview">')
    _a('<h2 class="sd-section-title">Overview</h2>')
    if model.overview:
        _a(f'<p class="sd-overview-text">{_esc(model.overview)}</p>')
    else:
        _a('<p class="sd-empty">No overview provided.</p>')

    if model.decisions:
        _a('<h3 class="sd-subsection-title">Architecture Decisions</h3>')
        _a('<ol class="sd-decisions-list">')
        for d in model.decisions:
            _a(f"<li>{_esc(d)}</li>")
        _a("</ol>")
    _a("</section>")

    # ── Service Topology ─────────────────────────────────────────────────────
    _a('<section class="sd-section" id="topology">')
    _a('<h2 class="sd-section-title">Service Topology</h2>')

    if not model.services:
        _a('<p class="sd-empty">No services defined.</p>')
    else:
        # Group by tier
        tiers: dict[int, list[int]] = {}
        for idx, svc in enumerate(model.services):
            t = _tier(svc.kind)
            tiers.setdefault(t, []).append(idx)

        for tier_key in sorted(tiers.keys()):
            tier_label = _TIER_LABELS.get(tier_key, "Other")
            _a(f'<h4 class="sd-tier-label">{tier_label}</h4>')
            _a('<div class="sd-service-grid">')
            for idx in tiers[tier_key]:
                svc = model.services[idx]
                j_attr = " ".join(svc.journeys) if svc.journeys else ""
                _a(
                    f'<div class="sd-service-card" '
                    f'data-service-id="{_esc(svc.id)}" '
                    f'data-journeys="{_esc(j_attr)}">'
                )
                _a(f'<div class="sd-service-icon">{_kind_icon(svc.kind)}</div>')
                _a(f'<div class="sd-service-name">{_esc(svc.name)}</div>')
                _a(f'<div class="sd-service-kind">{_esc(svc.kind)}</div>')
                if svc.technology:
                    _a(f'<div class="sd-service-tech">{_esc(svc.technology)}</div>')
                if svc.port:
                    _a(f'<div class="sd-service-port">Port {_esc(svc.port)}</div>')
                if svc.description:
                    _a(f'<p class="sd-service-desc">{_esc(svc.description)}</p>')
                _a("</div>")
            _a("</div>")

        # Connections list
        if model.connections:
            _a('<h3 class="sd-subsection-title">Connections</h3>')
            _a('<div class="sd-connections-list">')
            # Build a name lookup
            svc_names = {s.id: s.name for s in model.services}
            for conn in model.connections:
                j_attr = " ".join(conn.journeys) if conn.journeys else ""
                from_name = _esc(svc_names.get(conn.from_id, conn.from_id))
                to_name = _esc(svc_names.get(conn.to_id, conn.to_id))
                proto = f" ({_esc(conn.protocol)})" if conn.protocol else ""
                _a(
                    f'<div class="sd-connection-row" data-journeys="{_esc(j_attr)}">'
                    f'<span class="sd-conn-from">{from_name}</span>'
                    f'<span class="sd-conn-arrow">&#x2192;</span>'
                    f'<span class="sd-conn-to">{to_name}</span>'
                    f'<span class="sd-conn-proto">{proto}</span>'
                    f'<span class="sd-conn-label">{_esc(conn.label)}</span>'
                    f"</div>"
                )
            _a("</div>")

    _a("</section>")

    # ── API Call Paths ───────────────────────────────────────────────────────
    if model.call_paths:
        _a('<section class="sd-section" id="call-paths">')
        _a('<h2 class="sd-section-title">API Call Paths</h2>')
        _a(
            '<button class="sd-expand-btn" id="sd-toggle-details" '
            'onclick="window.__sd_toggle_details()">Expand All</button>'
        )
        for cp in model.call_paths:
            j_attr = _esc(cp.journey_id) if cp.journey_id else ""
            _a(
                f'<details class="sd-call-path" data-journey-id="{j_attr}">'
            )
            _a(f"<summary>")
            _a(f'<span class="sd-cp-name">{_esc(cp.name)}</span>')
            if cp.journey_id:
                _a(f'<span class="sd-cp-journey">{_esc(cp.journey_id)}</span>')
            _a(f"</summary>")
            if cp.description:
                _a(f'<p class="sd-cp-desc">{_esc(cp.description)}</p>')
            if cp.steps:
                _a('<div class="sd-sequence">')
                _a('<div class="sd-seq-header">')
                _a('<div class="sd-seq-col sd-seq-num">#</div>')
                _a('<div class="sd-seq-col sd-seq-from">From</div>')
                _a('<div class="sd-seq-col sd-seq-arrow"></div>')
                _a('<div class="sd-seq-col sd-seq-to">To</div>')
                _a('<div class="sd-seq-col sd-seq-action">Action</div>')
                _a('<div class="sd-seq-col sd-seq-returns">Returns</div>')
                _a("</div>")
                for step in cp.steps:
                    _a('<div class="sd-seq-row">')
                    _a(f'<div class="sd-seq-col sd-seq-num">{step.sequence}</div>')
                    _a(f'<div class="sd-seq-col sd-seq-from">{_esc(step.from_service)}</div>')
                    _a('<div class="sd-seq-col sd-seq-arrow">&#x2192;</div>')
                    _a(f'<div class="sd-seq-col sd-seq-to">{_esc(step.to_service)}</div>')
                    _a(
                        f'<div class="sd-seq-col sd-seq-action">'
                        f"<div>{_esc(step.action)}</div>"
                        f'<div class="sd-seq-detail">{_esc(step.description)}</div>'
                        f"</div>"
                    )
                    _a(
                        f'<div class="sd-seq-col sd-seq-returns">'
                        f"{_esc(step.returns) if step.returns else '&mdash;'}"
                        f"</div>"
                    )
                    _a("</div>")
                _a("</div>")
            else:
                _a('<p class="sd-empty">No steps defined.</p>')
            _a("</details>")
        _a("</section>")

    # ── API Endpoints ────────────────────────────────────────────────────────
    if model.api_endpoints:
        _a('<section class="sd-section" id="endpoints">')
        _a('<h2 class="sd-section-title">API Endpoints</h2>')
        svc_names = {s.id: s.name for s in model.services}
        _a('<div class="sd-table-wrap">')
        _a('<table class="sd-table">')
        _a("<thead><tr>")
        _a("<th>Method</th><th>Path</th><th>Service</th><th>Auth</th><th>Description</th>")
        _a("</tr></thead>")
        _a("<tbody>")
        for ep in model.api_endpoints:
            svc_display = _esc(svc_names.get(ep.service_id, ep.service_id))
            _a("<tr>")
            _a(f"<td>{_method_badge(ep.method)}</td>")
            _a(f'<td><code>{_esc(ep.path)}</code></td>')
            _a(
                f'<td><span class="sd-svc-ref" data-ref-service="{_esc(ep.service_id)}">'
                f"{svc_display}</span></td>"
            )
            _a(f"<td>{_esc(ep.auth) if ep.auth else '&mdash;'}</td>")
            _a(f"<td>{_esc(ep.description)}</td>")
            _a("</tr>")
        _a("</tbody>")
        _a("</table>")
        _a("</div>")
        _a("</section>")

    # ── Entity Model ─────────────────────────────────────────────────────────
    if model.entities:
        _a('<section class="sd-section" id="entities">')
        _a('<h2 class="sd-section-title">Entity Model</h2>')
        svc_names = {s.id: s.name for s in model.services}
        _a('<div class="sd-entity-grid">')
        for ent in model.entities:
            j_attr = " ".join(ent.journeys) if ent.journeys else ""
            svc_display = _esc(svc_names.get(ent.service_id, ent.service_id))
            _a(
                f'<div class="sd-entity-card" '
                f'data-service-id="{_esc(ent.service_id)}" '
                f'data-journeys="{_esc(j_attr)}">'
            )
            _a(f'<div class="sd-entity-header">')
            _a(f'<h4 class="sd-entity-name">{_esc(ent.name)}</h4>')
            _a(
                f'<span class="sd-entity-svc" data-ref-service="{_esc(ent.service_id)}">'
                f"{svc_display}</span>"
            )
            _a("</div>")
            if ent.fields:
                _a('<table class="sd-field-table">')
                _a("<thead><tr><th>Field</th><th>Type</th><th>Constraints</th></tr></thead>")
                _a("<tbody>")
                for f in ent.fields:
                    _a("<tr>")
                    _a(f"<td>{_esc(f.name)}</td>")
                    _a(f'<td><code>{_esc(f.type)}</code></td>')
                    _a(f"<td>{_esc(f.constraints) if f.constraints else '&mdash;'}</td>")
                    _a("</tr>")
                _a("</tbody>")
                _a("</table>")
            else:
                _a('<p class="sd-empty">No fields defined.</p>')
            _a("</div>")
        _a("</div>")
        _a("</section>")

    # ── Entity Relationships ─────────────────────────────────────────────────
    if model.entity_relations:
        _a('<section class="sd-section" id="relations">')
        _a('<h2 class="sd-section-title">Entity Relationships</h2>')
        _a('<div class="sd-table-wrap">')
        _a('<table class="sd-table">')
        _a("<thead><tr>")
        _a("<th>From</th><th>Relationship</th><th>To</th><th>Label</th>")
        _a("</tr></thead>")
        _a("<tbody>")
        for rel in model.entity_relations:
            _a("<tr>")
            _a(f"<td>{_esc(rel.from_entity)}</td>")
            _a(f'<td><span class="sd-rel-kind">{_esc(rel.kind)}</span></td>')
            _a(f"<td>{_esc(rel.to_entity)}</td>")
            _a(f"<td>{_esc(rel.label) if rel.label else '&mdash;'}</td>")
            _a("</tr>")
        _a("</tbody>")
        _a("</table>")
        _a("</div>")
        _a("</section>")

    # ── Risks ────────────────────────────────────────────────────────────────
    if model.risks:
        _a('<section class="sd-section" id="risks">')
        _a('<h2 class="sd-section-title">Risks</h2>')
        _a('<ul class="sd-risks-list">')
        for r in model.risks:
            _a(f"<li>{_esc(r)}</li>")
        _a("</ul>")
        _a("</section>")

    _a("</main>")

    # ── Footer ───────────────────────────────────────────────────────────────
    _a('<footer class="sd-footer">')
    _a("<p>System Design Document</p>")
    _a("</footer>")

    # ── Inline JavaScript ────────────────────────────────────────────────────
    _a("<script>")
    _a(_JS)
    _a("</script>")

    _a("</body>")
    _a("</html>")

    return "\n".join(parts)


# ── Embedded CSS ─────────────────────────────────────────────────────────────

_CSS = """\
<style>
  /* ── Reset & Base ─────────────────────────────────────────────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  @font-face {
    font-family: 'Charter';
    src: local('Charter'), local('Georgia');
    font-display: swap;
  }

  :root {
    --text-primary: #1a1a2e;
    --text-secondary: #555;
    --text-muted: #888;
    --bg-page: #faf9f7;
    --bg-card: #fff;
    --border-subtle: rgba(0, 0, 0, 0.06);
    --border-medium: rgba(0, 0, 0, 0.1);
    --accent: #2d5be3;
    --accent-faint: rgba(45, 91, 227, 0.06);
    --accent-light: rgba(45, 91, 227, 0.12);
    --content-width: 960px;
    --gutter: clamp(20px, 5vw, 48px);
    --radius: 10px;
    --radius-sm: 6px;
  }

  html {
    font-size: 17px;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
    scroll-behavior: smooth;
  }

  body {
    font-family: Charter, Georgia, 'Times New Roman', serif;
    color: var(--text-primary);
    background: var(--bg-page);
    line-height: 1.72;
    min-height: 100vh;
  }

  ::selection {
    background: rgba(45, 91, 227, 0.18);
    color: inherit;
  }

  /* ── Header ──────────────────────────────────────────────────────── */
  .sd-header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: rgba(250, 249, 247, 0.92);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border-subtle);
    padding: 0 var(--gutter);
  }

  .sd-header-inner {
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 20px 0 16px;
  }

  .sd-title {
    font-size: clamp(1.8rem, 4vw, 2.6rem);
    font-weight: 700;
    line-height: 1.15;
    letter-spacing: -0.025em;
    color: var(--text-primary);
    margin-bottom: 12px;
  }

  .sd-nav {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 12px;
  }

  .sd-nav-link {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
    text-decoration: none;
    padding: 4px 10px;
    border-radius: var(--radius-sm);
    transition: color 0.15s, background 0.15s;
  }

  .sd-nav-link:hover {
    color: var(--accent);
    background: var(--accent-faint);
  }

  /* ── Filter pills ────────────────────────────────────────────────── */
  .sd-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding-top: 4px;
  }

  .sd-filter-label {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-right: 4px;
  }

  .sd-pill {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px;
    font-weight: 500;
    padding: 3px 12px;
    border: 1px solid var(--border-medium);
    border-radius: 100px;
    background: var(--bg-card);
    color: var(--text-secondary);
    cursor: pointer;
    transition: all 0.15s;
  }

  .sd-pill:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .sd-pill-active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }

  .sd-pill-active:hover {
    background: #1a3fa0;
    border-color: #1a3fa0;
    color: #fff;
  }

  /* ── Main content ────────────────────────────────────────────────── */
  .sd-main {
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 32px var(--gutter) 80px;
  }

  .sd-section {
    margin-bottom: 48px;
  }

  .sd-section-title {
    font-size: 1.5rem;
    font-weight: 700;
    line-height: 1.3;
    letter-spacing: -0.01em;
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-primary);
  }

  .sd-subsection-title {
    font-size: 1.1rem;
    font-weight: 700;
    margin: 28px 0 14px;
    color: var(--text-primary);
  }

  .sd-overview-text {
    font-size: 1.05rem;
    line-height: 1.75;
    color: var(--text-secondary);
    margin-bottom: 1.4em;
  }

  .sd-empty {
    font-style: italic;
    color: var(--text-muted);
    padding: 16px 0;
  }

  /* ── Decisions list ──────────────────────────────────────────────── */
  .sd-decisions-list {
    padding-left: 1.6em;
    color: var(--text-secondary);
  }

  .sd-decisions-list li {
    margin-bottom: 0.5em;
    line-height: 1.6;
  }

  /* ── Tier labels ─────────────────────────────────────────────────── */
  .sd-tier-label {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 20px 0 10px;
  }

  /* ── Service grid ────────────────────────────────────────────────── */
  .sd-service-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 14px;
    margin-bottom: 8px;
  }

  .sd-service-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 18px;
    transition: opacity 0.3s, border-color 0.2s, box-shadow 0.2s;
    cursor: default;
  }

  .sd-service-card:hover {
    border-color: var(--accent);
    box-shadow: 0 2px 12px rgba(45, 91, 227, 0.08);
  }

  .sd-service-card.sd-highlight {
    border-color: var(--accent);
    box-shadow: 0 2px 16px rgba(45, 91, 227, 0.14);
  }

  .sd-service-icon {
    font-size: 1.5rem;
    margin-bottom: 8px;
  }

  .sd-service-name {
    font-weight: 700;
    font-size: 1rem;
    color: var(--text-primary);
    margin-bottom: 2px;
  }

  .sd-service-kind {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin-bottom: 6px;
  }

  .sd-service-tech {
    font-family: 'SF Mono', 'Fira Code', Menlo, Consolas, monospace;
    font-size: 0.78rem;
    color: #1a3fa0;
    background: var(--accent-faint);
    display: inline-block;
    padding: 1px 8px;
    border-radius: 4px;
    margin-bottom: 4px;
  }

  .sd-service-port {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-bottom: 6px;
  }

  .sd-service-desc {
    font-size: 0.88rem;
    color: var(--text-secondary);
    line-height: 1.55;
    margin-top: 6px;
  }

  /* ── Connections list ────────────────────────────────────────────── */
  .sd-connections-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .sd-connection-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.88rem;
    transition: opacity 0.3s;
    flex-wrap: wrap;
  }

  .sd-conn-from, .sd-conn-to {
    font-weight: 600;
    color: var(--text-primary);
  }

  .sd-conn-arrow {
    color: var(--accent);
    font-weight: 700;
  }

  .sd-conn-proto {
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  .sd-conn-label {
    color: var(--text-secondary);
    font-weight: 400;
    margin-left: auto;
  }

  /* ── Call paths (details/summary) ────────────────────────────────── */
  .sd-call-path {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    margin-bottom: 10px;
    transition: opacity 0.3s;
    overflow: hidden;
  }

  .sd-call-path summary {
    padding: 14px 18px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-weight: 600;
    font-size: 0.95rem;
    color: var(--text-primary);
    list-style: none;
  }

  .sd-call-path summary::-webkit-details-marker { display: none; }

  .sd-call-path summary::before {
    content: '\\25B6';
    font-size: 0.65rem;
    color: var(--text-muted);
    transition: transform 0.2s;
    flex-shrink: 0;
  }

  .sd-call-path[open] summary::before {
    transform: rotate(90deg);
  }

  .sd-cp-name { flex: 1; }

  .sd-cp-journey {
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--accent);
    background: var(--accent-faint);
    padding: 2px 8px;
    border-radius: 100px;
  }

  .sd-cp-desc {
    padding: 0 18px 12px;
    font-size: 0.9rem;
    color: var(--text-secondary);
    line-height: 1.6;
  }

  /* ── Sequence diagram rows ───────────────────────────────────────── */
  .sd-sequence {
    margin: 0 18px 18px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    overflow-x: auto;
  }

  .sd-seq-header, .sd-seq-row {
    display: grid;
    grid-template-columns: 40px 1fr 32px 1fr 2fr 1.5fr;
    gap: 8px;
    padding: 8px 14px;
    align-items: start;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.82rem;
  }

  .sd-seq-header {
    background: rgba(0, 0, 0, 0.02);
    font-weight: 700;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border-subtle);
  }

  .sd-seq-row {
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-secondary);
  }

  .sd-seq-row:last-child { border-bottom: none; }

  .sd-seq-row:hover { background: rgba(45, 91, 227, 0.02); }

  .sd-seq-num {
    font-weight: 700;
    color: var(--accent);
    text-align: center;
  }

  .sd-seq-from, .sd-seq-to { font-weight: 600; color: var(--text-primary); }

  .sd-seq-arrow { color: var(--accent); text-align: center; font-weight: 700; }

  .sd-seq-action { color: var(--text-primary); }

  .sd-seq-detail {
    font-size: 0.78rem;
    color: var(--text-muted);
    margin-top: 2px;
  }

  .sd-seq-returns {
    font-family: 'SF Mono', 'Fira Code', Menlo, Consolas, monospace;
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  /* ── Expand/collapse button ──────────────────────────────────────── */
  .sd-expand-btn {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px;
    font-weight: 500;
    padding: 4px 14px;
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    background: var(--bg-card);
    color: var(--text-secondary);
    cursor: pointer;
    margin-bottom: 14px;
    transition: all 0.15s;
  }

  .sd-expand-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  /* ── Tables ──────────────────────────────────────────────────────── */
  .sd-table-wrap {
    overflow-x: auto;
  }

  .sd-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid var(--border-subtle);
  }

  .sd-table th, .sd-table td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
  }

  .sd-table th {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-weight: 600;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    background: rgba(0, 0, 0, 0.02);
  }

  .sd-table tr:last-child td { border-bottom: none; }
  .sd-table tr:hover td { background: rgba(45, 91, 227, 0.02); }

  .sd-table code {
    font-family: 'SF Mono', 'Fira Code', Menlo, Consolas, monospace;
    font-size: 0.82em;
    background: var(--accent-faint);
    padding: 2px 6px;
    border-radius: 4px;
    color: #1a3fa0;
    word-break: break-all;
  }

  .sd-svc-ref {
    cursor: pointer;
    font-weight: 500;
    color: var(--accent);
    transition: color 0.15s;
  }

  .sd-svc-ref:hover { color: #1a3fa0; }

  .sd-rel-kind {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--text-muted);
    background: rgba(0,0,0,0.03);
    padding: 2px 8px;
    border-radius: 4px;
  }

  /* ── Entity grid ─────────────────────────────────────────────────── */
  .sd-entity-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 14px;
  }

  .sd-entity-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    overflow: hidden;
    transition: opacity 0.3s, border-color 0.2s, box-shadow 0.2s;
  }

  .sd-entity-card:hover {
    border-color: var(--accent);
    box-shadow: 0 2px 12px rgba(45, 91, 227, 0.08);
  }

  .sd-entity-card.sd-highlight {
    border-color: var(--accent);
    box-shadow: 0 2px 16px rgba(45, 91, 227, 0.14);
  }

  .sd-entity-header {
    padding: 14px 16px 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .sd-entity-name {
    font-size: 1rem;
    font-weight: 700;
    color: var(--text-primary);
  }

  .sd-entity-svc {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--accent);
    background: var(--accent-faint);
    padding: 2px 8px;
    border-radius: 100px;
    cursor: pointer;
  }

  .sd-field-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }

  .sd-field-table th, .sd-field-table td {
    padding: 7px 16px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
  }

  .sd-field-table th {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-weight: 600;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    background: rgba(0, 0, 0, 0.015);
  }

  .sd-field-table tr:last-child td { border-bottom: none; }
  .sd-field-table tr:hover td { background: rgba(45, 91, 227, 0.02); }

  .sd-field-table code {
    font-family: 'SF Mono', 'Fira Code', Menlo, Consolas, monospace;
    font-size: 0.85em;
    background: var(--accent-faint);
    padding: 1px 5px;
    border-radius: 3px;
    color: #1a3fa0;
  }

  /* ── Risks ───────────────────────────────────────────────────────── */
  .sd-risks-list {
    list-style: none;
    padding: 0;
  }

  .sd-risks-list li {
    padding: 10px 16px;
    margin-bottom: 6px;
    background: #fef3f2;
    border: 1px solid rgba(239, 68, 68, 0.12);
    border-left: 3px solid #ef4444;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    font-size: 0.92rem;
    color: var(--text-secondary);
    line-height: 1.6;
  }

  /* ── Footer ──────────────────────────────────────────────────────── */
  .sd-footer {
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 24px var(--gutter) 48px;
    text-align: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 0.75rem;
    color: var(--text-muted);
    border-top: 1px solid var(--border-subtle);
  }

  /* ── Dimmed state for journey filtering ──────────────────────────── */
  .sd-dimmed {
    opacity: 0.15 !important;
    pointer-events: none;
  }

  /* ── Entrance animation ──────────────────────────────────────────── */
  @keyframes sd-fade-up {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .sd-header, .sd-main {
    animation: sd-fade-up 0.45s ease both;
  }

  .sd-main { animation-delay: 0.08s; }

  /* ── Responsive ──────────────────────────────────────────────────── */
  @media (max-width: 700px) {
    html { font-size: 15px; }
    .sd-service-grid { grid-template-columns: 1fr; }
    .sd-entity-grid { grid-template-columns: 1fr; }
    .sd-seq-header, .sd-seq-row {
      grid-template-columns: 32px 1fr 24px 1fr 1.5fr 1fr;
      font-size: 0.75rem;
    }
  }

  @media (max-width: 480px) {
    .sd-header-inner { padding: 16px 0 12px; }
    .sd-nav { gap: 4px; }
    .sd-nav-link { font-size: 12px; padding: 3px 8px; }
    .sd-filters { gap: 4px; }
  }
</style>"""

# ── Embedded JavaScript ──────────────────────────────────────────────────────

_JS = """\
(function () {
  'use strict';

  // ── Journey filtering ──────────────────────────────────────────────
  var activeJourney = '__all__';

  window.__sd_filter = function (journeyId) {
    activeJourney = journeyId;

    // Update pill states
    var pills = document.querySelectorAll('.sd-pill');
    for (var i = 0; i < pills.length; i++) {
      var p = pills[i];
      if (p.getAttribute('data-journey') === journeyId) {
        p.classList.add('sd-pill-active');
      } else {
        p.classList.remove('sd-pill-active');
      }
    }

    // Filter elements with data-journeys
    var tagged = document.querySelectorAll('[data-journeys]');
    for (var j = 0; j < tagged.length; j++) {
      var el = tagged[j];
      if (journeyId === '__all__') {
        el.classList.remove('sd-dimmed');
      } else {
        var jList = (el.getAttribute('data-journeys') || '').split(/\\s+/);
        var match = false;
        for (var k = 0; k < jList.length; k++) {
          if (jList[k] === journeyId) { match = true; break; }
        }
        el.classList.toggle('sd-dimmed', !match);
      }
    }

    // Filter elements with data-journey-id (call paths)
    var cpTagged = document.querySelectorAll('[data-journey-id]');
    for (var m = 0; m < cpTagged.length; m++) {
      var cpEl = cpTagged[m];
      if (journeyId === '__all__') {
        cpEl.classList.remove('sd-dimmed');
      } else {
        var cpJourney = cpEl.getAttribute('data-journey-id') || '';
        cpEl.classList.toggle('sd-dimmed', cpJourney !== '' && cpJourney !== journeyId);
      }
    }
  };

  // ── Expand / collapse all details ──────────────────────────────────
  var detailsExpanded = false;

  window.__sd_toggle_details = function () {
    detailsExpanded = !detailsExpanded;
    var details = document.querySelectorAll('.sd-call-path');
    for (var i = 0; i < details.length; i++) {
      details[i].open = detailsExpanded;
    }
    var btn = document.getElementById('sd-toggle-details');
    if (btn) {
      btn.textContent = detailsExpanded ? 'Collapse All' : 'Expand All';
    }
  };

  // ── Service hover cross-highlighting ───────────────────────────────
  function highlightService(serviceId, on) {
    // Highlight service cards
    var cards = document.querySelectorAll('.sd-service-card[data-service-id="' + serviceId + '"]');
    for (var i = 0; i < cards.length; i++) {
      cards[i].classList.toggle('sd-highlight', on);
    }

    // Highlight entity cards belonging to this service
    var entities = document.querySelectorAll('.sd-entity-card[data-service-id="' + serviceId + '"]');
    for (var j = 0; j < entities.length; j++) {
      entities[j].classList.toggle('sd-highlight', on);
    }

    // Highlight service references in tables
    var refs = document.querySelectorAll('.sd-svc-ref[data-ref-service="' + serviceId + '"]');
    for (var k = 0; k < refs.length; k++) {
      refs[k].style.fontWeight = on ? '700' : '';
      refs[k].style.textDecoration = on ? 'underline' : '';
    }
  }

  // Attach hover listeners to service cards
  document.addEventListener('DOMContentLoaded', function () {
    var svcCards = document.querySelectorAll('.sd-service-card[data-service-id]');
    for (var i = 0; i < svcCards.length; i++) {
      (function (card) {
        var sid = card.getAttribute('data-service-id');
        card.addEventListener('mouseenter', function () { highlightService(sid, true); });
        card.addEventListener('mouseleave', function () { highlightService(sid, false); });
      })(svcCards[i]);
    }

    // Attach hover listeners to service refs (entity badges, table refs)
    var svcRefs = document.querySelectorAll('[data-ref-service]');
    for (var j = 0; j < svcRefs.length; j++) {
      (function (ref) {
        var sid = ref.getAttribute('data-ref-service');
        ref.addEventListener('mouseenter', function () { highlightService(sid, true); });
        ref.addEventListener('mouseleave', function () { highlightService(sid, false); });
      })(svcRefs[j]);
    }
  });

  // ── Smooth scroll via nav links ────────────────────────────────────
  var navLinks = document.querySelectorAll('.sd-nav-link');
  for (var i = 0; i < navLinks.length; i++) {
    navLinks[i].addEventListener('click', function (e) {
      var href = this.getAttribute('href');
      if (href && href.charAt(0) === '#') {
        var target = document.getElementById(href.substring(1));
        if (target) {
          e.preventDefault();
          var headerHeight = document.querySelector('.sd-header')
            ? document.querySelector('.sd-header').offsetHeight
            : 0;
          var top = target.getBoundingClientRect().top + window.pageYOffset - headerHeight - 16;
          window.scrollTo({ top: top, behavior: 'smooth' });
        }
      }
    });
  }
})();"""

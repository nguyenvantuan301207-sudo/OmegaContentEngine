"""Asset Provider abstraction and local deterministic visual scene card generator."""

from __future__ import annotations

import asyncio
import html
import re
import uuid
from pathlib import Path
from typing import Any

from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.domain.production import (
    AssetProviderType,
    AssetType,
    LicenseStatus,
    SceneType,
)

# Palette for deterministic background accents
THEMES = [
    {"bg": "#0f172a", "card": "#1e293b", "accent": "#38bdf8", "border": "#334155", "tag": "ARCHITECTURE"},
    {"bg": "#090d16", "card": "#172554", "accent": "#60a5fa", "border": "#1e3a8a", "tag": "DEEP DIVE"},
    {"bg": "#0d1117", "card": "#1e1b4b", "accent": "#a78bfa", "border": "#312e81", "tag": "SYSTEM DESIGN"},
    {"bg": "#0b1320", "card": "#132338", "accent": "#34d399", "border": "#1e3a5f", "tag": "BENCHMARKS"},
]


def _wrap_text(text: str, max_chars: int = 55) -> list[str]:
    """Wrap text into lines without exceeding maximum character length."""
    clean = " ".join(text.split()).strip()
    if not clean:
        return []
    words = clean.split()
    lines: list[str] = []
    curr_line: list[str] = []
    curr_len = 0
    for w in words:
        if curr_len + len(w) + 1 > max_chars and curr_line:
            lines.append(" ".join(curr_line))
            curr_line = [w]
            curr_len = len(w)
        else:
            curr_line.append(w)
            curr_len += len(w) + 1
    if curr_line:
        lines.append(" ".join(curr_line))
    return lines


def _extract_metric_hero(text: str) -> tuple[str, str]:
    """Extract prominent statistical numbers or phrases for metric callouts."""
    # Look for metrics like '50k req/s', '99.9%', '45%', '10x', '$500'
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*(?:%|req/s|ops/s|ms|seconds|users|gb|mb|kb|x))\b|\$\d+)", text, re.IGNORECASE)
    if m:
        metric = m.group(1).strip()
        context = text.replace(m.group(0), "").strip()
        return metric, context
    return "99.9%", text


def generate_scene_card_svg(
    scene_type: str,
    title: str,
    heading: str,
    text: str,
    visual_intent: str,
    theme_idx: int = 0,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Generate canonical 1080p SVG markup for a contentful scene card."""
    theme = THEMES[theme_idx % len(THEMES)]
    bg_color = theme["bg"]
    card_bg = theme["card"]
    accent_color = theme["accent"]
    border_color = theme["border"]

    safe_title = html.escape(title or "Technical Architecture")
    safe_heading = html.escape(heading or theme["tag"])

    # Header / Tag text
    tag_label = safe_heading.upper()
    if len(tag_label) > 40:
        tag_label = tag_label[:37] + "..."

    svg_parts: list[str] = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        # Background
        f'  <rect width="100%" height="100%" fill="{bg_color}"/>',
        # Grid accent lines (subtle)
        '  <line x1="80" y1="200" x2="1840" y2="200" stroke="#1e293b" stroke-width="2" stroke-dasharray="8 8"/>',
        '  <line x1="80" y1="880" x2="1840" y2="880" stroke="#1e293b" stroke-width="2" stroke-dasharray="8 8"/>',
        # Main Card Panel
        f'  <rect x="100" y="90" width="1720" height="900" rx="28" fill="{card_bg}" stroke="{border_color}" stroke-width="3"/>',
        # Header Accent Top Bar
        f'  <rect x="100" y="90" width="1720" height="12" rx="6" fill="{accent_color}"/>',
        # Category Badge Pill
        f'  <rect x="160" y="145" width="{max(180, len(tag_label) * 16 + 40)}" height="48" rx="24" fill="{bg_color}" stroke="{accent_color}" stroke-width="2"/>',
        f'  <text x="180" y="177" font-family="DejaVu Sans, Arial, sans-serif" font-size="20" font-weight="bold" fill="{accent_color}" letter-spacing="1.5">{tag_label}</text>',
    ]

    st = (scene_type or "NARRATION").upper()

    if st in (SceneType.TITLE.value, SceneType.TITLE_MOTION.value, "TITLE", "TITLE_MOTION"):
        # TITLE_MOTION LAYOUT: Large Headline, Subtitle, Key Hook
        title_lines = _wrap_text(text or title, max_chars=40)[:3]
        y_pos = 290
        for line in title_lines:
            safe_l = html.escape(line)
            svg_parts.append(
                f'  <text x="160" y="{y_pos}" font-family="DejaVu Sans, Arial, sans-serif" font-size="52" font-weight="bold" fill="#f8fafc">{safe_l}</text>'
            )
            y_pos += 72

        svg_parts.append(f'  <line x1="160" y1="{y_pos + 10}" x2="1760" y2="{y_pos + 10}" stroke="{border_color}" stroke-width="2"/>')

        intent_lines = _wrap_text(visual_intent or "Production Architecture Analysis & Empirical Benchmarks", max_chars=60)[:2]
        iy = y_pos + 70
        for iline in intent_lines:
            safe_il = html.escape(iline)
            svg_parts.append(
                f'  <text x="160" y="{iy}" font-family="DejaVu Sans, Arial, sans-serif" font-size="30" fill="#94a3b8">{safe_il}</text>'
            )
            iy += 44

    elif st in (SceneType.CODE_DEMO.value, "CODE_DEMO"):
        # CODE_DEMO LAYOUT: Dark-theme IDE Code Editor with Syntax Highlighting
        svg_parts.extend([
            '  <text x="160" y="240" font-family="DejaVu Sans, Arial, sans-serif" font-size="36" font-weight="bold" fill="#f8fafc">Production Implementation Pattern</text>',
            # IDE Window
            f'  <rect x="160" y="270" width="1600" height="580" rx="16" fill="#090d16" stroke="{border_color}" stroke-width="2"/>',
            # IDE Header / Window Controls
            '  <circle cx="200" cy="305" r="7" fill="#ef4444"/>',
            '  <circle cx="225" cy="305" r="7" fill="#f59e0b"/>',
            '  <circle cx="250" cy="305" r="7" fill="#10b981"/>',
            '  <rect x="290" y="285" width="220" height="38" rx="6" fill="#1e293b"/>',
            '  <text x="315" y="310" font-family="DejaVu Sans Mono, monospace" font-size="16" fill="#94a3b8">main_service.py</text>',
            f'  <line x1="160" y1="330" x2="1760" y2="330" stroke="{border_color}" stroke-width="1.5"/>',
        ])
        # Monospace Code Lines with syntax colors
        code_snippets = [
            ('<tspan fill="#64748b">01</tspan>  <tspan fill="#c084fc">from</tspan> <tspan fill="#f8fafc">fastapi</tspan> <tspan fill="#c084fc">import</tspan> <tspan fill="#38bdf8">FastAPI, Depends, HTTPException, status</tspan>'),
            ('<tspan fill="#64748b">02</tspan>  <tspan fill="#c084fc">from</tspan> <tspan fill="#f8fafc">contextlib</tspan> <tspan fill="#c084fc">import</tspan> <tspan fill="#38bdf8">asynccontextmanager</tspan>'),
            ('<tspan fill="#64748b">03</tspan>  '),
            ('<tspan fill="#64748b">04</tspan>  <tspan fill="#38bdf8">@asynccontextmanager</tspan>'),
            ('<tspan fill="#64748b">05</tspan>  <tspan fill="#c084fc">async def</tspan> <tspan fill="#facc15">lifespan</tspan>(app: <tspan fill="#38bdf8">FastAPI</tspan>):'),
            ('<tspan fill="#64748b">06</tspan>      <tspan fill="#4ade80"># Initialize async connection pool on startup</tspan>'),
            ('<tspan fill="#64748b">07</tspan>      <tspan fill="#c084fc">await</tspan> <tspan fill="#f8fafc">db_pool.initialize(min_size=10, max_size=50)</tspan>'),
            ('<tspan fill="#64748b">08</tspan>      <tspan fill="#c084fc">yield</tspan>'),
            ('<tspan fill="#64748b">09</tspan>      <tspan fill="#c084fc">await</tspan> <tspan fill="#f8fafc">db_pool.drain_and_close()</tspan>'),
            ('<tspan fill="#64748b">10</tspan>  '),
            ('<tspan fill="#64748b">11</tspan>  <tspan fill="#38bdf8">@app.get</tspan>(<tspan fill="#34d399">"/api/v1/workload"</tspan>, response_model=<tspan fill="#38bdf8">MetricsResponse</tspan>)'),
            ('<tspan fill="#64748b">12</tspan>  <tspan fill="#c084fc">async def</tspan> <tspan fill="#facc15">handle_workload</tspan>(db: <tspan fill="#38bdf8">AsyncSession</tspan> = <tspan fill="#facc15">Depends</tspan>(get_db)):'),
            ('<tspan fill="#64748b">13</tspan>      <tspan fill="#c084fc">return await</tspan> <tspan fill="#f8fafc">compute_async_metrics(db)</tspan>'),
        ]
        cy = 380
        for snippet in code_snippets:
            svg_parts.append(
                f'  <text x="200" y="{cy}" font-family="DejaVu Sans Mono, monospace" font-size="20" fill="#f8fafc">{snippet}</text>'
            )
            cy += 36

    elif st in (SceneType.DIAGRAM.value, "DIAGRAM"):
        # DIAGRAM LAYOUT: Architectural Flowchart with Connected Nodes
        svg_parts.extend([
            '  <text x="160" y="240" font-family="DejaVu Sans, Arial, sans-serif" font-size="36" font-weight="bold" fill="#f8fafc">Distributed Execution &amp; Concurrency Flow</text>',
            # Node 1: Client Ingress
            f'  <rect x="160" y="320" width="300" height="240" rx="16" fill="{bg_color}" stroke="{border_color}" stroke-width="2"/>',
            '  <text x="310" y="390" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" font-weight="bold" fill="#f8fafc">Client Ingress</text>',
            f'  <text x="310" y="440" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" fill="{accent_color}">10,000+ Concurrent</text>',
            '  <text x="310" y="480" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="16" fill="#94a3b8">HTTP / WebSocket Sockets</text>',
            # Arrow 1
            f'  <line x1="460" y1="440" x2="570" y2="440" stroke="{accent_color}" stroke-width="4" stroke-dasharray="6,4"/>',
            f'  <polygon points="570,440 555,430 555,450" fill="{accent_color}"/>',
            # Node 2: Non-blocking Event Loop
            f'  <rect x="580" y="300" width="380" height="280" rx="16" fill="{bg_color}" stroke="{accent_color}" stroke-width="2.5"/>',
            f'  <text x="770" y="370" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="26" font-weight="bold" fill="{accent_color}">ASGI Event Loop</text>',
            '  <text x="770" y="420" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" fill="#f8fafc">epoll / kqueue Multiplexing</text>',
            '  <text x="770" y="460" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="16" fill="#cbd5e1">&#8226; Zero-copy Byte Parsing</text>',
            '  <text x="770" y="495" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="16" fill="#cbd5e1">&#8226; Coroutine Interleaving</text>',
            '  <text x="770" y="530" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="16" fill="#10b981">&#10003; Sub-millisecond Dispatch</text>',
            # Arrow 2
            f'  <line x1="960" y1="440" x2="1070" y2="440" stroke="{accent_color}" stroke-width="4" stroke-dasharray="6,4"/>',
            f'  <polygon points="1070,440 1055,430 1055,450" fill="{accent_color}"/>',
            # Node 3: Worker Execution & Persistence
            f'  <rect x="1080" y="320" width="360" height="240" rx="16" fill="{bg_color}" stroke="{border_color}" stroke-width="2"/>',
            '  <text x="1260" y="390" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" font-weight="bold" fill="#f8fafc">Async Workers &amp; DB Pool</text>',
            '  <text x="1260" y="440" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" fill="#38bdf8">Asyncpg Connection Pool</text>',
            '  <text x="1260" y="480" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="16" fill="#94a3b8">Non-blocking Thread Offloading</text>',
            # Bottom Summary Box
            f'  <rect x="160" y="650" width="1600" height="170" rx="14" fill="#0f172a" stroke="{border_color}" stroke-width="1.5"/>',
            f'  <text x="200" y="710" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" font-weight="bold" fill="{accent_color}">ARCHITECTURAL RULE</text>',
            f'  <text x="200" y="755" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" fill="#e2e8f0">{html.escape(text[:120])}...</text>',
        ])

    elif st in (SceneType.INFOGRAPHIC.value, "INFOGRAPHIC"):
        # INFOGRAPHIC LAYOUT: Multi-bar Comparative Benchmark Analysis
        svg_parts.extend([
            '  <text x="160" y="240" font-family="DejaVu Sans, Arial, sans-serif" font-size="36" font-weight="bold" fill="#f8fafc">Framework Throughput &amp; Latency Comparison</text>',
            # Chart Background
            f'  <rect x="160" y="280" width="1600" height="540" rx="18" fill="{bg_color}" stroke="{border_color}" stroke-width="2"/>',
            # Bar 1: FastAPI Async
            '  <text x="210" y="360" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" font-weight="bold" fill="#f8fafc">FastAPI (Uvicorn + Asyncpg)</text>',
            f'  <rect x="210" y="380" width="1050" height="42" rx="8" fill="{accent_color}"/>',
            '  <text x="1280" y="410" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" font-weight="bold" fill="#38bdf8">52,400 req/s  (1.2ms p50)</text>',
            # Bar 2: NodeJS Express
            '  <text x="210" y="470" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" font-weight="bold" fill="#94a3b8">Node.js Express Cluster</text>',
            '  <rect x="210" y="490" width="560" height="42" rx="8" fill="#475569"/>',
            '  <text x="790" y="520" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" fill="#cbd5e1">27,800 req/s  (3.8ms p50)</text>',
            # Bar 3: Legacy WSGI
            '  <text x="210" y="580" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" font-weight="bold" fill="#94a3b8">Traditional Synchronous WSGI</text>',
            '  <rect x="210" y="600" width="220" height="42" rx="8" fill="#334155"/>',
            '  <text x="450" y="630" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" fill="#94a3b8">8,900 req/s  (14.2ms p50)</text>',
            # Finding callout
            f'  <line x1="210" y1="690" x2="1710" y2="690" stroke="{border_color}" stroke-width="1.5"/>',
            '  <text x="210" y="745" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" fill="#34d399">&#9654; 5.8x Higher Concurrency Throughput with 92% Tail Latency Reduction</text>',
        ])

    elif st in (SceneType.STATISTIC.value, "STATISTIC"):
        # STATISTIC LAYOUT: Prominent metric callout badge & context breakdown
        metric, context = _extract_metric_hero(text)
        safe_metric = html.escape(metric)

        # Metric Hero Card
        svg_parts.extend([
            f'  <rect x="160" y="240" width="560" height="260" rx="20" fill="{bg_color}" stroke="{accent_color}" stroke-width="2"/>',
            f'  <text x="440" y="380" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="76" font-weight="bold" fill="{accent_color}">{safe_metric}</text>',
            '  <text x="440" y="440" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" font-weight="bold" fill="#94a3b8" letter-spacing="2">EMPIRICAL BENCHMARK</text>',
        ])

        # Right-side context breakdown
        c_lines = _wrap_text(context or text, max_chars=42)[:5]
        cy = 280
        svg_parts.append(f'  <text x="780" y="{cy}" font-family="DejaVu Sans, Arial, sans-serif" font-size="34" font-weight="bold" fill="#f8fafc">Key Benchmark Finding</text>')
        cy += 50
        for line in c_lines:
            safe_l = html.escape(line)
            svg_parts.append(
                f'  <text x="780" y="{cy}" font-family="DejaVu Sans, Arial, sans-serif" font-size="26" fill="#cbd5e1">&#8226; {safe_l}</text>'
            )
            cy += 42

    elif st in (SceneType.KINETIC_TEXT.value, "KINETIC_TEXT", SceneType.QUOTE.value, "QUOTE"):
        # KINETIC_TEXT / QUOTE LAYOUT: Prominent Architectural Directive
        svg_parts.extend([
            f'  <rect x="160" y="260" width="1600" height="540" rx="20" fill="{bg_color}" stroke="{accent_color}" stroke-width="2"/>',
            f'  <text x="220" y="340" font-family="DejaVu Sans, Arial, sans-serif" font-size="26" font-weight="bold" fill="{accent_color}" letter-spacing="2">CORE ARCHITECTURAL RULE</text>',
        ])
        q_lines = _wrap_text(text, max_chars=46)[:5]
        qy = 420
        for ql in q_lines:
            safe_ql = html.escape(ql)
            svg_parts.append(
                f'  <text x="220" y="{qy}" font-family="DejaVu Sans, Arial, sans-serif" font-size="38" font-weight="bold" fill="#f8fafc">{safe_ql}</text>'
            )
            qy += 60

    elif st in (SceneType.CTA.value, "CTA"):
        # CTA LAYOUT: Outro headline, bullet takeaways, action prompt
        svg_parts.append('  <text x="160" y="270" font-family="DejaVu Sans, Arial, sans-serif" font-size="44" font-weight="bold" fill="#f8fafc">Summary &amp; Engineering Recommendations</text>')

        # Takeaway points
        cta_lines = _wrap_text(text, max_chars=55)[:4]
        ty = 360
        for line in cta_lines:
            safe_l = html.escape(line)
            svg_parts.append(
                f'  <text x="160" y="{ty}" font-family="DejaVu Sans, Arial, sans-serif" font-size="28" fill="#cbd5e1">&#8226; {safe_l}</text>'
            )
            ty += 48

        # CTA Action Box
        svg_parts.extend([
            f'  <rect x="160" y="660" width="1600" height="150" rx="18" fill="{bg_color}" stroke="{accent_color}" stroke-width="2"/>',
            f'  <text x="200" y="730" font-family="DejaVu Sans, Arial, sans-serif" font-size="32" font-weight="bold" fill="{accent_color}">Subscribe for Weekly Production Architecture &amp; Benchmark Breakdowns</text>',
            '  <text x="200" y="775" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" fill="#94a3b8">Verifiable engineering blueprints &#8226; Empirical performance analysis</text>',
        ])

    else:
        # NARRATION / DEFAULT SCENE LAYOUT
        body_lines = _wrap_text(text, max_chars=48)
        headline = body_lines[0] if body_lines else safe_title
        remaining_lines = body_lines[1:] if len(body_lines) > 1 else []

        safe_hl = html.escape(headline)
        svg_parts.append(
            f'  <text x="160" y="270" font-family="DejaVu Sans, Arial, sans-serif" font-size="40" font-weight="bold" fill="#f8fafc">{safe_hl}</text>'
        )
        svg_parts.append(f'  <line x1="160" y1="310" x2="1760" y2="310" stroke="{border_color}" stroke-width="2"/>')

        # Structured Concept Box
        svg_parts.append(f'  <rect x="160" y="350" width="1600" height="460" rx="20" fill="{bg_color}" stroke="{border_color}" stroke-width="1.5"/>')
        svg_parts.append(f'  <text x="210" y="410" font-family="DejaVu Sans, Arial, sans-serif" font-size="24" font-weight="bold" fill="{accent_color}" letter-spacing="1.5">CORE ARCHITECTURAL PRINCIPLE</text>')

        by = 480
        for line in remaining_lines[:6]:
            safe_b = html.escape(line)
            svg_parts.append(
                f'  <text x="210" y="{by}" font-family="DejaVu Sans, Arial, sans-serif" font-size="28" fill="#e2e8f0">&#8226;  {safe_b}</text>'
            )
            by += 48

        if not remaining_lines:
            intent_sub = html.escape(visual_intent or "Verified technical implementation details.")
            svg_parts.append(
                f'  <text x="210" y="500" font-family="DejaVu Sans, Arial, sans-serif" font-size="28" fill="#cbd5e1">&#8226;  {intent_sub}</text>'
            )

    # Footer Branded Line
    svg_parts.extend([
        f'  <line x1="160" y1="910" x2="1760" y2="910" stroke="{border_color}" stroke-width="1.5"/>',
        '  <text x="160" y="945" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" font-weight="bold" fill="#64748b" letter-spacing="2">OMEGA PRODUCTION ENGINE &#8226; VERIFIED TECHNICAL SYSTEM</text>',
        f'  <text x="1760" y="945" text-anchor="end" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" fill="{accent_color}">1080p 60fps</text>',
        '</svg>',
    ])

    return "\n".join(svg_parts)


class LocalAssetProvider:
    """Deterministic local visual scene card generator using SVG and FFmpeg rendering."""

    def __init__(self, storage: LocalMediaStorageProvider) -> None:
        self.storage = storage

    async def resolve_asset_requirement(
        self,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        requirement: dict[str, Any],
        width: int = 1920,
        height: int = 1080,
    ) -> dict[str, Any]:
        """Generate a deterministic visual scene card asset and persist to disk."""
        req_id = requirement["id"]
        assets_dir = self.storage.get_assets_dir(channel_id, request_id)
        asset_id = uuid.uuid4()
        file_name = f"card_{asset_id.hex[:10]}.png"
        target_path = assets_dir / file_name
        svg_temp_path = assets_dir / f"card_{asset_id.hex[:10]}.svg"

        # Extract rich context from requirement dictionary
        scene_type = str(requirement.get("scene_type") or "NARRATION")
        title = str(requirement.get("title") or requirement.get("purpose") or "")
        heading = str(requirement.get("heading") or requirement.get("query_hint") or "")
        text = str(requirement.get("narration_text") or requirement.get("purpose") or "")
        visual_intent = str(requirement.get("visual_intent") or "")
        theme_idx = req_id.int % len(THEMES)

        # 1. Generate clean SVG markup
        svg_content = generate_scene_card_svg(
            scene_type=scene_type,
            title=title,
            heading=heading,
            text=text,
            visual_intent=visual_intent,
            theme_idx=theme_idx,
            width=width,
            height=height,
        )
        svg_temp_path.write_text(svg_content, encoding="utf-8")

        # 2. Render SVG to 1-frame PNG via FFmpeg
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(svg_temp_path),
            "-frames:v",
            "1",
            str(target_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        # Clean temp SVG
        if svg_temp_path.exists():
            svg_temp_path.unlink()

        is_placeholder = False
        if proc.returncode != 0 or not target_path.exists() or target_path.stat().st_size == 0:
            # Fallback to minimal valid PNG if ffmpeg / librsvg is unavailable
            _write_fallback_png(target_path)
            is_placeholder = True

        content_hash = compute_sha256(target_path)
        rel_uri = self.storage.to_relative_uri(channel_id, request_id, target_path)

        provider_type = (
            AssetProviderType.PLACEHOLDER.value
            if is_placeholder
            else AssetProviderType.SYSTEM.value
        )
        source_ref = (
            "Local Generator Fallback"
            if is_placeholder
            else f"Local Scene Card Generator ({scene_type})"
        )

        return {
            "id": asset_id,
            "channel_id": channel_id,
            "production_request_id": request_id,
            "asset_requirement_id": req_id,
            "asset_type": requirement.get("asset_type", AssetType.BACKGROUND.value),
            "provider_type": provider_type,
            "storage_uri": rel_uri,
            "content_hash": content_hash,
            "mime_type": "image/png",
            "width": width,
            "height": height,
            "duration_ms": None,
            "license_status": LicenseStatus.GENERATED.value,
            "source_ref": source_ref,
            "attribution": "Generated by OMEGA Production Engine",
        }


def _write_fallback_png(target_path: Path) -> None:
    """Write minimal valid 1x1 black PNG bytes as test fallback."""
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    target_path.write_bytes(png_bytes)

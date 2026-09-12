"""Deterministic text layout decisions for fixed-size production templates."""

from __future__ import annotations

import textwrap

from pydantic import BaseModel, ConfigDict


class TextFittingDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    source_text: str
    rendered_text: str
    font_size: int
    line_count: int
    max_lines: int
    text_truncated: bool


def fit_text(
    text: str,
    *,
    role: str,
    initial_font_size: int,
    min_font_size: int,
    max_lines: int,
    chars_per_line_at_initial_size: int,
) -> TextFittingDecision:
    """Apply wrap -> downscale -> explicit ellipsis fallback in a stable order."""
    if min_font_size <= 0 or initial_font_size < min_font_size:
        raise ValueError("invalid text fitting font bounds")
    if max_lines <= 0 or chars_per_line_at_initial_size <= 0:
        raise ValueError("invalid text fitting layout bounds")

    normalized = " ".join(str(text).split())
    size = initial_font_size
    lines: list[str] = []
    while size >= min_font_size:
        width = max(4, int(chars_per_line_at_initial_size * initial_font_size / size))
        lines = textwrap.wrap(
            normalized,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        ) or [""]
        if len(lines) <= max_lines and all(len(line) <= width for line in lines):
            return TextFittingDecision(
                role=role,
                source_text=normalized,
                rendered_text="\n".join(lines),
                font_size=size,
                line_count=len(lines),
                max_lines=max_lines,
                text_truncated=False,
            )
        size -= 2

    width = max(4, int(chars_per_line_at_initial_size * initial_font_size / min_font_size))
    expanded: list[str] = []
    for line in lines:
        if len(line) <= width:
            expanded.append(line)
        else:
            expanded.extend(line[i : i + width] for i in range(0, len(line), width))
    kept = expanded[:max_lines]
    if kept:
        kept[-1] = kept[-1].rstrip(" .…") + "…"
    return TextFittingDecision(
        role=role,
        source_text=normalized,
        rendered_text="\n".join(kept),
        font_size=min_font_size,
        line_count=len(kept),
        max_lines=max_lines,
        text_truncated=True,
    )

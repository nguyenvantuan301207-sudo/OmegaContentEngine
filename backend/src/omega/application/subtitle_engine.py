"""Subtitle generation, SRT formatting, and file export."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.domain.production import (
    AssetProviderType,
    AssetType,
    LicenseStatus,
)


class SubtitleRenderStyle(BaseModel):
    """Canonical render-time subtitle controls supported by the ASS pipeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    font_family: str = "Arial"
    font_size: int = Field(default=48, ge=24, le=96)
    min_font_size: int = Field(default=32, ge=20, le=96)
    bold: bool = False
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = Field(default=2.0, ge=0, le=8)
    shadow: float = Field(default=2.0, ge=0, le=8)
    background_box: bool = False
    alignment: int = Field(default=2, ge=1, le=9)
    margin_v: int = Field(default=80, ge=0, le=400)
    max_lines: int = Field(default=2, ge=1, le=3)
    max_width_ratio: float = Field(default=0.82, ge=0.4, le=0.95)

    @field_validator("font_family")
    @classmethod
    def _safe_font_family(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,64}", value):
            raise ValueError("font_family contains unsupported characters")
        return value

    @field_validator("primary_color", "outline_color")
    @classmethod
    def _safe_rgb_color(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", value):
            raise ValueError("subtitle colors must use #RRGGBB")
        return value


class SubtitleLayoutDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    cue_order: int
    resolved_font_size: int
    line_count: int
    text_truncated: bool


class KaraokeASSDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    style: SubtitleRenderStyle
    layout: tuple[SubtitleLayoutDecision, ...]


def _ass_color(rgb: str) -> str:
    """Convert validated #RRGGBB to opaque ASS &H00BBGGRR."""
    return f"&H00{rgb[5:7]}{rgb[3:5]}{rgb[1:3]}"


def _escape_ass_text(value: Any) -> str:
    """Neutralize user-authored ASS override/control syntax."""
    clean = str(value).replace("\r", " ").replace("\n", " ")
    clean = clean.replace("\\", "／").replace("{", "（").replace("}", "）")
    return " ".join(clean.split())


def _layout_karaoke_words(
    words: list[dict[str, Any]],
    *,
    width: int,
    style: SubtitleRenderStyle,
) -> tuple[list[list[dict[str, Any]]], int, bool]:
    """Deterministically wrap, downscale, then truncate only as a final fallback."""
    safe_words = [dict(word, text=_escape_ass_text(word.get("text", ""))) for word in words]
    resolved_size = style.font_size
    lines: list[list[dict[str, Any]]] = []

    while resolved_size >= style.min_font_size:
        chars_per_line = max(8, int((width * style.max_width_ratio) / (resolved_size * 0.58)))
        candidate: list[list[dict[str, Any]]] = [[]]
        current_chars = 0
        for word in safe_words:
            word_len = len(str(word["text"]))
            added = word_len if not candidate[-1] else word_len + 1
            if candidate[-1] and current_chars + added > chars_per_line:
                candidate.append([])
                current_chars = 0
                added = word_len
            candidate[-1].append(word)
            current_chars += added
        lines = candidate
        if len(lines) <= style.max_lines and all(
            sum(len(str(word["text"])) for word in line) + max(0, len(line) - 1)
            <= chars_per_line
            for line in lines
        ):
            return lines, resolved_size, False
        resolved_size -= 2

    resolved_size = style.min_font_size
    chars_per_line = max(8, int((width * style.max_width_ratio) / (resolved_size * 0.58)))
    token_truncated = False
    for line in lines:
        for word in line:
            if len(str(word["text"])) > chars_per_line:
                word["text"] = str(word["text"])[: max(1, chars_per_line - 1)] + "…"
                token_truncated = True
    kept = lines[: style.max_lines]
    omitted_duration = sum(
        int(word.get("duration_ms", 0)) for line in lines[style.max_lines:] for word in line
    )
    if omitted_duration and kept and kept[-1]:
        kept[-1][-1]["duration_ms"] = int(kept[-1][-1].get("duration_ms", 0)) + omitted_duration
        kept[-1][-1]["centiseconds"] = int(kept[-1][-1].get("centiseconds", 0)) + round(omitted_duration / 10)
        kept[-1][-1]["text"] = str(kept[-1][-1]["text"]).rstrip("…") + "…"
    return kept, resolved_size, bool(omitted_duration) or token_truncated


def format_srt_time(ms: int) -> str:
    """Format milliseconds into SRT timestamp format: HH:MM:SS,mmm."""
    ms = max(ms, 0)
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

def format_ass_time(ms: int) -> str:
    """Format milliseconds into ASS timestamp format: H:MM:SS.cc"""
    ms = max(ms, 0)
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    cs = (ms % 1000) // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def generate_srt_content(subtitle_cues: list[dict[str, Any]]) -> str:
    """Serialize subtitle cues into canonical SubRip (.srt) string."""
    blocks: list[str] = []
    for cue in sorted(subtitle_cues, key=lambda c: int(c.get("cue_order", 0))):
        order = cue.get("cue_order", 1)
        start_time = format_srt_time(int(cue.get("start_ms", 0)))
        end_time = format_srt_time(int(cue.get("end_ms", 0)))
        text = str(cue.get("text", "")).strip()

        block = f"{order}\n{start_time} --> {end_time}\n{text}\n"
        blocks.append(block)

    return "\n".join(blocks)

def generate_karaoke_cues(
    segments: list[dict[str, Any]],
    *,
    max_words_per_cue: int = 5,
    max_chars_per_cue: int = 36,
) -> list[dict[str, Any]]:
    cues = []
    cue_order = 1
    previous_end_ms = -1
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        start_ms = int(segment.get("start_ms", 0))
        duration_ms = int(segment.get("duration_ms", 0))
        if not text:
            raise ValueError("Blank narration text in segment")
        if duration_ms <= 0:
            raise ValueError("Segment duration must be > 0")
        if start_ms < previous_end_ms:
            raise ValueError("Overlapping segments are not allowed")
        previous_end_ms = start_ms + duration_ms
        text = " ".join(text.split())
        words = text.split(" ")
        if duration_ms < len(words):
            raise ValueError("duration_ms must be >= number of words")
        weights = []
        for w in words:
            alpha_chars = sum(1 for c in w if c.isalnum())
            base_wt = max(alpha_chars, 1)
            if any(p in w for p in [',', ';', ':']):
                base_wt += 2
            if any(p in w for p in ['.', '?', '!']):
                base_wt += 4
            weights.append(base_wt)
        total_weight = sum(weights)
        word_durations = []
        accumulated_duration = 0
        for i, wt in enumerate(weights):
            if i == len(weights) - 1:
                wd = duration_ms - accumulated_duration
            else:
                wd = int(round(duration_ms * wt / total_weight))
            wd = max(wd, 1)
            word_durations.append(wd)
            accumulated_duration += wd
        diff = sum(word_durations) - duration_ms
        if diff > 0:
            for i in range(len(word_durations)-1, -1, -1):
                if word_durations[i] > diff + 1:
                    word_durations[i] -= diff
                    diff = 0
                    break
                elif word_durations[i] > 1:
                    dec = word_durations[i] - 1
                    word_durations[i] = 1
                    diff -= dec
        elif diff < 0:
            word_durations[-1] += -diff
        current_cue_words = []
        current_chars = 0
        current_start = start_ms
        current_cue_duration = 0
        for w, wd in zip(words, word_durations, strict=True):
            word_len = len(w)
            if current_cue_words and (
                len(current_cue_words) >= max_words_per_cue
                or (current_chars + 1 + word_len) > max_chars_per_cue
            ):
                cues.append(
                    {
                        "cue_order": cue_order,
                        "start_ms": current_start,
                        "end_ms": current_start + current_cue_duration,
                        "text": " ".join(item["text"] for item in current_cue_words),
                        "words": current_cue_words,
                    }
                )
                cue_order += 1
                current_start += current_cue_duration
                current_cue_words = []
                current_cue_duration = 0
                current_chars = 0
            chars_to_add = word_len if not current_cue_words else word_len + 1
            current_cue_words.append({"text": w, "duration_ms": wd})
            current_chars += chars_to_add
            current_cue_duration += wd
        if current_cue_words:
            cues.append({
                "cue_order": cue_order,
                "start_ms": current_start,
                "end_ms": current_start + current_cue_duration,
                "text": " ".join(cw["text"] for cw in current_cue_words),
                "words": current_cue_words
            })
            cue_order += 1
    return cues

def generate_karaoke_ass_document(
    karaoke_cues: list[dict[str, Any]],
    *,
    width: int = 1920,
    height: int = 1080,
    style: SubtitleRenderStyle | None = None,
) -> KaraokeASSDocument:
    resolved_style = style or SubtitleRenderStyle()
    if resolved_style.min_font_size > resolved_style.font_size:
        raise ValueError("min_font_size must not exceed font_size")
    primary = _ass_color(resolved_style.primary_color)
    outline = _ass_color(resolved_style.outline_color)
    bold = -1 if resolved_style.bold else 0
    border_style = 3 if resolved_style.background_box else 1
    header = f"[Script Info]\n" \
             f"ScriptType: v4.00+\n" \
             f"PlayResX: {width}\n" \
             f"PlayResY: {height}\n" \
             f"WrapStyle: 1\n\n" \
             f"[V4+ Styles]\n" \
             f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n" \
             f"Style: OMEGA_KARAOKE,{resolved_style.font_family},{resolved_style.font_size},{primary},&H000000FF,{outline},&H80000000,{bold},0,0,0,100,100,0,0,{border_style},{resolved_style.outline_width:g},{resolved_style.shadow:g},{resolved_style.alignment},30,30,{resolved_style.margin_v},1\n\n" \
             f"[Events]\n" \
             f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    events = []
    decisions: list[SubtitleLayoutDecision] = []
    for cue in karaoke_cues:
        start_ass = format_ass_time(cue["start_ms"])
        end_ass = format_ass_time(cue["end_ms"])
        event_duration_cs = int(round((cue["end_ms"] - cue["start_ms"]) / 10))
        word_cs_list = [w["duration_ms"] / 10.0 for w in cue["words"]]
        allocated = []
        fractions = []
        for i, val in enumerate(word_cs_list):
            int_val = int(val)
            frac = val - int_val
            allocated.append(int_val)
            fractions.append((frac, -i))
        diff = event_duration_cs - sum(allocated)
        if diff > 0:
            fractions.sort(reverse=True)
            for i in range(diff):
                idx = -fractions[i][1]
                allocated[idx] += 1
        elif diff < 0:
            # Although rare since we floor positive values, handle just in case
            allocated[-1] += diff

        if sum(allocated) != event_duration_cs:
            raise ValueError("Centisecond allocation failed to conserve duration")

        timed_words = [
            {"text": word["text"], "duration_ms": allocated[i] * 10, "centiseconds": allocated[i]}
            for i, word in enumerate(cue["words"])
        ]
        lines, resolved_size, truncated = _layout_karaoke_words(
            timed_words, width=width, style=resolved_style
        )
        dialogue_lines = [
            " ".join(f"{{\\kf{word['centiseconds']}}}{word['text']}" for word in line)
            for line in lines
        ]
        dialogue_text = r"\N".join(dialogue_lines)
        decisions.append(
            SubtitleLayoutDecision(
                cue_order=int(cue.get("cue_order", len(decisions) + 1)),
                resolved_font_size=resolved_size,
                line_count=len(lines),
                text_truncated=truncated,
            )
        )
        if resolved_size != resolved_style.font_size:
            dialogue_text = f"{{\\fs{resolved_size}}}{dialogue_text}"
        events.append(f"Dialogue: 0,{start_ass},{end_ass},OMEGA_KARAOKE,,0,0,0,,{dialogue_text}")
    return KaraokeASSDocument(
        content=header + "\n".join(events) + "\n",
        style=resolved_style,
        layout=tuple(decisions),
    )


def generate_karaoke_ass_content(
    karaoke_cues: list[dict[str, Any]],
    *,
    width: int = 1920,
    height: int = 1080,
    style: SubtitleRenderStyle | None = None,
) -> str:
    return generate_karaoke_ass_document(
        karaoke_cues, width=width, height=height, style=style
    ).content


class SubtitleEngine:
    """Engine for writing subtitle files to media storage."""

    def __init__(self, storage: LocalMediaStorageProvider) -> None:
        self.storage = storage

    def export_srt_file(
        self,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        subtitle_cues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate and save `.srt` file and return asset metadata."""
        subtitles_dir = self.storage.get_subtitles_dir(channel_id, request_id)
        asset_id = uuid.uuid4()
        target_path = subtitles_dir / f"subtitles_{asset_id.hex[:10]}.srt"

        srt_text = generate_srt_content(subtitle_cues)
        target_path.write_text(srt_text, encoding="utf-8")

        content_hash = compute_sha256(target_path)
        rel_uri = self.storage.to_relative_uri(channel_id, request_id, target_path)

        return {
            "id": asset_id,
            "channel_id": channel_id,
            "production_request_id": request_id,
            "asset_requirement_id": None,
            "asset_type": AssetType.SUBTITLE.value
            if hasattr(AssetType, "SUBTITLE")
            else "SUBTITLE",
            "provider_type": AssetProviderType.SYSTEM.value,
            "storage_uri": rel_uri,
            "content_hash": content_hash,
            "mime_type": "application/x-subrip",
            "width": None,
            "height": None,
            "duration_ms": None,
            "license_status": LicenseStatus.OWNED.value,
            "source_ref": "Generated Subtitles",
            "attribution": "Generated by OMEGA Subtitle Engine",
        }

    def export_ass_file(
        self,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        karaoke_cues: list[dict[str, Any]],
        style: SubtitleRenderStyle | None = None,
    ) -> dict[str, Any]:
        """Generate and save `.ass` file and return asset metadata."""
        subtitles_dir = self.storage.get_subtitles_dir(channel_id, request_id)
        asset_id = uuid.uuid4()
        target_path = subtitles_dir / f"subtitles_{asset_id.hex[:10]}.ass"
        ass_text = generate_karaoke_ass_content(karaoke_cues, style=style)
        target_path.write_text(ass_text, encoding="utf-8")
        content_hash = compute_sha256(target_path)
        rel_uri = self.storage.to_relative_uri(channel_id, request_id, target_path)
        return {
            "id": asset_id,
            "channel_id": channel_id,
            "production_request_id": request_id,
            "asset_requirement_id": None,
            "asset_type": AssetType.SUBTITLE.value if hasattr(AssetType, "SUBTITLE") else "SUBTITLE",
            "provider_type": AssetProviderType.SYSTEM.value,
            "storage_uri": rel_uri,
            "content_hash": content_hash,
            "mime_type": "text/x-ssa",
            "width": None,
            "height": None,
            "duration_ms": None,
            "license_status": LicenseStatus.OWNED.value,
            "source_ref": "OMEGA Karaoke Subtitles",
            "attribution": "Generated by OMEGA Subtitle Engine",
        }

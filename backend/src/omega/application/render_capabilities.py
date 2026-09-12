"""Read-only renderer capability contract for Production Studio clients."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from omega.application.subtitle_engine import SubtitleRenderStyle

TruthState = Literal["RENDER_APPLIED", "PREVIEW_ONLY", "UNSUPPORTED"]


class SubtitleFieldCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    truth_state: TruthState = "RENDER_APPLIED"
    user_editable: bool = True


class SubtitleCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    defaults: SubtitleRenderStyle
    fields: tuple[SubtitleFieldCapability, ...]
    font_families: tuple[str, ...]
    alignments: dict[int, str]


class TextFittingCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    wrap: bool
    font_downscale: bool
    truncation_fallback: bool
    truncation_provenance: bool


class VideoCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    fps_mode: Literal["CFR"]
    target_fps: int
    user_editable: bool


class ProductionRenderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    truth_states: tuple[TruthState, ...]
    subtitle: SubtitleCapabilities
    text_fitting: TextFittingCapabilities
    video: VideoCapabilities
    subtitle_timing_label: str


def get_production_render_capabilities() -> ProductionRenderCapabilities:
    fields = tuple(
        SubtitleFieldCapability(
            name=name,
            user_editable=name != "min_font_size",
        )
        for name in SubtitleRenderStyle.model_fields
    )
    return ProductionRenderCapabilities(
        truth_states=("RENDER_APPLIED", "PREVIEW_ONLY", "UNSUPPORTED"),
        subtitle=SubtitleCapabilities(
            defaults=SubtitleRenderStyle(),
            fields=fields,
            font_families=("Arial", "DejaVu Sans"),
            alignments={
                1: "Bottom left",
                2: "Bottom center",
                3: "Bottom right",
                5: "Center",
                8: "Top center",
            },
        ),
        text_fitting=TextFittingCapabilities(
            wrap=True,
            font_downscale=True,
            truncation_fallback=True,
            truncation_provenance=True,
        ),
        video=VideoCapabilities(fps_mode="CFR", target_fps=12, user_editable=False),
        subtitle_timing_label="Estimated word timing",
    )

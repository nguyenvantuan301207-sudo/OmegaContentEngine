"""Renderer-neutral camera and transition contracts for P22-B."""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CameraIntent(enum.StrEnum):
    STATIC = "STATIC"
    PUSH_IN = "PUSH_IN"
    PULL_OUT = "PULL_OUT"
    PAN_LEFT = "PAN_LEFT"
    PAN_RIGHT = "PAN_RIGHT"
    PAN_UP = "PAN_UP"
    PAN_DOWN = "PAN_DOWN"
    REFRAME = "REFRAME"
    TRACK_SUBJECT = "TRACK_SUBJECT"
    DETAIL_FOCUS = "DETAIL_FOCUS"
    RETURN_TO_CONTEXT = "RETURN_TO_CONTEXT"


class MotionStrength(enum.StrEnum):
    SUBTLE = "SUBTLE"
    MODERATE = "MODERATE"
    EMPHATIC = "EMPHATIC"


class EasingProfile(enum.StrEnum):
    LINEAR = "LINEAR"
    SMOOTHSTEP = "SMOOTHSTEP"


class TransitionIntent(enum.StrEnum):
    CUT = "CUT"
    CROSSFADE = "CROSSFADE"
    FADE_THROUGH = "FADE_THROUGH"
    MATCH_CONTINUITY = "MATCH_CONTINUITY"
    HARD_CONTEXT_SWITCH = "HARD_CONTEXT_SWITCH"


class MotionFindingCode(enum.StrEnum):
    EXCESSIVE_CAMERA_MOTION = "EXCESSIVE_CAMERA_MOTION"
    MOTION_WITHOUT_EDITORIAL_PURPOSE = "MOTION_WITHOUT_EDITORIAL_PURPOSE"
    RAPID_DIRECTION_REVERSAL = "RAPID_DIRECTION_REVERSAL"
    MOTION_TOO_FAST = "MOTION_TOO_FAST"
    MOTION_TOO_SLOW = "MOTION_TOO_SLOW"
    CAMERA_CHANGE_TOO_FREQUENT = "CAMERA_CHANGE_TOO_FREQUENT"


class FocusRegion(BaseModel):
    """Normalized, bounded region of interest in source-frame coordinates."""

    model_config = ConfigDict(frozen=True)

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    label: str | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> FocusRegion:
        if self.x + self.width > 1.0 + 1e-9 or self.y + self.height > 1.0 + 1e-9:
            raise ValueError("focus region must remain inside normalized frame bounds")
        return self

    @classmethod
    def clamp(
        cls, *, x: float, y: float, width: float, height: float, label: str | None = None
    ) -> FocusRegion:
        """Construct a safe region from untrusted coordinates without negative crop geometry."""
        safe_width = min(1.0, max(0.05, float(width)))
        safe_height = min(1.0, max(0.05, float(height)))
        safe_x = min(1.0 - safe_width, max(0.0, float(x)))
        safe_y = min(1.0 - safe_height, max(0.0, float(y)))
        return cls(x=safe_x, y=safe_y, width=safe_width, height=safe_height, label=label)


class SafeArea(BaseModel):
    model_config = ConfigDict(frozen=True)

    left: float = Field(default=0.05, ge=0.0, le=0.25)
    right: float = Field(default=0.05, ge=0.0, le=0.25)
    top: float = Field(default=0.05, ge=0.0, le=0.25)
    bottom: float = Field(default=0.08, ge=0.0, le=0.25)


class CameraFrameState(BaseModel):
    model_config = ConfigDict(frozen=True)

    scale: float = Field(ge=1.0, le=1.2)
    center_x: float = Field(ge=0.0, le=1.0)
    center_y: float = Field(ge=0.0, le=1.0)


class CameraPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    visual_beat_id: UUID
    parent_scene_index: int = Field(ge=1)
    beat_index: int = Field(ge=0)
    source_editorial_beat_indices: tuple[int, ...]
    duration_ms: int = Field(gt=0)
    preferred_asset_type: str
    intent: CameraIntent
    strength: MotionStrength
    focus_region: FocusRegion | None = None
    start_state: CameraFrameState
    end_state: CameraFrameState
    easing: EasingProfile = EasingProfile.SMOOTHSTEP
    safe_area: SafeArea = Field(default_factory=SafeArea)
    editorial_purpose: str


class TransitionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous_visual_beat_id: UUID | None = None
    visual_beat_id: UUID
    parent_scene_index: int = Field(ge=1)
    beat_index: int = Field(ge=0)
    source_editorial_beat_indices: tuple[int, ...]
    requested_intent: TransitionIntent
    applied_intent: TransitionIntent
    duration_ms: int = Field(ge=0, le=600)
    fallback_reason: str | None = None


class MotionFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: MotionFindingCode
    affected_beat_indices: tuple[int, ...]
    explanation: str


class CameraTransitionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1)
    camera_plans: tuple[CameraPlan, ...]
    transition_plans: tuple[TransitionPlan, ...]
    findings: tuple[MotionFinding, ...] = ()

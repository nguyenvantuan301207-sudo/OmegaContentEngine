"""Application-layer Deterministic Physical Camera Motion Authority for Visual Beats.

Provides:
- Shared immutable camera motion contracts (CameraMotionProfile, CameraMotionState);
- Deterministic resolution of BeatMotionIntent into restrained cinematic profiles;
- Smoothstep evaluation authority for progress in [0, 1];
- Pure CSS camera style injection for IMAGE_EXPLAINER frames (#image-element);
- Deterministic FFmpeg zoompan filter generator for BROLL backgrounds.

Zero randomness, zero clock dependencies, zero external provider calls.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omega.application.editorial_beat import BeatMotionIntent


class CameraMotionProfile(BaseModel):
    """Immutable parameters defining a camera motion trajectory."""

    model_config = ConfigDict(frozen=True)

    intent: BeatMotionIntent = Field(description="Underlying beat motion intent")
    start_scale: float = Field(ge=1.0, le=1.2, description="Normalized scale at start")
    end_scale: float = Field(ge=1.0, le=1.2, description="Normalized scale at end")
    start_x_offset: float = Field(
        ge=-1.0, le=1.0, description="Normalized horizontal offset at start"
    )
    end_x_offset: float = Field(
        ge=-1.0, le=1.0, description="Normalized horizontal offset at end"
    )
    start_y_offset: float = Field(
        ge=-1.0, le=1.0, description="Normalized vertical offset at start"
    )
    end_y_offset: float = Field(
        ge=-1.0, le=1.0, description="Normalized vertical offset at end"
    )


class CameraMotionState(BaseModel):
    """Evaluated camera transform state at an instant in time."""

    model_config = ConfigDict(frozen=True)

    scale: float = Field(ge=1.0, le=1.2, description="Current scale factor")
    x_offset: float = Field(ge=-1.0, le=1.0, description="Current normalized x offset")
    y_offset: float = Field(ge=-1.0, le=1.0, description="Current normalized y offset")


def smoothstep(progress: float) -> float:
    """Evaluate smoothstep easing curve p^2 * (3 - 2p) with progress clamped to [0, 1]."""
    p = max(0.0, min(1.0, float(progress)))
    return p * p * (3.0 - 2.0 * p)


def resolve_camera_motion_profile(intent: BeatMotionIntent) -> CameraMotionProfile:
    """Deterministically map BeatMotionIntent to a restrained CameraMotionProfile.

    Restrained cinematic motion bounds:
    - STATIC: 1.00 -> 1.00, center anchored.
    - SLOW_PUSH_IN: 1.00 -> 1.04, center anchored.
    - SLOW_PULL_OUT: 1.04 -> 1.00, center anchored.
    - PAN_LEFT: constant scale 1.04, subtle right-to-left shift in viewport authority (+0.012 -> -0.012).
    - PAN_RIGHT: constant scale 1.04, subtle left-to-right shift (-0.012 -> +0.012).
    - DRIFT: constant scale 1.03, subtle diagonal drift (x: -0.006 -> +0.006, y: -0.004 -> +0.004).
    - FOCAL_ZOOM: 1.00 -> 1.06, center-biased (zero semantic subject assumptions).
    """
    if intent == BeatMotionIntent.STATIC:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.00,
            end_scale=1.00,
            start_x_offset=0.0,
            end_x_offset=0.0,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )
    if intent == BeatMotionIntent.SLOW_PUSH_IN:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.00,
            end_scale=1.04,
            start_x_offset=0.0,
            end_x_offset=0.0,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )
    if intent == BeatMotionIntent.SLOW_PULL_OUT:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.04,
            end_scale=1.00,
            start_x_offset=0.0,
            end_x_offset=0.0,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )
    if intent == BeatMotionIntent.PAN_LEFT:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.04,
            end_scale=1.04,
            start_x_offset=0.012,
            end_x_offset=-0.012,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )
    if intent == BeatMotionIntent.PAN_RIGHT:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.04,
            end_scale=1.04,
            start_x_offset=-0.012,
            end_x_offset=0.012,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )
    if intent == BeatMotionIntent.DRIFT:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.03,
            end_scale=1.03,
            start_x_offset=-0.006,
            end_x_offset=0.006,
            start_y_offset=-0.004,
            end_y_offset=0.004,
        )
    if intent == BeatMotionIntent.FOCAL_ZOOM:
        return CameraMotionProfile(
            intent=intent,
            start_scale=1.00,
            end_scale=1.06,
            start_x_offset=0.0,
            end_x_offset=0.0,
            start_y_offset=0.0,
            end_y_offset=0.0,
        )

    # Fallback to safe static profile for unknown or unhandled intents
    return CameraMotionProfile(
        intent=intent,
        start_scale=1.00,
        end_scale=1.00,
        start_x_offset=0.0,
        end_x_offset=0.0,
        start_y_offset=0.0,
        end_y_offset=0.0,
    )


def evaluate_camera_motion(
    profile: CameraMotionProfile,
    progress: float,
) -> CameraMotionState:
    """Deterministically evaluate camera motion state at a normalized progress point."""
    eased = smoothstep(progress)
    scale = profile.start_scale + (profile.end_scale - profile.start_scale) * eased
    x_offset = (
        profile.start_x_offset + (profile.end_x_offset - profile.start_x_offset) * eased
    )
    y_offset = (
        profile.start_y_offset + (profile.end_y_offset - profile.start_y_offset) * eased
    )

    return CameraMotionState(
        scale=round(scale, 6),
        x_offset=round(x_offset, 6),
        y_offset=round(y_offset, 6),
    )


def inject_camera_motion_style(html: str, state: CameraMotionState) -> str:
    """Inject camera motion transform targeting exclusively #image-element.

    Ensures:
    - #image-element receives transform: translate(...) scale(...);
    - transform-origin is center center;
    - #image-frame, text, and scene-root remain untouched;
    - Uses distinct <style id="omega-camera-motion-state"> block separate from
      omega-dom-motion-state;
    - Replaces prior omega-camera-motion-state if already present.
    """
    style_content = (
        f'<style id="omega-camera-motion-state">\n'
        f"#image-element {{\n"
        f"  transform: translate({state.x_offset * 100:.4f}%, {state.y_offset * 100:.4f}%) scale({state.scale:.4f});\n"
        f"  transform-origin: center center;\n"
        f"}}\n"
        f"</style>"
    )

    start_tag = '<style id="omega-camera-motion-state">'
    end_tag = "</style>"

    # Deterministically strip any existing camera motion style block(s) to guarantee exactly 1 block
    while start_tag in html:
        before, rest = html.split(start_tag, 1)
        if end_tag in rest:
            _inner, after = rest.split(end_tag, 1)
            html = before + after
        else:
            html = before
            break

    if "</head>" in html:
        return html.replace("</head>", f"{style_content}\n</head>", 1)

    return html + f"\n{style_content}"


def build_broll_zoompan_filter(
    profile: CameraMotionProfile,
    *,
    total_frames: int,
    fps: int,
) -> str:
    """Build a deterministic FFmpeg zoompan filter expression for BROLL background camera motion.

    Guarantees:
    - Output is exactly 1920x1080 at requested fps;
    - Output stream advances 1 frame per input frame (d=1);
    - Evaluated purely against output frame index on;
    - Crop window stays clamped within [0, iw - iw/zoom] and [0, ih - ih/zoom];
    - Coordinates are subtracted so crop window displacement matches CSS translate displacement;
      e.g. for PAN_LEFT (content moves right-to-left), crop window moves left-to-right;
    - Never exposes uncropped or negative coordinates;
    - Codec output is yuv420p video with no black borders.
    """
    n_denom = max(1, total_frames - 1)
    smooth_expr = f"(on/{n_denom})*(on/{n_denom})*(3-2*(on/{n_denom}))"

    # Scale / zoom expression
    if profile.start_scale == profile.end_scale:
        z_expr = f"{profile.start_scale:.4f}"
    else:
        diff_s = profile.end_scale - profile.start_scale
        sign_s = "+" if diff_s >= 0 else "-"
        z_expr = f"{profile.start_scale:.4f}{sign_s}{abs(diff_s):.4f}*{smooth_expr}"

    # Horizontal crop / pan expression
    # Note: In zoompan, increasing crop x shifts visible content to the left (X_screen = (X_source - x) * zoom).
    # Subtracting offset_x ensures positive offset_x shifts content right and negative shifts content left,
    # in 1:1 agreement with CSS translate(x_offset, y_offset).
    if profile.start_x_offset == 0.0 and profile.end_x_offset == 0.0:
        x_expr = "clip((iw-iw/zoom)/2,0,iw-iw/zoom)"
    else:
        diff_x = profile.end_x_offset - profile.start_x_offset
        sign_x = "+" if diff_x >= 0 else "-"
        offset_x = f"({profile.start_x_offset:.4f}{sign_x}{abs(diff_x):.4f}*{smooth_expr})*iw"
        x_expr = f"clip((iw-iw/zoom)/2-({offset_x}),0,iw-iw/zoom)"

    # Vertical crop / tilt expression
    if profile.start_y_offset == 0.0 and profile.end_y_offset == 0.0:
        y_expr = "clip((ih-ih/zoom)/2,0,ih-ih/zoom)"
    else:
        diff_y = profile.end_y_offset - profile.start_y_offset
        sign_y = "+" if diff_y >= 0 else "-"
        offset_y = f"({profile.start_y_offset:.4f}{sign_y}{abs(diff_y):.4f}*{smooth_expr})*ih"
        y_expr = f"clip((ih-ih/zoom)/2-({offset_y}),0,ih-ih/zoom)"

    return f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s=1920x1080:fps={fps}"

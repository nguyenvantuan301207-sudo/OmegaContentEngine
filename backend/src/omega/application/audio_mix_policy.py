"""Deterministic audio mix policy for background music and SFX."""

from dataclasses import dataclass

from omega.domain.production import LicenseStatus


def validate_audio_license(license_status: LicenseStatus | str) -> bool:
    """Validate that the given license status is permitted for mixing."""
    if isinstance(license_status, LicenseStatus):
        status_str = license_status.value
    else:
        status_str = license_status

    allowed_statuses = {
        LicenseStatus.OWNED.value,
        LicenseStatus.GENERATED.value,
        LicenseStatus.LICENSED.value,
        LicenseStatus.PUBLIC_DOMAIN.value,
        LicenseStatus.ATTRIBUTION_REQUIRED.value,
    }

    return status_str in allowed_statuses


@dataclass(frozen=True)
class BackgroundMusicPlan:
    """Deterministic representation for background music mixing."""

    music_duration_ms: int
    target_duration_ms: int
    gain_db: float
    fade_in_ms: int
    fade_out_ms: int
    loop_required: bool
    attribution_required: bool


def build_background_music_plan(
    *,
    video_duration_ms: int,
    music_duration_ms: int,
    license_status: LicenseStatus | str,
    gain_db: float = -20.0,
    fade_in_ms: int = 1000,
    fade_out_ms: int = 1500,
) -> BackgroundMusicPlan:
    """Pure builder for background music mixing."""
    if video_duration_ms <= 0:
        raise ValueError("video_duration_ms must be > 0")
    if music_duration_ms <= 0:
        raise ValueError("music_duration_ms must be > 0")

    if not validate_audio_license(license_status):
        raise ValueError(f"Invalid or rejected license status: {license_status}")

    if not (-40.0 <= gain_db <= -6.0):
        raise ValueError("gain_db must be between -40.0 and -6.0")

    if fade_in_ms < 0 or fade_out_ms < 0:
        raise ValueError("fade values cannot be negative")

    total_fade = fade_in_ms + fade_out_ms
    if total_fade > video_duration_ms:
        in_ratio = fade_in_ms / total_fade
        fade_in_ms = int(video_duration_ms * in_ratio)
        fade_out_ms = video_duration_ms - fade_in_ms

    loop_required = music_duration_ms < video_duration_ms

    status_str = license_status.value if isinstance(license_status, LicenseStatus) else license_status
    attribution_required = status_str == LicenseStatus.ATTRIBUTION_REQUIRED.value

    return BackgroundMusicPlan(
        music_duration_ms=music_duration_ms,
        target_duration_ms=video_duration_ms,
        gain_db=gain_db,
        fade_in_ms=fade_in_ms,
        fade_out_ms=fade_out_ms,
        loop_required=loop_required,
        attribution_required=attribution_required,
    )


@dataclass(frozen=True)
class SFXEventPlan:
    """Deterministic SFX event representation."""

    event_id: str
    start_ms: int
    duration_ms: int
    gain_db: float
    license_status: str
    attribution_required: bool


def build_sfx_event_plan(
    *,
    event_id: str,
    start_ms: int,
    duration_ms: int,
    video_duration_ms: int,
    license_status: LicenseStatus | str,
    gain_db: float,
) -> SFXEventPlan:
    """Pure validator/planner for SFX events."""
    if not event_id:
        raise ValueError("event_id must be non-empty")
    if start_ms < 0:
        raise ValueError("start_ms must be >= 0")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be > 0")

    if start_ms + duration_ms > video_duration_ms:
        raise ValueError("SFX event must fit fully inside video_duration_ms")

    if not validate_audio_license(license_status):
        raise ValueError(f"Invalid or rejected license status: {license_status}")

    if not (-30.0 <= gain_db <= 0.0):
        raise ValueError("gain_db must be between -30.0 and 0.0")

    status_str = license_status.value if isinstance(license_status, LicenseStatus) else license_status
    attribution_required = status_str == LicenseStatus.ATTRIBUTION_REQUIRED.value

    return SFXEventPlan(
        event_id=event_id,
        start_ms=start_ms,
        duration_ms=duration_ms,
        gain_db=gain_db,
        license_status=status_str,
        attribution_required=attribution_required,
    )


@dataclass(frozen=True)
class AudioMixPlan:
    """Lightweight AudioMixPlan representation containing all audio items."""

    video_duration_ms: int
    background_music: BackgroundMusicPlan | None
    sfx_events: list[SFXEventPlan]


def build_audio_mix_plan(
    *,
    video_duration_ms: int,
    background_music: BackgroundMusicPlan | None = None,
    sfx_events: list[SFXEventPlan] | None = None,
) -> AudioMixPlan:
    """Pure builder that validates elements and returns deterministic ordering."""
    if video_duration_ms <= 0:
        raise ValueError("video_duration_ms must be > 0")

    if background_music is not None and background_music.target_duration_ms != video_duration_ms:
        raise ValueError("Background music target duration must match video duration")

    final_sfx = []
    if sfx_events:
        for sfx in sfx_events:
            if sfx.start_ms + sfx.duration_ms > video_duration_ms:
                raise ValueError("SFX event exceeds video duration")
            final_sfx.append(sfx)

    final_sfx.sort(key=lambda x: (x.start_ms, x.event_id))

    return AudioMixPlan(
        video_duration_ms=video_duration_ms,
        background_music=background_music,
        sfx_events=final_sfx,
    )

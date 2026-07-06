"""
NSFW detection via Sightengine (https://sightengine.com).
Free tier gives a few thousand checks/month - fine for testing and small groups.

Note: Sightengine's dedicated video endpoint (video/check-sync.json) requires
a PAID plan - it returns a "usage_limit" error on the free tier. To support
video/GIFs/video-stickers without paying for that, we extract a handful of
frames from the video ourselves using ffmpeg (free, local, no API cost) and
run each frame through the same free image endpoint used for photos. This
uses more image-check requests per video (one per extracted frame) but stays
entirely on the free plan.

Every check returns a dict with two independent scores:
  - "severity": the main nudity intensity score (sexual_activity down through
    mildly_suggestive) - this is what "explicit content" usually means.
  - "swimwear": a separate score for bikinis/lingerie/cleavage/etc, which
    Sightengine tracks as its own sub-category and does NOT fold into the
    main severity score (a bikini photo can score very low on "severity" while
    still clearly being a bikini photo). Chats can choose whether to also
    filter on this via the filter_swimwear setting.

Swap out `check_image_bytes` with a self-hosted model later if you want to
avoid per-request API costs entirely - the rest of the bot doesn't need to
change, it just expects this same {"severity":.., "swimwear":..} shape back.
"""

import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from config import SIGHTENGINE_API_USER, SIGHTENGINE_API_SECRET

try:
    import imageio_ffmpeg
    _FFMPEG_BINARY = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    # Falls back to whatever "ffmpeg" resolves to on PATH (e.g. a system
    # install via 'pkg install ffmpeg' on Termux). imageio-ffmpeg bundles its
    # own prebuilt binary, which is what makes this work on Pydroid 3 too,
    # where there's no package manager to install a system ffmpeg.
    _FFMPEG_BINARY = "ffmpeg"

IMAGE_CHECK_URL = "https://api.sightengine.com/1.0/check.json"

# Main nudity intensity ladder, most to least explicit.
SEVERITY_FIELDS = [
    "sexual_activity", "sexual_display", "erotica",
    "very_suggestive", "suggestive", "mildly_suggestive",
]

# Sightengine tracks these as sub-classes of "suggestive" content, separate
# from the main severity score - this is what catches swimwear/lingerie/etc
# photos that don't necessarily score high on general "explicitness".
SWIMWEAR_CLASS_FIELDS = ["bikini", "lingerie", "cleavage", "male_underwear", "miniskirt"]

# How many frames to sample from a video/GIF/video-sticker. More frames =
# more thorough but more API calls (and slower). 4-5 is a reasonable balance
# for short Telegram clips.
FRAMES_TO_SAMPLE = 4

# A shared session (with a bigger connection pool) is noticeably faster than
# calling requests.post() fresh each time, since it reuses TCP/TLS connections
# instead of renegotiating one per request - this matters a lot when we fire
# several frame-checks in parallel.
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
_session.mount("https://", _adapter)


def _max_field_score(d: dict, fields: list) -> float:
    scores = [d.get(field, 0.0) for field in fields if field in d]
    return max(scores) if scores else 0.0


def check_image_bytes(image_bytes: bytes, filename: str = "image.jpg") -> dict:
    """
    Send a still image to Sightengine and return
    {"severity": float, "swimwear": float}, both in the 0.0-1.0 range.
    """
    if not SIGHTENGINE_API_USER or not SIGHTENGINE_API_SECRET:
        raise RuntimeError("Sightengine credentials are not configured in .env")

    files = {"media": (filename, image_bytes)}
    data = {
        "models": "nudity-2.1",
        "api_user": SIGHTENGINE_API_USER,
        "api_secret": SIGHTENGINE_API_SECRET,
    }

    resp = _session.post(IMAGE_CHECK_URL, files=files, data=data, timeout=30)
    result = resp.json()

    if result.get("status") != "success":
        raise RuntimeError(f"Sightengine error: {result.get('error')}")

    nudity = result.get("nudity", {})
    severity = _max_field_score(nudity, SEVERITY_FIELDS)
    swimwear = _max_field_score(nudity.get("suggestive_classes", {}), SWIMWEAR_CLASS_FIELDS)

    return {"severity": severity, "swimwear": swimwear}


def _extract_frames(video_bytes: bytes, suffix: str) -> list:
    """Use ffmpeg to pull a handful of evenly-spaced frames out of a video/gif/webm."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, f"input{suffix}")
        with open(input_path, "wb") as f:
            f.write(video_bytes)

        output_pattern = os.path.join(tmpdir, "frame_%02d.jpg")

        cmd = [
            _FFMPEG_BINARY, "-y", "-i", input_path,
            "-vf", f"fps=2",
            "-frames:v", str(FRAMES_TO_SAMPLE),
            output_pattern,
        ]

        try:
            subprocess.run(
                cmd, check=True, capture_output=True, timeout=60,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg binary not found. Install it with 'pip install imageio-ffmpeg' "
                "(bundled binary, works everywhere including Pydroid 3) or "
                "'pkg install ffmpeg' (Termux system install) to enable video/GIF/"
                "video-sticker scanning without a thumbnail."
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed to extract frames: {e.stderr.decode(errors='ignore')[:300]}")

        frames = []
        for name in sorted(os.listdir(tmpdir)):
            if name.startswith("frame_") and name.endswith(".jpg"):
                with open(os.path.join(tmpdir, name), "rb") as f:
                    frames.append(f.read())

        return frames


def check_video_bytes(video_bytes: bytes, filename: str = "video.mp4") -> dict:
    """
    Check a video/GIF/video-sticker for NSFW content WITHOUT using
    Sightengine's paid video endpoint. Extracts a few frames locally with
    ffmpeg and runs each one through the free image endpoint IN PARALLEL,
    returning {"severity": float, "swimwear": float} using the highest score
    seen (independently) across all sampled frames for each category.
    """
    suffix = os.path.splitext(filename)[1] or ".mp4"
    frames = _extract_frames(video_bytes, suffix)

    if not frames:
        # Nothing extracted (e.g. corrupt file, unsupported codec) - fail safe
        # by returning zeros rather than blocking a message we couldn't check.
        return {"severity": 0.0, "swimwear": 0.0}

    best_severity = 0.0
    best_swimwear = 0.0
    with ThreadPoolExecutor(max_workers=len(frames)) as executor:
        futures = [
            executor.submit(check_image_bytes, frame_bytes, f"frame_{i}.jpg")
            for i, frame_bytes in enumerate(frames)
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
                best_severity = max(best_severity, result["severity"])
                best_swimwear = max(best_swimwear, result["swimwear"])
            except Exception:
                # One frame failing shouldn't sink the whole check - the other
                # frames still get a fair shot at flagging the content.
                continue

    return {"severity": best_severity, "swimwear": best_swimwear}

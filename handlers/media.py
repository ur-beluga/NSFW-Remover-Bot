import logging
import time
import threading
from database.db import (
    get_settings, get_cached_score, set_cached_score,
    bump_stats, is_pack_blacklisted,
)
from services.nsfw_detector import check_image_bytes, check_video_bytes

logger = logging.getLogger(__name__)

# Telegram's Bot API cannot download files larger than this, regardless of
# what the bot does - it's a platform-side limit, not something fixable in
# code (short of running your own local Bot API server, which is a much
# heavier setup than this bot needs).
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def _get_file_size(message):
    if message.photo:
        return message.photo[-1].file_size
    if message.animation:
        return message.animation.file_size
    if message.sticker:
        return message.sticker.file_size
    if message.video:
        return message.video.file_size
    return None


def _get_unique_id(message):
    if message.photo:
        return message.photo[-1].file_unique_id
    if message.animation:
        return message.animation.file_unique_id
    if message.sticker:
        return message.sticker.file_unique_id
    if message.video:
        return message.video.file_unique_id
    return None


def _get_thumbnail_file_id(media_obj):
    """
    Telegram sends a small static JPEG thumbnail alongside videos, GIFs, and
    video stickers. Checking just that thumbnail is dramatically faster than
    downloading + extracting frames from the full file, at the cost of only
    seeing one representative frame instead of several. This mirrors how
    most fast NSFW-filter bots handle video by default.
    """
    thumb = getattr(media_obj, "thumbnail", None) or getattr(media_obj, "thumb", None)
    return thumb.file_id if thumb else None


def _download_file(bot, file_id: str) -> bytes:
    file_info = bot.get_file(file_id)
    return bot.download_file(file_info.file_path)


def _auto_delete_after(bot, chat_id: int, message_id: int, delay_seconds: float = 5.0):
    """Deletes a message after a short delay, without blocking the calling
    worker thread (spawns a lightweight background thread instead)."""
    def _worker():
        time.sleep(delay_seconds)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass  # message may have already been removed/expired - not a problem

    threading.Thread(target=_worker, daemon=True).start()


def _mention(user):
    """Best display form for a user: @username if they have one, else their full name."""
    if not user:
        return "Someone"
    return f"@{user.username}" if user.username else user.full_name


def _handle_flagged(bot, message, score, settings):
    action = settings["action"]
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
        return

    user = message.from_user
    notice_lines = ["✅ Removed this content successfully"]

    if action == "delete_and_mute" and user:
        try:
            # No until_date means the restriction is permanent until manually lifted.
            bot.restrict_chat_member(message.chat.id, user.id, can_send_messages=False)
            logger.info(f"[{message.chat.id}] permanently muted user {user.id}")
            notice_lines.append(f"🔇 {_mention(user)} (ID: {user.id}) has been muted.")
        except Exception as e:
            logger.warning(f"Could not mute user: {e}")

    elif action == "delete_and_ban" and user:
        try:
            # No until_date means a permanent ban until manually unbanned.
            bot.ban_chat_member(message.chat.id, user.id)
            logger.info(f"[{message.chat.id}] permanently banned user {user.id}")
            notice_lines.append(f"🔨 {_mention(user)} (ID: {user.id}) has been banned.")
        except Exception as e:
            logger.warning(f"Could not ban user: {e}")

    try:
        notice = bot.send_message(message.chat.id, "\n".join(notice_lines))
        _auto_delete_after(bot, message.chat.id, notice.message_id, delay_seconds=5)
    except Exception as e:
        logger.warning(f"Could not send/schedule confirmation message: {e}")


def register_handlers(bot):

    @bot.message_handler(content_types=["photo", "animation", "sticker", "video"])
    def scan_media(message):
        settings = get_settings(message.chat.id)
        if not settings["enabled"]:
            logger.info(f"[{message.chat.id}] scanning disabled, skipping message")
            return

        # Sticker pack blacklist - instantly delete stickers from banned packs
        # without needing an API call at all.
        if message.sticker and message.sticker.set_name:
            if is_pack_blacklisted(message.chat.id, message.sticker.set_name):
                try:
                    bot.delete_message(message.chat.id, message.message_id)
                    logger.info(f"[{message.chat.id}] deleted sticker from blacklisted pack '{message.sticker.set_name}'")
                    bump_stats(message.chat.id, flagged=1)
                    notice = bot.send_message(message.chat.id, "✅ Removed this content successfully")
                    _auto_delete_after(bot, message.chat.id, notice.message_id, delay_seconds=5)
                except Exception as e:
                    logger.warning(f"Could not delete blacklisted sticker: {e}")
                return

        file_id = None
        kind = None
        mode = None  # "image", "thumbnail", or "frames"
        media_obj = None

        if message.photo:
            kind = "photo"
            mode = "image"
            if settings["scan_photos"]:
                file_id = message.photo[-1].file_id
            else:
                logger.info(f"[{message.chat.id}] photo scanning disabled, skipping")

        elif message.animation:
            kind = "gif"
            media_obj = message.animation
            if settings["scan_gifs"]:
                file_id = _get_thumbnail_file_id(media_obj) or media_obj.file_id
                mode = "thumbnail" if _get_thumbnail_file_id(media_obj) else "frames"
            else:
                logger.info(f"[{message.chat.id}] gif scanning disabled, skipping")

        elif message.sticker:
            sticker = message.sticker
            if sticker.is_animated:
                kind = "animated sticker (.tgs)"
                logger.info(f"[{message.chat.id}] {kind} - not supported (vector animation, not raster/video), skipping")
            elif sticker.is_video:
                kind = "video sticker (.webm)"
                media_obj = sticker
                if settings["scan_videos"]:
                    file_id = _get_thumbnail_file_id(media_obj) or media_obj.file_id
                    mode = "thumbnail" if _get_thumbnail_file_id(media_obj) else "frames"
                else:
                    logger.info(f"[{message.chat.id}] video scanning disabled, skipping video sticker")
            else:
                kind = "static sticker"
                mode = "image"
                if settings["scan_stickers"]:
                    file_id = sticker.file_id
                else:
                    logger.info(f"[{message.chat.id}] sticker scanning disabled, skipping")

        elif message.video:
            kind = "video"
            media_obj = message.video
            if settings["scan_videos"]:
                file_id = _get_thumbnail_file_id(media_obj) or media_obj.file_id
                mode = "thumbnail" if _get_thumbnail_file_id(media_obj) else "frames"
            else:
                logger.info(f"[{message.chat.id}] video scanning disabled, skipping")

        if not file_id:
            return

        # Only the full-file paths ("image" for photos/stickers, "frames" for
        # video fallback) need the size check - "thumbnail" mode downloads a
        # tiny preview image regardless of how large the original file is.
        if mode != "thumbnail":
            file_size = _get_file_size(message)
            if file_size and file_size > TELEGRAM_MAX_DOWNLOAD_BYTES:
                logger.warning(
                    f"[{message.chat.id}] {kind} is {file_size / 1024 / 1024:.1f}MB - "
                    f"exceeds Telegram's 20MB bot download limit, cannot scan, skipping"
                )
                return

        # Cache lookup - if we've seen this exact file before (very common for
        # popular stickers/GIFs reused across many chats), skip the API call
        # entirely and reuse the previous result. We store severity and
        # swimwear scores separately so each chat can apply its own
        # filter_swimwear setting to the same cached file.
        unique_id = _get_unique_id(message)
        cached = get_cached_score(unique_id)

        if cached is not None:
            effective_score = cached["severity"]
            if settings["filter_swimwear"]:
                effective_score = max(effective_score, cached["swimwear"])
            logger.info(
                f"[{message.chat.id}] {kind} cache hit, severity={cached['severity']:.2f} "
                f"swimwear={cached['swimwear']:.2f} effective={effective_score:.2f} (no API call)"
            )
            bump_stats(message.chat.id, scanned=1, cache_hits=1)
            if effective_score >= settings["threshold"]:
                _handle_flagged(bot, message, effective_score, settings)
            return

        logger.info(f"[{message.chat.id}] downloading {kind} ({mode})...")
        t0 = time.time()
        try:
            media_bytes = _download_file(bot, file_id)
        except Exception as e:
            logger.error(f"[{message.chat.id}] download failed for {kind}: {e}")
            return
        download_time = time.time() - t0
        size_kb = len(media_bytes) / 1024
        logger.info(f"[{message.chat.id}] download took {download_time:.1f}s ({size_kb:.0f}KB)")

        logger.info(f"[{message.chat.id}] checking {kind} via {mode}...")
        t1 = time.time()
        try:
            if mode == "frames":
                result = check_video_bytes(media_bytes)
            else:
                result = check_image_bytes(media_bytes)
        except Exception as e:
            logger.error(f"[{message.chat.id}] NSFW check failed for {kind}: {e}")
            return
        check_time = time.time() - t1
        logger.info(f"[{message.chat.id}] API check took {check_time:.1f}s")

        severity = result["severity"]
        swimwear = result["swimwear"]
        effective_score = severity
        if settings["filter_swimwear"]:
            effective_score = max(severity, swimwear)

        logger.info(
            f"[{message.chat.id}] {kind} severity={severity:.2f} swimwear={swimwear:.2f} "
            f"effective={effective_score:.2f} threshold={settings['threshold']}"
        )
        bump_stats(message.chat.id, scanned=1)

        if unique_id:
            set_cached_score(unique_id, severity, swimwear, kind)

        if effective_score >= settings["threshold"]:
            bump_stats(message.chat.id, flagged=1)
            _handle_flagged(bot, message, effective_score, settings)

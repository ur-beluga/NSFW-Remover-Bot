from telebot import types
from database.db import (
    get_settings, update_setting, get_stats,
    add_blacklisted_pack, remove_blacklisted_pack, list_blacklisted_packs,
)

ACTION_LABELS = {
    "delete": "Delete only",
    "delete_and_mute": "Delete + permanent mute",
    "delete_and_ban": "Delete + permanent ban",
}


def _is_admin(bot, message) -> bool:
    if message.chat.type == "private":
        return True
    member = bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in ("administrator", "creator")


def _settings_text(settings: dict) -> str:
    return (
        f"⚙️ Settings for this chat\n"
        f"Threshold: {settings['threshold']}\n"
        f"Action: {ACTION_LABELS.get(settings['action'], settings['action'])}"
    )


def _settings_keyboard(settings: dict) -> types.InlineKeyboardMarkup:
    def toggle_label(name, key):
        return f"{'✅' if settings[key] else '❌'} {name}"

    def action_label(name, value):
        return f"{'🔘' if settings['action'] == value else '⚪'} {name}"

    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton(
        text=f"{'🟢 Enabled' if settings['enabled'] else '🔴 Disabled'}",
        callback_data="toggle:enabled"))
    kb.row(
        types.InlineKeyboardButton(text=toggle_label("Photos", "scan_photos"), callback_data="toggle:scan_photos"),
        types.InlineKeyboardButton(text=toggle_label("GIFs", "scan_gifs"), callback_data="toggle:scan_gifs"),
    )
    kb.row(
        types.InlineKeyboardButton(text=toggle_label("Videos", "scan_videos"), callback_data="toggle:scan_videos"),
        types.InlineKeyboardButton(text=toggle_label("Stickers", "scan_stickers"), callback_data="toggle:scan_stickers"),
    )
    kb.row(
        types.InlineKeyboardButton(text=toggle_label("Swimwear/lingerie filter", "filter_swimwear"), callback_data="toggle:filter_swimwear"),
    )
    kb.row(
        types.InlineKeyboardButton(text="Low (0.4)", callback_data="threshold:0.4"),
        types.InlineKeyboardButton(text="Med (0.65)", callback_data="threshold:0.65"),
        types.InlineKeyboardButton(text="High (0.85)", callback_data="threshold:0.85"),
    )
    kb.row(
        types.InlineKeyboardButton(text=action_label("Delete only", "delete"), callback_data="action:delete"),
    )
    kb.row(
        types.InlineKeyboardButton(text=action_label("🔇 Mute permanently", "delete_and_mute"), callback_data="action:delete_and_mute"),
        types.InlineKeyboardButton(text=action_label("🔨 Ban permanently", "delete_and_ban"), callback_data="action:delete_and_ban"),
    )
    return kb


def register_handlers(bot):

    @bot.message_handler(commands=["start"])
    def cmd_start(message):
        bot.send_message(
            message.chat.id,
            "🔞 <b>NSFW Remover</b> - automatically delete inappropriate content in groups.\n\n"
            "<b>Features:</b>\n"
            "⚡ Fast thumbnail-based scanning for photos, GIFs, videos, stickers\n"
            "🧠 Smart caching - repeated stickers/GIFs are never re-checked\n"
            "🛡 Sticker pack blacklist, usage stats\n"
            "🔨 Delete only, or delete + permanent mute/ban - your choice\n"
            "⚙️ Flexible sensitivity and content category controls\n\n"
            "<b>Commands:</b>\n"
            "/settings - configure this chat (admins only)\n"
            "/stats - see scanning stats for this chat\n"
            "/blacklist add|remove|list &lt;pack_name&gt; - ban entire sticker packs\n\n"
            "<b>How to use:</b>\n"
            "1. Add me to your group\n"
            "2. Make me an admin (need delete + restrict permissions)\n"
            "3. Run /settings in the group to configure",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["settings"])
    def cmd_settings(message):
        if not _is_admin(bot, message):
            bot.send_message(message.chat.id, "Only group admins can change settings.")
            return

        settings = get_settings(message.chat.id)
        bot.send_message(
            message.chat.id,
            _settings_text(settings),
            reply_markup=_settings_keyboard(settings),
        )

    @bot.message_handler(commands=["stats"])
    def cmd_stats(message):
        stats = get_stats(message.chat.id)
        scanned = stats["scanned"]
        flagged = stats["flagged"]
        cache_hits = stats["cache_hits"]
        pct = f"{(flagged / scanned * 100):.1f}%" if scanned else "0%"
        bot.send_message(
            message.chat.id,
            "📊 <b>Scanning stats for this chat</b>\n\n"
            f"Items scanned: <b>{scanned}</b>\n"
            f"Items removed: <b>{flagged}</b> ({pct})\n"
            f"Cache hits (skipped re-checks): <b>{cache_hits}</b>",
        )

    @bot.message_handler(commands=["blacklist"])
    def cmd_blacklist(message):
        if not _is_admin(bot, message):
            bot.send_message(message.chat.id, "Only group admins can manage the blacklist.")
            return

        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "Usage:\n"
                "/blacklist add &lt;pack_name&gt;\n"
                "/blacklist remove &lt;pack_name&gt;\n"
                "/blacklist list\n\n"
                "Tip: forward a sticker from the pack you want to ban and long-press "
                "it to see its pack name, or check the sticker pack's share link.",
            )
            return

        sub = parts[1].lower()

        if sub == "list":
            packs = list_blacklisted_packs(message.chat.id)
            if not packs:
                bot.send_message(message.chat.id, "No sticker packs are blacklisted in this chat.")
            else:
                bot.send_message(message.chat.id, "Blacklisted sticker packs:\n" + "\n".join(f"• {p}" for p in packs))
            return

        if len(parts) < 3:
            bot.send_message(message.chat.id, "Please also specify a pack name, e.g. /blacklist add SomePackName")
            return

        pack_name = parts[2].strip()

        if sub == "add":
            add_blacklisted_pack(message.chat.id, pack_name)
            bot.send_message(message.chat.id, f"✅ Sticker pack '{pack_name}' is now blacklisted - its stickers will be auto-deleted.")
        elif sub == "remove":
            remove_blacklisted_pack(message.chat.id, pack_name)
            bot.send_message(message.chat.id, f"Removed '{pack_name}' from the blacklist.")
        else:
            bot.send_message(message.chat.id, "Unknown subcommand. Use add, remove, or list.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("action:"))
    def cb_action(call):
        if not _is_admin(bot, call.message):
            bot.answer_callback_query(call.id, "Admins only.", show_alert=True)
            return

        value = call.data.split(":", 1)[1]
        update_setting(call.message.chat.id, "action", value)

        settings = get_settings(call.message.chat.id)
        bot.edit_message_text(
            _settings_text(settings),
            call.message.chat.id, call.message.message_id,
            reply_markup=_settings_keyboard(settings),
        )
        bot.answer_callback_query(call.id, f"Action set to: {ACTION_LABELS.get(value, value)}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("toggle:"))
    def cb_toggle(call):
        if not _is_admin(bot, call.message):
            bot.answer_callback_query(call.id, "Admins only.", show_alert=True)
            return

        field = call.data.split(":", 1)[1]
        settings = get_settings(call.message.chat.id)
        new_value = 0 if settings[field] else 1
        update_setting(call.message.chat.id, field, new_value)

        settings = get_settings(call.message.chat.id)
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id,
            reply_markup=_settings_keyboard(settings),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("threshold:"))
    def cb_threshold(call):
        if not _is_admin(bot, call.message):
            bot.answer_callback_query(call.id, "Admins only.", show_alert=True)
            return

        value = float(call.data.split(":", 1)[1])
        update_setting(call.message.chat.id, "threshold", value)

        settings = get_settings(call.message.chat.id)
        bot.edit_message_text(
            _settings_text(settings),
            call.message.chat.id, call.message.message_id,
            reply_markup=_settings_keyboard(settings),
        )
        bot.answer_callback_query(call.id, f"Threshold set to {value}")

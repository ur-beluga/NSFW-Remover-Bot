# NSFW Remover Bot (fast, pure-Python edition)

A Telegram bot that auto-detects and deletes NSFW images/GIFs/videos/stickers
in group chats — built to run entirely on free-tier tools (no paid Sightengine
plan, no Rust/compiled dependencies, works fine on Termux/Android).

## Features

- **Photos & static stickers** — checked directly via Sightengine's free image API.
- **Videos, GIFs, video stickers** — checked via a fast thumbnail (Telegram
  auto-generates one for these), with a slower full-frame-extraction fallback
  (via `ffmpeg`, free/local) if no thumbnail is available.
- **Smart caching** — every file is keyed by its unique Telegram file ID. If
  the same sticker/GIF shows up again (in this chat or any other), the bot
  reuses the previous score instantly instead of calling the API again. Huge
  speed and quota savings for popular stickers/GIFs that get reposted a lot.
- **Admin bypass** — group admins can be exempted from scanning (toggle in `/settings`).
- **Sticker pack blacklist** — instantly delete every sticker from specific
  packs, no API call needed, via `/blacklist add <pack_name>`.
- **Usage stats** — `/stats` shows items scanned, removed, and cache hits per chat.
- **Concurrent processing** — 8-worker thread pool so multiple photos/GIFs
  sent at once are handled in parallel instead of queueing one-by-one.
- **Shared HTTP connection pool** — faster repeated API calls (reuses TCP/TLS
  connections instead of renegotiating each time).

## Known limits (platform-level, not fixable in code)

- **20MB max file size** — Telegram's Bot API cannot download anything larger,
  regardless of what any bot does. Oversized files are skipped with a clear
  log message rather than failing silently. (Videos still get scanned via
  their small thumbnail regardless of the original file's size.)
- **Animated stickers (.tgs)** — these are vector animation data, not an
  image or video file, so there's no frame to send to an image classifier
  without extra rendering tooling. Skipped and logged.
- **Sightengine's dedicated video endpoint is paid-only** — we route around
  this entirely using the thumbnail/frame-extraction approach above, which
  stays on the free image-check tier.

## Setup

1. **Create your bot**
   - Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the token.

2. **Get NSFW detection credentials**
   - Sign up at [sightengine.com](https://sightengine.com) (free tier) → copy API user + secret.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   No C compiler, Rust, or build tools needed.

4. **Install ffmpeg** (used as a fallback for videos without a thumbnail)
   ```bash
   pkg install ffmpeg      # Termux
   # or
   apt install ffmpeg      # Debian/Ubuntu
   ```

5. **Configure**
   ```bash
   cp .env.example .env
   # edit .env: BOT_TOKEN, SIGHTENGINE_API_USER, SIGHTENGINE_API_SECRET
   ```

6. **Run**
   ```bash
   python bot.py
   ```

## Using the bot

1. Add the bot to your Telegram group.
2. Promote it to **admin** with at least *delete messages* and *restrict members* permissions.
3. Run `/settings` (admins only) to configure:
   - Enable/disable scanning
   - Toggle photos / GIFs / videos / stickers
   - Toggle admin bypass
   - Set sensitivity threshold (low / medium / high)
4. Run `/stats` any time to see scanning activity for the chat.
5. Manage sticker pack bans with:
   ```
   /blacklist add <pack_name>
   /blacklist remove <pack_name>
   /blacklist list
   ```
   Tip: forward a sticker from the pack in question and check its share
   link or long-press it to find the pack's name.

## Swapping the detection engine

`services/nsfw_detector.py` is isolated - swap `check_image_bytes()` for a
self-hosted model if you'd rather not use a third-party API at all. Nothing
else needs to change as long as it still returns a float between 0.0 and 1.0.

## Project structure

```
nsfw-bot-v2/
├── bot.py                  # entrypoint (8-worker thread pool)
├── config.py                 # env config loader
├── database/
│   └── db.py                   # settings, media cache, stats, sticker blacklist (SQLite)
├── handlers/
│   ├── media.py                  # scanning logic: cache, admin bypass, blacklist, thumbnails
│   └── settings.py                # /start, /settings, /stats, /blacklist
├── services/
│   └── nsfw_detector.py            # Sightengine integration (shared connection pool)
├── requirements.txt
└── .env.example
```

## Running on Termux long-term

- Run inside `tmux` or with `nohup python bot.py &` so it survives closing the terminal.
- Run `termux-wake-lock` beforehand, and disable Android battery optimization
  for Termux, or the process will get killed in the background.
- For real 24/7 uptime, a small VPS is more reliable than a phone.

# Telegram Setup

## Difficulty: Easy

## What you need

- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A chat ID where the bot can post

## Steps

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
4. Add the bot to your channel as an admin, or get your chat ID via [@userinfobot](https://t.me/userinfobot)

## Configure

```bash
autoposter setup telegram
```

You will be prompted for:
- Bot token
- Chat ID (e.g. `@yourchannel` or `-1001234567890`)

## Or via .env

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

Then run `autoposter setup telegram --non-interactive`.

## Permissions

The bot needs:
- Post messages in the target chat/channel
- (Optional) Send photos and videos

## Limits

- 4096 characters per message
- Up to 10 media items per media group
- 50 MB per file

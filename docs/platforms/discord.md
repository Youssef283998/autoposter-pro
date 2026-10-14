# Discord Setup

## Difficulty: Easy

## What you need

- A Discord server where you have admin permissions
- Either a webhook URL or a bot token

## Webhook setup (recommended)

1. Open your server settings → Integrations → Webhooks
2. Create a new webhook for the target channel
3. Copy the webhook URL

```bash
autoposter setup discord
# Choose "webhook" and paste the URL
```

## Bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot → Copy token
3. Enable "Message Content Intent" in the Privileged Gateway Intents
4. Invite the bot to your server with `bot` and `Send Messages` permissions

```bash
autoposter setup discord
# Choose "bot" and paste the token + channel ID
```

## Limits

- 2000 characters per message
- Up to 10 files per message
- 25 MB per file

# Mastodon Setup

## Difficulty: Easy

## What you need

- A Mastodon account on any instance
- An application token with `write` scope

## Steps

1. Go to your instance's settings → Development → New application
2. Name it "AutoPoster" and grant `write` scope
3. Copy the access token

```bash
autoposter setup mastodon
```

You will be prompted for:
- Instance URL (e.g. `https://mastodon.social`)
- Client ID
- Client secret
- Access token

## Or via .env

```env
MASTODON_API_BASE=https://mastodon.social
MASTODON_CLIENT_ID=your_client_id
MASTODON_CLIENT_SECRET=your_client_secret
MASTODON_ACCESS_TOKEN=your_access_token
```

## Limits

- 500 characters per post (may vary by instance)
- Up to 4 media attachments
- 16 MB per file
- Alt text supported

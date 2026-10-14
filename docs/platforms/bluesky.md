# Bluesky Setup

## Difficulty: Easy

## What you need

- A Bluesky account
- An app password (not your main password)

## Steps

1. Go to [Bluesky Settings](https://bsky.app/settings/app-passwords)
2. Click "Add App Password"
3. Name it "AutoPoster" and create the password
4. Copy the app password (looks like `xxxx-xxxx-xxxx-xxxx`)

```bash
autoposter setup bluesky
```

You will be prompted for:
- Handle (e.g. `you.bsky.social`)
- App password

## Or via .env

```env
BLUESKY_HANDLE=you.bsky.social
BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

## Limits

- 300 graphemes per post
- Up to 4 images
- 1 MB per image
- Alt text supported

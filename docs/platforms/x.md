# X (Twitter) Setup

## Difficulty: Medium

## What you need

- An approved X developer account
- A project with OAuth 2.0 enabled
- API access (free tier is very limited)

## Steps

1. Go to the [X Developer Portal](https://developer.x.com)
2. Create a project and app
3. Enable OAuth 2.0 with PKCE
4. Set the callback URL to `http://127.0.0.1:8765/callback`
5. Copy the client ID and client secret

```bash
autoposter setup x
```

A browser window will open. Authorize AutoPoster and paste the code from the redirect URL.

## Or via .env

```env
X_CLIENT_ID=your_client_id
X_CLIENT_SECRET=your_client_secret
```

## Permissions

- `tweet.read`, `tweet.write`, `users.read`, `media.write`, `offline.access`

## Limits

- 280 characters (links count as 23)
- Up to 4 images or 1 video
- 5 MB per image, 140 seconds per video
- Rate limits apply (check X API docs)

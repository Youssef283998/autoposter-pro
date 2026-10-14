# LinkedIn Setup

## Difficulty: Hard

## What you need

- An approved LinkedIn developer app
- The "Sign In with LinkedIn" or "Share on LinkedIn" product enabled
- A personal profile or company page to post to

## Steps

1. Go to the [LinkedIn Developer Portal](https://developer.linkedin.com)
2. Create an app and request the "Sign In with LinkedIn" product
3. Add `http://127.0.0.1:8765/callback` as an authorized redirect URL
4. Copy the client ID and client secret

```bash
autoposter setup linkedin
```

A browser window will open. Authorize AutoPoster and paste the code.

## Or via .env

```env
LINKEDIN_CLIENT_ID=your_client_id
LINKEDIN_CLIENT_SECRET=your_client_secret
LINKEDIN_PERSON_URN=urn:li:person:your_id
```

## Permissions

- `w_member_social` (personal posts)
- `r_liteprofile` (basic profile)

## Limits

- 3000 characters per post
- Up to 9 images
- 20 MB per file

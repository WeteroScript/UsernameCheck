# GitHub integration setup

The bot uses a GitHub App installation flow with a verified GitHub user identity. Each Telegram user authorizes their own GitHub account, then installs the app and selects the repositories it may access. Installation IDs are stored per Telegram user. Short-lived installation access tokens are generated when needed and are not persisted.

## 1. Create the GitHub App

Open GitHub **Settings → Developer settings → GitHub Apps → New GitHub App**.

Configure:
- Homepage URL: the public Bothost URL of the bot.
- Callback URL: https://YOUR_BOTHOST_DOMAIN/github/oauth/callback
- Setup URL: https://YOUR_BOTHOST_DOMAIN/github/callback
- Disable webhooks for this integration.
- Repository permissions:
  - Contents: Read and write
  - Pull requests: Read and write
  - Metadata: Read-only (required).
- Allow installation on any account if users should connect personal accounts and organizations.

Generate a private key. The Callback URL and Setup URL must match the corresponding Bothost variables exactly.

## 2. Bothost environment variables

Add:
- GITHUB_APP_ID: numeric App ID from GitHub App settings.
- GITHUB_APP_SLUG: URL slug from https://github.com/apps/SLUG.
- GITHUB_APP_CLIENT_ID: Client ID from the GitHub App settings.
- GITHUB_APP_CLIENT_SECRET: Client secret from the GitHub App settings.
- GITHUB_APP_PRIVATE_KEY: complete PEM private key. Preserve newlines; if the host requires one line, replace line breaks with \n.
- GITHUB_STATE_SECRET: a long random secret used to sign short-lived authorization state.
- GITHUB_OAUTH_CALLBACK_URL: exact OAuth callback URL, https://YOUR_BOTHOST_DOMAIN/github/oauth/callback.
- GITHUB_CALLBACK_URL: exact installation callback URL, https://YOUR_BOTHOST_DOMAIN/github/callback.
- GITHUB_INSTALLATIONS_FILE (optional): defaults to data/github_installations.json. Keep /app/data on the persistent Bothost volume.

## 3. Deploy and verify

Deploy main. In Telegram, open GitHub → Подключить аккаунт. The user first authorizes their GitHub identity, then chooses the account and repositories for the GitHub App installation. After GitHub displays the success page, return to Telegram and open Мои репозитории.

Users can remove the installation from GitHub settings or use Отключить GitHub in the bot. Removing the installation in GitHub also revokes its access.

# GitHub integration setup

The bot uses a GitHub App installation flow. Each Telegram user installs the app on their own GitHub account and selects which repositories the app may access. Installation IDs are stored per Telegram user. Short-lived installation access tokens are generated when needed and are not persisted.

## 1. Create the GitHub App

Open GitHub **Settings → Developer settings → GitHub Apps → New GitHub App**.

Configure:
- Homepage URL: the public Bothost URL of the bot.
- Setup URL: https://YOUR_BOTHOST_DOMAIN/github/callback
- Disable webhooks for this integration.
- Repository permissions:
  - Contents: Read and write
  - Metadata: Read-only (required).
- Allow installation on any account if users should connect personal accounts and organizations.

Generate a private key. The GitHub App Setup URL must match the callback URL below.

## 2. Bothost environment variables

Add:
- GITHUB_APP_ID: numeric App ID from GitHub App settings.
- GITHUB_APP_SLUG: URL slug from https://github.com/apps/SLUG.
- GITHUB_APP_PRIVATE_KEY: complete PEM private key. Preserve newlines; if the host requires one line, replace line breaks with \n.
- GITHUB_STATE_SECRET: a long random secret used to sign short-lived authorization state.
- GITHUB_CALLBACK_URL: exact public callback URL, https://YOUR_BOTHOST_DOMAIN/github/callback.
- GITHUB_INSTALLATIONS_FILE (optional): defaults to data/github_installations.json. Keep /app/data on the persistent Bothost volume.

The Setup URL in GitHub App settings and GITHUB_CALLBACK_URL must match exactly.

## 3. Deploy and verify

Deploy main. In Telegram, open GitHub → Подключить аккаунт, approve the selected repository access on GitHub, then return to Telegram and open Мои репозитории.

Users can remove the installation from GitHub settings or use Отключить GitHub in the bot. Removing the installation in GitHub also revokes its access.

# GitHub integration setup

The bot uses GitHub App Device Flow to verify each Telegram user's GitHub identity. The user then installs the GitHub App and selects the repositories it may access. Installation IDs are stored per Telegram user. Short-lived installation access tokens are generated when needed and are not persisted.

## 1. Configure the GitHub App

Open GitHub **Settings → Developer settings → GitHub Apps → your app**.

Configure:
- Enable **Device Flow** and save the changes.
- Homepage URL: the public Bothost URL of the bot.
- Setup URL: https://YOUR_BOTHOST_DOMAIN/github/callback
- Webhooks: disabled.
- Repository permissions:
  - Contents: Read and write
  - Pull requests: Read and write
  - Metadata: Read-only (required).
- Use personal GitHub accounts for now. Organization-owned repositories are not supported by the current account-ownership check.

The OAuth Callback URL is not used by Device Flow. The Setup URL must match the Bothost callback route.

Generate a private key under **Private keys → Generate a private key**.

## 2. Bothost environment variables

Add:
- GITHUB_APP_ID: numeric App ID from GitHub App settings.
- GITHUB_APP_SLUG: URL slug from https://github.com/apps/SLUG.
- GITHUB_APP_CLIENT_ID: Client ID from the GitHub App settings.
- GITHUB_APP_PRIVATE_KEY: complete PEM private key. Preserve newlines; if the host requires one line, replace line breaks with \\n.
- GITHUB_STATE_SECRET: a long random secret used to sign short-lived installation state.
- GITHUB_CALLBACK_URL: exact installation callback URL, https://YOUR_BOTHOST_DOMAIN/github/callback.
- GITHUB_INSTALLATIONS_FILE (optional): defaults to data/github_installations.json. Keep /app/data on the persistent Bothost volume.

**GITHUB_APP_CLIENT_SECRET** and **GITHUB_OAUTH_CALLBACK_URL** are not required for this Device Flow implementation.

## 3. Deploy and verify

Deploy `main`. In Telegram, open GitHub → Подключить аккаунт. The bot displays a one-time code and a GitHub verification link. After the user confirms the code, the bot offers the GitHub App installation page. Select the account and repositories. After GitHub displays the success page, return to Telegram and open Мои репозитории.

In an AI chat, send a repository URL. The model receives a filtered source snapshot. If it returns GITHUB_FILE blocks, the bot creates a separate branch and opens a Pull Request instead of writing directly to the default branch. Review and merge the PR after checking the changes.

Users can remove the installation from GitHub settings or use Отключить GitHub in the bot. Removing the installation in GitHub also revokes its access.

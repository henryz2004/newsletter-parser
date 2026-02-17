# Gmail API Setup Guide

This guide walks you through creating a Google Cloud project and enabling the Gmail API so the newsletter parser can read and send emails on your behalf.

## Prerequisites

- A Google account (the Gmail inbox you want to parse)
- Python 3.11+ installed
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Step 1: Create a Google Cloud Project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown at the top → **New Project**
3. Name it something like `newsletter-parser` → **Create**
4. Make sure the new project is selected in the dropdown

## Step 2: Enable the Gmail API

1. In the Cloud Console, go to **APIs & Services → Library**
2. Search for **Gmail API**
3. Click on it → **Enable**

## Step 3: Configure the OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** user type → **Create**
3. Fill in the required fields:
   - **App name:** Newsletter Parser
   - **User support email:** your email
   - **Developer contact:** your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.modify`
6. Click **Save and Continue**
7. On the **Test users** page, click **Add Users** and add your Gmail address
8. Click **Save and Continue** → **Back to Dashboard**

## Step 4: Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Select **Desktop app** as the application type
4. Name it `newsletter-parser-desktop`
5. Click **Create**
6. Click **Download JSON** on the confirmation dialog
7. Rename the downloaded file to `credentials.json`
8. Move it to the root of this project directory:
   ```bash
   mv ~/Downloads/client_secret_*.json ./credentials.json
   ```

## Step 5: Install Dependencies & Authenticate

```bash
# Install the project
uv sync

# Run the setup command to trigger the OAuth flow
uv run newsletter-parser setup
```

This will open a browser window asking you to authorize the app. After granting access, a `token.json` file will be saved locally for future runs.

## Step 6: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

## Step 7: Run

```bash
# Manual run
uv run newsletter-parser run

# Or set up a cron job for twice-daily execution (7 AM and 7 PM)
crontab -e
# Add this line (adjust the path):
# 0 7,19 * * * cd /path/to/newsletter-parser && uv run newsletter-parser run >> /tmp/newsletter-parser.log 2>&1
```

## Troubleshooting

- **"Access blocked" error during OAuth:** Make sure you added your email as a test user in Step 3.7
- **Token expired:** Delete `token.json` and re-run `uv run newsletter-parser setup`
- **Quota errors:** The Gmail API has a default quota of 250 units/second. The parser stays well within this limit.

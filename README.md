# Hermes Email Workflows

A local Gmail automation dashboard for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

```text
Gmail watch → Google Cloud Pub/Sub pull → Gmail-query rules
            → one fresh Hermes task per matched rule
            → one combined Telegram notification per email
```

The service never replies by email. It runs on `127.0.0.1`, keeps credentials and state outside the repository, and uses OAuth rather than an IMAP password or Gmail App Password.

## Behavior

- Monitors one OAuth-connected Gmail account at a time. Reconnect to switch accounts.
- Receives near-real-time mailbox changes through Gmail API `watch` and a local Pub/Sub pull subscriber.
- Evaluates every enabled rule using Gmail's own search language. One email may match multiple rules.
- Runs each matching rule as a separate, fresh `hermes chat` session.
- Combines the rule outputs into one Telegram message through `hermes send --to telegram`.
- Marks the email read as soon as at least one rule matches.
- Leaves unmatched emails unread.
- Deduplicates Gmail message IDs in SQLite. If Telegram delivery fails, it retries the saved notification without rerunning Hermes tasks.
- Renews the Gmail watch before its seven-day expiration.

## Requirements

- macOS with Python 3.11+
- Hermes Agent configured with a Telegram home channel
- A Google Cloud project where you can manage Pub/Sub resources
- An OAuth Desktop client JSON from that same project

Gmail requires the Pub/Sub topic to live in the same Google Cloud project as the OAuth client used for the Gmail API call.

## Google Cloud preparation

For the OAuth client's project:

1. Enable the **Gmail API** and **Cloud Pub/Sub API**.
2. Configure the OAuth consent screen.
3. If the app is in Testing, add every Gmail account you may connect as a test user.
4. Create an OAuth client with application type **Desktop app** and download its JSON file.
5. Ensure the signing-in Google account can create Pub/Sub topics/subscriptions and edit topic IAM policy. Project Owner or Pub/Sub Admin plus permission to set IAM policy is sufficient.

The dashboard requests only:

- `gmail.modify` — fetch messages and remove the `UNREAD` label after a match
- `pubsub` — create/use the local pull subscription

During setup the app creates a topic and pull subscription, then grants
`gmail-api-push@system.gserviceaccount.com` the `roles/pubsub.publisher` role on the topic, as required by Gmail push notifications.

## Install

```bash
git clone git@github.com:shuyangli/hermes-email-workflows.git
cd hermes-email-workflows
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
chmod +x scripts/*.sh
```

For this machine, Python 3.11 is available at:

```bash
~/.hermes/hermes-agent/venv/bin/python
```

## Run locally

```bash
.venv/bin/hermes-email-workflows
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787), select **Setup**, enter the Google Cloud project and OAuth client JSON path, and approve Google OAuth.

The default client path is `~/.hermes/google_client_secret.json`. The existing client's project is `shuyangli-claw`.

## Run continuously with launchd

```bash
scripts/install_launchagent.sh
```

This installs and starts:

```text
~/Library/LaunchAgents/com.shuyang.hermes-email-workflows.plist
```

Logs:

```text
~/Library/Logs/hermes-email-workflows/stdout.log
~/Library/Logs/hermes-email-workflows/stderr.log
```

Uninstall the service without deleting application data:

```bash
scripts/uninstall_launchagent.sh
```

## Rules

Each rule has:

- **Gmail query** — any Gmail search expression, such as `from:billing@example.com is:unread` or `label:orders subject:(receipt OR invoice)`
- **Hermes prompt template** — the task passed to a fresh Hermes session
- **Priority** — lower values execute first
- **Timeout** — maximum runtime for that Hermes task
- **Toolsets / skills** — comma-separated Hermes capabilities to preload; toolsets default to read-only `web` rather than the full CLI tool set
- **Account restriction** — optional; blank rules apply to whichever account is active

Available prompt variables:

```text
${sender}
${to}
${subject}
${body}
${gmail_id}
${thread_id}
```

Example:

```text
Extract the merchant, total, and due date from this invoice.

Subject: ${subject}
From: ${sender}
Body:
${body}
```

Email content is wrapped as untrusted data before it reaches Hermes. Keep toolsets narrow for rules that process mail from external senders.

## Data and credentials

Nothing sensitive is stored in the repository.

```text
~/.local/share/hermes-email-workflows/workflows.db       SQLite state and rules
~/.local/share/hermes-email-workflows/google-token.json  OAuth token, mode 0600
```

The app binds only to `127.0.0.1:8787`.

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Health check:

```bash
curl http://127.0.0.1:8787/healthz
```

## Failure and delivery semantics

- Pub/Sub notifications are acknowledged only after history processing succeeds.
- Gmail message IDs are unique per connected account and form the deduplication key.
- The combined Telegram notification is persisted before delivery. A delivery retry does not rerun completed Hermes tasks.
- Task failures are included in the combined Telegram notification instead of preventing other matched rules from running.
- The Gmail history cursor advances only after all messages in the notification are handled.

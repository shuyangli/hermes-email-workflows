# Google Cloud provisioning handoff

This document is a self-contained handoff for provisioning the Google Cloud resources used by `hermes-email-workflows`.

## Security requirements

- Perform all owner/admin authentication on the provisioning device.
- Do not send Cloud owner credentials, Application Default Credentials (ADC) files, OAuth tokens, refresh tokens, or broad service-account keys to the machine running Hermes.
- Do not replace the existing OAuth client unless it is genuinely unusable.
- Give the local runtime only a dedicated, least-privilege Pub/Sub identity.
- Never commit OAuth client JSON, OAuth tokens, ADC files, or service-account keys.
- Report resource names, IAM bindings, and verification output, but redact tokens, keys, client secrets, and credential file contents.

## Expected configuration

| Resource | Value |
|---|---|
| Project | `shuyangli-claw` |
| APIs | `gmail.googleapis.com`, `pubsub.googleapis.com` |
| Pub/Sub topic | `hermes-email-events` |
| Full topic path | `projects/shuyangli-claw/topics/hermes-email-events` |
| Pull subscription | `hermes-email-workflows-local` |
| Full subscription path | `projects/shuyangli-claw/subscriptions/hermes-email-workflows-local` |
| Runtime service account | `hermes-email-workflows@shuyangli-claw.iam.gserviceaccount.com` |
| Gmail publisher | `gmail-api-push@system.gserviceaccount.com` |

The Pub/Sub topic must be in the same Google Cloud project as the OAuth Desktop client used for Gmail. Confirm that the existing OAuth client's embedded project ID is `shuyangli-claw` before provisioning.

## Provisioning tasks

1. Authenticate `gcloud` on the provisioning device with an account authorized to administer `shuyangli-claw`.
2. Confirm that `shuyangli-claw` is the project embedded in the existing OAuth Desktop client.
3. Enable the Gmail API and Cloud Pub/Sub API.
4. Create the Pub/Sub topic if it does not exist.
5. Grant `gmail-api-push@system.gserviceaccount.com` the `roles/pubsub.publisher` role on the topic. Do not grant it a project-wide role.
6. Create the pull subscription if it does not exist, with:
   - acknowledgment deadline: 60 seconds;
   - message retention: 7 days;
   - expiration: never.
7. Create the dedicated runtime service account if it does not exist.
8. Grant the runtime service account `roles/pubsub.subscriber` on the subscription only.
9. Inspect the OAuth consent configuration:
   - use a Desktop OAuth client;
   - configure Gmail API access;
   - request exactly `https://www.googleapis.com/auth/gmail.modify`;
   - if the application remains in Testing, add the intended Gmail mailbox as a test user;
   - do not request Cloud Platform scopes or broader Gmail scopes through the Gmail OAuth client.
10. Do not generate a broad project-admin service-account key.
11. If a local credential must be generated, generate it only for the dedicated runtime service account and transfer it manually without pasting its contents into chat. A JSON key is a long-lived secret; use a separate restricted OS account or stronger identity mechanism if stronger isolation is required.

An external OAuth application left in Testing commonly receives Gmail-scoped refresh tokens that expire after seven days. Confirm the intended publication and verification posture before relying on unattended long-term operation.

## Reference commands

These commands are designed to be idempotent where practical.

```bash
PROJECT_ID=shuyangli-claw
TOPIC=hermes-email-events
SUBSCRIPTION=hermes-email-workflows-local
RUNTIME_SA=hermes-email-workflows
RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"

gcloud services enable \
  gmail.googleapis.com \
  pubsub.googleapis.com \
  --project="$PROJECT_ID"

gcloud pubsub topics describe "$TOPIC" \
  --project="$PROJECT_ID" \
  >/dev/null 2>&1 ||
gcloud pubsub topics create "$TOPIC" \
  --project="$PROJECT_ID"

gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

gcloud pubsub subscriptions describe "$SUBSCRIPTION" \
  --project="$PROJECT_ID" \
  >/dev/null 2>&1 ||
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
  --project="$PROJECT_ID" \
  --topic="$TOPIC" \
  --ack-deadline=60 \
  --message-retention-duration=7d \
  --expiration-period=never

gcloud iam service-accounts describe "$RUNTIME_SA_EMAIL" \
  --project="$PROJECT_ID" \
  >/dev/null 2>&1 ||
gcloud iam service-accounts create "$RUNTIME_SA" \
  --project="$PROJECT_ID" \
  --display-name="Hermes email workflows subscriber"

gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/pubsub.subscriber"
```

## Verification commands

```bash
gcloud services list \
  --enabled \
  --project="$PROJECT_ID" \
  --filter='NAME:(gmail.googleapis.com OR pubsub.googleapis.com)'

gcloud pubsub topics describe "$TOPIC" \
  --project="$PROJECT_ID"

gcloud pubsub topics get-iam-policy "$TOPIC" \
  --project="$PROJECT_ID"

gcloud pubsub subscriptions describe "$SUBSCRIPTION" \
  --project="$PROJECT_ID"

gcloud pubsub subscriptions get-iam-policy "$SUBSCRIPTION" \
  --project="$PROJECT_ID"
```

The provisioning agent should return:

- project ID;
- full topic path;
- full subscription path;
- runtime service-account email;
- enabled-API verification;
- sanitized topic IAM policy showing the Gmail publisher binding;
- sanitized subscription IAM policy showing the runtime subscriber binding;
- subscription configuration;
- OAuth consent/client readiness;
- any organization-policy or permission blockers.

## Pre-provisioned resources mode

The setup form has a "Resources are pre-provisioned" checkbox. When checked, the OAuth callback:

- uses the configured topic and subscription without creating them;
- performs no IAM reads or writes;
- performs no subscription or topic metadata reads, because a consume-only identity cannot call `pubsub.subscriptions.get`;
- trusts the resource paths supplied by the operator, so provisioning must verify that the subscription exists and is attached to the configured topic before setup.

With the checkbox enabled, a runtime identity with `pubsub.subscriptions.consume` on the subscription is sufficient to complete OAuth setup and run the subscriber. Leave it unchecked only when the local Application Default Credentials are intentionally privileged enough to create resources and administer topic IAM — the historical behavior, which re-writes topic IAM on every reconnect.

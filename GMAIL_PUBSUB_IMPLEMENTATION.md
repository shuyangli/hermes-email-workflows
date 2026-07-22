# Gmail API + Pub/Sub pull implementation specification

Research date: 2026-07-21. This is a design/setup specification only; no Google Cloud or Gmail resources were modified.

## 1. Recommended shape

Use two independent credential sets:

1. **End-user Gmail OAuth** for the one active mailbox, with only `https://www.googleapis.com/auth/gmail.modify`.
2. **Google Cloud credentials** for the local Pub/Sub subscriber, authorized only to consume the pull subscription. Do not add Cloud Platform scopes to the Gmail token merely to reuse one token.

Data path:

```text
Gmail mailbox
  -> users.watch
  -> projects/shuyangli-claw/topics/hermes-gmail-watch
  -> pull subscription hermes-gmail-pull
  -> local StreamingPull receiver
  -> history.list from SQLite cursor
  -> durable candidate/message records
  -> evaluate every enabled rule
  -> durable (message_id, rule_id) jobs
  -> remove UNREAD once >=1 match is durably recorded
  -> one fresh `hermes chat` per matched rule
  -> combine results for that message
  -> one `hermes send --to telegram`
```

A Gmail notification is only a **mailbox-change hint**. Its decoded body contains an email address and a mailbox `historyId`, not Gmail message IDs or message content. Always use `users.history.list` to discover changes.

## 2. Cloud setup and IAM

The Pub/Sub topic's project ID must exactly match the Google developer project that executes `users.watch`. The existing OAuth client says `project_id: shuyangli-claw`, so the watch topic must be in that project unless the Gmail OAuth client/project is changed.

Illustrative one-time setup (operator runs this; the app must not silently create cloud resources):

```bash
PROJECT_ID=shuyangli-claw
TOPIC=hermes-gmail-watch
SUBSCRIPTION=hermes-gmail-pull
SUBSCRIBER_SA=hermes-email-subscriber

gcloud config set project "$PROJECT_ID"
gcloud services enable gmail.googleapis.com pubsub.googleapis.com

gcloud pubsub topics create "$TOPIC"

gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
  --member='serviceAccount:gmail-api-push@system.gserviceaccount.com' \
  --role='roles/pubsub.publisher'

gcloud pubsub subscriptions create "$SUBSCRIPTION" \
  --topic="$TOPIC" \
  --ack-deadline=60 \
  --message-retention-duration=7d \
  --expiration-period=never

gcloud iam service-accounts create "$SUBSCRIBER_SA"
gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION" \
  --member="serviceAccount:${SUBSCRIBER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role='roles/pubsub.subscriber'
```

Important constraints:

- Gmail's publisher principal is exactly `gmail-api-push@system.gserviceaccount.com`; grant it `roles/pubsub.publisher` on the **topic**, not broad project Editor.
- Domain Restricted Sharing can block adding that Google-managed principal. An organization-policy exception is then required.
- The local consumer needs `pubsub.subscriptions.consume` for pull, acknowledge, and ModifyAckDeadline. `roles/pubsub.subscriber` contains it and can be granted on just the subscription.
- Creating the subscription itself requires `pubsub.subscriptions.create` on its project plus `pubsub.topics.attachSubscription` on the topic. An operator can use `roles/pubsub.editor` during setup, but the runtime identity should not retain that role.
- Pub/Sub does not accept API keys. Client libraries use Application Default Credentials (ADC).
- For interactive local development, `gcloud auth application-default login` is supported. For an unattended LaunchAgent, prefer a dedicated workload identity/impersonation arrangement where practical. A service-account JSON key also works but is a long-lived secret: store outside the repo, mode `0600`, and expose its path to launchd as `GOOGLE_APPLICATION_CREDENTIALS`.
- Never commit OAuth client JSON, Gmail tokens, ADC files, or service-account keys.

Recommended subscription properties for this low-volume local app:

- Pull subscription; default at-least-once delivery is sufficient because SQLite idempotency is still required for Gmail/history duplicates.
- `ack-deadline=60` is a reasonable initial value, but use the high-level library's lease management.
- Retention defaults to 7 days (range 10 minutes–31 days). Seven days aligns with Gmail's usual history lifetime but does not replace Gmail reconciliation.
- Set subscription expiration to never for a laptop that may be offline for over 31 days; otherwise an idle subscription defaults to expiring after 31 days.
- Exponential retry/backoff is optional. A dead-letter topic is usually unnecessary because Pub/Sub messages are only hints; recovery should come from `history.list` plus reconciliation.
- Exactly-once is supported only for pull subscriptions and may be enabled, but it has higher latency and does **not** eliminate application dedupe: Gmail can coalesce/drop notifications and the same Gmail message may occur in multiple history records. Prefer simple at-least-once plus durable unique constraints.

## 3. Gmail OAuth

Use one OAuth client appropriate for a local installed app (normally **Desktop app**) and request offline access through Google's maintained OAuth libraries. Persist the refresh token securely.

Required scope:

```text
https://www.googleapis.com/auth/gmail.modify
```

Why this scope:

- `watch`, `history.list`, `messages.list`, and `messages.get` accept `gmail.modify`.
- Marking read uses `messages.modify`, which requires `gmail.modify` (or the much broader full-mail scope).
- `gmail.readonly` cannot mark read.
- `gmail.metadata` cannot read bodies and cannot use the `q` parameter on `messages.list`.
- Do not request `https://mail.google.com/`; Google explicitly reserves it for cases needing immediate permanent deletion bypassing Trash.

`gmail.modify` is a **restricted** Gmail scope. Public/production apps need the applicable OAuth verification, and storing or transmitting restricted-scope data on servers can trigger a security assessment. A genuinely personal app used by fewer than 100 personally known users is not treated as a production app in Google's OAuth policy, but tokens and user data must still be protected.

Operational OAuth pitfalls:

- An external consent screen left in **Testing** issues refresh tokens that expire in 7 days when Gmail scopes are requested. Move to an appropriate publishing status/verification posture for durable automation.
- A Gmail-scoped refresh token can stop working after the user changes their Google password.
- Refresh tokens can also be revoked, expire after six months of non-use, or be invalidated by token-count/admin-session policies. Surface `invalid_grant` as “reconnect Gmail”; do not spin retry forever.
- There is a limit of 100 live refresh tokens per Google Account per OAuth client ID; repeatedly forcing consent during development can invalidate the oldest token.
- Store the granted scope set and verify it includes `gmail.modify` before enabling the watcher.
- Encrypt tokens at rest if feasible and restrict file permissions. Never log authorization codes, access tokens, refresh tokens, or raw message bodies.

For “one active account,” store one active account row and one cursor. On account switch: stop intake, wait for/abort old processing according to product policy, call `users.stop` for the old mailbox if credentials remain valid, revoke/delete the old local token if requested, clear active routing state, authorize the new mailbox, verify its profile/email, baseline it, and create its watch. A topic may be shared, but reject/ack notifications whose `emailAddress` does not equal the active authenticated account.

## 4. Watch request and renewal

Endpoint:

```http
POST https://gmail.googleapis.com/gmail/v1/users/me/watch
Authorization: Bearer <Gmail access token>
Content-Type: application/json

{
  "topicName": "projects/shuyangli-claw/topics/hermes-gmail-watch"
}
```

For correctness, do **not** apply a watch label filter initially. A watch is only a wake-up signal, and watching all mailbox changes avoids subtle misses. Filter discovered messages locally to `messageAdded` plus current `UNREAD`. If product scope is explicitly inbox-only, `labelIds: ["INBOX"]` with `labelFilterBehavior: "include"` is possible. Do not use deprecated `labelFilterAction`.

Successful response:

```json
{
  "historyId": "1234567890",
  "expiration": "<epoch milliseconds>"
}
```

Store both as strings/64-bit-safe values; do not put Gmail IDs through JavaScript floating-point numbers. The successful watch also immediately publishes a notification.

Renewal rules:

- Gmail requires `watch` at least every 7 days and recommends once per day.
- Schedule renewal daily and also renew when `expiration - now` crosses a safety threshold (for example 24 hours).
- A renewal response gives a current `historyId`, but **do not blindly replace the existing sync cursor with it**, or changes between the cursor and renewal can be skipped. Keep the old cursor, drain `history.list`, and only advance through the normal durable sync transaction. Store the new expiration separately.
- On renewal failure, retain the prior cursor, mark health degraded, retry with bounded exponential backoff, and expose the error in the dashboard.
- `users.stop` stops updates; new notifications should cease within a few minutes.

## 5. Pull subscriber mechanics

Use `google-cloud-pubsub`'s high-level asynchronous subscriber (`SubscriberClient.subscribe`) unless strict process-resource control demands unary Pull. Google's current recommendation is the high-level library with asynchronous `StreamingPull`; it handles reconnection, flow control, and ack-deadline lease extension.

Python shape:

```python
from google.cloud import pubsub_v1

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(
    "shuyangli-claw", "hermes-gmail-pull"
)
flow = pubsub_v1.types.FlowControl(max_messages=1)
future = subscriber.subscribe(
    subscription_path,
    callback=on_pubsub_message,
    flow_control=flow,
)
```

`message.data` is bytes containing the Base64-decoded Pub/Sub data. Parse it as UTF-8 JSON:

```json
{"emailAddress":"user@example.com","historyId":"9876543210"}
```

The Pub/Sub `message_id` is unrelated to Gmail message IDs.

ACK policy:

- ACK only after the notification has been converted into durable local state and the history cursor/candidate enqueue transaction has committed.
- NACK (or let the deadline expire) on transient Gmail, token-refresh, SQLite-busy, or local-shutdown failures before that commit.
- ACK malformed messages, inactive-account notifications, and notifications already covered by the durable cursor after recording diagnostics; retries cannot repair them.
- Do not keep a Pub/Sub delivery outstanding while `hermes chat` tasks run. Durable local jobs are the handoff boundary; acknowledge first, then workers execute them.
- Serialize sync for the one active account (`asyncio.Lock`/single consumer). Notifications can arrive out of order, be duplicated, or carry a history ID already behind the cursor.
- The subscriber callback runs on library-managed threads. Do not use a thread-bound SQLite connection directly in it. Hand off to the app event loop/queue or open a correctly configured per-thread connection.
- Configure graceful LaunchAgent shutdown: stop accepting callbacks, finish/commit current sync, ACK if committed, cancel the streaming future, close the subscriber, then close DB connections.

## 6. History and recovery algorithm

Persist `last_history_id` per OAuth account. Treat IDs as opaque decimal strings; their ordering is chronological but values are non-contiguous.

For each wake-up (Pub/Sub notification or periodic safety poll), under the account sync lock:

1. Load the durable cursor `C`.
2. Call:

   ```http
   GET /gmail/v1/users/me/history?startHistoryId=C&historyTypes=messageAdded&maxResults=500
   ```

3. Follow every `nextPageToken`. Results are chronological. Use `messagesAdded`, not the generic `messages` array (the generic array can duplicate specific change entries).
4. Deduplicate all discovered Gmail message IDs in memory and against SQLite.
5. For each unseen ID, call `messages.get(format=FULL)` (or `RAW` if exact MIME fidelity is needed). History records usually contain only `id` and `threadId`.
6. Retain as candidates only messages whose current `labelIds` includes `UNREAD`. Decide explicitly whether Spam/Trash are excluded; the recommended default is to skip them.
7. In one SQLite transaction, insert candidate/message records idempotently and advance the cursor to the final `history.list` response's `historyId`. Commit.
8. ACK the Pub/Sub hint.
9. Process durable candidates independently.

Do not set the cursor merely to the notification's `historyId`: the history response is the authoritative complete range and its returned `historyId` is the safe next cursor after all pages have been durably handled.

A notification above Gmail's per-user maximum rate of one event/second can be dropped, and extreme delays/drops are possible. Therefore add a periodic safety sync (for example every 5–15 minutes while online) that runs the same `history.list(C)` path even with no notification.

### Initial baseline (no unintended old-mail processing)

Default onboarding should not run workflows on all pre-existing unread mail:

1. Start/renew the watch and obtain current history ID `H0`.
2. Enumerate current unread message IDs and store them as `baseline_ignored`/seen without changing read status.
3. Persist `H0` only after the baseline IDs are durable.
4. Serialize this operation with notification handling. The immediate watch notification can then be handled normally; any post-`H0` changes are recovered by history, and duplicate IDs are harmless.

Offer an explicit “process existing unread” backfill option separately.

### Stale cursor / HTTP 404

`startHistoryId` is usually valid for at least a week but can, rarely, be valid for only hours. An invalid/out-of-date cursor returns HTTP 404 and requires full sync/reconciliation.

Recovery that preserves “new only” semantics:

1. Enter a single-account recovery lock and mark health as recovering.
2. Call `watch` to get a fresh boundary `H0` (and expiration), without overwriting the old cursor yet.
3. Enumerate current unread IDs (`messages.list(labelIds=UNREAD)`). Compare with durable message IDs and the initial baseline set. Unseen IDs are recovery candidates; this is why the initial baseline must be stored.
4. Durably insert those candidates and set cursor to `H0` in one transaction.
5. Immediately run `history.list(startHistoryId=H0)` to capture races after the boundary.
6. Resume normal operation. Dedupe makes overlap safe.

There is an unavoidable ambiguity if local baseline/seen state was lost: Gmail cannot tell the app which currently unread messages predate installation. In that case require the operator to choose “baseline current unread” or “process current unread,” rather than silently acting.

## 7. Exact per-message rule matching

Gmail has no API that accepts an arbitrary Gmail search query plus an internal Gmail message ID as a direct predicate. `users.messages.list(q=...)` is the search API. It returns only message `id`/`threadId`, supports Gmail search-box syntax, defaults to 100 and maxes at 500 per page.

For each new candidate, obtain its RFC 5322 `Message-ID` header, then evaluate **all enabled rules before removing `UNREAD`** with:

```text
(<user rule query>) rfc822msgid:<the-message-id> is:unread
```

The Gmail API documentation itself gives the pattern `from:... rfc822msgid:<...> is:unread`. Call `messages.list(q=combined_query, maxResults=10)` and count the candidate as matched only if the returned Gmail internal ID equals the candidate ID. Parenthesizing the user rule prevents a top-level `OR` from escaping the identity/unread constraints.

Fallbacks/pitfalls:

- `Message-ID` is not guaranteed to exist and, although intended to be unique, duplicates occur. Always compare the returned internal Gmail ID, not only the header.
- If no usable `Message-ID` exists, run the rule query with `labelIds=["UNREAD"]`, paginate, and intersect results with the pending candidate IDs. This is exact but can be expensive for broad rules. Batch all pending candidates per rule so the full result set is scanned once, not once per message.
- Do not mark the message read after the first rule check. That would make subsequent checks containing the base `is:unread` fail and violate multi-match behavior.
- Gmail API search is message-oriented; labels can differ between messages in one thread. Store and process the internal message ID, not thread ID.
- Use `labelIds=["UNREAD"]` as a server-side base constraint in the broad fallback. All listed labels must match.
- Search does not include Spam/Trash unless `includeSpamTrash=true`; leave false unless product requirements say otherwise.
- Preserve each rule's original query exactly for display/debugging and store the exact combined query used for each evaluation.

Recommended candidate state machine:

```text
DISCOVERED -> MATCHING -> MATCHES_DURABLE -> READ_MARKED -> RUNNING
           -> NO_MATCH (leave unread; complete)
MATCHES_DURABLE/RUNNING -> COMPLETE | RETRYABLE_FAILURE | TERMINAL_FAILURE
```

After all enabled rules are evaluated:

1. Insert one job per match with unique key `(account_id, gmail_message_id, rule_id)` and snapshot the rule query/prompt template in the job. Commit matches/jobs first.
2. If at least one match, immediately call:

   ```http
   POST /gmail/v1/users/me/messages/{id}/modify
   {"removeLabelIds":["UNREAD"]}
   ```

3. Record `READ_MARKED`. The call is naturally idempotent if retried.
4. Run every matched rule as a separate fresh `hermes chat` process/task. Never reuse a chat session between rules.
5. Store each result independently. Once every matched job reaches a terminal result, compose one Telegram message for the email and use a unique delivery/outbox key for the single `hermes send --to telegram` action.

The DB-before-Gmail order is intentional: if the process crashes after removing `UNREAD`, durable jobs still exist. If Gmail were modified first and SQLite then failed, restart logic that selects current unread messages could permanently lose the work.

## 8. SQLite idempotency/recovery requirements

At minimum enforce unique constraints on:

- `(account_id, gmail_message_id)` for messages/candidates.
- `(account_id, gmail_message_id, rule_id)` for matched jobs.
- `(account_id, gmail_message_id)` for Telegram outbox/delivery aggregation.
- Optionally Pub/Sub `message_id` for diagnostics; do not rely on it for Gmail dedupe.

Persist rule and prompt snapshots on each job so edits do not change a retry midway. Record stdout, stderr, exit status, attempt count, and timestamps for each fresh Hermes task. Use an outbox row for Telegram and mark sent only after the real command succeeds. Since a crash can happen after Telegram accepts a send but before SQLite records success, true exactly-once Telegram delivery is impossible without an idempotency facility at the destination; expose the rare duplicate risk and include a stable message key in logs/content if acceptable.

SQLite operational settings for FastAPI + subscriber + workers: WAL mode, `busy_timeout`, short write transactions, and one writer queue or disciplined per-thread connections. Never hold a DB transaction open across a long Hermes subprocess. The only external Gmail call intentionally sequenced after a committed transaction is mark-read.

## 9. Health/status fields for the dashboard

Expose at least:

- Active Gmail address and granted scope status.
- Watch `expiration`, last successful renewal, and renewal error.
- Durable `last_history_id`, last successful partial sync, last full reconciliation.
- Pub/Sub stream connected state, last Pub/Sub publish time/message ID, ACK/NACK counts.
- Oldest unprocessed local candidate/job and retry counts.
- OAuth `invalid_grant` / reconnect-required state.
- Subscription backlog metrics if available.

Never display tokens, credentials, complete raw headers, or full bodies in routine logs.

## 10. Primary sources

- Gmail push notifications (setup, publisher principal, payload, ACK, renewal, rate/reliability): https://developers.google.com/workspace/gmail/api/guides/push
- `users.watch` reference (topic-project equality, filters, response, scopes): https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/watch
- `users.history.list` reference (pagination, 404, history fields/scopes): https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Gmail synchronization guide: https://developers.google.com/workspace/gmail/api/guides/sync
- `users.messages.list` (`q`, `rfc822msgid`, pagination, label behavior): https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
- `users.messages.get`: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get
- `users.messages.modify`: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify
- Gmail scope classification: https://developers.google.com/workspace/gmail/api/auth/scopes
- Google OAuth overview and refresh-token expiration: https://developers.google.com/identity/protocols/oauth2
- Google OAuth policies: https://developers.google.com/identity/protocols/oauth2/policies
- Pub/Sub pull mechanics: https://cloud.google.com/pubsub/docs/pull
- Pub/Sub authentication/ADC: https://cloud.google.com/pubsub/docs/authentication
- Pub/Sub IAM roles/permissions: https://cloud.google.com/pubsub/docs/access-control
- Pull subscription creation/properties: https://cloud.google.com/pubsub/docs/create-subscription and https://cloud.google.com/pubsub/docs/subscription-properties
- Exactly-once limitations: https://cloud.google.com/pubsub/docs/exactly-once-delivery

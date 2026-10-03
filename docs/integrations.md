# Integrations

An **integration** is a workspace's configured, credentialed instance of a connector. Connectors (the code) are described
in [connectors](connectors.md); this page is for operators and users.

| Kind | Triggers (start workflows) | Actions (workflow steps) | Notes |
|---|---|---|---|
| `github` | `issues.opened`, `issues.labeled`, `pull_request.opened`, `check_suite.completed` | `github.comment`, `github.add_label`, `github.create_pull_request` | one repository per integration |
| `slack` | `app_mention`, `message.channels` | `slack.post_message` | only the channels you list; bot messages never trigger |
| `linear` | `Issue.create`, `Issue.update`, `Comment.create` | `linear.comment` | comment is the only write: no state changes |
| `jira` | `issue_created`, `issue_updated`, `comment_created` | `jira.comment` | Jira Cloud, one project, https only |
| `notion` | none | `notion.read_page` (read only, no approval) | only the pages you list; the text is untrusted data |
| `webhook` | any event types you list | `webhook.post` | senders sign with HMAC-SHA256; posts go only to named endpoints you register |

Writes always need a human approval node upstream in the workflow (validation rejects a workflow that can reach an external
write without one) and are subject to policy (`action.<name>`, for example `action.slack.post_message`).

## Connect one

```bash
patchquest integrations kinds
patchquest secrets keygen                            # once; keep the output in the server's environment as PATCHQUEST_SECRET_KEY
patchquest integrations add slack --workspace ws_x --set channels='["C0123ABC"]' \
    --secret bot_token --secret signing_secret       # prompts; values are stored encrypted
patchquest integrations add github --set repo=acme/widgets --secret-env token=GITHUB_TOKEN --secret-env webhook_secret=GITHUB_WEBHOOK_SECRET
patchquest integrations test <id>
```
or `POST /api/integrations` (admins; the web form uses `GET /api/integrations/kinds`). A workspace has at most one
integration of each kind, so `slack.post_message` means one thing there.

**Secrets** are `{"env": "VARIABLE"}` (read from the server's environment when used) or `{"value": "..."}` (stored
encrypted, see below). They are write-only: no route returns them, the audit log records only which were set, and a
failed call's message never contains them. `patchquest doctor` fails when stored secrets exist but the key is missing.

**Encryption.** Fernet (AES-128-CBC + HMAC-SHA256) under `PATCHQUEST_SECRET_KEY`, which is never stored with the data. Each
ciphertext embeds the workspace, owner and name it belongs to and is checked on decryption, so a ciphertext copied into
another row or tenant is rejected. Rotate: set the new key as `PATCHQUEST_SECRET_KEY` and the old one(s) in
`PATCHQUEST_SECRET_KEY_PREVIOUS` (comma separated), re-save each secret, then drop the old key. Not provided:
hardware-backed or per-tenant keys, or protection from someone who can read both the server's environment and database.

## Receiving events

Point the sender at `https://<your host>/hooks/<integration id>` (the id is shown by `integrations list`; add your public
host name to `allowed_hosts`). The endpoint has no bearer token: the **signature** authenticates the sender, and is checked
before anything is parsed or stored.

- Unknown, disabled and inbound-less integrations all answer 404; a bad or missing signature is 401 (and audited);
  oversize bodies 413; more than 300 requests/minute per integration 429.
- A valid delivery is deduplicated per workspace (`connector_events`), normalised, and given to the workflow engine, which
  starts every active workflow whose trigger type and filter match (`github.issues.labeled`, `slack.app_mention`,
  `webhook.deploy.finished`, ...) and wakes runs waiting for it. Valid but irrelevant deliveries (other repository or
  channel, a bot's message, an unlisted event type) are acknowledged as `ignored` so the sender stops retrying.
- Slack's `url_verification` handshake is answered only for a correctly signed request.
- **Event payload text is untrusted** (it can contain prompt injection). Workflow templates pass it to agents as task text,
  never as policy; nothing from an event is stored as a preference or procedure.
- `GET /api/integrations/<id>/events` shows what arrived and what became of it.

### The generic webhook

Sign `"<unix time>.<raw body>"` with the shared secret (HMAC-SHA256) and send `X-PatchQuest-Timestamp`,
`X-PatchQuest-Signature: sha256=<hex>`, `X-PatchQuest-Delivery` (unique id) and `X-PatchQuest-Event`. Deliveries more than
five minutes away from now are refused.

## Acting

A workflow action runs against the **workflow's own workspace's** integration, with that integration's credentials; a
workspace without one fails the step with "no slack connector is connected". The engine records the idempotency key
before calling, and after a crash asks the connector whether the effect already exists (GitHub/Linear/Jira by a marker in
the text, Slack by message metadata) instead of repeating it. `webhook.post` cannot be reconciled, so a crash during it
leaves the step `uncertain` for a person to resolve.

## Going live

Everything above is verified only against simulators. To check a connector against the real service: create an
integration with real credentials in a scratch workspace, run `patchquest integrations test <id>`, send a real event
(Slack's *Event Subscriptions > Request URL* shows the handshake result directly), and approve one action. Report any
difference between the simulator and the service as a bug in `testing/mock_server.py`.

## Not done

Slack interactive approvals (buttons), OAuth install flows and token refresh, GitLab/Teams/Discord/e-mail, multiple
instances of one kind per workspace, and pinning outbound connections to the validated IP (see the SSRF notes).

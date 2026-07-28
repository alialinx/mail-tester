# Mail Tester

Mail Tester is a self hosted service that checks the quality and deliverability of outgoing emails.

Users send an email to a temporary test address. The system receives the email on its own SMTP
server, analyzes it in the background and returns a score with explanations.

---

## What This Tool Does

Mail Tester helps you understand why an email may fail or succeed.

You can test:

- Email server configuration
- SPF, DKIM and DMARC records
- Reverse DNS (rDNS)
- Sending IP blacklist status
- The SMTP connection itself: client IP, HELO name, STARTTLS version and cipher
- Envelope sender (MAIL FROM) and the From header
- Required and recommended email headers
- Basic email content quality
- SpamAssassin score and rules

---

## How It Works

1. The user calls `POST /generate`.
2. The system creates a unique temporary address, for example `test-a92bd12f98bc4@yourdomain.com`,
   stores it in MongoDB and writes it to Redis with a TTL.
3. The user sends an email to that address.
4. Our own Postfix (`mx` service) receives it on port 25. For every `RCPT TO` it asks the
   `ingest` service whether the address is live. Unknown or expired addresses are rejected inside
   the SMTP conversation, so spam never enters the queue.
5. Postfix delivers the mail over LMTP to `ingest`, which stores the raw message in GridFS
   together with the connection facts and queues the analysis task.
6. The Celery worker analyzes the mail and stores the result.
7. The browser gets the result pushed over SSE (`GET /events/{to_address}`) and reads it from
   `GET /result/{to_address}`.

There is no IMAP polling. The address is single use: once a mail arrives it is removed from Redis
and further mails to it are rejected.

---

## Architecture

```
internet ──25──▶ mx (Postfix)
                   │
                   ├─ RCPT TO  ──tcp:2500──▶ ingest ──▶ Redis
                   └─ DATA     ──lmtp:2400─▶ ingest
                                               ├─ raw mail  ──▶ MongoDB GridFS
                                               └─ task      ──▶ Redis ──▶ worker
                                                                            │
                                       browser ◀── SSE ── api ◀── MongoDB ──┘
```

| Service | Job |
|---|---|
| `mx` | Postfix. Receives mail on port 25. No mailbox, no alias, no SASL, no relay. |
| `ingest` | LMTP server + Postfix recipient map. Stores the mail and triggers analysis. |
| `api` | FastAPI. Address generation, results, SSE stream and the web interface. |
| `worker` | Celery. Runs the analysis. |
| `dns` | unbound. Own recursive resolver, required for correct blacklist results. |
| `spamassassin` | spamd, reached over TCP. |
| `mongo` | Test addresses, mail events, analysis results, raw mails (GridFS). |
| `redis` | Celery broker, live test addresses, SSE pub/sub. |

### Why Postfix and not just a Python SMTP server

Postfix gives us a queue. If `ingest` or MongoDB is down for a moment, `ingest` answers `451` and
Postfix keeps the mail in its queue and retries, so nothing is lost during a deploy. It also
handles STARTTLS, protocol edge cases and rate limits.

`mx` is **not** placed behind a reverse proxy on purpose. The real client IP from the TCP
connection is the most valuable input of the whole analysis and a proxy would hide it.

### Why there is no spam filtering on the MX

There is deliberately no DNSBL, postscreen or content filter in `mx/main.cf.template`. The job of
a mail tester is to tell a blacklisted sender that it is blacklisted. If we rejected that sender,
the user would never get a result. This is the opposite of how a normal mail server is configured.

---

## Requirements

- A server with **inbound TCP port 25 open**. Nothing else needs to be reachable from outside.
- A domain whose MX record points to that server.
- Docker and Docker Compose.

Port 587 stays closed. This service only receives mail, so there is no submission port, no SASL
and no IMAP, which means there are no credentials to brute force.

---

## DNS Records

For `example.com` on server `1.2.3.4`:

```
example.com.          MX   10 mx.example.com.
mx.example.com.       A       1.2.3.4
example.com.          TXT     "v=spf1 -all"
_dmarc.example.com.   TXT     "v=DMARC1; p=reject;"
```

The SPF and DMARC records above say "this domain never sends mail", which prevents anyone from
spoofing it. If you later send mail from the domain, add the sending host to the SPF record.

Do not add a wildcard A or CNAME record. Setting the server's PTR record to `mx.example.com` is
recommended but not required, since the service only receives mail.

---

## Quickstart

```bash
cp .env.example .env
# MAIL_DOMAIN, MX_HOSTNAME, MongoDB credentials and SECRET_KEY must be filled in
docker compose build
docker compose up -d
docker compose logs -f mx ingest
```

Then open `http://127.0.0.1:8000/docs` for Swagger.

### Web interface

This repository ships the API and the SMTP ingest only, without a user interface. If you mount a
directory containing an `index.html` at `/app/public`, the API serves it from the root path on the
same origin, so there is nothing to configure for CORS:

```yml
api:
  volumes:
    - /opt/my-frontend:/app/public:ro
```

Without that mount the API runs on its own and `/` returns a small JSON pointing at `/docs`.
`GET /health` reports whether an interface is mounted.

### STARTTLS certificate

If no certificate is mounted, `mx` generates a self signed one on startup. That is enough for
opportunistic TLS, but mounting a real certificate is better:

```yml
mx:
  volumes:
    - /etc/letsencrypt/live/mx.example.com/fullchain.pem:/etc/postfix/tls/mx.crt:ro
    - /etc/letsencrypt/live/mx.example.com/privkey.pem:/etc/postfix/tls/mx.key:ro
```

---

## Deployment Specific Configuration

`docker-compose.yml` is meant to run anywhere without changes. Anything that belongs to one
particular server goes into `docker-compose.override.yml`, which Docker Compose merges
automatically and which is not committed.

An example for a host that already runs Traefik is shipped in the repo:

```bash
cp docker-compose.traefik.yml docker-compose.override.yml
# edit the Host() rule, entrypoint and certresolver names to match your Traefik setup
docker compose up -d
```

Joining Traefik's network does not change Traefik itself. Traefik discovers containers through
the Docker API and reads their labels, so adding a labelled container is purely additive: no
restart and no configuration change on the existing stack. Just keep the router and middleware
names unique.

`mx` is never routed through Traefik. It binds port 25 on the host directly, because a proxy
would replace the sending server's IP address with its own.

---

## Accounts

Tests work without an account, limited per IP address per day. Registered users get their own
daily quota, stored on the user document.

- `POST /register` – JSON body with `email` and `password`
- `POST /login` – form encoded `username` and `password`, returns a bearer token
- `POST /generate` – accepts an optional `Authorization: Bearer <token>` header

The web interface stores the token in `localStorage` and falls back to anonymous mode when the
token expires.

---

## Result Statuses

`GET /result/{to_address}` may return:

- `pending` – address created, no mail yet
- `received` – mail arrived, analysis queued
- `processing` – analysis running
- `analyzed` – analysis completed
- `expired` – test address expired
- `error` – an error occurred during processing

---

## Scoring System

- Every email starts with 10 points
- Points are reduced when problems are detected
- Each issue explains what is wrong, how much it costs and how to fix it

### Example Score Reductions

- Missing SPF record → -2.0
- Missing DKIM record → -1.0
- Missing DMARC record → -1.0
- Missing important headers → -0.5
- Reverse DNS mismatch → -0.4
- IP listed in blacklists → -0.5

### Final Score Meaning

- 9–10 → Excellent
- 7–8.9 → Good
- 5–6.9 → Average
- Below 5 → Poor

A blacklist query that is refused by the provider is reported as `blocked`, not as `listed`.
Telling somebody their IP is blacklisted when it is not would make the whole report untrustworthy.

---

## Database

### MongoDB Indexes (Required)

```bash
mongosh

use <your_database_name>

// Auto-delete expired test email records (expires_at is set by the API)
db.test_emails.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })

// Ensure generated addresses are unique
db.test_emails.createIndex({ to_address: 1 }, { unique: true })

// Anonymous daily quota counts by IP and analysis date
db.test_emails.createIndex({ created_ip: 1, analyzed_at: 1 })
```

Analyzed records get their `expires_at` removed so the TTL index cannot delete a finished result.

---

## Usage Flow

1. `POST /generate` → temporary test address
2. Send an email to that address
3. Subscribe to `GET /events/{to_address}` (SSE) or poll `GET /result/{to_address}`
4. When the status is `analyzed`, read the result

# Mail Tester

https://mail-tester.alialin.me

Mail Tester is a simple service that checks the quality and deliverability of outgoing emails.

Users send an email to a temporary test address.  
The system receives the email, analyzes it in the background using Celery, and returns a score with explanations.

---

## What This Tool Does

Mail Tester helps you understand why an email may fail or succeed.

You can test:

- Email server configuration
- SPF, DKIM, and DMARC records
- Reverse DNS (rDNS)
- Sending IP blacklist status
- Required and recommended email headers
- Basic email content quality
- Common delivery and spam-related issues

---

## How It Works

1. The user calls Generate Test Email.
2. The system creates a unique temporary email address, for example:

   test-a92bd12f98bc4@yourdomain.com

3. The system stores the generated address in MongoDB with status `pending`.
4. The user sends an email to the generated test address.
5. A Celery worker polls the mailbox via IMAP until the email arrives.
6. When the email is received, it is analyzed and stored.
7. The user requests the result and receives the score and report.

The system does not access the user’s mailbox.  
The user only sends an email to the generated test address.

---

## Background Processing (Celery)

Mail Tester uses Celery for asynchronous background processing.

- The API never waits for incoming emails
- Email retrieval and analysis run in background workers
- The API remains fast and non-blocking
- All state and results are stored in MongoDB
- Redis is used as the Celery broker (task queue)
- All processing states and analysis results are stored in MongoDB


---


## SpamAssassin (Spam Score)

Mail Tester can optionally run a SpamAssassin scan and include the spam score and report in the final result.

SpamAssassin runs as a separate `spamd` service and is accessed by the Celery worker over TCP.

This keeps the API fast and allows SpamAssassin to be enabled or disabled independently.

### Docker Compose service

Add the following service to `docker-compose.yml`:

```yml
spamassassin:
  image: axllent/spamassassin:latest
  container_name: mailtester-spamassassin
  restart: unless-stopped
  ports:
    - "783:783"
  worker:
  depends_on:
    - mongo
    - redis
    - spamassassin
  ```
## Result Statuses

GET /result/{to_address} may return:

- `pending` – address created, worker not started yet
- `processing` – worker is waiting for the email
- `analyzed` – analysis completed successfully
- `expired` – test address expired
- `error` – an error occurred during processing

---

## Scoring System

- Every email starts with 10 points
- Points are reduced when problems are detected
- Each issue clearly explains:
  - What is wrong
  - How much it affects the score

### Example Score Reductions

- Missing SPF record → -2.0
- Missing DKIM record → -1.5
- Missing DMARC record → -1.5
- Missing important headers → -0.2 to -0.5
- Reverse DNS mismatch → -0.4
- IP listed in blacklists → up to -3.0

### Final Score Meaning

- 9–10 → Excellent
- 7–8.9 → Good
- 5–6.9 → Average
- Below 5 → Poor

---

## Quickstart (Docker)



---

### 1) Configure environment variables

Create a `.env` file in the project root:


### 2) Start the application

Mail Tester is designed to run fully with Docker.

Run this command in the project root:

docker compose up --build

---

### What this does

This single command:

- Builds the API image
- Builds the Celery worker image
- Starts MongoDB
- Starts Redis
- Starts all services in the correct order
- Connects all services using Docker networking

No manual setup is required.

---

### Services started

- FastAPI API
  - Runs with Uvicorn inside the container
  - Exposes port 8000
  - Handles all HTTP requests

- Celery Worker
  - Runs in the background
  - Polls the mailbox via IMAP
  - Analyzes emails
  - Stores results in MongoDB

- Redis
  - Message broker for Celery
  - Result backend for Celery

- MongoDB
  - Stores test emails
  - Stores processing state
  - Stores analysis results

---

### Accessing the API

Once everything is running, open:

http://localhost:8000

Swagger UI will be available on the root page.

---

## Postfix Virtual Alias Setup

Mail Tester creates temporary email addresses like:

test-1234@example.com

These addresses do not exist as real mailboxes.  
Postfix must forward all incoming test emails to one real mailbox.

---

### Configuration

Edit the Postfix virtual alias file:

```bash
/etc/postfix/virtual_alias

@example.com    mail-tester@example.com

postmap /etc/postfix/virtual_alias
systemctl reload postfix
```


## Database

### MongoDB Indexes (Required)

This project uses a TTL index to automatically remove expired test email records from `test_emails`.
Make sure to create the following indexes after setting up MongoDB:

```bash
mongosh

use <your_database_name>

// Auto-delete expired test email records (expires_at is set by the API)
db.test_emails.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })

// Ensure generated addresses are unique
db.test_emails.createIndex({ to_address: 1 }, { unique: true })
```


## Usage Flow

1. Call POST /generate
2. Receive a temporary test email address
3. Send an email to that address
4. Poll GET /result/{to_address}
5. When status is `analyzed`, read the result

---


## Summary

Mail Tester is built as an asynchronous system.

- FastAPI handles HTTP requests
- Celery processes emails in the background
- Redis coordinates tasks
- MongoDB stores results

This architecture is designed for safe, scalable, and non-blocking email analysis.

A simple demo `index.html` may be included for testing purposes.
The backend API works independently from any frontend.
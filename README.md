# Mail Tester

Mail Tester is a simple service that checks the quality and deliverability of outgoing emails.

Users send an email to a temporary test address.  
The system receives the email, analyzes it, and returns a clear score with explanations.

---

## What This Tool Does

Mail Tester helps you understand **why an email may fail or succeed**.

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

1. The user clicks **Generate Test Email**
2. The system creates a unique temporary email address, for example:

test-a92bd12f98bc4@yourdomain.com




3. The user sends an email to this address
4. The server receives the email via IMAP
5. The system analyzes the email:
   - Read email headers
   - Extract metadata
   - Detect sender IP
   - Check SPF, DKIM, DMARC
   - Check reverse DNS
   - Check IP blacklists
   - Run basic content checks
6. A **score (0–10)** and a detailed report are returned to the user

The system does **not** access the user’s mailbox.  
The user only sends an email to the generated test address.

---

## Scoring System

- Every email starts with **10 points**
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

- **9–10** → Excellent
- **7–8.9** → Good
- **5–6.9** → Average
- **Below 5** → Poor

---

## Features

- Generate one-time test email addresses
- Receive emails using IMAP
- Parse email headers and content
- DNS checks (SPF / DKIM / DMARC)
- Reverse DNS validation
- IP blacklist checks
- Simple, transparent scoring system
- Clear explanations for each issue

---


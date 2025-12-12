# Mail Tester

Mail Tester is a simple service that helps users check the quality of their outgoing emails.

The user generates a temporary test email address on this website.  
Then, they send an email from their own email server or provider to that test address.

Our system receives the email, analyzes it, and returns a score based on several checks.

This allows users to test:
- How their email server is configured
- Whether their emails are delivered correctly
- Basic spam-related issues
- Headers, authentication, and content quality

---

## How It Works

1. The user clicks **"Generate Test Email"**
2. The system creates a unique temporary address such as:  
   `test-a92bd12f98bc4@yourdomain.com`
3. The user sends an email to this generated address
4. Our server receives the message
5. We process the email:
   - Read headers  
   - Extract metadata  
   - Analyze SPF, DKIM, DMARC, content, etc.  
6. A score and report are returned to the user

The system does **not** access the user’s IMAP or mailbox.  
The user simply sends an email to our test address.

---

## Features

- Generate one-time test email addresses  
- Receive and parse incoming messages  
- Extract headers and content  
- Run basic quality and deliverability checks  
- Return a clear score and report  

---
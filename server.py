from flask import Flask, jsonify
from flask_cors import CORS
import imaplib
import email
from email.header import decode_header
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

IMAP_SERVER = os.environ.get("IMAP_SERVER", "gw.enjet.co.kr")
IMAP_PORT = int(os.environ.get("IMAP_PORT", 993))
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

def decode_str(s):
    if s is None:
        return ""
    decoded = decode_header(s)
    result = ""
    for part, enc in decoded:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += part
    return result

def get_emails(limit=30):
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

        _, data = mail.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:][::-1]

        emails = []
        for uid in ids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            subject = decode_str(msg.get("Subject", ""))
            sender = decode_str(msg.get("From", ""))
            date_str = msg.get("Date", "")
            
            try:
                date = email.utils.parsedate_to_datetime(date_str)
                date_formatted = date.strftime("%Y-%m-%d %H:%M")
            except:
                date_formatted = date_str

            body = ""
            has_attachment = False
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    cd = str(part.get("Content-Disposition", ""))
                    if "attachment" in cd:
                        has_attachment = True
                        fname = decode_str(part.get_filename() or "")
                        attachments.append(fname)
                    elif ct == "text/plain" and not body:
                        try:
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        except:
                            body = ""
                    elif ct == "text/html" and not body:
                        try:
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        except:
                            body = ""
            else:
                try:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                except:
                    body = ""

            emails.append({
                "id": uid.decode(),
                "subject": subject,
                "sender": sender,
                "date": date_formatted,
                "body": body[:500],
                "has_attachment": has_attachment,
                "attachments": attachments
            })

        mail.logout()
        return emails
    except Exception as e:
        return {"error": str(e)}

@app.route("/api/emails")
def api_emails():
    emails = get_emails(30)
    return jsonify(emails)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

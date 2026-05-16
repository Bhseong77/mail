from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
import imaplib
import email
from email.header import decode_header
from email import policy
import os
import io
import re

app = Flask(__name__)
CORS(app)

IMAP_SERVER = os.environ.get("IMAP_SERVER", "gw.enjet.co.kr")
IMAP_PORT = int(os.environ.get("IMAP_PORT", 993))
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

def decode_str(s):
    if s is None: return ""
    decoded = decode_header(s)
    result = ""
    for part, enc in decoded:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += str(part)
    return result

def parse_addresses(addr_str):
    if not addr_str: return []
    results = []
    for name, addr in email.utils.getaddresses([addr_str]):
        try: name = decode_str(name)
        except: pass
        results.append({"name": name.strip(), "email": addr.strip()})
    return results

def html_to_text(html):
    """HTML을 일반 텍스트로 변환"""
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.I)
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'</p>', '\n', text, flags=re.I)
    text = re.sub(r'</div>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def extract_body(msg):
    """이메일에서 본문 추출 (모든 케이스 처리)"""
    plain = ""
    html = ""

    for part in msg.walk():
        ct = part.get_content_type()
        cd = str(part.get("Content-Disposition", ""))
        if "attachment" in cd.lower():
            continue
        
        charset = part.get_content_charset() or "utf-8"
        
        if ct == "text/plain" and not plain:
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode(charset, errors="ignore")
            except: pass
        
        elif ct == "text/html" and not html:
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(charset, errors="ignore")
            except: pass
        
        elif ct == "message/rfc822":
            # 전달된 메일 처리
            try:
                fwd_msg = part.get_payload(decode=False)
                if isinstance(fwd_msg, list) and len(fwd_msg) > 0:
                    fwd_body = extract_body(fwd_msg[0])
                    if fwd_body and not plain:
                        plain = "--- 전달된 메일 ---\n" + fwd_body
            except: pass

    if plain:
        return plain.strip()
    elif html:
        return html_to_text(html)
    return ""

def extract_attachments(msg):
    """첨부파일 목록 추출"""
    attachments = []
    for part in msg.walk():
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        if "attachment" in cd.lower():
            fname = decode_str(part.get_filename() or "")
            attachments.append({"name": fname, "content_type": ct})
        elif ct.startswith("image/") and "inline" in cd.lower():
            fname = decode_str(part.get_filename() or "")
            if fname:
                attachments.append({"name": fname, "content_type": ct, "inline": True})
    return attachments

# ── 기존 IMAP 엔드포인트 ────────────────────────────
def get_emails(limit=50):
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
            cc_raw = msg.get("Cc", "") or ""
            to_raw = msg.get("To", "") or ""
            date_str = msg.get("Date", "")
            try:
                date = email.utils.parsedate_to_datetime(date_str)
                date_formatted = date.strftime("%Y-%m-%d %H:%M")
            except:
                date_formatted = date_str
            body = extract_body(msg)
            attachments = extract_attachments(msg)
            emails.append({
                "id": uid.decode(),
                "subject": subject,
                "sender": sender,
                "to": parse_addresses(to_raw),
                "cc": parse_addresses(cc_raw),
                "date": date_formatted,
                "body": body[:1000],
                "has_attachment": len(attachments) > 0,
                "attachments": attachments
            })
        mail.logout()
        return emails
    except Exception as e:
        return {"error": str(e)}

@app.route("/api/emails")
def api_emails():
    return jsonify(get_emails(50))

@app.route("/api/email/<mail_id>/attachment/<int:att_index>")
def download_attachment(mail_id, att_index):
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        _, msg_data = mail.fetch(mail_id.encode(), "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        mail.logout()
        att_count = 0
        for part in msg.walk():
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd.lower():
                if att_count == att_index:
                    fname = decode_str(part.get_filename() or f"attachment_{att_index}")
                    payload = part.get_payload(decode=True)
                    ct = part.get_content_type() or "application/octet-stream"
                    return send_file(io.BytesIO(payload), mimetype=ct, as_attachment=True, download_name=fname)
                att_count += 1
        return jsonify({"error": "첨부파일을 찾을 수 없습니다"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── 신규: eml 파싱 엔드포인트 ───────────────────────
@app.route("/api/parse-eml", methods=["POST"])
def parse_eml_endpoint():
    """
    SharePoint에서 받아온 eml 바이트를 파싱해서 본문/첨부파일 반환
    대시보드에서 POST로 eml 파일 내용(bytes) 전송
    """
    try:
        eml_bytes = request.get_data()
        if not eml_bytes:
            return jsonify({"error": "eml 데이터가 없습니다"}), 400

        msg = email.message_from_bytes(eml_bytes)

        subject = decode_str(msg.get("Subject", ""))
        from_addr = decode_str(msg.get("From", ""))
        to_raw = msg.get("To", "") or ""
        cc_raw = msg.get("Cc", "") or ""
        date_str = msg.get("Date", "")

        try:
            date = email.utils.parsedate_to_datetime(date_str)
            date_formatted = date.strftime("%Y-%m-%d %H:%M")
        except:
            date_formatted = date_str

        body = extract_body(msg)
        attachments = extract_attachments(msg)

        return jsonify({
            "subject": subject,
            "from": from_addr,
            "to": parse_addresses(to_raw),
            "cc": parse_addresses(cc_raw),
            "date": date_formatted,
            "dateRaw": date_str,
            "body": body,
            "hasAttachment": len(attachments) > 0,
            "attachments": attachments
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

# ── 서명에서 연락처 추출 ─────────────────────────────
TITLES = r'사원|인턴|주임|연구원|대리|과장|차장|부장|팀장|수석|책임|선임|이사|상무|전무|부사장|사장|대표이사|대표|매니저|Manager|Director|Engineer|Senior|Analyst'
DEPTS  = r'[가-힣A-Za-z]{2,15}(?:팀|부|실|센터|본부|연구소|그룹|파트|Division|Team|Dept)'

def extract_contact_from_body(body, from_addr):
    """본문 서명에서 연락처 정보 추출"""
    if not body:
        return {}

    lines = body.split('\n')
    
    # 서명 영역: 구분선 이후 또는 마지막 40줄
    sig_start = max(0, len(lines) - 40)
    for i in range(len(lines)-1, max(0, len(lines)-60), -1):
        if re.match(r'^[-_=]{2,}\s*$', lines[i].strip()) or lines[i].strip() == '--':
            sig_start = i + 1
            break
    sig_lines = lines[sig_start:]
    sig = '\n'.join(sig_lines)

    name = ""
    title = ""
    dept = ""
    mobile = ""
    tel = ""
    company = ""

    for line in sig_lines:
        line = line.strip()
        if not line:
            continue

        # ── 이름+직함+부서 라인 ──
        # "우은영  Eunyoung Woo 대리 | 기술연구소"
        # "홍길동 과장 | 영업팀"
        # "안 희 범    기업금융센터 선임매니저"
        m = re.match(
            rf'^([가-힣]{{1,2}}\s[가-힣]{{1,2}}|[가-힣]{{2,4}})\s{{1,}}'
            rf'(?:[A-Za-z\s\-]+\s+)?({TITLES})'
            rf'(?:\s*[|｜]\s*({DEPTS}))?',
            line
        )
        if m and not name:
            name = m.group(1).replace(' ', '')
            title = m.group(2)
            dept = m.group(3) or ""

        # 부서만 있는 라인 (이름 없는 경우)
        if not dept:
            m2 = re.match(rf'^({DEPTS})\s+({TITLES})', line)
            if m2:
                dept = m2.group(1)
                title = m2.group(2)

        # ── 전화번호 라인 ──
        # "M +82.10.8222.6372   T +82.70.4892.8100   F +82.70.4892.8121"
        if not mobile:
            m = re.search(r'(?:^|\|)\s*M\s+(\+?[\d.\-\s]{9,20})', line)
            if not m:
                m = re.search(r'(?:Mobile|H\.P|Cell|HP)\s*[:|]?\s*(\+?[\d.\-\s]{9,20})', line, re.I)
            if m:
                mobile = m.group(1).strip().rstrip()

        if not tel:
            m = re.search(r'(?:^|\|)\s*T\s+(\+?[\d.\-\s]{8,20})', line)
            if not m:
                m = re.search(r'(?:Tel|Phone|D)\s*[:|]?\s*(\+?[\d.\-\s]{8,20})', line, re.I)
            if m:
                tel = m.group(1).strip().rstrip()

        # 모바일/전화 둘 다 없으면 한국 번호 패턴
        if not mobile and not tel:
            phones = re.findall(
                r'(\+?82[\s.]?0?\d[\s.]\d{3,4}[\s.]\d{4}|0\d{1,2}[\s.\-]\d{3,4}[\s.\-]\d{4})',
                line
            )
            if phones:
                mobile = phones[0]
            if len(phones) > 1:
                tel = phones[1]

        # ── 회사명 ──
        if not company:
            m = re.search(
                r'([가-힣A-Za-z\s()（）]{1,20}(?:주식회사|㈜|\(주\)))'
                r'|([가-힣A-Za-z\s]{2,25}(?:은행|증권|보험|캐피탈|카드|생명|화재|투자증권|자산운용))'
                r'|([A-Za-z][A-Za-z\s&.]{2,25}(?:Co\.,?\s*Ltd\.?|Inc\.?|Corp\.))',
                line
            )
            if m:
                company = (m.group(1) or m.group(2) or m.group(3) or "").strip()

    # From 헤더에서 이름 보완
    if not name and from_addr:
        # "홍길동 <email>" 패턴
        m = re.match(r'([가-힣]{2,4})\s*<', from_addr)
        if m:
            name = m.group(1)
        else:
            # "Hong Gildong <email>" 패턴
            m = re.match(r'"?([^"<]+)"?\s*<', from_addr)
            if m:
                name = m.group(1).strip()

    dept_full = ' '.join(filter(None, [dept, title])).strip()
    
    return {
        "name": name,
        "company": company,
        "dept": dept_full,
        "mobile": mobile.strip() if mobile else "",
        "tel": tel.strip() if tel else "",
    }


@app.route("/api/parse-eml-contact", methods=["POST"])
def parse_eml_contact():
    """eml에서 연락처 정보 추출"""
    try:
        eml_bytes = request.get_data()
        if not eml_bytes:
            return jsonify({"error": "데이터 없음"}), 400

        msg = email.message_from_bytes(eml_bytes)
        from_addr = decode_str(msg.get("From", ""))
        body = extract_body(msg)
        
        contact = extract_contact_from_body(body, from_addr)
        
        # 이메일 추출
        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', body)
        from_email = email.utils.parseaddr(from_addr)[1]
        contact["email"] = email_match.group(0).lower() if email_match else from_email
        contact["from"] = from_addr

        return jsonify(contact)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

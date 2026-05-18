from flask import Flask, jsonify, send_file, request
import urllib.request
import json
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
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

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

# ── OpenAI 연락처 분석 캐시 ──────────────────────────
_ai_cache = {}

def ai_extract_contact(domain, body_text, from_addr):
    """OpenAI로 도메인+본문에서 연락처 정보 추출"""
    cache_key = domain + "|" + body_text[:100]
    if cache_key in _ai_cache:
        return _ai_cache[cache_key]

    prompt = f"""다음 이메일 본문에서 발신자의 연락처 정보를 JSON으로 추출해주세요.

이메일 도메인: {domain}
발신자 주소: {from_addr}

이메일 본문:
{body_text}

다음 형식의 JSON만 반환하세요 (설명 없이):
{{
  "name": "한글이름",
  "company": "정확한 회사명",
  "dept": "부서명 직함",
  "mobile": "휴대폰번호",
  "tel": "직통전화번호"
}}

=== 추출 규칙 ===

[이름]
- 서명의 "홍 길 동  Gildong Hong 과장" → "홍길동" (공백제거)
- "홍길동입니다", "홍길동 드림" 패턴에서 추출
- 영문 이름만 있으면 영문 그대로

[회사명] ← 가장 중요!
- 반드시 본문/서명에서 실제 회사명을 찾을 것. 도메인만으로 추측 금지.
- 삼성 계열사 구분 (samsung.com 도메인이라도 본문에서 정확히 구분):
  * 삼성전자(주) / Samsung Electronics → "삼성전자"
  * 삼성전기(주) / Samsung Electro-Mechanics / SEMCO → "삼성전기"
  * 삼성디스플레이(주) / Samsung Display / SDC → "삼성디스플레이"
  * 삼성SDI(주) / Samsung SDI → "삼성SDI"
  * 삼성물산(주) / Samsung C&T → "삼성물산"
  * 삼성SDS(주) / Samsung SDS → "삼성SDS"
  * 삼성생명(주) / Samsung Life → "삼성생명"
  * 삼성화재(주) / Samsung Fire → "삼성화재"
  * 삼성증권(주) / Samsung Securities → "삼성증권"
  * 삼성바이오로직스 / Samsung Biologics → "삼성바이오로직스"
- LG 계열사도 동일하게 본문에서 구분:
  * LG전자, LG화학, LG디스플레이, LG이노텍, LG CNS 등
- 일반 규칙: 본문에서 "XX주식회사", "XX(주)", "XX Co.,Ltd" 패턴 찾기
- 본문에 회사명이 없으면 도메인으로 추정:
  * gachon.ac.kr → 가천대학교
  * kopti.re.kr → 한국생산기술연구원
  * etri.re.kr → ETRI(한국전자통신연구원)
  * kist.re.kr → KIST(한국과학기술연구원)
  * 그 외 알 수 없으면 도메인 첫번째 파트를 회사명으로

[부서/직함]
- 서명 라인에서 추출: "설계기술팀 책임연구원", "영업1팀 과장" 등
- 직함만: 사원/주임/대리/과장/차장/부장/팀장/수석/책임/선임/이사/상무/전무/대표
- 본문 첫줄 "기구공정기술2G 서보범입니다" → dept: "기구공정기술2G"
- 부서+직함 조합: "설계팀 과장" (부서명과 직함 사이 공백 하나)
- 불필요한 것 제거: 주소, 이메일주소, 전화번호, 회사명, 영문주소

[전화번호]
- M/Mobile → mobile 필드
- T/Tel/D/Direct → tel 필드
- +82.10.xxxx.xxxx 형식도 인식
- 정보 없으면 빈 문자열 (절대 추측 금지)
"""

    try:
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0
        }).encode('utf-8')

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_KEY}"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            text = result["choices"][0]["message"]["content"].strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            _ai_cache[cache_key] = parsed
            return parsed
    except Exception as e:
        print(f"OpenAI 오류: {e}")
        return {}


@app.route("/api/parse-eml-contact-ai", methods=["POST"])
def parse_eml_contact_ai():
    """OpenAI로 eml에서 연락처 정보 추출"""
    try:
        if not OPENAI_KEY:
            return jsonify({"error": "OPENAI_API_KEY 미설정"}), 400

        eml_bytes = request.get_data()
        if not eml_bytes:
            return jsonify({"error": "데이터 없음"}), 400

        msg = email.message_from_bytes(eml_bytes)
        from_addr = decode_str(msg.get("From", ""))
        from_email = email.utils.parseaddr(from_addr)[1]
        domain = from_email.split("@")[1] if "@" in from_email else ""
        body = extract_body(msg)

        if not body and not domain:
            return jsonify({"error": "본문 없음"}), 400

        # 본문 전체 전송 (최대 1200자) - 서명뿐 아니라 첫줄 소개도 포함
        body_text = body[:1200].strip()

        # OpenAI 분석
        result = ai_extract_contact(domain, body_text, from_addr)
        result["email"] = from_email
        result["from"] = from_addr

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

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


# ── 팀원별 IMAP 메일 조회 ────────────────────────────
def imap_connect(user, password, server=None, port=993):
    """IMAP 연결 - 한글/특수문자 비밀번호 지원"""
    srv = server or IMAP_SERVER
    mail = imaplib.IMAP4_SSL(srv, port)
    # imaplib은 ASCII만 지원 → UTF-8로 인코딩해서 로그인
    try:
        mail.login(user, password)
    except imaplib.IMAP4.error:
        # ASCII 실패 시 UTF-8 인코딩 시도
        if isinstance(password, str):
            mail2 = imaplib.IMAP4_SSL(srv, port)
            user_bytes = user.encode('utf-8') if isinstance(user, str) else user
            pass_bytes = password.encode('utf-8') if isinstance(password, str) else password
            # AUTH LOGIN 방식으로 직접 시도
            import base64
            mail2._imap.send(b'A1 LOGIN ' + 
                base64.b64encode(user_bytes) + b' ' + 
                base64.b64encode(pass_bytes) + b'\r\n')
            mail2._imap.readline()
            return mail2
        raise
    return mail

def imap_get_mails(user, password, email_addr, server=None, limit=200):
    """팀원 메일 조회"""
    try:
        mail = imap_connect(user, password, server)
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # 최신순
        
        results = []
        # 헤더만 먼저 가져오기 (빠름)
        for uid in ids:
            try:
                _, msg_data = mail.fetch(uid, "(RFC822.HEADER)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject  = decode_str(msg.get("Subject", ""))
                from_raw = decode_str(msg.get("From", ""))
                to_raw   = msg.get("To", "") or ""
                cc_raw   = msg.get("Cc", "") or ""
                date_str = msg.get("Date", "")
                ct       = msg.get("Content-Type", "")
                try:
                    d = email.utils.parsedate_to_datetime(date_str)
                    date_fmt = d.strftime("%Y. %m. %d. %p %I:%M").replace("AM","오전").replace("PM","오후")
                    date_raw = d.isoformat()
                except:
                    date_fmt = date_str
                    date_raw = date_str
                
                results.append({
                    "cacheKey":    f"{email_addr}/imap/{uid.decode()}",
                    "uid":         uid.decode(),
                    "owner":       email_addr,
                    "ownerName":   "",  # 대시보드에서 채움
                    "subject":     subject,
                    "from":        from_raw,
                    "to":          parse_addresses(to_raw),
                    "cc":          parse_addresses(cc_raw),
                    "date":        date_fmt,
                    "dateRaw":     date_raw,
                    "hasAttachment": "mixed" in ct.lower(),
                    "attachments": [],
                    "headerLoaded": True,
                    "bodyLoaded":  False,
                    "body":        "",
                    "source":      "imap",  # SP 백업과 구분
                })
            except Exception as e:
                continue
        
        mail.logout()
        return {"mails": results, "count": len(results), "error": None}
    except Exception as e:
        return {"mails": [], "count": 0, "error": str(e)}

def imap_get_body(user, password, uid, server=None):
    """특정 메일 본문 가져오기"""
    try:
        mail = imap_connect(user, password, server)
        mail.select("INBOX")
        _, msg_data = mail.fetch(uid.encode(), "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        mail.logout()
        body = extract_body(msg)
        attachments = extract_attachments(msg)
        return {
            "body": body,
            "attachments": [{"name": a["name"], "content_type": a.get("content_type","")} for a in attachments],
            "hasAttachment": len(attachments) > 0,
            "error": None
        }
    except Exception as e:
        return {"body": "", "attachments": [], "hasAttachment": False, "error": str(e)}


@app.route("/api/imap/mails", methods=["POST"])
def api_imap_mails():
    """팀원별 IMAP 메일 목록 조회
    Body: { user, pass, email, server(optional), limit(optional) }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON 데이터 없음"}), 400
        
        user     = data.get("user", "")
        password = data.get("pass", "")
        email_addr = data.get("email", "")
        server   = data.get("server", None)
        limit    = int(data.get("limit", 200))
        
        if not user or not password:
            return jsonify({"error": "user/pass 필요"}), 400
        
        result = imap_get_mails(user, password, email_addr, server, limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/imap/body", methods=["POST"])
def api_imap_body():
    """특정 메일 본문 조회
    Body: { user, pass, uid, server(optional) }
    """
    try:
        data = request.get_json()
        user     = data.get("user", "")
        password = data.get("pass", "")
        uid      = data.get("uid", "")
        server   = data.get("server", None)
        
        if not user or not password or not uid:
            return jsonify({"error": "user/pass/uid 필요"}), 400
        
        result = imap_get_body(user, password, uid, server)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/imap/test", methods=["POST"])
def api_imap_test():
    """IMAP 연결 테스트
    Body: { user, pass, server(optional) }
    """
    try:
        data = request.get_json()
        user     = data.get("user", "")
        password = data.get("pass", "")
        server   = data.get("server", IMAP_SERVER)
        
        mail = imap_connect(user, password, server)
        _, data2 = mail.select("INBOX")
        count = int(data2[0])
        mail.logout()
        return jsonify({"status": "ok", "count": count, "server": server})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400



# ── IMAP → SP 백업 ──────────────────────────────────
@app.route("/api/imap/backup", methods=["POST"])
def api_imap_backup():
    """IMAP 메일을 SP에 eml로 백업
    Body: {
      user, pass, email, server(optional),
      sp_token: MS Graph 액세스 토큰,
      drive_id: SP 드라이브 ID,
      folder: 저장 폴더 경로,
      existing_keys: 이미 있는 파일명 목록 (중복 방지)
    }
    """
    try:
        data = request.get_json()
        user       = data.get("user", "")
        password   = data.get("pass", "")
        email_addr = data.get("email", "")
        server     = data.get("server", None)
        sp_token   = data.get("sp_token", "")
        drive_id   = data.get("drive_id", "")
        folder     = data.get("folder", "")
        existing   = set(data.get("existing_keys", []))
        limit      = int(data.get("limit", 500))

        if not all([user, password, sp_token, drive_id, folder]):
            return jsonify({"error": "필수 파라미터 없음"}), 400

        # IMAP 연결
        srv = server or IMAP_SERVER
        mail = imaplib.IMAP4_SSL(srv, 993)
        mail.login(user, password)
        mail.select("INBOX")
        _, data2 = mail.search(None, "ALL")
        ids = data2[0].split()
        ids = ids[-limit:]  # 최신 N통

        saved = 0
        errors = []

        for uid in ids:
            filename = f"{uid.decode().zfill(8)}.eml"
            if filename in existing:
                continue  # 이미 백업됨

            try:
                # eml 전체 다운로드
                _, msg_data = mail.fetch(uid, "(RFC822)")
                eml_bytes = msg_data[0][1]

                # SP에 업로드
                sp_path = f"{folder}/{filename}"
                parts = "/".join(urllib.parse.quote(p, safe="") for p in sp_path.split("/"))
                url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parts}:/content"

                req = urllib.request.Request(
                    url,
                    data=eml_bytes,
                    headers={
                        "Authorization": f"Bearer {sp_token}",
                        "Content-Type": "message/rfc822",
                    },
                    method="PUT"
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    if r.status in (200, 201):
                        saved += 1
            except Exception as e:
                errors.append(f"{uid.decode()}: {str(e)}")
                continue

        mail.logout()
        return jsonify({
            "saved": saved,
            "total": len(ids),
            "errors": errors[:10],  # 최대 10개만
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/imap/backup/status", methods=["POST"])
def api_imap_backup_status():
    """백업 상태 확인 (IMAP 총 메일 수 vs SP 저장된 수)"""
    try:
        data = request.get_json()
        user     = data.get("user", "")
        password = data.get("pass", "")
        server   = data.get("server", None)

        srv = server or IMAP_SERVER
        mail = imaplib.IMAP4_SSL(srv, 993)
        mail.login(user, password)
        mail.select("INBOX")
        _, data2 = mail.search(None, "ALL")
        total = len(data2[0].split())
        mail.logout()

        return jsonify({"imap_total": total, "status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


# ── CalDAV 캘린더 ────────────────────────────────────
import urllib.parse
from datetime import datetime, timedelta

CALDAV_SERVER = os.environ.get("CALDAV_SERVER", "https://gw.enjet.co.kr")

CALDAV_MEMBERS = [
    {"email": "kwkang@enjet.co.kr",   "name": "강경원",
     "user": os.environ.get("CALDAV_USER1", "kwkang"),
     "password": os.environ.get("CALDAV_PASS1", "")},
    {"email": "baekhoon@enjet.co.kr", "name": "성백훈",
     "user": os.environ.get("CALDAV_USER2", "baekhoon"),
     "password": os.environ.get("CALDAV_PASS2", "")},
]

def caldav_fetch(user, password, url, body=None, method="PROPFIND", depth="1"):
    import base64
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Depth": depth,
        "Content-Type": "application/xml; charset=utf-8",
    }
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"CalDAV 오류 {url}: {e}")
        return None

def parse_ical_events(ical_text, member_name, member_email):
    events = []
    if not ical_text: return events
    vevent_blocks = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ical_text, re.DOTALL)
    for block in vevent_blocks:
        def get_prop(name):
            m = re.search(rf'^{name}[;:][^\r\n]*((?:\r?\n[ \t][^\r\n]*)*)', block, re.MULTILINE)
            if not m: return ""
            val = re.sub(rf'^{name}[^:]*:', '', m.group(0))
            val = re.sub(r'\r?\n[ \t]', '', val)
            return val.strip()

        def parse_dt(dtstr):
            if not dtstr: return ""
            dtstr = re.sub(r'^.*:', '', dtstr).strip()
            try:
                if len(dtstr) == 8:
                    return f"{dtstr[:4]}-{dtstr[4:6]}-{dtstr[6:8]}"
                dt = dtstr[:15].replace('T','T')
                return f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}T{dt[9:11]}:{dt[11:13]}"
            except: return dtstr

        raw_start = get_prop("DTSTART")
        allday = "VALUE=DATE" in block or (len(re.sub(r'^.*:', '', raw_start).strip()) == 8)
        ev = {
            "uid":       get_prop("UID"),
            "title":     get_prop("SUMMARY").replace("\\n","\n").replace("\\,",","),
            "start":     parse_dt(raw_start),
            "end":       parse_dt(get_prop("DTEND")),
            "location":  get_prop("LOCATION").replace("\\,",","),
            "desc":      get_prop("DESCRIPTION")[:200].replace("\\n","\n"),
            "allDay":    allday,
            "owner":     member_email,
            "ownerName": member_name,
            "color":     "#5b8fff" if "kwkang" in member_email else "#a78bfa",
        }
        if ev["title"] or ev["start"]:
            events.append(ev)
    return events

def get_caldav_events(member, start, end):
    report_body = f'''<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}T000000Z" end="{end}T235959Z"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>'''

    # URL 패턴 여러개 시도
    urls = [
        f"{CALDAV_SERVER}/principals/users/{member['email']}/calendars/%EB%82%B4%20%EC%9D%BC%EC%A0%95/",
        f"{CALDAV_SERVER}/principals/users/{member['email']}/calendars/",
        f"{CALDAV_SERVER}/caldav/users/{member['email']}/",
    ]
    for url in urls:
        result = caldav_fetch(member["user"], member["password"], url,
                              body=report_body, method="REPORT", depth="1")
        if result and "VEVENT" in result:
            cal_blocks = re.findall(r'<.*?calendar-data[^>]*>(.*?)</.*?calendar-data>', result, re.DOTALL)
            all_events = []
            for blk in cal_blocks:
                blk = blk.replace("&lt;","<").replace("&gt;",">").replace("&amp;","&").replace("&#13;","")
                all_events.extend(parse_ical_events(blk, member["name"], member["email"]))
            return all_events
    return []

@app.route("/api/calendar")
def api_calendar():
    today = datetime.now()
    start = request.args.get("start", (today - timedelta(days=30)).strftime("%Y%m%d"))
    end   = request.args.get("end",   (today + timedelta(days=60)).strftime("%Y%m%d"))
    all_events, errors = [], []
    for member in CALDAV_MEMBERS:
        if not member["password"]:
            errors.append(f"{member['name']}: CALDAV_PASS 미설정"); continue
        try:
            events = get_caldav_events(member, start, end)
            all_events.extend(events)
        except Exception as e:
            errors.append(f"{member['name']}: {e}")
    all_events.sort(key=lambda e: e.get("start",""))
    return jsonify({"events": all_events, "count": len(all_events), "errors": errors})

@app.route("/api/calendar/test")
def api_calendar_test():
    results = []
    for member in CALDAV_MEMBERS:
        if not member["password"]:
            results.append({"name": member["name"], "status": "PASS 미설정"}); continue
        url = f"{CALDAV_SERVER}/principals/users/{member['email']}/"
        resp = caldav_fetch(member["user"], member["password"], url,
            body='<?xml version="1.0"?><propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>',
            method="PROPFIND", depth="0")
        results.append({
            "name": member["name"], "url": url,
            "status": "성공" if resp and len(resp) > 10 else "실패",
            "resp_len": len(resp) if resp else 0
        })
    return jsonify(results)

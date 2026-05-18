"""
ENJET 메일 백업 인덱스 생성 스크립트
==========================================
SharePoint에 백업된 .eml 파일들의 헤더를 파싱해서
각 멤버 폴더에 _index.json 파일로 저장합니다.

이 인덱스 하나만 있으면 웹 대시보드가 SP 호출을 거의 안 해도
8000건 메일 목록을 즉시 표시할 수 있습니다.

==========================================
사용법:
==========================================
1. pip install msal requests
2. python build_index.py
3. 첫 실행 시 브라우저에서 로그인 화면 → 회사 계정으로 로그인
4. 자동으로 모든 멤버 폴더 스캔하며 _index.json 생성

==========================================
실행 결과:
==========================================
📦영업/10.mail_backup/
  └─ kwkang@enjet.co.kr/
      ├─ _index.json   ← 이게 새로 생김 (전체 메일 헤더 모음)
      ├─ messages/
      ├─ messages1/
      └─ sent/

새 백업 올릴 때마다 이 스크립트 다시 실행하면 인덱스 갱신됨.
==========================================
"""

import os
import json
import email
import email.utils
import email.header
import requests
import time
import sys
from datetime import datetime
from msal import PublicClientApplication, SerializableTokenCache

# ── 설정 ────────────────────────────────────────────
CLIENT_ID = "76793776-3330-496f-97f1-9f0c034f3f07"   # 대시보드와 동일한 앱 ID
TENANT_ID = "58b310e1-9278-47ab-8e49-792728818c9e"   # ENJET 테넌트
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Files.ReadWrite.All", "Sites.ReadWrite.All"]
DRIVE_ID = "b!1Va0aO9NoEaz4dpZxMSwjDgP35aZ2VBCgGsTHl1ORaP52YVq1dDmTZVIyBhR5YG-"
MAIL_BACKUP_PATH = "📦영업/10.mail_backup"

TOKEN_CACHE_FILE = os.path.expanduser("~/.enjet_mail_token.json")
INDEX_FILENAME = "_index.json"
CONTACTS_FILENAME = "_contacts.json"

# ── MSAL 토큰 관리 ──────────────────────────────────
def get_token():
    """디바이스 코드 플로우로 토큰 발급. 캐시 활용."""
    cache = SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())

    app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        print("\n로그인이 필요합니다.")
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise Exception("Device flow 실패: " + json.dumps(flow))
        print("\n" + "="*60)
        print(flow["message"])
        print("="*60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise Exception("토큰 발급 실패: " + str(result))

    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(cache.serialize())

    return result["access_token"]

# ── Graph API 헬퍼 ─────────────────────────────────
def sp_url(path):
    """Graph API URL 빌더. path는 슬래시로 구분된 SP 경로."""
    enc = "/".join(requests.utils.quote(p, safe="") for p in path.split("/"))
    return f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/root:/{enc}:"

def sp_get(url, tok, **kwargs):
    """GET with throttle handling."""
    for attempt in range(5):
        r = requests.get(url, headers={"Authorization": "Bearer " + tok}, **kwargs)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "0")) or (2 ** attempt)
            print(f"  [throttle] {wait}초 대기...")
            time.sleep(wait)
            continue
        return r
    return r

def list_children(path, tok):
    """폴더의 자식 항목 목록"""
    url = sp_url(path) + "/children?$top=5000&$select=name,folder,size,createdDateTime"
    all_items = []
    while url:
        r = sp_get(url, tok)
        if not r.ok:
            print(f"  ✗ list 실패 ({path}): {r.status_code}")
            return []
        d = r.json()
        all_items.extend(d.get("value", []))
        url = d.get("@odata.nextLink")
    return all_items

def download_eml(path, tok, max_bytes=20000):
    """eml 파일 다운로드. 헤더 파싱용이므로 처음 20KB만."""
    url = sp_url(path) + "/content"
    r = sp_get(url, tok, headers={"Authorization": "Bearer " + tok, "Range": f"bytes=0-{max_bytes-1}"})
    if not r.ok:
        return None
    return r.content

def upload_json(path, data, tok):
    """JSON 파일 업로드 (overwrite)"""
    url = sp_url(path) + f"/content"
    body = json.dumps(data, ensure_ascii=False, indent=None).encode("utf-8")
    r = requests.put(url, headers={
        "Authorization": "Bearer " + tok,
        "Content-Type": "application/json"
    }, data=body)
    return r.ok, r

# ── EML 헤더 파싱 ──────────────────────────────────
def decode_header(s):
    """RFC2047 인코딩 헤더 디코딩"""
    if not s:
        return ""
    try:
        parts = email.header.decode_header(s)
        out = []
        for content, charset in parts:
            if isinstance(content, bytes):
                try:
                    out.append(content.decode(charset or "utf-8", errors="replace"))
                except:
                    out.append(content.decode("utf-8", errors="replace"))
            else:
                out.append(content)
        return "".join(out)
    except:
        return s if isinstance(s, str) else str(s)

def parse_addresses(raw):
    """이메일 주소 리스트 파싱"""
    if not raw:
        return []
    try:
        parsed = email.utils.getaddresses([raw])
        return [{"name": decode_header(n), "email": e} for n, e in parsed if e]
    except:
        return []


# ── 연락처 정보 추출 (본문/서명에서) ─────────────────
import re as _re

# 한국 회사 도메인 → 회사명 매핑 (일부)
KNOWN_COMPANIES = {
    "samsung.com": "삼성전자",
    "samsungdisplay.com": "삼성디스플레이",
    "lge.com": "LG전자",
    "lgdisplay.com": "LG디스플레이",
    "lgensol.com": "LG에너지솔루션",
    "sk.com": "SK",
    "skhynix.com": "SK하이닉스",
    "skoncc.com": "SK온",
    "hyundai.com": "현대자동차",
    "kia.com": "기아",
    "hd.com": "현대중공업",
    "posco.com": "포스코",
    "naver.com": "네이버",
    "kakaocorp.com": "카카오",
    "auo.com": "AU Optronics",
    "boe.com.cn": "BOE",
    "tianma.cn": "Tianma",
    "innolux.com": "Innolux",
    "kccworld.co.kr": "KCC",
    "kcc.co.kr": "KCC",
    "hanmail.net": "(개인)",
    "gmail.com": "(개인)",
    "naver.com": "(개인)",
    "daum.net": "(개인)",
}

# 부서/직함 키워드 (한국어/영문)
DEPT_KEYWORDS = [
    "팀", "그룹", "본부", "부서", "실", "센터", "사업부", "연구소", "랩",
    "Team", "Group", "Department", "Division", "Center", "Lab",
]
TITLE_KEYWORDS = [
    "사원", "대리", "과장", "차장", "부장", "팀장", "본부장", "이사", "상무",
    "전무", "부사장", "사장", "대표", "회장", "주임", "선임", "책임", "수석",
    "엔지니어", "매니저", "Manager", "Director", "Engineer", "VP", "CEO", "CTO", "CFO",
    "President", "Vice", "Senior", "Junior", "Lead",
]

def extract_phone(text):
    """본문에서 전화번호 추출 (모바일 우선, 일반전화 후순)"""
    if not text:
        return {"mobile": "", "tel": ""}
    mobile = ""
    tel = ""
    # 모바일: 010-XXXX-XXXX
    m = _re.search(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}", text)
    if m:
        mobile = _re.sub(r"\s+", "", m.group(0))
        mobile = _re.sub(r"\.+", "-", mobile)
    # 일반전화: 02-XXXX-XXXX, 031-XXX-XXXX 등
    m = _re.search(r"0(?:2|[3-6][1-5])[-\s.]?\d{3,4}[-\s.]?\d{4}", text)
    if m:
        tel = _re.sub(r"\s+", "", m.group(0))
        tel = _re.sub(r"\.+", "-", tel)
    return {"mobile": mobile, "tel": tel}


def extract_dept_title(text, name):
    """본문/서명에서 부서와 직함 추출"""
    if not text or not name:
        return {"dept": "", "title": ""}
    # 본문에서 이름 주변 컨텍스트 찾기
    lines = text.split("\n")[:30]   # 첫 30줄 + 마지막 20줄 (서명은 보통 끝)
    if len(text.split("\n")) > 50:
        lines += text.split("\n")[-20:]

    dept = ""
    title = ""

    for line in lines:
        line = line.strip()
        if not line or len(line) > 200:
            continue

        # 부서 키워드 포함된 줄
        if not dept:
            for kw in DEPT_KEYWORDS:
                if kw in line and len(line) < 80:
                    # 줄에서 깨끗한 부서명 추출
                    # 예: "기구공정기술2그룹 책임" → "기구공정기술2그룹"
                    m = _re.search(r"([\w가-힣]+(?:" + "|".join(DEPT_KEYWORDS) + r"))", line)
                    if m:
                        dept = m.group(1)
                        break

        # 직함
        if not title:
            for kw in TITLE_KEYWORDS:
                if kw in line and len(line) < 80:
                    title = kw
                    break

        if dept and title:
            break

    # 부서+직함 합치기
    if dept and title:
        full = f"{dept} {title}"
    elif dept:
        full = dept
    elif title:
        full = title
    else:
        full = ""
    return {"dept": full, "title": title}


def extract_body_text(msg, max_chars=3000):
    """eml에서 본문 텍스트만 추출 (서명 분석용, 최대 3000자)"""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            text = payload.decode(charset, errors="replace")
                        except:
                            text = payload.decode("utf-8", errors="replace")
                        return text[:max_chars]
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            html = payload.decode(charset, errors="replace")
                        except:
                            html = payload.decode("utf-8", errors="replace")
                        # 간단 HTML→text (태그 제거)
                        text = _re.sub(r"<[^>]+>", " ", html)
                        text = _re.sub(r"&nbsp;", " ", text)
                        text = _re.sub(r"\s+", " ", text)
                        return text[:max_chars]
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")[:max_chars]
                except:
                    return payload.decode("utf-8", errors="replace")[:max_chars]
    except:
        pass
    return ""


def extract_contact_from_msg(msg, from_addr_raw):
    """
    메일 메시지에서 발신자 연락처 정보 추출
    반환: {"email", "name", "company", "dept", "mobile", "tel"}
    """
    # 발신자 파싱
    try:
        addrs = email.utils.getaddresses([from_addr_raw])
        if not addrs or not addrs[0][1]:
            return None
        name_raw, email_addr = addrs[0]
        name = decode_header(name_raw) if name_raw else ""
    except:
        return None

    domain = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
    if domain == "enjet.co.kr":
        return None   # 내부

    company = KNOWN_COMPANIES.get(domain, "")
    if not company:
        # 도메인의 첫 부분을 회사명으로 (대소문자 유지)
        company = domain.split(".")[0].capitalize() if domain else ""

    # 본문 추출
    body = extract_body_text(msg, max_chars=3000)
    phones = extract_phone(body)
    dept_info = extract_dept_title(body, name)

    return {
        "email": email_addr,
        "name": name,
        "company": company,
        "dept": dept_info["dept"],
        "mobile": phones["mobile"],
        "tel": phones["tel"],
    }


def extract_all_data(eml_bytes):
    """eml 바이트에서 헤더 + 연락처 정보 둘 다 추출"""
    if not eml_bytes:
        return None, None
    try:
        msg = email.message_from_bytes(eml_bytes)
        date_str = msg.get("Date", "")
        try:
            d = email.utils.parsedate_to_datetime(date_str)
            date_iso = d.isoformat()
            date_fmt = d.strftime("%Y. %m. %d. %p %I:%M").replace("AM","오전").replace("PM","오후")
        except:
            date_iso = date_str
            date_fmt = date_str
        ct = msg.get("Content-Type", "") or ""
        from_raw = msg.get("From", "")

        headers = {
            "subject":      decode_header(msg.get("Subject", "")),
            "from":         decode_header(from_raw),
            "to":           parse_addresses(msg.get("To", "")),
            "cc":           parse_addresses(msg.get("Cc", "")),
            "date":         date_fmt,
            "dateRaw":      date_iso,
            "hasAttachment": "mixed" in ct.lower() or "related" in ct.lower(),
        }

        # 연락처 정보 추출 (발신자 기준)
        contact = extract_contact_from_msg(msg, from_raw)

        return headers, contact
    except Exception as e:
        return {"_error": str(e)}, None

# ── 메인 ───────────────────────────────────────────
def build_index_for_member(member_email, tok, force=False):
    """한 멤버의 전체 백업 폴더를 인덱싱"""
    print(f"\n▶ {member_email}")
    member_path = f"{MAIL_BACKUP_PATH}/{member_email}"

    # 1) 멤버 폴더의 자식 폴더 목록 (messages*, sent*)
    children = list_children(member_path, tok)
    sub_folders = [c for c in children if c.get("folder")
                   and (c["name"].startswith("messages") or c["name"].startswith("sent"))]
    if not sub_folders:
        print(f"  ⚠ 백업 폴더 없음")
        return

    # 2) 기존 _index.json 로드 (있으면 증분 모드)
    existing = {}  # key: "폴더/파일명" → 헤더
    index_file_path = f"{member_path}/{INDEX_FILENAME}"
    if not force:
        r = sp_get(sp_url(index_file_path) + "/content", tok)
        if r.ok:
            try:
                existing_list = r.json()
                for item in existing_list:
                    key = f"{item['_folder']}/{item['fileName']}"
                    existing[key] = item
                print(f"  기존 인덱스: {len(existing)}건")
            except:
                pass

    # 2-2) 기존 _contacts.json 로드
    contacts_file_path = f"{member_path}/{CONTACTS_FILENAME}"
    contacts_map = {}  # email.lower() → {email, name, company, dept, mobile, tel, firstSeen, count}
    if not force:
        r = sp_get(sp_url(contacts_file_path) + "/content", tok)
        if r.ok:
            try:
                existing_contacts = r.json()
                for c in existing_contacts:
                    ek = (c.get("email") or "").lower()
                    if ek:
                        contacts_map[ek] = c
                print(f"  기존 연락처: {len(contacts_map)}명")
            except:
                pass

    # 3) 각 하위 폴더 순회
    all_items = []
    new_count = 0
    skip_count = 0
    fail_count = 0

    for sub in sub_folders:
        sub_name = sub["name"]
        folder_kind = "sent" if sub_name.startswith("sent") else "inbox"
        sub_path = f"{member_path}/{sub_name}"
        print(f"  📁 {sub_name} ({folder_kind})", end=" ", flush=True)

        files = list_children(sub_path, tok)
        emls = [f for f in files if f["name"].lower().endswith(".eml")]
        print(f"...{len(emls)}건")

        for i, f in enumerate(emls):
            fname = f["name"]
            key = f"{sub_name}/{fname}"

            # 이미 있으면 스킵
            if key in existing:
                all_items.append(existing[key])
                skip_count += 1
                continue

            # 다운로드 (헤더 + 본문 일부) - 연락처 분석용으로 50KB
            eml_bytes = download_eml(f"{sub_path}/{fname}", tok, max_bytes=50000)
            if not eml_bytes:
                fail_count += 1
                continue

            hdrs, contact = extract_all_data(eml_bytes)
            if not hdrs or "_error" in (hdrs or {}):
                fail_count += 1
                continue

            item = dict(hdrs)
            item["fileName"] = fname
            item["_folder"] = sub_name
            item["folder"] = folder_kind
            item["createdDateTime"] = f.get("createdDateTime", "")
            all_items.append(item)
            new_count += 1

            # 연락처 정보 누적 (받은 메일의 발신자만)
            if contact and folder_kind == "inbox":
                ek = contact["email"].lower()
                date_iso = item.get("dateRaw") or item.get("createdDateTime") or ""
                if ek in contacts_map:
                    # 더 풍부한 정보로 업데이트
                    existing_c = contacts_map[ek]
                    if not existing_c.get("dept") and contact["dept"]:
                        existing_c["dept"] = contact["dept"]
                    if not existing_c.get("mobile") and contact["mobile"]:
                        existing_c["mobile"] = contact["mobile"]
                    if not existing_c.get("tel") and contact["tel"]:
                        existing_c["tel"] = contact["tel"]
                    if not existing_c.get("name") and contact["name"]:
                        existing_c["name"] = contact["name"]
                    existing_c["count"] = existing_c.get("count", 0) + 1
                    # firstSeen 더 빠른 날짜로
                    if date_iso and (not existing_c.get("firstSeen") or date_iso < existing_c["firstSeen"]):
                        existing_c["firstSeen"] = date_iso
                else:
                    contacts_map[ek] = {
                        "email": contact["email"],
                        "name": contact["name"],
                        "company": contact["company"],
                        "dept": contact["dept"],
                        "mobile": contact["mobile"],
                        "tel": contact["tel"],
                        "firstSeen": date_iso,
                        "count": 1,
                    }

            if new_count % 50 == 0:
                print(f"    진행: 신규 {new_count}건 / 연락처 {len(contacts_map)}명...", flush=True)

    # 4) 정렬 (날짜 내림차순)
    all_items.sort(key=lambda x: x.get("dateRaw") or x.get("createdDateTime") or "", reverse=True)

    # 5) _index.json 업로드
    print(f"  💾 _index.json 업로드 ({len(all_items)}건)...", end=" ", flush=True)
    ok, r = upload_json(index_file_path, all_items, tok)
    if ok:
        print("✓")
    else:
        print(f"✗ ({r.status_code}: {r.text[:200]})")
        return

    # 6) _contacts.json 업로드
    contacts_list = list(contacts_map.values())
    contacts_list.sort(key=lambda c: c.get("count", 0), reverse=True)
    print(f"  💾 _contacts.json 업로드 ({len(contacts_list)}명)...", end=" ", flush=True)
    ok, r = upload_json(contacts_file_path, contacts_list, tok)
    if ok:
        print("✓")
    else:
        print(f"✗ ({r.status_code}: {r.text[:200]})")

    print(f"  ✅ 완료: 메일 {len(all_items)}건 (신규 {new_count}, 기존 {skip_count}, 실패 {fail_count}) / 연락처 {len(contacts_list)}명")

def main():
    print("="*60)
    print("ENJET 메일 백업 인덱스 생성")
    print("="*60)

    # 토큰 발급
    print("\n토큰 확인 중...")
    tok = get_token()
    print("✓ 토큰 확보")

    # 멤버 목록: 인자로 받거나, mail_backup 폴더의 자식들 자동 발견
    if len(sys.argv) > 1:
        members = sys.argv[1:]
        print(f"\n지정된 멤버 {len(members)}명: {members}")
    else:
        print(f"\n📁 {MAIL_BACKUP_PATH} 폴더 스캔 중...")
        children = list_children(MAIL_BACKUP_PATH, tok)
        members = [c["name"] for c in children
                   if c.get("folder") and "@" in c["name"]]
        print(f"발견된 멤버: {len(members)}명")
        for m in members:
            print(f"  - {m}")

    # 각 멤버 처리
    for em in members:
        try:
            build_index_for_member(em, tok)
        except Exception as e:
            print(f"\n✗ {em} 처리 중 오류: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*60)
    print("완료!")
    print("="*60)

if __name__ == "__main__":
    main()

# ENJET 메일 대시보드

다우오피스 메일을 웹 대시보드로 확인하는 시스템입니다.

## 구조
```
깃허브 Pages (index.html) → Render.com (server.py) → 다우오피스 IMAP
```

---

## 배포 방법

### 1단계. 깃허브 레포 만들기
1. github.com → New repository
2. 이름: `enjet-mail-dashboard`
3. Public으로 설정
4. 이 폴더의 파일 전부 업로드

### 2단계. 깃허브 Pages 설정
1. 레포 → Settings → Pages
2. Source: Deploy from a branch
3. Branch: main / (root)
4. Save

→ `https://[계정명].github.io/enjet-mail-dashboard` 로 접속 가능

### 3단계. Render.com 백엔드 배포
1. render.com 가입 (무료)
2. New → Web Service
3. 깃허브 레포 연결
4. Environment Variables 설정:
   - `EMAIL_USER` = baekhoon@enjet.co.kr
   - `EMAIL_PASS` = 다우오피스 비밀번호
5. Deploy

### 4단계. 대시보드 연결
1. Render에서 배포된 URL 복사
   (예: https://enjet-mail-api.onrender.com)
2. 대시보드 접속 → 설정창에 URL 입력
3. 연결하기

---

## IMAP 서버 정보
- 서버: gw.enjet.co.kr
- 포트: 993 (SSL)

# ENJET 메일 대시보드 - 인덱스 작업 가이드

## 📋 작업 순서 (회사 도착 후)

### Step 1: GitHub에 파일 5개 push

`bhseong77/mail` 저장소에 다음 파일들 업로드:

| 파일 | 위치 | 설명 |
|---|---|---|
| `index.html` | 루트 | 웹 대시보드 (덮어쓰기) |
| `server.py` | 루트 | Render 백엔드 (덮어쓰기) |
| `build_index.py` | 루트 | **신규** - 인덱싱 스크립트 |
| `build_index_requirements.txt` | 루트 | **신규** - 필요 패키지 |
| `.gitignore` | 루트 | **신규** - 토큰/인덱스 보호 |

⚠️ **`_index.json`은 절대 GitHub에 올리지 마세요** (회사 기밀)

---

### Step 2: 15~30분 대기

지금 Microsoft Graph 가 throttle 걸어놨을 가능성이 있습니다 (어제 너무 많이 호출).
서버 재배포 + 시간 경과로 자연 회복.

그 동안 Step 3 준비.

---

### Step 3: PC에 Python 환경 준비

회사 PC 명령창(cmd 또는 PowerShell):

```bash
# 저장소 클론 (또는 폴더에 위 파일들 복사)
git clone https://github.com/bhseong77/mail.git
cd mail

# 패키지 설치 (1회)
pip install -r build_index_requirements.txt
```

---

### Step 4: 인덱스 생성 (오래 걸림)

```bash
python build_index.py
```

**처음 실행 시**:
```
============================================================
ENJET 메일 백업 인덱스 생성
============================================================

토큰 확인 중...

로그인이 필요합니다.

============================================================
To sign in, use a web browser to open the page 
https://microsoft.com/devicelogin and enter the code 
ABCD1234 to authenticate.
============================================================
```

→ 브라우저에서 `https://microsoft.com/devicelogin` 열고
→ `ABCD1234` (실제 코드) 입력
→ 회사 계정 `baekhoon@enjet.co.kr` 로 로그인
→ 권한 동의

그러면 자동으로 진행:
```
✓ 토큰 확보

📁 📦영업/10.mail_backup 폴더 스캔 중...
발견된 멤버: 8명

▶ baekhoon@enjet.co.kr
  📁 messages (inbox) ...XXX건
  ...
  💾 _index.json 업로드 (XXX건)... ✓

▶ hdlee@enjet.co.kr
  ...
```

**예상 시간**: 80,000건 전체면 1~2시간

💡 백그라운드로 두고 다른 작업하셔도 됩니다. 절전 모드만 안 들어가게.

---

### Step 5: 웹에서 확인

인덱스 생성 끝나면:

1. 웹 대시보드 새로고침 (https://bhseong77.github.io/mail/)
2. **설정 → 🗑️ 캐시 초기화** (기존 망가진 데이터 정리)
3. 다시 새로고침
4. 각 멤버 _index.json 1개씩만 로드 → 즉시 모든 메일 표시

콘솔에 이런 로그 나옴:
```
[SP] 강경원 _index.json: 6759건
[SP] 김영권 _index.json: 2128건
[SP] 박태준 _index.json: 3387건
...
```

---

## 🔧 새 백업 올렸을 때 (앞으로)

1. SP에 messages2, sent2 등 새 폴더 업로드
2. PC에서 `python build_index.py` 다시 실행
   - 기존 인덱스 있는 메일은 자동 스킵
   - 신규만 처리 → 몇 분이면 끝
3. 웹에서 **➕ 추가분** 버튼 클릭

---

## ❓ 문제 발생 시

### "토큰 발급 실패" 에러
- 회사 계정 권한 부족: IT에 Sites.ReadWrite.All 권한 요청
- 또는 다른 계정으로 로그인했는지 확인

### Throttle (429) 에러
- 스크립트가 자동으로 대기 후 재시도
- 1시간 이상 계속되면 잠시 중단했다 재실행

### 인덱스 생성 도중 멈춤
- Ctrl+C 후 다시 `python build_index.py` 실행
- 멤버 단위로 저장되므로 이미 끝난 멤버는 스킵

### 웹에서 인덱스 안 보임
- 콘솔에 `[SP] {이름} 인덱스 없음 (build_index.py 실행 필요)` 메시지
- → 그 멤버 폴더 처리 안 됐다는 뜻. 스크립트 다시 돌리기

---

## 📊 동작 원리 요약

```
[이전 방식]                    [새 방식]
8,000개 .eml 파일               멤버당 _index.json 1개
→ 헤더만 8,000번 다운로드        → 인덱스 1개로 8,000건 헤더 알아냄
→ MS Graph throttle             → 호출 횟수 8회만
→ 페이지 멈춤                   → 즉시 표시
```

본문은 클릭할 때만 .eml 1개씩 받음 (지금과 동일).

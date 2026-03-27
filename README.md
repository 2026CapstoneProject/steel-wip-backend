# steel-wip-backend

철강 잔재 재고 관리 시스템 백엔드 서버 (FastAPI)

## 기술 스택

- **Framework**: FastAPI
- **DB**: MySQL (비동기 aiomysql)
- **ORM**: SQLAlchemy 2.0 (async)
- **마이그레이션**: Alembic
- **환경설정**: Pydantic Settings

## 로컬 실행 방법

```bash
# 1. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 DB 정보 입력

# 4. 서버 실행
uvicorn main:app --reload
```

## 브랜치 전략

```
main      ← 배포 브랜치 (hotfix/*, release/* 만 PR 허용)
  └── develop  ← 통합 개발 브랜치
        ├── feature/GP-123-기능명
        ├── bugfix/GP-123-버그명
        └── refactor/GP-123-리팩터링명
```

## PR 규칙

- PR 제목에 Jira 키 포함 필수 (예: `[BE][Feature] GP-123 잔재 조회 API 구현`)
- 브랜치명 형식: `feature/GP-123-wip-list-api`
- `develop` 브랜치로만 PR 가능 (main은 hotfix/release 만)

## GitHub Secrets 등록 필요 항목

| Secret 이름 | 설명 |
|---|---|
| `JIRA_BASE_URL` | Jira 도메인 (예: https://xxx.atlassian.net) |
| `JIRA_API_TOKEN` | Jira API 토큰 |
| `JIRA_USER_EMAIL` | Jira 계정 이메일 |
| `JIRA_DONE_TRANSITION_ID` | Jira "완료" 전환 ID |

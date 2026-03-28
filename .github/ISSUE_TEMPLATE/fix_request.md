---
name: 기능 수정 계획
about: 기존 기능의 문제 해결을 위한 수정 계획을 정리합니다. (Jira 연동 가능)
title: "[BE][Fix] "
labels: ["bug", "fix"]
assignees:
  - 작성자_깃허브ID
---

## 🗂️ Epic 및 일정
Jira **에픽/시작일/기한**을 아래에 입력하세요.

- epic: SY2026-
- start: 2025-00-00
- due: 2025-00-00

---

## 🧠 어떤 기능을 수정할 예정인가요?
수정이 필요한 기능과 문제 상황을 간단히 설명해 주세요.
- 예: 잔재 조회 API에서 CONSUMED 상태 잔재가 함께 반환되는 문제
- 예: 시나리오 생성 시 due date 누락 시 500 에러 발생

---

## 🔄 예상 수정 방식 / 구성 방식 (선택)
어떤 방식으로 수정할지, 동작 흐름이나 로직을 간단히 설명해 주세요.
예:
- 쿼리에 `.where(SteelWip.status == WipStatus.IN_STOCK)` 필터 추가
- due date nullable 처리 및 422 에러 응답 포맷 통일

---

# 알바 공고 수집기 구현 계획

목표: 알바몬·알바천국의 서울·경기 공고를 설정에 따라 수집하고 로컬 검증 뒤 공개 GitHub Actions에서 접속 시험.

구조: 사이트별 목록·상세 파서와 HTTP·브라우저 전송 분리. 버전이 있는 공고 모델과 저장 interface로 향후 클라우드 DB와 웹·TUI 소비자가 같은 데이터를 사용. 이번 범위는 수집·명령행 조회·runner probe이며 클라우드 DB 서비스와 UI는 미선택.

기술: Python 3.12 이상, BeautifulSoup, Playwright Chromium, SQLite, JSONL. 기존 조사에서 사용한 라이브러리로 구현. 외부 페이지는 데이터로만 취급.

## 계약 및 범위

- 식별자: source와 source_id 조합. source_id는 원문 공고 ID 문자열.
- 금액: 원화 정수 또는 null. 급여 단위는 별도 필드. 미확인 금액을 최저임금으로 대체하지 않음.
- 시각: UTC ISO 8601. 최초·최종 관측과 내용 변경 이력 구분.
- 본문: text, image_only, unavailable, policy_excluded, not_requested 상태와 원문 링크 보존. 본문 누락을 전체 수집 성공으로 표시하지 않음.
- 지역은 광고 노출 지역이 아닌 상세 근무지로 필터링. 업종·지역 설정 변경 가능. 미확인 근무지는 검토용으로 보존.
- 알바몬의 robots 제외 iframe을 요청하지 않음. 알바천국은 일반 비로그인 브라우저 사용. 로그인·CAPTCHA·접속 실패는 오류로 반환.
- 자동 수집은 페이지·상세 수·대기·timeout으로 제한. 부분 실패 시 성공 데이터는 보존하되 실행은 비정상 종료.
- 공개 저장소에는 코드·가공 fixture·문서만 포함. runner probe artifact는 집계 상태만 포함. 공고 본문·연락처는 artifact에 올리지 않음.

## Task 1: 파서·설정·저장 경로 (검토 묶음 A)

파일: src/alba_collector/{model,config,parsers,storage}.py, tests/test_core.py.
Interfaces: load_config(path) -> Config; listing_urls(source, html, url) -> list[str]; parse_detail(source, html, url, observed_at, body_html=None) -> Job; SQLiteStore.save(job) -> str.
완료 기준: 잘못된 설정 거부, 실제 형태의 fixture 추출, 접속 실패·없는 공고 거부, 중복 저장·변경 이력 검증. 테스트 실패 확인 후 구현.

## Task 2: 수집·probe·조회 (검토 묶음 A)

파일: src/alba_collector/{transport,runner,cli,__main__}.py, tests/test_runner.py.
Interfaces: collect(config, output, probe=False) -> RunReport; 명령 collect/probe/query.
완료 기준: HTTP 200 오류 페이지 감지, 외부 URL 거부, partial failure 기록·비정상 종료, SQLite/JSONL 조회, 두 소스 소량 실측. 결과를 못 찾은 경우 성공으로 숨기지 않음.

## Task 3: GitHub 실행 (검토 묶음 B)

파일: .github/workflows/{ci,probe}.yml, README.md.
완료 기준: 테스트 CI와 source별 수동 probe, timeout·동시 실행 제어·실패 시 보고서 업로드, cron-job.org POST 연결 설명. 저장소 이름 결정 뒤 게시·runner 실측. 토큰은 공개 코드·로그에 포함하지 않음.

## 검증과 위험

각 묶음 구현 후 단위·통합 시험, 소량 live 시험, 독립 검토. 최종 통합 검토 후 사용자에게 로컬·runner 결과를 구분해 보고.
접속 차단은 우회하지 않고 source error로 기록. 이미지 전용 본문은 OCR 없이 image_only. 목록 일부만 읽는 bounded scan이며 전체 공고를 망라한다고 주장하지 않음. 공고가 목록에서 사라진 것만으로 마감 처리하지 않음.

## 아직 정해지지 않은 사항

공개 저장소·프로젝트 이름은 alba-monitor-job(사용자 결정 alba-monitor-job/D-000001). 클라우드 DB 제공자·자격증명·UI 방식·상시 수집 일정은 이번 구현의 선행 조건이 아니며 후속 설정 대상.

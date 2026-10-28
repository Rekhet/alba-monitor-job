# Alba Collector

알바몬·알바천국 공개 공고의 근무조건과 확보 가능한 본문을 수집하는 Python CLI. 초기 지역은 서울·경기이며 지역·업종·검색어는 `config.toml`에서 변경합니다. 현재 구현은 제한된 페이지를 읽는 bounded scan입니다. 두 사이트의 전체 공고를 망라하거나 마감 여부를 계속 추적하는 서비스는 아닙니다.

## 설치와 첫 실행

Python 3.12 이상이 필요합니다. 프로젝트 디렉터리에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[test]'
python -m playwright install chromium
python -m alba_collector probe --source all --pages 1 --limit 2
python -m alba_collector collect --pages 2 --limit 10
python -m alba_collector query --keyword 카페 --limit 20
```

Linux에서 Chromium 시스템 의존성이 없으면 `python -m playwright install --with-deps chromium`을 사용합니다. `probe`는 공고 내용·연락처를 저장하지 않고 `reports/report.json`에 집계 상태만 기록합니다. `collect`는 `data/jobs.sqlite3`, `data/jobs.jsonl`, `data/report.json`을 생성합니다. 수집 데이터는 git에서 제외됩니다.

## 설정

기존 `config.toml`의 최상위 키를 편집합니다. 다른 파일은 `collect --config 경로/설정.toml` 또는 `probe --config 경로/설정.toml`로 지정합니다.

```toml
regions = ["서울", "경기"]
industries = ["바리스타", "주방보조"]
keywords = []
exclude_keywords = []
```

| 항목 | 의미 |
|---|---|
| `regions` | 실제 근무지 주소의 지역. `[]`이면 지역 제한 없음. 예: `["서울", "경기"]`, `["서울 마포구"]` |
| `industries` | 상세 업종에 포함되는 문자열 중 하나. `[]`이면 업종 제한 없음 |
| `keywords` | 제목·업체·업종·요약·본문 중 하나에 포함될 문자열. 목록 내 OR 조건 |
| `exclude_keywords` | 일치하는 공고 제외 |
| `sources` | `albamon`, `alba` 중 수집 대상 |
| `pages_per_source` | 사이트별 목록 페이지 수, 1~100 |
| `max_details_per_source` | 사이트별 상세 요청 수 상한, 1~1000. 필터링 전 공고 수 기준 |
| `request_delay_seconds` | 페이지 이동 사이 최소 대기, 0.5~60초 |
| `timeout_seconds` | 요청·브라우저 작업 제한, 1~120초 |
| `collect_body` | 확보 가능한 상세 모집요강 수집 여부 |

업종·근무지 미기재 공고는 검토 대상으로 유지합니다. 보고서의 `unknown_location`으로 근무지 미확인 건수를 확인합니다. 광고의 노출 지역과 상세 근무지가 다를 수 있어 상세 주소를 기준으로 필터링합니다. 제한된 목록에서 후보를 먼저 읽으므로 지역 조건에 맞는 결과가 없을 수도 있습니다. 그 경우 `parsed`와 `filtered`를 확인합니다.

명령행의 `--source`, `--pages`, `--limit`는 해당 실행에만 설정을 덮어씁니다. `--limit`는 소스별·필터링 전 상세 요청 수 상한입니다. `probe`는 별도 지정하지 않으면 1페이지·상세 2건으로 제한합니다. 알 수 없는 설정 키, 중복 항목, 범위 밖 값은 쓰기 작업 전에 거부합니다.

## 수집 상태와 실패

알바몬은 일반 HTTP와 `JobPosting` 구조화 데이터·공개 근무조건을 사용합니다. 알바천국은 비로그인 Chromium으로 목록·상세 및 본문 iframe을 읽습니다. 로그인이 필요하거나 접속 오류가 나오면 실패로 기록하며 인증·접근 제한 우회는 구현하지 않습니다.

| `body_status` | 의미 |
|---|---|
| `text` | 본문 텍스트 확보. 이미지에만 있는 정보까지 확보했다는 뜻은 아님 |
| `image_only` | 본문에 이미지가 있지만 텍스트 없음. OCR 미구현 |
| `policy_excluded` | 알바몬 robots.txt에서 제외한 모집요강 iframe. 요청하지 않음 |
| `unavailable` | 본문을 요청했으나 확보하지 못함 |
| `not_requested` | 본문 수집을 설정에서 끔 |

`report.ok`는 요청한 목록·상세 수집에서 오류가 없었는지 나타냅니다. 알바몬의 `policy_excluded`나 이미지 전용 공고가 있어도 true일 수 있습니다. `body_complete`는 조사한 공고 모두에서 텍스트 본문이 확보됐는지를 별도로 나타내며, 원문 이미지 정보의 완전성은 보장하지 않습니다. 요청한 본문이 누락되면 성공한 근무조건은 저장하되 실행은 partial 처리합니다.

보고서는 전체 `ok`·`body_complete`와 소스별 집계를 담습니다. 예를 들어 `sources.alba.parsed`는 알바천국 상세 파싱 건수, `sources.alba.filtered`는 그중 설정 조건으로 제외한 건수입니다. `sources.alba.unknown_location`은 상세 근무지를 확인하지 못한 건수입니다.

일부 페이지 실패 시 확보한 목록은 계속 처리합니다. 다른 소스의 성공 데이터도 보존합니다. 오류가 하나라도 있으면 종료 코드 1, 잘못된 설정·파일 오류는 2입니다. 오류 보고에는 원문이나 인증 쿼리 문자열을 넣지 않고 단계·오류 종류만 기록합니다. `probe`에서도 같은 실패 판정 기준을 사용합니다.

## 저장 모델과 클라우드 연결

`Job`은 버전이 있는 공통 계약입니다. 식별자는 `(source, source_id)`, 금액은 KRW 정수 또는 null, 급여 단위는 별도 필드, 관측 시각은 UTC ISO 8601입니다. 미확인 급여를 최저임금으로 채우지 않습니다. `description`은 사이트의 요약이고 `body_text`는 별도 모집요강입니다.

SQLite는 같은 공고의 반복 관측을 덮어쓰되 최초·최종 관측을 유지하고, 내용이 바뀔 때 `job_versions`에 이력을 기록합니다. 오래된 관측은 최신 데이터를 덮어쓰지 않습니다. JSONL은 해당 실행분만이 아니라 SQLite에 누적된 공고의 현재 상태 전체 snapshot이며, 클라우드 DB로 옮기거나 다른 프로그램에서 읽을 수 있습니다.

클라우드 저장소는 `model.JobSink.save(job)` 계약에 맞춰 연결할 수 있습니다. 실제 클라우드 DB adapter·업로드·웹 UI·대화형 TUI는 현재 구현에 포함되지 않습니다. 지금은 `query`로 터미널에서 조회하고 `--json`으로 프로그램용 출력을 얻습니다.

```bash
python -m alba_collector query --source albamon --limit 10
python -m alba_collector query --db data/jobs.sqlite3 --json
python -m pytest -q
python -m compileall -q src
```

## GitHub runner 접속 시험

`ci.yml`은 Python 3.12·3.14에서 테스트·컴파일·wheel build를 수행합니다. `probe.yml`은 수동으로 실행하며 실제 목록과 상세를 소량 조회합니다. runner에는 본문·SQLite·JSONL을 artifact로 올리지 않고 집계 보고서만 7일 보존합니다. 일반 수집 데이터의 원격 DB 업로드는 DB가 정해진 뒤 연결합니다.

```bash
gh workflow run probe.yml --ref BRANCH -f source=all
gh run list --workflow probe.yml --limit 3
gh run view RUN_ID
```

`gh` 설치·로그인 및 대상 저장소 workflow 실행 권한이 필요합니다. `BRANCH`, `RUN_ID`는 실제 저장소의 값으로 바꿉니다. GitHub runner IP에서 두 사이트가 접근 가능한지는 실제 실행 결과로 판단합니다. 로컬 성공만으로 원격 성공을 주장하지 않습니다.

## cron-job.org 연결

현재 연결 대상은 데이터 누적 수집이 아니라 접속 시험인 `probe.yml`입니다. 예약 서비스는 GitHub API에 짧은 실행 요청을 보냅니다. 저장소와 runner 시험을 마친 뒤 다음 값을 설정합니다.

```text
Method: POST
URL: https://api.github.com/repos/OWNER/REPO/actions/workflows/probe.yml/dispatches
Headers:
  Accept: application/vnd.github+json
  Authorization: Bearer <대상 저장소에 한정된 토큰>
  X-GitHub-Api-Version: 2026-03-10
Body: {"ref":"BRANCH","inputs":{"source":"all"}}
```

GitHub의 fine-grained token에는 대상 저장소의 `Actions: write` 권한이 필요합니다. 토큰은 cron-job.org 요청 설정에 보관하고 저장소·로그에 넣지 않습니다. 예약 주기는 아직 정하지 않았으며 자동 등록하지 않았습니다. cron-job.org의 호출 성공은 실행 요청 접수 결과이므로 실제 수집 결과는 Actions 보고서에서도 확인해야 합니다. 외부 예약 호출을 사용해도 runner 대기 시간이 없어지는 것은 아닙니다.

공개 코드 저장소와 공고 원문 공개는 별개입니다. 공고 재배포 가능 범위를 확정하기 전에는 수집한 원문·담당자 연락처를 공개 저장소나 공개 artifact에 올리지 않습니다.

## 근거

- [알바몬 robots.txt](https://www.albamon.com/robots.txt), [개인회원 이용약관](https://www.albamon.com/service-center/terms/member)
- [알바천국 채용정보](https://www.alba.co.kr/job/Main), [이용약관](https://sign.alba.co.kr/policy/agreement.asp?site=WWW)
- [Playwright frame API](https://playwright.dev/python/docs/frames)
- [GitHub 공개 runner 요금·사양](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [cron-job.org FAQ](https://cron-job.org/en/faq/)

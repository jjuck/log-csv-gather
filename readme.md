# log-csv-gather

현장 PC에서 생성되는 CSV 로그를 Google Drive로 업로드하고, 관리 PC에서 같은 Drive 폴더의 로그를 로컬로 다운로드하는 로컬 전용 운영 도구입니다.

일반 사용자는 `run.bat` 하나만 실행해 웹 대시보드에서 초기설정, 인증, 점검, 수동 실행, 스케줄러 등록, 상태 확인을 처리할 수 있습니다.

## 주요 기능

- 현장 PC 업로드 역할과 관리 PC 다운로드 역할 지원
- Google Drive OAuth 인증
- FastAPI 기반 로컬 웹 대시보드
- Windows 작업 스케줄러 등록/해제/간격 변경
- SQLite 기반 로컬 처리 이력 관리
- 실패 재시도, 충돌 보존, 처리 상태 시각화
- 설정 초기화와 로컬 상태 초기화 버튼 제공

## 처리 대상

업로더는 설정된 로그 루트 폴더 아래에서 등록된 원본 폴더만 스캔합니다.

| 원본 폴더 | Drive 로그 타입 |
| --- | --- |
| `PAS Test data` | `PAS` |
| `HM-3203-011 Test data` | `3203` |
| `HM-3903-011 Test data` | `3903` |
| `LITE Test data` | `LITE` |
| `SMIC_Test data` | `SMIC` |

처리 규칙:

- `fail` 폴더와 미등록 폴더는 무시합니다.
- 날짜 폴더는 정확히 `YYYYMMDD` 형식만 허용합니다.
- 파일명에 `总数据`가 포함된 `.csv` 파일만 업로드합니다.
- Drive 저장 경로는 아래 형식으로 정규화합니다.

```text
logs/Array_MIC/{log_type}/{machine_id}/{YYMMDD}/{YYMMDD}_{log_type}.csv
```

예시:

```text
logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv
```

## 빠른 실행

운영 배포본에서는 프로젝트 폴더의 `run.bat`를 더블클릭합니다.

첫 실행 흐름:

1. 역할 선택: 현장 PC 업로더 또는 관리 PC 다운로더
2. 웹 대시보드에서 `초기설정` 실행
3. PC 이름, 검사기명, Drive 루트 폴더 ID, 로그 폴더 또는 다운로드 폴더 설정
4. `Auth`로 Google 계정 최초 인증
5. `Doctor`로 설정과 Drive 접근 상태 점검
6. `Dry-run`으로 처리 예정 파일 확인
7. `Once`로 1회 실제 실행
8. 이상 없으면 스케줄러를 등록해 반복 실행

스케줄러를 등록하면 웹 대시보드나 터미널을 닫아도 Windows 작업 스케줄러가 정해진 간격으로 업로드 또는 다운로드를 실행합니다.

## 개발 실행

개발 환경에서는 Python 3.11 이상을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m log_csv_gather web --config configs\active.yaml
```

테스트:

```powershell
python -m pytest -q
```

## 설정 파일

대시보드가 실제로 사용하는 설정은 `configs/active.yaml`입니다.

- `configs/production.uploader.yaml`: 업로더 템플릿
- `configs/production.downloader.yaml`: 다운로더 템플릿
- `configs/active.yaml`: 현재 PC에서 사용하는 활성 설정

`active.yaml`이 없으면 `run.bat`가 최초 1회 역할 선택을 받고 템플릿을 복사합니다. 이후에는 웹 대시보드의 초기설정 창에서 운영 값을 수정합니다.

민감 정보와 로컬 상태 파일은 커밋하지 않습니다.

- `secrets/oauth-client.json`
- `runtime/`
- `token.json`
- `state.sqlite`
- `app.log`

## 초기화 버튼

`설정 초기화`:

- 등록된 스케줄러가 있으면 해제합니다.
- `configs/active.yaml`을 삭제합니다.
- 다음 `run.bat` 실행 시 역할 선택부터 다시 시작합니다.

`로컬 상태 초기화`:

- `{state_dir}/state.sqlite`를 백업한 뒤 로컬 처리 이력만 초기화합니다.
- 업로드/다운로드 성공 이력, 실패, 충돌, 버튼 상태가 초기화됩니다.
- 원본 CSV 파일, Google Drive 파일, OAuth 토큰, `active.yaml`, `app.log`는 삭제하지 않습니다.

두 버튼을 순서대로 실행하면 원본 로그 파일과 Drive 파일을 제외하고 운영 설정과 로컬 처리 상태를 최초 배포에 가까운 상태로 되돌릴 수 있습니다.

## 참고 문서

- [운영 런북](docs/production-runbook.md)
- [웹 대시보드 설계](docs/web-dashboard.md)
- [Windows 작업 스케줄러 가이드](docs/windows-task-scheduler.md)
- [사용자 가이드](User_Guide.txt)

## 보안 메모

Google 계정 비밀번호, OAuth 토큰, 서비스 키, `secrets` 폴더, `runtime` 폴더는 저장소에 커밋하지 않습니다. PC별 인증은 각 PC의 로컬 토큰 파일로 유지합니다.

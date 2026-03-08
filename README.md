# 3GPP FTP → Confluence Auto-Sync

Google Drive에 저장된 3GPP 표준 문서(zip)를 다운로드하여 파싱한 후 Confluence에 자동 게시하는 파이프라인.

---

## 전체 아키텍처

```
Google Drive (zip files)
        ↓ download_file()
  ./downloads/*.zip
        ↓ extract_zip()
  ./extracted/<stem>/*.doc/.docx
        ↓ parse_docx() / parse_doc()
  구조화된 dict (sections, tables, metadata)
        ↓ merge_parsed_docs() + format_confluence_page()
  Confluence Storage Format HTML
        ↓ ConfluencePublisher.upsert_page()
  Confluence 페이지 (부모 페이지 하위)
```

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `main.py` | 메인 파이프라인 (전체 흐름: 다운로드 → 파싱 → 게시) |
| `reprocess_all.py` | 이미 추출된 파일만 재파싱 → Confluence 업데이트 (다운로드 없음) |
| `process_oom_specs.py` | OOM 위험 대형 스펙(38133, 38523-1) 별도 처리 (파일 크기 한도 적용) |
| `retry_large.py` | 실패한 대용량 zip / 손상 zip 재처리 (Drive에서 재다운로드) |
| `retry_doc.py` | 특정 .doc 파일만 재처리 (추출 완료된 파일 대상) |
| `cleanup.py` | 부모 페이지 하위 모든 Confluence 페이지 삭제 (초기화용) |
| `cleanup_old_pages.py` | 구버전/고아 Confluence 페이지 선별 삭제 |

---

## 환경 변수 (.env)

```env
GDRIVE_SERVICE_ACCOUNT_JSON=./service_account.json
GDRIVE_FOLDER_ID=<Google Drive 폴더 ID>
CONFLUENCE_URL=https://<your-domain>.atlassian.net
CONFLUENCE_USER=<이메일>
CONFLUENCE_API_TOKEN=<API 토큰>
CONFLUENCE_SPACE_KEY=<스페이스 키>
CONFLUENCE_PARENT_PAGE_ID=<부모 페이지 ID>
```

---

## 실행 순서

### 1. 최초 전체 실행

```bash
python main.py
```

Google Drive에서 zip 파일 전체를 다운로드 → 추출 → 파싱 → Confluence 게시.

### 2. 재처리 (추출 파일 재활용)

```bash
python reprocess_all.py
```

`./extracted/` 하위 모든 디렉터리를 재파싱하여 Confluence 업데이트.
OOM 위험 스펙(`38133-j30`, `38523-1-j30`)은 자동 스킵.

**특정 스펙부터 재시작:** `reprocess_all.py` 상단의 `START_FROM` 변수에 디렉터리명 입력.

### 3. OOM 위험 대형 스펙 처리

```bash
python process_oom_specs.py
```

`reprocess_all.py`에서 스킵된 두 스펙(`38133-j30`, `38523-1-j30`)을 파일 크기 합계 10MB 이하로 제한하여 처리.

### 4. 구버전 페이지 정리 (선택)

```bash
# dry-run (실제 삭제 없이 대상 확인)
python cleanup_old_pages.py

# 실제 삭제
python cleanup_old_pages.py --execute
```

다음 3가지 유형의 고아 페이지를 탐색·삭제:
- `38900-f00` 형식 stem-only 페이지 (구버전 retry_doc.py 생성)
- `3GPP TR 38.900 V15.0.0...` 형식 페이지 (초기 파이프라인 구버전)
- 잘못된 doc_type 중복 페이지 (TR이 맞는데 TS 페이지가 존재하는 경우)

---

## 주요 모듈 설명 (main.py)

### Google Drive 연동

| 함수 | 설명 |
|------|------|
| `get_gdrive_service()` | Service Account 인증 |
| `list_files_in_folder()` | 폴더 재귀 탐색 (공유 드라이브 포함) |
| `download_file()` | 대용량 파일 다운로드 (acknowledgeAbuse 처리) |
| `extract_zip()` | zip → doc/docx 추출 (한글 파일명 EUC-KR 변환) |

### 문서 파싱

| 함수 | 설명 |
|------|------|
| `parse_docx()` | python-docx 기반 구조 파싱 (헤딩 계층, 표, 단락) |
| `parse_doc()` | mammoth → olefile → raw text 순서 폴백 |
| `_extract_text_from_ole()` | OLE2 바이너리 .doc 텍스트 추출 (CP1252/UTF-16LE) |
| `_filter_word_markup_lines()` | Word 내부 마크업/SPRM 레코드 라인 필터링 |
| `_extract_doc_type_and_title()` | TS/TR 판별 및 문서 제목 추출 (4단계 패턴 매칭) |
| `merge_parsed_docs()` | 여러 docx 파싱 결과 병합 |

### Confluence 게시

| 함수/클래스 | 설명 |
|------------|------|
| `format_confluence_page()` | Confluence Storage Format HTML 생성 |
| `ConfluencePublisher` | 페이지 생성/업데이트 (부모 하위에서만 검색, rate limiting, 3회 재시도) |
| `_sanitize_title()` | 깨진 유니코드 감지 → fallback 제목 사용 |

---

## Confluence 페이지 구조

각 스펙 페이지는 다음 구조로 구성:

```
[TOC 매크로]
[Info 패널: 메타데이터 (author, created, subject, keywords)]
[h1~h4 헤딩]
  [본문 텍스트 - 투명 테이블로 감싸 넓이 통일]
  [데이터 테이블]
...
[Expand 매크로: Full Text (검색 인덱싱용, 최대 15,000자)]
```

**본문과 표의 넓이 통일 방법:**
Confluence는 `<table>` 요소를 자동으로 `<div class="table-wrap">`으로 감싸 동일한 CSS를 적용한다.
본문 텍스트도 `border:none` 투명 테이블로 감싸서 동일한 렌더링 경로를 타게 함.

---

## 파싱 전략 상세

### .docx 파싱 (`parse_docx`)

1. ToC 스타일 단락 스킵 (페이지 번호 제거)
2. Heading 1~4 스타일 → 섹션 계층 구조 생성
3. 표 → `_parse_table()`: 병합 셀 colspan 계산 포함
4. 메타데이터: author, created, modified, subject, keywords (템플릿 플레이스홀더 `<...>` 제거)

### .doc 파싱 (`parse_doc`)

1. **mammoth**: .doc가 실제로 docx 포맷인 경우 HTML 변환
2. **olefile**: OLE2 바이너리에서 WordDocument/1Table/0Table 스트림 추출
   - CP1252, UTF-16LE 두 인코딩 모두 시도 → ASCII 비율 높은 것 선택
   - CJK 문자 제거 (ASCII + 한글만 허용)
   - `_filter_word_markup_lines()`: Word 내부 SPRM 레코드, 필드 코드 제거
   - OLE 바이너리 헤더 제거: `3GPP TS/TR` 마커 이전 내용 스킵
3. **raw text**: 최후 수단 (UTF-16LE → ASCII 폴백)

### Word 내부 마크업 필터 (`_filter_word_markup_lines`)

OLE 텍스트 추출 시 Word 내부 포맷 코드가 섞여 출력되는 문제를 해결:

| 패턴 | 예시 | 처리 |
|------|------|------|
| Word 필드 코드 | `STYLEREF`, `MERGEFORMAT`, `EMBED` | 해당 줄 제거 |
| SPRM 약어 2개 이상 | `mH nH tH OJ` | 해당 줄 제거 |
| SPRM 레코드 패턴 | `h\|\t mH sH h` | 해당 줄 제거 |
| 짧은 줄 + 마크업 | `sH cH` (30자 미만) | 해당 줄 제거 |
| 단어 60%가 2자 이하 | `y h mH sH CJ` 6토큰 이상 | 해당 줄 제거 |

### TS/TR 판별 및 제목 추출 (`_extract_doc_type_and_title`)

**TS/TR 판별:** 전체 텍스트 앞 2,000자에서 "Technical Report" / "3GPP TR" 키워드 탐색.

**제목 추출 (4단계 우선순위):**
1. TSG RAN 라인 이후 실제 문서 제목 ("`Radio Access Network` → ... → `(Release XX)`")
2. `NR;` / `LTE;` 바로 다음 줄 제목
3. `Scope` 섹션 첫 문장
4. 첫 번째 의미있는 섹션 헤딩 (15자 이상, 숫자로 시작하지 않음)

---

## 크기 제한 처리

Confluence 페이지 크기 제한: **4MB** (UTF-8 기준)

초과 시 단계적 축소:
1. sections를 3/4씩 반복 축소
2. 섹션 제거 후에도 초과: full_text를 5,000자 → 절반씩 반복 축소

대형 스펙(`process_oom_specs.py`): 파일 크기 누적 **10MB** 이하 파일만 파싱.

---

## 로그

```
./logs/sync.log        # main.py 실행 로그
./logs/reprocess.log   # reprocess_all.py 로그 (파일로 리디렉션 시)
```

---

## 주요 이슈 및 해결 이력

### 1. .doc OLE 텍스트에 Word 내부 마크업 출력

**증상:** Confluence 페이지에 `STYLEREF ZA 3GPP TS 38.475`, `y h| mH sH h` 등 Word 내부 포맷 코드가 본문에 포함됨.

**원인:** `olefile`로 추출한 WordDocument 스트림에 SPRM(Single Property Modifier) 레코드와 필드 코드가 ASCII 범위로 출력 가능한 쓰레기 문자로 포함됨.

**해결:** `_filter_word_markup_lines()` 함수 추가. 5가지 패턴(필드 코드, SPRM 약어 2개+, SPRM 레코드, 짧은 줄+마크업, 고밀도 단음절 토큰)으로 마크업 라인 제거.

### 2. 인코딩 오류 페이지 (CJK 문자 대량 포함)

**증상:** 일부 Confluence 페이지에 102,893개 CJK 문자가 포함된 인코딩 오류 발생.

**원인:** OLE 필터 적용 이전 구버전 파이프라인이 생성한 페이지가 남아 있음. `reprocess_all.py`는 타이틀이 다른 경우 기존 페이지를 덮어쓰지 않아 고아 페이지로 잔존.

**해결:** `cleanup_old_pages.py`로 38개 구버전/고아 페이지 삭제.

### 3. 본문 텍스트와 표 넓이 불일치

**증상:** Confluence에서 표는 페이지 전체 폭으로 표시되지만 본문 텍스트는 더 좁게 표시됨.

**원인:** Confluence는 `<table>` 요소를 자동으로 `<div class="table-wrap">`으로 감싸는 반면 `<div><p>` 본문은 다른 CSS 경로를 탐. `overflow-x:auto`, `overflow-wrap` 등 div inline style은 Confluence가 렌더링 시 제거.

**해결:** 본문 텍스트를 `border:none` 투명 `<table>`로 감싸서 Confluence가 동일한 `table-wrap` CSS를 적용하도록 변경. 동시에 데이터 테이블의 기존 `<div>` 외부 래퍼 제거.

### 4. OOM 위험 대형 스펙

**증상:** 38133-j30 (22파일/36MB), 38523-1-j30 (12파일/30MB) 처리 시 메모리 1.2GB+ 도달.

**해결:** `reprocess_all.py`에서 `SKIP_STEMS`로 제외, `process_oom_specs.py`에서 파일 크기 누적 10MB 이하로 선별 처리.

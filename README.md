# 3GPP FTP → Confluence Auto-Sync

Google Drive에 저장된 3GPP 38 시리즈 표준 문서(zip)를 다운로드·파싱하여 Confluence에 자동 게시하고, 품질 유지를 위한 사후 처리 스크립트를 포함한 파이프라인.

---

## 전체 아키텍처

```
Google Drive (zip files)
        ↓ download_file()
  ./downloads/*.zip
        ↓ extract_zip()
  ./extracted/<stem>/*.doc/.docx
        ↓ parse_docx() / parse_doc() / parse_doc_word_com()
  구조화된 dict { title, sections, tables, full_text, metadata }
        ↓ merge_parsed_docs() + format_confluence_page()
  Confluence Storage Format HTML
        ↓ ConfluencePublisher.upsert_page()
  Confluence 페이지 (부모 페이지 하위, 스펙 번호 오름차순 정렬)
```

---

## 파일 구성

### 메인 파이프라인

| 파일 | 역할 |
|------|------|
| `main.py` | 핵심 파이프라인: 다운로드 → 추출 → 파싱 → Confluence 게시. 모든 공통 함수 포함 |
| `reprocess_all.py` | 추출 완료된 파일을 재파싱하여 Confluence 업데이트 (Drive 다운로드 없음). `START_FROM` 변수로 특정 스펙부터 재시작 가능 |
| `process_oom_specs.py` | OOM 위험 대형 스펙(38133-j30, 38523-1-j30) 별도 처리. 파일 크기 누적 10MB 이하로 제한 |
| `retry_large.py` | 실패한 대용량 zip 또는 손상 zip을 Drive에서 재다운로드하여 재처리 |
| `retry_doc.py` | 특정 .doc 파일만 재처리 (이미 추출 완료된 파일 대상) |

### 사후 처리 및 품질 관리

| 파일 | 역할 |
|------|------|
| `reprocess_by_id.py` | 페이지 ID 직접 지정으로 재파싱·업데이트 (제목 매칭 없음, 21개 단일 `<p>` 문제 페이지 대상) |
| `reprocess_doc_via_word.py` | Microsoft Word COM 자동화로 .doc 파일을 구조적으로 파싱 후 Confluence 업데이트. 헤딩 계층(H1~H4), 본문, NOTE, 참조 등 스타일별 분리 |
| `reformat_old_pages.py` | 구버전 포맷 페이지(info 매크로·투명 테이블 없음)를 신규 포맷으로 직접 변환. .doc 재파싱 없이 기존 Confluence body를 래핑 |
| `reprocess_targets.py` | 특정 stem 목록 대상 재파싱 스크립트 (내부 참고용, 가비지 .doc 파일에는 사용 불가) |
| `fix_empty_titles.py` | 33개 빈 제목 페이지에 3GPP 공식 사이트 기준 올바른 제목 적용 |
| `fix_linebreaks.py` | 단일 `<p>` 블록에 모든 텍스트가 몰린 페이지의 줄바꿈 수정 (TR 38.855 대상) |
| `scan_linebreaks.py` | 전체 페이지 중 50KB 이상 단일 `<p>` 블록 탐지 후 일괄 단락 분리 (21개 페이지 처리) |
| `scan_titles.py` | 섹션 번호·부록 제목·본문 텍스트 등이 페이지 제목으로 잘못 설정된 페이지 탐지 |
| `update_index.py` | 부모 페이지(인덱스)를 전체 자식 페이지 목록 기반으로 자동 재생성. TS/TR 통합 테이블, 스펙 번호 오름차순 정렬 |
| `reorder_pages.py` | Confluence 하위 페이지 순서를 스펙 번호 오름차순으로 재정렬 (REST API move 사용) |

### 정리 스크립트

| 파일 | 역할 |
|------|------|
| `cleanup.py` | 부모 페이지 하위 모든 Confluence 페이지 삭제 (초기화용, 주의) |
| `cleanup_old_pages.py` | 구버전/고아 Confluence 페이지 선별 삭제 (stem-only 제목, 구버전 포맷 등) |

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

Google Drive에서 zip 전체 다운로드 → 추출 → 파싱 → Confluence 게시.

### 2. 재처리 (추출 파일 재활용)

```bash
python reprocess_all.py
```

`./extracted/` 하위 모든 디렉터리를 재파싱하여 Confluence 업데이트.
OOM 위험 스펙(`38133-j30`, `38523-1-j30`)은 자동 스킵.
특정 스펙부터 재시작 시 `START_FROM = "38xxx-xxx"` 변수 설정.

### 3. OOM 위험 대형 스펙 처리

```bash
python process_oom_specs.py
```

`reprocess_all.py`에서 스킵된 두 스펙을 파일 크기 누적 10MB 이하로 처리.

### 4. .doc 파일 Word COM 재파싱

```bash
python reprocess_doc_via_word.py
```

Microsoft Word가 설치된 환경에서 바이너리 .doc 파일을 COM 자동화로 정확히 파싱.
헤딩 계층 구조(Foreword / 1 Scope / 2 References / 3.1 Definitions ...)가 Confluence에 올바르게 반영됨.

### 5. 인덱스 페이지 업데이트

```bash
python update_index.py
```

부모 페이지를 전체 자식 페이지 목록 기반 인덱스 테이블로 재생성.

### 6. 페이지 순서 재정렬

```bash
python reorder_pages.py
```

Confluence 페이지 목록을 스펙 번호 오름차순으로 재정렬.

### 7. 구버전 페이지 정리 (선택)

```bash
python cleanup_old_pages.py          # dry-run
python cleanup_old_pages.py --execute  # 실제 삭제
```

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
| `_parse_html_to_struct()` | mammoth HTML 결과를 섹션 구조로 변환. XML leak 필터 포함 |
| `_filter_word_markup_lines()` | Word 내부 마크업/SPRM 레코드 라인 필터링 |
| `_extract_doc_type_and_title()` | TS/TR 판별 및 문서 제목 추출 (4단계 패턴 매칭) |
| `_extract_spec_number()` | zip stem에서 스펙 번호 추출 (38xxx-xxx → 38.xxx) |
| `_sanitize_title()` | 깨진 유니코드 감지 → fallback 제목 사용 |
| `merge_parsed_docs()` | 여러 doc 파싱 결과 병합 (sections, tables, full_text) |

### Confluence 게시

| 함수/클래스 | 설명 |
|------------|------|
| `format_confluence_page()` | Confluence Storage Format HTML 생성 (TOC + info 매크로 + 본문) |
| `ConfluencePublisher` | 페이지 생성/업데이트 (부모 하위에서만 검색, rate limiting, 3회 재시도) |

---

## Confluence 페이지 구조

각 스펙 페이지는 다음 구조로 구성:

```
[TOC 매크로]
[Info 패널: 페이지 제목 + "Source: 3GPP 38 Series Specification"]
[border:none 투명 테이블]
  [h2 ~ h5 섹션 헤딩]
  [<p> 본문 텍스트]
  [데이터 테이블]
  ...
[Expand 매크로: Full Text (검색 인덱싱용, 최대 15,000자)]
```

**본문과 표의 넓이 통일 방법:**
Confluence는 `<table>` 요소를 자동으로 `div.table-wrap`으로 감싸 동일한 CSS를 적용한다.
본문 텍스트도 `border:none` 투명 `<table>`로 감싸서 동일한 렌더링 경로를 타게 함.

---

## 파싱 전략 상세

### .docx 파싱 (`parse_docx`)

1. ToC 스타일 단락 스킵 (페이지 번호 제거)
2. Heading 1~4 스타일 → 섹션 계층 구조 생성
3. 표 → `_parse_table()`: 병합 셀 colspan 계산 포함
4. 메타데이터: author, created, modified, subject, keywords (템플릿 플레이스홀더 `<...>` 제거)

### .doc 파싱 (`parse_doc`) — OLE 바이너리

1. **mammoth**: .doc가 실제로 OOXML 포맷인 경우 HTML 변환
2. **olefile**: OLE2 바이너리에서 WordDocument 스트림 추출
   - CP1252, UTF-16LE 두 인코딩 시도 → ASCII 비율 높은 것 선택
   - CJK 문자 제거 (ASCII + 한글만 허용)
   - `_filter_word_markup_lines()`: SPRM 레코드, 필드 코드 제거
   - OLE 바이너리 헤더 제거: `3GPP TS/TR` 마커 이전 내용 스킵
3. **raw text**: 최후 수단 (UTF-16LE → ASCII 폴백)

> **주의:** 일부 구형 .doc 파일(38801-e00, 38802-e20 등 e00/e20 시리즈)은 olefile 파싱 결과가 SPRM 레코드(`t 0 4 4`, `y hEZ`)만 출력되는 완전 손상 파일임.
> 이 경우 `reprocess_doc_via_word.py`를 사용하거나, 기존 Confluence body를 `reformat_old_pages.py`로 재포맷.

### Word COM 파싱 (`parse_doc_word_com` in `reprocess_doc_via_word.py`)

Microsoft Word가 설치된 Windows 환경에서만 사용 가능. `win32com.client`로 Word를 백그라운드 실행하여 .doc를 직접 열고 단락별 스타일을 읽음.

| Word 스타일 | Confluence 변환 |
|------------|----------------|
| 제목 1 (Heading 1) | `<h2>` |
| 제목 2 (Heading 2) | `<h3>` |
| 제목 3 (Heading 3) | `<h4>` |
| 제목 4 (Heading 4) | `<h5>` |
| 표준 (Normal) | `<p>` |
| 목차 (TOC) | 스킵 |
| ZA/ZB/ZT/ZU (표지) | 스킵 |

**장점:** 실제 헤딩 계층 구조가 보존됨 (Foreword / 1 Scope / 2 References / 3.1 Definitions ...).
**한계:** Word가 설치된 Windows 환경에서만 동작.

### XML leak 필터 (`_parse_html_to_struct`)

mammoth가 특정 .doc 파일을 변환할 때 drawingml/OpenXML 내부 스키마가 텍스트로 누출되는 현상 처리:

```python
XML_LEAK = re.compile(r'<\?xml\b|xmlns:|schemas\.openxmlformats\.org|schemas\.microsoft\.com')
if XML_LEAK.search(content):
    continue
```

### TS/TR 판별 및 제목 추출 (`_extract_doc_type_and_title`)

**TS/TR 판별:** 전체 텍스트 앞 2,000자에서 "Technical Report" / "3GPP TR" 키워드 탐색.

**제목 추출 (4단계 우선순위):**
1. TSG RAN 라인 이후 실제 문서 제목
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

## 인덱스 페이지 (`update_index.py`)

부모 페이지에 자동 생성되는 전체 스펙 목록 인덱스.

- TS/TR 통합 단일 테이블 (스펙 번호 오름차순 정렬)
- 컬럼: Type (4%) / Spec No. (12%) / Title (84%)
- 빈 제목은 `-` (회색 이탤릭) 표시
- 각 행에 Confluence 페이지 링크 포함
- 하단: Total / TS수 / TR수 / 마지막 업데이트 날짜

정렬 키: `"38.101-1"` → `(38, 101, 1)` 튜플 숫자 비교

---

## 페이지 품질 관리

### 중복/가비지 페이지 탐지 및 삭제

구버전 파이프라인(`reprocess_targets.py`)이 .doc 파싱 실패 시 잘못된 제목으로 새 페이지를 생성하는 문제가 있었음. 이로 인해 총 **38개 이상**의 가비지/중복 페이지가 생성되어 수동으로 정리.

삭제 기준:
- SPRM 가비지 패턴 포함 (`t 0 4 4`, `hzf h`, `jc h _ ja`)
- 동일 스펙에 TS/TR 두 버전이 모두 존재할 때 garbled인 버전
- 회의자료(Tdoc)가 스펙 페이지로 잘못 게시된 경우 (예: TS 38.141-003)

### 제목 이상 탐지 (`scan_titles.py`)

다음 패턴을 탐지:

| 패턴 | 예시 | 설명 |
|------|------|------|
| `.6 형식` | `TS 38.305 - .6 Error handling` | 섹션 번호가 제목으로 |
| `A.1 형식` | `TS 38.300 - A.1 PDU Session Establishment` | 부록 번호가 제목으로 |
| `Annex A` | `TR 38.867 - Annex A: Cost evaluations` | 부록 섹션이 제목으로 |
| 소문자 시작 | `TR 38.717-01 - including contiguous...` | 문장 중간부터 추출 |
| 본문 텍스트 | `TR 38.870 - The requirements in this clause...` | 본문 첫 줄이 제목으로 |

탐지 후 3GPP 공식 사이트(www.3gpp.org/DynaReport/38-series.htm) 기준 올바른 제목으로 수동 수정.

### TS/TR 분류 검증

3GPP 공식 목록과 비교하여 잘못된 TS/TR 분류를 탐지·수정:

| 페이지 | 수정 내용 |
|--------|---------|
| TR 38.173-001 | TR → **TS** (3GPP 공식 목록 기준) |
| TS 38.857 | TS → **TR** |
| TS 38.807, TS 38.826, TS 38.855 | 삭제 (TR 버전이 정상 존재) |

---

## 로그

```
./logs/sync.log        # main.py 실행 로그
```

---

## 주요 이슈 및 해결 이력

### 1. .doc OLE 텍스트에 Word 내부 마크업 출력

**증상:** Confluence 페이지에 `STYLEREF ZA 3GPP TS 38.475`, `y h| mH sH h` 등 SPRM 코드가 본문에 포함됨.

**원인:** `olefile`로 추출한 WordDocument 스트림에 SPRM(Single Property Modifier) 레코드가 ASCII 범위 쓰레기 문자로 포함됨.

**해결:** `_filter_word_markup_lines()` 함수 추가. 5가지 패턴으로 마크업 라인 제거.

---

### 2. 인코딩 오류 페이지 (CJK 문자 대량 포함)

**증상:** 일부 페이지에 102,893개 CJK 문자가 포함된 인코딩 오류.

**원인:** OLE 필터 이전 구버전 파이프라인 생성 페이지가 고아로 잔존.

**해결:** `cleanup_old_pages.py`로 38개 구버전/고아 페이지 삭제.

---

### 3. 본문 텍스트와 표 넓이 불일치

**증상:** 표는 페이지 전체 폭, 본문 텍스트는 더 좁게 표시.

**원인:** Confluence가 `<table>`은 `div.table-wrap`으로 감싸고 `<p>`는 다른 CSS 경로 적용.

**해결:** 본문 텍스트를 `border:none` 투명 `<table>`로 감싸서 동일한 CSS 경로 적용.

---

### 4. OOM 위험 대형 스펙

**증상:** 38133-j30(22파일/36MB), 38523-1-j30(12파일/30MB) 처리 시 메모리 1.2GB+ 도달.

**해결:** `SKIP_STEMS`로 제외 후 `process_oom_specs.py`에서 10MB 이하로 선별 처리.

---

### 5. 구버전 포맷 페이지 (info 매크로 없음)

**증상:** 일부 페이지가 TOC/info 매크로 없이 `<h1> + <p>` 직접 구조로 표시됨.

**원인:** 초기 파이프라인으로 생성된 페이지를 이후 포맷 변경 시 업데이트하지 못함.

**해결:** `reformat_old_pages.py`로 기존 body를 신규 포맷으로 직접 변환 (재파싱 없음).

---

### 6. 단일 거대 `<p>` 블록 (줄바꿈 없음)

**증상:** TR 38.855 등 21개 페이지의 본문 전체가 하나의 `<p>` 태그에 몰려 있음 (최대 628KB).

**원인:** 구버전 파이프라인이 OLE 텍스트 추출 결과를 단락 구분 없이 단일 블록으로 저장.

**해결 1차:** `scan_linebreaks.py`로 문장 경계 패턴 기반 단락 분리 (21개 페이지).

**해결 2차:** `reprocess_doc_via_word.py`로 .doc 파일을 Word COM으로 재파싱하여 실제 헤딩 계층 구조 복원 (14개 .doc 페이지).

---

### 7. XML 스키마 누출 (mammoth 파싱)

**증상:** TS 38.475-030 등 일부 페이지에 `schemas.openxmlformats.org`, `xmlns:` 등 OpenXML 내부 스키마가 본문으로 출력.

**원인:** mammoth가 drawingml 내장 객체를 변환할 때 XML 네임스페이스가 텍스트로 누출.

**해결:** `_parse_html_to_struct()`에 `XML_LEAK` 정규식 필터 추가.

---

### 8. 잘못된 TS/TR 분류 및 중복 페이지

**증상:** 3GPP 공식 TR 스펙이 Confluence에 TS로 등록되거나, 동일 스펙에 TS/TR 두 버전 중복 존재.

**원인:** `_extract_doc_type_and_title()` 패턴 매칭 실패 시 잘못된 doc_type 사용, 또는 `reprocess_targets.py`가 기존 페이지를 찾지 못하고 새 페이지 생성.

**해결:** 3GPP 공식 목록(www.3gpp.org/DynaReport/38-series.htm) 대조 후 잘못 분류된 5개 페이지 수정, 가비지 중복 25개 삭제.

---

### 9. 잘못된 페이지 제목 (섹션 번호·부록 제목 등)

**증상:** `TS 38.305 - .6 Error handling`, `TS 38.300 - A.1 PDU Session Establishment`, `TR 38.867 - Annex A: Cost evaluations` 등 섹션 제목이 페이지 제목으로 사용됨.

**원인:** `_extract_doc_type_and_title()` 4단계 매칭이 모두 실패할 때 첫 번째 섹션 헤딩을 제목으로 fallback.

**해결:** `scan_titles.py`로 이상 제목 탐지 → 3GPP 공식 사이트에서 올바른 제목 확인 후 수정. 총 15개 페이지 제목 수정.

---

## .gitignore

```
.env
*.json              # Service Account 키 파일
downloads/
extracted/
logs/
__pycache__/
```

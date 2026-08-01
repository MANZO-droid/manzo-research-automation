# 01-input

사람이 직접 넣는 **원본 입력**만 두는 폴더입니다. 여기 들어온 원본 파일은 내용을 고치지 않습니다(오타 수정 포함). 형식을 바꾸고 싶으면 새 파일로 따로 만들고 원본은 그대로 둡니다.

## 지금 들어있는 것 (공통 샘플, 가상)

- `N-01-top10-api-snapshot.txt` — 가상 당일 상승률 Top10 원시 자료, 표 형식 (정상 입력 1)
- `N-02-top10-text-list.txt` — 같은 날짜·같은 종목을 텔레그램 채널 요약체로 옮긴 자료 (정상 입력 2, 표현·형식만 다름)

두 파일 모두 강의 `강의자료/common-sample-pack/`의 원본을 그대로 복사한 것이며, 모든 종목명·수치·뉴스 문구는 수업용 가상 정보입니다. 원본은 `강의자료/common-sample-pack/`에도 그대로 남아 있습니다.

## 목 입력 (semiclass-mock-input-generator로 생성)

- `E-01-missing-classification.txt` — 종목코드·구분(우선주/관리종목/ETF/정리매매)이 표시되지 않은 형식 오류 목 입력
- `E-02-duplicate-of-N-01.txt` — N-01과 내용이 같은 중복 제출 목 입력
- `N-03-top10-enriched-news.txt` — N-01과 순위·필터링 결과는 같지만, `정보 요청`으로 남았던 5종목의 뉴스를 3~5건씩 보강한 회귀 시험용 목 입력

세 파일의 시험 목적·기대 행동은 `tests/lesson-02-results.md`의 매니페스트 표에 있습니다.

## 만조리서치 자동화의 실제 입력

- `manzo-real-2026-07-13-049080.txt` — 가상이 아닌 실제 데이터. 이미 커밋된 `stock-analysis-data.json`(2026-07-13, 기가레인 049080)에서 `analyze_stock()`이 Gemini에게 실제로 전달하는 범위(종목명·종목코드·날짜·등락률·뉴스 제목)만 뽑은 것. 입력 정의는 "Gemini 분석 전 원본 자료"로 확정(2026-07-25).

이 파일과 `02-reference/manzo-output-contract.md`를 대조한 결과는 `tests/manzo-regression-2026-07-13.md`에 있습니다.

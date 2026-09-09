# autoshorts

방송/영상 콘텐츠에서 유튜브 쇼츠 후보를 AI로 찾아내고, 리뷰·편집·렌더링까지 돕는
사내 공용 도구. [radihola](https://github.com/kunil-choi/radihola)(KBS 라디오
'라디올라' 코너 전용 도구)를 여러 부서가 같이 쓸 수 있게 일반화한 후속 프로젝트.

## 어떻게 동작하나

무거운 작업(다운로드·음성인식·렌더링)은 각 작업자의 PC에서 직접 실행된다
(하이브리드 구조) - 유튜브 URL을 쓰는 경우 클라우드 서버 IP가 봇으로 차단되는
문제를 피하기 위해서고, 원본 파일을 업로드하는 경우도 어차피 무거운 렌더링을
로컬에서 처리하는 게 낫기 때문이다.

```
1. 입력          - 유튜브 링크 또는 보유 중인 원본 영상 파일
2. 분석          - 자막 추출(유튜브 자막 또는 whisper) + Claude로 대본 분석
3. 조건 선택      - A 훅 위주 / B 핵심 내용 위주 (기본 둘 다) + C 직접 입력한 주제(선택)
4. 길이 선택      - 코너별 목표 길이 (1분/2분/3분 등)
5. 리뷰 화면      - 좌: 영상 미리보기 / 우: 타임코드 + 자막 목록
6. 구간 선택      - 자막 블록을 클릭해 1개 또는 여러 개 구간 선택 (여러 개는 하드컷으로 이어붙임)
7. 초안 렌더      - 배너/로고 없이 빠르게 미리보기 생성 → 자막 오타 수정
8. 메타데이터      - 썸네일 문구, 출연자 이름(자동 추출값 프리필), 로고/하단 이미지 업로드
9. 최종 렌더      - 배너·로고·수정된 자막까지 합성한 완성 mp4 생성
```

## 프로젝트 구조

```
src/autoshorts/
  sources/       - MediaSource 추상화 (YouTubeSource / UploadedFileSource)
  transcript.py  - 자막/whisper 결과를 타임스탬프 있는 Segment로
  analyze.py     - Claude로 후보 구간 제안 (hook/substantive/custom 티어)
  clipbuild.py   - 여러 구간을 하드컷으로 이어붙이기
  render.py      - 세로형(9:16) 쇼츠로 합성 (2단계: 초안 → 최종)
webui/           - 로컬에서 띄우는 리뷰 웹앱 (FastAPI)
```

## 현재 상태

핵심 파이프라인(소스 추상화 → 자막/whisper → Claude 후보 분석 → 하드컷 이어붙이기
→ 2단계 렌더링)과 webui가 전부 연결되어 동작한다. 합성 테스트용 영상으로
source → clipbuild → render_draft → render_final 전체 흐름과 webui의 모든
라우트(작업 생성 → 리뷰 화면 → 초안 빌드 → 최종 렌더링)를 검증했다. 유튜브
경로(`YouTubeSource`)는 이 레포를 만든 빌드 환경에 유튜브 네트워크 접근이 없어
직접 실행해보지는 못했다 - 업로드 경로와 동일한 계약(`extract_clip`이 정확히
그 구간만 반환)을 따르므로 실제 사용 전에 한 번 확인이 필요하다.

아직 없는 것: 로그인/부서별 권한, 여러 부서가 동시에 써도 안전한 데이터 저장
구조(지금은 `data/`에 커밋하는 방식도 아직 붙이지 않음).

## 설정

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp webui/.env.example webui/.env
# webui/.env를 열어 ANTHROPIC_API_KEY를 채운다
```

- Python 3.11+, `ffmpeg`, 한글 폰트(`fonts-nanum` 등) 필요
- Claude API 키(`ANTHROPIC_API_KEY`) 필요 - [console.anthropic.com](https://console.anthropic.com)

## 로컬 리뷰 웹앱 실행

```bash
uvicorn webui.server:app --reload --port 8787
# http://localhost:8787 접속
```

## 테스트

```bash
pip install -r requirements.txt pytest
pytest
```

유튜브 다운로드·Claude API 호출처럼 외부 네트워크가 필요한 부분은 유닛 테스트로
검증할 수 없어서, 정규식 매칭/자막 파싱/ffmpeg 필터 그래프 구성 같은 순수 로직만
테스트로 커버했다.

## 다른 부서에 배포하기 (Windows 원클릭 설치파일 만들기)

이 프로젝트의 원래 문제의식 - "파이썬 설치/venv 같은 게 너무 개인화 돼 있어서
공유하기 어렵다" - 를 해결하기 위해, Python/ffmpeg를 몰라도 되는 배포용 실행파일을
`installer/`에서 빌드할 수 있다. **Windows에서 딱 한 번 실행**하면 된다:

```bat
installer\build.bat
```

이 스크립트가 하는 일:
1. 가상환경 생성 + `requirements.txt`/`pyinstaller` 설치
2. 정적 ffmpeg 빌드를 다운로드해 `installer/ffmpeg_bin/`에 준비
3. PyInstaller로 `dist/autoshorts/` 폴더에 `autoshorts.exe` 빌드
4. ffmpeg와 `.env.example`을 그 폴더로 복사

빌드가 끝나면 `dist/autoshorts/` **폴더 전체**가 배포 가능한 결과물이다 - 이
폴더를 zip으로 압축해서 다른 부서 PC에 풀어주면, 그쪽은 Python도 ffmpeg도
설치할 필요 없이 `autoshorts.exe`만 더블클릭하면 된다 (`.env` 파일이 없으면
`.env.example`을 자동으로 복사해주니, 그 파일을 열어 `ANTHROPIC_API_KEY`만
채우면 끝).

> **참고**: 이 레포를 만든 빌드 환경 자체가 리눅스라 실제 Windows .exe를 직접
> 만들어볼 수는 없었다 - PyInstaller의 패키징 분석 단계(의존성 추적, 데이터 파일
> 배치)는 리눅스에서도 동일하게 동작하므로 그 부분은 실제로 빌드까지 해보고
> 검증했지만, Windows 바이너리 자체의 동작은 검증 전이다. 처음 `build.bat`를
> 돌렸을 때 안 되는 부분이 있으면 로그를 그대로 알려주면 된다.

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

위 1~9단계(리뷰·확인 흐름) 외에, 사람 확인 없이 전부 자동으로 진행하는 **완전
자동 모드**도 별도 경로로 제공한다 - 입력(링크/파일), 목표 길이, 만들 개수만
고르면 후보 선정 → 자막 맞춤법 교정 → 렌더링 → 유튜브 **비공개** 업로드까지
한 번에 끝난다 (아래 "완전 자동 모드 설정" 참고). 기존 리뷰 흐름에는 영향을
주지 않는다. 이 완전 자동 모드는 두 가지 경로로 들어갈 수 있다:

- **수동 버튼**: 메인 화면에서 아무 링크/파일이나 넣고 바로 실행
- **스케줄러**: `config.py`의 `PROGRAMS`에 등록된 정규 방송 프로그램(현재
  "성공예감 이대호입니다", "경제쇼")을 방송 종료 시각 기준으로 자동 감지해서
  사람이 아무것도 안 해도 매일 알아서 실행 (아래 "스케줄러" 참고)

어느 경로든 결과물(비공개 업로드)은 모두 `/library` 화면에 쌓이고, 거기서
공개로 전환하거나 삭제한다 (유튜브 스튜디오에 따로 들어갈 필요 없음).

## 프로젝트 구조

```
src/autoshorts/
  sources/            - MediaSource 추상화 (YouTubeSource / UploadedFileSource)
  transcript.py       - 자막/whisper 결과를 타임스탬프 있는 Segment로
  analyze.py          - Claude로 후보 구간 제안 (hook/substantive/custom 티어) + 자막 교정
  clipbuild.py        - 여러 구간을 하드컷으로 이어붙이기
  render.py           - 세로형(9:16) 쇼츠로 합성 (2단계: 초안 → 최종)
  youtube_upload.py   - 유튜브 업로드/공개전환/삭제/재생목록 추가 (YouTube Data API v3)
  scheduler.py        - config.PROGRAMS의 재생목록을 감시해 자동 트리거
  library.py          - 완전 자동 모드가 올린 영상 기록 (work/library.json)
  config.py           - 길이 프리셋 + PROGRAMS(스케줄러가 감시할 프로그램 목록)
webui/                - 로컬에서 띄우는 리뷰 웹앱 (FastAPI)
default_assets/       - 완전 자동 모드에서 쓸 고정 로고/배너 이미지를 놓는 곳
  <프로그램 키>/      - 그 프로그램 전용 로고 (default_assets/README.txt 참고)
```

## 현재 상태

핵심 파이프라인(소스 추상화 → 자막/whisper → Claude 후보 분석 → 하드컷 이어붙이기
→ 2단계 렌더링)과 webui가 전부 연결되어 동작한다. 합성 테스트용 영상으로
source → clipbuild → render_draft → render_final 전체 흐름과 webui의 모든
라우트(작업 생성 → 리뷰 화면 → 초안 빌드 → 최종 렌더링)를 검증했다. 유튜브
경로(`YouTubeSource`)는 이 레포를 만든 빌드 환경에 유튜브 네트워크 접근이 없어
직접 실행해보지는 못했다 - 업로드 경로와 동일한 계약(`extract_clip`이 정확히
그 구간만 반환)을 따르므로 실제 사용 전에 한 번 확인이 필요하다.

완전 자동 모드/스케줄러/라이브러리(`/library`)는 전체 파이프라인 오케스트레이션
(후보 선정 → 빌드 → 자막 교정 → 렌더링 → 업로드 → library.json 기록 → 공개
전환/삭제)을 유튜브 API 호출을 모킹한 상태로 끝까지 검증했고, 스케줄러의
재생목록 조회(`yt_dlp`)는 두 프로그램 재생목록에 대해 실제로 호출해 확인했다.
다만 실제 구글 OAuth 로그인 흐름과 실제 유튜브 업로드/공개전환/삭제 API 호출
자체는 이 빌드 환경에 인증 정보가 없어 직접 실행해보지 못했다 - 처음 써볼 때
브라우저 동의 화면이 뜨는지, 업로드가 실제로 되는지 한 번 확인이 필요하다.

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

## 완전 자동 모드 설정 (선택)

이 모드까지 쓰려면 아래를 준비한다 - 안 해도 나머지 기능(1~9단계 리뷰 흐름)은
그대로 쓸 수 있다.

1. **유튜브 업로드 인증**: 구글 클라우드 콘솔에서 YouTube Data API v3를 켠
   프로젝트의 OAuth 클라이언트(`client_secret.json`)를 발급받아, `webui/.env`와
   같은 위치(배포본은 `.exe` 옆, 개발 중이면 `webui/` 폴더가 아니라 저장소
   루트)에 `client_secret.json`이라는 이름으로 놓는다. 첫 업로드 때 브라우저가
   한 번 뜨면서 업로드할 유튜브 채널 계정으로 로그인/동의하면, 그 뒤로는
   `youtube_token.json`에 저장된 토큰으로 자동 재사용된다 (재로그인 불필요).
   업로드/비공개·공개 전환/삭제/재생목록 추가까지 전부 쓰므로 OAuth 동의
   화면에는 "YouTube 계정 관리" 권한 전체가 뜬다 (단순 업로드 권한보다 넓음) -
   `youtube_token.json`을 예전(업로드 전용 범위) 버전부터 갖고 있었다면 한 번
   지우고 다시 로그인해야 한다. 토큰 범위는 한 번 부여된 뒤 자동으로 넓어지지
   않는다.
2. **고정 로고/배너 이미지**: `default_assets/README.txt`를 참고해서 공용
   이미지(`logo_right.jpg` 등)와 프로그램별 이미지(`<프로그램 키>/logo_left.*`
   등)를 채운다. 자동 모드는 사람이 매번 이미지를 올릴 수 없으므로 이 폴더에
   있는 파일을 그대로 쓴다 - 없으면 그 자리는 비워둔 채로 렌더링된다. 현재
   "성공예감 이대호입니다"/"경제쇼" 프로그램 로고와 KBS 1라디오 채널 로고는
   이미 저장소에 포함돼 있다; 라디올라 코너 로고(`default_assets/banner_bottom.*`)는
   아직 없으므로 직접 넣어야 한다.
3. **(선택) 재생목록 추가**: `.env`의 `RADIHOLA_PLAYLIST_ID`에 본인 채널의
   "라디올라" 코너 재생목록 id를 넣으면, 자동 업로드된 영상이 그 재생목록에도
   자동으로 추가된다. 비워두면 재생목록 추가 없이 채널에만 올라간다.

## 스케줄러 (등록된 프로그램 자동 감지)

`src/autoshorts/config.py`의 `PROGRAMS`에 등록된 프로그램(현재 KBS 1라디오의
"성공예감 이대호입니다", "경제쇼")에 대해, 앱이 켜져 있는 동안 백그라운드에서
계속 동작한다:

- 평일에만 동작, 각 프로그램의 방송 종료 시각 + `check_delay_min`(기본 30분)
  부터 그 프로그램의 유튜브 재생목록을 주기적으로 확인한다.
- 새 영상(그날 처음 보는 영상 id)을 발견하면 그 프로그램의 설정(목표 길이,
  개수, 전용 로고)으로 완전 자동 모드를 자동 실행한다.
- 영상을 못 찾은 채로 `GIVE_UP_AFTER_HOURS`(기본 4시간)가 지나면 그날은
  포기하고 다음날 다시 시도한다.
- 상태는 `work/scheduler_state.json`에 저장되므로 앱을 껐다 켜도 같은 날
  영상을 중복 처리하지 않는다.

**이 기능이 동작하려면 앱(브라우저가 아니라 `autoshorts.exe` 자체)이 방송이
끝난 뒤에도 계속 켜져 있어야 한다** - 지금처럼 필요할 때만 실행하는 방식이
아니라 상시 실행 구조가 된다는 뜻이다. 끄고 싶으면 `.env`의
`AUTOSHORTS_SCHEDULER_ENABLED=0`으로 비활성화할 수 있다 (수동 "완전 자동
모드" 버튼과 1~9단계 리뷰 흐름에는 영향 없음). 프로그램 목록/방송 종료
시각/목표 길이/개수를 바꾸려면 `config.py`의 `PROGRAMS`를 수정한다.

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
4. ffmpeg, `.env.example`, `default_assets/`를 그 폴더로 복사 (이전 빌드에
   이미 있던 `.env`/`client_secret.json`/`youtube_token.json`/로고 이미지는
   그대로 유지됨)

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

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

프로젝트 뼈대만 잡힌 상태 - 인터페이스와 디렉터리 구조는 정해졌지만
`sources`/`analyze`/`clipbuild`/`render`의 실제 로직(Claude 프롬프트, ffmpeg
합성 등)은 아직 구현 전이다. radihola에 있는 검증된 로직을 이 구조에 맞게
포팅/일반화하는 작업이 다음 단계.

## 설정

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp webui/.env.example webui/.env  # 아직 없음 - ANTHROPIC_API_KEY 등 추가 예정
```

- Python 3.11+, `ffmpeg`, 한글 폰트(`fonts-nanum` 등) 필요
- Claude API 키(`ANTHROPIC_API_KEY`) 필요 - [console.anthropic.com](https://console.anthropic.com)

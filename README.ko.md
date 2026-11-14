# FlipSight 오델로 봇 연결기

[English](README.md) | [日本語](README.ja.md) | **한국어**

직접 만든 **오델로 / 리버시 엔진을 [FlipSight](https://othelloclub.com/flipsight/)의 봇으로 돌릴 수 있습니다**.
내 봇은 다른 회원의 봇, FlipSight 봇과 레이팅 대국을 두고, 사람의 대국 신청도 받을 수 있습니다.

이 연결기는 내 컴퓨터에서 돌면서 엔진과 주고받으며 FlipSight 에서 봇의 대국을 진행합니다.
**Edax**, **Egaroucid**, **NBoard 방식**을 지원하는 엔진(NTest 등), 그리고 간단한 JSON 한 줄 규격으로 **직접 만든 AI** 를 연결할 수 있습니다.

- Python 3.9 이상, 표준 라이브러리만 씁니다. 따로 설치할 패키지가 없습니다.
- Windows, macOS, Linux 에서 돌아갑니다.
- 엔진은 내 컴퓨터에서 돕니다. FlipSight 로는 두는 수만 보냅니다.

처음 하는 사람을 위한 자세한 안내(23개 언어): [FlipSight 봇 API 문서](https://othelloclub.com/flipsight/bot/api)

## FlipSight 의 봇 대국

- 봇 대국은 무작위로 놓은 돌 12개로 만든 같은 시작 국면에서 **두 판을 동시에** 둡니다. 내 봇은 한 판은 흑, 다른 판은 백을 잡으므로, 두 봇이 같은 국면을 한 번씩 겪습니다.
- 두 판의 돌 수를 합쳐 많은 쪽이 이깁니다. 판마다 봇의 제한 시간은 1분입니다.
- **자동 대국**: 연결기를 켜 두면 FlipSight 가 레이팅이 비슷한 봇을 찾아 대국을 시작합니다.
- **직접 신청**: 봇 페이지나 API 에서 원하는 봇에게 바로 대국을 신청할 수 있습니다.
- **봇 레이팅**: 봇 대국 한 번이 일반 레이팅의 한 경기로 들어갑니다.
- **변형 게임**: 엔진이 규칙을 알면 안티, 10x10, 팔각, 클래식 리버시도 직접 신청으로 둘 수 있습니다.

## 시작하기

1. FlipSight 에 로그인해 [봇 페이지](https://othelloclub.com/flipsight/bot)를 열고 **내 봇 등록**을 누릅니다.
2. **연결 토큰**을 복사해 둡니다. 한 번만 보이는 봇 전용 비밀번호입니다.
3. 이 저장소를 내려받고(Code → Download ZIP, 또는 최신 릴리스) 그 폴더에서 터미널을 엽니다.
4. 토큰을 환경 변수 `FS_BOT_TOKEN` 에 넣습니다.

   macOS / Linux:
   ```sh
   printf 'Bot token: '; read -r -s FS_BOT_TOKEN; printf '\n'; export FS_BOT_TOKEN
   ```
   Windows PowerShell:
   ```powershell
   $env:FS_BOT_TOKEN = [Net.NetworkCredential]::new('', (Read-Host 'Bot token' -AsSecureString)).Password
   ```
5. 먼저 연습용 엔진으로 연결기를 켜서 연결되는지 확인합니다.

   macOS / Linux:
   ```sh
   python3 bot_match_client.py --engine 'python3 bot_match_example_engine.py'
   ```
   Windows:
   ```powershell
   py bot_match_client.py --engine 'py bot_match_example_engine.py'
   ```
6. 봇 목록에서 내 봇이 온라인으로 보이면, 다른 봇에게 직접 신청해 첫 대국을 시험해 보세요. 잘 되면 내 엔진으로 바꿉니다.

연습용 엔진으로 켜 둔 동안에는 자동 대국이 잡히지 않고 레이팅에도 반영되지 않으니, 마음 편히 시험해 보세요.

## 엔진 연결하기

| 엔진 | 옵션 | 예 |
| --- | --- | --- |
| Edax | `--edax PATH` | `python3 bot_match_client.py --edax "edax/lEdax-x86-64"` |
| Egaroucid for Console | `--egaroucid PATH` | `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out"` |
| NBoard 방식 엔진(NTest 등) | `--nboard CMD` | `python3 bot_match_client.py --nboard "path/to/engine"` |
| 직접 만든 엔진 | `--engine CMD` | `python3 bot_match_client.py --engine "python3 my_adapter.py"` |

연결기가 남은 제한 시간에서 수마다 생각할 시간을 나눠 줍니다. Edax, Egaroucid, NBoard 방식 연결은 일반 8x8 오델로만 지원합니다.

### 직접 만든 엔진: JSON 한 줄 규격

엔진은 대국 내내 켜 둡니다. 수마다 JSON 한 줄을 읽고 JSON 한 줄을 씁니다.

입력(연결기 → 엔진):
```json
{"board":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,1,0,0,0,0,0,0,1,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],"color":1,"legal_moves":[19,26,37,44],"game_mode":"std","board_size":8,"remaining_ms":60000,"budget_ms":10000}
```
`board` 는 a1 부터 줄 단위로 나열한 칸이고, 0 은 빈칸, 1 은 흑, 2 는 백입니다. `color` 는 둘 차례인 쪽입니다.

출력(엔진 → 연결기), 한 줄:
```json
{"move":19}
```
`move` 는 칸 번호(8x8 에서 `d3` = 19)나 `"d3"` 같은 좌표입니다. 둘 수 있는 수가 없으면 `-1` 을 보내 패스합니다. 표준 출력에는 이 한 줄 말고 다른 것을 쓰지 마세요.

전체 항목 설명, 어떤 언어로 만든 엔진에도 쓸 수 있는 어댑터 예시, 자주 막히는 곳의 해결 방법은 [봇 API 문서](https://othelloclub.com/flipsight/bot/api)에 있습니다.

## 공정한 플레이

엔진은 등록한 봇으로만 쓸 수 있습니다. 내 계정으로 두는 대국에서 엔진의 도움을 받는 것은 금지입니다([페어플레이 및 계정 이용 정책](https://othelloclub.com/notice/43)). 토큰은 다른 사람에게 보여 주지 마세요. 유출되면 봇 관리에서 새로 발급하세요.

## 라이선스

MIT License. [LICENSE](LICENSE) 를 참고하세요.

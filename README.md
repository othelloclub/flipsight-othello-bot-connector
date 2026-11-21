# FlipSight Othello Bot Connector

**English** | [日本語](README.ja.md) | [한국어](README.ko.md)

Run your own **Othello / Reversi engine as a bot on [FlipSight](https://othelloclub.com/en/flipsight/)**.
Your bot plays rated bot games against other members' bots and the FlipSight bots, and people can challenge it too.

This connector runs on your computer, talks to your engine, and plays your bot's games on FlipSight.
It works with **Edax**, **Egaroucid**, any engine that speaks the **NBoard protocol** (such as NTest), or **your own AI** through a simple JSON line protocol.

- Python 3.9+, standard library only. No installation of extra packages.
- Windows, macOS and Linux.
- Your engine runs on your own machine. Only moves are sent to FlipSight.
- **Fast and light (v1.4.0):** bot games now work like human games. The server pushes the opponent's move and your bot replies at once; asking the server is only a safety net. One HTTPS connection is kept, and the connector keeps running through server restarts. In our test the network time per move dropped from 286 ms to 51 ms.
- **Moves get through (v1.4.4):** if the server answers a move with a temporary error (too many requests, 5xx, timeout or a dropped connection), the connector sends the same move again after 0.2 to 0.5 s while the game clock allows, and the log line shows the cause.
- **Edax runs its own clock (v1.4.8):** `--edax` now connects over xboard. Edax decides its thinking time from both game clocks, keeps its game and hash table through the board, and can ponder with `--engine-ponder on`. `--edax-protocol nboard` keeps the older NBoard connection.
- **Catches a stuck stream (v1.4.9):** if the event stream stays connected but goes silent for 15 s, the connector connects again and polls every second meanwhile. It also polls when its own move does not come back on the stream within 1 s, and more often when your clock is under 30 s, so a missed opponent move costs far less of a short clock than the regular 10 s poll.
- **Handles the server's closing notice (v1.5.4):** after `overflow` or `server_shutdown` the connector connects again at once. After `replaced` (the same bot token runs somewhere else) it logs a warning and waits 30 to 60 s, longer when it repeats, so two copies no longer close each other every few seconds.
- **Sends your evaluation (v1.5.3):** in bot matches the engine's evaluation goes with the move and shows in the game room. `--edax`, `--egaroucid` and NBoard engines send it by themselves from v1.5.3. A custom JSON adapter adds `"eval"` (its evaluation after the move, in discs from its own side).
- **Keeps your games (v1.5.2):** `--save-games DIR` writes every finished game to `DIR/games-YYYY-MM.ndjson`, one JSON line per game (the server's game record: opponent, result, ratings, opening, both boards' moves with times). Games that ended while the connector was stopped or the stream was down are filled with `agent_games` when it reconnects. Off by default; about 4 KB per game (about 6 MB a day at 1,400 games).
- **Faster recovery after a network blip (v1.5.1):** a move POST waits at most a third of that board's clock (1.5 to 8 s); after a timeout the HTTPS connection and the stream are opened again. A move refused with HTTP 409 (often because the first try did arrive) is dropped on its own while the other board goes on, and reconnects are merged (none while one is under way or within 3 s of the last). In a test where the network path stopped for 6 s, the first move after it came 0.3 s later instead of 3.4 s, and a 9 s slow answer no longer cost both boards.
- **Fix of 2026-11-20 11:58 (UTC+9) in v1.5.0:** no reconnect for a quiet board while the opponent thinks (the earlier 1.5.0 reconnected after 2 to 4 s of quiet near the end of a game). It reconnects only when the stream is silent for 15 s, its own move is not echoed within 2 s, or the opponent's whole clock has passed with no news of that match.
- **No agent_poll (v1.5.0):** the connector never calls `agent_poll`. The state comes only from the event stream; when an update looks missed it reconnects the stream, at most 5 times in 10 minutes. Engine details go with `agent_environment`. **`agent_poll` is removed on 2026-11-22 00:00 (UTC+9; 2026-11-21 15:00 UTC): from then on older connectors (1.4.x and the first 1.5.0 of 2026-11-20) cannot play at all, so update to this version.**
- **State from the stream (v1.4.10):** the event stream is opened with `?state=1`. The server sends the current state as soon as the stream connects or reconnects, and games against people and invitations come with your own view, so the connector no longer polls for them. The safety poll during a game is now 30 s instead of 10 s; a board waiting for the opponent is checked only after 10 s without news of that match (on a 1-minute clock; under 30 s left as in v1.4.9), and the 1 s echo check stays.

Full step-by-step guide in 23 languages: [FlipSight bot API docs](https://othelloclub.com/en/flipsight/lab/api)

## How bot games work on FlipSight

- A bot game is **two boards played at the same time** from the same starting position of 12 randomly placed discs. Your bot plays Black on one board and White on the other, so both bots get the same position once.
- The discs from both boards are added up, and the bot with more wins. Each bot has 1 minute per board.
- **Automatic games**: while your connector is running, FlipSight finds a bot with a similar rating and starts a game.
- **Direct challenges** from the bot page or the API.
- **Bot rating**: one bot game counts as one game in the standard rating.
- **Variants**: Anti, 10x10, Octagon and classic Reversi through direct challenges, if your engine knows the rules.

## Quick start

1. Sign in to FlipSight, open the [bot page](https://othelloclub.com/en/flipsight/lab) and press **Register my bot**.
2. Copy the **connection token**. It is shown only once and works as your bot's password.
3. Download this repository (Code → Download ZIP, or the latest release) and open a terminal in the folder.
4. Put the token into the `FS_BOT_TOKEN` environment variable.

   macOS / Linux:
   ```sh
   printf 'Bot token: '; read -r -s FS_BOT_TOKEN; printf '\n'; export FS_BOT_TOKEN
   ```
   Windows PowerShell:
   ```powershell
   $env:FS_BOT_TOKEN = [Net.NetworkCredential]::new('', (Read-Host 'Bot token' -AsSecureString)).Password
   ```
5. Start the connector with the practice engine to check the connection.

   macOS / Linux:
   ```sh
   python3 bot_match_client.py --engine-threads 1 --engine 'python3 bot_match_example_engine.py'
   ```
   Windows:
   ```powershell
   py bot_match_client.py --engine-threads 1 --engine 'py bot_match_example_engine.py'
   ```
6. When your bot shows as online in the bot list, challenge another bot to test your first game. Then switch to your own engine.

Games with the practice engine are not rated and are not picked for automatic games, so you can experiment freely.

## Connect your engine

| Engine | Option | Example |
| --- | --- | --- |
| Edax | `--edax PATH` | `python3 bot_match_client.py --edax "edax/lEdax-x86-64"` |
| Egaroucid for Console | `--egaroucid PATH` | `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out"` |
| NBoard protocol engines (NTest and others) | `--nboard CMD` | `python3 bot_match_client.py --engine-threads 2 --nboard "path/to/engine"` |
| Your own engine | `--engine CMD` | `python3 bot_match_client.py --engine-threads 2 --engine "python3 my_adapter.py"` |

Edax and Egaroucid decide their own thinking time from the game clocks the connector sends before every move (Edax over xboard from v1.4.8). NBoard engines search with their own settings within `--max-think-seconds`. Edax, Egaroucid and NBoard connections play standard 8x8 games.

Add `--engine-threads N` to show how many threads your engine uses per game on your bot's record. With Edax and Egaroucid it also sets the engine's thread count (default 4 when the computer has more than 4 logical CPUs, otherwise 1). You can change the CPU name and threads later in bot management. With `--engine` the value is also sent as `engine_threads` in every request, so your engine can size its search to it. The connector thinks on as many games at once as your bot's max simultaneous games in bot management. The two boards of a bot match count as one game and think together, so a bot match runs one more engine; keep threads per game × (that number + 1) within your CPU threads.

With `--engine` or `--nboard`, omitting `--engine-threads` prompts for the actual threads per game before connecting. For background or unattended runs, specify the option in the command. The reported value must match the engine configuration; the connector cannot enforce it for custom engines.

When running the bundled example engine, the connector reports `example_engine=true` and its filename. New paired matches retain the engine name and example status from the start for history and room displays, even if the engine is changed later. `practice=true` alone does not identify an example engine.

The CPU model is detected once at startup. On Windows the connector reads the product name; an explicit `--cpu-name` takes precedence. Replace an older connector with the latest file and restart it to use the improved detection.

Add `--timing-log PATH` to append one line per move showing where the time went: receiving the move, waiting for a free search, the engine, and sending the move. It is off by default and sends nothing extra to FlipSight.

### Hash memory and pondering (v1.4.5)

Hash is shown after Threads on the bot record, in MB per engine (1 MB = 1,048,576 bytes), not total process RAM. Known standard Edax 4.6 settings (including the current folder's `edax.ini`) and the Egaroucid Console adapter's default `-hash 20` configuration are reported automatically; from v1.4.7 `--engine-hash-mb` also sets them (see below). Unsupported or unknown configurations are left unreported. For a custom build or engine, optionally add `--engine-hash-mb 256` with the actual configured capacity. For those engines this reports the value; it does not change engine memory. A reported value takes precedence; without one, the owner can enter it in bot settings. Existing commands still work.

한국어: 해시는 엔진 1개 기준 MB이며 스레드 다음에 표시됩니다. 연결기 값이 없으면 봇 설정에서 입력할 수 있습니다. 자체 엔진은 `--engine-hash-mb 256`으로 실제 용량을 보고할 수 있고 엔진 설정 자체는 바뀌지 않습니다.

日本語: ハッシュ容量はエンジン1個あたりのMBで、スレッドの次に表示します。接続プログラムの値がなければボット設定で手入力できます。自作エンジンは `--engine-hash-mb 256` で容量を報告できます。エンジンのメモリ設定自体は変更しません。

Pondering is shown after Hash. The Edax and Egaroucid adapters report Off unless `--engine-ponder on` is given (Egaroucid from v1.4.7, Edax from v1.4.8, below). For custom engines, `--engine-ponder on` or `--engine-ponder off` optionally reports the actual setting without changing it. If unreported, select the value in bot settings; reported values take precedence. Website values are informational and do not reconfigure the engine.

한국어: 폰더는 해시 다음에 표시하며, 보고값이 없으면 봇 설정에서 켜짐/꺼짐을 선택합니다. `--engine-ponder on/off`는 자체 엔진의 실제 설정을 보고할 뿐 동작을 바꾸지 않습니다.

日本語: 先読みはハッシュの次に表示し、未報告ならボット設定でオン/オフを選べます。`--engine-ponder on/off` は自作エンジンの実際の設定を報告するだけで、動作を変更しません。

### Engine hash and pondering settings (v1.4.7)

With `--edax` or `--egaroucid`, `--engine-hash-mb MB` now sets the engine's hash table to the largest power-of-two size not above MB (Edax `-h`, Egaroucid `-hash`) and reports that size, for example Edax 900 → 864 MB and Egaroucid 600 → 512 MB. Without it, Edax keeps `edax.ini` and Egaroucid uses 32 MB as before. Each engine has its own table, so a bot match playing two games at once uses twice as much.

`--engine-ponder on` with `--egaroucid` starts Egaroucid with `-ponder`: it keeps searching during the opponent's time, uses CPU then, and reports On. From v1.4.8 it also works with `--edax` (see "Edax over xboard" below); with `--edax-protocol nboard` Edax does not ponder and the option is rejected. For custom engines both options still only report the setting.

```
python3 bot_match_client.py --engine-threads 2 --engine-hash-mb 256 --engine-ponder on --egaroucid "egaroucid/bin/Egaroucid_for_Console.out"
```

한국어: `--edax`, `--egaroucid`에서 `--engine-hash-mb`는 그 값 이하에서 가장 큰 2의 거듭제곱 크기로 엔진 해시를 실제로 설정하고 그 크기를 보고합니다. `--engine-ponder on`은 Egaroucid를 `-ponder`로 실행합니다(상대 차례에도 CPU 사용). v1.4.8부터는 `--edax`도 폰더를 켤 수 있습니다(`--edax-protocol nboard`는 제외).

日本語: `--edax` と `--egaroucid` では `--engine-hash-mb` がその値以下で最大の2の累乗の大きさにエンジンのハッシュを実際に設定し、その大きさを報告します。`--engine-ponder on` は Egaroucid を `-ponder` で起動します(相手の手番中もCPUを使用)。v1.4.8からは `--edax` でも先読みをオンにできます(`--edax-protocol nboard` を除く)。

### Engine-reported settings (v1.4.6)

A JSON engine can include `"environment":{"engine_threads":1,"hash_mb":32,"ponder":true}` in a normal move reply. The connector validates only these allocation fields and forwards them in the existing safety poll, using a locked snapshot. This supports FlipMind's Python adapters without another API or engine query. Legacy engines that return only `move` are unchanged. Restart an older connector and adapter to use response metadata.

### Edax over xboard (v1.4.8)

`--edax` now starts Edax with `-xboard`, one process per board. Before every search the connector sends both site clocks (`time` and `otim`, keeping 0.75 s back for network delay), and Edax decides how long to think from its own clock. After the opening position is set, the opponent's moves are sent as `usermove`, so Edax keeps its game and hash table through the board; a position that does not follow from its game is set again. With `--engine-ponder on` Edax keeps searching during the opponent's time (`hard`) and reports Ponder On. `--max-think-seconds` still caps each move: at the cap the connector sends `?` and Edax plays its best move so far at once. `--edax-protocol nboard` keeps the connection used up to v1.4.7 (the connector sets each move's time, no pondering).

```
python3 bot_match_client.py --engine-threads 2 --engine-ponder on --edax "edax/lEdax-x86-64"
```

한국어: v1.4.8부터 `--edax`는 Edax를 xboard 방식으로 실행합니다. 매 수 전에 양쪽 남은 시간을 보내고 생각할 시간은 Edax가 정하며, 판 안에서는 기보와 해시를 이어 씁니다. `--engine-ponder on`이면 상대 차례에도 탐색합니다. 예전 연결은 `--edax-protocol nboard`입니다.

日本語: v1.4.8から `--edax` は Edax を xboard 方式で起動します。毎手の前に双方の残り時間を送り、考える時間は Edax が決め、局の途中は棋譜とハッシュを引き継ぎます。`--engine-ponder on` なら相手の手番中も探索します。従来の接続は `--edax-protocol nboard` です。

### Server closing notice (v1.5.4)

v1.5.4 reads the `closing` line the server sends just before it closes the stream. After an `overflow` of unsent lines or a `server_shutdown` restart, it connects again at once and gets the state again. `replaced` means another connector opened a stream with the same bot token, so it logs a warning and connects again after 30 to 60 s. From the third time within 10 minutes the wait doubles each time, up to 15 minutes. Run each bot token in one place only.

한국어: v1.5.4 는 서버가 스트림을 닫기 직전에 보내는 `closing` 줄을 읽습니다. 까닭이 줄 넘침인 `overflow` 나 서버 재시작인 `server_shutdown` 이면 바로 다시 연결해 상태를 새로 받습니다. `replaced` 는 같은 봇 토큰으로 다른 연결기가 스트림을 열었다는 뜻이라, 경고를 남기고 30~60초 뒤에 다시 연결합니다. 10분 안에 세 번째부터는 기다림이 두 배씩 늘어 최대 15분이 됩니다. 봇 토큰 하나는 한 곳에서만 돌리세요.

日本語: v1.5.4 はサーバーがストリームを閉じる直前に送る `closing` 行を読みます。理由が送れなかった行があふれた `overflow` か、サーバー再起動の `server_shutdown` なら、すぐに接続し直して状態を受け取り直します。`replaced` は同じボットトークンで別の接続プログラムがストリームを開いたという意味なので、警告を残して30〜60秒後に接続し直します。10分以内の3回目からは待ち時間が毎回2倍になり、最大15分になります。ボットトークン1つは1か所だけで動かしてください。

### Sending your evaluation (v1.5.3)

From v1.5.3 the connector sends your engine's evaluation with each move in bot matches; it shows as a chip and a graph in the game room and stays in the game record. `--edax`, `--egaroucid` and NBoard engines send the engine's own score by themselves once you update to v1.5.3. A custom JSON adapter adds `"eval"` to its move reply (after the move, in discs from its own side). The range is up to the playable squares (64 on 8x8, 100 on 10x10, 88 on octagon); without a value, out of range or in games against people, only the move is sent.

한국어: v1.5.3 부터 연결기는 엔진의 평가치를 봇끼리 대국의 수와 함께 보내고, 게임방 칩과 그래프, 대국 기록에 남습니다. `--edax`, `--egaroucid`, NBoard 엔진은 v1.5.3 으로 업데이트하면 엔진이 낸 점수를 저절로 보냅니다. 직접 만든 JSON 어댑터는 수 답에 `"eval"`(둔 뒤의 내 쪽 기준 돌 단위)을 넣습니다. 범위는 둘 수 있는 칸 수(8x8 64, 10x10 100, 팔각 88)까지이고, 값이 없거나 범위 밖이거나 사람과의 대국이면 수만 보냅니다.

日本語: v1.5.3 から接続プログラムはエンジンの評価値をボット同士の対局の手と一緒に送り、対局画面のチップとグラフ、対局記録に残します。`--edax`、`--egaroucid`、NBoard エンジンは v1.5.3 に更新すれば、エンジン自身のスコアを自動で送ります。自作の JSON アダプターは着手の返答に `"eval"`（打った後の自分側から見た石数）を入れます。範囲は打てるマスの数（8x8 は 64、10x10 は 100、八角は 88）までで、値がない場合、範囲外の場合、人との対局では手だけを送ります。

### Saving game records (v1.5.2)

`--save-games DIR` keeps every finished game in `DIR/games-YYYY-MM.ndjson` (one file per month), one game per line. Each line is the server's game record (JSON, `format` 1), the same object as the `record` of a finished match event and each line of `agent_games`: game ID, time, opponent name and ID, game type, colors, result (`void` for a technical void), discs, finish reason, ratings before and after, the opening, both boards' moves (`f5d6…`, `pa` for a pass, plus square numbers) and the time and remaining clock of every move. Games that ended while the connector was stopped or the stream was down are filled with `agent_games` on reconnecting, from the newest saved game (an empty folder starts now). A game that changes later (a technical void) gets one more line: the last line with the same `kind` and `id` is the current one. Filling uses its own HTTPS connection, so moves are not delayed. It is off by default: nothing is saved without the option. A game takes about 4 KB (about 6 KB on 10×10), so 1,400 games a day come to about 6 MB a day and about 180 MB a month; delete old month files yourself when you no longer need them. Optional update; the fields are described in the "Game records" section of the bot API docs.

한국어: v1.5.2 는 `--save-games 폴더` 를 주면 끝난 대국을 모두 `games-YYYY-MM.ndjson` 에 줄마다 한 판(서버의 대국 기록, `format` 1)으로 남깁니다. 끊긴 사이 끝난 대국은 다시 붙을 때 `agent_games` 로 채우고, 나중에 바뀐 판(기술 무효)은 한 줄 더 씁니다(같은 `kind`, `id` 의 마지막 줄이 맞는 값). 기본은 꺼짐이고 이 옵션을 켰을 때만 저장합니다. 한 판은 약 4KB(10×10 은 약 6KB)라 하루 1,400판이면 하루 약 6MB, 한 달 약 180MB 입니다. 필요 없어진 지난 달 파일은 직접 지우세요. 선택 업데이트입니다.

日本語: v1.5.2 では `--save-games フォルダ` を指定すると、終わった対局をすべて `games-YYYY-MM.ndjson` に1行1局（サーバーの対局記録、`format` 1）で残します。切れていた間に終わった対局は再接続のときに `agent_games` で埋め、後で変わった対局（技術的無効）はもう1行書きます（同じ `kind`、`id` の最後の行が正しい値）。既定ではオフで、このオプションを付けたときだけ保存します。1局は約4KB（10×10は約6KB）なので、1日1,400局なら1日約6MB、1か月約180MBになります。不要になった古い月のファイルはご自分で削除してください。任意のアップデートです。

### Network blips (v1.5.1)

When the network path of the connector PC changes or stalls for a few seconds (a mobile hotspot, Wi-Fi roaming), the open connections stop and both boards of a bot match can lose on time. v1.5.1 limits each move POST to a third of that board's clock (1.5 to 8 s); after a timeout it opens the HTTPS connection and the event stream again at once. A move refused with HTTP 409 (the server moved on, often because the first try arrived after all) is dropped on its own and the other board goes on; the stream is reconnected only if that match has no news within 1 s. Reconnects asked for are merged: none while one is under way or within 3 s of the last, so a burst of errors no longer uses up the 5-in-10-minutes budget. In a test against a local server where the network path stopped for 6 s, the first move after it came 0.3 s later (3.4 s with v1.5.0); with a 9 s slow answer, v1.5.0 lost both boards on time and v1.5.1 none. Recommended update.

한국어: v1.5.1 은 연결기 PC 의 네트워크가 몇 초 끊기거나 바뀔 때(휴대폰 테더링, Wi-Fi 이동) 빨리 회복합니다. 착수 요청은 그 판 남은 시계의 1/3(1.5~8초)만 기다리고, 시간이 지나면 HTTPS 연결과 스트림을 바로 새로 엽니다. HTTP 409 로 거절된 수(첫 시도가 이미 들어간 경우가 많음)는 그 수만 버리고 다른 판을 계속 두며, 재연결 요청은 묶습니다(진행 중이거나 3초 안이면 다시 하지 않음). 권장 업데이트입니다.

日本語: v1.5.1 は接続PCのネットワークが数秒切れたり変わったりしたとき（スマートフォンのテザリング、Wi-Fi の移動）に早く回復します。着手の送信はその盤の残り時間の1/3（1.5〜8秒）だけ待ち、時間切れになると HTTPS 接続とストリームをすぐに開き直します。HTTP 409 で断られた手（最初の送信が届いていたことが多い）はその手だけを捨てて他の盤を続け、再接続の要求はまとめます（再接続中か3秒以内なら行わない）。推奨アップデートです。

### No agent_poll (v1.5.0)

In FlipSight bot API v2, the state comes only from the event stream (your bot and ongoing games once when you connect, then one small event per changed game), moves and accepting stay plain POSTs, and if you suspect you missed something you reconnect the stream. **`agent_poll` is removed on 2026-11-22 00:00 (UTC+9; 2026-11-21 15:00 UTC) and returns HTTP 410 from then on, so the official connectors before this version (1.4.x, and the first 1.5.0 published on 2026-11-20 at 10:34 UTC+9, which still polled once a minute) stop working at that time.** From 2026-11-21 23:40 (UTC+9) registered bots that call `agent_poll` or reconnect more than 10 times in 10 minutes are left out of automatic matching. Bot management on FlipLab shows your bot's status.

v1.5.0 never calls `agent_poll`: a move that does not come back on the stream within 2 s and a stream silent for 15 s each lead to a reconnect, at most 5 voluntary reconnects in 10 minutes. A board waiting on the opponent leads to a reconnect only once the opponent's whole clock plus 5 s has passed with no news of that match (once per match). Fixed on 2026-11-20 at 11:58 (UTC+9): the 1.5.0 published at 11:33 also reconnected after 10 s of quiet on a 1-minute clock, and after 2 to 4 s when its own clock was low, several times a game. The server also re-sends a missed update within a few seconds. Engine details (CPU, engine, threads, practice) are reported with `agent_environment` at start and when they change. `--no-stream` is gone. In a test against a local server, reaction time and recovery matched the first 1.5.0 (a lost opponent move was played 3.3 s later with the server re-send, about 10 s later without it) with no polls.

한국어: v1.5.0 은 `agent_poll` 을 쓰지 않습니다. 상태는 스트림으로만 받고, 내 수가 2초 안에 돌아오지 않거나 스트림이 15초 동안 조용하면 스트림을 다시 연결합니다(10분에 5번까지). 상대 차례인 판은 상대 시계를 다 쓰고도 5초가 지나도록 소식이 없을 때만 한 번 다시 연결합니다. 2026-11-20 11:58 (UTC+9) 수정: 상대 차례 조용한 판 재연결 없앰(11:33 에 낸 1.5.0 은 남은 시간이 적으면 2~4초만 조용해도 다시 연결했습니다). 엔진 정보는 `agent_environment` 로 시작할 때와 바뀔 때만 보냅니다. `--no-stream` 은 없앴습니다. **`agent_poll` 은 2026-11-22 00:00 (UTC+9) 에 없어지므로 그 전 공식 연결기(1.4.x, 2026-11-20 10:34 에 낸 첫 1.5.0)는 그때부터 대국을 둘 수 없습니다.** 2026-11-21 23:40 (UTC+9) 부터 `agent_poll` 을 쓰거나 재연결이 10분에 10번을 넘는 등록봇은 자동 매칭에서 빠집니다.

日本語: v1.5.0 は `agent_poll` を使いません。状態はストリームだけで受け取り、自分の手が2秒以内に返ってこないとき、ストリームが15秒間静かなときはストリームを再接続します（10分に5回まで）。相手の手番の盤は、相手の持ち時間を使い切って5秒過ぎても知らせがないときだけ1回再接続します。2026-11-20 11:58 (UTC+9) 修正：相手の手番で静かな盤での再接続をなくしました（11:33 に出した 1.5.0 は残り時間が少ないと2〜4秒静かなだけで再接続していました）。エンジンの情報は `agent_environment` で起動時と変わったときだけ送ります。`--no-stream` はなくなりました。**`agent_poll` は 2026-11-22 00:00 (UTC+9) に廃止されるため、それ以前の公式接続プログラム（1.4.x、2026-11-20 10:34 に出した最初の 1.5.0）はその時点から対局できません。** 2026-11-21 23:40 (UTC+9) から、`agent_poll` を使うか再接続が10分に10回を超える登録ボットは自動マッチングから外れます。

### State from the stream (v1.4.10)

The connector opens the event stream with `?state=1`. Right after `hello` the server sends a `state` event with the same content as an `agent_poll` answer, and every `match` event (bot matches, invitations and games against people) carries your own view of the match (`"view": "agent"`), so connecting, reconnecting, accepting and games against people need no poll. `agent_poll` is now only the safety net: once at start (it reports your environment), every 30 s during a game (was 10 s) and every 60 s while idle. On a board waiting for the opponent, the connector counts the time since the last news of that match as used on your clock and checks after 10 s on a 1-minute clock (under 30 s left, every quarter of the remaining time as in v1.4.9). The 1 s echo check and the stuck-stream detection stay. A server without `?state=1` support is handled as before. In a test against a local server, a bot match, a game against a person and one reconnect took 7 polls in 140 s instead of 40.

한국어: v1.4.10부터 스트림을 `?state=1`로 엽니다. 연결 직후 서버가 `agent_poll`과 같은 상태를 보내고, 봇끼리 대국, 신청, 사람과의 대국이 모두 내 쪽 값(`"view": "agent"`)으로 와서 연결, 재연결, 수락, 사람과의 대국에 조회가 필요 없습니다. 안전 조회는 시작할 때 한 번, 대국 중 30초(예전 10초), 쉴 때 60초입니다. 상대 차례인 판은 그 대국 소식이 없던 시간을 내 시계에서 쓴 것으로 보고 1분 시계에서 10초 뒤 확인합니다(남은 30초 아래는 v1.4.9와 같음).

日本語: v1.4.10からストリームを `?state=1` で開きます。接続直後にサーバーが `agent_poll` と同じ状態を送り、ボット同士の対局、申し込み、人との対局がすべて自分側の値(`"view": "agent"`)で届くため、接続、再接続、承諾、人との対局で問い合わせが要りません。安全な問い合わせは開始時に1回、対局中30秒(従来10秒)、待機中60秒です。相手の手番の盤は、その対局の知らせがない時間を自分の持ち時間から使ったものとみなし、1分の持ち時間なら10秒後に確認します(残り30秒未満はv1.4.9と同じ)。

### Stream watch and safety polls (v1.4.9)

The server pings the event stream every 10 s. If the stream stays connected but no line (event or ping) arrives for 15 s, the connector closes it, connects again and polls every second until it is back. While the stream is up it adds two safety polls: once when its own move has not come back on the stream within 1 s, and, on a board waiting for the opponent with less than 30 s on your clock (counting the time since the last state as used), every quarter of the remaining time (1 to 10 s, at most 60 times per match). With a healthy stream nothing changes: moves still come from the stream and the regular safety poll stays at 10 s. Stream drops, reconnects, silences, events more than 2 s late and turns skipped for lack of time each get one log line with the local time. No new options; older connectors keep working.

한국어: v1.4.9부터 스트림이 연결된 채 15초 동안 아무것도 오지 않으면 다시 연결하고 그동안 1초마다 조회합니다. 내가 둔 수가 1초 안에 스트림으로 돌아오지 않을 때, 그리고 상대 차례인 판에서 내 남은 시간이 30초 아래일 때(남은 시간의 4분의 1, 1~10초, 대국마다 60번까지) 더 조회합니다. 끊김과 재연결, 멈춤, 늦은 이벤트는 시각과 함께 로그에 남습니다.

日本語: v1.4.9から、ストリームがつながったまま15秒間何も届かなければ接続し直し、その間は1秒ごとに問い合わせます。自分の着手が1秒以内にストリームで返ってこないとき、また相手の手番の盤で自分の残り時間が30秒を切ったとき(残り時間の4分の1、1〜10秒、1対局60回まで)にも問い合わせます。切断、再接続、無通信、遅れたイベントは時刻付きでログに残ります。

### Engine level and time

- **Edax level:** add `--nboard-depth N` (1 to 60) to `--edax`. Edax uses N as its level, so `--nboard-depth 20` is L20; without it the level is 60. Edax plays its best move so far when it reaches the level or the time for the move runs out, whichever comes first. Over xboard the level is sent as `sd N`.
  `python3 bot_match_client.py --edax "edax/lEdax-x86-64" --nboard-depth 20`
- **Egaroucid level:** add `--egaroucid-level N` (1 to 60) to `--egaroucid`. Egaroucid starts with `-level N` and gets no remaining time (`settime`); 0 or omitted keeps time mode, which follows the game clock. Level mode cannot stop a search early: if a move goes past `--max-think-seconds`, the connector stops the engine and plays a legal fallback move. Choose a level that finishes well within the game clock.
  `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out" --egaroucid-level 10`
- **Time:** `--max-think-seconds S` is the per-move cap (default 10, maximum 300, 0 = no per-move cap). The game clock itself is set by the site and the connector cannot change it: bot pairs and automatic bot matches give each color 60 seconds per board, and a person who challenges your bot directly picks the clock. With Edax (v1.4.8) Edax sets the time for each move from its own clock within S, and at S the connector makes it play its best move so far at once. With `--edax-protocol nboard` the time for a move is the smaller of S and (remaining clock − 0.75 s) ÷ (empty squares ÷ 2) × 1.5.

### Your own engine: JSON lines

Your engine stays running for the whole game. For each move it reads one JSON line and writes one JSON line.

Input (connector → engine):
```json
{"board":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,1,0,0,0,0,0,0,1,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],"color":1,"legal_moves":[19,26,37,44],"game_mode":"std","board_size":8,"remaining_ms":60000,"budget_ms":10000}
```
`board` cells are 0 empty, 1 Black, 2 White, row by row from a1. `color` is the side to move.

Output (engine → connector), one line:
```json
{"move":19}
```
`move` is a square index (`d3` = 19 on 8x8) or a coordinate such as `"d3"`. Send `-1` to pass when there is no legal move. Write nothing else to standard output.

Optional (v1.5.3): `{"move":19,"eval":1.5}` adds your evaluation after the move, in discs from your side (+ means you are ahead). The connector sends it only in bot matches and only when it is a number within the playable squares (64 on 8x8, 100 on 10x10, 88 on octagon).

The full field list, adapter examples for engines written in any language, and fixes for common problems are in the [bot API docs](https://othelloclub.com/en/flipsight/lab/api).

## Fair play

Engines may be used only through registered bots. Using engine help in games you play from your own account is not allowed ([Fair Play and Account Policy](https://othelloclub.com/en/notice_en/6)). Keep your token private; if it leaks, issue a new one in bot management.

## License

MIT License. See [LICENSE](LICENSE).

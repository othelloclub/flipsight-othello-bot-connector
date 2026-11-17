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

Full step-by-step guide in 23 languages: [FlipSight bot API docs](https://othelloclub.com/en/flipsight/bot/api)

## How bot games work on FlipSight

- A bot game is **two boards played at the same time** from the same starting position of 12 randomly placed discs. Your bot plays Black on one board and White on the other, so both bots get the same position once.
- The discs from both boards are added up, and the bot with more wins. Each bot has 1 minute per board.
- **Automatic games**: while your connector is running, FlipSight finds a bot with a similar rating and starts a game.
- **Direct challenges** from the bot page or the API.
- **Bot rating**: one bot game counts as one game in the standard rating.
- **Variants**: Anti, 10x10, Octagon and classic Reversi through direct challenges, if your engine knows the rules.

## Quick start

1. Sign in to FlipSight, open the [bot page](https://othelloclub.com/en/flipsight/bot) and press **Register my bot**.
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

The full field list, adapter examples for engines written in any language, and fixes for common problems are in the [bot API docs](https://othelloclub.com/en/flipsight/bot/api).

## Fair play

Engines may be used only through registered bots. Using engine help in games you play from your own account is not allowed ([Fair Play and Account Policy](https://othelloclub.com/en/notice_en/6)). Keep your token private; if it leaks, issue a new one in bot management.

## License

MIT License. See [LICENSE](LICENSE).

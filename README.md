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
   python3 bot_match_client.py --engine 'python3 bot_match_example_engine.py'
   ```
   Windows:
   ```powershell
   py bot_match_client.py --engine 'py bot_match_example_engine.py'
   ```
6. When your bot shows as online in the bot list, challenge another bot to test your first game. Then switch to your own engine.

Games with the practice engine are not rated and are not picked for automatic games, so you can experiment freely.

## Connect your engine

| Engine | Option | Example |
| --- | --- | --- |
| Edax | `--edax PATH` | `python3 bot_match_client.py --edax "edax/lEdax-x86-64"` |
| Egaroucid for Console | `--egaroucid PATH` | `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out"` |
| NBoard protocol engines (NTest and others) | `--nboard CMD` | `python3 bot_match_client.py --nboard "path/to/engine"` |
| Your own engine | `--engine CMD` | `python3 bot_match_client.py --engine "python3 my_adapter.py"` |

The connector allocates thinking time for each move from the remaining game clock. Edax, Egaroucid and NBoard connections play standard 8x8 games.

Add `--engine-threads N` to show how many threads your engine uses per game on your bot's record. With Edax and Egaroucid it also sets the engine's thread count (default 4 when the computer has more than 4 logical CPUs, otherwise 1). You can change the CPU name and threads later in bot management. With `--engine` the value is also sent as `engine_threads` in every request, so your engine can size its search to it. The connector thinks on as many games at once as your bot's max simultaneous games in bot management. The two boards of a bot match count as one game and think together, so a bot match runs one more engine; keep threads per game × (that number + 1) within your CPU threads.

When running the bundled example engine, the connector reports `example_engine=true` and its filename. New paired matches retain the engine name and example status from the start for history and room displays, even if the engine is changed later. `practice=true` alone does not identify an example engine.

The CPU model is detected once at startup. On Windows the connector reads the product name; an explicit `--cpu-name` takes precedence. Replace an older connector with the latest file and restart it to use the improved detection.

Add `--timing-log PATH` to append one line per move showing where the time went: receiving the move, waiting for a free search, the engine, and sending the move. It is off by default and sends nothing extra to FlipSight.

Direct challenges from other bots are accepted automatically. Add `--decline-bot-challenges` to decline them all (the site then tells challengers "bot does not accept bot challenges" right away), or `--block-bot NAME` (repeatable, name or number) to decline only certain bots. Automatic games and games against people are not affected.

### Engine level and time

- **Edax level:** add `--nboard-depth N` (1 to 60) to `--edax`. Edax uses N as its level, so `--nboard-depth 20` is L20; without it the level is 60. Edax plays its best move so far when it reaches the level or the time for the move runs out, whichever comes first.
  `python3 bot_match_client.py --edax "edax/lEdax-x86-64" --nboard-depth 20`
- **Egaroucid level:** add `--egaroucid-level N` (1 to 60) to `--egaroucid`. Egaroucid starts with `-level N` and gets no remaining time (`settime`); 0 or omitted keeps time mode, which follows the game clock. Level mode cannot stop a search early: if a move goes past `--max-think-seconds`, the connector stops the engine and plays a legal fallback move. Choose a level that finishes well within the game clock.
  `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out" --egaroucid-level 10`
- **Time:** `--max-think-seconds S` is the per-move cap (default 10, maximum 300, 0 = no per-move cap). The game clock itself is set by the site and the connector cannot change it: bot pairs and automatic bot matches give each color 60 seconds per board, and a person who challenges your bot directly picks the clock. With Edax the time for a move is the smaller of S and (remaining clock − 0.75 s) ÷ (empty squares ÷ 2) × 1.5, so on a tight clock Edax may play below the chosen level.

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

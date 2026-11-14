# FlipSight Othello Bot Connector

**English** | [日本語](README.ja.md) | [한국어](README.ko.md)

Run your own **Othello / Reversi engine as a bot on [FlipSight](https://othelloclub.com/en/flipsight/)**.
Your bot plays rated bot games against other members' bots and the FlipSight bots, and people can challenge it too.

This connector runs on your computer, talks to your engine, and plays your bot's games on FlipSight.
It works with **Edax**, **Egaroucid**, any engine that speaks the **NBoard protocol** (such as NTest), or **your own AI** through a simple JSON line protocol.

- Python 3.9+, standard library only. No installation of extra packages.
- Windows, macOS and Linux.
- Your engine runs on your own machine. Only moves are sent to FlipSight.

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

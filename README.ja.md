# FlipSight オセロ ボット接続プログラム

[English](README.md) | **日本語** | [한국어](README.ko.md)

自作の**オセロ / リバーシ エンジンを [FlipSight](https://othelloclub.com/ja/flipsight/) のボットとして動かせます**。
あなたのボットは、他の会員のボットや FlipSight ボットとレーティング対局を行い、人からの対局申し込みも受けられます。

この接続プログラムはあなたのパソコンで動き、エンジンとやり取りしながら FlipSight でボットの対局を進めます。
**Edax**、**Egaroucid**、**NBoard 方式**に対応したエンジン(NTest など)、そして簡単な JSON 1行のやり取りで**自作の AI** にも対応しています。

- Python 3.9 以上、標準ライブラリだけで動きます。追加パッケージのインストールは不要です。
- Windows、macOS、Linux に対応しています。
- エンジンは自分のパソコンで動きます。FlipSight に送られるのは打つ手だけです。

初めての方向けの詳しい手順(23言語): [FlipSight ボット API 文書](https://othelloclub.com/ja/flipsight/bot/api)

## FlipSight のボット対局

- ボット対局は、ランダムに配置された12個の石からなる同じ開始局面で**2局を同時に**打ちます。自分のボットは一方で黒、もう一方で白を持つので、両方のボットが同じ局面を1回ずつ経験します。
- 2局の石数を合計し、多いほうが勝ちです。各局の持ち時間はボットごとに1分です。
- **自動対局**: 接続プログラムを起動しておくと、FlipSight がレーティングの近いボットを探して対局を始めます。
- **直接申し込み**: ボットページや API から、好きなボットにすぐ対局を申し込めます。
- **ボットのレーティング**: ボット対局1回が通常のレーティングの1試合になります。
- **変形ゲーム**: エンジンがルールに対応していれば、アンチ、10x10、八角、クラシックリバーシも直接申し込みで打てます。

## はじめ方

1. FlipSight にログインして[ボットページ](https://othelloclub.com/ja/flipsight/bot)を開き、**自分のボットを登録**を押します。
2. **接続トークン**をコピーします。一度だけ表示される、ボット専用のパスワードです。
3. このリポジトリをダウンロードし(Code → Download ZIP、または最新のリリース)、そのフォルダーで端末を開きます。
4. トークンを環境変数 `FS_BOT_TOKEN` に入れます。

   macOS / Linux:
   ```sh
   printf 'Bot token: '; read -r -s FS_BOT_TOKEN; printf '\n'; export FS_BOT_TOKEN
   ```
   Windows PowerShell:
   ```powershell
   $env:FS_BOT_TOKEN = [Net.NetworkCredential]::new('', (Read-Host 'Bot token' -AsSecureString)).Password
   ```
5. まず練習用エンジンで接続プログラムを起動し、つながるか確かめます。

   macOS / Linux:
   ```sh
   python3 bot_match_client.py --engine 'python3 bot_match_example_engine.py'
   ```
   Windows:
   ```powershell
   py bot_match_client.py --engine 'py bot_match_example_engine.py'
   ```
6. ボット一覧で自分のボットがオンラインになったら、他のボットに直接申し込んで最初の対局を試してみてください。うまく動いたら、自分のエンジンに切り替えます。

練習用エンジンで動かしている間は自動対局が組まれず、レーティングにも反映されないので、気軽に試せます。

## エンジンのつなぎ方

| エンジン | オプション | 例 |
| --- | --- | --- |
| Edax | `--edax PATH` | `python3 bot_match_client.py --edax "edax/lEdax-x86-64"` |
| Egaroucid for Console | `--egaroucid PATH` | `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out"` |
| NBoard 方式のエンジン(NTest など) | `--nboard CMD` | `python3 bot_match_client.py --nboard "path/to/engine"` |
| 自作エンジン | `--engine CMD` | `python3 bot_match_client.py --engine "python3 my_adapter.py"` |

接続プログラムが、残りの持ち時間から1手ごとの考慮時間を割り振ります。Edax、Egaroucid、NBoard 方式の接続は通常の 8x8 オセロだけに対応しています。

### 自作エンジン: JSON 1行のやり取り

エンジンは対局中ずっと起動したままにします。1手ごとに JSON を1行読み、JSON を1行書きます。

入力(接続プログラム → エンジン):
```json
{"board":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,1,0,0,0,0,0,0,1,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],"color":1,"legal_moves":[19,26,37,44],"game_mode":"std","board_size":8,"remaining_ms":60000,"budget_ms":10000}
```
`board` は a1 から行ごとに並んだマスで、0 が空き、1 が黒、2 が白です。`color` は手番の色です。

出力(エンジン → 接続プログラム)、1行:
```json
{"move":19}
```
`move` はマスの番号(8x8 では `d3` = 19)か、`"d3"` のような座標です。打てる手がないときは `-1` を送ってパスします。標準出力にはこの1行以外を書かないでください。

全項目の説明、どんな言語で書いたエンジンにも使えるアダプターの例、よくあるつまずきの直し方は [ボット API 文書](https://othelloclub.com/ja/flipsight/bot/api) にあります。

## フェアプレー

エンジンは登録したボットでのみ使えます。自分のアカウントで打つ対局でエンジンの助けを借りることは禁止です([フェアプレーおよびアカウント利用ポリシー](https://othelloclub.com/ja/notice_jp/6))。トークンは人に見せないでください。漏れたときはボット管理で新しく発行してください。

## ライセンス

MIT License。[LICENSE](LICENSE) を参照してください。

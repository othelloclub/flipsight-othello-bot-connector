# FlipSight オセロ ボット接続プログラム

[English](README.md) | **日本語** | [한국어](README.ko.md)

自作の**オセロ / リバーシ エンジンを [FlipSight](https://othelloclub.com/ja/flipsight/) のボットとして動かせます**。
あなたのボットは、他の会員のボットや FlipSight ボットとレーティング対局を行い、人からの対局申し込みも受けられます。

この接続プログラムはあなたのパソコンで動き、エンジンとやり取りしながら FlipSight でボットの対局を進めます。
**Edax**、**Egaroucid**、**NBoard 方式**に対応したエンジン(NTest など)、そして簡単な JSON 1行のやり取りで**自作の AI** にも対応しています。

- Python 3.9 以上、標準ライブラリだけで動きます。追加パッケージのインストールは不要です。
- Windows、macOS、Linux に対応しています。
- エンジンは自分のパソコンで動きます。FlipSight に送られるのは打つ手だけです。
- **速く軽く（v1.4.0）：** ボット対局も人同士の対局と同じく、相手が打つとサーバーがその手をすぐ届け、ボットは受け取ってすぐ打ちます。サーバーへの問い合わせは取りこぼしを拾う安全網だけです。HTTPS 接続を1本使い続け、サーバーが再起動しても止まらずにつなぎ直します。当方のテストでは1手あたりの通信時間が286msから51msに減りました。

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

`--engine-threads N` を付けると、エンジンが1局で使うスレッド数がボットのユーザー記録に表示されます。Edax、Egaroucid ではこの値でエンジンのスレッド数も決まります(既定は論理 CPU が4個より多ければ 4、それ以外は 1)。CPU 名とスレッド数は、あとからボット管理でも変更できます。 `--engine` では各リクエストに `engine_threads` としても入るので、エンジンはこの値に合わせて探索スレッドを決められます。接続プログラムはボット管理の同時対局数まで(最低 2)の対局を同時に考えるので、1局あたりのスレッド数 × 同時対局数が CPU のスレッド数を超えないようにしてください。

同梱のサンプルエンジンを使うと、接続プログラムは `example_engine=true` とファイル名を送信します。新しいボット対戦では開始時のエンジン名とサンプルかどうかを保存し、履歴と対局室に表示します。あとでエンジンを変更しても過去の表示は変わりません。`practice=true` だけではサンプルエンジンとは判定しません。

CPU のモデル名は起動時に自動取得します。Windows では製品名を優先し、`--cpu-name` を指定した場合はその値を使います。古い接続プログラムを実行中の場合は、最新ファイルに置き換えて再起動すると新しい取得方法が適用されます。

`--timing-log パス` を付けると、1手ごとに時間の内訳（相手の手の受信、探索の空き待ち、エンジン、着手の送信）をファイルに1行ずつ残します。既定ではオフで、FlipSight に余分なものは送りません。

### エンジンのレベルと時間

- **Edaxのレベル:** `--edax` に `--nboard-depth N`（1～60）を付けます。EdaxはNをレベルとして使うので、`--nboard-depth 20` はL20です。省略すると60です。Edaxはレベルに達するか、その手の時間が尽きるか、早い方の時点までの最善手を打ちます。
  `python3 bot_match_client.py --edax "edax/lEdax-x86-64" --nboard-depth 20`
- **Egaroucidのレベル:** `--egaroucid` に `--egaroucid-level N`（1～60）を付けます。Egaroucidを `-level N` で起動し、残り時間（`settime`）を送りません。0または省略時は対局の持ち時間に従う時間モードです。レベルモードは探索を途中で止められないため、1手が `--max-think-seconds` を超えると、接続プログラムがエンジンを止めて合法手を1つ代わりに打ちます。持ち時間内に余裕を持って終わるレベルを選んでください。
  `python3 bot_match_client.py --egaroucid "egaroucid/bin/Egaroucid_for_Console.out" --egaroucid-level 10`
- **時間:** `--max-think-seconds S` は1手の上限です（既定10、最大300、0なら1手の上限なし）。対局の持ち時間はサイトが決め、接続プログラムでは変えられません。ボット同士の対局と自動ボット対戦は局ごと、色ごとに60秒で、人が直接申し込んだ対局は申し込んだ人が選びます。Edaxの1手の時間はSと（残り時間 − 0.75秒）÷（空きマス数 ÷ 2）× 1.5 の小さい方なので、時間が厳しいと指定したレベルより浅く打つことがあります。

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

これって何？

これは、restdb.io などのマネージドサービスに代わる、軽量で最適な選択肢です。API の利用制限や厳格な制約に悩まされているなら、このツールは柔軟で、自社でホストできるソリューションを提供します。

何故必要なのか

当方は別プロジェクトでSTSenというソフトウェアを開発しています。

ですが、運用開始翌日に利用していたサービスの利用制限にぶち当たってしまったため、システムが止まってしまいました。

STSen自体を改造してもよかったのですが、それはそれで別の不具合が発生する懸念がありました。

それなら使ってたサービスの代替ソフトウェアを作ればよくねと思いまして。

手元にAI君が・・・。

バックエンドにはMariaDB (SQLAlchemy + aiomysql 経由)を使用し、AIでバイブコーディンングしました。

故にこのリポジトリはAIで生成されたコードしかありません。

MITで公開してますが、国のルールによっては著作権が認められない場所もあるかと思いますが、その場合このソースコードはパブリックドメインとして扱います。
(勢いで作ったので別にMIT継承しなくてもいいです。私は怒りません。)

実行 (開発環境):

```bash
# 依存関係のインストール
python -m pip install -r requirements.txt
# 実行 (localhost:3306 で MariaDB が稼働していることを想定)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888
```

環境設定: `.env.example` を `.env` にコピーし、MariaDB の接続情報を設定してください。

Linux: クイックインストールと実行
 - `.env.example` を `.env` にコピーし、必要に応じて値を編集します。
 - 仮想環境を作成し、依存関係をインストールして、サーバーを実行します:

```bash
cd restdb.io-proxy
./run.sh
```

`run.sh` はワークスペース内に `.venv` を作成し、依存関係をインストールしてアプリを起動します（開発環境）。仮想環境を再作成せずに実行するには、以下を使用します:

```bash
cd restdb.io-proxy
./start.sh
```

Systemd (例)

`systemd/restdb-io-proxy.service` のテンプレートを使用して `/etc/systemd/system/restdb-io-proxy.service` にユニットを作成し、サービスを有効化して開始します:

```bash
sudo cp restdb.io-proxy/systemd/restdb-io-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now restdb-io-proxy.service
```

インストールパスに合わせて、ユニット内の `WorkingDirectory` と `EnvironmentFile` を調整してください。

Docker (リモートまたは再現可能なデプロイに推奨)

Docker Compose を使用して、MariaDB と phpMyAdmin と共にプロキシをビルドして起動します:

```bash
cd restdb.io-proxy
docker compose up --build -d
```

これにより `proxy` イメージがビルドされ、`mariadb`、`phpmyadmin`、および `proxy` サービスが開始されます。`.env` ファイルが存在する場合、`env_file` を介して `proxy` コンテナに読み込まれます。

環境変数

環境変数を使用してプロキシを設定できます。`.env.example` を `.env` にコピーして値を設定するか、`docker compose` に直接変数を渡します。

重要な変数 (詳細は `.env.example` を参照):
- `PROXY_API_KEY` - クライアントに `x-apikey` ヘッダーの提供を要求するためのオプションの API キー。
- `DATABASE_URL` - オプションの完全なデータベース URL (ホスト/ポート/ユーザー/パスワード/DB を上書きします)。
- `DB_USER` - MariaDB ユーザー名 (Compose を使用する場合のデフォルト: `restdb_user`)。
- `DB_PASSWORD` - MariaDB パスワード (`.env` または外部のシークレットストアで設定)。
- `DB_HOST` - MariaDB ホスト名 (Compose を使用する場合のデフォルト: `mariadb`)。
- `DB_PORT` - MariaDB ポート (デフォルト: `3306`)。
- `DB_NAME` - MariaDB データベース名 (デフォルト: `restdb_proxy`)。

例

`.env` ファイルの使用 (推奨):

```bash
cp .env.example .env
# .env を編集して PROXY_API_KEY またはデータベースの認証情報を設定
docker compose up --build -d
```

インラインで変数を渡す:

```bash
PROXY_API_KEY=secret docker compose up --build -d
```

phpMyAdmin (データベース管理用 GUI)

`docker compose up` を使用すると、phpMyAdmin が自動的に開始され、以下でアクセスできます:
**http://localhost:8081**

デフォルトの認証情報 (環境変数で上書きされていない場合、docker-compose.yml で設定):
- サーバー: `mariadb`
- ユーザー名: `root`
- パスワード: `PMA_PASSWORD` で設定

この GUI を使用して以下の操作が可能です:
- すべてのテーブルの閲覧 (queue と requests)
- データの手動作成、編集、削除
- 生の SQL クエリの実行
- データベースの操作の監視

ヘルスチェック

プロキシはデータベース接続を確認するための `/health` エンドポイントを提供します (API キー不要):

```bash
curl http://localhost:8888/health
```

レスポンス (成功):
```json
{"status":"ok","redis":"connected"}
```

レスポンス (エラー):
```json
{"status":"error","redis":"failed","error":"connection error details"}
```

このエンドポイントは、監視/アラートシステムで使用してください。
restdb.io API の互換性

プロキシは restdb.io REST API と高いレベルで互換性を実装しています。サポートされているエンドポイントと機能は以下の通りです:

**サポートされている HTTP メソッド**
- GET - ドキュメントの取得
- POST - ドキュメントの作成
- PUT - ドキュメント全体の置換
- PATCH - 部分的な更新
- DELETE - ドキュメントの削除

**コレクション**
- `/rest/queue` - queue コレクション用の汎用 REST エンドポイント
- `/rest/requests` - requests コレクション用の汎用 REST エンドポイント
- `/rest/config` - config コレクション用の汎用 REST エンドポイント (キー/値の設定)
- 後方互換性のためにレガシーエンドポイント `/queue`, `/requests` をサポート

**MongoDB 形式のクエリ** (`?q={}` パラメータ経由)
- `$eq` - 等しい
- `$gt` - より大きい
- `$lt` - より小さい
- `$gte` - 以上
- `$lte` - 以下
- `$in` - 配列に含まれる
- `$nin` - 配列に含まれない
- `$ne` - 等しくない

例:
```bash
curl -H "x-apikey: secret" \
  "http://localhost:8888/rest/queue?q={\"priority\":true}"
```

**ヘッダーオプション** (`?h={}` パラメータ経由)
- `$orderby` - フィールドのソート: `{"priority": 1, "id": -1}` (1=昇順, -1=降順)
- `$fields` - フィールドの選択: `{"videoId": 1, "priority": 1}`
- `$max` - 結果の制限: `{"$max": 10}`
- `$skip` - 結果のオフセット: `{"$skip": 5}`

例:
```bash
curl -H "x-apikey: secret" \
  "http://localhost:8888/rest/queue?h={\"$orderby\":{\"id\":-1},\"$max\":10}"
```

**メタデータ API**
- `GET /rest/_meta` - データベースのメタデータを取得
- `GET /rest/<collection>/_meta` - コレクションのメタデータを取得 (フィールドタイプ、ドキュメント数)

**バルク操作**
- `DELETE /rest/<collection>/*` - ID リストで削除 (本文: `["id1", "id2"]`)
- `DELETE /rest/<collection>/*?q={...}` - MongoDB 形式のクエリで削除

**API キー**
- `x-apikey` ヘッダー経由で API キーを渡す
- `PROXY_API_KEY` 環境変数を設定して認証を強制する

トラブルシューティング

**Docker Compose: "Can't connect to MySQL server on 'localhost'"**

このエラーは、MariaDB の準備ができる前にプロキシが起動した場合に発生します。この問題は以下によって修正されています:
- MariaDB サービスに追加されたヘルスチェック
- `service_healthy` に設定された `depends_on` 条件

依然としてこのエラーが表示される場合:
```bash
# すべてのサービスを再起動
docker compose down
docker compose up --build -d

# MariaDB が正常 (healthy) か確認
docker compose ps

# MariaDB の準備ができるまで待機 (初回起動時は 15〜30 秒かかる場合があります)
docker compose logs mariadb | tail -20

# MariaDB が正常になったらプロキシを再起動
docker compose restart proxy
```

**ログの "Can't initialize database" 警告**

データベースが起動時に準備できていなくても、プロキシは動作を継続するようになりました。テーブルは初回のリクエスト時に作成されます。

**phpMyAdmin: データベースに接続できない**

phpMyAdmin が接続できない場合は、以下を確認してください:
1. MariaDB サービスが実行されているか: `docker compose ps`
2. MariaDB のログを確認: `docker compose logs mariadb`
3. docker-compose.yml と MariaDB コンテナ間で環境変数が一致しているか検証

**データベースの接続確認**

ヘルスエンドポイントを使用:
```bash
curl http://localhost:8888/health
```

または MariaDB を直接テスト:
```bash
docker exec restdb.io-proxy-mariadb-1 mysqladmin -h localhost -u root -p${MYSQL_ROOT_PASSWORD} ping
```

**ログ**

すべてのサービスログを表示:
```bash
docker compose logs -f
```

特定のサービスを表示:
```bash
docker compose logs -f proxy
docker compose logs -f mariadb
```

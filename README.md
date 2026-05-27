# 概要

本プロジェクトは、restdb.io などのマネージドデータベースサービスに代わる、軽量でセルフホスト可能な代替ソリューションです。
API の利用制限や厳格な制約を回避し、柔軟かつ自社でホストできる環境を提供します。

バックエンドには MariaDB（SQLAlchemy + aiomysql 経由）を使用しています。

# 開発の背景と経緯

当方は別プロジェクトで「STSen」というソフトウェアを開発しています。
しかし、運用開始の翌日に利用していた外部サービスのAPI利用制限に到達してしまい、システムが停止する事態に陥りました。

STSen自体のプログラムを改修する選択肢もありましたが、別の不具合を誘発する懸念があったため、「それなら利用していたサービスの代替ソフトウェアを自作すれば良いのでは」と思い至りました。

幸い手元には優秀なAIアシスタントがいたため、AIと共に Vibe Coding（バイブコーディング） で一気に構築しました。そのため、このリポジトリはAIによって生成されたコードのみで構成されています。

# API 互換性と機能

本プロキシは、restdb.io の REST API と高いレベルでの互換性を実装しています。

サポートされている HTTP メソッド

GET - ドキュメントの取得

POST - ドキュメントの作成

PUT - ドキュメント全体の置換

PATCH - 部分的な更新

DELETE - ドキュメントの削除

# コレクション

/rest/queue - queue コレクション用の汎用 REST エンドポイント

/rest/requests - requests コレクション用の汎用 REST エンドポイント

/rest/config - config コレクション用の汎用 REST エンドポイント (キー/値の設定)

※後方互換性のためにレガシーエンドポイント /queue, /requests もサポートしています。

MongoDB 形式のクエリ (?q={} パラメータ)

$eq (等しい), $gt (より大きい), $lt (より小さい), $gte (以上), $lte (以下)

$in (配列に含まれる), $nin (配列に含まれない), $ne (等しくない)

# リクエスト例:

curl -H "x-apikey: secret" "http://localhost:8888/rest/queue?q={\"priority\":true}"


# ヘッダーオプション (?h={} パラメータ)

$orderby - フィールドのソート: {"priority": 1, "id": -1} (1=昇順, -1=降順)

$fields - フィールドの選択: {"videoId": 1, "priority": 1}

$max - 結果の制限: {"$max": 10}

$skip - 結果のオフセット: {"$skip": 5}

# リクエスト例:

curl -H "x-apikey: secret" "http://localhost:8888/rest/queue?h={\"$orderby\":{\"id\":-1},\"$max\":10}"


# メタデータ API とバルク操作

GET /rest/_meta - データベースのメタデータを取得

GET /rest/<collection>/_meta - コレクションのメタデータを取得 (フィールドタイプ、ドキュメント数)

DELETE /rest/<collection>/* - ID リストで削除 (本文: ["id1", "id2"])

DELETE /rest/<collection>/*?q={...} - MongoDB 形式のクエリで削除

# インストールと実行

## Docker を使用する場合 (推奨)

リモートサーバーへのデプロイや、環境を汚さない再現可能なデプロイには Docker Compose が推奨されます。MariaDB と phpMyAdmin も同時に起動します。

# 環境変数の準備（推奨）

cp .env.example .env
# .env を編集して PROXY_API_KEY やデータベースの認証情報を設定します


# ビルドと起動

docker compose up --build -d


※インラインで変数を渡して起動することも可能です。
PROXY_API_KEY=secret docker compose up --build -d

# ローカル・開発環境での実行

Python環境で直接実行する場合の手順です。（※別途 localhost:3306 で MariaDB が稼働している必要があります）

# 依存関係のインストール
python -m pip install -r requirements.txt

# 環境設定ファイルの準備
cp .env.example .env
# .env を編集して MariaDB の接続情報を設定してください

# アプリケーションの実行
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888


# Linux でのクイック起動とサービス化

提供しているスクリプトを利用して簡単に起動できます。

## クイック起動:
```
cd restdb.io-proxy
cp .env.example .env  # 必要に応じて編集
./run.sh              # 仮想環境(.venv)を作成・インストールして起動
```

※次回以降、仮想環境を再作成せずに実行する場合は ./start.sh を使用してください。

## Systemd を利用した自動起動:
```
sudo cp restdb.io-proxy/systemd/restdb-io-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now restdb-io-proxy.service
```

※ユニットファイル内の WorkingDirectory と EnvironmentFile はご自身のインストールパスに合わせて調整してください。

# 環境変数

環境変数を使用してプロキシを設定できます。.env ファイルを使用するか、環境変数として直接指定してください。
```
変数名

説明

デフォルト値 / 備考

PROXY_API_KEY

クライアントに x-apikey ヘッダーを要求するためのAPIキー

オプション

DATABASE_URL

データベースの完全な接続 URL

設定時は以下の個別設定を上書き

DB_USER

MariaDB ユーザー名

restdb_user (Compose利用時)

DB_PASSWORD

MariaDB パスワード

.env で設定必須

DB_HOST

MariaDB ホスト名

mariadb (Compose利用時)

DB_PORT

MariaDB ポート番号

3306

DB_NAME

MariaDB データベース名

restdb_proxy

付属ツールと監視

phpMyAdmin (データベース管理 GUI)

Docker Compose を使用した場合、自動的に phpMyAdmin が起動します。

アクセスURL: http://localhost:8081

サーバー: mariadb

ユーザー名: root

パスワード: 環境変数 PMA_PASSWORD で設定した値

このGUIから、データの閲覧・編集・削除、生SQLの実行などが可能です。
```
# ヘルスチェック

監視システム向けに、APIキー不要のヘルスチェック用エンドポイントを用意しています。
```
curl http://localhost:8888/health


成功時のレスポンス: {"status":"ok","redis":"connected"}

エラー時のレスポンス: {"status":"error","redis":"failed","error":"..."}

トラブルシューティング
```
起動時のエラー
Q. Docker Compose で "Can't connect to MySQL server on 'localhost'" が出る
MariaDB の起動完了前にプロキシが接続しようとした場合に発生します。
基本的にはヘルスチェック条件により修正されていますが、発生した場合は以下をお試しください。

# サービスの再起動
docker compose down
docker compose up --build -d

# MariaDB のログを確認（準備完了まで15〜30秒かかる場合があります）
docker compose logs mariadb | tail -20

# MariaDB の準備ができたらプロキシを再起動
docker compose restart proxy

Q. ログに "Can't initialize database" 警告が出る
データベースが起動時に準備できていなくても、プロキシは動作を継続します。テーブルは初回のリクエスト時に自動作成されるため問題ありません。

接続に関するエラー

Q. phpMyAdmin からデータベースに接続できない

MariaDB サービスが実行されているか確認: docker compose ps

MariaDB のログを確認: docker compose logs mariadb

docker-compose.yml とコンテナ間で環境変数が一致しているか確認。
```
Q. データベースの接続を直接確認したい

# Docker コンテナ内で ping を実行
docker exec restdb.io-proxy-mariadb-1 mysqladmin -h localhost -u root -p${MYSQL_ROOT_PASSWORD} ping


Q. ログを確認したい

docker compose logs -f         # すべて
docker compose logs -f proxy   # プロキシのみ
docker compose logs -f mariadb # MariaDBのみ
```


# ライセンス
```
本プロジェクトは MITライセンス にて公開しています。

ただし、生成AIを活用して作成されたコードであることを考慮し、国や地域の法制度によって著作権が認められない（あるいはMITライセンスの適用がそぐわない）環境下においては、本ソースコードを パブリックドメイン として扱っていただいて構いません。
（勢いで作ったものなので、厳密なライセンス継承等は気にしません。ご自由にご活用ください。）
```

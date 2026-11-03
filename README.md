# article-production

このリポジトリは、AI記事制作スキル `article-production` を管理します。

- 正本は `main` の [`article-production/SKILL.md`](article-production/SKILL.md) です。リポジトリ直下の `SKILL.md` はありません。
- `SKILL.md` は入口・共通ルール・シート構成のみ。工程の手順は [`references/`](article-production/references)（outline／draft／check／finish）に分割
- 機械チェックは [`article-production/mech_check.py`](article-production/mech_check.py)（旧・付録から独立）
- 受付・構成作成・原稿作成・チェック・仕上げまで実装済み
- HTMLからドキュメントへの変換は [`article-production/html_to_blocks.py`](article-production/html_to_blocks.py)
- ラッコキーワードはコネクタ、無ければ公式API（[`article-production/rakko_fetch.py`](article-production/rakko_fetch.py)、環境変数 `RAKKO_API_KEY`）

https://github.com/lovejack0803/article-production/blob/main/article-production/SKILL.md

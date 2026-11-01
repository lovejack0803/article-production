#!/usr/bin/env python3
"""ラッコキーワード公式API（コネクタが無いときの経路）。

仕様の出典: https://api.rakkokeyword.com/docs （OpenAPI 3.1.1、サーバ https://api.rakkokeyword.com）
認証はヘッダ X-API-Key。キーは環境変数 RAKKO_API_KEY だけから読む。
標準出力・標準エラー・終了メッセージにキーを出さない。

  python3 -I rakko_fetch.py --keyword "メインKW"
成功時は終了コード 0。キーが無いときは 2。呼び出し失敗は 1。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://api.rakkokeyword.com"
# 公式ドキュメントの POST。コネクタの取得範囲（Googleサジェスト100、見出しは h1〜h3 の10件）にパラメータを合わせる。
PATHS = {
    "suggest-keywords": "/v1/suggest-keywords",
    "co-occurrence": "/v1/co-occurrence",
    "other-keywords": "/v1/other-keywords",
    "headline": "/v1/headline",
}


def payloads(keyword):
    return {
        "suggest-keywords": {"keyword": keyword, "modes": ["google"], "limit": 100},
        "co-occurrence": {"keyword": keyword, "getDetails": False},
        "other-keywords": {"keyword": keyword},
        "headline": {
            "keyword": keyword,
            "h1": True,
            "h2": True,
            "h3": True,
            "h4": False,
            "h5": False,
            "h6": False,
            "limit": 10,
        },
    }


def redact(text, key):
    if not text:
        return ""
    if key:
        text = text.replace(key, "[REDACTED]")
    return text


def _errors_from(body):
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, list) and errs:
            return [str(x) for x in errs]
        if body.get("result") is False:
            return ["result is false"]
    return []


def _credit(body):
    if not isinstance(body, dict):
        return 0
    meta = body.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("consumedCredit"), (int, float)):
        return meta["consumedCredit"]
    return 0


def post_json(name, body, key, timeout):
    url = BASE + PATHS[name]
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": key,
        },
    )
    status = None
    raw = ""
    content_type = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            status = res.status
            content_type = res.headers.get("Content-Type", "")
            raw = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        raw = exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return {
            "name": name,
            "url": url,
            "status": None,
            "ok": False,
            "loginPage": False,
            "keyInvalid": False,
            "errors": [redact(str(exc.reason), key)],
            "consumedCredit": 0,
        }
    parsed = None
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    login_page = "text/html" in content_type.lower() or (
        raw.lstrip()[:1] == "<" and "ログイン" in raw[:4000]
    )
    errors = _errors_from(parsed)
    if not errors and parsed is None:
        errors = [redact(raw[:500], key) or "empty response"]
    ok = status == 200 and isinstance(parsed, dict) and parsed.get("result") is True and not login_page
    if not ok and not errors:
        errors = [f"HTTP {status}"]
    return {
        "name": name,
        "url": url,
        "status": status,
        "ok": ok,
        "loginPage": login_page,
        "keyInvalid": status in (401, 403),
        "errors": [redact(e, key) for e in errors],
        "consumedCredit": _credit(parsed),
        "body": parsed if ok else None,
    }


def call_with_retry(name, body, key, timeout):
    first = post_json(name, body, key, timeout)
    if first["ok"]:
        return first
    second = post_json(name, body, key, timeout)
    second["retried"] = True
    second["consumedCredit"] = first["consumedCredit"] + second["consumedCredit"]
    return second


def sheet_rows(bodies):
    """公式レスポンスを、制作シートの2タブに書く列へそろえる。"""
    suggest = [item.get("keyword") or "" for item in bodies["suggest-keywords"]["data"]["items"]]
    cooccurrence = [item.get("word") or "" for item in bodies["co-occurrence"]["data"]["items"]]
    lsi, paa = [], []
    for item in bodies["other-keywords"]["data"]["items"]:
        if item.get("type") == "lsi" and item.get("keyword"):
            lsi.append(item["keyword"])
        elif item.get("type") == "paa" and item.get("question"):
            paa.append(item["question"])
    pages = []
    for item in bodies["headline"]["data"]["items"]:
        page = item.get("page") or {}
        metrics = item.get("metrics") or {}
        headlines = []
        for hl in item.get("headlines") or []:
            level = str(hl.get("level") or "").lower()
            text = hl.get("text") or ""
            if level and text:
                headlines.append(f"{level}:{text}")
        pages.append({
            "rank": metrics.get("position"),
            "title": page.get("title") or "",
            "url": page.get("url") or "",
            "description": page.get("description") or "",
            "headlineCount": metrics.get("headlineCount"),
            "wordCount": metrics.get("wordCount"),
            "headlines": headlines,
        })
    return {
        "suggest": [x for x in suggest if x],
        "cooccurrence": [x for x in cooccurrence if x],
        "lsi": lsi,
        "paa": paa,
        "pages": pages,
    }


def public_call(result):
    return {k: v for k, v in result.items() if k != "body"}


def fetch(keyword, key, timeout):
    calls = []
    bodies = {}
    credit = 0
    for name, body in payloads(keyword).items():
        result = call_with_retry(name, body, key, timeout)
        credit += result["consumedCredit"]
        if not result["ok"]:
            calls.append(public_call(result))
            return {
                "ok": False,
                "route": "api",
                "connectorMissing": None,
                "keyMissing": False,
                "keyInvalid": result["keyInvalid"],
                "loginPage": result["loginPage"],
                "consumedCredit": credit,
                "calls": calls,
                "suggest": [],
                "cooccurrence": [],
                "lsi": [],
                "paa": [],
                "pages": [],
            }
        calls.append(public_call(result))
        bodies[name] = result["body"]
    rows = sheet_rows(bodies)
    ok = bool(rows["pages"])
    return {
        "ok": ok,
        "route": "api",
        "connectorMissing": None,
        "keyMissing": False,
        "keyInvalid": False,
        "loginPage": False,
        "consumedCredit": credit,
        "calls": calls,
        "stoppedBecause": None if ok else "headline items are empty",
        **rows,
    }


def missing_key_result():
    return {
        "ok": False,
        "route": "api",
        "connectorMissing": None,
        "keyMissing": True,
        "keyInvalid": False,
        "loginPage": False,
        "consumedCredit": 0,
        "calls": [],
        "errors": ["RAKKO_API_KEY is not set"],
        "suggest": [],
        "cooccurrence": [],
        "lsi": [],
        "paa": [],
        "pages": [],
    }


def self_test():
    secret = "rakko-secret-value"
    assert redact("bad rakko-secret-value here", secret) == "bad [REDACTED] here"
    assert secret not in redact("x", secret)
    bodies = {
        "suggest-keywords": {"data": {"items": [{"keyword": "seo 対策"}, {"keyword": ""}]}},
        "co-occurrence": {"data": {"items": [{"word": "検索"}]}},
        "other-keywords": {"data": {"items": [
            {"type": "lsi", "keyword": "内部対策"},
            {"type": "paa", "question": "費用は？"},
        ]}},
        "headline": {"data": {"items": [{
            "page": {"url": "https://example.com/a", "title": "例", "description": "説明"},
            "metrics": {"position": 1, "headlineCount": 2, "wordCount": 100},
            "headlines": [{"level": "h2", "text": "費用"}, {"level": "h3", "text": "内訳"}],
        }, {
            "page": {"url": "https://example.com/a", "title": "同じ", "description": ""},
            "metrics": {"position": 1, "headlineCount": 1, "wordCount": 10},
            "headlines": [{"level": "h2", "text": "費用"}],
        }]}},
    }
    rows = sheet_rows(bodies)
    assert rows["suggest"] == ["seo 対策"]
    assert rows["cooccurrence"] == ["検索"]
    assert rows["lsi"] == ["内部対策"] and rows["paa"] == ["費用は？"]
    assert rows["pages"][0]["headlines"] == ["h2:費用", "h3:内訳"]
    assert rows["pages"][0]["rank"] == 1
    dumped = json.dumps(missing_key_result())
    assert "RAKKO_API_KEY is not set" in dumped
    assert secret not in dumped
    failed = {
        "name": "suggest-keywords",
        "url": BASE + PATHS["suggest-keywords"],
        "errors": [redact("rejected " + secret, secret)],
        "body": {"secret": secret},
    }
    blob = json.dumps(public_call(failed), ensure_ascii=False)
    assert secret not in blob
    assert failed["url"] == "https://api.rakkokeyword.com/v1/suggest-keywords"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        print("ok")
        return
    if not args.keyword:
        sys.exit("keyword が必要です")
    key = os.environ.get("RAKKO_API_KEY") or ""
    if not key.strip():
        json.dump(missing_key_result(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.exit(2)
    result = fetch(args.keyword, key, args.timeout)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

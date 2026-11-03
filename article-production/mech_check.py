#!/usr/bin/env python3
"""article-production の機械チェック。レギュレーションタブ（read_range の結果を {"values": [...]} にした JSON）から案件・工程に合う有効な行を選び、
I列「機械チェック」の指定で検査する。文字数はタグを除き、半角スペースと全角スペースを数えない。
H2_COUNT は「まとめ」と「よくある質問」のH2を個数に入れない。
出力 JSON: errors / warnings（目安）/ unsupported（未対応の指定）/ checked。
  python3 -I mech_check.py --reg reg.json --project 案件名 --stage 構成 --print-rules
  python3 -I mech_check.py --reg reg.json --project 案件名 --stage 構成|本文|タイトル等|最終確認 --file F --kw KW [--outline O] [--split-out J]
"""
import argparse, csv, io, json, re, sys

REG_INFO = {}  # 読んだ行数と、最終行が埋まっているか（範囲切れの検出用）

STAGES = ("構成", "本文", "タイトル等", "最終確認")
FENCE = "`" * 3
SOFT = re.compile(r"[（(]目安[)）]")


def norm_name(s):
    return re.sub(r"(様|さま|御中)$", "", re.sub(r"[\s　]+", "", str(s or "")))


def load_rules(path, project, stage):
    raw = open(path, encoding="utf-8").read()
    try:
        d = json.loads(raw)
        rows = d["values"] if isinstance(d, dict) else d
    except ValueError:  # read_range の CSV をそのまま保存したものも受け付ける
        rows = list(csv.reader(io.StringIO(raw)))
    REG_INFO["rows"] = len(rows)
    REG_INFO["last_filled"] = bool(rows) and any(str(c).strip() for c in rows[-1])
    head = [str(h).strip() for h in rows[0]] if rows else []
    col = lambda name, dflt: head.index(name) if name in head else dflt  # 列は見出し名で探す
    c_t, c_s, c_r, c_on, c_url, c_m = (col("対象名", 1), col("対象工程", 2), col("ルール", 4),
                                       col("有効", 5), col("参照URL", 6), col("機械チェック", 8))
    pj, out = norm_name(project), []
    for r in rows[1:]:
        g = lambda i: str(r[i]).strip() if i < len(r) and r[i] is not None else ""
        if g(c_on).upper() != "TRUE" or not g(c_r):
            continue
        if not (g(c_t) == "共通" or (pj and pj != "共通" and norm_name(g(c_t)) == pj)):
            continue
        if stage not in [x.strip() for x in re.split(r"[,、，]", g(c_s))]:
            continue
        out.append({"target": g(c_t), "rule": g(c_r), "url": g(c_url), "check": g(c_m)})
    return out


def parse_checks(rules):
    out = []
    for ru in rules:
        for item in [x.strip() for x in re.split(r"[,，]", ru["check"]) if x.strip()]:
            cid, _, p = SOFT.sub("", item).strip().partition(":")
            out.append((cid.strip(), p.strip(), bool(SOFT.search(item))))
    return out


# 文字数: HTMLタグを除く。半角スペース(U+0020)と全角スペース(U+3000)は数えない。改行・タブ・NBSPも数えない。①や「」は1文字。
plain = lambda s: re.sub(r"[ \t\n\r\f\v\u00a0\u3000]+", "", re.sub(r"<[^>]+>", "", s or ""))
is_matome = lambda t: t.startswith("まとめ")


def is_faq(t):
    """H2_COUNT に数えない。先頭が「よくある質問」、または FAQ / Q&A だけの見出し。"""
    s = re.sub(r"^[【\[［(（]+", "", t.strip())
    if s.startswith("よくある質問"):
        return True
    key = re.sub(r"[ \t\u3000]+", "", s).lower()
    return key in {"faq", "q&a", "q＆a", "ｑ＆ａ"}


def rng(p):
    a, _, b = p.partition("-")
    return (int(a) if a else 0, int(b) if b else 10**9)


def kw_in(text, kw):
    t, k = re.sub(r"\s+", "", text.lower()), kw.lower()
    toks = [x for x in re.split(r"[\s　]+", k) if x]
    return re.sub(r"[\s　]+", "", k) in t or bool(toks and all(x in t for x in toks))


def headings(text):
    return [(m.group(1).lower(), plain(m.group(2)))
            for m in re.finditer(r"<(h[1-6])[^>]*>(.*?)</\1>", text, re.S | re.I)]


def outline_text(raw):
    """outline.json（[{"tag","heading",…}]）でも <hN>…</hN> の行でも受け付ける"""
    if raw.strip().startswith("["):
        return "\n".join("<{0}>{1}</{0}>".format(it["tag"].strip("<>/"), it["heading"]) for it in json.loads(raw))
    return raw


def check_outline(text, checks, kw, add, done):
    hs = headings(text)
    for cid, p, soft in checks:
        a = add(soft)
        if cid == "HEAD_TAGS":
            for l in [l.strip() for l in text.splitlines() if l.strip()]:
                if not re.fullmatch(r"<(h[23])>.+</\1>", l):
                    a(f"HEAD_TAGS: h2/h3以外の行「{l[:40]}」")
        elif cid == "H2_COUNT":
            n, (lo, hi) = sum(t == "h2" and not is_matome(x) and not is_faq(x) for t, x in hs), rng(p)
            if not lo <= n <= hi:
                a(f"H2_COUNT: まとめ・よくある質問以外のh2が{n}個（{p}個にする）")
        elif cid == "H3_PER_H2":
            (lo, hi), cur, cnt = rng(p), None, 0
            for t, x in hs + [("h2", "__END__")]:
                if t == "h2":
                    if cur and not is_matome(cur) and not lo <= cnt <= hi:
                        a(f"H3_PER_H2: 「{cur}」の下のh3が{cnt}個（{p}個にする）")
                    cur, cnt = x, 0
                elif t == "h3":
                    cnt += 1
        elif cid == "HEAD_LEN":
            lo, hi = rng(p)
            for t, x in hs:
                if not is_matome(x) and not lo <= len(x) <= hi:
                    a(f"HEAD_LEN: {t}「{x}」が{len(x)}文字（{p}文字にする）")
        elif cid == "END_MATOME":
            if not hs or hs[-1] != ("h2", "まとめ"):
                a("END_MATOME: 最後が<h2>まとめ</h2>になっていない")
        elif cid == "HEAD_ORDER":
            if hs and hs[0][0] != "h2":
                a("HEAD_ORDER: 最初の見出しがh2ではない")
            for (t1, x1), (t2, _) in zip(hs, hs[1:]):
                if int(t2[1]) > int(t1[1]) + 1:
                    a(f"HEAD_ORDER: 「{x1}」({t1})の次が{t2}")
        elif cid == "H2_KW":
            if not kw:
                a("H2_KW: --kw がないため判定できない")
            for t, x in hs if kw else []:
                if t == "h2" and not is_matome(x) and not kw_in(x, kw):
                    a(f"H2_KW: h2「{x}」に対策キーワード「{kw}」がない")
        else:
            continue
        done.add(cid)


def check_body(text, checks, outline, add, done, errors):
    bl = [(m.group(1).lower(), m.group(2)) for m in re.finditer(r"<(h2|h3|p|ul)[^>]*>(.*?)</\1>", text, re.S | re.I)]
    hs = [(t, plain(x)) for t, x in bl if t in ("h2", "h3")]
    if outline is not None:  # 確定構成と見出しが一致しているかは常に見る
        want = [(t, x) for t, x in headings(outline) if not is_matome(x)]
        if hs != want:
            miss = [x for t, x in want if (t, x) not in hs]
            extra = [x for t, x in hs if (t, x) not in want]
            errors.append("HEAD_MATCH: 見出しが確定構成と一致しない" + (f" 不足={miss[:5]}" if miss else "")
                          + (f" 余分={extra[:5]}" if extra else "") + ("" if miss or extra else "（順番が違う）"))
    secs = []
    for t, x in bl:
        if t in ("h2", "h3"):
            secs.append({"tag": t, "title": plain(x), "items": []})
        elif secs:
            secs[-1]["items"].append((t, x))
    for cid, p, soft in checks:
        a = add(soft)
        if cid == "BODY_TAGS":
            bad = sorted({m.group(1).lower() for m in re.finditer(r"</?([a-zA-Z0-9]+)[^>]*>", text)} - {"h2", "h3", "p", "ul", "li"})
            if bad:
                a(f"BODY_TAGS: 使えないタグ {bad}")
        elif cid == "BODY_NO_MATOME":
            if any(t == "h2" and is_matome(x) for t, x in hs):
                a("BODY_NO_MATOME: 本文に<h2>まとめ</h2>が含まれている")
        elif cid in ("H2_INTRO_LIST", "H2_INTRO_LEN"):
            for i, s in enumerate(secs):
                if s["tag"] != "h2" or is_matome(s["title"]):
                    continue
                h3s = []
                for s2 in secs[i + 1:]:
                    if s2["tag"] == "h2":
                        break
                    h3s.append(s2["title"])
                if cid == "H2_INTRO_LIST" and len(h3s) >= 2:
                    uls = [x for t, x in s["items"] if t == "ul"]
                    lis = [plain(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", uls[0], re.S)] if uls else []
                    if lis != h3s:
                        a(f"H2_INTRO_LIST: 「{s['title']}」の導入の箇条書きが配下h3と一致しない")
                if cid == "H2_INTRO_LEN":
                    ps = [x for t, x in s["items"] if t == "p"]
                    if len(ps) != 1:
                        add(False)(f"H2_INTRO_LEN: 「{s['title']}」のリードの<p>が{len(ps)}個（1個にする）")
                    else:
                        n, (lo, hi) = len(plain(ps[0])), rng(p)
                        if not lo <= n <= hi:
                            a(f"H2_INTRO_LEN: 「{s['title']}」のリードが{n}文字（{p}文字）")
        elif cid == "H3_P":
            lo, hi = rng(p)
            for s in [s for s in secs if s["tag"] == "h3"]:
                ps = [x for t, x in s["items"] if t == "p"]
                if len(ps) != 1:
                    a(f"H3_P: h3「{s['title']}」の<p>が{len(ps)}個（1個にする）")
                elif not lo <= len(plain(ps[0])) <= hi:
                    a(f"H3_P: h3「{s['title']}」の<p>が{len(plain(ps[0]))}文字（{p}文字にする）")
        else:
            continue
        done.add(cid)


LABELS = ["タイトル", "導入文", "まとめ", "メタディスクリプション"]


def split_title_parts(text):
    pat = re.compile(r"^\s*[#【\*]*\s*(タイトル|導入文|まとめ|メタディスクリプション)\s*[】\*：:]*\s*$", re.M)
    marks, parts = [(m.start(), m.end(), m.group(1)) for m in pat.finditer(text)], {}
    for i, (s, e, lab) in enumerate(marks):
        parts.setdefault(lab, text[e:marks[i + 1][0] if i + 1 < len(marks) else len(text)].strip())
    return {k: parts.get(k, "") for k in LABELS}


def check_title(text, checks, kw, add, done, errors):
    pt = split_title_parts(text)
    errors += [f"PARSE: 「{k}」が見つからない" for k, v in pt.items() if not v]
    lens = {"TITLE_LEN": "タイトル", "INTRO_LEN": "導入文", "MATOME_LEN": "まとめ", "META_LEN": "メタディスクリプション"}
    for cid, p, soft in checks:
        a = add(soft)
        if cid in lens:
            lab = lens[cid]
            body = pt[lab] if lab == "タイトル" else re.sub(r"<h[12][^>]*>.*?</h[12]>", "", pt[lab], flags=re.S)
            n, (lo, hi) = len(plain(body)), rng(p)
            if not lo <= n <= hi:
                a(f"{cid}: {lab}が{n}文字（{p}文字にする）")
        elif cid == "TITLE_KW":
            if not kw:
                a("TITLE_KW: --kw がないため判定できない")
            elif pt["タイトル"] and not kw_in(plain(pt["タイトル"]), kw):
                a(f"TITLE_KW: タイトルに対策キーワード「{kw}」がない")
        else:
            continue
        done.add(cid)
    return pt


def check_final(text, checks, add, done):
    # 文字数は plain（タグと半角・全角スペースを除く）。（目安）は soft で警告になる。SENT_LEN の数字は上限。
    sents = []
    for seg in [plain(x) for x in re.split(r"</?(?:p|li|ul|h[1-6])[^>]*>|\n", text) if plain(x)]:
        sents += [s for s in re.split(r"(?<=[。！？])", seg) if len(s) > 1]
    for cid, p, soft in checks:
        a = add(soft)
        if cid == "SENT_LEN":
            for s in sents:
                if len(s) > int(p or 80):
                    a(f"SENT_LEN: {len(s)}文字の文「{s[:30]}…」")
        elif cid == "SAME_END":
            n, ends, run = int(p or 3), [s.rstrip("。！？")[-2:] for s in sents], 1
            for i in range(1, len(ends)):
                run = run + 1 if ends[i] == ends[i - 1] else 1
                if run == n:
                    a(f"SAME_END: 文末「{ends[i]}」が{n}文連続「{sents[i][:30]}…」")
        else:
            continue
        done.add(cid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reg", required=True)
    ap.add_argument("--project", default="共通")
    ap.add_argument("--stage", required=True, choices=STAGES)
    ap.add_argument("--file")
    ap.add_argument("--kw", default="")
    ap.add_argument("--outline")
    ap.add_argument("--split-out")
    ap.add_argument("--print-rules", action="store_true")
    a = ap.parse_args()
    rules = load_rules(a.reg, a.project, a.stage)
    if REG_INFO.get("last_filled") and REG_INFO["rows"] >= 100:
        print(f"警告 REG_RANGE: レギュレーションの最終行（{REG_INFO['rows']}行目）まで埋まっている。範囲を広げて読み直す", file=sys.stderr)
    if a.print_rules:
        for r in rules:
            print(f"[{'共通' if r['target'] == '共通' else '案件'}] {r['rule']}" + (f"\n参照URL: {r['url']}" if r["url"] else ""))
        return
    if not a.file:
        sys.exit("--file が必要です")
    text = open(a.file, encoding="utf-8").read().replace(FENCE + "html", "").replace(FENCE, "")
    checks = parse_checks(rules)
    E, done = {"errors": [], "warnings": [], "unsupported": [], "checked": []}, set()
    add = lambda soft: E["warnings" if soft else "errors"].append
    if a.stage == "構成":
        check_outline(outline_text(text), checks, a.kw, add, done)
    elif a.stage == "本文":
        ol = outline_text(open(a.outline, encoding="utf-8").read()) if a.outline else None
        check_body(text, checks, ol, add, done, E["errors"])
    elif a.stage == "タイトル等":
        pt = check_title(text, checks, a.kw, add, done, E["errors"])
        if a.split_out:
            json.dump(pt, open(a.split_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    else:
        check_final(text, checks, add, done)
    for cid, p, soft in checks:
        (E["checked"] if cid in done else E["unsupported"]).append(cid + (f":{p}" if p else "") + ("(目安)" if soft else ""))
    if REG_INFO.get("last_filled") and REG_INFO["rows"] >= 100:
        E["warnings"].append(f"REG_RANGE: レギュレーションの最終行（{REG_INFO['rows']}行目）まで埋まっている。範囲を広げて読み直す")
    json.dump(E, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()

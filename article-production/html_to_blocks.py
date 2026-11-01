#!/usr/bin/env python3
"""完成原稿のHTMLを、Googleドキュメントへ積むブロック列にする。

原稿変換の規則（見出しの重複削除、スタイル、執筆依頼、メタの接頭辞、文字の整形）をここで固定する。
呼び出し: python3 -I html_to_blocks.py final.html
標準出力は JSON 配列。中身の変換を手でやり直さない。
"""
import json
import re
import sys
from html.parser import HTMLParser

NOTICE_KEYS = ("生成不要", "未生成", "事例のため", "まとめのため", "手動執筆")
NOTICE = "【執筆依頼】この見出しはAIで生成していません。構成案をもとに執筆をお願いいたします。"
HEADING_STYLE = {"title": "TITLE", "h1": "TITLE", "h2": "HEADING_2", "h3": "HEADING_3", "h4": "HEADING_4"}
UNWRAP = {"html", "body", "div", "span", "section", "article", "main", "header", "footer", "figure", "figcaption", "blockquote"}
SKIP = {"script", "style"}


def clean_text(s):
    """<br> を改行にし、タグを除き、指定の文字参照だけを戻す。"""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    return s.strip()


def _paragraph(style, text, bold=False, text_color=None):
    block = {"type": "paragraph", "style": style, "text": text}
    if bold:
        block["bold"] = True
    if text_color:
        block["textColor"] = text_color
    return block


class _Node:
    def __init__(self, tag):
        self.tag = tag
        self.children = []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = _Node(None)
        self.stack = [self.root]
        self.skip = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            return
        if tag in SKIP:
            self.skip = tag
            return
        if tag == "br":
            self.stack[-1].children.append("\n")
            return
        node = _Node(tag)
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip:
                self.skip = None
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.skip or not data:
            return
        self.stack[-1].children.append(data)

    def handle_entityref(self, name):
        self.handle_data("&" + name + ";")

    def handle_charref(self, name):
        self.handle_data("&#" + name + ";")


def _inner_text(node):
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br":
            parts.append("\n")
        else:
            parts.append(_inner_text(child))
    return clean_text("".join(parts))


def _is_label_only(text):
    rest = text.replace("メタディスクリプション", "")
    return rest.strip(" \n:：[]【】") == ""


def _table_rows(node):
    rows = []

    def walk(n):
        for child in n.children:
            if isinstance(child, str):
                continue
            if child.tag == "tr":
                cells = [_inner_text(c) for c in child.children if not isinstance(c, str) and c.tag in ("td", "th")]
                if cells:
                    rows.append(cells)
            elif child.tag in ("thead", "tbody", "tfoot"):
                walk(child)

    walk(node)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def _list_items(node, level):
    items = []
    for child in node.children:
        if isinstance(child, str):
            continue
        if child.tag == "li":
            parts = []
            nested = []
            for grand in child.children:
                if isinstance(grand, str):
                    parts.append(grand)
                elif grand.tag in ("ul", "ol"):
                    nested.append(grand)
                else:
                    parts.append(_inner_text(grand))
            text = clean_text("".join(parts))
            if text:
                items.append({"text": text, "level": level, "ordered": node.tag == "ol"})
            for sub in nested:
                items.extend(_list_items(sub, level + 1))
        elif child.tag in ("ul", "ol"):
            items.extend(_list_items(child, level + 1))
    return items


def _emit_list(items, blocks):
    run = []

    def flush():
        if not run:
            return
        blocks.append({
            "type": "list",
            "ordered": run[0]["ordered"],
            "items": [{"text": item["text"], "level": item["level"]} for item in run],
        })
        run.clear()

    for item in items:
        if run and run[0]["ordered"] != item["ordered"]:
            flush()
        run.append(item)
    flush()


def _emit_heading(tag, text, blocks, seen):
    if tag in ("h2", "h3", "h4"):
        key = (tag, text)
        if key in seen:
            return
        seen.add(key)
    blocks.append(_paragraph(HEADING_STYLE[tag], text))
    if any(key in text for key in NOTICE_KEYS):
        blocks.append(_paragraph("NORMAL_TEXT", NOTICE, bold=True, text_color="#CC0000"))


def _walk(nodes, blocks, seen, prefix, release_tail):
    """prefix[0] は、直後の <p> に接頭辞を付ける未処理の文。None でなければまだ段落にしていない。"""
    buf = []

    def flush():
        cleaned = clean_text("".join(buf))
        buf.clear()
        if not cleaned:
            return
        if "メタディスクリプション" in cleaned:
            prefix[0] = cleaned
            return
        _release()
        blocks.append(_paragraph("NORMAL_TEXT", cleaned))

    def _release():
        if isinstance(prefix[0], str):
            blocks.append(_paragraph("NORMAL_TEXT", prefix[0]))
            prefix[0] = None

    for node in nodes:
        if isinstance(node, str):
            buf.append(node)
            continue
        flush()
        if node.tag in UNWRAP:
            _walk(node.children, blocks, seen, prefix, False)
            continue
        if node.tag == "p":
            text = _inner_text(node)
            if isinstance(prefix[0], str) and text and not text.startswith("【メタディスクリプション】"):
                text = "【メタディスクリプション】" + text
            prefix[0] = None
            if text:
                blocks.append(_paragraph("NORMAL_TEXT", text))
            continue
        _release()
        if node.tag in ("title", "h1", "h2", "h3", "h4"):
            text = _inner_text(node)
            if text:
                _emit_heading(node.tag, text, blocks, seen)
        elif node.tag == "table":
            rows = _table_rows(node)
            if rows:
                blocks.append({"type": "table", "headerBold": True, "rows": rows})
        elif node.tag in ("ul", "ol"):
            _emit_list(_list_items(node, 0), blocks)
        else:
            _walk(node.children, blocks, seen, prefix, False)
    flush()
    if release_tail:
        _release()


def html_to_blocks(html):
    parser = _Tree()
    parser.feed(html or "")
    parser.close()
    blocks = []
    _walk(parser.root.children, blocks, set(), [None], True)
    return blocks


def main():
    if len(sys.argv) != 2:
        sys.exit("使い方: python3 -I html_to_blocks.py final.html")
    with open(sys.argv[1], encoding="utf-8") as fh:
        html = fh.read()
    json.dump(html_to_blocks(html), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

"""Rebuild docs/manual/USER_MANUAL.html from USER_MANUAL.md with the images embedded (python-markdown).
    python tools/docs/manual_html.py
"""
import base64
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs/manual/USER_MANUAL.md"
DST = ROOT / "docs/manual/USER_MANUAL.html"
STYLE = """body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:30px auto;padding:0 24px;color:#222;line-height:1.45}
h1{color:#1F3864;border-bottom:3px solid #1F3864;padding-bottom:6px}h2{color:#1F3864;margin-top:40px;border-bottom:1px solid #ccd}
h3{color:#2F5496}table{border-collapse:collapse;margin:12px 0;font-size:14px}th{background:#1F3864;color:#fff;text-align:left;padding:6px 10px}
td{border-bottom:1px solid #ddd;padding:5px 10px;vertical-align:top}code{background:#f3f3f3;padding:1px 4px;border-radius:3px}
pre{background:#f3f3f3;padding:10px;overflow:auto}img{max-width:100%;border:1px solid #ccc;margin:8px 0}
blockquote{border-left:4px solid #ccd;margin:0;padding:4px 14px;color:#444}hr{border:0;border-top:1px solid #ccd;margin:30px 0}"""


def main() -> int:
    md = SRC.read_text(encoding="utf-8")
    html = markdown.markdown(md, extensions=["tables", "fenced_code"])

    def embed(m):
        p = SRC.parent / m.group(1)
        if not p.exists():
            print(f"missing image {p}", file=sys.stderr)
            return m.group(0)
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        return f'src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"'

    html = re.sub(r'src="(img/[^"]+)"', embed, html)
    DST.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>XoL Pricing Tool - User manual</title>"
                   f"<style>\n{STYLE}</style></head><body>\n{html}\n</body></html>", encoding="utf-8")
    print(f"wrote {DST.relative_to(ROOT)} ({DST.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

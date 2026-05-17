"""Render an outreach Markdown file to PDF with ESL corporate styling.

Uses Python-Markdown to produce HTML, then headless Chrome/Edge to print to
PDF. No LaTeX, no wkhtmltopdf, no Pandoc required.

Usage:
    python render_pdf.py B_brief_for_perforce.md
    python render_pdf.py C_questions_for_perforce.md
    python render_pdf.py *.md
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CSS = """
@page { size: A4; margin: 22mm 20mm 18mm 20mm; }
* { box-sizing: border-box; }
body {
    font-family: "Open Sans", "Segoe UI", Arial, Helvetica, sans-serif;
    font-size: 10.5pt;
    line-height: 1.35;
    color: #111;
    margin: 0;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 0;
    margin-bottom: 14px;
}
.header .brand {
    font-size: 11pt;
    color: #333;
}
.header .logo {
    font-weight: 700;
    font-size: 14pt;
    color: #1a4f8a;
    letter-spacing: 1px;
}
h1 { font-size: 19pt; margin: 6px 0 4px; font-weight: 700; }
h2 { font-size: 14pt; margin: 18px 0 6px; font-weight: 700; }
h3 { font-size: 12pt; margin: 16px 0 4px; font-weight: 700; }
h4 { font-size: 11pt; margin: 12px 0 4px; font-weight: 700; }
p, li { margin: 4px 0; }
ul, ol { margin: 4px 0 4px 20px; padding: 0; }
code {
    font-family: Consolas, "Courier New", monospace;
    font-size: 0.92em;
    color: #000;
}
pre {
    font-family: Consolas, "Courier New", monospace;
    font-size: 9pt;
    background: #f6f8fa;
    border: 1px solid #e1e4e8;
    border-radius: 4px;
    padding: 8px 10px;
    overflow-x: auto;
    line-height: 1.25;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0 12px;
    font-size: 10pt;
}
th, td {
    border: 1px solid #d3d3d3;
    padding: 5px 8px;
    text-align: left;
    vertical-align: top;
}
th { background: #f2f2f2; font-weight: 700; }
hr { border: 0; border-top: 1px solid #d3d3d3; margin: 16px 0; }
blockquote {
    border-left: 3px solid #c0c0c0;
    margin: 8px 0;
    padding: 2px 12px;
    color: #555;
    font-style: italic;
}
.footer-note {
    margin-top: 24px;
    font-size: 9pt;
    color: #666;
    border-top: 1px solid #e0e0e0;
    padding-top: 8px;
}
"""

COMPACT_CSS = """
@page { size: A4; margin: 12mm 12mm 10mm 12mm; }
* { box-sizing: border-box; }
body {
    font-family: "Open Sans", "Segoe UI", Arial, Helvetica, sans-serif;
    font-size: 8.2pt;
    line-height: 1.2;
    color: #111;
    margin: 0;
}
.header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 4px;
}
.header .brand { font-size: 8.5pt; color: #333; }
.header .logo { font-weight: 700; font-size: 11pt; color: #1a4f8a; letter-spacing: 1px; }
h1 { font-size: 14pt; margin: 2px 0 2px; font-weight: 700; }
h2 { font-size: 10.5pt; margin: 7px 0 2px; font-weight: 700; }
h3 { font-size: 9pt; margin: 6px 0 2px; font-weight: 700; }
h4 { font-size: 8.5pt; margin: 5px 0 2px; font-weight: 700; }
p, li { margin: 1px 0; }
ul, ol { margin: 2px 0 2px 16px; padding: 0; }
code { font-family: Consolas, "Courier New", monospace; font-size: 0.9em; color: #000; }
pre {
    font-family: Consolas, "Courier New", monospace;
    font-size: 7.5pt;
    background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px;
    padding: 4px 6px; line-height: 1.2; margin: 3px 0;
}
table { border-collapse: collapse; width: 100%; margin: 3px 0 5px; font-size: 7.8pt; }
th, td { border: 1px solid #d3d3d3; padding: 2px 5px; text-align: left; vertical-align: top; }
th { background: #f2f2f2; font-weight: 700; }
hr { border: 0; border-top: 1px solid #d3d3d3; margin: 6px 0; }
blockquote {
    border-left: 3px solid #c0c0c0; margin: 4px 0; padding: 1px 8px;
    color: #555; font-style: italic;
}
"""

HEADER_HTML = """
<div class="header">
  <div class="logo">ESL</div>
  <div class="brand">Engineering Software Lab Ltd.</div>
</div>
"""

HTML_TEMPLATE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head><body>
{header}
{body}
</body></html>
"""


def find_chrome() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    raise RuntimeError("No Chrome or Edge browser found for headless PDF rendering.")


def render(md_path: Path, out_dir: Path, compact: bool = False) -> Path:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )
    html = HTML_TEMPLATE.format(
        title=md_path.stem,
        css=COMPACT_CSS if compact else CSS,
        header=HEADER_HTML,
        body=body,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{md_path.stem}.pdf"

    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "doc.html"
        html_path.write_text(html, encoding="utf-8")
        chrome = find_chrome()
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not pdf_path.exists():
            raise RuntimeError(
                f"Chrome failed to render {md_path.name}:\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
    return pdf_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Render outreach Markdown to PDF.")
    ap.add_argument("inputs", nargs="+", help="Markdown files (globs allowed)")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "pdf",
        help="Output directory (default: ./pdf)",
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="Render with tighter margins/fonts to fit more on one page.",
    )
    args = ap.parse_args()

    files: list[Path] = []
    for pat in args.inputs:
        p = Path(pat)
        if p.is_file():
            files.append(p)
        else:
            files.extend(Path().glob(pat))

    if not files:
        print("No input files found.", file=sys.stderr)
        return 1

    for f in files:
        out = render(f, args.out, compact=args.compact)
        print(f"  {f.name}  ->  {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert the demo walkthrough markdown to a styled PDF via WeasyPrint.

Usage:
    python demo/md_to_pdf.py

Requirements:
    pip install weasyprint
    brew install pandoc  (or equivalent for your OS)
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from weasyprint import HTML

DEMO_DIR = Path(__file__).resolve().parent
MD_FILE = DEMO_DIR / "DEMO_WALKTHROUGH.md"
OUT_PDF = DEMO_DIR / "DEMO_WALKTHROUGH.pdf"

CSS = """
@page {
    size: letter;
    margin: 0.75in 0.85in;
    @bottom-center {
        content: "GCO (Global Capacity Orchestrator on AWS)";
        font-size: 8pt;
        color: #999;
        font-family: "Helvetica Neue", Arial, sans-serif;
    }
    @bottom-right {
        content: counter(page);
        font-size: 8pt;
        color: #999;
        font-family: "Helvetica Neue", Arial, sans-serif;
    }
}
body {
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #232f3e;
}
h1 {
    font-size: 22pt;
    color: #232f3e;
    border-bottom: 3px solid #ff9900;
    padding-bottom: 6px;
    margin-top: 0;
}
h2 {
    font-size: 15pt;
    color: #232f3e;
    border-bottom: 1px solid #ddd;
    padding-bottom: 4px;
    margin-top: 28px;
    page-break-after: avoid;
}
h3 {
    font-size: 12pt;
    color: #232f3e;
    margin-top: 18px;
    page-break-after: avoid;
}
p, li {
    font-size: 10.5pt;
    color: #333;
}
em {
    color: #555;
}
code {
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 9pt;
    background: #f4f6f9;
    padding: 1px 4px;
    border-radius: 3px;
}
pre {
    background: #f4f6f9;
    border: 1px solid #dde0e4;
    border-left: 3px solid #ff9900;
    border-radius: 4px;
    padding: 10px 14px;
    font-size: 8.5pt;
    line-height: 1.45;
    overflow-x: auto;
    page-break-inside: avoid;
}
pre code {
    background: none;
    padding: 0;
    font-size: 8.5pt;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
th {
    background: #232f3e;
    color: white;
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
}
td {
    padding: 6px 10px;
    border-bottom: 1px solid #e0e0e0;
}
tr:nth-child(even) td {
    background: #f9fafb;
}
hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 20px 0;
}
a {
    color: #0073bb;
    text-decoration: none;
}
blockquote {
    border-left: 3px solid #ff9900;
    margin-left: 0;
    padding: 6px 14px;
    background: #fffbf0;
    color: #555;
    font-size: 10pt;
}
strong {
    color: #232f3e;
}
"""


def main():
    # Accept an optional filename argument (default: DEMO_WALKTHROUGH)
    name = sys.argv[1] if len(sys.argv) > 1 else "DEMO_WALKTHROUGH"
    md_file = DEMO_DIR / f"{name}.md"
    out_pdf = DEMO_DIR / f"{name}.pdf"

    if not md_file.exists():
        print(f"Error: {md_file} not found", file=sys.stderr)
        sys.exit(1)

    # Step 1: Convert markdown to HTML with pandoc
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as tmp:
        result = subprocess.run(
            ["pandoc", str(md_file), "-f", "markdown", "-t", "html5", "--standalone"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"pandoc error: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        # Inject our CSS into the HTML
        html_content = result.stdout.replace(
            "</head>",
            f"<style>{CSS}</style></head>",
        )
        tmp.write(html_content)
        tmp.flush()
        tmp_path = tmp.name

    # Step 2: Render to PDF with WeasyPrint
    HTML(filename=tmp_path).write_pdf(str(out_pdf))
    Path(tmp_path).unlink()

    print(f"✅ PDF saved to {out_pdf}")
    print(f"   Size: {out_pdf.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

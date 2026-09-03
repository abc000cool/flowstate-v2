"""PDF rendering of the markdown validation report (CLAUDE.md §7.4).

The markdown written by :func:`validation.report.generate_report` is the
source of truth; this module renders *that text* to PDF with fpdf2 (the
optional ``validation[pdf]`` extra) — headings, paragraphs, bullet lists,
tables as simple grids in a monospace face, the speed-contour PNGs, and page
numbers. It parses only the markdown subset the report template emits and
never computes or reformats a number, so the PDF can carry no value that is
not in the markdown.

Fonts: DejaVu Sans / Sans Mono, taken from matplotlib's bundled font files
(already a dependency of the report generator), so the Unicode characters
the report uses (em dash, Δ, σ, ≥, ±) render without a core-font fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

if TYPE_CHECKING:
    from fpdf import FPDF

#: The optional-dependency extra that provides fpdf2.
PDF_EXTRA = "validation[pdf]"

_IMAGE_RE = re.compile(r"^!\[(?P<caption>.*?)\]\((?P<path>[^)]+)\)\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_BULLET_RE = re.compile(r"^-\s+(?P<text>.*)$")
_CONTINUATION_RE = re.compile(r"^\s{2,}(?P<text>\S.*)$")

_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_SANS = "DejaVu"
_MONO = "DejaVuMono"
_FONT_FILES: dict[tuple[str, str], str] = {
    (_SANS, ""): "DejaVuSans.ttf",
    (_SANS, "B"): "DejaVuSans-Bold.ttf",
    (_SANS, "I"): "DejaVuSans-Oblique.ttf",
    (_SANS, "BI"): "DejaVuSans-BoldOblique.ttf",
    (_MONO, ""): "DejaVuSansMono.ttf",
    (_MONO, "B"): "DejaVuSansMono-Bold.ttf",
}

_HEADING_PT = {1: 17.0, 2: 13.5, 3: 11.5, 4: 10.5, 5: 10.0, 6: 10.0}
_BODY_PT = 9.5
_TABLE_PT = 7.0
_CAPTION_PT = 8.0
_LINE_MM = 5.0
_TABLE_LINE_MM = 3.8
_BULLET_INDENT_MM = 5.0
#: Column-width heuristic for grids: proportional to the longest cell text,
#: floored so short headings do not wrap and capped so one wordy column does
#: not squeeze the others (long text wraps inside its cell instead).
_MIN_COL_CHARS = 9
_MAX_COL_CHARS = 44


@dataclass
class _Block:
    """One parsed markdown block."""

    kind: str  # heading | paragraph | quote | bullets | table | image
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    path: str = ""


def _plain(text: str) -> str:
    """Drop inline code marks; keep ``**bold**`` for the markdown-aware cells."""
    return text.replace("`", "")


def _cell_text(text: str) -> str:
    return _plain(text).replace("**", "").strip()


def _split_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [_cell_text(c) for c in inner.split("|")]


def parse_markdown(text: str) -> list[_Block]:
    """Parse the report's markdown subset into render blocks.

    Recognised: ATX headings, ``>`` block quotes, ``-`` bullet lists (with
    two-space continuation lines), pipe tables (separator row dropped),
    ``![caption](path)`` images, and paragraphs (consecutive non-empty
    lines joined by a space). Anything else is treated as paragraph text.

    Args:
        text: Markdown source.

    Returns:
        Blocks in document order.
    """
    blocks: list[_Block] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m_head = _HEADING_RE.match(line)
        if m_head:
            blocks.append(
                _Block("heading", text=_plain(m_head["text"]), level=len(m_head["hashes"]))
            )
            i += 1
            continue
        m_img = _IMAGE_RE.match(stripped)
        if m_img:
            blocks.append(_Block("image", text=_plain(m_img["caption"]), path=m_img["path"]))
            i += 1
            continue
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(_Block("quote", text=_plain(" ".join(quote))))
            continue
        if stripped.startswith("|"):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _TABLE_SEP_RE.match(lines[i].strip()):
                    rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(_Block("table", rows=rows))
            continue
        m_bullet = _BULLET_RE.match(line)
        if m_bullet:
            items: list[str] = []
            while i < len(lines):
                m_b = _BULLET_RE.match(lines[i])
                m_c = _CONTINUATION_RE.match(lines[i])
                if m_b:
                    items.append(_plain(m_b["text"].strip()))
                elif m_c and items:
                    items[-1] += " " + _plain(m_c["text"].strip())
                else:
                    break
                i += 1
            blocks.append(_Block("bullets", items=items))
            continue
        para: list[str] = []
        while i < len(lines):
            s = lines[i].strip()
            if not s or s.startswith(("#", "|", ">", "![")) or _BULLET_RE.match(lines[i]):
                break
            para.append(s)
            i += 1
        blocks.append(_Block("paragraph", text=_plain(" ".join(para))))
    return blocks


def _load_pdf_class() -> type[FPDF]:
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise RuntimeError(
            f"PDF output needs fpdf2, which is not installed; install the {PDF_EXTRA} extra"
        ) from exc

    class _ReportPDF(FPDF):
        def footer(self) -> None:
            self.set_y(-12.0)
            self.set_font(_SANS, size=_CAPTION_PT)
            self.cell(0, 6.0, f"Page {self.page_no()}/{{nb}}", align="C")

    return _ReportPDF


def _register_fonts(pdf: FPDF) -> None:
    for (family, style), fname in _FONT_FILES.items():
        path = _FONT_DIR / fname
        if not path.is_file():
            raise RuntimeError(f"font file {path} missing from matplotlib's data directory")
        pdf.add_font(family, style, str(path))


def _png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return int(im.width), int(im.height)


def _render_heading(pdf: FPDF, block: _Block) -> None:
    size = _HEADING_PT.get(block.level, _BODY_PT)
    pdf.ln(2.0 if block.level > 1 else 0.0)
    pdf.set_font(_SANS, style="B", size=size)
    pdf.multi_cell(0, size * 0.55, block.text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def _render_paragraph(pdf: FPDF, text: str, *, italic: bool = False) -> None:
    pdf.set_font(_SANS, style="I" if italic else "", size=_BODY_PT)
    pdf.multi_cell(0, _LINE_MM, text, markdown=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def _render_bullets(pdf: FPDF, items: list[str]) -> None:
    pdf.set_font(_SANS, size=_BODY_PT)
    for item in items:
        x0 = pdf.l_margin
        pdf.set_x(x0)
        pdf.cell(_BULLET_INDENT_MM, _LINE_MM, "•")
        pdf.set_x(x0 + _BULLET_INDENT_MM)
        pdf.multi_cell(
            pdf.epw - _BULLET_INDENT_MM,
            _LINE_MM,
            item,
            markdown=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.ln(1.5)


def _render_table(pdf: FPDF, rows: list[list[str]]) -> None:
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]
    widths = tuple(
        float(min(_MAX_COL_CHARS, max(_MIN_COL_CHARS, max(len(r[c]) for r in padded))))
        for c in range(n_cols)
    )
    pdf.set_font(_MONO, size=_TABLE_PT)
    with pdf.table(
        col_widths=widths,
        text_align="LEFT",
        line_height=_TABLE_LINE_MM,
        padding=0.8,
        markdown=False,
    ) as table:
        for r in padded:
            row = table.row()
            for cell in r:
                row.cell(cell)
    pdf.ln(2.0)


def _render_image(pdf: FPDF, block: _Block, base_dir: Path) -> None:
    path = base_dir / block.path
    if not path.is_file():
        raise FileNotFoundError(f"figure {path} referenced by the report is missing")
    w_px, h_px = _png_size(path)
    width = pdf.epw
    height = width * h_px / w_px
    caption_h = 2.0 * _LINE_MM
    if pdf.will_page_break(height + caption_h):
        pdf.add_page()
    pdf.image(str(path), w=width, h=height)
    pdf.set_font(_SANS, style="I", size=_CAPTION_PT)
    pdf.multi_cell(0, _LINE_MM * 0.8, block.text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2.0)


def render_pdf(markdown_path: str | Path, out_path: str | Path) -> Path:
    """Render a markdown report file to PDF beside its figures.

    Args:
        markdown_path: The markdown report; image paths inside it are
            resolved relative to its directory.
        out_path: Destination ``.pdf`` path.

    Returns:
        ``out_path`` as a :class:`~pathlib.Path`.

    Raises:
        RuntimeError: If fpdf2 (the ``validation[pdf]`` extra) is not
            installed, or a bundled font file is missing.
        FileNotFoundError: If a figure referenced by the markdown is absent.
    """
    md = Path(markdown_path)
    out = Path(out_path)
    pdf_cls = _load_pdf_class()
    pdf = pdf_cls(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18.0)
    pdf.set_margins(left=16.0, top=16.0, right=16.0)
    _register_fonts(pdf)
    pdf.add_page()

    for block in parse_markdown(md.read_text()):
        if block.kind == "heading":
            _render_heading(pdf, block)
        elif block.kind == "paragraph":
            _render_paragraph(pdf, block.text)
        elif block.kind == "quote":
            _render_paragraph(pdf, block.text, italic=True)
        elif block.kind == "bullets":
            _render_bullets(pdf, block.items)
        elif block.kind == "table":
            _render_table(pdf, block.rows)
        elif block.kind == "image":
            _render_image(pdf, block, md.parent)
        else:  # pragma: no cover - parse_markdown emits only the kinds above
            raise RuntimeError(f"unknown block kind {block.kind!r}")

    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    return out

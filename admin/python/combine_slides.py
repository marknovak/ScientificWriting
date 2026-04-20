#!/usr/bin/env python3
"""
combine_slides.py

Combines multiple LaTeX Beamer .tex slide files into a single Beamer
presentation with a table of contents.

Each class's slides are included as a \\part{} in the combined document,
preserving their original \\section{} and \\subsection{} hierarchy.
A table of contents is placed at the start, and automatic section-overview
frames are inserted at every \\section break.

Folder convention expected:
  <classes_dir>/<folder_name>/tex/<folder_name>_slides.tex

The document is compiled automatically with two pdflatex passes (required for
the TOC to resolve correctly).  Pass --no-compile to skip compilation.

Folder list sources (can be combined):
  1. Positional command-line arguments.
  2. A Markdown file passed via --from-file / -f.

Markdown file format:
  Plain bullet:   - lecture1
  Custom title:   - [My Custom Title](lecture1)
  All other lines (headings, blank lines, prose) are silently ignored.

Folder name matching is case-insensitive: "Lecture1", "lecture1", and
"LECTURE1" all resolve to whichever spelling exists on disk.

Usage:
  python combine_slides.py --classes-dir /path/to/classes -f tex2include.md
  python combine_slides.py --classes-dir /path/to/classes -f tex2include.md -o course_slides.tex
  python combine_slides.py --classes-dir /path/to/classes -f tex2include.md --no-compile
"""

import argparse
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Combine slides from multiple Beamer .tex files into a single "
            "Beamer slide set with a native \\tableofcontents. "
            "Each FOLDER is resolved to "
            "<classes-dir>/<folder>/tex/<folder>_slides.tex."
        ),
        epilog=(
            "Markdown file format — each bullet list item names one folder:\n"
            "  Plain bullet:   - lecture1\n"
            "  Custom title:   - [My Custom Title](lecture1)\n\n"
            "Lines that are not bullet items are silently ignored.\n\n"
            "Folder names are matched case-insensitively against the filesystem."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folders",
        nargs="*",
        metavar="FOLDER",
        help="Folder names inside --classes-dir. Optional if --from-file is given.",
    )
    parser.add_argument(
        "--classes-dir",
        required=True,
        metavar="DIR",
        help="Root directory that contains all lecture sub-folders.",
    )
    parser.add_argument(
        "-f", "--from-file",
        metavar="MARKDOWN_FILE",
        help=(
            "Markdown file whose bullet items specify folders in order. "
            "Can be combined with positional FOLDER arguments."
        ),
    )
    parser.add_argument(
        "-o", "--output",
        default="combined_slides.tex",
        metavar="OUTPUT",
        help="Output .tex filename (default: combined_slides.tex).",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Write the .tex file but do not run pdflatex.",
    )
    args = parser.parse_args()

    if not args.folders and not args.from_file:
        parser.error("Provide at least one FOLDER argument or use --from-file / -f.")

    return args


# ---------------------------------------------------------------------------
# Parsing a Markdown file for folder names
# ---------------------------------------------------------------------------

MD_LINK_RE  = re.compile(r'^\s*[-*+]\s+\[([^\]]+)\]\(([^)]+)\)\s*$')
MD_PLAIN_RE = re.compile(r'^\s*[-*+]\s+(\S+)\s*$')


def parse_markdown_file(md_path):
    """
    Return an ordered list of (folder_name, custom_title) tuples from a
    Markdown bullet list.

      - lecture1                   ->  ('lecture1', None)
      - [My Title](lecture1)       ->  ('lecture1', 'My Title')

    All non-bullet lines are ignored.
    """
    if not os.path.isfile(md_path):
        print(f"ERROR: Markdown file '{md_path}' not found.", file=sys.stderr)
        sys.exit(1)

    items = []
    with open(md_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = MD_LINK_RE.match(line)
            if m:
                items.append((m.group(2).strip(), m.group(1).strip()))
                continue
            m = MD_PLAIN_RE.match(line)
            if m:
                items.append((m.group(1).strip(), None))

    if not items:
        print(f"WARNING: No bullet items found in '{md_path}'.", file=sys.stderr)

    return items


# ---------------------------------------------------------------------------
# Case-insensitive folder resolution
# ---------------------------------------------------------------------------

def resolve_folder_name(classes_dir, folder_name):
    """
    Return the actual on-disk directory name inside *classes_dir* that matches
    *folder_name* case-insensitively, or *None* if no match is found.

    When the filesystem is already case-insensitive (macOS HFS+, Windows NTFS)
    this is a no-op safety net.  On case-sensitive filesystems (Linux ext4)
    it allows callers to supply any capitalisation variant.

    If multiple entries happen to differ only in case the first one found
    (alphabetical order) is returned and a warning is printed.
    """
    try:
        entries = os.listdir(classes_dir)
    except OSError as exc:
        print(f"ERROR: Cannot list '{classes_dir}': {exc}", file=sys.stderr)
        sys.exit(1)

    needle = folder_name.lower()
    matches = [e for e in entries
               if e.lower() == needle and os.path.isdir(os.path.join(classes_dir, e))]

    if not matches:
        return None
    if len(matches) > 1:
        print(
            f"WARNING: Multiple directories match '{folder_name}' "
            f"case-insensitively: {matches}. Using '{matches[0]}'.",
            file=sys.stderr,
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Resolving a folder name to its .tex file path
# ---------------------------------------------------------------------------

def resolve_tex_path(classes_dir, folder_name):
    """
    'lecture1'  ->  '<classes_dir>/lecture1/tex/lecture1_slides.tex'

    *folder_name* must already be the real on-disk directory name (use
    resolve_folder_name() first to normalise capitalisation).

    The slide filename itself is resolved case-insensitively so that, e.g.,
    'Collaboration_slides.tex' and 'collaboration_slides.tex' are both
    accepted.
    """
    tex_dir = os.path.join(classes_dir, folder_name, "tex")
    expected = f"{folder_name}_slides.tex"

    if not os.path.isdir(tex_dir):
        return None

    try:
        entries = os.listdir(tex_dir)
    except OSError:
        return None

    needle = expected.lower()
    for entry in entries:
        if entry.lower() == needle and os.path.isfile(os.path.join(tex_dir, entry)):
            return os.path.join(tex_dir, entry)

    return None


# ---------------------------------------------------------------------------
# Parsing a single .tex file
# ---------------------------------------------------------------------------

# Handles all common forms:
#   \section{Title}  \section*{Title}
#   \section[Short]{Long}  \section*[Short]{Long}
SECTION_RE = re.compile(
    r'^\s*\\(section|subsection)(\*)?'
    r'\s*(?:\[[^\]]*\])?'
    r'\s*\{([^}]*)\}'
)


def extract_entries(filepath):
    """
    Return a list of (level, title) tuples ('section' or 'subsection')
    in document order.  Starred variants are normalised to non-starred.
    """
    entries = []
    with open(filepath, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.lstrip().startswith("%"):
                continue
            code = re.sub(r'(?<!\\)%.*$', '', line)
            m = SECTION_RE.match(code)
            if m:
                entries.append((m.group(1), m.group(3).strip()))
    return entries


# ---------------------------------------------------------------------------
# Deriving a display title from a folder name or custom title
# ---------------------------------------------------------------------------

def title_from_folder(folder_name, custom_title=None):
    if custom_title:
        return custom_title
    return folder_name.replace("_", " ").replace("-", " ").title()


# ---------------------------------------------------------------------------
# Extracting preamble extras and body content from individual .tex files
# ---------------------------------------------------------------------------

# Preamble command prefixes that belong to the standard shared Beamer
# template.  Anything else found in the preamble is treated as an "extra".
_STANDARD_CMD_PREFIXES = [
    r'\documentclass',
    r'\usetheme',
    r'\usecolortheme',
    r'\setbeamertemplate',
    r'\beamertemplatenavigationsymbolsempty',
    r'\title',
    r'\subtitle',
    r'\author',
    r'\institute',
    r'\date',
]


def extract_preamble_extras(filepath):
    """
    Scan the preamble (before \\begin{document}) for non-standard lines:
    extra \\usepackage commands, custom \\setbeamerfont calls, etc.

    Return them as a list of raw LaTeX strings.
    Standard template commands (including multi-line \\title, \\author, etc.)
    and the \\AtBeginSection block are excluded.
    """
    extras = []

    # State for skipping multi-line commands / blocks.
    # When skip_mode is True we consume lines until brace_depth returns
    # to zero after having gone positive (seen_open_brace).
    skip_mode = False
    brace_depth = 0
    seen_open_brace = False

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if r'\begin{document}' in line:
                break

            stripped = line.strip()

            # --- Continue skipping a multi-line command/block ---
            if skip_mode:
                opens = stripped.count('{')
                closes = stripped.count('}')
                brace_depth += opens - closes
                if opens > 0:
                    seen_open_brace = True
                if seen_open_brace and brace_depth <= 0:
                    skip_mode = False
                continue

            # Skip blank lines and comments
            if not stripped or stripped.startswith('%'):
                continue

            # --- \AtBeginSection / \AtBeginSubsection block ---
            if stripped.startswith(r'\AtBeginSection') or \
               stripped.startswith(r'\AtBeginSubsection'):
                skip_mode = True
                seen_open_brace = False
                opens = stripped.count('{')
                closes = stripped.count('}')
                brace_depth = opens - closes
                if opens > 0:
                    seen_open_brace = True
                if seen_open_brace and brace_depth <= 0:
                    skip_mode = False
                continue

            # --- Standard preamble command ---
            is_standard = any(
                stripped.startswith(p) for p in _STANDARD_CMD_PREFIXES
            )
            if not is_standard:
                is_standard = bool(
                    re.match(
                        r'\\usepackage\s*\[\s*utf8\s*\]\s*\{inputenc\}',
                        stripped,
                    )
                )

            if is_standard:
                opens = stripped.count('{')
                closes = stripped.count('}')
                depth = opens - closes
                if depth > 0:
                    # Unbalanced braces — argument continues on next line(s)
                    skip_mode = True
                    brace_depth = depth
                    seen_open_brace = True
                elif opens == 0 and re.search(r'\]\s*$', stripped):
                    # e.g. \title[short] or \author[] — the {arg} follows
                    skip_mode = True
                    brace_depth = 0
                    seen_open_brace = False
                # else: command is complete on this line
                continue

            extras.append(line.rstrip())

    return extras


def extract_body_content(filepath):
    """
    Return the document body (between \\begin{document} and \\end{document})
    as a string, with any title-page frame removed.

    Two title-page forms are recognised and stripped:
      \\frame{\\titlepage}
      \\begin{frame} ... \\titlepage ... \\end{frame}
    """
    with open(filepath, encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()

    # Locate body boundaries
    body_start = body_end = None
    for i, line in enumerate(all_lines):
        if r'\begin{document}' in line and body_start is None:
            body_start = i + 1
        elif r'\end{document}' in line:
            body_end = i
            break

    if body_start is None or body_end is None:
        return ""

    body = ''.join(all_lines[body_start:body_end])

    # Remove single-line title page: \frame{\titlepage}
    body = re.sub(r'[ \t]*\\frame\s*\{\s*\\titlepage\s*\}[ \t]*\n?', '', body)

    # Remove multi-line title page frame:
    #   \begin{frame}...
    #     \titlepage
    #   \end{frame}
    body = re.sub(
        r'[ \t]*\\begin\{frame\}[^\n]*\n'
        r'\s*\\titlepage\s*\n'
        r'[ \t]*\\end\{frame\}[ \t]*\n?',
        '',
        body,
    )

    # Remove \AtBeginSection / \AtBeginSubsection blocks from body
    # (the combined preamble provides its own versions).
    # Uses brace-depth tracking because blocks contain nested braces.
    body = _strip_atbegin_blocks(body)

    return body


def _strip_atbegin_blocks(text):
    r"""Remove \AtBeginSection and \AtBeginSubsection{...} blocks."""
    result = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if re.match(r'\\AtBegin(?:Sub)?[Ss]ection\s*(?:\[\s*\])?\s*$', stripped):
            # Found the command line — now skip until its braced block closes
            i += 1
            # Look for the opening '{'
            while i < len(lines) and '{' not in lines[i]:
                i += 1
            if i < len(lines):
                depth = 0
                while i < len(lines):
                    depth += lines[i].count('{') - lines[i].count('}')
                    i += 1
                    if depth <= 0:
                        break
            continue
        result.append(lines[i])
        i += 1
    return '\n'.join(result)


# Regex matching \section or \subsection (incl. starred / short-title forms)
# at the start of a line, used for the hierarchy remap.
_REMAP_RE = re.compile(
    r'^(?P<indent>\s*)'
    r'(?P<cmd>\\(?:sub)?section)(?P<star>\*?)'
    r'(?P<rest>\s*(?:\[[^\]]*\])?\s*\{)',
    re.MULTILINE,
)


def _remap_sections(body):
    r"""
    Demote every \section  -> \subsection
              and \subsection -> \subsubsection
    in *body* (a string of LaTeX source).

    Only lines where the command appears at the start (modulo whitespace)
    are rewritten, so occurrences inside \tableofcontents options or
    comments are left alone.
    """
    def _replace(m):
        indent = m.group('indent')
        cmd    = m.group('cmd')
        star   = m.group('star')
        rest   = m.group('rest')
        if cmd == r'\subsection':
            return f"{indent}\\subsubsection{star}{rest}"
        else:  # \section
            return f"{indent}\\subsection{star}{rest}"

    return _REMAP_RE.sub(_replace, body)


# ---------------------------------------------------------------------------
# Building the combined Beamer document
# ---------------------------------------------------------------------------

def make_preamble(extra_preamble_lines, graphics_dirs):
    """
    Return the Beamer document preamble (through the opening TOC frames).

    *extra_preamble_lines* — deduplicated non-standard preamble lines
        collected from the individual source files.
    *graphics_dirs* — absolute paths to each source file's tex/ directory,
        used to build \\graphicspath so that relative \\includegraphics
        paths resolve correctly.
    """
    parts = [r"""\documentclass{beamer}
\usepackage[utf8]{inputenc}

% ---------------------------------------------------------------
% Combined Beamer slides — generated by combine_slides.py
% ---------------------------------------------------------------

\usetheme{Berkeley}
\usecolortheme{default}

% Hide title/author from the Berkeley sidebar but keep section navigation
\title[]{IB 514: Scientific Writing}
\author[]{Mark Novak}

\setbeamertemplate{footline}[frame number]
\beamertemplatenavigationsymbolsempty

% Sidebar: show only sections (class names); suppress subsections entirely
\makeatletter
\beamer@nav@subsectionstyle{hide}
\makeatother"""]

    # Extra packages / commands from individual source files
    if extra_preamble_lines:
        parts.append("")
        parts.append("% --- Extra packages/settings from individual slide files ---")
        for line in extra_preamble_lines:
            parts.append(line)

    # Graphics search paths
    if graphics_dirs:
        gp_entries = ''.join(f'{{{d}/}}' for d in graphics_dirs)
        parts.append("")
        parts.append(f"\\graphicspath{{{gp_entries}}}")

    parts.append(r"""
\date{\today}

% Auto-generate an overview frame at each \section (class-level)
% sections={\thesection} restricts to the current section only
\AtBeginSection[]
{
  \begin{frame}[allowframebreaks=0.8]
    \frametitle{Overview}
    \tableofcontents[sections={\thesection}, sectionstyle=show, subsectionstyle=show, subsubsectionstyle=show]
  \end{frame}
}

% Auto-generate an overview frame at each \subsection (topic-level)
\AtBeginSubsection[]
{
  \begin{frame}[allowframebreaks=0.8]
    \frametitle{Overview}
    \tableofcontents[sections={\thesection}, sectionstyle=show, subsectionstyle=show/shaded, subsubsectionstyle=show/show/hide]
  \end{frame}
}

\begin{document}

\frame{\titlepage}

\begin{frame}[allowframebreaks=0.8]
  \frametitle{Table of Contents}
  \tableofcontents[hideallsubsections]
\end{frame}

""")

    return '\n'.join(parts)


FOOTER = r"""
\end{document}
"""


def build_body(folder_entries):
    r"""
    Emit the combined slide content.

    Each source file contributes:
      1. A \section{FolderTitle} heading (top-level in the TOC).
      2. A divider frame with the class title.
      3. The file's slide content with section levels demoted:
           \section   -> \subsection
           \subsection -> \subsubsection
    """
    chunks = []

    for folder_name, filepath, entries, custom_title, body_content in folder_entries:
        folder_title = title_from_folder(folder_name, custom_title)

        chunks.append(f"% {'=' * 60}\n")
        chunks.append(f"% {folder_name}\n")
        chunks.append(f"% {'=' * 60}\n\n")

        # Top-level section for this class
        chunks.append(f"\\section{{{folder_title}}}\n\n")

        # Divider frame for this class
        chunks.append("\\begin{frame}\n")
        chunks.append("  \\centering\n")
        chunks.append("  \\vfill\n")
        chunks.append(f"  {{\\Large\\bfseries {folder_title}}}\n")
        chunks.append("  \\vfill\n")
        chunks.append("\\end{frame}\n\n")

        if body_content.strip():
            chunks.append(_remap_sections(body_content))
        else:
            chunks.append(
                f"% NOTE: no body content extracted from "
                f"{os.path.basename(filepath)}\n"
            )

        chunks.append("\n")

    return ''.join(chunks)


def build_document(folder_entries, extra_preamble_lines, graphics_dirs):
    """
    Assemble and return the full combined .tex document string.
    """
    return (
        make_preamble(extra_preamble_lines, graphics_dirs)
        + build_body(folder_entries)
        + FOOTER
    )


# ---------------------------------------------------------------------------
# PDF compilation
# ---------------------------------------------------------------------------

def compile_pdf(tex_path):
    """
    Run pdflatex twice on tex_path (two passes are required for the TOC to
    resolve correctly).  Both runs are performed in the same directory as the
    .tex file so that auxiliary files land alongside it.
    """
    tex_dir  = os.path.dirname(os.path.abspath(tex_path))
    tex_file = os.path.basename(tex_path)

    for pass_num in (1, 2):
        print(f"  pdflatex pass {pass_num}/2 ...")
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_file],
            cwd=tex_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Print the last 30 lines of the log to help diagnose errors
            log_tail = "\n".join(result.stdout.splitlines()[-30:])
            print(
                f"\nERROR: pdflatex failed on pass {pass_num}.\n"
                f"--- last 30 lines of output ---\n{log_tail}",
                file=sys.stderr,
            )
            sys.exit(1)

    pdf_path = os.path.splitext(os.path.abspath(tex_path))[0] + ".pdf"
    print(f"  PDF written to: {pdf_path}")
    return pdf_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    classes_dir = os.path.expanduser(os.path.abspath(args.classes_dir))
    if not os.path.isdir(classes_dir):
        print(
            f"ERROR: --classes-dir '{classes_dir}' is not a valid directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Build ordered list of (folder_name, custom_title) pairs
    # ------------------------------------------------------------------
    folder_specs = []

    if args.from_file:
        md_path = os.path.expanduser(os.path.abspath(args.from_file))
        md_items = parse_markdown_file(md_path)
        print(f"Read {len(md_items)} folder(s) from '{args.from_file}'")
        folder_specs.extend(md_items)

    for folder_name in args.folders:
        folder_specs.append((folder_name, None))

    # ------------------------------------------------------------------
    # Resolve each folder spec to a .tex file and parse it
    # ------------------------------------------------------------------
    folder_entries = []   # (folder_name, filepath, entries, custom_title, body_content)
    all_extra_preamble = []
    graphics_dirs = []

    for raw_name, custom_title in folder_specs:
        # --- Case-insensitive lookup: find the real on-disk folder name ---
        actual_name = resolve_folder_name(classes_dir, raw_name)
        if actual_name is None:
            print(
                f"WARNING: '{raw_name}' -> no matching directory found in "
                f"'{classes_dir}' (case-insensitive) — skipping.",
                file=sys.stderr,
            )
            continue
        if actual_name != raw_name:
            print(
                f"Note: '{raw_name}' matched on-disk folder '{actual_name}' "
                f"(case-insensitive)."
            )

        filepath = resolve_tex_path(classes_dir, actual_name)

        if filepath is None:
            print(
                f"WARNING: '{actual_name}' -> expected slide file not found in "
                f"'{os.path.join(classes_dir, actual_name, 'tex')}' "
                f"(case-insensitive match for '{actual_name}_slides.tex') — skipping.",
                file=sys.stderr,
            )
            continue

        print(f"Parsing: {filepath}")
        entries = extract_entries(filepath)

        if not entries:
            print(f"  (no \\section/\\subsection entries found in"
                  f" {os.path.basename(filepath)})")
        else:
            sec    = sum(1 for lvl, _ in entries if lvl == "section")
            subsec = sum(1 for lvl, _ in entries if lvl == "subsection")
            print(f"  found {sec} section(s), {subsec} subsection(s)")

        body_content = extract_body_content(filepath)
        extras = extract_preamble_extras(filepath)
        all_extra_preamble.extend(extras)

        tex_dir = os.path.dirname(os.path.abspath(filepath))
        if tex_dir not in graphics_dirs:
            graphics_dirs.append(tex_dir)

        folder_entries.append(
            (actual_name, filepath, entries, custom_title, body_content)
        )

    if not folder_entries:
        print("No valid input files found. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate extra preamble lines while preserving order
    all_extra_preamble = list(dict.fromkeys(all_extra_preamble))

    # ------------------------------------------------------------------
    # Write the .tex file
    # ------------------------------------------------------------------
    document = build_document(folder_entries, all_extra_preamble, graphics_dirs)

    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(document)
    print(f"\nTeX file written to: {output_path}")

    # ------------------------------------------------------------------
    # Compile to PDF (two pdflatex passes)
    # ------------------------------------------------------------------
    if args.no_compile:
        print("Skipping compilation (--no-compile).  To compile manually:")
        print(f"  pdflatex {args.output} && pdflatex {args.output}")
    else:
        print("Compiling PDF ...")
        compile_pdf(output_path)


if __name__ == "__main__":
    main()
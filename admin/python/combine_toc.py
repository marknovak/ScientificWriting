#!/usr/bin/env python3
"""
combine_toc.py

Extracts \section and \subsection commands from a list of LaTeX Beamer .tex files
and produces a new standalone Beamer .tex file with a combined table of contents.

Folder convention expected:
  <classes_dir>/<folder_name>/tex/<folder_name>_slides.tex

  e.g. with --classes-dir /home/user/classes:
    lecture1  ->  /home/user/classes/lecture1/tex/lecture1_slides.tex
    lecture2  ->  /home/user/classes/lecture2/tex/lecture2_slides.tex

Hierarchy remapping:
  - Each input file becomes a bold chapter-level heading.
  - \section{} in source    -> bullet item (section level).
  - \subsection{} in source -> indented sub-item (subsection level).

Folder list sources (can be combined):
  1. Positional command-line arguments.
  2. A Markdown file passed via --from-file / -f.

Markdown file format:
  Plain bullet:   - lecture1
  Custom title:   - [My Custom Title](lecture1)
  Lines that are not bullet items are ignored (headings, blank lines, prose).

Usage:
  # From command-line arguments only:
  python combine_toc.py --classes-dir /path/to/classes lecture1 lecture2

  # From a markdown file only:
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md

  # Both (file list comes first, then positional arguments are appended):
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md lecture3

  # With custom output filename:
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md -o syllabus_toc.tex
"""

import argparse
import os
import re
import sys


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Combine TOCs from multiple Beamer .tex files into one. "
            "Each FOLDER argument is resolved to "
            "<classes-dir>/<folder>/tex/<folder>_slides.tex. "
            "Folders can be supplied as positional arguments, via a Markdown "
            "file (--from-file), or both."
        ),
        epilog=(
            "Markdown file format — each bullet list item names one folder:\n"
            "  Plain bullet:   - lecture1\n"
            "  Custom title:   - [My Custom Title](lecture1)\n\n"
            "Lines that are not bullet items (headings, blank lines, prose)\n"
            "are silently ignored."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folders",
        nargs="*",                  # 0 or more — may come from --from-file instead
        metavar="FOLDER",
        help=(
            "Folder names inside --classes-dir to process "
            "(e.g. 'lecture1' 'lecture2'). Optional if --from-file is given."
        ),
    )
    parser.add_argument(
        "--classes-dir",
        required=True,
        metavar="DIR",
        help="Path to the root 'classes' directory that contains all lecture folders.",
    )
    parser.add_argument(
        "-f", "--from-file",
        metavar="MARKDOWN_FILE",
        help=(
            "Path to a Markdown file whose bullet-list items specify the folders "
            "to include, in order. Can be combined with positional FOLDER arguments; "
            "positional folders are appended after the file list."
        ),
    )
    parser.add_argument(
        "-o", "--output",
        default="combined_toc.tex",
        metavar="OUTPUT",
        help="Output .tex filename (default: combined_toc.tex).",
    )
    args = parser.parse_args()

    # Must have at least one source of folder names
    if not args.folders and not args.from_file:
        parser.error("Provide at least one FOLDER argument or use --from-file / -f.")

    return args


# ---------------------------------------------------------------------------
# Parsing a Markdown file for folder names
# ---------------------------------------------------------------------------

# Matches a Markdown bullet item in two forms:
#   - [Custom Title](folder_name)   ->  groups: (title, folder_name)
#   - folder_name                   ->  groups: (None,  folder_name)
# The bullet marker can be -, *, or +.
MD_LINK_RE  = re.compile(r'^\s*[-*+]\s+\[([^\]]+)\]\(([^)]+)\)\s*$')
MD_PLAIN_RE = re.compile(r'^\s*[-*+]\s+(\S+)\s*$')


def parse_markdown_file(md_path):
    """
    Read a Markdown file and return an ordered list of (folder_name, custom_title)
    tuples extracted from its bullet-list items.

    custom_title is None when the item is a plain bullet (no link syntax).

    Supported item forms:
      - lecture1                        ->  ('lecture1', None)
      - [Introduction](lecture1)        ->  ('lecture1', 'Introduction')

    All other lines (headings, blank lines, prose, code blocks) are ignored.
    """
    if not os.path.isfile(md_path):
        print(f"ERROR: Markdown file '{md_path}' not found.", file=sys.stderr)
        sys.exit(1)

    items = []
    with open(md_path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            # Try linked form first: - [Title](folder)
            m = MD_LINK_RE.match(line)
            if m:
                custom_title = m.group(1).strip()
                folder_name  = m.group(2).strip()
                items.append((folder_name, custom_title))
                continue

            # Try plain bullet: - folder_name
            m = MD_PLAIN_RE.match(line)
            if m:
                folder_name = m.group(1).strip()
                items.append((folder_name, None))

    if not items:
        print(
            f"WARNING: No bullet-list items found in '{md_path}'.",
            file=sys.stderr,
        )

    return items


# ---------------------------------------------------------------------------
# Resolving a folder name to its .tex file path
# ---------------------------------------------------------------------------

def resolve_tex_path(classes_dir, folder_name):
    """
    Given:
      classes_dir  = '/home/user/classes'
      folder_name  = 'lecture1'

    Returns:
      '/home/user/classes/lecture1/tex/lecture1_slides.tex'
    """
    return os.path.join(classes_dir, folder_name, "tex", f"{folder_name}_slides.tex")


# ---------------------------------------------------------------------------
# Parsing a single .tex file
# ---------------------------------------------------------------------------

# Matches \section and \subsection in all common forms:
#   \section{Title}
#   \section*{Title}
#   \section[Short title]{Long Title}   <- optional [...] is discarded
#   \section*[Short]{Long}
#
# Capture groups:
#   1 - command type: 'section' or 'subsection'
#   2 - optional star: '*' or None
#   3 - main title in {...}
SECTION_RE = re.compile(
    r'^\s*\\(section|subsection)(\*)?'   # command + optional star
    r'\s*(?:\[[^\]]*\])?'                # optional [short title] — discarded
    r'\s*\{([^}]*)\}'                    # mandatory {main title} — captured
)


def extract_entries(filepath):
    """
    Parse a Beamer .tex file and return a list of (level, title) tuples where
    level is 'section' or 'subsection', preserving document order.

    Starred variants (\section*) are normalised to non-starred equivalents.
    Returns an empty list if the file contains no relevant commands.
    """
    entries = []

    with open(filepath, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # Skip fully commented-out lines
            if line.lstrip().startswith("%"):
                continue

            # Strip inline comments (% not preceded by \)
            code = re.sub(r'(?<!\\)%.*$', '', line)

            m = SECTION_RE.match(code)
            if m:
                level = m.group(1)          # 'section' or 'subsection'
                # group 2 is '*' or None — we normalise both to non-starred
                title = m.group(3).strip()
                entries.append((level, title))

    return entries


# ---------------------------------------------------------------------------
# Deriving a human-readable part title from a folder name or custom title
# ---------------------------------------------------------------------------

def title_from_folder(folder_name, custom_title=None):
    """
    Return the display title for a chapter heading.

    If a custom_title is provided (from Markdown link syntax), use it directly.
    Otherwise derive a title from the folder name:
      'my_lecture_01'  ->  'My Lecture 01'
      'linear-algebra' ->  'Linear Algebra'
    """
    if custom_title:
        return custom_title
    return folder_name.replace("_", " ").replace("-", " ").title()


# ---------------------------------------------------------------------------
# Building the combined .tex document
# ---------------------------------------------------------------------------

PREAMBLE = r"""\documentclass{beamer}

% ---------------------------------------------------------------
% Combined Table of Contents
% Generated automatically by combine_toc.py
% ---------------------------------------------------------------

\usepackage{enumitem}   % fine-grained itemize control
\usepackage{multicol}   % two-column TOC layout

\usetheme{Berkeley}
\usecolortheme{default}
\setbeamertemplate{navigation symbols}{}  % hide nav icons

\title{IB 514: Scientific Writing}
\subtitle{Combined Table of Contents}
\date{}
\author[]{Mark Novak}

\institute[]
{
  \inst{}
  Dept. of Integrative Biology\\
  Oregon State University
}
\date{}

\begin{document}

\maketitle

% ---------------------------------------------------------------
% NOTE: The TOC is built as a nested itemize rather than via
% Beamer's \tableofcontents.  This avoids two Beamer limitations:
%   (1) \tableofcontents only shows the *current* \part's sections
%       by default, hiding every file except the last one.
%   (2) Subsections are not displayed unless special options are set.
% ---------------------------------------------------------------
"""

FOOTER = r"""
\end{document}
"""


def render_entries_for_column(folder_entries_subset, indent="  "):
    """
    Render the itemize block for a single column given a subset of
    folder_entries.  Returns a list of lines.

    Visual hierarchy:
      chapter (file)  ->  bold large heading, no bullet
      section         ->  filled bullet  •
      subsection      ->  en-dash  --  (indented)
    """
    i = indent
    lines = []

    lines.append(f"{i}\\begin{{itemize}}[leftmargin=0pt, label={{}}, itemsep=0.6em]\n\n")

    for folder_name, filepath, entries, custom_title in folder_entries_subset:
        chapter_title = title_from_folder(folder_name, custom_title)

        lines.append(f"{i}  % ----- {folder_name} -----\n")
        lines.append(f"{i}  \\item \\textbf{{\\large {chapter_title}}}\n")

        if not entries:
            lines.append(
                f"{i}  % NOTE: no sections found in {os.path.basename(filepath)}\n\n"
            )
            continue

        lines.append(f"{i}  \\begin{{itemize}}[leftmargin=1.2em,"
                     f" label={{$\\bullet$}}, itemsep=0.15em]\n")

        in_subsection_block = False

        for level, title in entries:
            if level == "section":
                if in_subsection_block:
                    lines.append(f"{i}      \\end{{itemize}}\n")
                    in_subsection_block = False
                lines.append(f"{i}    \\item {title}\n")
            else:  # subsection
                if not in_subsection_block:
                    lines.append(
                        f"{i}      \\begin{{itemize}}[leftmargin=1.2em,"
                        f" label={{--}}, itemsep=0.1em]\n"
                    )
                    in_subsection_block = True
                lines.append(f"{i}        \\item {title}\n")

        if in_subsection_block:
            lines.append(f"{i}      \\end{{itemize}}\n")

        lines.append(f"{i}  \\end{{itemize}}\n\n")

    lines.append(f"{i}\\end{{itemize}}\n")
    return lines


def build_toc_frame(folder_entries):
    """
    Build the TOC frame with entries arranged in two side-by-side columns.

    The folder list is split as evenly as possible by total entry count so
    that both columns have roughly the same visual weight.  Each column is
    rendered as a nested itemize inside a multicols environment.

    Note: allowframebreaks is intentionally omitted here because it is
    incompatible with the multicols environment.  If the TOC overflows a
    single slide, reduce \\itemsep values or adjust the font size with
    \\small / \\footnotesize inside each column.
    """
    # -----------------------------------------------------------------
    # Split folder_entries into two halves with balanced entry counts
    # -----------------------------------------------------------------
    total_entries = sum(len(e) for _, _, e, _ in folder_entries)
    half = total_entries / 2

    left, right = [], []
    running = 0
    for item in folder_entries:
        if running < half:
            left.append(item)
        else:
            right.append(item)
        running += len(item[2])

    # Ensure neither column is empty (e.g. only one folder provided)
    if not right and left:
        mid = max(1, len(left) // 2)
        left, right = left[:mid], left[mid:]

    lines = []
    lines.append("\\begin{frame}{Table of Contents}\n")
    lines.append("  \\begin{multicols}{2}\n")
    lines.append("    \\small  % reduce font size if needed to fit the frame\n\n")

    lines.extend(render_entries_for_column(left,  indent="    "))
    lines.append("\n    \\columnbreak\n\n")
    lines.extend(render_entries_for_column(right, indent="    "))

    lines.append("  \\end{multicols}\n")
    lines.append("\\end{frame}\n")
    return "".join(lines)


def build_document(folder_entries):
    """
    Assemble the full .tex document string.
    """
    return PREAMBLE + "\n" + build_toc_frame(folder_entries) + FOOTER


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Normalise the classes directory path
    classes_dir = os.path.expanduser(os.path.abspath(args.classes_dir))

    if not os.path.isdir(classes_dir):
        print(
            f"ERROR: --classes-dir '{classes_dir}' is not a valid directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Build the ordered list of (folder_name, custom_title) pairs.
    # Markdown file entries come first, then positional arguments.
    # ------------------------------------------------------------------
    folder_specs = []   # list of (folder_name, custom_title | None)

    if args.from_file:
        md_path = os.path.expanduser(os.path.abspath(args.from_file))
        md_items = parse_markdown_file(md_path)
        print(f"Read {len(md_items)} folder(s) from '{args.from_file}'")
        folder_specs.extend(md_items)

    # Positional arguments have no custom title
    for folder_name in args.folders:
        folder_specs.append((folder_name, None))

    # ------------------------------------------------------------------
    # Resolve each folder spec to a .tex file and parse it
    # ------------------------------------------------------------------
    folder_entries = []   # (folder_name, filepath, entries, custom_title)

    for folder_name, custom_title in folder_specs:
        filepath = resolve_tex_path(classes_dir, folder_name)

        if not os.path.isfile(filepath):
            print(
                f"WARNING: '{folder_name}' -> expected file not found:"
                f" {filepath} — skipping.",
                file=sys.stderr,
            )
            continue

        print(f"Parsing: {filepath}")
        entries = extract_entries(filepath)

        if not entries:
            print(
                f"  (no \\section/\\subsection entries found"
                f" in {os.path.basename(filepath)})"
            )
        else:
            section_count    = sum(1 for lvl, _ in entries if lvl == "section")
            subsection_count = sum(1 for lvl, _ in entries if lvl == "subsection")
            print(f"  found {section_count} section(s),"
                  f" {subsection_count} subsection(s)")

        folder_entries.append((folder_name, filepath, entries, custom_title))

    if not folder_entries:
        print("No valid input files found. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Build and write the combined .tex document
    document = build_document(folder_entries)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(document)

    print(f"\nDone! Combined TOC written to: {args.output}")
    print(f"Compile with:  pdflatex {args.output}")


if __name__ == "__main__":
    main()
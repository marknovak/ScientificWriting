#!/usr/bin/env python3
"""
combine_toc.py

Extracts \\section and \\subsection commands from a list of LaTeX Beamer .tex
files and produces a standalone LaTeX article with a combined table of contents.

Folder convention expected:
  <classes_dir>/<folder_name>/tex/<folder_name>_slides.tex

Hierarchy remapping into the article document:
  source file      ->  \\section{}       (numbered 1, 2, 3 ...)
  \\section{}      ->  \\subsection{}    (numbered 1.1, 1.2 ...)
  \\subsection{}   ->  \\subsubsection{} (numbered 1.1.1, 1.1.2 ...)

LaTeX's native \\tableofcontents then produces correctly numbered output.
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
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md -o syllabus_toc.tex
  python combine_toc.py --classes-dir /path/to/classes -f lectures.md --no-compile
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
            "Combine TOCs from multiple Beamer .tex files into a single "
            "LaTeX article with a native \\tableofcontents. "
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
        default="combined_toc.tex",
        metavar="OUTPUT",
        help="Output .tex filename (default: combined_toc.tex).",
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
# Building the LaTeX article document
# ---------------------------------------------------------------------------

def make_preamble():
    """
    Return the document preamble as a string.

    Key choices:
      - 'article' class with 'tocloft' for TOC formatting control.
      - 'titlesec' to style \\section headings as bold chapter-like titles.
      - 'hyperref' for a clickable, bookmarked PDF TOC.
      - Sections are numbered but \\section entries in the TOC are styled to
        look like chapter headings (bold, slightly larger).
    """
    return r"""\documentclass[12pt]{article}

% ---------------------------------------------------------------
% Combined Table of Contents — generated by combine_toc.py
% ---------------------------------------------------------------

\usepackage[margin=2.5cm]{geometry}
\usepackage{tocloft}       % fine-grained TOC formatting
\usepackage{titlesec}      % custom section heading styles
\usepackage{parskip}       % space between paragraphs, no indent
\usepackage[hidelinks]{hyperref}  % clickable TOC entries, no coloured boxes

% --- TOC formatting ---
% Make \section entries in the TOC appear bold (chapter-like)
\renewcommand{\cftsecfont}{\bfseries\large}
\renewcommand{\cftsecpagefont}{\bfseries\large}
\renewcommand{\cftsecleader}{\cftdotfill{\cftdotsep}}
\setlength{\cftbeforesecskip}{6pt}   % extra space above each folder entry

% Subsection entries: normal weight, slightly indented
\setlength{\cftsubsecindent}{1.8em}
\setlength{\cftbeforesubsecskip}{1pt}

% Subsubsection entries: normal weight, more indented
\setlength{\cftsubsubsecindent}{3.6em}
\setlength{\cftbeforesubsubsecskip}{0pt}

% --- Section heading styles ---
% \section  -> bold, large  (represents a source file / folder)
\titleformat{\section}{\bfseries\large}{\thesection.}{0.6em}{}
% \subsection -> normal bold  (represents an original \section)
\titleformat{\subsection}{\bfseries\normalsize}{\thesubsection}{0.6em}{}
% \subsubsection -> italic  (represents an original \subsection)
\titleformat{\subsubsection}{\itshape\normalsize}{\thesubsubsection}{0.6em}{}

% Hide section headings from the page body — we only want the TOC,
% not the actual headings typeset in the document.
\setcounter{secnumdepth}{3}   % number down to subsubsection
\setcounter{tocdepth}{3}      % show down to subsubsection in TOC

\title{\textbf{IB 514: Scientific Writing}}
\author{Mark Novak \\
  \small Dept.\ of Integrative Biology, Oregon State University}
\date{}

\begin{document}

\maketitle
\thispagestyle{empty}

\tableofcontents

% ---------------------------------------------------------------
% The sectioning commands below are never rendered as body text --
% they exist solely to populate the TOC.  Each source file becomes
% a \\section, its \\section entries become \\subsection, and its
% \\subsection entries become \\subsubsection.
% ---------------------------------------------------------------

"""


FOOTER = r"""
\end{document}
"""


def build_body(folder_entries):
    """
    Emit the \\section / \\subsection / \\subsubsection commands that
    populate the TOC.  No body text is written — only the heading commands
    needed for LaTeX to build the TOC.
    """
    lines = []

    for folder_name, filepath, entries, custom_title in folder_entries:
        folder_title = title_from_folder(folder_name, custom_title)

        lines.append(f"% ----- {folder_name} -----\n")

        # Source file  ->  \section
        lines.append(f"\\section{{{folder_title}}}\n")

        if not entries:
            lines.append(
                f"% NOTE: no \\section/\\subsection entries found"
                f" in {os.path.basename(filepath)}\n"
            )

        for level, title in entries:
            if level == "section":
                # Original \section  ->  \subsection
                lines.append(f"\\subsection{{{title}}}\n")
            else:
                # Original \subsection  ->  \subsubsection
                lines.append(f"\\subsubsection{{{title}}}\n")

        lines.append("\n")

    return "".join(lines)


def build_document(folder_entries):
    """
    Assemble and return the full .tex document string.
    """
    return (
        make_preamble()
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
    folder_entries = []   # (folder_name, filepath, entries, custom_title)

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

        folder_entries.append((actual_name, filepath, entries, custom_title))

    if not folder_entries:
        print("No valid input files found. Exiting.", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Write the .tex file
    # ------------------------------------------------------------------
    document = build_document(folder_entries)

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
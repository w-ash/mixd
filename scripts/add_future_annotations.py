#!/usr/bin/env python3
"""Script to add 'from __future__ import annotations' to all Python files.

Adds the import after the module docstring (if present) or at the top of the file.
Skips files that already have the import.
"""

import ast
from pathlib import Path
import sys


def has_future_annotations(content: str) -> bool:
    """Check if file already has 'from __future__ import annotations'."""
    return "from __future__ import annotations" in content


def get_insertion_line(content: str) -> int:
    """Get the line number where we should insert the future import.

    Returns line number (0-indexed) where the import should be inserted.
    This is either after the module docstring or at the start.
    """
    try:
        tree = ast.parse(content)

        # Check if there's a module docstring
        if (tree.body and
            isinstance(tree.body[0], ast.Expr) and
            isinstance(tree.body[0].value, ast.Constant) and
            isinstance(tree.body[0].value.value, str)):
            # Insert after the docstring
            docstring_node = tree.body[0]
            return docstring_node.end_lineno  # Line after docstring ends
        else:
            # No docstring, insert at the top
            return 0

    except SyntaxError:
        # If we can't parse, insert at top
        return 0


def add_future_annotations(file_path: Path, dry_run: bool = False) -> bool:
    """Add 'from __future__ import annotations' to a Python file.

    Args:
        file_path: Path to the Python file
        dry_run: If True, don't actually modify files

    Returns:
        True if file was modified (or would be in dry run), False otherwise
    """
    content = file_path.read_text()

    # Skip if already has the import
    if has_future_annotations(content):
        return False

    lines = content.splitlines(keepends=True)
    insertion_line = get_insertion_line(content)

    # Build the new content
    if insertion_line == 0:
        # Insert at the very top (no docstring)
        new_lines = ["from __future__ import annotations\n", "\n"] + lines
    else:
        # Insert after docstring
        new_lines = (
            lines[:insertion_line] +
            ["\n", "from __future__ import annotations\n"] +
            lines[insertion_line:]
        )

    new_content = "".join(new_lines)

    if not dry_run:
        file_path.write_text(new_content)

    return True


def main():
    """Process all Python files in src/ directory."""
    src_dir = Path(__file__).parent.parent / "src"

    if not src_dir.exists():
        print(f"Error: {src_dir} does not exist")
        sys.exit(1)

    # Find all Python files
    python_files = list(src_dir.rglob("*.py"))

    print(f"Found {len(python_files)} Python files in {src_dir}")

    # First, do a dry run to see what would change
    print("\n=== DRY RUN ===")
    to_modify = []
    for py_file in python_files:
        if add_future_annotations(py_file, dry_run=True):
            to_modify.append(py_file)
            print(f"Would modify: {py_file.relative_to(src_dir.parent)}")

    if not to_modify:
        print("\nNo files need modification. All files already have future annotations!")
        return

    print(f"\n{len(to_modify)} files would be modified")
    print(f"{len(python_files) - len(to_modify)} files already have the import")

    # Ask for confirmation
    response = input("\nProceed with modifications? [y/N]: ")
    if response.lower() != 'y':
        print("Aborted.")
        return

    # Actually modify files
    print("\n=== MODIFYING FILES ===")
    modified_count = 0
    for py_file in to_modify:
        if add_future_annotations(py_file, dry_run=False):
            modified_count += 1
            print(f"Modified: {py_file.relative_to(src_dir.parent)}")

    print(f"\n✅ Successfully modified {modified_count} files")
    print("\nNext steps:")
    print("1. Run: poetry run basedpyright src/")
    print("2. Run: poetry run pytest tests/unit/")
    print("3. Review changes with: git diff")


if __name__ == "__main__":
    main()

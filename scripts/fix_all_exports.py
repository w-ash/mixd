#!/usr/bin/env python3
"""Script to fix __all__ lists that had quotes incorrectly removed.

The previous script removed quotes from __all__ exports, but these
must remain strings. This script adds them back.
"""

from pathlib import Path
import re
import sys


def fix_all_list(content: str) -> tuple[str, int]:
    """Fix __all__ list items by ensuring they're quoted.

    Args:
        content: File content

    Returns:
        Tuple of (modified content, number of changes made)
    """
    changes = 0

    # Find __all__ = [...] blocks
    # Pattern to match unquoted identifiers in __all__ lists
    def replace_unquoted_in_all(match):
        nonlocal changes
        all_content = match.group(1)

        # Replace unquoted identifiers (must start with letter or underscore)
        # But preserve already-quoted strings
        def quote_identifier(m):
            nonlocal changes
            identifier = m.group(0)
            changes += 1
            return f'"{identifier}"'

        # Match identifiers that aren't already quoted
        # Negative lookbehind/ahead to avoid already quoted strings
        fixed = re.sub(
            r'(?<!["\'])([A-Za-z_][A-Za-z0-9_]*)(?!["\'])',
            quote_identifier,
            all_content,
        )

        return f"__all__ = [{fixed}]"

    # Match __all__ = [...]
    pattern = r"__all__\s*=\s*\[(.*?)\]"
    new_content = re.sub(pattern, replace_unquoted_in_all, content, flags=re.DOTALL)

    return new_content, changes


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """Process a single Python file.

    Args:
        file_path: Path to Python file
        dry_run: If True, don't modify files

    Returns:
        Number of changes made
    """
    content = file_path.read_text()
    new_content, count = fix_all_list(content)

    if count > 0:
        if not dry_run:
            file_path.write_text(new_content)
        return count

    return 0


def main():
    """Process all Python files in src/ directory."""
    src_dir = Path(__file__).parent.parent / "src"

    if not src_dir.exists():
        print(f"Error: {src_dir} does not exist")
        sys.exit(1)

    # Find all Python files
    python_files = list(src_dir.rglob("*.py"))

    print(f"Found {len(python_files)} Python files in {src_dir}")

    # First, do a dry run
    print("\n=== DRY RUN ===")
    total_changes = 0
    files_to_modify = []

    for py_file in python_files:
        count = process_file(py_file, dry_run=True)
        if count > 0:
            files_to_modify.append((py_file, count))
            total_changes += count
            print(f"{py_file.relative_to(src_dir.parent)}: {count} unquoted exports")

    if total_changes == 0:
        print("\nNo unquoted __all__ exports found!")
        return

    print(f"\nTotal: {total_changes} unquoted exports in {len(files_to_modify)} files")

    # Ask for confirmation
    response = input("\nProceed with modifications? [y/N]: ")
    if response.lower() != "y":
        print("Aborted.")
        return

    # Actually modify files
    print("\n=== MODIFYING FILES ===")
    total_fixed = 0
    for py_file, _ in files_to_modify:
        count = process_file(py_file, dry_run=False)
        total_fixed += count
        print(f"Fixed {count:2d} in {py_file.relative_to(src_dir.parent)}")

    print(f"\n✅ Successfully fixed {total_fixed} __all__ exports!")
    print("\nNext steps:")
    print("1. Run: poetry run pytest tests/unit/")


if __name__ == "__main__":
    main()

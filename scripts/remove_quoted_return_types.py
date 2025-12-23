#!/usr/bin/env python3
"""Script to remove quoted return type annotations.

With PEP 649 (from __future__ import annotations), we no longer need quotes
around forward references in return types.

Example:
    def with_id(self, db_id: int) -> "Track":
    # Becomes:
    def with_id(self, db_id: int) -> Track:
"""

import re
from pathlib import Path
import sys


def remove_quoted_return_types(content: str) -> tuple[str, int]:
    """Remove quotes from return type annotations.

    Args:
        content: File content

    Returns:
        Tuple of (modified content, number of changes made)
    """
    # Pattern to match -> "TypeName" in function signatures
    # Matches: -> "SomeType" with optional whitespace and colon after
    pattern = r'(\s+->\s*)"([A-Z][A-Za-z0-9_]+)"(\s*:)'

    def replacer(match):
        return f'{match.group(1)}{match.group(2)}{match.group(3)}'

    new_content, count = re.subn(pattern, replacer, content)
    return new_content, count


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """Process a single Python file.

    Args:
        file_path: Path to Python file
        dry_run: If True, don't modify files

    Returns:
        Number of changes made
    """
    content = file_path.read_text()
    new_content, count = remove_quoted_return_types(content)

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
            print(f"{py_file.relative_to(src_dir.parent)}: {count} quoted types")

    if total_changes == 0:
        print("\nNo quoted return types found!")
        return

    print(f"\nTotal: {total_changes} quoted return types in {len(files_to_modify)} files")

    # Ask for confirmation
    response = input("\nProceed with modifications? [y/N]: ")
    if response.lower() != 'y':
        print("Aborted.")
        return

    # Actually modify files
    print("\n=== MODIFYING FILES ===")
    total_fixed = 0
    for py_file, _ in files_to_modify:
        count = process_file(py_file, dry_run=False)
        total_fixed += count
        print(f"Fixed {count:2d} in {py_file.relative_to(src_dir.parent)}")

    print(f"\n✅ Successfully removed {total_fixed} quoted return types!")
    print("\nNext steps:")
    print("1. Run: poetry run basedpyright src/")
    print("2. Run: poetry run pytest tests/unit/")


if __name__ == "__main__":
    main()

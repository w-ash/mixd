#!/usr/bin/env python3
"""Script to remove ALL quoted type annotations.

With PEP 649, we no longer need quotes around forward references anywhere.
This includes:
- Return types: -> "Type"
- Generic types: Awaitable["Type"], list["Type"], dict[str, "Type"]
- Parameter types: param: "Type"
"""

from pathlib import Path
import re
import sys


def remove_all_quoted_types(content: str) -> tuple[str, int]:
    """Remove quotes from all type annotations.

    Args:
        content: File content

    Returns:
        Tuple of (modified content, number of changes made)
    """
    total_changes = 0

    # Pattern 1: Quoted types in generic brackets: Awaitable["Type"], list["Type"], etc.
    # Matches: ["TypeName"] inside brackets
    pattern1 = r'\["([A-Z][A-Za-z0-9_]+)"\]'
    content, count1 = re.subn(pattern1, r"[\1]", content)
    total_changes += count1

    # Pattern 2: Quoted types in function parameters: param: "Type"
    # Matches: : "TypeName" (colon space quote)
    pattern2 = r'(:\s*)"([A-Z][A-Za-z0-9_]+)"'
    content, count2 = re.subn(pattern2, r"\1\2", content)
    total_changes += count2

    # Pattern 3: Quoted types in dict/other generics: dict[str, "Type"]
    # Matches: , "TypeName" inside brackets
    pattern3 = r'(,\s*)"([A-Z][A-Za-z0-9_]+)"(\s*[\]\),])'
    content, count3 = re.subn(pattern3, r"\1\2\3", content)
    total_changes += count3

    return content, total_changes


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """Process a single Python file.

    Args:
        file_path: Path to Python file
        dry_run: If True, don't modify files

    Returns:
        Number of changes made
    """
    content = file_path.read_text()
    new_content, count = remove_all_quoted_types(content)

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
        print("\nNo quoted types found!")
        return

    print(f"\nTotal: {total_changes} quoted types in {len(files_to_modify)} files")

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

    print(f"\n✅ Successfully removed {total_fixed} quoted types!")
    print("\nNext steps:")
    print("1. Run: poetry run basedpyright src/")
    print("2. Run: poetry run pytest tests/unit/")


if __name__ == "__main__":
    main()

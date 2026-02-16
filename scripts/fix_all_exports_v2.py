#!/usr/bin/env python3
"""Fix __all__ exports by properly re-quoting unquoted identifiers.

This script fixes the issue where remove_all_quoted_types.py inadvertently
removed quotes from __all__ export lists, breaking Python syntax.
"""

from pathlib import Path
import re


def fix_all_exports_in_file(file_path: Path) -> bool:
    """Fix __all__ exports in a single file by re-quoting unquoted identifiers.

    Returns True if file was modified, False otherwise.
    """
    content = file_path.read_text()

    # Find __all__ assignment using regex to locate it
    # Pattern: __all__ = [ ... ]
    pattern = r"(__all__\s*=\s*\[)(.*?)(\])"

    def fix_all_list(match):
        prefix = match.group(1)  # '__all__ = ['
        list_content = match.group(2)  # The content between brackets
        suffix = match.group(3)  # ']'

        # Split by commas, but be careful with already-quoted strings
        # Use a simple approach: find all items (quoted or unquoted)
        items = []
        current_item = ""
        in_string = False
        string_char = None

        for char in list_content + ",":  # Add comma to flush last item
            if char in ('"', "'") and not in_string:
                in_string = True
                string_char = char
                current_item += char
            elif char == string_char and in_string:
                in_string = False
                string_char = None
                current_item += char
            elif char == "," and not in_string:
                # End of item
                item = current_item.strip()
                if item:
                    items.append(item)
                current_item = ""
            else:
                current_item += char

        # Now fix each item - if it's not quoted, quote it
        fixed_items = []
        for item in items:
            item = item.strip()
            if not item:
                continue

            # Check if properly quoted (starts and ends with quote, no quotes in middle)
            if (item.startswith('"') and item.endswith('"')) or (
                item.startswith("'") and item.endswith("'")
            ):
                # Check if there are additional quotes in the middle (malformed)
                inner_content = item[1:-1]  # Remove outer quotes
                if '"' in inner_content or "'" in inner_content:
                    # Malformed - has quotes inside
                    clean_item = item.replace('"', "").replace("'", "")
                    fixed_items.append(f'"{clean_item}"')
                else:
                    # Properly quoted
                    fixed_items.append(item)
            elif item.startswith('"') or item.startswith("'"):
                # Starts with quote but doesn't end properly
                clean_item = item.replace('"', "").replace("'", "")
                fixed_items.append(f'"{clean_item}"')
            else:
                # Not quoted at all - add quotes
                fixed_items.append(f'"{item}"')

        # Reconstruct the __all__ list with proper formatting
        # Keep the original indentation style
        if "\n" in list_content:
            # Multi-line format - preserve it
            indent = "    "  # Standard 4-space indent
            items_str = ",\n".join(f"{indent}{item}" for item in fixed_items)
            return f"{prefix}\n{items_str},\n{suffix}"
        else:
            # Single-line format
            items_str = ", ".join(fixed_items)
            return f"{prefix}{items_str}{suffix}"

    # Apply the fix
    new_content = re.sub(pattern, fix_all_list, content, flags=re.DOTALL)

    if new_content != content:
        file_path.write_text(new_content)
        return True
    return False


def main():
    """Fix __all__ exports in all Python files in src/."""
    src_dir = Path("src")

    if not src_dir.exists():
        print("Error: src/ directory not found")
        return

    modified_count = 0
    total_files = 0

    for py_file in src_dir.rglob("*.py"):
        total_files += 1
        if fix_all_exports_in_file(py_file):
            modified_count += 1
            print(f"Fixed: {py_file}")

    print(f"\n✅ Fixed {modified_count} files out of {total_files} total")


if __name__ == "__main__":
    main()

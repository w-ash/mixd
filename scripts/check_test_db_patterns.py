#!/usr/bin/env python3
"""Check for problematic database usage patterns in tests.

This script helps maintain consistent database testing patterns by detecting
anti-patterns like direct get_session() usage in test files.
"""

from pathlib import Path
import re
import sys


def check_test_files():
    """Check all test files for problematic database patterns."""
    test_dir = Path("tests")
    if not test_dir.exists():
        print("❌ Tests directory not found")
        return False

    issues_found = False

    for test_file in test_dir.rglob("*.py"):
        if test_file.name == "__init__.py":
            continue

        content = test_file.read_text()

        for line_number, line in enumerate(content.split("\n"), 1):

            # Check for direct get_session() usage (excluding session provider patterns)
            if re.search(r"async with get_session\(\)", line):
                print(
                    f"❌ {test_file}:{line_number}: Direct get_session() usage in test"
                )
                print(f"   Line: {line.strip()}")
                print("   Fix: Use 'db_session' fixture parameter instead")
                issues_found = True

            # Check for improper imports of get_session in tests
            if (
                re.search(r"from.*get_session", line)
                and r"session_provider" not in line
            ):
                print(f"⚠️  {test_file}:{line_number}: get_session import in test file")
                print(f"   Line: {line.strip()}")
                print("   Consider: Remove unused import if not using session provider")

            # Check for hardcoded database IDs
            if re.search(r"track_id\s*=\s*\d+", line) and not re.search(
                r"saved_track\.id", line
            ):
                print(f"⚠️  {test_file}:{line_number}: Possible hardcoded track ID")
                print(f"   Line: {line.strip()}")
                print("   Consider: Use auto-generated IDs from repository saves")

    if not issues_found:
        print("✅ All test files follow correct database patterns")

    return not issues_found


if __name__ == "__main__":
    success = check_test_files()
    sys.exit(0 if success else 1)

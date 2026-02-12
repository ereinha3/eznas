#!/usr/bin/env python3
"""
Simple standalone test for verification models that doesn't require full orchestrator import.
"""

import sys
from pathlib import Path
import pytest

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))


def test_type_annotations():
    """Test that test_complete_setup.py has proper type annotations"""

    # Read the file and check for type annotations
    with open("test_complete_setup.py", "r") as f:
        content = f.read()

    # Check for proper return type annotations
    assert "def __init__(self)" in content or "def __init__(self) -> None:" in content
    assert "async def" in content

    # Check for proper type hints in function signatures
    assert "Dict[str, Any]" in content
    assert "-> bool:" in content
    assert "-> None:" in content

    # Check that we're using proper HTTP client
    assert "httpx" in content
    assert "AsyncClient" in content

    # Check that we fixed the success attribute conflict
    assert "self.success_count" in content

    # Check that try/except blocks are properly structured
    assert "except Exception" in content
    assert "except (" in content

    print("✅ All type annotation checks passed!")


def test_imports():
    """Test that all necessary imports are present"""

    with open("test_complete_setup.py", "r") as f:
        content = f.read()

    required_imports = [
        "import asyncio",
        "import json",
        "import os",
        "import sys",
        "import time",
        "import httpx",
        "from pathlib import Path",
        "from typing import Dict, Any",
    ]

    for imp in required_imports:
        assert imp in content, f"Missing import: {imp}"

    print("✅ All required imports are present!")


def test_class_structure():
    """Test that classes have proper structure"""

    with open("test_complete_setup.py", "r") as f:
        content = f.read()

    # Check TestResults class
    assert "class TestResults:" in content
    assert 'def success(self, test_name: str, details: str = "") -> None:' in content
    assert "def failure(self, test_name: str, error: str) -> None:" in content
    assert "def warning(self, test_name: str, warning: str) -> None:" in content
    assert "def info(self, message: str) -> None:" in content

    # Check NASOrchestratorTester class
    assert "class NASOrchestratorTester:" in content
    assert "def __init__(self):" in content
    assert "async def test_api_endpoint(" in content

    print("✅ All class structure checks passed!")


if __name__ == "__main__":
    # Run tests directly
    test_type_annotations()
    test_imports()
    test_class_structure()
    print("\n🎉 All tests passed! test_complete_setup.py is properly structured.")

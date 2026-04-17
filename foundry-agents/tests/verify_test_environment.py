#!/usr/bin/env python3
"""
Test Environment Verification Script

This script verifies that all testing prerequisites are correctly installed
and configured before running the test suite.

Usage:
    python tests/verify_test_environment.py
"""

import sys
import subprocess
from pathlib import Path


def check_python_version():
    """Verify Python version is 3.10+"""
    print("\n=== Checking Python Version ===")
    version = sys.version_info
    print(f"Python Version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print("❌ FAIL: Python 3.10+ required")
        return False
    
    print("✅ PASS: Python version is compatible")
    return True


def check_package_version(package_name, min_version=None):
    """Check if a package is installed and optionally verify minimum version"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package_name],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            print(f"❌ FAIL: {package_name} not installed")
            return False
        
        # Extract version from output
        for line in result.stdout.split('\n'):
            if line.startswith('Version:'):
                version = line.split(':')[1].strip()
                print(f"✅ PASS: {package_name} version {version} installed", end="")
                
                if min_version:
                    if version < min_version:
                        print(f" (WARNING: minimum recommended version is {min_version})")
                        return False
                    else:
                        print(f" (>= {min_version})")
                else:
                    print()
                
                return True
        
        return False
        
    except Exception as e:
        print(f"❌ ERROR: Failed to check {package_name}: {e}")
        return False


def check_testing_packages():
    """Verify all required testing packages are installed"""
    print("\n=== Checking Testing Packages ===")
    
    packages = {
        "pytest": "8.0.0",
        "pytest-asyncio": "0.24.0",  # CRITICAL version
        "pytest-dotenv": "0.5.2",
        "pytest-order": "1.2.0",
        "pytest-cov": "5.0.0",
        "httpx": "0.27.0",
        "azure-ai-evaluation": "1.0.0",
        "azure-identity": "1.16.0",
        "azure-storage-blob": "12.20.0",
        "azure-data-tables": "12.7.0",
        "azure-search-documents": None,  # No strict version check
        "anyio": "4.0.0",
    }
    
    all_passed = True
    for package, min_version in packages.items():
        if not check_package_version(package, min_version):
            all_passed = False
    
    return all_passed


def check_env_file():
    """Verify .env.test file exists or environment variables are set (for CI/CD)"""
    print("\n=== Checking Environment Configuration ===")
    
    import os
    
    # Check if running in CI/CD environment (GitHub Actions, Azure DevOps, etc.)
    is_ci = os.getenv('CI') or os.getenv('GITHUB_ACTIONS') or os.getenv('TF_BUILD')
    
    if is_ci:
        print("✅ PASS: Running in CI/CD environment - using environment variables")
        
        # Optionally verify key environment variables are set
        required_vars = ['API_BASE_URL', 'AZURE_STORAGE_ACCOUNT_NAME']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            print(f"⚠️  WARNING: Missing environment variables: {', '.join(missing_vars)}")
            print("   Tests may fail if these are required")
        
        return True
    
    # For local development, check for .env.test file
    project_root = Path(__file__).parent.parent
    env_test_file = project_root / ".env.test"
    
    if env_test_file.exists():
        print(f"✅ PASS: .env.test file found at {env_test_file}")
        return True
    else:
        print(f"❌ FAIL: .env.test file not found at {env_test_file}")
        print("   Create .env.test with required Azure configuration")
        print("   Or run in CI/CD environment where variables are set via secrets/variables")
        return False


def check_pytest_config():
    """Verify pytest.ini configuration"""
    print("\n=== Checking Pytest Configuration ===")
    
    project_root = Path(__file__).parent.parent
    pytest_ini = project_root / "pytest.ini"
    
    if pytest_ini.exists():
        print(f"✅ PASS: pytest.ini found at {pytest_ini}")
        
        # Check for asyncio marker
        content = pytest_ini.read_text()
        if "asyncio:" in content or "asyncio =" in content:
            print("✅ PASS: asyncio marker configured in pytest.ini")
        else:
            print("⚠️  WARNING: asyncio marker may not be configured in pytest.ini")
        
        return True
    else:
        print(f"❌ FAIL: pytest.ini not found at {pytest_ini}")
        return False


def main():
    """Run all verification checks"""
    print("=" * 60)
    print("Testing Environment Verification")
    print("=" * 60)
    
    checks = [
        ("Python Version", check_python_version),
        ("Testing Packages", check_testing_packages),
        ("Environment File", check_env_file),
        ("Pytest Configuration", check_pytest_config),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            passed = check_func()
            results.append((name, passed))
        except Exception as e:
            print(f"❌ ERROR in {name}: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = all(passed for _, passed in results)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 60)
    
    if all_passed:
        print("\n✅ All checks passed! You're ready to run tests.")
        print("\nRun tests with:")
        print("  pytest tests/integration/ -v")
        return 0
    else:
        print("\n❌ Some checks failed. Please install missing dependencies:")
        print("\n  pip install -r tests/test-requirements.txt")
        print("\nFor critical pytest-asyncio version issue:")
        print("  python3 -m pip install --force-reinstall pytest-asyncio==0.24.0")
        return 1


if __name__ == "__main__":
    sys.exit(main())

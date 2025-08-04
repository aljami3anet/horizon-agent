#!/usr/bin/env python3
"""
Test runner for AI Coder Agent
"""

import subprocess
import sys
import os

def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"\n🔄 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"Command: {cmd}")
        print(f"Error: {e.stderr}")
        return False

def main():
    """Run all tests and checks."""
    print("🧪 Running AI Coder Agent Tests")
    print("=" * 50)
    
    # Create necessary directories
    os.makedirs('chats', exist_ok=True)
    os.makedirs('workspace', exist_ok=True)
    os.makedirs('knowledge', exist_ok=True)
    os.makedirs('backups', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    # Run linting
    linting_ok = run_command(
        "flake8 . --max-line-length=127 --count --select=E9,F63,F7,F82 --show-source --statistics",
        "Linting (errors only)"
    )
    
    if linting_ok:
        run_command(
            "flake8 . --max-line-length=127 --count --exit-zero --max-complexity=10 --statistics",
            "Linting (all issues)"
        )
    
    # Run type checking
    type_check_ok = run_command(
        "mypy app.py --ignore-missing-imports",
        "Type checking"
    )
    
    # Run security checks
    security_ok = run_command(
        "bandit -r . -f json -o bandit-report.json",
        "Security checks (bandit)"
    )
    
    # Run tests
    tests_ok = run_command(
        "pytest tests/ -v --cov=app --cov-report=html --cov-report=term-missing",
        "Running tests with coverage"
    )
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Summary:")
    print(f"   Linting: {'✅ PASS' if linting_ok else '❌ FAIL'}")
    print(f"   Type Checking: {'✅ PASS' if type_check_ok else '❌ FAIL'}")
    print(f"   Security: {'✅ PASS' if security_ok else '❌ FAIL'}")
    print(f"   Tests: {'✅ PASS' if tests_ok else '❌ FAIL'}")
    
    if all([linting_ok, type_check_ok, security_ok, tests_ok]):
        print("\n🎉 All checks passed!")
        return 0
    else:
        print("\n⚠️  Some checks failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
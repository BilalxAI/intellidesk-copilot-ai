#!/usr/bin/env python3
"""
Simple launcher script for the IT Support Assistant.
"""

import subprocess
import sys
import os
from pathlib import Path

# Set working directory to project root
project_root = Path(__file__).parent
os.chdir(project_root)

def run_tests():
    """Run the test suite"""
    print("\n" + "="*60)
    print("Running Test Suite...")
    print("="*60 + "\n")
    
    result = subprocess.run([sys.executable, "test_assistant.py"])
    return result.returncode == 0


def run_server():
    """Run the FastAPI server"""
    print("\n" + "="*60)
    print("Starting IT Support Assistant API Server...")
    print("="*60)
    print("\nServer running at: http://127.0.0.1:8000")
    print("Swagger UI: http://127.0.0.1:8000/docs\n")
    
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "127.0.0.1",
        "--port", "8000",
        "--reload"
    ])


def main():
    """Main launcher"""
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "test":
            run_tests()
        elif command == "server":
            run_server()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python launch.py [test|server]")
            sys.exit(1)
    else:
        print("\n" + "="*60)
        print("IT Support Assistant Launcher")
        print("="*60)
        print("\nUsage:")
        print("  python launch.py test   - Run test suite")
        print("  python launch.py server - Start API server")
        print("\nFor manual control:")
        print("  python test_assistant.py")
        print("  python -m uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()

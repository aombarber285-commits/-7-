from pathlib import Path

main_code = '''"""
SIGZY BRAIN V6.0 - Railway Launcher
-----------------------------------
Railway entrypoint only.
Does NOT use /mnt/data and does NOT generate/copy the V6 file.

The actual brain lives in:
    SIGZY_BRAIN_V6_0_TH_TIME.py

Railway should run:
    python main.py
"""

from pathlib import Path
import runpy
import sys

BASE_DIR = Path(__file__).resolve().parent
BRAIN_FILE = BASE_DIR / "SIGZY_BRAIN_V6_0_TH_TIME.py"


def main():
    if not BRAIN_FILE.exists():
        print("=" * 70)
        print("SIGZY BRAIN V6 ERROR")
        print(f"Missing brain file: {BRAIN_FILE}")
        print("Make sure SIGZY_BRAIN_V6_0_TH_TIME.py is in the repository root.")
        print("=" * 70)
        sys.exit(1)

    print("=" * 70)
    print("SIGZY BRAIN V6.0")
    print("Railway launcher")
    print(f"Brain: {BRAIN_FILE.name}")
    print("Storage: repository/runtime directory")
    print("No /mnt/data dependency")
    print("=" * 70)

    # Execute the real V6 script exactly as if:
    # python SIGZY_BRAIN_V6_0_TH_TIME.py
    runpy.run_path(str(BRAIN_FILE), run_name="__main__")


if __name__ == "__main__":
    main()
'''

path = Path("/mnt/data/main.py")
path.write_text(main_code, encoding="utf-8")

print(f"Created: {path}")

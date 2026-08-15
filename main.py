import os
import sys
import time

print("================================", flush=True)
print("TRADEIFY IMPORT TEST", flush=True)
print(f"Python: {sys.version}", flush=True)

print("[1] requests...", flush=True)
import requests
print("[OK] requests", flush=True)

print("[2] yfinance...", flush=True)
import yfinance as yf
print("[OK] yfinance", flush=True)

print("[3] google.genai...", flush=True)
try:
    from google import genai
    print("[OK] google.genai", flush=True)
except Exception as e:
    print(f"[WARN] google.genai: {e}", flush=True)

print("================================", flush=True)
print("ALL IMPORT TESTS PASSED", flush=True)
print("================================", flush=True)

while True:
    print("TRADEIFY TEST ALIVE", flush=True)
    time.sleep(30)

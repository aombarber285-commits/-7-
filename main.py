import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.getenv("PORT", "8080"))

print("================================", flush=True)
print("TRADEIFY BOOT TEST", flush=True)
print(f"PORT={PORT}", flush=True)
print("PYTHON STARTED", flush=True)
print("================================", flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"TRADEIFY OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

server = HTTPServer(("0.0.0.0", PORT), Handler)

print(f"SERVER LISTENING ON {PORT}", flush=True)

while True:
    server.handle_request()

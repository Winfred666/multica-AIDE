"""An embeddable solution tree and its optional read-only HTTP server."""
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from .tree import SearchTree


def render_tree(nodes, title="SOP exploration", data_url=None):
    """Return a standalone HTML widget; nodes contain no viewer-specific state."""
    data = json.dumps({"nodes": list(nodes), "data_url": data_url}, allow_nan=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("tree.html").read_text()
    return template.replace("__TITLE__", escape(title)).replace("__TREE_DATA__", data)


def make_server(tree, port, host="127.0.0.1"):
    tree = tree if isinstance(tree, SearchTree) else SearchTree(tree)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in {"/", "/tree.json"}:
                self.send_error(404)
                return
            state = tree.snapshot()
            if path == "/tree.json":
                body = json.dumps({"nodes": state["nodes"], "baseline_id": state["baseline_id"]}, allow_nan=False)
                content_type = "application/json"
            else:
                body = render_tree(state["nodes"], data_url="/tree.json")
                content_type = "text/html; charset=utf-8"
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(tree, port, host="127.0.0.1"):
    with make_server(tree, port, host) as server:
        server.serve_forever()

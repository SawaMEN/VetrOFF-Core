#!/usr/bin/env python3
"""Exercise the built Linux candidate without a VPN server or external test URL."""
import argparse
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time


class LocalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


def request_through_proxy(port, endpoint):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        conn.request("GET", endpoint)
        response = conn.getresponse()
        response.read()
        if response.status != 204:
            raise RuntimeError(f"unexpected local proxy status {response.status}")
    finally:
        conn.close()


def wait_for_proxy(proc, port, endpoint):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"candidate exited during startup: {proc.returncode}")
        try:
            request_through_proxy(port, endpoint)
            return
        except (OSError, http.client.HTTPException):
            time.sleep(0.1)
    raise RuntimeError("local proxy did not become ready")


def stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise RuntimeError("candidate did not stop after SIGTERM")


def successful_run(binary, root, env, config, port, endpoint, name, flags):
    log = root / (name + ".log")
    with log.open("wb") as stream:
        proc = subprocess.Popen(
            [str(binary), "srun", "-c", str(config), *flags],
            cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT,
        )
        try:
            wait_for_proxy(proc, port, endpoint)
        except Exception:
            print(log.read_text(errors="replace"))
            raise
        finally:
            stop_process(proc)
    if proc.returncode != 0:
        raise RuntimeError(f"{name}: shutdown exit {proc.returncode}: {log.read_text(errors='replace')}")
    print(f"PASS: {name}: local HTTP proxy and graceful shutdown", flush=True)


def rejected_run(binary, root, env, config, name, flags, expected):
    try:
        proc = subprocess.run(
            [str(binary), "srun", "-c", str(config), *flags],
            cwd=root, env=env, capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{name}: invalid configuration did not exit") from exc
    output = proc.stdout + proc.stderr
    if proc.returncode == 0 or expected not in output:
        raise RuntimeError(f"{name}: unexpected result {proc.returncode}: {output}")
    print(f"PASS: {name}: rejected without starting adaptive probes", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="anti-dpi-smoke-") as work:
        root = Path(work)
        env = dict(os.environ)
        for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            directory = root / key.lower()
            directory.mkdir()
            env[key] = str(directory)
        # No external service, TUN, elevated privileges, or real credentials.
        # This fixture intentionally uses only a normal local direct outbound.
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/health"
            port = free_port()
            config = root / "config.json"
            write_json(config, {
                "log": {"level": "info"},
                "inbounds": [{"type": "mixed", "tag": "local", "listen": "127.0.0.1", "listen_port": port}],
                "outbounds": [{"type": "direct", "tag": "local-direct"}],
                "route": {"final": "local-direct"},
            })
            policy = {"version": 1, "enabled": False, "failureThreshold": 2,
                      "recoveryThreshold": 3, "cooldownSeconds": 30,
                      "maxAgeSeconds": 90, "failback": False}
            disabled = root / "disabled.json"
            enabled = root / "enabled.json"
            write_json(disabled, policy)
            write_json(enabled, {**policy, "enabled": True})
            flags = ["--anti-dpi-selector", "local-direct", "--anti-dpi-probe-url", "https://probe.invalid/204"]
            successful_run(binary, root, env, config, port, endpoint, "default-disabled", [])
            successful_run(binary, root, env, config, port, endpoint, "explicit-disabled",
                           ["--anti-dpi-policy", str(disabled), *flags])
            rejected_run(binary, root, env, config, "incomplete-flags",
                         ["--anti-dpi-selector", "local-direct"], "all three Anti-DPI CLI flags are required")
            rejected_run(binary, root, env, config, "disabled-pool",
                         ["--anti-dpi-policy", str(disabled), *flags, "--anti-dpi-pool"],
                         "Anti-DPI optional modes require enabled local Adaptive Connect policy")
            rejected_run(binary, root, env, config, "unsupported-outbound",
                         ["--anti-dpi-policy", str(enabled), *flags], "Anti-DPI requires a plain selector")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()

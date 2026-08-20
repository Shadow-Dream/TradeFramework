#!/usr/bin/env python3
"""Run the Mining worker with a private-loopback Unix egress relay."""

from __future__ import annotations

import argparse
import selectors
import signal
import socket
import socketserver
import threading

from mining.config import load_config
from mining.store import MiningStore
from mining.worker import MiningWorker, WorkerAlreadyRunning


class RelayHandler(socketserver.BaseRequestHandler):
    broker_socket: str

    def handle(self) -> None:
        broker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            broker.settimeout(20)
            broker.connect(self.broker_socket)
            self.request.setblocking(False)
            broker.setblocking(False)
            selector = selectors.DefaultSelector()
            selector.register(self.request, selectors.EVENT_READ, broker)
            selector.register(broker, selectors.EVENT_READ, self.request)
            try:
                while selector.get_map():
                    for key, _events in selector.select(timeout=60):
                        source = key.fileobj
                        target = key.data
                        try:
                            payload = source.recv(64 * 1024)
                        except BlockingIOError:
                            continue
                        if not payload:
                            selector.unregister(source)
                            try:
                                target.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                            continue
                        target.setblocking(True)
                        try:
                            target.sendall(payload)
                        finally:
                            target.setblocking(False)
            finally:
                selector.close()
        finally:
            broker.close()


class RelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False
    address_family = socket.AF_INET


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--broker-socket", required=True)
    parser.add_argument("--listen-port", type=int, default=54321)
    args = parser.parse_args()
    RelayHandler.broker_socket = args.broker_socket
    relay = RelayServer(("127.0.0.1", args.listen_port), RelayHandler)
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    thread.start()
    try:
        config = load_config(args.config)
        store = MiningStore(config)
        try:
            MiningWorker(config, store=store).run(stop_event)
        except WorkerAlreadyRunning:
            raise SystemExit(73)
    finally:
        stop_event.set()
        relay.shutdown()
        relay.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()

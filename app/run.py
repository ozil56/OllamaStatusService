import argparse
from contextlib import contextmanager, ExitStack
import errno
import json
import logging
from dataclasses import fields
from pathlib import Path
import socket

import uvicorn
from gateway import Settings, create_app


LOG = logging.getLogger("ollama_status.listener")


def bind_listener(host, port, family):
    """Keep separate native sockets so IPv4 stays available and peers stay real."""
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, port))
        listener.listen(2048)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


@contextmanager
def dual_stack_listeners(port):
    listeners = []
    try:
        ipv4 = bind_listener("0.0.0.0", port, socket.AF_INET)
        listeners.append(ipv4)
        # Also supports port=0 in isolated tests; both families use the SAME port.
        port = ipv4.getsockname()[1]
        try:
            listeners.append(bind_listener("::", port, socket.AF_INET6))
        except OSError as exc:
            unavailable = {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL,
                           10047, 10043, 10049}  # Windows WSA equivalents.
            if exc.errno not in unavailable and getattr(exc, "winerror", None) not in unavailable:
                raise  # Port conflicts/permission failures must not silently drop IPv6.
            LOG.warning("IPv6 is unavailable; continuing on IPv4 port %s: %s", port, exc)
        for listener in listeners:
            LOG.info("Listening on %s port %s", listener.getsockname()[0], port)
        yield listeners
    finally:
        for listener in listeners:
            listener.close()


@contextmanager
def configured_listeners(host, port, ipv6):
    if host == "0.0.0.0" and ipv6:
        with dual_stack_listeners(port) as listeners:
            yield listeners
        return
    # Resolve explicit addresses/hostnames without widening their bind scope.
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    listeners = []
    seen = set()
    try:
        for family, _, _, _, address in addresses:
            bind_host = address[0]
            if family == socket.AF_INET6 and address[3] and "%" not in bind_host:
                bind_host += "%" + str(address[3])
            key = (family, bind_host)
            if key in seen:
                continue
            seen.add(key)
            listeners.append(bind_listener(bind_host, port, family))
        yield listeners
    finally:
        for listener in listeners:
            listener.close()


@contextmanager
def gateway_listeners(settings):
    # Bind ALL sockets before starting the one ASGI application/lifespan. A proxy
    # port conflict must fail startup, not leave the direct-only service running.
    with ExitStack() as stack:
        direct = stack.enter_context(configured_listeners(
            settings.listen_host, settings.listen_port, settings.listen_ipv6))
        proxy = stack.enter_context(configured_listeners(
            settings.proxy_listen_host, settings.proxy_listen_port, settings.listen_ipv6))
        for kind, listeners in (("direct", direct), ("proxy (always remote)", proxy)):
            for listener in listeners:
                LOG.info("%s listener: %s", kind, listener.getsockname())
        yield direct + proxy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    values = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    unknown = set(values) - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError("Unknown configuration fields: " + ", ".join(sorted(unknown)))
    settings = Settings(**values)
    settings.validate()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = uvicorn.Config(create_app(settings), host=settings.listen_host, port=settings.listen_port,
                            workers=1, access_log=False, timeout_graceful_shutdown=10,
                            proxy_headers=False, server_header=False)
    server = uvicorn.Server(config)
    with gateway_listeners(settings) as listeners:
        server.run(sockets=listeners)


if __name__ == "__main__":
    main()

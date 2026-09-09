#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import urllib.request
from urllib.parse import urlparse


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


OVERLAY_SCRIPT = r"""
(() => {
  const overlayVersion = 'fullscreen-20260907';
  const setBonecoWindowIdentity = () => {
    document.title = 'BONECO RUN';

    const iconSvg = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="14" fill="#071013"/>
        <path d="M16 21h18c8 0 13 4 13 11s-5 11-13 11H16V21zm10 8v6h8c2 0 4-1 4-3s-2-3-4-3h-8z" fill="#37d3c0"/>
        <path d="M45 43l-8-10h9l8 10h-9z" fill="#58a6ff"/>
      </svg>
    `;
    const iconHref = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(iconSvg)}`;
    document
      .querySelectorAll('link[rel~="icon"], link[rel="shortcut icon"], link[rel="apple-touch-icon"]')
      .forEach(node => node.remove());

    const icon = document.createElement('link');
    icon.rel = 'icon';
    icon.type = 'image/svg+xml';
    icon.href = iconHref;
    document.head.appendChild(icon);

    const shortcut = document.createElement('link');
    shortcut.rel = 'shortcut icon';
    shortcut.type = 'image/svg+xml';
    shortcut.href = iconHref;
    document.head.appendChild(shortcut);
  };

  setBonecoWindowIdentity();

  const old = document.getElementById('boneco-run-overlay-host');
  if (
    old
    && old.getAttribute('data-boneco-overlay-version') !== overlayVersion
  ) {
    old.remove();
  }
  if (old && old.isConnected) {
    return {ok: true, overlay: 'already_present'};
  }

  const host = document.createElement('div');
  host.id = 'boneco-run-overlay-host';
  host.setAttribute('data-boneco-overlay-version', overlayVersion);
  host.style.position = 'fixed';
  host.style.inset = '0';
  host.style.zIndex = '2147483647';
  host.style.width = '100vw';
  host.style.height = '100vh';
  host.style.overflow = 'hidden';
  host.style.background = '#071013';
  host.style.fontFamily = 'Segoe UI, system-ui, sans-serif';
  document.documentElement.appendChild(host);

  const shadow = host.attachShadow({mode: 'open'});
  shadow.innerHTML = `
    <style>
      * {
        box-sizing: border-box;
        scrollbar-color: rgba(115, 151, 168, .45) transparent;
        scrollbar-width: thin;
      }
      *::-webkit-scrollbar { width: 4px; height: 4px; }
      *::-webkit-scrollbar-track { background: transparent; }
      *::-webkit-scrollbar-thumb { background: rgba(115, 151, 168, .45); }
      .box {
        width: 100vw;
        height: 100vh;
        min-height: 100dvh;
        display: grid;
        grid-template-rows: 56px minmax(0, 1fr);
        border: 0;
        background: #071013;
        color: #eff4f7;
        box-shadow: none;
      }
      .head {
        display: flex;
        align-items: center;
        min-height: 56px;
        padding: 0 22px;
        border-bottom: 1px solid #213844;
        background: #0b171f;
        font-weight: 700;
        letter-spacing: 0;
      }
      .body {
        min-height: 0;
        display: grid;
        grid-template-rows: minmax(120px, 26vh) auto minmax(0, 1fr);
        gap: 14px;
        padding: 18px clamp(16px, 4vw, 42px) 22px;
        overflow: hidden;
        background: #071013;
      }
      textarea {
        width: 100%;
        height: 100%;
        resize: none;
        border: 1px solid #27404d;
        background: #09161d;
        color: #eff4f7;
        padding: 14px;
        font: inherit;
        line-height: 1.45;
        outline: none;
      }
      textarea:focus { border-color: #58a6ff; }
      .row {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: flex-end;
      }
      button {
        min-height: 40px;
        border: 1px solid #34414e;
        background: #26313b;
        color: #eff4f7;
        padding: 0 16px;
        font: inherit;
        cursor: pointer;
      }
      button.primary {
        background: #215a99;
        border-color: #3679bf;
      }
      .status {
        min-height: 0;
        overflow: auto;
        scrollbar-width: none;
        border: 1px solid #213844;
        background: #09161d;
        color: #b8c7d3;
        padding: 14px;
        font-size: 13px;
        line-height: 1.45;
        white-space: pre-wrap;
      }
      .status::-webkit-scrollbar { width: 0; height: 0; }
      @media (max-width: 640px) {
        .head { padding: 0 16px; }
        .body {
          grid-template-rows: minmax(116px, 28vh) auto minmax(0, 1fr);
          padding: 14px;
        }
        .row { align-items: stretch; }
        button { flex: 1; }
      }
    </style>
    <div class="box">
      <div class="head">
        <span>BONECO RUN</span>
      </div>
      <div class="body">
        <textarea placeholder="Digite o objetivo para este computador"></textarea>
        <div class="row">
          <button class="primary" type="button">Executar no PC</button>
          <button type="button" data-refresh>Atualizar</button>
        </div>
        <div class="status">Pronto.</div>
      </div>
    </div>
  `;

  const textarea = shadow.querySelector('textarea');
  const status = shadow.querySelector('.status');
  const send = shadow.querySelector('button.primary');
  const refresh = shadow.querySelector('[data-refresh]');

  const setStatus = value => {
    status.textContent = value || 'Aguardando.';
  };

  const safeText = value => String(value || '').replace(/\s+/g, ' ').trim();

  const update = async () => {
    const response = await fetch('http://127.0.0.1:8791/api/status', {cache: 'no-store'});
    const payload = await response.json();
    const activity = payload.activity || {};
    const job = payload.job || {};
    const line = job.running ? 'Executando' : safeText(activity.state || 'Aguardando');
    const step = safeText(activity.step || 'Sem tarefa ativa');
    setStatus(`${line}\n${step}`);
  };

  send.addEventListener('click', async () => {
    const goal = safeText(textarea.value);
    if (!goal) {
      setStatus('Digite um objetivo antes de executar.');
      return;
    }
    send.disabled = true;
    try {
      const response = await fetch('http://127.0.0.1:8791/api/job/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({goal})
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
      textarea.value = '';
      setStatus(`Job iniciado\n${payload.job_id || ''}`);
      setTimeout(update, 1200);
    } catch (error) {
      setStatus(`Falha: ${error.message || error}`);
    } finally {
      send.disabled = false;
    }
  });

  refresh.addEventListener('click', () => update().catch(error => setStatus(`Falha: ${error.message || error}`)));
  setInterval(() => {
    setBonecoWindowIdentity();
    update().catch(() => {});
  }, 5000);
  update().catch(() => {});

  return {ok: true, overlay: 'boneco-run-overlay', fullscreen: true, close_button: false};
})()
"""


class CDP:
    def __init__(self, port: int = 9227) -> None:
        self.port = int(port)
        base = f"http://127.0.0.1:{self.port}"

        with urllib.request.urlopen(base + "/json", timeout=3) as response:
            targets = json.loads(response.read().decode("utf-8"))

        page = next(
            (
                item
                for item in targets
                if item.get("type") == "page"
                and "chatgpt.com" in str(item.get("url") or "")
                and item.get("webSocketDebuggerUrl")
            ),
            None,
        )

        if page is None:
            raise RuntimeError("pagina ChatGPT nao encontrada")

        parsed = urlparse(page["webSocketDebuggerUrl"])
        self.sock = socket.create_connection(
            (parsed.hostname, parsed.port or 80),
            timeout=5,
        )

        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: http://127.0.0.1:{self.port}\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))

        headers = b""
        while b"\r\n\r\n" not in headers:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("falha no handshake CDP")
            headers += chunk

        status = headers.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(status.decode("latin1", errors="replace"))

        self.ident = 0

    def exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("WebSocket encerrado")
            data += chunk
        return data

    def receive(self) -> dict:
        while True:
            first, second = self.exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            size = second & 0x7F

            if size == 126:
                size = struct.unpack("!H", self.exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.exact(8))[0]

            mask = self.exact(4) if masked else b""
            payload = self.exact(size)

            if masked:
                payload = bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                )

            if opcode == 1:
                return json.loads(payload.decode("utf-8"))

            if opcode == 8:
                raise RuntimeError("WebSocket fechado")

    def send(self, value: dict) -> None:
        payload = json.dumps(value).encode("utf-8")
        mask = os.urandom(4)
        size = len(payload)

        if size < 126:
            header = bytes([0x81, 0x80 | size])
        elif size <= 65535:
            header = bytes([0x81, 0xFE]) + struct.pack("!H", size)
        else:
            header = bytes([0x81, 0xFF]) + struct.pack("!Q", size)

        masked = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
        self.sock.sendall(header + mask + masked)

    def evaluate(self, expression: str):
        self.ident += 1
        ident = self.ident
        self.send({
            "id": ident,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        })

        while True:
            message = self.receive()
            if message.get("id") != ident:
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return (
                message.get("result", {})
                .get("result", {})
                .get("value")
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Injeta painel BONECO RUN sobre ChatGPT.")
    parser.add_argument("--port", type=int, default=9227)
    args = parser.parse_args()

    try:
        result = CDP(args.port).evaluate(OVERLAY_SCRIPT)
    except Exception as exc:
        print("status=error")
        print(f"erro={type(exc).__name__}: {exc}")
        return 1

    print("status=overlay_injected")
    print(f"port={args.port}")
    print("result=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

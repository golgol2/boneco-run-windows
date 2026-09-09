#!/usr/bin/env python3

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from urllib.parse import urlparse

BASE = "http://127.0.0.1:9227"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class CDP:
    def __init__(self, port=9227):
        self.port = int(port)
        self.base = f"http://127.0.0.1:{self.port}"

        with urllib.request.urlopen(self.base + "/json", timeout=3) as response:
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
            raise RuntimeError("página ChatGPT não encontrada")

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
            raise RuntimeError(
                status.decode("latin1", errors="replace")
            )

        self.ident = 0

    def exact(self, size):
        data = b""

        while len(data) < size:
            chunk = self.sock.recv(size - len(data))

            if not chunk:
                raise RuntimeError("WebSocket encerrado")

            data += chunk

        return data

    def receive(self):
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

    def send(self, value):
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

    def evaluate(self, expression):
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


def snapshot(cdp):
    return cdp.evaluate(r"""
(() => {
  const clean = value => String(value || '')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  const countMarker = (text, marker) => {
    const pattern = new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
    return (text.match(pattern) || []).length;
  };

  const stripFooter = text => clean(text)
    .replace(/\n?ChatGPT é uma IA[\s\S]*$/i, '')
    .replace(/\n?ChatGPT can make mistakes[\s\S]*$/i, '')
    .trim();

  const lastAssistantFromPlainText = text => {
    const markers = [
      'O ChatGPT disse:',
      'ChatGPT disse:',
      'ChatGPT said:'
    ];
    let best = {index: -1, marker: ''};

    for (const marker of markers) {
      const index = text.lastIndexOf(marker);
      if (index > best.index) {
        best = {index, marker};
      }
    }

    if (best.index < 0) {
      return '';
    }

    let value = text.slice(best.index + best.marker.length);
    const nextMarkers = ['\nVocê disse:', '\nYou said:'];
    const nextIndexes = nextMarkers
      .map(marker => value.indexOf(marker))
      .filter(index => index >= 0);

    if (nextIndexes.length) {
      value = value.slice(0, Math.min(...nextIndexes));
    }

    return stripFooter(value);
  };

  const extractBonecoRunBlock = text => {
    const start = text.indexOf('# BONECO_RUN');
    if (start < 0) {
      return '';
    }

    let value = stripFooter(text.slice(start));
    const trailing = [
      '\nCopiar código',
      '\nCopiar resposta',
      '\nCompartilhar',
      '\nVocê disse:',
      '\nYou said:'
    ];

    for (const marker of trailing) {
      const index = value.indexOf(marker);
      if (index >= 0) {
        value = value.slice(0, index);
      }
    }

    return clean(value);
  };

  const assistants = [
    ...document.querySelectorAll(
      '[data-message-author-role="assistant"]'
    )
  ];

  const users = [
    ...document.querySelectorAll(
      '[data-message-author-role="user"]'
    )
  ];

  const stop = document.querySelector(
    '[data-testid="stop-button"], button[aria-label*="Parar"], button[aria-label*="Stop"]'
  );

  const last = assistants.at(-1);
  const codeBlocks = last
    ? [...last.querySelectorAll('pre code')]
        .map(node => (node.innerText || node.textContent || '').trimEnd())
        .filter(Boolean)
    : [];
  const allCodeBlocks = [...document.querySelectorAll('pre code, pre')]
    .map(node => (node.innerText || node.textContent || '').trimEnd())
    .filter(Boolean);
	  const mainText = clean((document.querySelector('main') || document.body).innerText || '');
	  const bodyText = clean(document.body.innerText || '');
	  const fallbackLatest = lastAssistantFromPlainText(mainText);
  const latest = last
    ? clean(last.innerText || last.textContent || '')
    : fallbackLatest;
  const latestCode = codeBlocks.at(-1)
    || allCodeBlocks.at(-1)
    || extractBonecoRunBlock(latest)
    || extractBonecoRunBlock(fallbackLatest);
  const assistantCount = assistants.length
    || countMarker(mainText, 'O ChatGPT disse:')
    || countMarker(mainText, 'ChatGPT said:');
  const userCount = users.length
    || countMarker(mainText, 'Você disse:')
    || countMarker(mainText, 'You said:');

	  return {
	    generating: !!stop,
	    loginRequired: /(^|\n)Entrar(\n|$)/i.test(bodyText) && !latest,
	    assistantCount,
    userCount,
    codeBlockCount: codeBlocks.length || allCodeBlocks.length || (latestCode ? 1 : 0),
	    latestCode: latestCode || '',
	    latest
	  };
})()
""")


def expected_marker_found(state, expected):
    if not expected:
        return True

    latest = (state or {}).get("latest", "").strip()
    latest_code = (state or {}).get("latestCode", "").strip()
    return expected in latest or expected in latest_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected")
    parser.add_argument(
        "--code-only",
        action="store_true",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9227,
    )
    args = parser.parse_args()

    cdp = CDP(port=args.port)

    deadline = time.monotonic() + max(1.0, args.timeout)
    last = None

    while time.monotonic() < deadline:
        state = snapshot(cdp)
        last = state

        latest = (state or {}).get("latest", "").strip()
        expected_found = expected_marker_found(state, args.expected)

        if (
            state
            and state.get("loginRequired")
            and not state.get("generating")
        ):
            break

        if (
            state
            and state.get("assistantCount", 0) > 0
            and latest
            and not state.get("generating")
            and expected_found
        ):
            break

        time.sleep(0.5)

    if not last:
        print("status=error")
        print("erro=estado da conversa indisponível")
        return 2

    if last.get("loginRequired"):
        print("generating=" + str(bool(last.get("generating"))).lower())
        print("user_count=" + str(last.get("userCount", 0)))
        print("assistant_count=" + str(last.get("assistantCount", 0)))
        print("code_blocks=" + str(last.get("codeBlockCount", 0)))
        print("validation=login_required")
        print("erro=ChatGPT precisa de login no perfil do BONECO RUN Windows")
        return 6

    print("generating=" + str(bool(last.get("generating"))).lower())
    print("user_count=" + str(last.get("userCount", 0)))
    print("assistant_count=" + str(last.get("assistantCount", 0)))
    print("code_blocks=" + str(last.get("codeBlockCount", 0)))

    if last.get("generating"):
        if args.code_only:
            print("----- CODE -----")
            print(last.get("latestCode", "").rstrip())
            print("----- END CODE -----")
        else:
            print("----- RESPONSE -----")
            print(last.get("latest", "").strip())
            print("----- END RESPONSE -----")
        print("validation=response_still_generating")
        return 4

    if args.code_only:
        latest = last.get("latestCode", "").rstrip()

        print("code_length=" + str(len(latest)))
        print("----- CODE -----")
        print(latest)
        print("----- END CODE -----")

        if not latest:
            print("validation=code_block_missing")
            return 5

        if args.expected:
            if args.expected in latest:
                print("validation=expected_marker_found")
                return 0

            print("validation=expected_marker_missing")
            return 3

        print("validation=code_block_received")
        return 0

    print("----- RESPONSE -----")
    print(last.get("latest", "").strip())
    print("----- END RESPONSE -----")

    latest = last.get("latest", "").strip()

    if args.expected:
        if args.expected in latest:
            print("validation=expected_marker_found")
            return 0

        print("validation=expected_marker_missing")
        return 3

    if latest:
        print("validation=response_received")
        return 0

    print("validation=empty_response")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

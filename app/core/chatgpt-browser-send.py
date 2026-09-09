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


class WS:
    def __init__(self, port=9227):
        self.port = int(port)
        self.base = f"http://127.0.0.1:{self.port}"

        with urllib.request.urlopen(self.base + "/json", timeout=3) as r:
            targets = json.loads(r.read().decode())

        page = next(
            x for x in targets
            if x.get("type") == "page"
            and "chatgpt.com" in str(x.get("url") or "")
            and x.get("webSocketDebuggerUrl")
        )

        p = urlparse(page["webSocketDebuggerUrl"])

        self.sock = socket.create_connection(
            (p.hostname, p.port or 80),
            timeout=5,
        )

        path = p.path or "/"
        key = base64.b64encode(os.urandom(16)).decode()

        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {p.hostname}:{p.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: http://127.0.0.1:{self.port}\r\n\r\n"
        )

        self.sock.sendall(req.encode())

        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)

        if b" 101 " not in data.split(b"\r\n", 1)[0]:
            raise RuntimeError("falha no WebSocket CDP")

        self.ident = 0

    def exact(self, n):
        data = b""
        while len(data) < n:
            part = self.sock.recv(n - len(data))
            if not part:
                raise RuntimeError("WebSocket encerrado")
            data += part
        return data

    def recv(self):
        while True:
            a, b = self.exact(2)
            opcode = a & 15
            size = b & 127

            if size == 126:
                size = struct.unpack("!H", self.exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.exact(8))[0]

            payload = self.exact(size)

            if opcode == 1:
                return json.loads(payload.decode())

            if opcode == 8:
                raise RuntimeError("WebSocket fechado")

    def send_raw(self, text):
        payload = text.encode()
        mask = os.urandom(4)
        size = len(payload)

        if size < 126:
            head = bytes([0x81, 0x80 | size])
        elif size <= 65535:
            head = bytes([0x81, 0xFE]) + struct.pack("!H", size)
        else:
            head = bytes([0x81, 0xFF]) + struct.pack("!Q", size)

        masked = bytes(
            value ^ mask[i % 4]
            for i, value in enumerate(payload)
        )

        self.sock.sendall(head + mask + masked)

    def eval(self, script):
        self.ident += 1
        ident = self.ident

        self.send_raw(json.dumps({
            "id": ident,
            "method": "Runtime.evaluate",
            "params": {
                "expression": script,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))

        while True:
            message = self.recv()

            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))

                return (
                    message.get("result", {})
                    .get("result", {})
                    .get("value")
                )


def send_message(text, port=9227):
    ws = WS(port=port)

    encoded = json.dumps(text)
    editor_result = None

    deadline = time.monotonic() + 20.0

    while time.monotonic() < deadline:
        editor_result = ws.eval("""
(() => {
  const selectors = [
    '#prompt-textarea',
    '#mobile-composer-prompt',
    '[data-testid="prompt-textarea"]',
    '[contenteditable="true"][role="textbox"]',
    '[role="textbox"][contenteditable="true"]',
    'textarea[aria-label]',
    'textarea[placeholder]'
  ];

  for (const selector of selectors) {
    const editor = document.querySelector(selector);

    if (!editor) {
      continue;
    }

    const visible = !!(
      editor.offsetWidth
      || editor.offsetHeight
      || editor.getClientRects().length
    );

    if (visible) {
      return {
        ok: true,
        selector,
        tag: editor.tagName,
        id: editor.id || '',
        role: editor.getAttribute('role') || '',
        aria: editor.getAttribute('aria-label') || ''
      };
    }
  }

  return {
    ok: false,
    readyState: document.readyState,
    url: location.href,
    title: document.title,
    candidates: [...document.querySelectorAll(
      'textarea, [contenteditable="true"], [role="textbox"]'
    )].map(node => ({
      tag: node.tagName,
      id: node.id || '',
      role: node.getAttribute('role') || '',
      aria: node.getAttribute('aria-label') || '',
      testid: node.getAttribute('data-testid') || '',
      visible: !!(
        node.offsetWidth
        || node.offsetHeight
        || node.getClientRects().length
      )
    })).slice(0, 20)
  };
})()
""")

        if editor_result and editor_result.get("ok"):
            break

        time.sleep(0.5)

    if not editor_result or not editor_result.get("ok"):
        print("editor_result=" + json.dumps(
            editor_result or {
                "ok": False,
                "error": "editor_not_found",
            },
            ensure_ascii=False,
        ))
        return 3

    selector = json.dumps(editor_result["selector"])

    result = ws.eval(f"""
(() => {{
  const editor = document.querySelector({selector});

  if (!editor) {{
    return {{ok:false, error:'editor_not_found'}};
  }}

  editor.focus();

  const text = {encoded};

  if ('value' in editor) {{
    const descriptor = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(editor),
      'value'
    ) || Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value'
    );
    const setter = descriptor && descriptor.set;

    if (setter) {{
      setter.call(editor, text);
    }} else {{
      editor.value = text;
    }}
  }} else {{
    editor.replaceChildren(
      document.createTextNode(text)
    );
  }}

  editor.dispatchEvent(
    new InputEvent('input', {{
      bubbles: true,
      inputType: 'insertText',
      data: text
    }})
  );

  editor.dispatchEvent(
    new Event('change', {{
      bubbles: true
    }})
  );

  return {{
    ok:true,
    selector: {selector},
    text:editor.innerText || editor.value || ''
  }};
}})()
""")

    print("editor_result=" + json.dumps(
        result,
        ensure_ascii=False,
    ))

    time.sleep(0.5)

    result = ws.eval("""
(() => {
  const candidates = [
    '[data-testid="send-button"]',
    'button[type="submit"]',
    'button[aria-label*="Enviar mensagem"]',
    'button[aria-label*="Enviar"]',
    'button[aria-label*="Send message"]',
    'button[aria-label*="Send"]'
  ];

  const visible = node => !!(
    node
    && (node.offsetWidth || node.offsetHeight || node.getClientRects().length)
  );

  const buttons = [];
  for (const selector of candidates) {
    buttons.push(...document.querySelectorAll(selector));
  }

  for (const button of buttons) {
    const selector = candidates.find(item => {
      try {
        return button.matches(item);
      } catch (_) {
        return false;
      }
    }) || '';

    if (button && visible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true') {
      button.click();

      return {
        ok: true,
        selector
      };
    }
  }

  const editor = document.querySelector({selector});
  const form = editor ? editor.closest('form') : null;
  if (form && typeof form.requestSubmit === 'function') {
    form.requestSubmit();
    return {
      ok: true,
      selector: 'form.requestSubmit'
    };
  }

  return {
    ok: false,
    buttons: [...document.querySelectorAll('button')]
      .map(b => ({
        aria: b.getAttribute('aria-label') || '',
        testid: b.getAttribute('data-testid') || '',
        disabled: !!b.disabled
      }))
      .filter(x => x.aria || x.testid)
      .slice(-20)
  };
})()
""")

    print("send_result=" + json.dumps(
        result,
        ensure_ascii=False,
    ))

    return 0 if result.get("ok") else 3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--text",
        required=True,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9227,
    )
    args = parser.parse_args()

    return send_message(args.text, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())

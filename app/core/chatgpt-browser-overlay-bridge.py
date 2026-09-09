#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.request
from urllib.parse import urlparse


LOCAL_PANEL = "http://127.0.0.1:8791"
BINDING_NAME = "bonecoRunNative"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def safe_text(value: object, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(text) > limit:
        return text[:limit].rstrip() + "..."

    return text


def panel_request(method: str, path: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}

    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        LOCAL_PANEL + path,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")

    return json.loads(raw) if raw else {}


def status_text(payload: dict) -> str:
    activity = payload.get("activity") if isinstance(payload, dict) else {}
    job = payload.get("job") if isinstance(payload, dict) else {}
    last_job = payload.get("last_job") if isinstance(payload, dict) else {}
    browser = payload.get("browser") if isinstance(payload, dict) else {}
    project_browser = browser.get("project") if isinstance(browser, dict) else {}

    if not isinstance(activity, dict):
        activity = {}
    if not isinstance(job, dict):
        job = {}
    if not isinstance(last_job, dict):
        last_job = {}
    if not isinstance(project_browser, dict):
        project_browser = {}

    state = "Executando" if job.get("running") else safe_text(activity.get("state") or "Aguardando")
    step = safe_text(activity.get("step") or "Sem tarefa ativa")
    browser_state = "ChatGPT pronto" if str(project_browser.get("cdp_ready")).lower() == "true" else "ChatGPT aguardando"
    parts = [state, step, browser_state]

    summary = safe_text(last_job.get("summary"), 900)
    validation = safe_text(last_job.get("validation"), 900)

    if summary:
        parts.extend(["", "Resultado:", summary])
    if validation:
        parts.extend(["", "Validacao:", validation])

    return "\n".join(parts)


def overlay_expression() -> str:
    return r"""
(() => {
  const overlayVersion = 'chat-composer-left-20260907';
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

    if (!document.head) {
      return;
    }

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

  const existing = document.getElementById('boneco-run-overlay-host');
  if (existing) {
    if (
      existing.getAttribute('data-boneco-bridge') === '1'
      && existing.getAttribute('data-boneco-overlay-version') === overlayVersion
    ) {
      return {ok: true, overlay: 'already_present'};
    }
    existing.remove();
  }

  const host = document.createElement('div');
  host.id = 'boneco-run-overlay-host';
  host.setAttribute('data-boneco-bridge', '1');
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
      .head-state {
        margin-left: auto;
        color: #85e8da;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .body {
        min-height: 0;
        display: grid;
        grid-template-columns: minmax(320px, 1fr);
        overflow: hidden;
        background: #071013;
      }
      .body.has-feedback {
        grid-template-columns: minmax(380px, 1fr) minmax(420px, 1fr);
      }
      .command-panel {
        min-width: 0;
        min-height: 0;
        display: grid;
        grid-template-rows: minmax(0, 1fr) auto;
        padding: 0;
        overflow: hidden;
      }
      .chat-feed {
        min-height: 0;
        overflow: auto;
        scrollbar-width: none;
        border: 0;
        background: transparent;
        padding: 18px 16px 24px;
      }
      .chat-feed::-webkit-scrollbar { width: 0; height: 0; }
      .message {
        max-width: min(760px, 88%);
        margin: 0 0 14px;
        padding: 12px 14px;
        border: 1px solid #263f4c;
        border-radius: 8px;
        color: #d6e1e8;
        font-size: 14px;
        line-height: 1.48;
        white-space: pre-wrap;
      }
      .message.user {
        margin-left: auto;
        margin-right: 0;
        border-color: #3679bf;
        background: #102b43;
      }
      .message.system {
        margin-left: 0;
        margin-right: auto;
        background: #0b1b22;
      }
      .message.warn {
        margin-left: 0;
        margin-right: auto;
        border-color: #7c6133;
        background: #20190d;
      }
      .composer {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 10px;
        align-items: center;
        width: 100%;
        margin: 0;
        padding: 8px 8px 8px 14px;
        border: 1px solid #27404d;
        border-radius: 8px;
        background: #09161d;
        box-shadow: 0 18px 36px rgba(0, 0, 0, .18);
      }
      .composer:focus-within {
        border-color: #58a6ff;
        box-shadow: 0 0 0 3px rgba(88, 166, 255, .12), 0 18px 36px rgba(0, 0, 0, .18);
      }
      .composer-wrap {
        padding: 10px 16px 16px;
        border-top: 1px solid #1f3541;
        background: #071013;
      }
      textarea {
        width: 100%;
        min-height: 40px;
        height: 40px;
        max-height: 132px;
        resize: none;
        border: 0;
        background: transparent;
        color: #eff4f7;
        padding: 8px 4px;
        font: inherit;
        line-height: 1.45;
        outline: none;
      }
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
      .composer button.primary {
        min-height: 40px;
        border-radius: 8px;
        padding: 0 18px;
        white-space: nowrap;
      }
      button.busy:disabled {
        cursor: wait;
        opacity: .7;
      }
      button:disabled {
        cursor: default;
        opacity: .72;
      }
      .feedback-panel {
        min-width: 0;
        min-height: 0;
        display: none;
        grid-template-rows: auto auto auto minmax(0, 1fr);
        gap: 14px;
        padding: 18px clamp(16px, 3.5vw, 42px) 22px;
        border-left: 1px solid #1f3541;
        background:
          linear-gradient(90deg, rgba(55, 211, 192, .07), transparent 32%),
          #08141a;
        overflow: hidden;
      }
      .body.has-feedback .feedback-panel {
        display: grid;
      }
      .feedback-head {
        min-width: 0;
        display: flex;
        gap: 14px;
        align-items: center;
        justify-content: space-between;
      }
      .kicker {
        color: #76a3b7;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .feedback-title {
        margin-top: 4px;
        color: #eff4f7;
        font-size: 20px;
        font-weight: 800;
      }
      .live-dot {
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: #37d3c0;
        box-shadow: 0 0 18px rgba(55, 211, 192, .78);
      }
      .live-dot.idle {
        background: #78909c;
        box-shadow: none;
      }
      .metrics {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }
      .metric {
        border: 1px solid #213844;
        background: #09161d;
        padding: 10px;
        min-width: 0;
      }
      .metric span {
        color: #b8c7d3;
        display: block;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .metric strong {
        display: block;
        margin-top: 5px;
        overflow: hidden;
        color: #eff4f7;
        font-size: 13px;
        font-weight: 700;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .output {
        min-height: 0;
        overflow: auto;
        scrollbar-width: none;
        border: 1px solid #213844;
        background:
          linear-gradient(180deg, rgba(88, 166, 255, .07), transparent 42%),
          #09161d;
        color: #c8d5dd;
        padding: 14px;
        font-size: 13px;
        line-height: 1.45;
        white-space: pre-wrap;
      }
      .output::-webkit-scrollbar { width: 0; height: 0; }
      .permission-row {
        display: none;
        gap: 8px;
        flex-wrap: wrap;
      }
      .permission-row.visible {
        display: flex;
      }
      .permission-row button {
        flex: 1 1 128px;
      }
      .permission-row .deny {
        background: #522528;
        border-color: #81383d;
      }
      .permission-row .always {
        background: #173a30;
        border-color: #2f7d61;
      }
      @media (max-width: 760px) {
        .head { padding: 0 16px; }
        .body,
        .body.has-feedback {
          grid-template-columns: 1fr;
          grid-template-rows: minmax(320px, 1fr) minmax(220px, .8fr);
        }
        .feedback-panel {
          padding: 14px;
        }
        .chat-feed {
          padding: 14px 12px 18px;
        }
        .composer {
          grid-template-columns: minmax(0, 1fr) auto;
          margin: 0;
        }
        .composer-wrap { padding: 10px 12px 14px; }
        .feedback-panel {
          border-left: 0;
          border-top: 1px solid #1f3541;
        }
        .row { align-items: stretch; }
        button { flex: 1; }
        .metrics { grid-template-columns: 1fr; }
      }
    </style>
    <div class="box">
      <div class="head">
        <span>BONECO RUN</span>
        <span class="head-state" data-header-state>Aguardando</span>
      </div>
      <div class="body" data-body>
        <section class="command-panel">
          <div class="chat-feed" data-chat-feed>
            <div class="message system">Pronto para receber um objetivo.</div>
          </div>
          <div class="composer-wrap">
            <div class="composer">
              <textarea rows="1" placeholder="Mensagem para a IA do projeto"></textarea>
              <button class="primary" type="button">Enviar</button>
            </div>
          </div>
        </section>
        <aside class="feedback-panel" aria-live="polite">
          <div class="feedback-head">
            <div>
              <div class="kicker">Retorno do computador</div>
              <div class="feedback-title" data-feedback-title>Conectando</div>
            </div>
            <div class="live-dot idle" data-live-dot></div>
          </div>
          <div class="metrics">
            <div class="metric"><span>Estado</span><strong data-state>Aguardando</strong></div>
            <div class="metric"><span>Etapa</span><strong data-step>Sem tarefa ativa</strong></div>
            <div class="metric"><span>Job</span><strong data-job>-</strong></div>
          </div>
          <div class="permission-row" data-permission-row>
            <button type="button" data-permit>Permitir</button>
            <button class="deny" type="button" data-deny>Negar</button>
            <button class="always" type="button" data-always>Permitir sempre</button>
          </div>
          <div class="output" data-result>Conectando ao BONECO RUN.</div>
        </aside>
      </div>
    </div>
  `;

  const body = shadow.querySelector('[data-body]');
  const chatFeed = shadow.querySelector('[data-chat-feed]');
  const textarea = shadow.querySelector('textarea');
  const send = shadow.querySelector('button.primary');
  const headerState = shadow.querySelector('[data-header-state]');
  const feedbackTitle = shadow.querySelector('[data-feedback-title]');
  const liveDot = shadow.querySelector('[data-live-dot]');
  const stateValue = shadow.querySelector('[data-state]');
  const stepValue = shadow.querySelector('[data-step]');
  const jobValue = shadow.querySelector('[data-job]');
  const resultValue = shadow.querySelector('[data-result]');
  const permissionRow = shadow.querySelector('[data-permission-row]');
  const permit = shadow.querySelector('[data-permit]');
  const deny = shadow.querySelector('[data-deny]');
  const always = shadow.querySelector('[data-always]');

  let seq = 0;
  let pendingStartId = '';
  let lastChatSignature = '';

  const compact = value => String(value || '').replace(/\s+/g, ' ').trim();
  const multiline = value => String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  const userFacing = value => multiline(value).replace(/```[\s\S]*?```/g, '[bloco tecnico omitido]').trim();

  const resizeTextarea = () => {
    textarea.style.height = '40px';
    textarea.style.height = `${Math.min(132, Math.max(40, textarea.scrollHeight))}px`;
  };

  const setBusy = running => {
    const busy = Boolean(running);
    send.disabled = busy;
    send.classList.toggle('busy', busy);
    send.textContent = busy ? 'Executando...' : 'Enviar';
  };

  const showFeedback = visible => {
    body.classList.toggle('has-feedback', Boolean(visible));
  };

  const appendChat = (kind, text) => {
    const cleanText = userFacing(text);
    if (!cleanText) return;
    const signature = `${kind}:${cleanText}`;
    if (signature === lastChatSignature) return;
    lastChatSignature = signature;

    const message = document.createElement('div');
    message.className = `message ${kind}`;
    message.textContent = cleanText.length > 900 ? `${cleanText.slice(0, 900).trim()}...` : cleanText;
    chatFeed.appendChild(message);

    while (chatFeed.children.length > 24) {
      chatFeed.removeChild(chatFeed.firstElementChild);
    }

    chatFeed.scrollTop = chatFeed.scrollHeight;
  };

  const showMessage = (title, text, running = false) => {
    const cleanTitle = compact(title || 'Status');
    const cleanText = multiline(text || 'Aguardando.');
    showFeedback(true);
    permissionRow.classList.remove('visible');
    headerState.textContent = cleanTitle;
    feedbackTitle.textContent = cleanTitle;
    stateValue.textContent = cleanTitle;
    stepValue.textContent = cleanText.split('\n')[0] || 'Status';
    jobValue.textContent = '-';
    resultValue.textContent = cleanText;
    liveDot.classList.toggle('idle', !running);
    setBusy(running);
    appendChat(running ? 'system' : 'warn', `${cleanTitle}\n${cleanText}`);
  };

  const outputFromStatus = (payload, running) => {
    const activity = payload.activity || {};
    const lastJob = payload.last_job || {};
    const parts = [];
    const summary = multiline(lastJob.summary || '');
    const validation = multiline(lastJob.validation || '');

    if (summary) parts.push(`Resultado\n${summary}`);
    if (validation) parts.push(`Validacao\n${validation}`);

    if (!parts.length && running) {
      parts.push(`Andamento\n${compact(activity.step || 'Aguardando retorno da IA e execucao no computador.')}`);
    }

    if (!parts.length && compact(activity.step)) {
      parts.push(`Ultimo status\n${compact(activity.step)}`);
    }

    return parts.join('\n\n');
  };

  const renderStatus = (payload, fallbackText = '') => {
    if (!payload || typeof payload !== 'object') {
      showMessage('Status', fallbackText || 'Aguardando.', false);
      return;
    }

    const activity = payload.activity || {};
    const job = payload.job || {};
    const lastJob = payload.last_job || {};
    const running = Boolean(job.running) || compact(activity.state).toLowerCase() === 'running';
    const needsUser = !running && ['needs_user', 'blocked'].includes(compact(lastJob.state).toLowerCase());
    const state = running ? 'Executando' : compact(activity.state || (lastJob.available ? 'Pronto' : 'Aguardando'));
    const step = compact(activity.step || (running ? 'Executando tarefa no computador' : 'Sem tarefa ativa'));
    const jobId = compact((running ? activity.request_id : lastJob.job_id) || activity.request_id || '-');
    const output = outputFromStatus(payload, running);
    const hasOutput = running || Boolean(lastJob.available) || Boolean(output);

    showFeedback(hasOutput);
    headerState.textContent = state;
    feedbackTitle.textContent = running ? 'Executando no computador' : 'Resultado da tarefa';
    stateValue.textContent = state;
    stepValue.textContent = step;
    jobValue.textContent = jobId;
    resultValue.textContent = output || 'Nenhuma saida final recebida ainda.';
    liveDot.classList.toggle('idle', !running);
    permissionRow.classList.toggle('visible', needsUser);
    setBusy(running);

    if (running) {
      appendChat('system', `Executando\n${step}`);
    } else if (lastJob.available && output) {
      appendChat(needsUser ? 'warn' : 'system', output);
    }
  };

  const callNative = payload => {
    if (typeof window.bonecoRunNative !== 'function') {
      showMessage('Ponte indisponivel', 'Ponte local indisponivel. Reabra o botao Comando sobre ChatGPT no painel.', false);
      return '';
    }

    const id = `${Date.now()}-${++seq}`;
    window.bonecoRunNative(JSON.stringify({
      id,
      ...payload
    }));
    return id;
  };

  window.addEventListener('__bonecoRunNativeResult', event => {
    const detail = event.detail || {};
    if (!detail.ok) {
      pendingStartId = '';
      showMessage('Falha', detail.error || 'erro desconhecido', false);
      return;
    }
    if (detail.type === 'start_job') {
      pendingStartId = '';
      textarea.value = '';
      resizeTextarea();
      showMessage('Job iniciado', `O computador recebeu a tarefa.\n${detail.job_id || ''}`, true);
      callNative({type: 'status'});
      return;
    }
    if (detail.type === 'status') {
      renderStatus(detail.status_payload || null, detail.status_text || 'Aguardando.');
    }
  });

  textarea.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send.click();
    }
  });
  textarea.addEventListener('input', resizeTextarea);

  send.addEventListener('click', () => {
    const goal = compact(textarea.value);
    if (!goal) {
      showMessage('Aguardando objetivo', 'Digite um objetivo antes de executar.', false);
      return;
    }
    pendingStartId = '';
    appendChat('user', goal);
    showMessage('Enviando', 'Enviando comando para o computador.', true);
    const requestId = callNative({type: 'start_job', goal});
    if (!requestId) return;
    pendingStartId = requestId;
    setTimeout(() => {
      if (pendingStartId !== requestId) return;
      pendingStartId = '';
      showMessage('Sem resposta', 'A janela nao recebeu confirmacao da ponte local. O sistema vai conferir o estado real automaticamente.', false);
      callNative({type: 'status'});
    }, 30000);
  });

  const sendPermission = decision => {
    const label = decision === 'deny'
      ? 'Autorizacao negada pelo usuario.'
      : decision === 'always'
        ? 'Usuario ativou Permitir sempre e autorizou continuar.'
        : 'Usuario autorizou continuar.';
    appendChat(decision === 'deny' ? 'warn' : 'user', label);
    showMessage('Enviando resposta', label, true);
    callNative({type: 'permission', decision});
  };

  permit.addEventListener('click', () => sendPermission('permit'));
  deny.addEventListener('click', () => sendPermission('deny'));
  always.addEventListener('click', () => sendPermission('always'));

  if (!window.__bonecoRunReloadGuardInstalled) {
    window.__bonecoRunReloadGuardInstalled = true;
    window.addEventListener('keydown', event => {
      const key = String(event.key || '').toLowerCase();
      const isReloadKey = key === 'f5' || ((event.ctrlKey || event.metaKey) && key === 'r');
      if (!isReloadKey) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      callNative({type: 'status'});
      return false;
    }, true);
  }

  setInterval(() => {
    setBonecoWindowIdentity();
    callNative({type: 'status'});
  }, 5000);
  resizeTextarea();
  setTimeout(() => callNative({type: 'status'}), 250);

  return {ok: true, overlay: 'created', fullscreen: true, close_button: false};
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
        self.sock.settimeout(1.0)

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

        return {}

    def send_frame(self, value: dict) -> None:
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

    def command(self, method: str, params: dict | None = None) -> int:
        self.ident += 1
        ident = self.ident
        self.send_frame({
            "id": ident,
            "method": method,
            "params": params or {},
        })
        return ident

    def wait_response(self, ident: int, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                message = self.receive()
            except socket.timeout:
                continue

            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message

            if message:
                handle_event(self, message)

        raise RuntimeError(f"timeout aguardando resposta CDP {ident}")

    def call(self, method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
        ident = self.command(method, params)
        return self.wait_response(ident, timeout=timeout)

    def evaluate(self, expression: str, timeout: float = 5.0):
        response = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        return (
            response.get("result", {})
            .get("result", {})
            .get("value")
        )


def dispatch_result(cdp: CDP, payload: dict) -> None:
    expression = (
        "window.dispatchEvent(new CustomEvent('__bonecoRunNativeResult', "
        "{detail: "
        + json.dumps(payload, ensure_ascii=False)
        + "}));"
    )
    try:
        cdp.evaluate(expression, timeout=3.0)
    except Exception as exc:
        print(f"overlay_dispatch_error={type(exc).__name__}: {exc}")


def resize_browser_window(cdp: CDP, width: int = 1280, height: int = 720) -> None:
    try:
        window = cdp.call("Browser.getWindowForTarget", timeout=3.0)
        window_id = window.get("result", {}).get("windowId")
        if not window_id:
            return
        cdp.call(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "windowState": "normal",
                    "left": 80,
                    "top": 60,
                    "width": width,
                    "height": height,
                },
            },
            timeout=3.0,
        )
    except Exception as exc:
        print(f"window_resize_warning={type(exc).__name__}: {exc}")


def handle_binding(cdp: CDP, payload_text: str) -> None:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        dispatch_result(cdp, {"ok": False, "type": "unknown", "error": f"JSON invalido: {exc}"})
        return

    request_id = str(payload.get("id") or "")
    request_type = str(payload.get("type") or "")

    try:
        if request_type == "status":
            status = panel_request("GET", "/api/status")
            dispatch_result(
                cdp,
                {
                    "ok": True,
                    "id": request_id,
                    "type": "status",
                    "status_text": status_text(status),
                    "status_payload": status,
                },
            )
            return

        if request_type == "start_job":
            goal = safe_text(payload.get("goal"), 3000)
            if not goal:
                raise RuntimeError("Digite um objetivo antes de executar.")
            result = panel_request("POST", "/api/job/start", {"goal": goal})
            dispatch_result(
                cdp,
                {
                    "ok": True,
                    "id": request_id,
                    "type": "start_job",
                    "job_id": result.get("job_id"),
                },
            )
            return

        if request_type == "permission":
            decision = str(payload.get("decision") or "").strip().lower()
            if decision not in {"permit", "deny", "always"}:
                raise RuntimeError("decisao de autorizacao invalida")

            if decision == "always":
                panel_request("POST", "/api/config", {"access_scope": "full"})
                goal = (
                    "O usuario ativou Permitir sempre / Full acesso neste computador Windows "
                    "e autorizou continuar a tarefa anterior. Continue a partir do ultimo ponto, "
                    "executando apenas o necessario e retornando resumo e validacao."
                )
            elif decision == "permit":
                goal = (
                    "O usuario autorizou continuar a tarefa anterior. Continue a partir do ultimo "
                    "pedido de autorizacao, executando apenas o necessario e retornando resumo e validacao."
                )
            else:
                goal = (
                    "O usuario negou a autorizacao solicitada. Nao execute a acao sensivel anterior. "
                    "Explique no resumo final que a tarefa foi interrompida por decisao do usuario."
                )

            result = panel_request("POST", "/api/job/start", {"goal": goal})
            dispatch_result(
                cdp,
                {
                    "ok": True,
                    "id": request_id,
                    "type": "start_job",
                    "job_id": result.get("job_id"),
                },
            )
            return

        raise RuntimeError("acao desconhecida")
    except Exception as exc:
        dispatch_result(
            cdp,
            {
                "ok": False,
                "id": request_id,
                "type": request_type or "unknown",
                "error": safe_text(f"{type(exc).__name__}: {exc}", 500),
            },
        )


def handle_event(cdp: CDP, message: dict) -> None:
    if message.get("method") != "Runtime.bindingCalled":
        return

    params = message.get("params") or {}
    if params.get("name") != BINDING_NAME:
        return

    handle_binding(cdp, str(params.get("payload") or ""))


def overlay_present(cdp: CDP) -> bool:
    try:
        return bool(cdp.evaluate("!!document.getElementById('boneco-run-overlay-host')", timeout=3.0))
    except Exception:
        return False


def install_overlay_preload(cdp: CDP) -> None:
    if getattr(cdp, "_boneco_overlay_preload", False):
        return

    try:
        cdp.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": overlay_expression()},
            timeout=5.0,
        )
        cdp._boneco_overlay_preload = True
    except Exception as exc:
        print(f"overlay_preload_warning={type(exc).__name__}: {exc}")


def install_overlay(cdp: CDP) -> None:
    cdp.call("Runtime.enable", timeout=5.0)
    resize_browser_window(cdp)
    try:
        cdp.call("Runtime.addBinding", {"name": BINDING_NAME}, timeout=5.0)
    except RuntimeError as exc:
        if "already" not in str(exc).lower() and "existe" not in str(exc).lower():
            raise
    install_overlay_preload(cdp)
    result = cdp.evaluate(overlay_expression(), timeout=5.0)
    print("overlay_result=" + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


def run_bridge(port: int, max_seconds: float = 0.0) -> int:
    cdp = CDP(port)
    install_overlay(cdp)
    print("status=overlay_bridge_running")
    print(f"port={port}")
    sys.stdout.flush()

    started = time.monotonic()
    next_check = time.monotonic() + 2.0

    while True:
        if max_seconds > 0 and time.monotonic() - started >= max_seconds:
            print("status=overlay_bridge_timeout")
            return 0

        try:
            message = cdp.receive()
            if message:
                handle_event(cdp, message)
        except socket.timeout:
            pass

        if time.monotonic() >= next_check:
            if not overlay_present(cdp):
                install_overlay(cdp)
            next_check = time.monotonic() + 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ponte local do overlay BONECO RUN no ChatGPT.")
    parser.add_argument("--port", type=int, default=9227)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    args = parser.parse_args()

    try:
        return run_bridge(args.port, args.max_seconds)
    except Exception as exc:
        print("status=error")
        print(f"erro={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

const state = {
  status: null,
  pairingCode: "",
  selectedLog: "",
  update: null,
};

const titles = {
  command: ["Comando", "Controle o computador Windows selecionado."],
  mobile: ["Celular", "Pareamento e agente local do PC."],
  browser: ["ChatGPT", "Canais separados para projeto e API local."],
  api: ["API Local", "Chat local para testes de integracao."],
  project: ["Projeto", "Pasta, URL alvo e gateway."],
  logs: ["Logs", "Saida tecnica do servico Windows."],
};

function $(id) {
  return document.getElementById(id);
}

function text(id, value) {
  const node = $(id);
  if (node) node.textContent = value == null || value === "" ? "-" : String(value);
}

function value(id, nextValue) {
  const node = $(id);
  if (node && document.activeElement !== node) node.value = nextValue || "";
}

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.style.borderColor = isError ? "#81383d" : "#34414e";
  node.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("visible"), 3500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {"Content-Type": "application/json"},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }

  return payload;
}

function compact(value, fallback = "-") {
  const textValue = String(value || "").trim();
  return textValue || fallback;
}

function browserReady(channel) {
  const browser = state.status?.browser?.[channel] || {};
  return browser.cdp_ready === "true" || browser.cdp_ready === true;
}

function browserSessionSaved(channel) {
  const browser = state.status?.browser?.[channel] || {};
  return browser.session_saved === "true" || browser.session_saved === true;
}

function refreshUi(payload) {
  state.status = payload;
  const activity = payload.activity || {};
  const mobile = payload.mobile || {};
  const localApi = payload.local_api || {};
  const config = payload.config || {};
  const lastJob = payload.last_job || {};

  const jobRunning = payload.job && payload.job.running;
  const currentState = jobRunning
    ? "executando"
    : compact(activity.state, "aguardando");

  text("jobState", currentState);
  text("jobStep", compact(activity.step, jobRunning ? "Processando no Windows" : "Sem tarefa ativa"));
  text("mobileState", mobile.agent_running ? "Agente ativo" : mobile.token_exists ? "Registrado" : "Nao registrado");
  text("projectBrowserState", browserReady("project")
    ? browserSessionSaved("project") ? "Sessao salva" : "Pronto"
    : "Parado");
  text("compactStatus", jobRunning ? "Executando" : browserReady("project") ? "Pronto" : "Aguardando");

  $("statusDot").className = "dot " + (jobRunning || browserReady("project") ? "ok" : "error");

  value("projectDirInput", config.project_dir);
  value("accessScopeSelect", config.access_scope || "computer");
  value("projectTargetUrlInput", config.project_target_url);
  value("gatewayUrlInput", config.gateway_url);
  value("updateManifestUrlInput", config.update_manifest_url);
  value("chatgptProjectUrl", config.chatgpt_project_url || payload.browser?.project?.url);
  value("chatgptApiUrl", config.chatgpt_api_url || payload.browser?.api?.url);
  text("localApiEndpoint", config.local_api_endpoint || "http://127.0.0.1:8765/chat");
  text("apiStatus", JSON.stringify(localApi, null, 2));

  const resultLines = [];
  if (lastJob.summary) resultLines.push(`Resultado\n${lastJob.summary}`);
  if (lastJob.validation) resultLines.push(`Validacao\n${lastJob.validation}`);
  if (!resultLines.length) {
    resultLines.push(jobRunning
      ? `Andamento\n${compact(activity.step, "Processando no Windows")}`
      : `Status\n${compact(activity.step, "Sem tarefa ativa")}`);
  }
  text("lastResult", resultLines.join("\n\n"));
}

async function refreshStatus() {
  try {
    refreshUi(await api("/api/status"));
  } catch (error) {
    text("compactStatus", "Daemon indisponivel");
    $("statusDot").className = "dot error";
    toast(error.message, true);
  }
}

async function checkUpdate(silent = false) {
  const button = $("checkUpdateBtn");

  try {
    const result = await api("/api/update/check");
    state.update = result;

    if (result.available) {
      button.textContent = `Atualizacao ${result.latest_version}`;
      button.classList.add("warn");
      if (!silent) {
        const openDownload = result.download_url && window.confirm(
          `${result.message}\n\nDeseja abrir o download do instalador?`
        );
        if (openDownload) window.open(result.download_url, "_blank", "noopener");
      } else {
        toast(result.message);
      }
      return result;
    }

    button.textContent = "Atualizado";
    button.classList.remove("warn");
    if (!silent) toast(result.message || "Sistema atualizado");
    return result;
  } catch (error) {
    button.textContent = "Atualizacao";
    button.classList.remove("warn");
    if (!silent) toast(error.message, true);
    return null;
  }
}

function setView(name) {
  for (const node of document.querySelectorAll(".view")) {
    node.classList.toggle("active", node.id === `view-${name}`);
  }
  for (const node of document.querySelectorAll(".nav-item")) {
    node.classList.toggle("active", node.dataset.view === name);
  }
  const [title, subtitle] = titles[name] || titles.command;
  text("viewTitle", title);
  text("viewSubtitle", subtitle);
}

async function runAction(label, fn) {
  try {
    const result = await fn();
    toast(label);
    await refreshStatus();
    return result;
  } catch (error) {
    toast(error.message, true);
    throw error;
  }
}

async function saveProjectConfig(extra = {}) {
  return api("/api/config", {
    method: "POST",
    body: {
      project_dir: $("projectDirInput").value,
      access_scope: $("accessScopeSelect").value,
      project_target_url: $("projectTargetUrlInput").value,
      gateway_url: $("gatewayUrlInput").value,
      update_manifest_url: $("updateManifestUrlInput").value,
      ...extra,
    },
  });
}

function bind() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });

  $("refreshBtn").addEventListener("click", refreshStatus);
  $("checkUpdateBtn").addEventListener("click", () => checkUpdate(false));
  $("openProjectBrowserBtn").addEventListener("click", () => {
    runAction("ChatGPT Projeto aberto", () => api("/api/browser/ensure", {
      method: "POST",
      body: {channel: "project"},
    }));
  });

  $("startJobBtn").addEventListener("click", async () => {
    const goal = $("goalInput").value.trim();
    const result = await runAction("Tarefa enviada para a IA do projeto", () => api("/api/job/start", {
      method: "POST",
      body: {goal},
    }));
    $("goalInput").value = "";
    if (result?.job_id) toast(`Job iniciado: ${result.job_id}`);
  });

  $("cancelJobBtn").addEventListener("click", () => {
    runAction("Tarefa cancelada", () => api("/api/job/cancel", {method: "POST"}));
  });

  $("registerPcBtn").addEventListener("click", () => {
    const email = $("emailInput").value.trim();
    const password = $("passwordInput").value;
    runAction("PC registrado no gateway", () => api("/api/mobile/register", {
      method: "POST",
      body: {email, password},
    })).then(() => {
      $("passwordInput").value = "";
    });
  });

  $("pairingBtn").addEventListener("click", async () => {
    const result = await runAction("Codigo de pareamento gerado", () => api("/api/mobile/pairing", {
      method: "POST",
    }));
    state.pairingCode = result.pairing_code || "";
    text("pairingCode", state.pairingCode || "-");
  });

  $("copyPairingBtn").addEventListener("click", async () => {
    if (!state.pairingCode) {
      toast("Gere um codigo primeiro.", true);
      return;
    }
    await navigator.clipboard.writeText(state.pairingCode);
    toast("Codigo copiado");
  });

  $("startAgentBtn").addEventListener("click", () => {
    runAction("Agente mobile iniciado", () => api("/api/mobile/agent/start", {method: "POST"}));
  });

  $("stopAgentBtn").addEventListener("click", () => {
    runAction("Agente mobile parado", () => api("/api/mobile/agent/stop", {method: "POST"}));
  });

  $("saveProjectChatUrlBtn").addEventListener("click", () => {
    runAction("URL do ChatGPT Projeto salva", () => saveProjectConfig({
      chatgpt_project_url: $("chatgptProjectUrl").value,
    }));
  });

  $("saveApiChatUrlBtn").addEventListener("click", () => {
    runAction("URL do ChatGPT API Local salva", () => saveProjectConfig({
      chatgpt_api_url: $("chatgptApiUrl").value,
    }));
  });

  $("ensureProjectBtn").addEventListener("click", () => {
    runAction("ChatGPT Projeto aberto", () => api("/api/browser/ensure", {
      method: "POST",
      body: {channel: "project"},
    }));
  });

  $("overlayProjectBtn").addEventListener("click", () => {
    runAction("Comando sobre ChatGPT ativado", () => api("/api/browser/overlay", {
      method: "POST",
      body: {channel: "project"},
    }));
  });

  $("loginProjectChatBtn").addEventListener("click", () => {
    runAction("Janela de login do ChatGPT aberta", () => api("/api/browser/login", {
      method: "POST",
      body: {channel: "project", mode: "chatgpt"},
    }));
  });

  $("loginProjectGoogleBtn").addEventListener("click", () => {
    runAction("Perfil Google Chrome aberto", () => api("/api/browser/login", {
      method: "POST",
      body: {channel: "project", mode: "google-profile"},
    }));
  });

  $("ensureApiBtn").addEventListener("click", () => {
    runAction("ChatGPT API Local aberto", () => api("/api/browser/ensure", {
      method: "POST",
      body: {channel: "api"},
    }));
  });

  $("loginApiChatBtn").addEventListener("click", () => {
    runAction("Login do ChatGPT API Local aberto", () => api("/api/browser/login", {
      method: "POST",
      body: {channel: "api", mode: "chatgpt"},
    }));
  });

  $("loginApiGoogleBtn").addEventListener("click", () => {
    runAction("Perfil Google Chrome da API aberto", () => api("/api/browser/login", {
      method: "POST",
      body: {channel: "api", mode: "google-profile"},
    }));
  });

  $("stopProjectBtn").addEventListener("click", () => {
    runAction("ChatGPT Projeto parado", () => api("/api/browser/stop", {
      method: "POST",
      body: {channel: "project"},
    }));
  });

  $("stopApiBtn").addEventListener("click", () => {
    runAction("ChatGPT API Local parado", () => api("/api/browser/stop", {
      method: "POST",
      body: {channel: "api"},
    }));
  });

  $("startLocalApiBtn").addEventListener("click", () => {
    runAction("API Local iniciada", () => api("/api/local-api/start", {method: "POST"}));
  });

  $("saveProjectConfigBtn").addEventListener("click", () => {
    runAction("Configuracao salva", () => saveProjectConfig());
  });

  $("selectProjectFolderBtn").addEventListener("click", async () => {
    try {
      const result = await api("/api/project/select-folder", {method: "POST"});
      if (result?.cancelled) {
        toast("Selecao cancelada");
        return;
      }
      if (result?.project_dir) $("projectDirInput").value = result.project_dir;
      toast("Pasta selecionada");
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("useRootFolderBtn").addEventListener("click", () => {
    const root = state.status?.root || "";
    $("projectDirInput").value = root;
    runAction("Pasta do Boneco salva", () => saveProjectConfig({project_dir: root}));
  });

  document.querySelectorAll(".log-choice").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedLog = button.dataset.log;
      document.querySelectorAll(".log-choice").forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      await refreshSelectedLog();
    });
  });
}

async function refreshSelectedLog(silent = false) {
  if (!state.selectedLog) return;

  try {
    const result = await api(`/api/logs?name=${encodeURIComponent(state.selectedLog)}`);
    text("logText", result.text || "Log vazio.");
  } catch (error) {
    if (!silent) text("logText", error.message);
  }
}

bind();
setView("command");
refreshStatus();
setTimeout(() => checkUpdate(true), 1500);
setInterval(() => {
  refreshStatus();
  refreshSelectedLog(true);
}, 5000);
setInterval(() => checkUpdate(true), 60 * 60 * 1000);

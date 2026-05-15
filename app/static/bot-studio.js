const state = {
  bots: [],
  activeBotId: null,
  activeBot: null,
};

const elements = {
  botList: document.getElementById("bot-list"),
  botRefreshBtn: document.getElementById("bot-refresh-btn"),
  seedDefaultBotBtn: document.getElementById("seed-default-bot-btn"),
  createForm: document.getElementById("bot-create-form"),
  detailName: document.getElementById("bot-detail-name"),
  detailBadge: document.getElementById("bot-detail-badge"),
  engineValue: document.getElementById("bot-engine-value"),
  endpointValue: document.getElementById("bot-endpoint-value"),
  projectValue: document.getElementById("bot-project-value"),
  channelValue: document.getElementById("bot-channel-value"),
  descriptionCard: document.getElementById("bot-description-card"),
  variableList: document.getElementById("bot-variable-list"),
  apiList: document.getElementById("bot-api-list"),
  instructionList: document.getElementById("bot-instruction-list"),
  envExample: document.getElementById("bot-env-example"),
  inboundExample: document.getElementById("bot-inbound-example"),
  outboundExample: document.getElementById("bot-outbound-example"),
  connectTestBotBtn: document.getElementById("connect-test-bot-btn"),
  disconnectTestBotBtn: document.getElementById("disconnect-test-bot-btn"),
  activateBotBtn: document.getElementById("activate-bot-btn"),
  deactivateBotBtn: document.getElementById("deactivate-bot-btn"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }

  return payload;
}

function formatEnvExample(envExample) {
  return Object.entries(envExample || {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function prettifyJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

function normalizeTruthText(value) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return ["true", "1", "yes", "y", "required", "req"].includes(normalized);
}

function parseVariableDefinitions(source) {
  return String(source || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => {
      const [key = "", required = "true", defaultValue = "", description = ""] = line.split("|");
      return {
        key: key.trim().toUpperCase(),
        required: normalizeTruthText(required),
        default_value: defaultValue.trim(),
        description: description.trim(),
      };
    })
    .filter((item) => item.key);
}

function parseApiBindings(source) {
  return String(source || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => {
      const [name = "", kind = "http", endpointUrl = "", notes = ""] = line.split("|");
      return {
        name: name.trim(),
        kind: kind.trim() || "http",
        endpoint_url: endpointUrl.trim(),
        notes: notes.trim(),
      };
    })
    .filter((item) => item.name);
}

function renderBotList() {
  if (!elements.botList) {
    return;
  }

  if (!state.bots.length) {
    elements.botList.innerHTML =
      '<div class="empty-state-card">Пока нет зарегистрированных ботов. Можно начать с дефолтного Dify-бота или добавить свой.</div>';
    return;
  }

  elements.botList.innerHTML = state.bots
    .map((bot) => {
      const isActive = bot.id === state.activeBotId;
      const statusClass = bot.enabled ? "status-badge-connected" : "status-badge-warning";
      const statusText = bot.enabled ? "Активен" : "Деактивирован";
      const badgeText = bot.is_default_template ? "Default template" : "Custom bot";
      const connectionPill = bot.test_connected ? '<span class="pill">test connected</span>' : "";

      return `
        <button class="bot-list-card${isActive ? " is-active" : ""}" type="button" data-bot-id="${escapeHtml(bot.id)}">
          <div class="bot-list-card-top">
            <div>
              <strong>${escapeHtml(bot.name)}</strong>
              <span>${escapeHtml(bot.slug)}</span>
            </div>
            <span class="status-badge ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <p>${escapeHtml(bot.description || "Описание пока не заполнено.")}</p>
          <div class="bot-card-meta">
            <span class="pill">${escapeHtml(bot.engine_type)}</span>
            <span class="pill">${escapeHtml(badgeText)}</span>
            <span class="pill">${escapeHtml(`${bot.variable_count} vars`)}</span>
            <span class="pill">${escapeHtml(`${bot.api_binding_count} apis`)}</span>
            ${connectionPill}
          </div>
        </button>
      `;
    })
    .join("");

  elements.botList.querySelectorAll("[data-bot-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const botId = button.getAttribute("data-bot-id");
      if (botId) {
        loadBotDetail(botId);
      }
    });
  });
}

function updateConnectionButtons(bot) {
  const hasBot = Boolean(bot && bot.id);
  const enabled = Boolean(bot?.enabled);
  const connected = Boolean(bot?.test_connected);

  if (elements.connectTestBotBtn) {
    elements.connectTestBotBtn.disabled = !hasBot || !enabled || connected;
    if (!hasBot) {
      elements.connectTestBotBtn.textContent = "Подключить тестового бота";
    } else if (!enabled) {
      elements.connectTestBotBtn.textContent = "Сначала активируйте бота";
    } else if (connected) {
      elements.connectTestBotBtn.textContent = "Тестовый бот уже подключён";
    } else {
      elements.connectTestBotBtn.textContent = "Подключить тестового бота";
    }
  }

  if (elements.disconnectTestBotBtn) {
    elements.disconnectTestBotBtn.disabled = !hasBot || !connected;
    elements.disconnectTestBotBtn.textContent = "Отключить тестового бота";
  }

  if (elements.activateBotBtn) {
    elements.activateBotBtn.disabled = !hasBot || enabled;
    elements.activateBotBtn.textContent = "Активировать бота";
  }

  if (elements.deactivateBotBtn) {
    elements.deactivateBotBtn.disabled = !hasBot || !enabled;
    elements.deactivateBotBtn.textContent = "Деактивировать бота";
  }
}

function resetDetailView() {
  state.activeBot = null;
  elements.detailName.textContent = "Выберите бота";
  elements.detailBadge.textContent = "Ожидание";
  elements.detailBadge.className = "status-badge status-badge-pending";
  elements.engineValue.textContent = "-";
  elements.endpointValue.textContent = "-";
  elements.projectValue.textContent = "-";
  elements.channelValue.textContent = "-";
  elements.descriptionCard.textContent =
    "Выберите бота слева, чтобы посмотреть его переменные, API-связки и инструкцию по подключению.";
  elements.variableList.innerHTML = '<div class="empty-state-card">Здесь появится список переменных бота.</div>';
  elements.apiList.innerHTML = '<div class="empty-state-card">Здесь появятся внешние API и webhook-связки.</div>';
  elements.instructionList.innerHTML =
    '<div class="empty-state-card">После выбора бота здесь появится пошаговая инструкция.</div>';
  elements.envExample.textContent = "# Выберите бота, чтобы увидеть рекомендованный .env набор.";
  elements.inboundExample.textContent = "{}";
  elements.outboundExample.textContent = "{}";
  updateConnectionButtons(null);
}

function renderDetailList(items, kind) {
  if (!items.length) {
    return '<div class="empty-state-card">Пока пусто.</div>';
  }

  if (kind === "variables") {
    return items
      .map(
        (item) => `
          <article class="bot-data-card">
            <div class="bot-data-card-head">
              <strong>${escapeHtml(item.key)}</strong>
              <span class="pill">${item.required ? "required" : "optional"}</span>
            </div>
            <p>${escapeHtml(item.description || "Описание не заполнено.")}</p>
            <code>${escapeHtml(item.default_value || "-")}</code>
          </article>
        `,
      )
      .join("");
  }

  return items
    .map(
      (item) => `
        <article class="bot-data-card">
          <div class="bot-data-card-head">
            <strong>${escapeHtml(item.name)}</strong>
            <span class="pill">${escapeHtml(item.kind || "http")}</span>
          </div>
          <p>${escapeHtml(item.notes || "Описание связи не заполнено.")}</p>
          <code>${escapeHtml(item.endpoint_url || "-")}</code>
        </article>
      `,
    )
    .join("");
}

function renderBotDetail(bot) {
  state.activeBotId = bot.id;
  state.activeBot = bot;
  renderBotList();

  elements.detailName.textContent = bot.name || "Без названия";
  elements.detailBadge.textContent = bot.test_connected
    ? "Подключён к WhatsApp"
    : bot.enabled
      ? "Готов"
      : "Деактивирован";
  elements.detailBadge.className = `status-badge ${
    bot.test_connected ? "status-badge-connected" : bot.enabled ? "status-badge-pending" : "status-badge-warning"
  }`;
  elements.engineValue.textContent = bot.engine_type || "-";
  elements.endpointValue.textContent = bot.endpoint_url || "Не указан";
  elements.projectValue.textContent = bot.linked_project_id || "Не привязан";
  elements.channelValue.textContent = bot.connected_channel_keys?.[0] || bot.linked_channel_key || "Не указан";
  elements.descriptionCard.textContent = bot.workflow_summary || bot.description || "Описание пока не заполнено.";
  elements.variableList.innerHTML = renderDetailList(bot.variables || [], "variables");
  elements.apiList.innerHTML = renderDetailList(bot.api_bindings || [], "api");
  elements.instructionList.innerHTML = (bot.platform_instructions || []).length
    ? (bot.platform_instructions || [])
        .map((item) => `<article class="instruction-card">${escapeHtml(item)}</article>`)
        .join("")
    : '<div class="empty-state-card">Инструкция пока не заполнена.</div>';
  elements.envExample.textContent = formatEnvExample(bot.env_example);
  elements.inboundExample.textContent = prettifyJson(bot.inbound_example);
  elements.outboundExample.textContent = prettifyJson(bot.outbound_example);
  updateConnectionButtons(bot);
}

async function loadBotDetail(botId) {
  try {
    const bot = await requestJson(`/api/v1/platform/bots/${encodeURIComponent(botId)}`);
    renderBotDetail(bot);
  } catch (error) {
    resetDetailView();
    elements.descriptionCard.textContent = `Не удалось загрузить бота: ${error.message}`;
  }
}

async function loadBotList({ preserveSelection = true } = {}) {
  try {
    state.bots = await requestJson("/api/v1/platform/bots");
    renderBotList();

    if (!state.bots.length) {
      state.activeBotId = null;
      resetDetailView();
      return;
    }

    const currentId = preserveSelection ? state.activeBotId : null;
    const nextActiveId = currentId && state.bots.some((bot) => bot.id === currentId) ? currentId : state.bots[0].id;
    await loadBotDetail(nextActiveId);
  } catch (error) {
    elements.botList.innerHTML = `<div class="empty-state-card">Не удалось загрузить реестр ботов: ${escapeHtml(error.message)}</div>`;
    resetDetailView();
  }
}

async function seedDefaultBot() {
  elements.seedDefaultBotBtn.disabled = true;
  elements.seedDefaultBotBtn.textContent = "Добавляем...";
  try {
    const bot = await requestJson("/api/v1/platform/bots/default", {
      method: "POST",
    });
    await loadBotList({ preserveSelection: false });
    renderBotDetail(bot);
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось добавить дефолтный бот: ${error.message}`;
  } finally {
    elements.seedDefaultBotBtn.disabled = false;
    elements.seedDefaultBotBtn.textContent = "Добавить дефолтный бот";
  }
}

async function connectTestBot() {
  if (!state.activeBot?.id || !elements.connectTestBotBtn) {
    return;
  }

  elements.connectTestBotBtn.disabled = true;
  elements.connectTestBotBtn.textContent = "Подключаем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/connect-test`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось подключить тестового бота: ${error.message}`;
    updateConnectionButtons(state.activeBot);
  }
}

async function disconnectTestBot() {
  if (!state.activeBot?.id || !elements.disconnectTestBotBtn) {
    return;
  }

  elements.disconnectTestBotBtn.disabled = true;
  elements.disconnectTestBotBtn.textContent = "Отключаем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/disconnect-test`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось отключить тестового бота: ${error.message}`;
    updateConnectionButtons(state.activeBot);
  } finally {
    if (elements.disconnectTestBotBtn) {
      elements.disconnectTestBotBtn.textContent = "Отключить тестового бота";
    }
  }
}

async function activateBot() {
  if (!state.activeBot?.id || !elements.activateBotBtn) {
    return;
  }

  elements.activateBotBtn.disabled = true;
  elements.activateBotBtn.textContent = "Активируем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/activate`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось активировать бота: ${error.message}`;
    updateConnectionButtons(state.activeBot);
  } finally {
    if (elements.activateBotBtn) {
      elements.activateBotBtn.textContent = "Активировать бота";
    }
  }
}

async function deactivateBot() {
  if (!state.activeBot?.id || !elements.deactivateBotBtn) {
    return;
  }

  elements.deactivateBotBtn.disabled = true;
  elements.deactivateBotBtn.textContent = "Деактивируем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/deactivate`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось деактивировать бота: ${error.message}`;
    updateConnectionButtons(state.activeBot);
  } finally {
    if (elements.deactivateBotBtn) {
      elements.deactivateBotBtn.textContent = "Деактивировать бота";
    }
  }
}

async function handleCreateBot(event) {
  event.preventDefault();

  const formData = new FormData(elements.createForm);
  const payload = {
    name: String(formData.get("name") || "").trim(),
    slug: String(formData.get("slug") || "").trim(),
    description: String(formData.get("description") || "").trim(),
    engine_type: String(formData.get("engine_type") || "custom").trim(),
    endpoint_url: String(formData.get("endpoint_url") || "").trim(),
    authorization_header: String(formData.get("authorization_header") || "").trim(),
    owner_label: String(formData.get("owner_label") || "").trim(),
    workflow_summary: String(formData.get("workflow_summary") || "").trim(),
    linked_project_id: String(formData.get("linked_project_id") || "").trim(),
    linked_channel_key: String(formData.get("linked_channel_key") || "").trim(),
    enabled: Boolean(formData.get("enabled")),
    variables: parseVariableDefinitions(document.getElementById("bot-variables-input").value),
    api_bindings: parseApiBindings(document.getElementById("bot-api-bindings-input").value),
  };

  const submitButton = elements.createForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = "Сохраняем...";

  try {
    const bot = await requestJson("/api/v1/platform/bots", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    elements.createForm.reset();
    document.getElementById("bot-channel-input").value = "platform-main";
    document.getElementById("bot-enabled-input").checked = true;
    await loadBotList({ preserveSelection: false });
    renderBotDetail(bot);
  } catch (error) {
    elements.descriptionCard.textContent = `Не удалось сохранить бота: ${error.message}`;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Сохранить бота";
  }
}

function enableRevealObserver() {
  if (typeof IntersectionObserver !== "function") {
    document.querySelectorAll(".reveal").forEach((element) => element.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
        }
      });
    },
    { threshold: 0.18 },
  );

  document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));
}

function wireEvents() {
  elements.botRefreshBtn?.addEventListener("click", () => {
    loadBotList();
  });
  elements.seedDefaultBotBtn?.addEventListener("click", () => {
    seedDefaultBot();
  });
  elements.connectTestBotBtn?.addEventListener("click", () => {
    connectTestBot();
  });
  elements.disconnectTestBotBtn?.addEventListener("click", () => {
    disconnectTestBot();
  });
  elements.activateBotBtn?.addEventListener("click", () => {
    activateBot();
  });
  elements.deactivateBotBtn?.addEventListener("click", () => {
    deactivateBot();
  });
  elements.createForm?.addEventListener("submit", handleCreateBot);
}

resetDetailView();
wireEvents();
enableRevealObserver();
loadBotList();

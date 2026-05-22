const state = {
  bots: [],
  activeBotId: null,
  activeBot: null,
};

const elements = {
  botList: document.getElementById("bot-list"),
  botRefreshBtn: document.getElementById("bot-refresh-btn"),
  createForm: document.getElementById("bot-create-form"),
  detailName: document.getElementById("bot-detail-name"),
  detailBadge: document.getElementById("bot-detail-badge"),
  engineValue: document.getElementById("bot-engine-value"),
  endpointValue: document.getElementById("bot-endpoint-value"),
  channelValue: document.getElementById("bot-channel-value"),
  projectValue: document.getElementById("bot-project-value"),
  fullUrlPreview: document.getElementById("project-full-url-preview"),
  projectFields: document.getElementById("project-integration-fields"),
  difyFields: document.getElementById("dify-fields"),
  descriptionCard: document.getElementById("bot-description-card"),
  connectBotBtn: document.getElementById("connect-test-bot-btn"),
  disconnectBotBtn: document.getElementById("disconnect-test-bot-btn"),
  deleteBotBtn: document.getElementById("delete-bot-btn"),
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

function slugify(value) {
  const base = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 42);
  return `${base || "bot"}-${Date.now().toString(36)}`;
}

function normalizeAuthorization(value) {
  const cleaned = String(value || "").trim();
  if (!cleaned) {
    return "";
  }
  return cleaned.toLowerCase().startsWith("bearer ") ? cleaned : `Bearer ${cleaned}`;
}

function readableEngine(value) {
  return {
    webhook: "Webhook",
    n8n: "n8n",
    dify: "Dify",
    custom: "Custom",
  }[String(value || "").toLowerCase()] || "Webhook";
}

function joinUrlPath(baseUrl, pathValue) {
  const base = String(baseUrl || "").trim().replace(/\/+$/, "");
  const path = String(pathValue || "").trim();
  if (!base) {
    return "";
  }
  if (!path) {
    return base;
  }
  return `${base}/${path.replace(/^\/+/, "")}`;
}

function updateFullUrlPreview() {
  if (!elements.fullUrlPreview || !elements.createForm) {
    return;
  }
  const formData = new FormData(elements.createForm);
  const engineType = String(formData.get("engine_type") || "webhook");
  const fullUrl =
    engineType === "dify"
      ? String(formData.get("dify_base_url") || "").trim()
      : joinUrlPath(formData.get("project_base_url"), formData.get("project_path"));
  elements.fullUrlPreview.textContent =
    fullUrl || (engineType === "dify" ? "https://api.dify.ai/v1" : "https://project.example.com/api/whatsapp/incoming");
}

function updateTypeFields() {
  if (!elements.createForm) {
    return;
  }
  const formData = new FormData(elements.createForm);
  const engineType = String(formData.get("engine_type") || "webhook");
  const isDify = engineType === "dify";
  if (elements.projectFields) {
    elements.projectFields.hidden = isDify;
  }
  if (elements.difyFields) {
    elements.difyFields.hidden = !isDify;
  }

  const authInput = document.getElementById("bot-auth-input");
  if (authInput) {
    authInput.placeholder = isDify ? "Можно оставить пустым, если API Key указан выше" : "Можно оставить пустым";
  }
  updateFullUrlPreview();
}

function renderBotList() {
  if (!elements.botList) {
    return;
  }

  if (!state.bots.length) {
    elements.botList.innerHTML =
      '<div class="empty-state-card">Пока нет ботов. Добавьте своего бота выше или проверьте настройки дефолтного бота.</div>';
    return;
  }

  elements.botList.innerHTML = state.bots
    .map((bot) => {
      const isActive = bot.id === state.activeBotId;
      const connected = Boolean(bot.test_connected);
      const statusClass = connected ? "status-badge-connected" : bot.enabled ? "status-badge-pending" : "status-badge-warning";
      const statusText = connected ? "В WhatsApp" : bot.enabled ? "Готов" : "Выключен";
      const endpoint = bot.endpoint_url || "URL не указан";
      const botKind = bot.is_default_template ? "Дефолтный бот" : readableEngine(bot.engine_type);

      return `
        <button class="bot-list-card${isActive ? " is-active" : ""}" type="button" data-bot-id="${escapeHtml(bot.id)}">
          <div class="bot-list-card-top">
            <div>
              <strong>${escapeHtml(bot.name)}</strong>
              <span>${escapeHtml(botKind)}</span>
            </div>
            <span class="status-badge ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <p>${escapeHtml(endpoint)}</p>
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

function updateActionButtons(bot) {
  const hasBot = Boolean(bot?.id);
  const connected = Boolean(bot?.test_connected);

  if (elements.connectBotBtn) {
    elements.connectBotBtn.disabled = !hasBot || connected;
    elements.connectBotBtn.textContent = connected ? "Уже подключён" : "Подключить к WhatsApp";
  }
  if (elements.disconnectBotBtn) {
    elements.disconnectBotBtn.disabled = !hasBot || !connected;
  }
  if (elements.deleteBotBtn) {
    elements.deleteBotBtn.disabled = !hasBot || Boolean(bot?.is_default_template);
  }
}

function setMessage(message) {
  if (elements.descriptionCard) {
    elements.descriptionCard.textContent = message;
  }
}

function resetDetailView() {
  state.activeBot = null;
  if (elements.detailName) {
    elements.detailName.textContent = "Выберите бота";
  }
  if (elements.detailBadge) {
    elements.detailBadge.textContent = "Ожидание";
    elements.detailBadge.className = "status-badge status-badge-pending";
  }
  if (elements.engineValue) {
    elements.engineValue.textContent = "-";
  }
  if (elements.endpointValue) {
    elements.endpointValue.textContent = "-";
  }
  if (elements.channelValue) {
    elements.channelValue.textContent = "platform-main";
  }
  if (elements.projectValue) {
    elements.projectValue.textContent = "-";
  }
  setMessage("Выберите бота из списка или добавьте нового. Активный бот будет отвечать на входящие сообщения WhatsApp.");
  updateActionButtons(null);
}

function renderBotDetail(bot) {
  state.activeBotId = bot.id;
  state.activeBot = bot;
  renderBotList();

  const connected = Boolean(bot.test_connected);
  elements.detailName.textContent = bot.name || "Без названия";
  elements.detailBadge.textContent = connected ? "Работает в WhatsApp" : bot.enabled ? "Готов" : "Выключен";
  elements.detailBadge.className = `status-badge ${
    connected ? "status-badge-connected" : bot.enabled ? "status-badge-pending" : "status-badge-warning"
  }`;
  elements.engineValue.textContent = readableEngine(bot.engine_type);
  elements.endpointValue.textContent = bot.endpoint_url || "Не указан";
  elements.channelValue.textContent = bot.connected_channel_keys?.[0] || bot.linked_channel_key || "platform-main";
  if (elements.projectValue) {
    elements.projectValue.textContent = bot.linked_project_id || bot.owner_label || "-";
  }
  setMessage(
    connected
      ? "Бот подключён к WhatsApp. Новые входящие сообщения будут отправляться этому боту, а ответ вернётся в чат."
      : "Бот сохранён, но сейчас не подключён к WhatsApp.",
  );
  updateActionButtons(bot);
}

function formatDiagnosticsMessage(result) {
  const diagnostics = result?.diagnostics || {};
  if (result?.bot_ready || diagnostics.ok) {
    return "Бот подключён к WhatsApp и готов отвечать.";
  }
  return `Бот подключён, но endpoint требует проверки: ${diagnostics.reason || "нет успешной диагностики"}`;
}

async function loadBotDetail(botId) {
  try {
    const bot = await requestJson(`/api/v1/platform/bots/${encodeURIComponent(botId)}`);
    renderBotDetail(bot);
  } catch (error) {
    resetDetailView();
    setMessage(`Не удалось загрузить бота: ${error.message}`);
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
    const nextActiveId =
      currentId && state.bots.some((bot) => bot.id === currentId) ? currentId : state.bots[0].id;
    await loadBotDetail(nextActiveId);
  } catch (error) {
    elements.botList.innerHTML = `<div class="empty-state-card">Не удалось загрузить список ботов: ${escapeHtml(error.message)}</div>`;
    resetDetailView();
  }
}

async function connectBot() {
  if (!state.activeBot?.id || !elements.connectBotBtn) {
    return;
  }

  elements.connectBotBtn.disabled = true;
  elements.connectBotBtn.textContent = "Подключаем...";
  try {
    const result = await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/connect`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
    setMessage(formatDiagnosticsMessage(result));
  } catch (error) {
    setMessage(`Не удалось подключить бота: ${error.message}`);
    updateActionButtons(state.activeBot);
  }
}

async function disconnectBot() {
  if (!state.activeBot?.id || !elements.disconnectBotBtn) {
    return;
  }

  elements.disconnectBotBtn.disabled = true;
  elements.disconnectBotBtn.textContent = "Отключаем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(state.activeBot.id)}/disconnect`, {
      method: "POST",
    });
    await loadBotList({ preserveSelection: true });
    setMessage("Бот отключён от WhatsApp.");
  } catch (error) {
    setMessage(`Не удалось отключить бота: ${error.message}`);
    updateActionButtons(state.activeBot);
  } finally {
    elements.disconnectBotBtn.textContent = "Отключить";
  }
}

async function deleteBot() {
  if (!state.activeBot?.id || !elements.deleteBotBtn || state.activeBot.is_default_template) {
    return;
  }

  const confirmed = window.confirm(`Удалить бота "${state.activeBot.name || state.activeBot.slug}"?`);
  if (!confirmed) {
    return;
  }

  const deletedBotId = state.activeBot.id;
  elements.deleteBotBtn.disabled = true;
  elements.deleteBotBtn.textContent = "Удаляем...";
  try {
    await requestJson(`/api/v1/platform/bots/${encodeURIComponent(deletedBotId)}`, {
      method: "DELETE",
    });
    state.activeBotId = null;
    state.activeBot = null;
    await loadBotList({ preserveSelection: false });
    setMessage("Бот удалён.");
  } catch (error) {
    setMessage(`Не удалось удалить бота: ${error.message}`);
    updateActionButtons(state.activeBot);
  } finally {
    elements.deleteBotBtn.textContent = "Удалить";
  }
}

async function handleCreateBot(event) {
  event.preventDefault();

  const formData = new FormData(elements.createForm);
  const name = String(formData.get("name") || "").trim();
  const projectId = String(formData.get("linked_project_id") || "").trim();
  const engineType = String(formData.get("engine_type") || "webhook").trim();
  const isDify = engineType === "dify";
  const endpointUrl = isDify
    ? String(formData.get("dify_base_url") || "").trim().replace(/\/+$/, "")
    : joinUrlPath(formData.get("project_base_url"), formData.get("project_path"));
  const difyApiKey = String(formData.get("dify_api_key") || "").trim();
  const authorizationHeader = normalizeAuthorization(difyApiKey || formData.get("authorization_header"));

  const payload = {
    name,
    slug: slugify(name),
    description: projectId ? `WhatsApp integration for ${projectId}` : `WhatsApp integration: ${name}`,
    engine_type: engineType,
    endpoint_url: endpointUrl,
    authorization_header: authorizationHeader,
    owner_label: projectId,
    workflow_summary: "",
    linked_project_id: projectId,
    linked_channel_key: "platform-main",
    enabled: true,
    variables: [],
    api_bindings: [],
  };

  const submitButton = elements.createForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = "Сохраняем и подключаем...";

  try {
    const bot = await requestJson("/api/v1/platform/bots", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const result = await requestJson(`/api/v1/platform/bots/${encodeURIComponent(bot.id)}/connect`, {
      method: "POST",
    });

    elements.createForm.reset();
    await loadBotList({ preserveSelection: false });
    await loadBotDetail(bot.id);
    setMessage(formatDiagnosticsMessage(result));
  } catch (error) {
    setMessage(`Не удалось добавить бота: ${error.message}`);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Сохранить и подключить";
  }
}

function wireEvents() {
  elements.botRefreshBtn?.addEventListener("click", () => {
    loadBotList();
  });
  elements.connectBotBtn?.addEventListener("click", connectBot);
  elements.disconnectBotBtn?.addEventListener("click", disconnectBot);
  elements.deleteBotBtn?.addEventListener("click", deleteBot);
  elements.createForm?.addEventListener("submit", handleCreateBot);
  elements.createForm?.addEventListener("input", updateTypeFields);
  elements.createForm?.addEventListener("change", updateTypeFields);
}

resetDetailView();
wireEvents();
updateTypeFields();
loadBotList();
const botStudioNavLinks = document.querySelector(".nav-links");
if (botStudioNavLinks && !botStudioNavLinks.querySelector('[href="/logout"]')) {
  const logoutLink = document.createElement("a");
  logoutLink.href = "/logout";
  logoutLink.textContent = "Выйти";
  botStudioNavLinks.appendChild(logoutLink);
}

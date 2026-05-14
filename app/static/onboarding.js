(function () {
  const TEXT = {
    defaults: {
      connectionName: "Platform WhatsApp",
      unknown: "Неизвестно",
      notConnected: "Не подключен",
      noData: "Пока нет данных",
      dash: "-",
      contactName: "Имя контакта: -",
      ready: "Экран подключения готов.",
      qrPlaceholder: "QR-код появится здесь.",
    },
    badges: {
      connected: "Подключен",
      disconnected: "Нужно подключить",
      notConfigured: "Не настроено",
      error: "Ошибка",
      loading: "Проверяем...",
    },
    polling: {
      ready: "Runtime активен",
      pending: "Запускаем runtime",
    },
    qrTypes: {
      qrCode: "QR готов",
      error: "Ошибка",
      alreadyLogged: "Уже подключен",
      unavailable: "QR недоступен",
    },
    states: {
      authorized: "Авторизован",
      notAuthorized: "Не авторизован",
      blocked: "Заблокирован",
      sleepMode: "Спящий режим",
      starting: "Запускается",
      qr: "QR готов",
      yellowCard: "Ограничен",
      online: "Онлайн",
      offline: "Офлайн",
      connecting: "Подключается",
      connected: "Подключено",
      disconnected: "Отключено",
    },
    business: {
      yes: "Да",
      no: "Нет",
    },
    messages: {
      waiting: "Подготавливаем локальную сессию и QR-код.",
      alreadyConnected: "WhatsApp уже подключен. Сканировать QR не нужно.",
      resetDone: "Сессия сброшена. Ожидаем новый QR-код.",
      updated: "Статус страницы обновлен.",
    },
  };

  const state = {
    pollTimer: null,
    lastConnectionStatus: "disconnected",
  };
  const STORAGE_KEY = "minigreenapi.simple-connect.snapshot";

  const dom = {
    connectionName: document.getElementById("connection-name"),
    connectionBadge: document.getElementById("connection-badge"),
    instanceState: document.getElementById("instance-state"),
    instanceStatus: document.getElementById("instance-status"),
    qrImage: document.getElementById("qr-image"),
    qrPlaceholder: document.getElementById("qr-placeholder"),
    pollingPill: document.getElementById("polling-pill"),
    qrPill: document.getElementById("qr-pill"),
    qrMessage: document.getElementById("qr-message"),
    avatarImage: document.getElementById("avatar-image"),
    avatarPlaceholder: document.getElementById("avatar-placeholder"),
    profileNameValue: document.getElementById("profile-name-value"),
    contactNameValue: document.getElementById("contact-name-value"),
    phoneValue: document.getElementById("phone-value"),
    chatIdValue: document.getElementById("chat-id-value"),
    deviceIdValue: document.getElementById("device-id-value"),
    businessValue: document.getElementById("business-value"),
    categoryValue: document.getElementById("category-value"),
    emailValue: document.getElementById("email-value"),
    descriptionValue: document.getElementById("description-value"),
    resultConsole: document.getElementById("result-console"),
    refreshButton: document.getElementById("refresh-btn"),
    resetButton: document.getElementById("reset-btn"),
  };

  function setConsole(message, payload) {
    if (payload === undefined) {
      dom.resultConsole.textContent = String(message);
      return;
    }

    dom.resultConsole.textContent = `${message}\n\n${JSON.stringify(payload, null, 2)}`;
  }

  function hasAccountIdentity(connection) {
    return Boolean(connection.profile_name || connection.contact_name || connection.phone || connection.chat_id);
  }

  function saveConnectionSnapshot(connection) {
    if (!window.localStorage) {
      return;
    }

    if (!hasAccountIdentity(connection) && connection.connection_status !== "connected") {
      window.localStorage.removeItem(STORAGE_KEY);
      return;
    }

    const snapshot = {
      ...connection,
      qr_code_data_url: "",
      qr_message: "",
      last_error: "",
      logout_performed: false,
    };

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  }

  function clearConnectionSnapshot() {
    if (!window.localStorage) {
      return;
    }

    window.localStorage.removeItem(STORAGE_KEY);
  }

  function readConnectionSnapshot() {
    if (!window.localStorage) {
      return null;
    }

    const rawSnapshot = window.localStorage.getItem(STORAGE_KEY);
    if (!rawSnapshot) {
      return null;
    }

    try {
      return JSON.parse(rawSnapshot);
    } catch (_error) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
  }

  function formatMapped(rawValue, mapping, fallback) {
    if (!rawValue) {
      return fallback;
    }

    const clean = String(rawValue).trim();
    return mapping[clean] || clean;
  }

  function setBadge(status) {
    dom.connectionBadge.className = "status-badge";

    if (status === "connected") {
      dom.connectionBadge.textContent = TEXT.badges.connected;
      dom.connectionBadge.classList.add("status-badge-connected");
      return;
    }

    if (status === "disconnected") {
      dom.connectionBadge.textContent = TEXT.badges.disconnected;
      dom.connectionBadge.classList.add("status-badge-disconnected");
      return;
    }

    if (status === "not_configured") {
      dom.connectionBadge.textContent = TEXT.badges.notConfigured;
      dom.connectionBadge.classList.add("status-badge-warning");
      return;
    }

    if (status === "error") {
      dom.connectionBadge.textContent = TEXT.badges.error;
      dom.connectionBadge.classList.add("status-badge-warning");
      return;
    }

    dom.connectionBadge.textContent = TEXT.badges.loading;
    dom.connectionBadge.classList.add("status-badge-pending");
  }

  function renderAvatar(connection) {
    const source = connection.base64_avatar || connection.avatar || "";
    if (source) {
      dom.avatarImage.src = source;
      dom.avatarImage.hidden = false;
      dom.avatarPlaceholder.hidden = true;
      return;
    }

    dom.avatarImage.hidden = true;
    dom.avatarImage.removeAttribute("src");
    dom.avatarPlaceholder.hidden = false;
  }

  function renderConnection(connection) {
    state.lastConnectionStatus = connection.connection_status || "disconnected";

    dom.connectionName.textContent = connection.connection_name || TEXT.defaults.connectionName;
    setBadge(connection.connection_status);
    dom.instanceState.textContent = formatMapped(connection.state_instance, TEXT.states, TEXT.defaults.unknown);
    dom.instanceStatus.textContent = formatMapped(connection.status_instance, TEXT.states, TEXT.defaults.unknown);

    dom.profileNameValue.textContent =
      connection.profile_name || connection.phone || connection.chat_id || TEXT.defaults.noData;
    dom.contactNameValue.textContent = connection.contact_name
      ? `Имя контакта: ${connection.contact_name}`
      : TEXT.defaults.contactName;
    dom.phoneValue.textContent = connection.phone || TEXT.defaults.notConnected;
    dom.chatIdValue.textContent = connection.chat_id || TEXT.defaults.dash;
    dom.deviceIdValue.textContent = connection.device_id || TEXT.defaults.dash;
    dom.businessValue.textContent = connection.is_business ? TEXT.business.yes : TEXT.business.no;
    dom.categoryValue.textContent = connection.category || TEXT.defaults.dash;
    dom.emailValue.textContent = connection.email || TEXT.defaults.dash;
    dom.descriptionValue.textContent = connection.description || TEXT.defaults.dash;

    dom.pollingPill.textContent = connection.polling_ready ? TEXT.polling.ready : TEXT.polling.pending;
    dom.pollingPill.classList.toggle("pill-active", Boolean(connection.polling_ready));

    dom.qrPill.textContent = formatMapped(connection.qr_type, TEXT.qrTypes, TEXT.qrTypes.unavailable);
    dom.qrPill.classList.toggle("pill-active", connection.qr_type === "qrCode");
    dom.qrPill.classList.toggle("pill-success", connection.connection_status === "connected");

    const messageParts = [];
    if (connection.qr_message) {
      messageParts.push(connection.qr_message);
    }
    if (connection.last_error) {
      messageParts.push(connection.last_error);
    }
    if (connection.logout_performed) {
      messageParts.push(TEXT.messages.resetDone);
    }
    if (!messageParts.length && connection.connection_status === "connected") {
      messageParts.push(TEXT.messages.alreadyConnected);
    }
    dom.qrMessage.textContent = messageParts.join(" ") || TEXT.messages.waiting;

    if (connection.qr_code_data_url && connection.connection_status !== "connected") {
      dom.qrImage.src = connection.qr_code_data_url;
      dom.qrImage.hidden = false;
      dom.qrPlaceholder.hidden = true;
    } else {
      dom.qrImage.hidden = true;
      dom.qrImage.removeAttribute("src");
      dom.qrPlaceholder.hidden = false;

      if (connection.connection_status === "connected") {
        dom.qrPlaceholder.textContent = TEXT.messages.alreadyConnected;
      } else if (connection.qr_message) {
        dom.qrPlaceholder.textContent = connection.qr_message;
      } else {
        dom.qrPlaceholder.textContent = TEXT.defaults.qrPlaceholder;
      }
    }

    renderAvatar(connection);
    saveConnectionSnapshot(connection);
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();
    let payload = null;

    try {
      payload = text ? JSON.parse(text) : null;
    } catch (_error) {
      payload = text;
    }

    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : text || `HTTP ${response.status}`;
      throw new Error(detail);
    }

    return payload;
  }

  async function refreshStatus(forceQr) {
    const includeQr = forceQr || state.lastConnectionStatus !== "connected";
    const payload = await requestJson(`/api/v1/connect/whatsapp/status?include_qr=${includeQr ? "true" : "false"}`, {
      headers: {
        Accept: "application/json",
      },
    });
    renderConnection(payload);
    setConsole(TEXT.messages.updated, payload);
  }

  async function resetConnection() {
    const payload = await requestJson("/api/v1/connect/whatsapp/reset", {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    });
    clearConnectionSnapshot();
    renderConnection(payload);
    setConsole(TEXT.messages.resetDone, payload);
  }

  function startPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
    }

    state.pollTimer = window.setInterval(() => {
      refreshStatus(false).catch((error) => {
        setConsole(error.message);
      });
    }, 10000);
  }

  dom.refreshButton.addEventListener("click", () => {
    refreshStatus(true).catch((error) => {
      setConsole(error.message);
    });
  });

  dom.resetButton.addEventListener("click", () => {
    resetConnection().catch((error) => {
      setConsole(error.message);
    });
  });

  dom.resultConsole.textContent = TEXT.defaults.ready;
  const cachedConnection = readConnectionSnapshot();
  if (cachedConnection) {
    renderConnection(cachedConnection);
    setConsole("Загружен сохраненный снимок платформенного аккаунта.");
  } else {
    setBadge("loading");
  }
  refreshStatus(true).catch((error) => {
    setConsole(error.message);
    setBadge("error");
  });
  startPolling();

  window.addEventListener("beforeunload", () => {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  });
})();

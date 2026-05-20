(function () {
  const POLL_INTERVAL_MS = 2200;

  const state = {
    activeChatId: "",
    activeChannelKey: "platform-main",
    conversations: [],
    pollHandle: null,
  };

  const dom = {
    conversationList: document.getElementById("conversation-list"),
    activeChatName: document.getElementById("active-chat-name"),
    activeChatAvatar: document.getElementById("active-chat-avatar"),
    activeChatMeta: document.getElementById("active-chat-meta"),
    chatChannelPill: document.getElementById("chat-channel-pill"),
    chatStatusPill: document.getElementById("chat-status-pill"),
    conversationCountPill: document.getElementById("conversation-count-pill"),
    messageStream: document.getElementById("message-stream"),
    manualReplyInput: document.getElementById("manual-reply-input"),
    sendReplyButton: document.getElementById("send-reply-btn"),
    messageRefreshButton: document.getElementById("message-refresh-btn"),
    conversationRefreshButton: document.getElementById("conversation-refresh-btn"),
    console: document.getElementById("chat-console"),
  };

  function setConsole(message, payload) {
    if (payload === undefined) {
      dom.console.textContent = String(message);
      return;
    }
    dom.console.textContent = `${message}\n\n${JSON.stringify(payload, null, 2)}`;
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

  function formatRelativeMeta(conversation) {
    const pieces = [];
    if (conversation.phone) {
      pieces.push(conversation.phone);
    }
    if (conversation.chat_id) {
      pieces.push(conversation.chat_id);
    }
    return pieces.join(" • ") || "Нет выбранного чата";
  }

  function getPresenceStatus(conversation) {
    const status = String(conversation?.presence_status || "offline").toLowerCase();
    return ["typing", "online"].includes(status) ? status : "offline";
  }

  function getPresenceLabel(conversation) {
    const status = getPresenceStatus(conversation);
    if (status === "typing") {
      return "печатает...";
    }
    if (status === "online") {
      return "online";
    }
    return "";
  }

  function setPresenceMeta(conversation) {
    const status = getPresenceStatus(conversation);
    const label = getPresenceLabel(conversation);
    dom.activeChatMeta.classList.toggle("is-typing", status === "typing");
    dom.activeChatMeta.classList.toggle("is-online", status === "online");

    if (label) {
      dom.activeChatMeta.textContent = label;
      return;
    }

    dom.activeChatMeta.textContent = `◉ WhatsApp • ${formatRelativeMeta(conversation)}`;
  }

  function isPersonalChat(conversation) {
    const chatId = String(conversation?.chat_id || "").toLowerCase();
    const [localPart, domain] = chatId.split("@", 2);
    return (
      chatId &&
      /^\d+$/.test(localPart || "") &&
      ["c.us", "lid", "s.whatsapp.net"].includes(domain || "")
    );
  }

  function getConversationName(conversation) {
    return conversation?.display_name || conversation?.phone || conversation?.chat_id || "Без имени";
  }

  function getInitials(value) {
    const words = String(value || "")
      .replace(/[^\p{L}\p{N}\s+]/gu, " ")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!words.length) {
      return "?";
    }
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
  }

  function avatarMarkup(conversation, extraClass = "") {
    const name = getConversationName(conversation);
    const avatarUrl = String(conversation?.avatar_url || "").trim();
    const className = `inbox-avatar ${extraClass}`.trim();
    if (avatarUrl) {
      return `<div class="${className}"><img src="${escapeHtml(avatarUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" /></div>`;
    }
    return `<div class="${className}"><span>${escapeHtml(getInitials(name))}</span></div>`;
  }

  function setActiveAvatar(conversation) {
    if (!dom.activeChatAvatar) {
      return;
    }
    dom.activeChatAvatar.outerHTML = avatarMarkup(conversation, "inbox-avatar-large");
    dom.activeChatAvatar = document.getElementById("active-chat-avatar") || document.querySelector(".inbox-avatar-large");
    if (dom.activeChatAvatar) {
      dom.activeChatAvatar.id = "active-chat-avatar";
    }
  }

  function formatTime(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderConversations(conversations) {
    const personalConversations = conversations.filter(isPersonalChat);
    state.conversations = personalConversations;
    if (dom.conversationCountPill) {
      dom.conversationCountPill.textContent = String(personalConversations.length);
    }

    if (!personalConversations.length) {
      dom.conversationList.innerHTML =
        '<div class="empty-state-card">Пока нет личных сообщений. Группы здесь не показываются.</div>';
      dom.chatStatusPill.textContent = "Нет диалогов";
      return;
    }

    dom.conversationList.innerHTML = personalConversations
      .map((conversation) => {
        const active = conversation.chat_id === state.activeChatId ? " is-active" : "";
        const presenceStatus = getPresenceStatus(conversation);
        const presenceLabel = getPresenceLabel(conversation);
        const presenceMarkup =
          presenceStatus !== "offline"
            ? `<span class="conversation-presence is-${presenceStatus}">${escapeHtml(presenceLabel)}</span>`
            : "";
        const unread =
          conversation.unread_count > 0
            ? `<span class="conversation-unread">${conversation.unread_count}</span>`
            : "";
        const preview = conversation.last_message_text || "Сообщений пока нет";
        const name = getConversationName(conversation);
        const directionIcon = conversation.last_direction === "outbound" ? "↩" : "↪";
        return `
          <button class="conversation-card${active}" type="button" data-chat-id="${conversation.chat_id}">
            ${avatarMarkup(conversation)}
            <div class="conversation-card-body">
              <div class="conversation-card-top">
                <strong>${escapeHtml(name)}</strong>
                <span>${escapeHtml(formatTime(conversation.last_message_at))}</span>
              </div>
              <div class="conversation-card-meta">
                <span>◉ WhatsApp</span>
                ${presenceMarkup}
                <span>${escapeHtml(conversation.phone || conversation.chat_id)}</span>
                ${unread}
              </div>
              <p><span>${directionIcon}</span>${escapeHtml(preview)}</p>
            </div>
          </button>
        `;
      })
      .join("");

    dom.conversationList.querySelectorAll("[data-chat-id]").forEach((element) => {
      element.addEventListener("click", () => {
        const chatId = element.getAttribute("data-chat-id") || "";
        openConversation(chatId).catch((error) => setConsole(error.message));
      });
    });
  }

  function renderMessages(messages) {
    if (!messages.length) {
      dom.messageStream.innerHTML =
        '<div class="empty-state-card">В этом диалоге еще нет сообщений.</div>';
      return;
    }

    dom.messageStream.innerHTML = messages
      .map((message) => {
        const sideClass = message.direction === "outbound" ? "message-card-outbound" : "message-card-inbound";
        const senderName = message.sender_name || (message.direction === "outbound" ? "Платформа" : "Клиент");
        const activeConversation =
          state.conversations.find((conversation) => conversation.chat_id === state.activeChatId) || null;
        const avatar =
          message.direction === "inbound"
            ? avatarMarkup(activeConversation, "inbox-message-avatar")
            : '<div class="inbox-avatar inbox-message-avatar"><span>AI</span></div>';
        return `
          <article class="message-row ${sideClass}">
            ${avatar}
            <div class="message-card">
              <p>${escapeHtml(message.text || "[пустое сообщение]")}</p>
              <div class="message-card-foot">
                <strong>${escapeHtml(senderName)}</strong>
                <span>${escapeHtml(formatTime(message.created_at))}</span>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    dom.messageStream.scrollTop = dom.messageStream.scrollHeight;
  }

  function updateActiveChatHeader(conversation) {
    if (!conversation) {
      dom.activeChatName.textContent = "Выберите диалог";
      setActiveAvatar(null);
      dom.activeChatMeta.classList.remove("is-typing", "is-online");
      dom.activeChatMeta.textContent =
        "Откройте чат слева, чтобы видеть историю сообщений и отвечать вручную.";
      dom.chatStatusPill.textContent = "Нет активного чата";
      return;
    }

    setActiveAvatar(conversation);
    dom.activeChatName.textContent = getConversationName(conversation);
    setPresenceMeta(conversation);
    const presenceStatus = getPresenceStatus(conversation);
    if (presenceStatus === "typing") {
      dom.chatStatusPill.textContent = "Бот печатает...";
      return;
    }
    if (presenceStatus === "online") {
      dom.chatStatusPill.textContent = "online";
      return;
    }
    dom.chatStatusPill.textContent =
      conversation.unread_count > 0 ? `Новых сообщений: ${conversation.unread_count}` : "Чат синхронизирован";
  }

  async function loadConversations(preferKeepSelection) {
    const conversations = await requestJson("/api/v1/platform/chats/conversations", {
      headers: { Accept: "application/json" },
    });

    renderConversations(conversations);

    if (!conversations.length) {
      state.activeChatId = "";
      updateActiveChatHeader(null);
      return;
    }

    const stillExists = conversations.some((conversation) => conversation.chat_id === state.activeChatId);
    if (preferKeepSelection && state.activeChatId && stillExists) {
      const activeConversation = conversations.find((conversation) => conversation.chat_id === state.activeChatId);
      updateActiveChatHeader(activeConversation || null);
      return;
    }

    if (!state.activeChatId || !stillExists) {
      await openConversation(conversations[0].chat_id);
      return;
    }

    const activeConversation = conversations.find((conversation) => conversation.chat_id === state.activeChatId);
    updateActiveChatHeader(activeConversation || null);
  }

  async function loadMessages(chatId) {
    const messages = await requestJson(`/api/v1/platform/chats/${encodeURIComponent(chatId)}/messages`, {
      headers: { Accept: "application/json" },
    });
    renderMessages(messages);
  }

  async function openConversation(chatId) {
    state.activeChatId = chatId;
    await requestJson(`/api/v1/platform/chats/${encodeURIComponent(chatId)}/read`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    await loadConversations(true);
    const activeConversation = state.conversations.find((conversation) => conversation.chat_id === chatId) || null;
    updateActiveChatHeader(activeConversation);
    await loadMessages(chatId);
    setConsole("Диалог открыт.");
  }

  async function sendReply() {
    if (!state.activeChatId) {
      throw new Error("Сначала выберите диалог слева.");
    }

    const text = dom.manualReplyInput.value.trim();
    if (!text) {
      throw new Error("Введите текст сообщения.");
    }

    dom.sendReplyButton.disabled = true;
    try {
      const payload = await requestJson(`/api/v1/platform/chats/${encodeURIComponent(state.activeChatId)}/send`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text }),
      });
      dom.manualReplyInput.value = "";
      await loadConversations(true);
      await loadMessages(state.activeChatId);
      setConsole("Сообщение отправлено из платформы.", payload);
    } finally {
      dom.sendReplyButton.disabled = false;
    }
  }

  function startPolling() {
    if (state.pollHandle) {
      window.clearInterval(state.pollHandle);
    }

    state.pollHandle = window.setInterval(() => {
      loadConversations(true)
        .then(() => {
          if (state.activeChatId) {
            return loadMessages(state.activeChatId);
          }
          return null;
        })
        .catch((error) => setConsole(error.message));
    }, POLL_INTERVAL_MS);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  dom.sendReplyButton.addEventListener("click", () => {
    sendReply().catch((error) => setConsole(error.message));
  });

  dom.messageRefreshButton.addEventListener("click", () => {
    if (!state.activeChatId) {
      setConsole("Сначала выберите диалог слева.");
      return;
    }
    loadMessages(state.activeChatId).catch((error) => setConsole(error.message));
  });

  dom.conversationRefreshButton.addEventListener("click", () => {
    loadConversations(true).catch((error) => setConsole(error.message));
  });

  dom.manualReplyInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      sendReply().catch((error) => setConsole(error.message));
    }
  });

  setConsole("Загружаем чаты платформы...");
  loadConversations(false)
    .then(() => {
      if (state.activeChatId) {
        return loadMessages(state.activeChatId);
      }
      return null;
    })
    .then(() => {
      setConsole("Чат-монитор синхронизирован.");
    })
    .catch((error) => setConsole(error.message));

  startPolling();

  window.addEventListener("beforeunload", () => {
    if (state.pollHandle) {
      window.clearInterval(state.pollHandle);
      state.pollHandle = null;
    }
  });
})();

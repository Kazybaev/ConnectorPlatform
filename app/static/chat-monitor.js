(function () {
  const POLL_INTERVAL_MS = 5000;

  const state = {
    activeChatId: "",
    activeChannelKey: "platform-main",
    conversations: [],
    pollHandle: null,
  };

  const dom = {
    conversationList: document.getElementById("conversation-list"),
    activeChatName: document.getElementById("active-chat-name"),
    activeChatMeta: document.getElementById("active-chat-meta"),
    chatChannelPill: document.getElementById("chat-channel-pill"),
    chatStatusPill: document.getElementById("chat-status-pill"),
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

  function renderConversations(conversations) {
    state.conversations = conversations;
    if (!conversations.length) {
      dom.conversationList.innerHTML =
        '<div class="empty-state-card">Пока нет сообщений. Как только в WhatsApp придет новый чат, он появится здесь.</div>';
      dom.chatStatusPill.textContent = "Нет диалогов";
      return;
    }

    dom.conversationList.innerHTML = conversations
      .map((conversation) => {
        const active = conversation.chat_id === state.activeChatId ? " is-active" : "";
        const unread =
          conversation.unread_count > 0
            ? `<span class="conversation-unread">${conversation.unread_count}</span>`
            : "";
        const preview = conversation.last_message_text || "Сообщений пока нет";
        return `
          <button class="conversation-card${active}" type="button" data-chat-id="${conversation.chat_id}">
            <div class="conversation-card-top">
              <strong>${escapeHtml(conversation.display_name || conversation.phone || conversation.chat_id)}</strong>
              ${unread}
            </div>
            <div class="conversation-card-meta">${escapeHtml(formatRelativeMeta(conversation))}</div>
            <p>${escapeHtml(preview)}</p>
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
        return `
          <article class="message-card ${sideClass}">
            <div class="message-card-head">
              <strong>${escapeHtml(senderName)}</strong>
              <span>${escapeHtml(message.created_at)}</span>
            </div>
            <p>${escapeHtml(message.text || "[пустое сообщение]")}</p>
          </article>
        `;
      })
      .join("");

    dom.messageStream.scrollTop = dom.messageStream.scrollHeight;
  }

  function updateActiveChatHeader(conversation) {
    if (!conversation) {
      dom.activeChatName.textContent = "Выберите диалог";
      dom.activeChatMeta.textContent =
        "Откройте чат слева, чтобы видеть историю сообщений и отвечать вручную.";
      dom.chatStatusPill.textContent = "Нет активного чата";
      return;
    }

    dom.activeChatName.textContent = conversation.display_name || conversation.phone || conversation.chat_id;
    dom.activeChatMeta.textContent = formatRelativeMeta(conversation);
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

const express = require("express");
const fs = require("fs");
const path = require("path");
const QRCode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

function loadLocalEnvFile() {
  const envPath = path.resolve(__dirname, "..", ".env");
  if (!fs.existsSync(envPath)) {
    return;
  }

  const raw = fs.readFileSync(envPath, "utf8");
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    const key = trimmed.slice(0, separatorIndex).trim();
    const value = trimmed.slice(separatorIndex + 1).trim().replace(/^['"]|['"]$/g, "");
    if (key && !(key in process.env)) {
      process.env[key] = value;
    }
  }
}

loadLocalEnvFile();

const app = express();
app.use(express.json({ limit: "30mb" }));

const platformPublicBaseUrl = String(
  process.env.PLATFORM_PUBLIC_BASE_URL || "http://127.0.0.1:8000"
).trim().replace(/\/+$/, "");
const runtimePort = Number(process.env.RUNTIME_PORT || process.env.RUNTIME_SERVICE_PORT || "8011");
const runtimeToken = String(process.env.RUNTIME_TOKEN || process.env.RUNTIME_SERVICE_TOKEN || "").trim();
const runtimePlatformCallbackUrl = String(
  process.env.RUNTIME_PLATFORM_CALLBACK_URL || `${platformPublicBaseUrl}/api/v1/runtime/incoming`
).trim();
const runtimePlatformCallbackToken = String(
  process.env.RUNTIME_PLATFORM_CALLBACK_TOKEN || process.env.RUNTIME_CALLBACK_TOKEN || runtimeToken || ""
).trim();
const historySyncEnabled = String(process.env.RUNTIME_HISTORY_SYNC_ENABLED || "true").trim().toLowerCase()
  !== "false";
const configuredHistorySyncChatLimit = Number(process.env.RUNTIME_HISTORY_SYNC_CHAT_LIMIT || "200");
const historySyncChatLimit = Number.isFinite(configuredHistorySyncChatLimit)
  ? Math.max(0, configuredHistorySyncChatLimit)
  : 200;
const configuredHistorySyncMessageLimit = Number(process.env.RUNTIME_HISTORY_SYNC_MESSAGE_LIMIT || "100");
const historySyncMessageLimit = Number.isFinite(configuredHistorySyncMessageLimit)
  ? Math.max(0, configuredHistorySyncMessageLimit)
  : 100;
const sessionRoot = path.resolve(__dirname, "..", "data", "runtime", "sessions");
const reconnectBaseDelayMs = 5000;
const reconnectMaxDelayMs = 60000;
const configuredConnectStallMs = Number(process.env.RUNTIME_CONNECT_STALL_MS || "90000");
const connectStallMs = Number.isFinite(configuredConnectStallMs)
  ? Math.max(30000, configuredConnectStallMs)
  : 90000;
const platformCallbackRetryMs = 5000;
const platformCallbackQueueMaxSize = 500;
const configuredTypingRefreshMs = Number(process.env.RUNTIME_TYPING_REFRESH_MS || "3500");
const typingRefreshMs = Number.isFinite(configuredTypingRefreshMs)
  ? Math.max(1500, configuredTypingRefreshMs)
  : 3500;
const onlinePresenceEnabled = String(process.env.RUNTIME_ONLINE_PRESENCE_ENABLED || "true").trim().toLowerCase()
  !== "false";
const configuredPresenceRefreshMs = Number(process.env.RUNTIME_ONLINE_PRESENCE_REFRESH_MS || "15000");
const presenceRefreshMs = Number.isFinite(configuredPresenceRefreshMs)
  ? Math.max(5000, configuredPresenceRefreshMs)
  : 15000;
const maxForwardedMediaBytes = 20 * 1024 * 1024;

fs.mkdirSync(sessionRoot, { recursive: true });

const channelStates = new Map();

function nowIso() {
  return new Date().toISOString();
}

function sessionDirectory(channelKey) {
  return path.join(sessionRoot, `session-${channelKey}`);
}

function sanitizeError(error) {
  if (!error) {
    return "Unknown runtime error.";
  }

  if (typeof error === "string") {
    return error;
  }

  if (error instanceof Error) {
    return error.message || error.toString();
  }

  return String(error);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildEmptyProfile() {
  return {
    name: "",
    short_name: "",
    push_name: "",
    number: "",
    about: "",
    avatar_url: "",
    is_business: false,
    is_enterprise: false,
    is_my_contact: false
  };
}

function buildInitialState(channelKey, displayName) {
  return {
    channelId: channelKey,
    displayName,
    connectionStatus: "disconnected",
    qrAvailable: false,
    qrCodeDataUrl: "",
    connectingSinceMs: 0,
    phone: "",
    pushName: "",
    platform: "",
    wid: "",
    profile: buildEmptyProfile(),
    lastConnectionAt: "",
    lastMessageAt: "",
    lastError: "",
    botActivatedAt: "",
    botActivatedAtMs: 0,
    recentForwardedMessageIds: new Map(),
    pendingForwardedMessageIds: new Set(),
    recentPlatformOutboundMessageIds: new Map(),
    pendingPlatformCallbacks: [],
    activeTypingSessions: new Map(),
    presenceKeepaliveTimer: null,
    lastPresenceAvailableAt: "",
    reconnectTimer: null,
    reconnectAttempts: 0,
    isResetting: false,
    stallRestarting: false,
    historySyncStarted: false,
    client: null,
    initPromise: null
  };
}

function getState(channelKey, displayName = "Platform WhatsApp") {
  const existing = channelStates.get(channelKey);
  if (existing) {
    if (displayName) {
      existing.displayName = displayName;
    }
    return existing;
  }

  const state = buildInitialState(channelKey, displayName);
  channelStates.set(channelKey, state);
  return state;
}

function setConnectionStatus(state, status) {
  state.connectionStatus = status;
  state.connectingSinceMs = status === "connecting" ? Date.now() : 0;
}

function serializeState(state) {
  return {
    channel_id: state.channelId,
    display_name: state.displayName,
    connection_status: state.connectionStatus,
    qr_available: state.qrAvailable,
    qr_code_data_url: state.qrCodeDataUrl,
    phone: state.phone,
    push_name: state.pushName,
    platform: state.platform,
    wid: state.wid,
    profile: state.profile,
    last_connection_at: state.lastConnectionAt,
    last_message_at: state.lastMessageAt,
    bot_activated_at: state.botActivatedAt,
    last_presence_available_at: state.lastPresenceAvailableAt,
    active_typing_chats: [...state.activeTypingSessions.keys()],
    last_error: state.lastError
  };
}

function isConnectingStalled(state) {
  return Boolean(
    state.connectionStatus === "connecting" &&
    state.connectingSinceMs &&
    Date.now() - state.connectingSinceMs > connectStallMs
  );
}

function normalizeIncomingText(message) {
  const body = String(message.body || "").trim();
  if (body) {
    return body;
  }

  const type = String(message.type || "").trim().toLowerCase();
  if (!type || type === "text") {
    return "";
  }

  return `[${type}]`;
}

function mediaByteLength(base64Value) {
  const value = String(base64Value || "");
  if (!value) {
    return 0;
  }
  return Math.floor((value.length * 3) / 4);
}

async function resolveIncomingMedia(message) {
  if (!message?.hasMedia || typeof message.downloadMedia !== "function") {
    return null;
  }

  const media = await message.downloadMedia().catch(() => null);
  if (!media || !media.data || !media.mimetype) {
    return null;
  }

  const sizeBytes = mediaByteLength(media.data);
  if (sizeBytes > maxForwardedMediaBytes) {
    return {
      skipped: true,
      reason: "media_too_large",
      mimetype: String(media.mimetype || "").trim(),
      size_bytes: sizeBytes
    };
  }

  return {
    mimetype: String(media.mimetype || "").trim(),
    data: String(media.data || ""),
    filename: String(media.filename || message?._data?.filename || "").trim(),
    caption: String(message.caption || message.body || "").trim(),
    size_bytes: sizeBytes
  };
}

function pruneRecentMessageMaps(state) {
  const cutoff = Date.now() - 15 * 60 * 1000;
  for (const [messageId, createdAt] of state.recentForwardedMessageIds.entries()) {
    if (createdAt < cutoff) {
      state.recentForwardedMessageIds.delete(messageId);
    }
  }
  for (const [messageId, createdAt] of state.recentPlatformOutboundMessageIds.entries()) {
    if (createdAt < cutoff) {
      state.recentPlatformOutboundMessageIds.delete(messageId);
    }
  }
}

function clearReconnectTimer(state) {
  if (!state.reconnectTimer) {
    return;
  }

  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;
}

function scheduleReconnect(state, reason) {
  if (state.isResetting || state.connectionStatus === "connected" || state.reconnectTimer) {
    return;
  }

  const delay = Math.min(
    reconnectMaxDelayMs,
    reconnectBaseDelayMs * Math.max(1, 2 ** state.reconnectAttempts)
  );
  state.reconnectAttempts += 1;

  console.warn(
    `[runtime:${state.channelId}] reconnect scheduled`,
    JSON.stringify({ reason: String(reason || "disconnected"), delayMs: delay })
  );

  state.reconnectTimer = setTimeout(async () => {
    state.reconnectTimer = null;
    if (state.isResetting || state.connectionStatus === "connected") {
      return;
    }

    try {
      state.client = null;
      state.initPromise = null;
      await ensureClient(state);
      await waitForRenderableState(state, 30000);
    } catch (error) {
      state.client = null;
      state.initPromise = null;
      state.lastError = sanitizeError(error);
      scheduleReconnect(state, "reconnect_failed");
    }
  }, delay);
}

function messageTimestampMs(message) {
  const rawTimestamp = message?.timestamp;
  if (rawTimestamp === null || rawTimestamp === undefined || rawTimestamp === "") {
    return 0;
  }

  const parsed = Number(rawTimestamp);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 0;
  }

  return parsed > 10000000000 ? Math.floor(parsed) : Math.floor(parsed * 1000);
}

function resolveBotEligibility(state, message, eventName = "message") {
  const sentAtMs = messageTimestampMs(message);
  if (!state.botActivatedAtMs) {
    return {
      botEligible: false,
      botSkipReason: "runtime_not_ready",
      messageTimestampMs: sentAtMs
    };
  }
  if (!sentAtMs) {
    return {
      botEligible: eventName !== "history_sync",
      botSkipReason: eventName === "history_sync" ? "missing_message_timestamp" : "",
      messageTimestampMs: sentAtMs
    };
  }
  if (sentAtMs < state.botActivatedAtMs) {
    return {
      botEligible: false,
      botSkipReason: "before_runtime_activation",
      messageTimestampMs: sentAtMs
    };
  }
  return {
    botEligible: true,
    botSkipReason: "",
    messageTimestampMs: sentAtMs
  };
}

function rememberPlatformOutboundMessage(state, messageId) {
  if (!messageId) {
    return;
  }
  pruneRecentMessageMaps(state);
  state.recentPlatformOutboundMessageIds.set(messageId, Date.now());
}

function shouldIgnoreRuntimeMessage(state, messageId) {
  if (!messageId) {
    return false;
  }

  pruneRecentMessageMaps(state);
  if (state.recentPlatformOutboundMessageIds.has(messageId)) {
    return true;
  }
  if (state.pendingForwardedMessageIds.has(messageId)) {
    return true;
  }
  if (state.recentForwardedMessageIds.has(messageId)) {
    return true;
  }
  return false;
}

function typingSessionKey(chatId) {
  return String(chatId || "").trim();
}

function clearPresenceKeepaliveTimer(state) {
  if (!state.presenceKeepaliveTimer) {
    return;
  }

  clearInterval(state.presenceKeepaliveTimer);
  state.presenceKeepaliveTimer = null;
}

async function sendOnlinePresence(state) {
  if (!onlinePresenceEnabled || !state.client || state.connectionStatus !== "connected") {
    return;
  }

  if (typeof state.client.sendPresenceAvailable !== "function") {
    return;
  }

  await state.client.sendPresenceAvailable();
  state.lastPresenceAvailableAt = nowIso();
}

function startPresenceKeepalive(state) {
  if (!onlinePresenceEnabled || state.presenceKeepaliveTimer) {
    return;
  }

  state.presenceKeepaliveTimer = setInterval(() => {
    sendOnlinePresence(state).catch((error) => {
      state.lastError = `Online presence failed: ${sanitizeError(error)}`;
      console.error(`[runtime:${state.channelId}] online presence failed`, state.lastError);
    });
  }, presenceRefreshMs);

  if (typeof state.presenceKeepaliveTimer.unref === "function") {
    state.presenceKeepaliveTimer.unref();
  }
}

async function ensureOnlinePresence(state) {
  if (!onlinePresenceEnabled) {
    return;
  }

  startPresenceKeepalive(state);
  try {
    await sendOnlinePresence(state);
  } catch (error) {
    state.lastError = `Online presence failed: ${sanitizeError(error)}`;
    console.error(`[runtime:${state.channelId}] online presence failed`, state.lastError);
  }
}

function isPersonalChatId(chatId) {
  const normalized = String(chatId || "").trim().toLowerCase();
  const [localPart, domain] = normalized.split("@", 2);
  return Boolean(
    normalized &&
    /^\d+$/.test(localPart || "") &&
    ["c.us", "lid", "s.whatsapp.net"].includes(domain || "")
  );
}

function derivePhoneFromChatId(chatId) {
  const value = String(chatId || "").trim();
  if (!value.includes("@")) {
    return value;
  }
  return value.split("@", 1)[0].trim();
}

function sameChatId(left, right) {
  return String(left || "").trim().toLowerCase() === String(right || "").trim().toLowerCase();
}

async function resolveContactProfile(state, chatId) {
  const normalizedChatId = String(chatId || "").trim();
  const profile = {
    chat_id: normalizedChatId,
    display_name: "",
    phone: derivePhoneFromChatId(normalizedChatId),
    avatar_url: "",
    skipped: false
  };

  if (!isPersonalChatId(normalizedChatId)) {
    profile.skipped = true;
    return profile;
  }

  let chatName = "";
  let contact = null;
  const chat = await state.client.getChatById(normalizedChatId).catch(() => null);
  if (chat) {
    chatName = String(chat.name || "").trim();
    contact = chat.contact || null;
  }

  if (!contact) {
    contact = await state.client.getContactById(normalizedChatId).catch(() => null);
  }

  const ownWid = String(state.client?.info?.wid?._serialized || state.wid || "").trim();
  const contactId = String(contact?.id?._serialized || "").trim();
  const contactBelongsToChat = Boolean(contactId && sameChatId(contactId, normalizedChatId));
  const contactIsOwnAccount = Boolean(ownWid && contactId && sameChatId(contactId, ownWid));
  if (contactIsOwnAccount && !sameChatId(normalizedChatId, ownWid)) {
    contact = null;
  }

  if (!contactBelongsToChat && contactId && !contactIsOwnAccount) {
    contact = null;
  }

  if (!contact && !chatName) {
    const fallbackContact = await state.client.getContactById(normalizedChatId).catch(() => null);
    const fallbackId = String(fallbackContact?.id?._serialized || "").trim();
    if (fallbackId && sameChatId(fallbackId, normalizedChatId)) {
      contact = fallbackContact;
    }
  }

  profile.display_name = String(
    chatName ||
    contact?.pushname ||
    contact?.name ||
    contact?.shortName ||
    contact?.number ||
    ""
  ).trim();
  profile.phone = String(contact?.number || profile.phone || "").trim();

  if (state.client) {
    const avatarUrl = await state.client.getProfilePicUrl(normalizedChatId).catch(() => "");
    profile.avatar_url = typeof avatarUrl === "string" ? avatarUrl : "";
  }

  return profile;
}

async function resolveChatContactProfile(state, chatId) {
  const normalizedChatId = String(chatId || "").trim();
  if (!isPersonalChatId(normalizedChatId)) {
    return { displayName: "", avatarUrl: "" };
  }

  const profile = await resolveContactProfile(state, normalizedChatId).catch(() => null);
  return {
    displayName: String(profile?.display_name || "").trim(),
    avatarUrl: String(profile?.avatar_url || "").trim()
  };
}

async function resolveMessageSenderName(message) {
  const contact = await message.getContact().catch(() => null);
  return String(
    contact?.pushname ||
    contact?.name ||
    contact?.shortName ||
    message?._data?.notifyName ||
    ""
  ).trim();
}

async function sendTypingPulse(state, chatId) {
  if (!state.client || state.connectionStatus !== "connected") {
    throw new Error(`Runtime channel is not connected (${state.connectionStatus}).`);
  }

  await ensureOnlinePresence(state);
  const chat = await state.client.getChatById(chatId);
  await chat.sendStateTyping();
}

async function startTypingState(state, chatId) {
  const key = typingSessionKey(chatId);
  if (!key) {
    throw new Error("chat_id is required.");
  }

  await ensureClient(state);
  await sendTypingPulse(state, key);

  const existing = state.activeTypingSessions.get(key);
  if (existing?.timer) {
    existing.lastPulseAt = nowIso();
    return;
  }

  const session = {
    timer: setInterval(() => {
      sendTypingPulse(state, key)
        .then(() => {
          const current = state.activeTypingSessions.get(key);
          if (current) {
            current.lastPulseAt = nowIso();
          }
        })
        .catch((error) => {
          state.lastError = `Typing state failed: ${sanitizeError(error)}`;
          console.error(`[runtime:${state.channelId}] typing pulse failed`, state.lastError);
          stopTypingState(state, key, { clearRemoteState: false }).catch(() => {});
        });
    }, typingRefreshMs),
    startedAt: nowIso(),
    lastPulseAt: nowIso(),
    mode: "typing"
  };

  if (typeof session.timer.unref === "function") {
    session.timer.unref();
  }

  state.activeTypingSessions.set(key, session);
}

async function stopTypingState(state, chatId, options = {}) {
  const key = typingSessionKey(chatId);
  if (!key) {
    return;
  }

  const existing = state.activeTypingSessions.get(key);
  if (existing?.timer) {
    clearInterval(existing.timer);
  }
  state.activeTypingSessions.delete(key);

  if (options.clearRemoteState === false || !state.client || state.connectionStatus !== "connected") {
    return;
  }

  try {
    const chat = await state.client.getChatById(key);
    await chat.clearState();
    await sendOnlinePresence(state);
  } catch (error) {
    state.lastError = `Typing clear failed: ${sanitizeError(error)}`;
  }
}

async function stopAllTypingStates(state, options = {}) {
  const chatIds = [...state.activeTypingSessions.keys()];
  for (const chatId of chatIds) {
    await stopTypingState(state, chatId, options);
  }
}

function platformCallbackKey(payload) {
  const message = payload?.message || {};
  const messageId = String(message.external_message_id || "").trim();
  if (messageId) {
    return `${payload.channel_key || ""}:${messageId}`;
  }
  return "";
}

function enqueuePlatformCallback(state, payload, reason) {
  const key = platformCallbackKey(payload);
  if (key && state.pendingPlatformCallbacks.some((item) => item.key === key)) {
    return;
  }

  if (state.pendingPlatformCallbacks.length >= platformCallbackQueueMaxSize) {
    state.pendingPlatformCallbacks.shift();
  }

  state.pendingPlatformCallbacks.push({
    key,
    payload,
    attempts: 0,
    queuedAt: nowIso(),
    lastError: String(reason || "").trim()
  });
}

async function sendPayloadToPlatform(state, payload, options = {}) {
  if (!runtimePlatformCallbackUrl) {
    return false;
  }

  const queueOnFailure = options.queueOnFailure !== false;
  const headers = {
    "Content-Type": "application/json"
  };
  if (runtimePlatformCallbackToken) {
    headers["X-Runtime-Callback-Token"] = runtimePlatformCallbackToken;
  }

  try {
    const response = await fetch(runtimePlatformCallbackUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      state.lastError = detail
        ? `Platform inbox callback failed: ${detail}`
        : `Platform inbox callback failed with status ${response.status}.`;
      console.error(`[runtime:${state.channelId}] platform callback failed`, state.lastError);
      if (queueOnFailure) {
        enqueuePlatformCallback(state, payload, state.lastError);
      }
      return false;
    }
    if (String(state.lastError || "").startsWith("Platform inbox callback failed:")) {
      state.lastError = "";
    }
    return true;
  } catch (error) {
    state.lastError = `Platform inbox callback failed: ${sanitizeError(error)}`;
    console.error(`[runtime:${state.channelId}] platform callback error`, state.lastError);
    if (queueOnFailure) {
      enqueuePlatformCallback(state, payload, state.lastError);
    }
    return false;
  }
}

async function flushPlatformCallbackQueue(state) {
  if (!state.pendingPlatformCallbacks.length) {
    return;
  }

  const remaining = [];
  for (const item of state.pendingPlatformCallbacks) {
    const ok = await sendPayloadToPlatform(state, item.payload, { queueOnFailure: false });
    if (!ok) {
      item.attempts += 1;
      item.lastError = state.lastError;
      remaining.push(item);
    }
  }
  state.pendingPlatformCallbacks = remaining;
}

async function inspectMessageChat(state, message) {
  let chat = null;
  try {
    chat = await message.getChat();
  } catch (_chatError) {
    // Fallback to raw message fields below.
  }

  const ownWid = String(state.client?.info?.wid?._serialized || state.wid || "").trim();
  const from = String(message.from || "").trim();
  const to = String(message.to || "").trim();

  let chatId = String(chat?.id?._serialized || "").trim();
  if (!chatId) {
    if (message.fromMe) {
      if (to) {
        chatId = to;
      } else if (from && from !== ownWid) {
        chatId = from;
      }
    }
  }

  if (!chatId) {
    chatId = from || to || ownWid;
  }

  const chatServer = String(chat?.id?.server || "").trim().toLowerCase();
  const normalizedChatId = chatId.toLowerCase();
  const isGroup = Boolean(chat?.isGroup) || normalizedChatId.endsWith("@g.us") || chatServer === "g.us";
  const isNewsletter = normalizedChatId.endsWith("@newsletter") || chatServer === "newsletter";
  const isBroadcast =
    normalizedChatId === "status@broadcast" ||
    normalizedChatId.endsWith("@broadcast") ||
    chatServer === "broadcast" ||
    isNewsletter;

  return {
    chatId,
    isGroup,
    isBroadcast,
    isNewsletter,
    chatServer
  };
}

async function resolveChatId(state, message) {
  const chatContext = await inspectMessageChat(state, message);
  return chatContext.chatId;
}

async function handleRuntimeMessageEvent(state, message, eventName) {
  state.lastMessageAt = nowIso();

  const messageId = String(message.id?._serialized || "").trim();
  if (shouldIgnoreRuntimeMessage(state, messageId)) {
    return;
  }

  if (messageId) {
    state.pendingForwardedMessageIds.add(messageId);
  }

  const ownWid = String(state.client?.info?.wid?._serialized || state.wid || "").trim();

  try {
    const chatContext = await inspectMessageChat(state, message);
    const chatId = chatContext.chatId;
    if (chatContext.isGroup || chatContext.isBroadcast) {
      console.log(
        `[runtime:${state.channelId}] skipped non-personal chat`,
        JSON.stringify({
          messageId,
          chatId,
          eventName,
          isGroup: chatContext.isGroup,
          isBroadcast: chatContext.isBroadcast,
          isNewsletter: chatContext.isNewsletter
        })
      );
      return;
    }

    const fromMe = Boolean(message.fromMe);
    const isSelfChat = Boolean(ownWid && chatId && chatId === ownWid);
    const allowBotReply = Boolean(fromMe && isSelfChat);
    const botEligibility =
      fromMe && !allowBotReply
        ? {
            botEligible: false,
            botSkipReason: "from_me",
            messageTimestampMs: messageTimestampMs(message)
          }
        : resolveBotEligibility(state, message, eventName);
    const posted = await postIncomingMessageToPlatform(state, message, {
      chatId,
      eventName,
      isSelfChat,
      allowBotReply,
      botEligible: botEligibility.botEligible,
      botSkipReason: botEligibility.botSkipReason,
      messageTimestampMs: botEligibility.messageTimestampMs,
      isGroup: chatContext.isGroup,
      isBroadcast: chatContext.isBroadcast,
      isNewsletter: chatContext.isNewsletter,
      chatType: chatContext.chatServer
    });
    if (posted && messageId) {
      state.recentForwardedMessageIds.set(messageId, Date.now());
    }
    if (posted) {
      console.log(
        `[runtime:${state.channelId}] forwarded ${eventName} message`,
        JSON.stringify({
          messageId,
          chatId,
          fromMe,
          isSelfChat,
          botEligible: botEligibility.botEligible,
          botSkipReason: botEligibility.botSkipReason,
          messageType: String(message.type || "text").trim() || "text"
        })
      );
    }
  } finally {
    if (messageId) {
      state.pendingForwardedMessageIds.delete(messageId);
    }
  }
}

async function syncRecentPersonalMessages(state) {
  if (!historySyncEnabled || state.historySyncStarted || !state.client) {
    return;
  }
  if (!historySyncChatLimit || !historySyncMessageLimit) {
    return;
  }

  state.historySyncStarted = true;
  let forwardedCount = 0;
  let scannedChatCount = 0;

  try {
    const chats = await state.client.getChats();
    const personalChats = chats
      .filter((chat) => isPersonalChatId(String(chat?.id?._serialized || "")))
      .sort((left, right) => Number(right?.timestamp || 0) - Number(left?.timestamp || 0))
      .slice(0, historySyncChatLimit);

    for (const chat of personalChats) {
      scannedChatCount += 1;
      const chatId = String(chat?.id?._serialized || "").trim();
      const messages = await chat.fetchMessages({ limit: historySyncMessageLimit }).catch((error) => {
        console.warn(
          `[runtime:${state.channelId}] history sync chat failed`,
          JSON.stringify({ chatId, error: sanitizeError(error) })
        );
        return [];
      });

      const sortedMessages = messages
        .slice()
        .sort((left, right) => messageTimestampMs(left) - messageTimestampMs(right));
      for (const message of sortedMessages) {
        await handleRuntimeMessageEvent(state, message, "history_sync");
        forwardedCount += 1;
      }
    }

    console.log(
      `[runtime:${state.channelId}] history sync complete`,
      JSON.stringify({
        scannedChats: scannedChatCount,
        scannedMessages: forwardedCount,
        chatLimit: historySyncChatLimit,
        messageLimit: historySyncMessageLimit
      })
    );
  } catch (error) {
    state.historySyncStarted = false;
    state.lastError = `WhatsApp history sync failed: ${sanitizeError(error)}`;
    console.error(`[runtime:${state.channelId}] history sync failed`, state.lastError);
  }
}

async function postIncomingMessageToPlatform(state, message, options = {}) {
  if (!runtimePlatformCallbackUrl) {
    return false;
  }

  const text = normalizeIncomingText(message);
  const chatId = String(options.chatId || "").trim() || await resolveChatId(state, message);
  const isSelfChat = Boolean(options.isSelfChat);
  const allowBotReply = Boolean(options.allowBotReply);
  const botEligible = options.botEligible !== false;
  const botSkipReason = String(options.botSkipReason || "").trim();
  const runtimeReceivedAt = nowIso();
  const chatContactProfile = await resolveChatContactProfile(state, chatId);
  const media = await resolveIncomingMedia(message);
  const senderName = Boolean(message.fromMe)
    ? "WhatsApp account"
    : (await resolveMessageSenderName(message) || chatContactProfile.displayName);
  const senderAvatarUrl = Boolean(message.fromMe) ? "" : chatContactProfile.avatarUrl;

  const payload = {
    channel_key: state.channelId,
    channel_name: state.displayName,
    message: {
      external_message_id: String(message.id?._serialized || "").trim(),
      chat_id: chatId,
      sender_id: String(message.author || message.from || message.to || "").trim(),
      sender_name: senderName,
      sender_avatar_url: typeof senderAvatarUrl === "string" ? senderAvatarUrl : "",
      text,
      message_type: String(message.type || "text").trim() || "text",
      has_media: Boolean(media && !media.skipped),
      media: media || null,
      timestamp: message.timestamp || null,
      timestamp_ms: Number(options.messageTimestampMs || 0),
      runtime_received_at: runtimeReceivedAt,
      runtime_activated_at: state.botActivatedAt,
      from_me: Boolean(message.fromMe),
      self_chat: isSelfChat,
      allow_bot_reply: allowBotReply,
      bot_eligible: botEligible,
      bot_skip_reason: botEligible ? "" : (botSkipReason || "not_bot_eligible"),
      is_group: Boolean(options.isGroup),
      is_broadcast: Boolean(options.isBroadcast),
      is_newsletter: Boolean(options.isNewsletter),
      chat_type: String(options.chatType || "").trim(),
      event_source: String(options.eventName || "message").trim() || "message"
    }
  };

  return sendPayloadToPlatform(state, payload);
}

async function waitForRenderableState(state, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (state.qrAvailable || state.connectionStatus === "connected" || state.lastError) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (state.connectionStatus === "connecting" && !state.lastError) {
    state.lastError = "WhatsApp Web JS is still connecting. The runtime will restart the local browser session if this state stalls.";
  }
}

function requireRuntimeToken(req, res, next) {
  if (!runtimeToken) {
    next();
    return;
  }

  const incomingToken = String(req.header("X-Runtime-Token") || "").trim();
  if (incomingToken !== runtimeToken) {
    res.status(401).json({ detail: "Invalid or missing X-Runtime-Token." });
    return;
  }

  next();
}

async function refreshProfile(state) {
  const client = state.client;
  if (!client || !client.info) {
    return;
  }

  const info = client.info;
  state.phone = String(info.wid?.user || state.phone || "").trim();
  state.pushName = String(info.pushname || state.pushName || "").trim();
  state.platform = String(info.platform || state.platform || "").trim();
  state.wid = String(info.wid?._serialized || state.wid || "").trim();

  const profile = { ...buildEmptyProfile() };
  profile.push_name = state.pushName;
  profile.number = state.phone;

  try {
    const contactId = state.wid || info.wid?._serialized;
    if (contactId) {
      const contact = await client.getContactById(contactId);
      profile.name = String(contact.name || "").trim();
      profile.short_name = String(contact.shortName || "").trim();
      profile.push_name = String(contact.pushname || profile.push_name || "").trim();
      profile.number = String(contact.number || profile.number || "").trim();
      profile.about = String(contact.about || "").trim();
      profile.is_business = Boolean(contact.isBusiness);
      profile.is_enterprise = Boolean(contact.isEnterprise);
      profile.is_my_contact = Boolean(contact.isMyContact);

      try {
        const avatarUrl = await client.getProfilePicUrl(contactId);
        profile.avatar_url = typeof avatarUrl === "string" ? avatarUrl : "";
      } catch (_avatarError) {
        profile.avatar_url = "";
      }
    }
  } catch (_contactError) {
    // Profile enrichment is best-effort only.
  }

  if (!profile.name) {
    profile.name = profile.push_name || state.displayName;
  }

  state.profile = profile;
}

async function attachClientEventHandlers(state, client) {
  client.on("qr", async (qr) => {
    try {
      state.qrCodeDataUrl = await QRCode.toDataURL(qr);
      state.qrAvailable = true;
      setConnectionStatus(state, "qr");
      state.lastError = "";
    } catch (error) {
      state.lastError = sanitizeError(error);
      setConnectionStatus(state, "disconnected");
      state.qrAvailable = false;
      state.qrCodeDataUrl = "";
    }
  });

  client.on("authenticated", () => {
    setConnectionStatus(state, "connecting");
    state.lastError = "";
  });

  client.on("ready", async () => {
    clearReconnectTimer(state);
    state.reconnectAttempts = 0;
    setConnectionStatus(state, "connected");
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.botActivatedAtMs = Date.now();
    state.botActivatedAt = new Date(state.botActivatedAtMs).toISOString();
    state.lastConnectionAt = state.botActivatedAt;
    state.lastError = "";
    await refreshProfile(state);
    await ensureOnlinePresence(state);
    syncRecentPersonalMessages(state).catch((error) => {
      state.lastError = `WhatsApp history sync failed: ${sanitizeError(error)}`;
      console.error(`[runtime:${state.channelId}] history sync failed`, state.lastError);
    });
  });

  client.on("message", async (message) => {
    await handleRuntimeMessageEvent(state, message, "message");
  });

  client.on("message_create", async (message) => {
    await handleRuntimeMessageEvent(state, message, "message_create");
  });

  client.on("auth_failure", (message) => {
    setConnectionStatus(state, "disconnected");
    clearPresenceKeepaliveTimer(state);
    stopAllTypingStates(state, { clearRemoteState: false }).catch(() => {});
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.botActivatedAt = "";
    state.botActivatedAtMs = 0;
    state.historySyncStarted = false;
    state.client = null;
    state.initPromise = null;
    state.lastError = sanitizeError(message || "WhatsApp authentication failed.");
    scheduleReconnect(state, "auth_failure");
  });

  client.on("disconnected", (reason) => {
    setConnectionStatus(state, "disconnected");
    clearPresenceKeepaliveTimer(state);
    stopAllTypingStates(state, { clearRemoteState: false }).catch(() => {});
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.botActivatedAt = "";
    state.botActivatedAtMs = 0;
    state.historySyncStarted = false;
    state.client = null;
    state.initPromise = null;
    state.lastError = sanitizeError(reason || "WhatsApp disconnected.");
    scheduleReconnect(state, "disconnected");
  });
}

async function createClient(state) {
  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: state.channelId,
      dataPath: sessionRoot
    }),
    puppeteer: {
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    }
  });

  state.client = client;
  setConnectionStatus(state, "connecting");
  state.lastError = "";
  state.botActivatedAt = "";
  state.botActivatedAtMs = 0;
  state.historySyncStarted = false;
  await attachClientEventHandlers(state, client);

  try {
    await client.initialize();
  } catch (error) {
    setConnectionStatus(state, "disconnected");
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.lastError = sanitizeError(error);
    throw error;
  }
}

async function ensureClient(state) {
  if (state.initPromise) {
    await state.initPromise;
    return state;
  }

  if (isConnectingStalled(state)) {
    console.warn(
      `[runtime:${state.channelId}] connecting stalled; restarting local browser session`,
      JSON.stringify({ stallMs: Date.now() - state.connectingSinceMs })
    );
    state.lastError = "WhatsApp Web JS connection stalled; restarted the local browser session.";
    await destroyClient(state, false);
  }

  if (state.client) {
    return state;
  }

  state.initPromise = createClient(state)
    .catch((error) => {
      state.client = null;
      throw error;
    })
    .finally(() => {
      state.initPromise = null;
    });

  await state.initPromise;
  return state;
}

async function restartStalledConnection(state) {
  if (!isConnectingStalled(state) || state.isResetting || state.stallRestarting) {
    return;
  }

  state.stallRestarting = true;
  try {
    console.warn(
      `[runtime:${state.channelId}] watchdog restarting stalled WhatsApp connection`,
      JSON.stringify({ stallMs: Date.now() - state.connectingSinceMs })
    );
    state.lastError = "WhatsApp Web JS connection stalled; watchdog restarted the local browser session.";
    state.initPromise = null;
    await destroyClient(state, false);
    await ensureClient(state);
    await waitForRenderableState(state, 30000);
  } catch (error) {
    state.client = null;
    state.initPromise = null;
    state.lastError = sanitizeError(error);
    scheduleReconnect(state, "watchdog_reconnect_failed");
  } finally {
    state.stallRestarting = false;
  }
}

async function bootstrapDefaultChannel() {
  const defaultChannelKey = String(process.env.RUNTIME_PLATFORM_CHANNEL_KEY || "").trim();
  if (!defaultChannelKey) {
    return;
  }

  const defaultDisplayName = String(process.env.SIMPLE_CONNECT_NAME || "Platform WhatsApp").trim();
  const state = getState(defaultChannelKey, defaultDisplayName);

  try {
    await ensureClient(state);
    await waitForRenderableState(state);
    console.log(
      `[runtime:${defaultChannelKey}] bootstrap complete`,
      JSON.stringify({
        connectionStatus: state.connectionStatus,
        qrAvailable: state.qrAvailable
      })
    );
  } catch (error) {
    state.lastError = sanitizeError(error);
    console.error(`[runtime:${defaultChannelKey}] bootstrap failed`, state.lastError);
  }
}

async function destroyClient(state, wipeSession) {
  clearReconnectTimer(state);
  clearPresenceKeepaliveTimer(state);
  await stopAllTypingStates(state, { clearRemoteState: true });
  if (state.client) {
    try {
      await state.client.destroy();
    } catch (_destroyError) {
      // Cleanup should continue even if the client is already gone.
    }
  }

  state.client = null;
  state.initPromise = null;
  setConnectionStatus(state, "disconnected");
  state.qrAvailable = false;
  state.qrCodeDataUrl = "";
  state.phone = "";
  state.pushName = "";
  state.platform = "";
  state.wid = "";
  state.profile = buildEmptyProfile();
  state.botActivatedAt = "";
  state.botActivatedAtMs = 0;
  state.historySyncStarted = false;

  if (wipeSession) {
    await removeSessionDirectoryWithRetries(sessionDirectory(state.channelId));
  }
}

async function removeSessionDirectoryWithRetries(targetDirectory) {
  let lastError = null;
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    try {
      fs.rmSync(targetDirectory, { recursive: true, force: true });
      return;
    } catch (error) {
      lastError = error;
      const code = String(error?.code || "").toUpperCase();
      if (!["EBUSY", "ENOTEMPTY", "EPERM"].includes(code)) {
        throw error;
      }
      await sleep(250 * attempt);
    }
  }

  throw lastError || new Error(`Could not remove session directory: ${targetDirectory}`);
}

const callbackQueueTimer = setInterval(() => {
  for (const state of channelStates.values()) {
    flushPlatformCallbackQueue(state).catch((error) => {
      state.lastError = `Platform callback retry failed: ${sanitizeError(error)}`;
      console.error(`[runtime:${state.channelId}] platform callback retry error`, state.lastError);
    });
  }
}, platformCallbackRetryMs);

if (typeof callbackQueueTimer.unref === "function") {
  callbackQueueTimer.unref();
}

const connectionWatchdogTimer = setInterval(() => {
  for (const state of channelStates.values()) {
    restartStalledConnection(state).catch((error) => {
      state.lastError = `Connection watchdog failed: ${sanitizeError(error)}`;
      console.error(`[runtime:${state.channelId}] connection watchdog error`, state.lastError);
    });
  }
}, 30000);

if (typeof connectionWatchdogTimer.unref === "function") {
  connectionWatchdogTimer.unref();
}

app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "ai-connector-runtime",
    channels: [...channelStates.keys()],
    callback_configured: Boolean(runtimePlatformCallbackUrl),
    callback_url: runtimePlatformCallbackUrl || ""
  });
});

app.put("/api/v1/channels/:channelKey", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  if (!channelKey) {
    res.status(400).json({ detail: "channelKey is required." });
    return;
  }

  const displayName = String(req.body?.display_name || req.body?.channel_name || "Platform WhatsApp").trim();
  const state = getState(channelKey, displayName);

  try {
    await ensureClient(state);
    await waitForRenderableState(state);
    res.json(serializeState(state));
  } catch (error) {
    res.status(502).json({ detail: sanitizeError(error) });
  }
});

app.get("/api/v1/channels/:channelKey", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const state = channelStates.get(channelKey);
  if (!state) {
    res.status(404).json({ detail: "Runtime channel not found." });
    return;
  }

  if (state.connectionStatus === "connected") {
    await refreshProfile(state);
  }

  res.json(serializeState(state));
});

app.post("/api/v1/channels/:channelKey/reset", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const existingState = channelStates.get(channelKey);
  const state = getState(channelKey, existingState?.displayName || "Platform WhatsApp");

  try {
    state.isResetting = true;
    await destroyClient(state, true);
    state.isResetting = false;
    await ensureClient(state);
    await waitForRenderableState(state);
    res.json(serializeState(state));
  } catch (error) {
    state.isResetting = false;
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.post("/api/v1/channels/:channelKey/typing", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const state = channelStates.get(channelKey);
  if (!state) {
    res.status(404).json({ detail: "Runtime channel not found." });
    return;
  }

  const chatId = String(req.body?.chat_id || "").trim();
  const active = req.body?.active !== false;
  if (!chatId) {
    res.status(400).json({ detail: "chat_id is required." });
    return;
  }

  try {
    if (active) {
      await startTypingState(state, chatId);
    } else {
      await stopTypingState(state, chatId);
    }
    res.json({ ok: true, channel_id: channelKey, chat_id: chatId, typing: active });
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.post("/api/v1/channels/:channelKey/contacts/resolve", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const state = channelStates.get(channelKey);
  if (!state) {
    res.status(404).json({ detail: "Runtime channel not found." });
    return;
  }

  const rawChatIds = Array.isArray(req.body?.chat_ids) ? req.body.chat_ids : [];
  const chatIds = [...new Set(
    rawChatIds
      .map((value) => String(value || "").trim())
      .filter((value) => value && isPersonalChatId(value))
  )].slice(0, 50);

  if (!chatIds.length) {
    res.json({ profiles: [] });
    return;
  }

  try {
    await ensureClient(state);
    if (state.connectionStatus !== "connected") {
      res.status(409).json({ detail: `Runtime channel is not connected (${state.connectionStatus}).` });
      return;
    }

    const profiles = [];
    for (const chatId of chatIds) {
      profiles.push(await resolveContactProfile(state, chatId));
    }
    res.json({ profiles });
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.post("/api/v1/channels/:channelKey/messages/send", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const state = channelStates.get(channelKey);
  if (!state) {
    res.status(404).json({ detail: "Runtime channel not found." });
    return;
  }

  const chatId = String(req.body?.chat_id || "").trim();
  const text = String(req.body?.text || "").trim();
  if (!chatId || !text) {
    res.status(400).json({ detail: "chat_id and text are required." });
    return;
  }

  try {
    await ensureClient(state);
    if (state.connectionStatus !== "connected") {
      res.status(409).json({ detail: `Runtime channel is not connected (${state.connectionStatus}).` });
      return;
    }

    const message = await state.client.sendMessage(chatId, text);
    state.lastMessageAt = nowIso();
    rememberPlatformOutboundMessage(state, String(message.id?._serialized || "").trim());
    res.json({ id_message: String(message.id?._serialized || "") });
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

const server = app.listen(runtimePort, "127.0.0.1", () => {
  console.log(
    `AI Connector runtime listening on 127.0.0.1:${runtimePort} | callback=${runtimePlatformCallbackUrl || "disabled"}`
  );
  bootstrapDefaultChannel().catch((error) => {
    console.error("[runtime] default bootstrap failed", sanitizeError(error));
  });
});

server.on("error", (error) => {
  console.error("[runtime] server listen failed", sanitizeError(error));
  process.exit(1);
});

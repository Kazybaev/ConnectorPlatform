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
app.use(express.json({ limit: "2mb" }));

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
const sessionRoot = path.resolve(__dirname, "..", "data", "runtime", "sessions");

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
    phone: "",
    pushName: "",
    platform: "",
    wid: "",
    profile: buildEmptyProfile(),
    lastConnectionAt: "",
    lastMessageAt: "",
    lastError: "",
    recentForwardedMessageIds: new Map(),
    pendingForwardedMessageIds: new Set(),
    recentPlatformOutboundMessageIds: new Map(),
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
    last_error: state.lastError
  };
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

async function resolveChatId(state, message) {
  try {
    const chat = await message.getChat();
    const resolved = String(chat?.id?._serialized || "").trim();
    if (resolved) {
      return resolved;
    }
  } catch (_chatError) {
    // Fallback to raw message fields below.
  }

  const ownWid = String(state.client?.info?.wid?._serialized || state.wid || "").trim();
  const from = String(message.from || "").trim();
  const to = String(message.to || "").trim();

  if (message.fromMe) {
    if (to) {
      return to;
    }
    if (from && from !== ownWid) {
      return from;
    }
  }

  return from || to || ownWid;
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
    const chatId = await resolveChatId(state, message);
    const isSelfChat = Boolean(ownWid && chatId && chatId === ownWid);
    const allowBotReply = Boolean(message.fromMe && isSelfChat);

    if (message.fromMe && !allowBotReply) {
      return;
    }

    const posted = await postIncomingMessageToPlatform(state, message, {
      chatId,
      eventName,
      isSelfChat,
      allowBotReply
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
          fromMe: Boolean(message.fromMe),
          isSelfChat,
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

async function postIncomingMessageToPlatform(state, message, options = {}) {
  if (!runtimePlatformCallbackUrl) {
    return false;
  }

  const text = normalizeIncomingText(message);
  const chatId = String(options.chatId || "").trim() || await resolveChatId(state, message);
  const isSelfChat = Boolean(options.isSelfChat);
  const allowBotReply = Boolean(options.allowBotReply);
  const contact = await message.getContact().catch(() => null);
  const senderName = String(
    contact?.pushname ||
    contact?.name ||
    contact?.shortName ||
    message?._data?.notifyName ||
    ""
  ).trim();

  const payload = {
    channel_key: state.channelId,
    channel_name: state.displayName,
    message: {
      external_message_id: String(message.id?._serialized || "").trim(),
      chat_id: chatId,
      sender_id: String(message.author || message.from || message.to || "").trim(),
      sender_name: senderName,
      text,
      message_type: String(message.type || "text").trim() || "text",
      timestamp: message.timestamp || null,
      from_me: Boolean(message.fromMe),
      self_chat: isSelfChat,
      allow_bot_reply: allowBotReply,
      event_source: String(options.eventName || "message").trim() || "message"
    }
  };

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
      return false;
    }
    return true;
  } catch (error) {
    state.lastError = `Platform inbox callback failed: ${sanitizeError(error)}`;
    console.error(`[runtime:${state.channelId}] platform callback error`, state.lastError);
    return false;
  }
}

async function waitForRenderableState(state, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (state.qrAvailable || state.connectionStatus === "connected" || state.lastError) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
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
      state.connectionStatus = "qr";
      state.lastError = "";
    } catch (error) {
      state.lastError = sanitizeError(error);
      state.connectionStatus = "disconnected";
      state.qrAvailable = false;
      state.qrCodeDataUrl = "";
    }
  });

  client.on("authenticated", () => {
    state.connectionStatus = "connecting";
    state.lastError = "";
  });

  client.on("ready", async () => {
    state.connectionStatus = "connected";
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.lastConnectionAt = nowIso();
    state.lastError = "";
    await refreshProfile(state);
  });

  client.on("message", async (message) => {
    await handleRuntimeMessageEvent(state, message, "message");
  });

  client.on("message_create", async (message) => {
    await handleRuntimeMessageEvent(state, message, "message_create");
  });

  client.on("auth_failure", (message) => {
    state.connectionStatus = "disconnected";
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.lastError = sanitizeError(message || "WhatsApp authentication failed.");
  });

  client.on("disconnected", (reason) => {
    state.connectionStatus = "disconnected";
    state.qrAvailable = false;
    state.qrCodeDataUrl = "";
    state.lastError = sanitizeError(reason || "WhatsApp disconnected.");
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
  state.connectionStatus = "connecting";
  state.lastError = "";
  await attachClientEventHandlers(state, client);

  try {
    await client.initialize();
  } catch (error) {
    state.connectionStatus = "disconnected";
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

async function destroyClient(state, wipeSession) {
  if (state.client) {
    try {
      await state.client.destroy();
    } catch (_destroyError) {
      // Cleanup should continue even if the client is already gone.
    }
  }

  state.client = null;
  state.initPromise = null;
  state.connectionStatus = "disconnected";
  state.qrAvailable = false;
  state.qrCodeDataUrl = "";
  state.phone = "";
  state.pushName = "";
  state.platform = "";
  state.wid = "";
  state.profile = buildEmptyProfile();

  if (wipeSession) {
    fs.rmSync(sessionDirectory(state.channelId), { recursive: true, force: true });
  }
}

app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "minigreenapi-runtime",
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
    if (state.client) {
      try {
        await state.client.logout();
      } catch (_logoutError) {
        // Some sessions cannot logout cleanly; we still wipe local auth below.
      }
    }

    await destroyClient(state, true);
    await ensureClient(state);
    await waitForRenderableState(state);
    res.json(serializeState(state));
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.post("/api/v1/channels/:channelKey/messages/send", requireRuntimeToken, async (req, res) => {
  const channelKey = String(req.params.channelKey || "").trim();
  const state = channelStates.get(channelKey);
  if (!state || !state.client) {
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
    const message = await state.client.sendMessage(chatId, text);
    state.lastMessageAt = nowIso();
    rememberPlatformOutboundMessage(state, String(message.id?._serialized || "").trim());
    res.json({ id_message: String(message.id?._serialized || "") });
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.listen(runtimePort, "127.0.0.1", () => {
  console.log(
    `MINIGREENAPI runtime listening on 127.0.0.1:${runtimePort} | callback=${runtimePlatformCallbackUrl || "disabled"}`
  );
});

const express = require("express");
const fs = require("fs");
const path = require("path");
const QRCode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

const app = express();
app.use(express.json({ limit: "2mb" }));

const runtimePort = Number(process.env.RUNTIME_PORT || "8011");
const runtimeToken = String(process.env.RUNTIME_TOKEN || "").trim();
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

  client.on("message", async () => {
    state.lastMessageAt = nowIso();
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
    channels: [...channelStates.keys()]
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
    res.json({ id_message: String(message.id?._serialized || "") });
  } catch (error) {
    state.lastError = sanitizeError(error);
    res.status(502).json({ detail: state.lastError });
  }
});

app.listen(runtimePort, "127.0.0.1", () => {
  console.log(`MINIGREENAPI runtime listening on 127.0.0.1:${runtimePort}`);
});

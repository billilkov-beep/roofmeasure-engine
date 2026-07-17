const { app, BrowserWindow, ipcMain, safeStorage, net, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const os = require('node:os');

const SERVER_URL = 'https://admin.goldslots.ai';
const IS_UI_TEST = process.env.GS_PLAYER_UI_TEST === '1';
let mainWindow = null;
let policyTimer = null;
let kioskLocked = false;
let policyReceived = false;
let securityChallenge = null;
let securityBypass = false;

app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');

const configPath = () => path.join(app.getPath('userData'), 'player-config.json');
const encrypt = (value) => safeStorage.encryptString(String(value || '')).toString('base64');
const decrypt = (value) => {
  try { return safeStorage.decryptString(Buffer.from(String(value || ''), 'base64')); }
  catch { return ''; }
};

function readConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), 'utf8'));
    return {
      serverUrl: String(raw.serverUrl || SERVER_URL).replace(/\/+$/, ''),
      deviceCode: String(raw.deviceCode || '').trim().toUpperCase(),
      deviceSecret: decrypt(raw.deviceSecret),
      sessionToken: decrypt(raw.sessionToken),
      demoPlayEnabled: raw.demoPlayEnabled === true,
      betOptionsCents: Array.isArray(raw.betOptionsCents) ? raw.betOptionsCents.map(Number).filter(Number.isInteger) : [],
      enabledGameKeys: Array.isArray(raw.enabledGameKeys) ? raw.enabledGameKeys.map(String) : null
    };
  } catch {
    return { serverUrl: SERVER_URL, deviceCode: '', deviceSecret: '', sessionToken: '', demoPlayEnabled: false, betOptionsCents: [], enabledGameKeys: null };
  }
}

function writeConfig(next = {}) {
  const value = { ...readConfig(), ...next };
  const payload = {
    serverUrl: String(value.serverUrl || SERVER_URL).trim().replace(/\/+$/, ''),
    deviceCode: String(value.deviceCode || '').trim().toUpperCase(),
    deviceSecret: encrypt(value.deviceSecret),
    sessionToken: encrypt(value.sessionToken),
    demoPlayEnabled: value.demoPlayEnabled === true,
    betOptionsCents: Array.isArray(value.betOptionsCents) ? value.betOptionsCents.map(Number).filter((n) => Number.isInteger(n) && n >= 100 && n <= 50000) : [],
    enabledGameKeys: Array.isArray(value.enabledGameKeys) ? value.enabledGameKeys.map(String) : null
  };
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(configPath(), JSON.stringify(payload), { mode: 0o600 });
  return readConfig();
}

function publicConfig() {
  const value = readConfig();
  return {
    serverUrl: value.serverUrl,
    deviceCode: value.deviceCode,
    configured: Boolean(value.deviceCode && value.deviceSecret),
    hasSession: Boolean(value.sessionToken),
    kioskLocked: policyReceived && kioskLocked,
    terminalDisabled: false,
    demoPlayEnabled: value.demoPlayEnabled,
    betOptionsCents: value.betOptionsCents,
    enabledGameKeys: value.enabledGameKeys
  };
}

function fingerprint() {
  return crypto.createHash('sha256').update([os.hostname(), os.platform(), os.arch(), app.getPath('userData')].join('|')).digest('hex');
}

async function activateTerminal(terminalIdValue) {
  const terminalId = String(terminalIdValue || '').trim().toUpperCase();
  if (!/^[A-Z0-9_-]{4,64}$/.test(terminalId)) throw new Error('Enter the Terminal ID from Super Admin.');
  if (IS_UI_TEST) {
    writeConfig({ serverUrl: SERVER_URL, deviceCode: terminalId, deviceSecret: 'UI-TEST-SECRET', sessionToken: '' });
    return publicConfig();
  }
  const response = await net.fetch(`${SERVER_URL}/api/device/activate`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID() },
    body: JSON.stringify({ terminalId, deviceType: 'PLAYER_TERMINAL', appVersion: app.getVersion(), hardwareFingerprint: fingerprint() })
  });
  let data;
  try { data = await response.json(); }
  catch { data = { error: `Server returned HTTP ${response.status}.` }; }
  if (!response.ok || data?.ok === false || !data?.deviceCode || !data?.deviceSecret) throw new Error(data?.error || 'Terminal pairing failed.');
  writeConfig({ serverUrl: SERVER_URL, deviceCode: data.deviceCode, deviceSecret: data.deviceSecret, sessionToken: '' });
  return publicConfig();
}

const ALLOWED_PATHS = new Set(['/api/player/reserve', '/api/player/state', '/api/player/action', '/api/player/release', '/api/player/security', '/api/player/policy', '/api/player/demo']);
let testBalance = 250000;
let testBlackjack = null;
let testPoker = null;

function testApi(pathname, body = {}) {
  const games = ['lucky-reels','five-card-poker','jacks-or-better','blackjack','keno','roulette','money-wheel','bingo','hi-lo','baccarat','craps'].map((key) => ({ key }));
  if (pathname === '/api/player/reserve') return { ok: true, token: 'UI-TEST-TOKEN', state: { card: { balanceCents: testBalance, currency: 'USD' }, games, betOptionsCents: [100,500,1000,2500,5000] } };
  if (pathname === '/api/player/state') return { ok: true, state: { card: { balanceCents: testBalance, currency: 'USD' }, games, betOptionsCents: [100,500,1000,2500,5000], gameState: { activeBlackjack: testBlackjack, activeJacksOrBetter: testPoker } } };
  if (pathname === '/api/player/policy') return { ok: true, terminalDisabled: false, kioskLocked: false, demoPlayEnabled: true, betOptionsCents: [100,500,1000,2500,5000], enabledGameKeys: games.map((game) => game.key) };
  if (pathname === '/api/player/release') return { ok: true, balanceCents: testBalance };
  if (pathname === '/api/player/security') return { ok: true, verified: true };
  if (pathname === '/api/player/demo') return { ok: true, round: { winCents: 0, result: { label: 'TEST ROUND COMPLETE', winMultiplier: 0, details: { firstCard: { rank: '7', suit: 'hearts' }, nextCard: { rank: 'K', suit: 'spades' }, number: 7, color: 'red', dice: [3,4] } } }, gameState: {} };
  if (pathname === '/api/player/action') {
    const action = body.action;
    const bet = Number(body.betCents || 0);
    if (action === 'HEARTBEAT') return { ok: true };
    if (action === 'BLACKJACK_DEAL') {
      testBalance -= bet;
      testBlackjack = { gameKey: 'blackjack-21', betCents: bet, playerCards: [{rank:'8',suit:'spades'},{rank:'6',suit:'clubs'}], dealerCards: [{rank:'10',suit:'hearts'},{rank:'7',suit:'diamonds'}] };
      return { ok: true, pending: true, balanceCents: testBalance, hand: testBlackjack };
    }
    if (action === 'BLACKJACK_HIT') {
      testBlackjack.playerCards.push({rank:'5',suit:'hearts'});
      return { ok: true, pending: true, balanceCents: testBalance, hand: testBlackjack };
    }
    if (action === 'BLACKJACK_STAND') {
      const hand = testBlackjack; testBlackjack = null;
      return { ok: true, balanceCents: testBalance, round: { winCents: 0, result: { label: 'DEALER WINS', winMultiplier: 0, playerCards: hand.playerCards, dealerCards: hand.dealerCards, playerTotal: 19, dealerTotal: 17 } } };
    }
    if (action === 'POKER_DEAL') {
      testBalance -= bet;
      testPoker = { gameKey: 'jacks-or-better', betCents: bet, cards: [{rank:'J',suit:'hearts'},{rank:'J',suit:'clubs'},{rank:'4',suit:'spades'},{rank:'8',suit:'diamonds'},{rank:'A',suit:'hearts'}] };
      return { ok: true, pending: true, balanceCents: testBalance, hand: testPoker };
    }
    if (action === 'POKER_DRAW') {
      const held = Array.isArray(body.heldIndexes) ? body.heldIndexes : [];
      const cards = testPoker.cards.map((card, index) => held.includes(index) ? card : ({rank:String(index + 2),suit:'spades'}));
      testPoker = null;
      return { ok: true, balanceCents: testBalance, round: { winCents: 0, result: { label: 'PAIR OF JACKS', winMultiplier: 0, cards, details: { heldIndexes: held } } } };
    }
    testBalance -= bet;
    return { ok: true, balanceCents: testBalance, round: { winCents: 0, result: { label: 'TEST ROUND COMPLETE', winMultiplier: 0, details: { firstCard: {rank:'7',suit:'hearts'}, nextCard: {rank:'K',suit:'spades'}, number: 7, color: 'red', dice: [3,4], segment: '2x', called: [1,2,3] } } } };
  }
  return { ok: true };
}

async function apiRequest(pathname, method = 'POST', body = null) {
  if (!ALLOWED_PATHS.has(pathname)) throw new Error('Unsupported Player request.');
  if (IS_UI_TEST) return { status: 200, ok: true, data: testApi(pathname, body || {}) };
  const config = readConfig();
  if (!/^https:\/\//i.test(config.serverUrl)) throw new Error('Server URL must use HTTPS.');
  const requestBody = ['/api/player/reserve','/api/player/security','/api/player/policy','/api/player/demo'].includes(pathname)
    ? { ...(body || {}), deviceCode: config.deviceCode, deviceSecret: config.deviceSecret }
    : body;
  const headers = { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID(), 'X-Player-Version': app.getVersion() };
  if (config.sessionToken) headers.Authorization = `Bearer ${config.sessionToken}`;
  const response = await net.fetch(`${config.serverUrl}${pathname}`, { method, headers, body: method === 'GET' || requestBody == null ? undefined : JSON.stringify(requestBody) });
  let data;
  try { data = await response.json(); }
  catch { data = { error: `Server returned HTTP ${response.status}.` }; }
  if (pathname === '/api/player/reserve' && response.ok && data?.token) writeConfig({ sessionToken: data.token });
  if (pathname === '/api/player/release' || response.status === 401) writeConfig({ sessionToken: '' });
  return { status: response.status, ok: response.ok && data?.ok !== false, data };
}

function applyWindowLock(locked) {
  kioskLocked = locked === true;
  if (!mainWindow || mainWindow.isDestroyed()) return;
  securityBypass = !kioskLocked;
  securityChallenge = null;
  mainWindow.setAlwaysOnTop(kioskLocked, kioskLocked ? 'screen-saver' : 'normal');
  mainWindow.setSkipTaskbar(kioskLocked);
  mainWindow.setKiosk(kioskLocked);
  mainWindow.setFullScreen(kioskLocked);
  if (!kioskLocked) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.maximize();
    mainWindow.show();
  }
}

function applyPolicy(policy = {}) {
  policyReceived = true;
  const terminalDisabled = policy.terminalDisabled === true;
  const bets = Array.isArray(policy.betOptionsCents) ? [...new Set(policy.betOptionsCents.map(Number).filter((n) => Number.isInteger(n) && n >= 100 && n <= 50000))].sort((a,b)=>a-b) : readConfig().betOptionsCents;
  const config = writeConfig({
    sessionToken: terminalDisabled ? '' : readConfig().sessionToken,
    demoPlayEnabled: !terminalDisabled && policy.demoPlayEnabled === true,
    betOptionsCents: terminalDisabled ? [] : bets,
    enabledGameKeys: terminalDisabled ? [] : Array.isArray(policy.enabledGameKeys) ? policy.enabledGameKeys : null
  });
  applyWindowLock(!terminalDisabled && policy.kioskLocked === true);
  mainWindow?.webContents.send('terminal:policy', {
    terminalDisabled,
    kioskLocked,
    minimizeAllowed: !kioskLocked,
    demoPlayEnabled: config.demoPlayEnabled,
    betOptionsCents: config.betOptionsCents,
    enabledGameKeys: config.enabledGameKeys
  });
}

async function refreshPolicy() {
  const config = readConfig();
  if (!config.deviceCode || !config.deviceSecret) return;
  try {
    const response = await apiRequest('/api/player/policy', 'POST', {});
    if (response.ok) applyPolicy(response.data);
    else if (response.status === 401) applyPolicy({ terminalDisabled: true, kioskLocked: false });
  } catch {}
}

function createWindow() {
  kioskLocked = false;
  policyReceived = false;
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    minWidth: 760,
    minHeight: 520,
    frame: true,
    fullscreen: false,
    kiosk: false,
    alwaysOnTop: false,
    skipTaskbar: false,
    autoHideMenuBar: true,
    show: false,
    backgroundColor: '#020711',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: !app.isPackaged || IS_UI_TEST,
      backgroundThrottling: false,
      spellcheck: false
    }
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event) => event.preventDefault());
  mainWindow.on('minimize', (event) => {
    if (!kioskLocked || securityBypass) return;
    event.preventDefault();
    mainWindow.restore();
  });
  mainWindow.on('close', (event) => {
    if (!kioskLocked || securityBypass) return;
    event.preventDefault();
    securityChallenge = { id: crypto.randomUUID(), intent: 'close' };
    mainWindow.webContents.send('security:challenge', securityChallenge);
  });
  mainWindow.once('ready-to-show', () => {
    mainWindow.maximize();
    mainWindow.show();
  });
  mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
}

app.whenReady().then(() => {
  ipcMain.handle('config:get', () => publicConfig());
  ipcMain.handle('config:activate', (_event, value) => activateTerminal(value?.terminalId));
  ipcMain.handle('session:clear', () => { writeConfig({ sessionToken: '' }); return true; });
  ipcMain.handle('api:request', (_event, value) => apiRequest(value.path, value.method || 'POST', value.body));
  ipcMain.handle('rfid:start', () => ({ ok: true, mode: 'USB_HID', message: 'USB HID keyboard-emulation reader ready.' }));
  ipcMain.handle('security:cancel', () => { securityChallenge = null; return { ok: true }; });
  ipcMain.handle('security:verify', async (_event, value) => {
    if (!kioskLocked) return { ok: true, intent: 'none' };
    if (!securityChallenge || value?.challengeId !== securityChallenge.id) return { ok: false, error: 'Authorization request expired.' };
    const response = await apiRequest('/api/player/security', 'POST', { action: 'VERIFY_TERMINAL_PIN', pin: String(value?.pin || ''), intent: securityChallenge.intent, challengeId: securityChallenge.id });
    if (!response.ok || response.data?.verified !== true) return { ok: false, error: response.data?.error || 'PIN not accepted.' };
    securityBypass = true;
    securityChallenge = null;
    applyWindowLock(false);
    mainWindow.close();
    return { ok: true, intent: 'close' };
  });
  createWindow();
  if (!IS_UI_TEST) {
    void refreshPolicy();
    policyTimer = setInterval(() => void refreshPolicy(), 30000);
  }
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => { if (policyTimer) clearInterval(policyTimer); });

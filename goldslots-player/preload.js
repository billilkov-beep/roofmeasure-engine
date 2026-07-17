const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('playerDesktop', {
  uiTest: process.env.GS_PLAYER_UI_TEST === '1',
  getConfig: () => ipcRenderer.invoke('config:get'),
  activateTerminal: (terminalId) => ipcRenderer.invoke('config:activate', { terminalId }),
  clearSession: () => ipcRenderer.invoke('session:clear'),
  request: (value) => ipcRenderer.invoke('api:request', value),
  startRfid: () => ipcRenderer.invoke('rfid:start'),
  verifyWindowPin: (value) => ipcRenderer.invoke('security:verify', value),
  cancelWindowRequest: () => ipcRenderer.invoke('security:cancel'),
  onTerminalPolicy: (handler) => ipcRenderer.on('terminal:policy', (_event, value) => handler(value)),
  onSecurityChallenge: (handler) => ipcRenderer.on('security:challenge', (_event, value) => handler(value))
});

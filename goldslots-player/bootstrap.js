const { app } = require('electron');

// Casino terminals use widely different integrated/NVIDIA/legacy drivers.
// Software rendering avoids GPU-process crashes and does not change clicks,
// server calculations, payouts, RFID sessions, or manual game decisions.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-gpu-compositing');

require('./main.js');

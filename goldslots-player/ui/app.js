const root = document.getElementById('app');

const GAMES = [
  { key:'lucky-reels', serverKey:'lucky-reels', name:'LUCKY 7 SLOTS', type:'slot', icon:'7', subtitle:'Classic five-reel cabinet', colors:['#7b161f','#22070b'] },
  { key:'five-card-poker', serverKey:'five-card-poker', name:'FIVE-CARD POKER', type:'poker-basic', icon:'♠', subtitle:'Dealer Duel or Pair Plus', colors:['#17452f','#07170f'] },
  { key:'jacks-or-better', serverKey:'jacks-or-better', name:'JACKS OR BETTER', type:'poker', icon:'J', subtitle:'Deal · Hold · Draw', colors:['#14513c','#061a12'] },
  { key:'blackjack-21', serverKey:'blackjack', name:'BLACKJACK', type:'blackjack', icon:'21', subtitle:'Manual Hit or Stand', colors:['#182d56','#070d1f'] },
  { key:'keno', serverKey:'keno', name:'KENO', type:'keno', icon:'80', subtitle:'Pick 1 to 10 numbers', colors:['#452a70','#12091f'] },
  { key:'roulette', serverKey:'roulette', name:'ROULETTE', type:'roulette', icon:'0', subtitle:'Single-zero wheel', colors:['#6e1721','#170508'] },
  { key:'money-wheel', serverKey:'money-wheel', name:'MONEY WHEEL', type:'wheel', icon:'×', subtitle:'Choose a multiplier', colors:['#164b78','#061526'] },
  { key:'bingo', serverKey:'bingo', name:'BINGO', type:'bingo', icon:'B', subtitle:'Choose a winning pattern', colors:['#74253b','#210812'] },
  { key:'hi-lo-cards', serverKey:'hi-lo', name:'HI-LO CARD', type:'hilo', icon:'↕', subtitle:'Choose Higher or Lower', colors:['#6c2430','#1e080d'] },
  { key:'baccarat', serverKey:'baccarat', name:'BACCARAT', type:'baccarat', icon:'B', subtitle:'Player · Banker · Tie', colors:['#614917','#1b1204'] },
  { key:'craps', serverKey:'craps', name:'CRAPS', type:'craps', icon:'⚄', subtitle:"Pass or Don't Pass", colors:['#195139','#061a11'] }
];

const state = {
  config:null,
  session:false,
  serverState:null,
  view:'boot',
  game:null,
  betCents:0,
  choices:{ rouletteBetType:null, rouletteBetValue:null, moneyWheelPick:null, fiveCardMode:null, hiLoPick:null, baccaratBet:null, crapsBet:null, bingoPattern:null },
  kenoPicks:[],
  busy:false,
  result:null,
  resultGame:null,
  error:'',
  message:'',
  soundOpen:false,
  sound:{master:true,effects:true,voice:true},
  readerStatus:'USB HID card reader ready',
  releasedBalance:0,
  blackjackHand:null,
  pokerHand:null,
  heldIndexes:[],
  securityChallenge:null,
  testMode:window.playerDesktop.uiTest === true
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money = (cents) => (Number(cents || 0) / 100).toLocaleString('en-US', { style:'currency', currency:state.serverState?.card?.currency || 'USD' });
const balance = () => Number(state.serverState?.card?.balanceCents || 0);
const allowedBets = () => {
  const source = state.config?.betOptionsCents?.length ? state.config.betOptionsCents : state.serverState?.betOptionsCents;
  const values = Array.isArray(source) ? source.map(Number).filter((n) => Number.isInteger(n) && n >= 100 && n <= 50000) : [];
  return [...new Set(values.length ? values : [100,500,1000,2500,5000])].sort((a,b)=>a-b);
};
const enabledGames = () => {
  const configured = Array.isArray(state.config?.enabledGameKeys) ? new Set(state.config.enabledGameKeys) : null;
  const live = Array.isArray(state.serverState?.games) ? new Set(state.serverState.games.map((game) => game.key)) : null;
  return GAMES.filter((game) => (!configured || configured.has(game.serverKey)) && (!live || live.has(game.serverKey)));
};

async function request(path, body = null, method = 'POST') {
  const response = await window.playerDesktop.request({ path, body, method });
  if (!response?.ok) throw new Error(response?.data?.error || 'The secure server could not complete this request.');
  return response.data;
}

function updateBalance(data) {
  const value = Number(data?.balanceCents ?? data?.round?.balanceAfterCents);
  if (Number.isFinite(value) && state.serverState?.card) state.serverState.card.balanceCents = value;
}

function clearChoices(game = state.game) {
  if (!game) return;
  if (game.type === 'roulette') { state.choices.rouletteBetType = null; state.choices.rouletteBetValue = null; }
  if (game.type === 'wheel') state.choices.moneyWheelPick = null;
  if (game.type === 'poker-basic') state.choices.fiveCardMode = null;
  if (game.type === 'hilo') state.choices.hiLoPick = null;
  if (game.type === 'baccarat') state.choices.baccaratBet = null;
  if (game.type === 'craps') state.choices.crapsBet = null;
  if (game.type === 'bingo') state.choices.bingoPattern = null;
  if (game.type === 'keno') state.kenoPicks = [];
}

function resetWager(game = state.game) {
  state.betCents = 0;
  clearChoices(game);
}

function manualDecisionReady(game) {
  if (!game) return false;
  if (game.type === 'slot' || game.type === 'blackjack' || game.type === 'poker') return true;
  if (game.type === 'poker-basic') return ['dealer-duel','pair-plus'].includes(state.choices.fiveCardMode);
  if (game.type === 'keno') return state.kenoPicks.length >= 1 && state.kenoPicks.length <= 10;
  if (game.type === 'roulette') return Boolean(state.choices.rouletteBetType && state.choices.rouletteBetValue !== null);
  if (game.type === 'wheel') return ['1x','2x','5x','10x'].includes(state.choices.moneyWheelPick);
  if (game.type === 'hilo') return ['higher','lower'].includes(state.choices.hiLoPick);
  if (game.type === 'baccarat') return ['player','banker','tie'].includes(state.choices.baccaratBet);
  if (game.type === 'craps') return ['pass','dont'].includes(state.choices.crapsBet);
  if (game.type === 'bingo') return ['line','corners','x'].includes(state.choices.bingoPattern);
  return false;
}

function canStart(game = state.game) {
  if (!game || state.busy || state.blackjackHand || state.pokerHand) return false;
  return allowedBets().includes(Number(state.betCents)) && manualDecisionReady(game);
}

function instruction(game = state.game) {
  if (!allowedBets().includes(Number(state.betCents))) return 'SELECT A STAKE FOR THIS ROUND';
  if (game?.type === 'poker-basic' && !state.choices.fiveCardMode) return 'CHOOSE DEALER DUEL OR PAIR PLUS';
  if (game?.type === 'keno' && !state.kenoPicks.length) return 'SELECT 1 TO 10 KENO NUMBERS';
  if (game?.type === 'roulette' && !state.choices.rouletteBetType) return 'SELECT A ROULETTE BET';
  if (game?.type === 'wheel' && !state.choices.moneyWheelPick) return 'SELECT 1X, 2X, 5X OR 10X';
  if (game?.type === 'hilo' && !state.choices.hiLoPick) return 'SELECT HIGHER OR LOWER';
  if (game?.type === 'baccarat' && !state.choices.baccaratBet) return 'SELECT PLAYER, BANKER OR TIE';
  if (game?.type === 'craps' && !state.choices.crapsBet) return "SELECT PASS OR DON'T PASS";
  if (game?.type === 'bingo' && !state.choices.bingoPattern) return 'SELECT LINE, CORNERS OR X';
  return 'PRESS PLAY TO CONFIRM THIS WAGER';
}

function messageMarkup() {
  const value = state.error || state.message;
  return value ? `<div class="message ${state.error ? 'error' : ''}">${esc(value)}</div>` : '';
}

function shell(content, options = {}) {
  return `<section class="shell">
    <header class="topbar">
      <div class="brand"><div class="logo">GS</div><div><strong>GOLD SLOTS</strong><small>Player Terminal · V6.9.2</small></div></div>
      <div class="top-actions"><div class="status-pill"><i></i><span>${state.session ? 'RFID SESSION ACTIVE' : 'SECURE TERMINAL'}</span></div><button class="icon-btn" data-action="sound-open" aria-label="Sound settings">♪</button><div class="terminal"><strong>${esc(state.config?.deviceCode || 'NOT PAIRED')}</strong><small>PLAYER TERMINAL</small></div></div>
    </header>
    <div class="content">${messageMarkup()}${content}</div>
    ${options.footer === false ? '' : `<footer class="footer"><small>SERVER-AUTHORITATIVE LEDGER · MANUAL PLAYER DECISIONS</small><div class="footer-actions">${state.session ? '<button class="btn" data-action="finish">FINISH / VISIT CLERK</button>' : ''}</div></footer>`}
    ${state.soundOpen ? soundModal() : ''}
    ${state.securityChallenge ? securityModal() : ''}
  </section>`;
}

function setupView() {
  return shell(`<div class="setup"><section class="setup-card"><div class="logo">GS</div><h1>Pair this Player</h1><p>Enter one Player Terminal ID from Super Admin. Pairing is required only once.</p><form id="pair-form"><input name="terminalId" autocomplete="off" placeholder="Example: TERM-002" required><button class="btn primary" type="submit">PAIR PLAYER TERMINAL</button></form></section></div>`, { footer:false });
}

function tapView() {
  return shell(`<div class="tap-screen"><section class="tap-card"><div class="logo">GS</div><h1>Tap player card</h1><p>Use the connected USB RFID/card reader, or enter the printed card number.</p><div class="card-entry"><input id="card-number" autocomplete="off" placeholder="CARD-000001"><button class="btn primary" data-action="open-card">OPEN SESSION</button></div><div class="reader">${esc(state.readerStatus)}</div>${state.config?.demoPlayEnabled ? '<button class="btn" data-action="demo-start" style="margin-top:14px">START TRAINING DEMO</button>' : ''}</section></div>`);
}

function lobbyView() {
  const cards = enabledGames().map((game) => `<button class="game-card" data-action="game" data-key="${game.key}" style="--card1:${game.colors[0]};--card2:${game.colors[1]};--glow:${game.colors[0]}99"><span class="game-icon">${esc(game.icon)}</span><span class="game-copy"><strong>${esc(game.name)}</strong><small>${esc(game.subtitle)}</small></span></button>`).join('');
  return shell(`<section class="lobby"><header class="lobby-head"><div><h1>Choose your game</h1><p>Every stake and game decision is selected manually for each round.</p></div><div class="balance"><span>CARD BALANCE</span><strong data-balance>${money(balance())}</strong></div></header><div class="game-grid">${cards}</div></section>`);
}

function choiceButton(label, key, value) {
  return `<button class="choice ${state.choices[key] === value ? 'active' : ''}" data-action="choice" data-choice-key="${key}" data-value="${value}">${label}</button>`;
}

function decisionMarkup(game) {
  if (game.type === 'poker-basic') return `<div class="choices">${choiceButton('DEALER DUEL','fiveCardMode','dealer-duel')}${choiceButton('PAIR PLUS','fiveCardMode','pair-plus')}</div>`;
  if (game.type === 'wheel') return `<div class="choices">${['1x','2x','5x','10x'].map((v) => choiceButton(v.toUpperCase(),'moneyWheelPick',v)).join('')}</div>`;
  if (game.type === 'hilo') return `<div class="choices">${choiceButton('LOWER','hiLoPick','lower')}${choiceButton('HIGHER','hiLoPick','higher')}</div>`;
  if (game.type === 'baccarat') return `<div class="choices">${['player','banker','tie'].map((v) => choiceButton(v.toUpperCase(),'baccaratBet',v)).join('')}</div>`;
  if (game.type === 'craps') return `<div class="choices">${choiceButton('PASS LINE','crapsBet','pass')}${choiceButton("DON'T PASS",'crapsBet','dont')}</div>`;
  if (game.type === 'bingo') return `<div class="choices">${choiceButton('LINE','bingoPattern','line')}${choiceButton('CORNERS','bingoPattern','corners')}${choiceButton('X PATTERN','bingoPattern','x')}</div>`;
  if (game.type === 'roulette') {
    const options = [['RED','color','red'],['BLACK','color','black'],['EVEN','parity','even'],['ODD','parity','odd'],['ZERO','number','0'],['SEVEN','number','7']];
    return `<div class="choices">${options.map(([label,type,value]) => `<button class="choice ${state.choices.rouletteBetType===type && String(state.choices.rouletteBetValue)===value ? 'active' : ''}" data-action="roulette" data-type="${type}" data-value="${value}">${label}</button>`).join('')}</div>`;
  }
  if (game.type === 'blackjack' && state.blackjackHand) return `<div class="choices"><button class="choice" data-action="blackjack-hit">HIT</button><button class="choice" data-action="blackjack-stand">STAND</button></div>`;
  if (game.type === 'poker' && state.pokerHand) return `<div class="decision"><strong>${state.heldIndexes.length} CARD${state.heldIndexes.length===1?'':'S'} HELD</strong><small>Tap cards to hold or release, then press Draw.</small></div>`;
  if (game.type === 'keno') return `<div class="choices"><button class="choice" data-action="keno-quick">PLAYER QUICK PICK</button><button class="choice" data-action="keno-clear">CLEAR</button></div>`;
  return `<div class="decision"><strong>MANUAL PLAY</strong><small>No automatic repeat or autoplay.</small></div>`;
}

function cardTotal(cards = []) {
  let total = 0, aces = 0;
  for (const card of cards) {
    if (card.rank === 'A') { total += 11; aces += 1; }
    else if (['K','Q','J'].includes(card.rank)) total += 10;
    else total += Number(card.rank) || 0;
  }
  while (total > 21 && aces) { total -= 10; aces -= 1; }
  return total;
}

function cardMarkup(card, index, holdable = false) {
  const red = ['hearts','diamonds'].includes(card?.suit);
  const suit = ({spades:'♠',hearts:'♥',diamonds:'♦',clubs:'♣'})[card?.suit] || '•';
  const inner = `${esc(card?.rank || '?')}<br>${suit}`;
  return holdable ? `<button class="card-button ${red?'red':''} ${state.heldIndexes.includes(index)?'held':''}" data-action="poker-hold" data-index="${index}">${inner}</button>` : `<div class="card-button ${red?'red':''}">${inner}</div>`;
}

function stageMarkup(game) {
  if (game.type === 'keno') return `<div class="keno-board">${Array.from({length:80},(_,i)=>i+1).map((n)=>`<button class="number ${state.kenoPicks.includes(n)?'active':''}" data-action="keno-number" data-number="${n}">${n}</button>`).join('')}</div>`;
  if (game.type === 'blackjack' && state.blackjackHand) {
    return `<div class="stage-art"><h2>PLAYER ${cardTotal(state.blackjackHand.playerCards)}</h2><div class="cards">${state.blackjackHand.playerCards.map((card,i)=>cardMarkup(card,i)).join('')}</div><p>Dealer shows ${esc(state.blackjackHand.dealerCards?.[0]?.rank || '?')} · Choose Hit or Stand.</p></div>`;
  }
  if (game.type === 'poker' && state.pokerHand) return `<div class="stage-art"><h2>SELECT CARDS TO HOLD</h2><div class="cards">${state.pokerHand.cards.map((card,i)=>cardMarkup(card,i,true)).join('')}</div><p>Only unheld cards are replaced when you press Draw.</p></div>`;
  const detail = game.type === 'hilo' ? 'The next card is revealed only after your Higher or Lower choice.' : game.subtitle;
  return `<div class="stage-art"><div class="stage-symbol">${esc(game.icon)}</div><h2>${esc(game.name)}</h2><p>${esc(detail)}</p></div>`;
}

function resultMarkup() {
  if (!state.result) return '';
  return `<aside class="result"><span>OFFICIAL ROUND RESULT</span><strong>${esc(state.result.label || 'ROUND COMPLETE')}</strong></aside>`;
}

function gameView() {
  const game = state.game;
  const pendingBlackjack = game.type === 'blackjack' && state.blackjackHand;
  const pendingPoker = game.type === 'poker' && state.pokerHand;
  const actionLabel = pendingBlackjack ? 'CHOOSE HIT OR STAND' : pendingPoker ? `DRAW ${5-state.heldIndexes.length} CARD${5-state.heldIndexes.length===1?'':'S'}` : game.type === 'blackjack' ? 'DEAL BLACKJACK' : game.type === 'poker' ? 'DEAL FIVE CARDS' : game.type === 'slot' ? 'SPIN REELS' : game.type === 'roulette' ? 'SPIN ROULETTE' : game.type === 'keno' ? 'DRAW KENO' : game.type === 'wheel' ? 'SPIN WHEEL' : game.type === 'hilo' ? 'REVEAL NEXT CARD' : game.type === 'craps' ? 'THROW DICE' : `PLAY ${game.name}`;
  const playAction = pendingPoker ? 'poker-draw' : 'play';
  const playDisabled = pendingBlackjack || (!pendingPoker && !canStart(game));
  return shell(`<section class="game-view" data-game="${game.key}"><header class="game-head"><button class="btn" data-action="back">‹ GAMES</button><div class="game-title"><h1>${esc(game.name)}</h1><small>${esc(game.subtitle)}</small></div><div class="balance"><span>CARD BALANCE</span><strong data-balance>${money(balance())}</strong></div></header><div class="stage">${stageMarkup(game)}${resultMarkup()}${state.busy?'<div class="spinner"></div>':''}</div><section class="controls"><div class="control-box"><label>STAKE · SELECT EACH ROUND</label><div class="stakes">${allowedBets().map((value)=>`<button class="stake ${state.betCents===value?'active':''}" data-action="stake" data-value="${value}" ${state.busy||pendingBlackjack||pendingPoker?'disabled':''}>${money(value)}</button>`).join('')}</div></div><div class="control-box"><label>PLAYER DECISION</label>${decisionMarkup(game)}</div><div class="control-box decision"><span>ROUND STATUS</span><strong>${state.busy?'ROUND IN PROGRESS':pendingBlackjack?'CHOOSE HIT OR STAND':pendingPoker?'SELECT HOLDS THEN DRAW':instruction(game)}</strong><small>No automatic wager or decision is made.</small></div><button class="play-button" data-action="${playAction}" ${playDisabled||state.busy?'disabled':''}>${esc(actionLabel)}</button></section></section>`);
}

function releasedView() {
  return shell(`<div class="released"><section class="released-card"><div class="logo">✓</div><h1>Session released</h1><p>The RFID card was released with a server balance of <strong>${money(state.releasedBalance)}</strong>.</p><button class="btn primary" data-action="next-player">READY FOR NEXT PLAYER</button></section></div>`);
}

function soundModal() {
  return `<div class="modal"><section class="modal-card"><header class="modal-head"><h2>Sound settings</h2><button class="icon-btn" data-action="sound-close">×</button></header>${['master','effects','voice'].map((key)=>`<button class="sound-row btn" data-action="sound-toggle" data-key="${key}"><span>${key.toUpperCase()}</span><strong>${state.sound[key]?'ON':'OFF'}</strong></button>`).join('')}</section></div>`;
}

function securityModal() {
  return `<div class="modal"><section class="modal-card"><h2>Clerk authorization required</h2><p>Super Admin has locked this terminal. Enter the Clerk PIN to close it.</p><form class="pin-form" id="pin-form"><input name="pin" type="password" inputmode="numeric" autocomplete="off" required><button class="btn primary" type="submit">AUTHORIZE</button><button class="btn" type="button" data-action="security-cancel">CANCEL</button></form></section></div>`;
}

function render() {
  let markup;
  if (state.view === 'boot') markup = shell('<div class="tap-screen"><div class="spinner"></div></div>', {footer:false});
  else if (state.view === 'setup') markup = setupView();
  else if (state.view === 'tap') markup = tapView();
  else if (state.view === 'released') markup = releasedView();
  else if (state.game) markup = gameView();
  else markup = lobbyView();
  root.innerHTML = markup;
}

function settle(game, data) {
  updateBalance(data);
  const round = data.round || data;
  state.result = round.result || data.result || { label:'ROUND COMPLETE' };
  state.resultGame = game.key;
  state.busy = false;
  resetWager(game);
  render();
}

async function standardPlay() {
  const game = state.game;
  if (!canStart(game)) { state.error = instruction(game); render(); return; }
  if (balance() < state.betCents) { state.error = 'Card balance is too low. Visit the Clerk to add money.'; render(); return; }
  state.busy = true; state.error = ''; state.result = null; render();
  try {
    const choices = { ...state.choices, kenoPicks:[...state.kenoPicks] };
    const data = await request('/api/player/action', { action:'PLAY', gameKey:game.serverKey, betCents:state.betCents, choices });
    settle(game, data);
  } catch (error) { state.busy=false; state.error=error.message; render(); }
}

async function blackjackDeal() {
  const game = state.game;
  if (!canStart(game)) { state.error=instruction(game); render(); return; }
  state.busy=true; state.error=''; state.result=null; render();
  try {
    const data=await request('/api/player/action',{action:'BLACKJACK_DEAL',betCents:state.betCents});
    updateBalance(data);
    if (data.pending) { state.blackjackHand=data.hand; state.busy=false; render(); }
    else settle(game,data);
  } catch(error){state.busy=false;state.error=error.message;render();}
}

async function blackjackDecision(action) {
  if (!state.blackjackHand || state.busy) return;
  state.busy=true;state.error='';render();
  try {
    const data=await request('/api/player/action',{action});
    updateBalance(data);
    if(data.pending){state.blackjackHand=data.hand;state.busy=false;render();}
    else{state.blackjackHand=null;settle(state.game,data);}
  }catch(error){state.busy=false;state.error=error.message;render();}
}

async function pokerDeal() {
  const game=state.game;
  if(!canStart(game)){state.error=instruction(game);render();return;}
  state.busy=true;state.error='';state.result=null;render();
  try{
    const data=await request('/api/player/action',{action:'POKER_DEAL',betCents:state.betCents});
    updateBalance(data);state.pokerHand=data.hand;state.heldIndexes=[];state.busy=false;render();
  }catch(error){state.busy=false;state.error=error.message;render();}
}

async function pokerDraw() {
  if(!state.pokerHand||state.busy)return;
  state.busy=true;state.error='';render();
  try{
    const data=await request('/api/player/action',{action:'POKER_DRAW',heldIndexes:[...state.heldIndexes]});
    state.pokerHand=null;state.heldIndexes=[];settle(state.game,data);
  }catch(error){state.busy=false;state.error=error.message;render();}
}

async function openSession(payload) {
  state.error='';state.message='Opening secure player session…';render();
  try{
    const data=await request('/api/player/reserve',payload);
    state.session=true;state.serverState=data.state;state.view='lobby';state.message='RFID card accepted. This terminal is reserved.';state.error='';resetWager();render();
  }catch(error){state.message='';state.error=error.message;render();}
}

async function finishSession() {
  if(state.busy||state.blackjackHand||state.pokerHand){state.error='Finish the active hand before releasing the card.';render();return;}
  try{
    const data=await request('/api/player/release',{reason:'Player selected Finish / Visit Clerk'});
    state.releasedBalance=Number(data.balanceCents??balance());state.session=false;state.serverState=null;state.game=null;state.view='released';await window.playerDesktop.clearSession();render();
  }catch(error){state.error=error.message;render();}
}

root.addEventListener('click', async (event) => {
  const button=event.target.closest('[data-action]');
  if(!button||button.disabled)return;
  const action=button.dataset.action;
  try{
    if(action==='game'){
      const game=enabledGames().find((item)=>item.key===button.dataset.key);
      if(!game)return;
      state.game=game;state.result=null;state.error='';state.message='';resetWager(game);render();
    }else if(action==='back'){
      if(state.blackjackHand||state.pokerHand){state.error='Finish the active Blackjack or Poker hand before leaving.';render();return;}
      state.game=null;state.result=null;state.error='';render();
    }else if(action==='stake'){
      state.betCents=Number(button.dataset.value);state.result=null;state.error='';render();
    }else if(action==='choice'){
      state.choices[button.dataset.choiceKey]=button.dataset.value;state.result=null;state.error='';render();
    }else if(action==='roulette'){
      state.choices.rouletteBetType=button.dataset.type;state.choices.rouletteBetValue=button.dataset.value;state.result=null;state.error='';render();
    }else if(action==='keno-number'){
      const number=Number(button.dataset.number);state.kenoPicks=state.kenoPicks.includes(number)?state.kenoPicks.filter((n)=>n!==number):state.kenoPicks.length<10?[...state.kenoPicks,number]:state.kenoPicks;state.result=null;render();
    }else if(action==='keno-quick'){
      const pool=Array.from({length:80},(_,i)=>i+1);for(let i=pool.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[pool[i],pool[j]]=[pool[j],pool[i]];}state.kenoPicks=pool.slice(0,10).sort((a,b)=>a-b);state.result=null;render();
    }else if(action==='keno-clear'){state.kenoPicks=[];state.result=null;render();}
    else if(action==='play'){
      if(state.game.type==='blackjack')await blackjackDeal();
      else if(state.game.type==='poker')await pokerDeal();
      else await standardPlay();
    }else if(action==='blackjack-hit')await blackjackDecision('BLACKJACK_HIT');
    else if(action==='blackjack-stand')await blackjackDecision('BLACKJACK_STAND');
    else if(action==='poker-hold'){
      const index=Number(button.dataset.index);state.heldIndexes=state.heldIndexes.includes(index)?state.heldIndexes.filter((n)=>n!==index):[...state.heldIndexes,index].sort((a,b)=>a-b);render();
    }else if(action==='poker-draw')await pokerDraw();
    else if(action==='open-card'){
      const value=document.getElementById('card-number')?.value.trim();if(!value)throw new Error('Tap a card or enter the printed card number.');await openSession({cardNumber:value});
    }else if(action==='demo-start')await openSession({cardNumber:'DEMO-TRAINING'});
    else if(action==='finish')await finishSession();
    else if(action==='next-player'){state.view='tap';state.releasedBalance=0;state.message='';state.error='';render();}
    else if(action==='sound-open'){state.soundOpen=true;render();}
    else if(action==='sound-close'){state.soundOpen=false;render();}
    else if(action==='sound-toggle'){state.sound[button.dataset.key]=!state.sound[button.dataset.key];render();}
    else if(action==='security-cancel'){await window.playerDesktop.cancelWindowRequest();state.securityChallenge=null;render();}
  }catch(error){state.error=error.message||'Action failed.';render();}
});

root.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form=new FormData(event.target);
  try{
    if(event.target.id==='pair-form'){
      state.config=await window.playerDesktop.activateTerminal(form.get('terminalId'));state.view='tap';state.message='Player terminal paired successfully.';render();
    }else if(event.target.id==='pin-form'){
      const result=await window.playerDesktop.verifyWindowPin({challengeId:state.securityChallenge.id,pin:form.get('pin')});
      if(!result.ok)throw new Error(result.error||'PIN not accepted.');state.securityChallenge=null;render();
    }
  }catch(error){state.error=error.message;render();}
});

let scannerBuffer='',scannerTimer=null,lastKeyAt=0;
window.addEventListener('keydown',(event)=>{
  const editable=['INPUT','TEXTAREA','SELECT'].includes(event.target?.tagName);
  if(editable||state.session||state.view!=='tap')return;
  const now=performance.now();if(now-lastKeyAt>180)scannerBuffer='';lastKeyAt=now;
  if(event.key==='Enter'&&scannerBuffer){const token=scannerBuffer.trim().toUpperCase();scannerBuffer='';clearTimeout(scannerTimer);if(/^[A-Z0-9_-]{4,64}$/.test(token))void openSession(token.startsWith('CARD-')?{cardNumber:token}:{uid:token});return;}
  if(event.key.length===1&&!event.ctrlKey&&!event.altKey&&!event.metaKey){scannerBuffer+=event.key;clearTimeout(scannerTimer);scannerTimer=setTimeout(()=>scannerBuffer='',350);}
});

window.playerDesktop.onTerminalPolicy((policy)=>{
  state.config={...state.config,terminalDisabled:policy.terminalDisabled===true,kioskLocked:policy.kioskLocked===true,demoPlayEnabled:policy.demoPlayEnabled===true,betOptionsCents:Array.isArray(policy.betOptionsCents)?policy.betOptionsCents:state.config?.betOptionsCents,enabledGameKeys:Array.isArray(policy.enabledGameKeys)?policy.enabledGameKeys:null};
  if(state.config.terminalDisabled){state.session=false;state.serverState=null;state.game=null;state.view='tap';state.error='This terminal was disabled by Super Admin.';}
  render();
});
window.playerDesktop.onSecurityChallenge((challenge)=>{state.securityChallenge=challenge;render();});

async function restore(){
  state.config=await window.playerDesktop.getConfig();
  if(state.testMode){state.config={...state.config,configured:true,deviceCode:'TERM-QA',demoPlayEnabled:true,betOptionsCents:[100,500,1000,2500,5000],enabledGameKeys:GAMES.map((g)=>g.serverKey)};await openSession({cardNumber:'CARD-QA'});return;}
  if(!state.config.configured){state.view='setup';render();return;}
  if(state.config.hasSession){
    try{const data=await request('/api/player/state',null,'GET');state.session=true;state.serverState=data.state||data;state.view='lobby';resetWager();}
    catch{await window.playerDesktop.clearSession();state.session=false;state.view='tap';}
  }else state.view='tap';
  const reader=await window.playerDesktop.startRfid();state.readerStatus=reader.message||'USB HID card reader ready';render();
}

render();
restore().catch((error)=>{state.error=error.message;state.view=state.config?.configured?'tap':'setup';render();});

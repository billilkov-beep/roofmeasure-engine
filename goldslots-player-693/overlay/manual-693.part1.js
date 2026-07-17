(() => {
  'use strict';

  const original = { compactChoices, pokerStage, blackjackStage, resultPanel, controlDeckMarkup };
  const manual = {
    betSelected: false,
    blackjackHand: null,
    blackjackDeck: [],
    pokerHand: null,
    pokerDeck: [],
    heldIndexes: [],
    pendingGameKey: null
  };
  state.manual = manual;

  Object.assign(state.choices, {
    rouletteBetType: null,
    rouletteBetValue: null,
    moneyWheelPick: null,
    hiLoPick: null,
    baccaratBet: null,
    crapsBet: null,
    bingoPattern: null,
    fiveCardMode: null
  });
  state.betCents = 0;

  const clone = (value) => value ? structuredClone(value) : null;
  const pendingBlackjack = () => manual.blackjackHand || state.serverState?.gameState?.activeBlackjack || null;
  const pendingPoker = () => manual.pokerHand || state.serverState?.gameState?.activeJacksOrBetter || null;
  const pendingHand = () => pendingBlackjack() || pendingPoker();

  function clearChoice(game) {
    if (!game) return;
    if (game.type === 'roulette') { state.choices.rouletteBetType = null; state.choices.rouletteBetValue = null; }
    if (game.type === 'wheel') state.choices.moneyWheelPick = null;
    if (game.type === 'hilo') state.choices.hiLoPick = null;
    if (game.type === 'baccarat') state.choices.baccaratBet = null;
    if (game.type === 'craps') state.choices.crapsBet = null;
    if (game.type === 'bingo') state.choices.bingoPattern = null;
    if (game.type === 'keno') state.kenoPicks = [];
    if (game.key === 'five-card-poker') state.choices.fiveCardMode = null;
  }

  function resetRound(game, preservePending = false) {
    if (!preservePending) {
      state.betCents = 0;
      manual.betSelected = false;
    }
    clearChoice(game);
  }

  function hydratePending(game) {
    if (!game) return;
    if (game.type === 'blackjack') {
      const hand = pendingBlackjack();
      if (hand) {
        manual.blackjackHand = clone(hand);
        manual.pendingGameKey = game.key;
        manual.betSelected = true;
        state.betCents = Number(hand.betCents || 0);
      }
    }
    if (game.key === 'jacks-or-better') {
      const hand = pendingPoker();
      if (hand) {
        manual.pokerHand = clone(hand);
        manual.heldIndexes = [...new Set(hand.heldIndexes || [])];
        manual.pendingGameKey = game.key;
        manual.betSelected = true;
        state.betCents = Number(hand.betCents || 0);
      }
    }
  }

  function hasDecision(game) {
    if (!game) return false;
    if (game.type === 'slot' || game.type === 'blackjack' || game.key === 'jacks-or-better') return true;
    if (game.key === 'five-card-poker') return ['dealer-duel','pair-plus'].includes(state.choices.fiveCardMode);
    if (game.type === 'keno') return state.kenoPicks.length >= 1 && state.kenoPicks.length <= 10;
    if (game.type === 'roulette') return Boolean(state.choices.rouletteBetType && state.choices.rouletteBetValue !== null && state.choices.rouletteBetValue !== undefined);
    if (game.type === 'wheel') return ['1x','2x','5x','10x'].includes(state.choices.moneyWheelPick);
    if (game.type === 'hilo') return ['higher','lower'].includes(state.choices.hiLoPick);
    if (game.type === 'baccarat') return ['player','banker','tie'].includes(state.choices.baccaratBet);
    if (game.type === 'craps') return ['pass','dont'].includes(state.choices.crapsBet);
    if (game.type === 'bingo') return ['line','corners','x'].includes(state.choices.bingoPattern);
    return false;
  }

  function ready(game) {
    if (!game || state.busy) return false;
    if (game.type === 'blackjack' && pendingBlackjack()) return false;
    if (game.key === 'jacks-or-better' && pendingPoker()) return false;
    return manual.betSelected && allowedBets().includes(Number(state.betCents)) && hasDecision(game);
  }

  function instruction(game) {
    if (!manual.betSelected || !allowedBets().includes(Number(state.betCents))) return 'SELECT A STAKE FOR THIS ROUND';
    if (game.key === 'five-card-poker' && !state.choices.fiveCardMode) return 'CHOOSE DEALER DUEL OR PAIR PLUS';
    if (game.type === 'keno' && !state.kenoPicks.length) return 'SELECT 1 TO 10 KENO NUMBERS';
    if (game.type === 'roulette' && !state.choices.rouletteBetType) return 'SELECT A ROULETTE BET';
    if (game.type === 'wheel' && !state.choices.moneyWheelPick) return 'SELECT 1X, 2X, 5X OR 10X';
    if (game.type === 'hilo' && !state.choices.hiLoPick) return 'SELECT HIGHER OR LOWER';
    if (game.type === 'baccarat' && !state.choices.baccaratBet) return 'SELECT PLAYER, BANKER OR TIE';
    if (game.type === 'craps' && !state.choices.crapsBet) return "SELECT PASS OR DON'T PASS";
    if (game.type === 'bingo' && !state.choices.bingoPattern) return 'SELECT LINE, CORNERS OR X';
    return 'PRESS THE GAME BUTTON TO CONFIRM';
  }

  function updateBalance(data) {
    const value = Number(data?.balanceCents ?? data?.round?.balanceAfterCents);
    if (Number.isFinite(value) && state.serverState?.card) state.serverState.card.balanceCents = value;
  }

  function announceResult() {
    const disposition = roundDisposition();
    const won = disposition === 'win';
    if (won) audio.play(Number(state.result?.winMultiplier || 0) >= 5 ? 'jackpot' : 'coinCascade');
    else if (disposition === 'push') audio.play('chip');
    else audio.play('lose');
    audio.announce(disposition === 'push' ? 'Push. Original wager returned' : resultAnnouncement(state.game, state.result, won));
  }

  function finishRound(game, data) {
    const round = data?.round || data;
    state.result = round?.result || data?.result || null;
    state.lastWinCents = Number(round?.winCents || 0);
    state.revealResult = true;
    state.animation = 'settled';
    state.busy = false;
    updateBalance(data);
    rememberResult(game, state.result);
    resetRound(game);
    render(true);
    announceResult();
    flushPendingPolicy();
  }

  async function standardRound(game) {
    if (!ready(game)) { state.error = instruction(game); render(true); return; }
    const betCents = Number(state.betCents);
    if (currentBalance() < betCents) { state.error = 'Card balance is too low. Please visit the Clerk to add money.'; render(true); return; }
    const choices = { ...state.choices, kenoPicks: [...state.kenoPicks] };
    if (game.type === 'roulette' && ['even','odd'].includes(choices.rouletteBetValue)) choices.rouletteBetType = 'parity';
    clearAnimationTimers();
    state.result = null; state.revealResult = false; state.error = ''; state.message = ''; state.busy = true;
    state.animation = startingAnimation(game.type); render(true); startRoundSound(game);
    try {
      let data;
      if (state.demoSession) {
        data = await request('/api/player/demo', { gameKey:game.key, betCents, choices, gameState:state.demoGameState });
        state.demoGameState = data.gameState || {};
        state.demoBalanceCents = state.demoBalanceCents - betCents + Number(data.round?.winCents || 0);
      } else {
        data = await request('/api/player/action', { action:'PLAY', gameKey:game.key, betCents, choices });
      }
      state.result = data.round.result;
      state.lastWinCents = Number(data.round.winCents || 0);
      updateBalance(data); rememberResult(game,state.result); render(true);
      await runRevealAnimation(game);
      finishRound(game,data);
    } catch (error) {
      audio.stopSlotSpin?.(false); state.error = error.message || 'No charge was made. Please try again.';
      state.busy = false; state.animation = 'idle'; render(true); flushPendingPolicy();
    }
  }

  function makeDeck() {
    const suits = ['spades','hearts','diamonds','clubs'];
    const ranks = ['2','3','4','5','6','7','8','9','10','J','Q','K','A'];
    const deck = suits.flatMap((suit) => ranks.map((rank) => ({rank,suit})));
    for (let i=deck.length-1;i>0;i-=1) { const j=Math.floor(Math.random()*(i+1)); [deck[i],deck[j]]=[deck[j],deck[i]]; }
    return deck;
  }

  function blackjackTotal(cards = []) {
    let total=0, aces=0;
    for (const card of cards) {
      const rank=String(card?.rank||'');
      if (rank==='A') { total+=11; aces+=1; }
      else if (['K','Q','J'].includes(rank)) total+=10;
      else total+=Number(rank)||0;
    }
    while (total>21&&aces) { total-=10; aces-=1; }
    return total;
  }

  function demoBlackjackResult(label, multiplier) {
    const hand = manual.blackjackHand;
    const bet = Number(hand.betCents || 0);

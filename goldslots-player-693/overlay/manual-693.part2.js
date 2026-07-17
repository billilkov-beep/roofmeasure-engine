    const payout = Math.round(bet * multiplier);
    state.demoBalanceCents += payout;
    state.result = {
      gameType:'blackjack-21', label,
      playerCards:hand.playerCards, dealerCards:hand.dealerCards,
      playerTotal:blackjackTotal(hand.playerCards), dealerTotal:blackjackTotal(hand.dealerCards),
      winMultiplier:multiplier, details:{ mechanic:'INTERACTIVE_HIT_STAND_DEMO' }
    };
    state.lastWinCents = payout;
    manual.blackjackHand = null; manual.blackjackDeck=[]; manual.pendingGameKey=null;
    state.revealResult=true; state.busy=false; state.animation='settled'; resetRound(state.game); render(true); announceResult();
  }

  async function dealBlackjack() {
    const game=state.game;
    if (!ready(game)) { state.error=instruction(game); render(true); return; }
    const bet=Number(state.betCents);
    if (currentBalance()<bet) { state.error='Card balance is too low. Please visit the Clerk to add money.'; render(true); return; }
    state.busy=true; state.error=''; state.result=null; state.revealResult=false; state.animation='dealing'; render(true); audio.play('cardFan');
    if (state.demoSession) {
      state.demoBalanceCents -= bet;
      const deck=makeDeck();
      const hand={gameKey:game.key,betCents:bet,playerCards:[deck.pop(),deck.pop()],dealerCards:[deck.pop(),deck.pop()],actions:[]};
      manual.blackjackDeck=deck; manual.blackjackHand=hand; manual.pendingGameKey=game.key; await wait(300);
      const pt=blackjackTotal(hand.playerCards), dt=blackjackTotal(hand.dealerCards);
      if (pt===21 || dt===21) {
        if (pt===21&&dt===21) demoBlackjackResult('Push — both have blackjack',1);
        else if (pt===21) demoBlackjackResult('Blackjack',2.5);
        else demoBlackjackResult('Dealer blackjack',0);
      } else { state.busy=false; state.animation='idle'; render(true); }
      return;
    }
    try {
      const data=await request('/api/player/action',{action:'BLACKJACK_DEAL',betCents:bet}); updateBalance(data); await wait(300);
      if (data.pending) {
        manual.blackjackHand=clone(data.hand); manual.pendingGameKey=game.key;
        state.serverState.gameState ||= {}; state.serverState.gameState.activeBlackjack=clone(data.hand);
        state.result=data.round?.result||null; state.animation='idle'; state.busy=false; render(true);
      } else {
        manual.blackjackHand=null; if(state.serverState?.gameState)delete state.serverState.gameState.activeBlackjack;
        await runRevealAnimation(game); finishRound(game,data);
      }
    } catch(error) { state.busy=false;state.animation='idle';state.error=error.message||'The Blackjack deal was not completed.';render(true); }
  }

  async function blackjackDecision(action) {
    const game=state.game, hand=pendingBlackjack();
    if(!game||game.type!=='blackjack'||!hand||state.busy)return;
    state.busy=true;state.error='';state.animation='dealing';render(true);audio.play('cardFan');
    if(state.demoSession){
      await wait(220);
      if(action==='BLACKJACK_HIT'){
        hand.playerCards.push(manual.blackjackDeck.pop());hand.actions.push('HIT');
        const total=blackjackTotal(hand.playerCards);
        if(total>21)demoBlackjackResult('Player bust',0);else{state.busy=false;state.animation='idle';render(true);}
      }else{
        hand.actions.push('STAND');
        while(blackjackTotal(hand.dealerCards)<17)hand.dealerCards.push(manual.blackjackDeck.pop());
        const pt=blackjackTotal(hand.playerCards),dt=blackjackTotal(hand.dealerCards);
        if(dt>21||pt>dt)demoBlackjackResult('Player wins',2);
        else if(pt===dt)demoBlackjackResult('Push',1);
        else demoBlackjackResult('Dealer wins',0);
      }
      return;
    }
    try{
      const data=await request('/api/player/action',{action});updateBalance(data);await wait(240);
      if(data.pending){manual.blackjackHand=clone(data.hand);state.serverState.gameState ||= {};state.serverState.gameState.activeBlackjack=clone(data.hand);state.animation='idle';state.busy=false;render(true);}
      else{manual.blackjackHand=null;manual.pendingGameKey=null;if(state.serverState?.gameState)delete state.serverState.gameState.activeBlackjack;await runRevealAnimation(game);finishRound(game,data);}
    }catch(error){state.busy=false;state.animation='idle';state.error=error.message||'The Blackjack decision was not completed.';render(true);}
  }

  function pokerScore(cards) {
    const order={'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,J:11,Q:12,K:13,A:14};
    const values=cards.map(c=>order[c.rank]).sort((a,b)=>a-b);
    const counts=new Map();values.forEach(v=>counts.set(v,(counts.get(v)||0)+1));
    const groups=[...counts.values()].sort((a,b)=>b-a);
    const flush=cards.every(c=>c.suit===cards[0].suit);
    let straight=values.every((v,i)=>i===0||v===values[i-1]+1);
    if(values.join(',')==='2,3,4,5,14')straight=true;
    const high=Math.max(...values);
    if(flush&&straight&&high===14)return {label:'Royal Flush',multiplier:250};
    if(flush&&straight)return {label:'Straight Flush',multiplier:50};
    if(groups[0]===4)return {label:'Four of a Kind',multiplier:25};
    if(groups[0]===3&&groups[1]===2)return {label:'Full House',multiplier:9};
    if(flush)return {label:'Flush',multiplier:6};
    if(straight)return {label:'Straight',multiplier:4};
    if(groups[0]===3)return {label:'Three of a Kind',multiplier:3};
    if(groups[0]===2&&groups[1]===2)return {label:'Two Pair',multiplier:2};
    if(groups[0]===2){const pair=[...counts].find(([,n])=>n===2)?.[0]||0;return pair>=11?{label:'Jacks or Better',multiplier:1}:{label:'Low Pair',multiplier:0};}
    return {label:'No Win',multiplier:0};
  }

  async function dealPoker() {
    const game=state.game;
    if(!ready(game)){state.error=instruction(game);render(true);return;}
    const bet=Number(state.betCents);
    if(currentBalance()<bet){state.error='Card balance is too low. Please visit the Clerk to add money.';render(true);return;}
    state.busy=true;state.error='';state.result=null;state.revealResult=false;state.animation='dealing';render(true);audio.play('cardFan');
    if(state.demoSession){
      state.demoBalanceCents-=bet;const deck=makeDeck();manual.pokerHand={gameKey:game.key,betCents:bet,cards:[deck.pop(),deck.pop(),deck.pop(),deck.pop(),deck.pop()],heldIndexes:[]};manual.pokerDeck=deck;manual.heldIndexes=[];manual.pendingGameKey=game.key;await wait(300);state.busy=false;state.animation='idle';render(true);return;
    }
    try{const data=await request('/api/player/action',{action:'POKER_DEAL',betCents:bet});updateBalance(data);await wait(300);manual.pokerHand=clone(data.hand);manual.heldIndexes=[];manual.pendingGameKey=game.key;state.serverState.gameState ||= {};state.serverState.gameState.activeJacksOrBetter=clone(data.hand);state.result=data.round?.result||null;state.animation='idle';state.busy=false;render(true);}
    catch(error){state.busy=false;state.animation='idle';state.error=error.message||'The Poker deal was not completed.';render(true);}
  }

  async function drawPoker() {
    const game=state.game,hand=pendingPoker();if(!game||game.key!=='jacks-or-better'||!hand||state.busy)return;
    state.busy=true;state.error='';state.animation='dealing';render(true);audio.play('cardFan');
    if(state.demoSession){
      await wait(260);for(let i=0;i<5;i+=1)if(!manual.heldIndexes.includes(i))hand.cards[i]=manual.pokerDeck.pop();
      const score=pokerScore(hand.cards),payout=Math.round(Number(hand.betCents||0)*score.multiplier);state.demoBalanceCents+=payout;
      state.result={gameType:'jacks-or-better',label:score.label,cards:hand.cards,winMultiplier:score.multiplier,details:{heldIndexes:[...manual.heldIndexes],mechanic:'VIDEO_POKER_HOLD_DRAW_DEMO'}};state.lastWinCents=payout;manual.pokerHand=null;manual.pokerDeck=[];manual.heldIndexes=[];manual.pendingGameKey=null;state.revealResult=true;state.busy=false;state.animation='settled';resetRound(game);render(true);announceResult();return;
    }
    try{const data=await request('/api/player/action',{action:'POKER_DRAW',heldIndexes:[...manual.heldIndexes]});updateBalance(data);manual.pokerHand=null;manual.pendingGameKey=null;if(state.serverState?.gameState)delete state.serverState.gameState.activeJacksOrBetter;await runRevealAnimation(game);finishRound(game,data);}
    catch(error){state.busy=false;state.animation='idle';state.error=error.message||'The Poker draw was not completed.';render(true);}
  }

  compactChoices = function(game) {
    if(game.key==='five-card-poker')return `<div class="mini-choices manual-decisions">${choiceButton('DEALER DUEL','fiveCardMode','dealer-duel')}${choiceButton('PAIR PLUS','fiveCardMode','pair-plus')}</div>`;
    if(game.key==='jacks-or-better')return pendingPoker()?'<div class="control-note manual-required"><strong>PLAYER DECISION REQUIRED</strong><span>Tap each card to HOLD or RELEASE, then press DRAW CARDS.</span></div>':'<div class="control-note manual-required"><strong>MANUAL DEAL · HOLD · DRAW</strong><span>No card is held or replaced until you choose.</span></div>';
    if(game.type==='blackjack')return pendingBlackjack()?'<div class="mini-choices manual-decisions"><button class="choice decision-hit" data-action="blackjack-hit">HIT</button><button class="choice decision-stand" data-action="blackjack-stand">STAND</button></div>':'<div class="control-note manual-required"><strong>MANUAL BLACKJACK</strong><span>Deal first, then choose HIT or STAND.</span></div>';
    if(game.type==='wheel')return `<div class="mini-choices manual-decisions">${['1x','2x','5x','10x'].map(v=>choiceButton(v.toUpperCase(),'moneyWheelPick',v)).join('')}</div>`;
    if(game.type==='hilo')return `<div class="mini-choices manual-decisions">${choiceButton('LOWER','hiLoPick','lower')}${choiceButton('HIGHER','hiLoPick','higher')}</div>`;
    if(game.type==='baccarat')return `<div class="mini-choices manual-decisions">${['player','tie','banker'].map(v=>choiceButton(v.toUpperCase(),'baccaratBet',v)).join('')}</div>`;
    if(game.type==='craps')return `<div class="mini-choices manual-decisions">${choiceButton('PASS LINE','crapsBet','pass')}${choiceButton("DON'T PASS",'crapsBet','dont')}</div>`;
    if(game.type==='bingo')return `<div class="mini-choices manual-decisions">${choiceButton('LINE','bingoPattern','line')}${choiceButton('CORNERS','bingoPattern','corners')}${choiceButton('X','bingoPattern','x')}</div>`;
    return original.compactChoices(game);
  };

  pokerStage = function(game) {
    const hand=game.key==='jacks-or-better'?pendingPoker():null;
    const cards=hand?.cards||state.result?.cards||[{rank:'10',suit:'hearts'},{rank:'J',suit:'spades'},{rank:'Q',suit:'hearts'},{rank:'K',suit:'clubs'},{rank:'A',suit:'diamonds'}];
    const pending=Boolean(hand);
    const markup=cards.map((card,index)=>pending?`<button class="poker-card-choice ${manual.heldIndexes.includes(index)?'held':''}" data-action="poker-hold" data-index="${index}">${playCard(card,index)}<span>${manual.heldIndexes.includes(index)?'HELD':'TAP TO HOLD'}</span></button>`:playCard(card,index)).join('');
    return `<div class="card-table poker-table ${state.animation}"><div class="table-crest"><span>${game.key==='jacks-or-better'?'JACKS OR BETTER':'FIVE CARD POKER'}</span><strong>${pending?'SELECT YOUR HOLDS':'PLAYER-CONTROLLED CARD GAME'}</strong></div><div class="poker-paytable"><span>ROYAL FLUSH <b>250×</b></span><span>STRAIGHT FLUSH <b>50×</b></span><span>FOUR OF A KIND <b>25×</b></span><span>FULL HOUSE <b>9×</b></span><span>JACKS OR BETTER <b>1×</b></span></div><div class="card-row">${markup}</div><div class="table-message">${pending?'TAP CARDS TO HOLD · PRESS DRAW WHEN READY':state.revealResult?esc(state.result?.label||'HAND COMPLETE'):'SELECT STAKE AND PLAYER DECISION'}</div></div>`;
  };

  blackjackStage = function() {
    const hand=pendingBlackjack();const player=hand?.playerCards||state.result?.playerCards||[{rank:'A',suit:'spades'},{rank:'K',suit:'hearts'}];const dealer=hand?.dealerCards||state.result?.dealerCards||[{rank:'7',suit:'spades'},null];const reveal=!hand&&state.revealResult;const pt=hand?blackjackTotal(player):state.result?.playerTotal;const dt=reveal?state.result?.dealerTotal:'';
    return `<div class="card-table blackjack-table ${state.animation}"><div class="felt-rule"><strong>PLAYER CHOOSES HIT OR STAND</strong><span>DEALER DRAWS ONLY AFTER STAND OR A FORCED RULE OUTCOME</span></div><div class="hand dealer-hand"><span>DEALER <b>${esc(dt??'')}</b></span><div class="card-row">${dealer.map((card,index)=>playCard(card,index,{hidden:!reveal&&index===1})).join('')}</div></div><div class="bet-spot">${chipMarker()}<span>${money(state.betCents,currency())}</span></div><div class="hand player-hand"><div class="card-row">${player.map((card,index)=>playCard(card,index+2)).join('')}</div><span>PLAYER <b>${esc(pt??'')}</b></span></div></div>`;
  };

  resultPanel = function(game) {
    if(game.type==='blackjack'&&pendingBlackjack())return '<div class="round-result ready manual-required"><span>BLACKJACK PLAYER TURN</span><strong>CHOOSE HIT OR STAND</strong><small>No card is dealt until you press HIT.</small></div>';
    if(game.key==='jacks-or-better'&&pendingPoker())return `<div class="round-result ready manual-required"><span>VIDEO POKER PLAYER TURN</span><strong>${manual.heldIndexes.length} CARD${manual.heldIndexes.length===1?'':'S'} HELD</strong><small>Unheld cards change only when you press DRAW CARDS.</small></div>`;
    if(!state.revealResult&&!ready(game))return `<div class="round-result ready manual-required"><span>MANUAL WAGER REQUIRED</span><strong>${esc(instruction(game))}</strong><small>No money is deducted until you press the final game button.</small></div>`;
    return original.resultPanel(game);
  };

  controlDeckMarkup = function(game) {
    const handLocked=Boolean((game.type==='blackjack'&&pendingBlackjack())||(game.key==='jacks-or-better'&&pendingPoker()));
    const bets=`<div class="bet-selector"><span>STAKE · SELECT EACH ROUND</span>${allowedBets().map(v=>`<button class="bet-chip ${manual.betSelected&&state.betCents===v?'active':''}" data-action="bet" data-value="${v}" ${state.busy||handLocked?'disabled':''}>${money(v,currency())}</button>`).join('')}</div>`;
    const choices=`<div class="choice-deck">${compactChoices(game)}</div>`;const panel=resultPanel(game);
    if(game.type==='blackjack'&&pendingBlackjack())return `${bets}${choices}${panel}<button class="play-action manual-wait" disabled><span>CHOOSE HIT OR STAND</span><i></i></button>`;
    if(game.key==='jacks-or-better'&&pendingPoker()){const count=5-manual.heldIndexes.length;return `${bets}${choices}${panel}<button class="play-action" data-action="poker-draw" ${state.busy?'disabled':''}><span>DRAW ${count} CARD${count===1?'':'S'}</span><i></i></button>`;}
    const label=game.type==='blackjack'?'DEAL BLACKJACK':game.key==='jacks-or-better'?'DEAL FIVE CARDS':actionLabel(game);
    return `${bets}${choices}${panel}<button class="play-action" data-action="play" ${ready(game)?'':'disabled'}><span>${label}</span><i></i></button>`;
  };

  app.addEventListener('click',(event)=>{
    const button=event.target.closest?.('[data-action]');if(!button)return;const action=button.dataset.action;
    if(action==='game'){
      const game=availableGames().find(item=>item.key===button.dataset.key);const active=pendingHand();const activeKey=active?.gameKey||manual.pendingGameKey;
      if(active&&game&&game.key!==activeKey){event.preventDefault();event.stopImmediatePropagation();state.error='Finish the active Blackjack or Poker hand before opening another game.';render(true);return;}
      resetRound(game,Boolean(active));queueMicrotask(()=>{hydratePending(game);render(true);});return;
    }
    if((action==='lobby'||action==='finish')&&pendingHand()){event.preventDefault();event.stopImmediatePropagation();state.error='Finish the active Blackjack or Poker hand before leaving. The wager is already open.';render(true);return;}
    if(action==='bet'){manual.betSelected=true;return;}
    if(action==='play'){event.preventDefault();event.stopImmediatePropagation();if(state.game?.type==='blackjack')void dealBlackjack();else if(state.game?.key==='jacks-or-better')void dealPoker();else void standardRound(state.game);return;}
    if(action==='blackjack-hit'||action==='blackjack-stand'){event.preventDefault();event.stopImmediatePropagation();void blackjackDecision(action==='blackjack-hit'?'BLACKJACK_HIT':'BLACKJACK_STAND');return;}
    if(action==='poker-hold'){event.preventDefault();event.stopImmediatePropagation();if(state.busy||!pendingPoker())return;const index=Number(button.dataset.index);manual.heldIndexes=manual.heldIndexes.includes(index)?manual.heldIndexes.filter(v=>v!==index):[...manual.heldIndexes,index].sort((a,b)=>a-b);manual.pokerHand.heldIndexes=[...manual.heldIndexes];audio.play('chip');render(true);return;}
    if(action==='poker-draw'){event.preventDefault();event.stopImmediatePropagation();void drawPoker();return;}
    if(action==='keno-quick'){event.preventDefault();event.stopImmediatePropagation();const pool=Array.from({length:80},(_,i)=>i+1);for(let i=pool.length-1;i>0;i-=1){const j=Math.floor(Math.random()*(i+1));[pool[i],pool[j]]=[pool[j],pool[i]];}state.kenoPicks=pool.slice(0,10).sort((a,b)=>a-b);audio.play('chip');render(true);return;}
    if(action==='keno-clear'){event.preventDefault();event.stopImmediatePropagation();state.kenoPicks=[];audio.play('chip');render(true);}
  },true);

  window.GoldSlotsManual={ready,instruction,pendingBlackjack,pendingPoker,resetRound,version:'6.9.3'};
  requestAnimationFrame(()=>render(true));
})();

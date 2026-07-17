(() => {
  'use strict';
  document.title='Gold Slots Player 6.9.3';
  document.documentElement.dataset.gsPublicRelease='6.9.3';
  let timer=null;
  function apply(){
    const w=Math.max(1,innerWidth),h=Math.max(1,innerHeight),portrait=h>w;
    let cols=portrait?2:(w<=820?3:w<=1100?4:6);
    if(portrait&&w>=1000)cols=3;
    const rows=Math.ceil(11/cols);
    const root=document.documentElement;
    root.dataset.gsOrientation=portrait?'portrait':'landscape';
    root.dataset.gsDensity=h<=700?'compact':h>=1180?'large':'standard';
    root.style.setProperty('--gs-cols',String(cols));
    root.style.setProperty('--gs-rows',String(rows));
    document.querySelectorAll('.brand span').forEach(node=>{if(/Private player terminal/i.test(node.textContent||''))node.textContent='Private player terminal · V6.9.3';});
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(apply,35)}
  addEventListener('resize',schedule,{passive:true});
  addEventListener('orientationchange',schedule,{passive:true});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)schedule()});
  new MutationObserver(apply).observe(document.getElementById('app'),{childList:true,subtree:true});
  window.GoldSlotsRelease={version:'6.9.3',apply};
  apply();
})();

/* Trilha de Fé: comportamento das telas.
 * Nenhuma página tem JavaScript embutido (nem onclick="..."): a política de conteúdo (CSP) só aceita scripts
 * deste arquivo. As telas se ligam aqui por atributos data-*:
 *   data-confirm="texto"  em <form> ou <button>: pede confirmação antes de enviar
 *   data-acao="..."       aba, fonte, slide-anterior, slide-proximo, ampliar, fechar-zoom, quiz-ir
 *   data-selecionar       seleciona o texto do campo ao tocar
 *   data-ao-terminar      em <audio>: formulário (seletor) a enviar quando o áudio acabar
 */
(function () {
  'use strict';

  // ---------- PWA ----------
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js'));
  }

  // ---------- "Mostrar senha": um botão ao lado do último campo de senha de cada formulário ----------
  document.querySelectorAll('form').forEach((form) => {
    const campos = form.querySelectorAll('input[type=password]');
    if (!campos.length) return;
    const campo = campos[campos.length - 1].closest('.campo');   // fora de um .campo (ex.: linha em flex), vai depois do formulário
    const rotulo = document.createElement('label');
    rotulo.className = 'mostrar-senha';
    const caixa = document.createElement('input');
    caixa.type = 'checkbox';
    rotulo.append(caixa, ' Mostrar senha');
    caixa.addEventListener('change', () => {
      campos.forEach((c) => { c.type = caixa.checked ? 'text' : 'password'; });
    });
    if (campo) campo.after(rotulo); else form.after(rotulo);
  });

  // ---------- Confirmação antes de ações destrutivas ----------
  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset && e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
  document.addEventListener('click', (e) => {
    const botao = e.target.closest('button[data-confirm]');
    if (botao && !window.confirm(botao.dataset.confirm)) e.preventDefault();
    const campo = e.target.closest('[data-selecionar]');
    if (campo) campo.select();
  });

  // ---------- Início: abas e pontinhos do mural ----------
  function mostrarAba(aba) {
    const colecao = document.getElementById('aba-colecao'), ultimas = document.getElementById('aba-ultimas');
    if (!colecao || !ultimas) return;
    colecao.style.display = aba === 'colecao' ? '' : 'none';
    ultimas.style.display = aba === 'ultimas' ? '' : 'none';
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('ativo', t.dataset.aba === aba));
    try { history.replaceState(null, '', aba === 'ultimas' ? '/?aba=ultimas' : '/'); } catch (e) { /* sem histórico, tudo bem */ }
  }

  const mural = document.getElementById('partilhas-trilho'), pontos = document.getElementById('partilha-pontos');
  if (mural && pontos) {
    mural.addEventListener('scroll', () => {
      const cards = [...mural.children], meio = mural.scrollLeft + mural.clientWidth / 2;
      let i = cards.findIndex((s) => s.offsetLeft - mural.offsetLeft + s.offsetWidth > meio);
      if (i < 0) i = cards.length - 1;
      [...pontos.children].forEach((p, k) => p.classList.toggle('ativo', k === i));
    }, { passive: true });
  }

  // ---------- Ler: tamanho da letra ----------
  function mudarFonte() {
    const el = document.getElementById('texto');
    if (!el) return;
    const atual = parseInt(getComputedStyle(el).fontSize, 10);
    el.style.fontSize = (atual >= 22 ? 18 : atual + 2) + 'px';
  }

  // ---------- Slides: carrossel e ampliação ----------
  const trilho = document.getElementById('slides-trilho');
  let alvo = 1, parou, total = 0, base = '';
  const contador = document.getElementById('slides-contador');
  const zoom = document.getElementById('slide-zoom');

  function slideAtual() { return Math.min(total, Math.max(1, Math.round(trilho.scrollLeft / trilho.clientWidth) + 1)); }
  // "alvo" guarda para onde estamos indo: toques rápidos somam, em vez de ler a posição no meio da animação.
  function irSlide(delta) {
    alvo = Math.min(total, Math.max(1, alvo + delta));
    trilho.scrollTo({ left: (alvo - 1) * trilho.clientWidth, behavior: 'smooth' });
  }
  function ampliar(n) {
    document.getElementById('slide-zoom-img').src = base + n;
    zoom.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function fecharZoom() {
    if (!zoom) return;
    zoom.hidden = true;
    document.body.style.overflow = '';
  }
  if (trilho) {
    total = parseInt(trilho.dataset.total, 10);
    base = trilho.dataset.base;
    trilho.addEventListener('scroll', () => {
      if (contador) contador.textContent = slideAtual() + ' de ' + total;
      clearTimeout(parou); parou = setTimeout(() => { alvo = slideAtual(); }, 150);   // depois de deslizar com o dedo
    }, { passive: true });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') fecharZoom();
      if (e.key === 'ArrowRight') irSlide(1);
      if (e.key === 'ArrowLeft') irSlide(-1);
    });
  }

  // ---------- Ouvir: ao terminar o áudio, marca o passo e segue (o botão continua como reserva) ----------
  document.querySelectorAll('audio[data-ao-terminar]').forEach((audio) => {
    audio.addEventListener('ended', () => {
      const form = document.querySelector(audio.dataset.aoTerminar);
      if (form) form.submit();
    });
  });

  // ---------- Quiz: uma pergunta por vez ----------
  function irPara(id) {
    document.querySelectorAll('.main > div[id]').forEach((d) => { d.style.display = 'none'; });
    const pergunta = document.getElementById(id);
    if (pergunta) pergunta.style.display = 'flex';
  }
  if (document.querySelector('.main > div[id]') && location.hash) {
    irPara(location.hash.replace('#', '').replace('-resposta', ''));
  }

  // ---------- Ações por data-acao ----------
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-acao]');
    if (!el) return;
    switch (el.dataset.acao) {
      case 'aba': mostrarAba(el.dataset.aba); break;
      case 'fonte': mudarFonte(); break;
      case 'slide-anterior': irSlide(-1); break;
      case 'slide-proximo': irSlide(1); break;
      case 'ampliar': ampliar(parseInt(el.dataset.n, 10)); break;
      case 'fechar-zoom': fecharZoom(); break;
      case 'quiz-ir': irPara(el.dataset.id); break;
      default: break;
    }
  });
})();

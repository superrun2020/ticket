/* Mobile presentation only: never fetch, replace editors, or mutate send state. */
function createMobileShell(doc, media) {
  const q = selector => doc.querySelector(selector);
  const sidebar = q('.sidebar'), main = q('main'), menu = q('#mobileMenu');
  let opened = false;
  function close(restore = true) {
    opened = false;
    sidebar.classList.remove('drawer-open');
    sidebar.inert = media.matches;
    sidebar.removeAttribute('role');
    sidebar.removeAttribute('aria-modal');
    main.inert = false;
    q('#mobileBar').inert = false;
    q('#mobileBackdrop').hidden = true;
    menu.setAttribute('aria-expanded', 'false');
    if (restore && media.matches) menu.focus();
  }
  function open() {
    if (!media.matches) return;
    opened = true;
    sidebar.inert = false;
    sidebar.classList.add('drawer-open');
    sidebar.setAttribute('role', 'dialog');
    sidebar.setAttribute('aria-modal', 'true');
    main.inert = true;
    q('#mobileBar').inert = true;
    q('#mobileBackdrop').hidden = false;
    menu.setAttribute('aria-expanded', 'true');
    q('#mobileClose').focus();
  }
  function keydown(event) {
    if (!opened) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const controls = [...sidebar.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]')]
      .filter(node => !node.hidden && node.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  menu.addEventListener('click', open);
  q('#mobileClose').addEventListener('click', () => close());
  q('#mobileBackdrop').addEventListener('click', () => close());
  doc.addEventListener('keydown', keydown);
  const detail = q('#detail');
  function sync() {
    const showing = detail.classList.contains('mobile-open');
    const hasEditor = !!detail.querySelector('#replyText, #composePageForm');
    q('#mobileBack').hidden = !showing;
    q('#mobileResume').hidden = showing || !hasEditor;
    detail.inert = media.matches && !showing;
    q('#ticketList').inert = media.matches && showing;
    main.classList.toggle('mobile-detail-visible', media.matches && showing);
  }
  function back() {
    detail.classList.remove('mobile-open');
    sync();
    q('#mobileResume').focus();
  }
  function resume() {
    detail.classList.add('mobile-open');
    sync();
    q('#mobileBack').focus();
  }
  q('#mobileBack').addEventListener('click', back);
  q('#mobileResume').addEventListener('click', resume);
  media.addEventListener('change', () => { close(false); sync(); });
  close(false);
  sync();
  function select(event, editorTicketId) {
    if (!media.matches) return;
    const card = event.target.closest('.ticket-card');
    const compose = event.target.closest('#composeButton');
    const returnToReply = card && card.dataset.id === editorTicketId && detail.querySelector('#replyText');
    const returnToCompose = compose && detail.querySelector('#composePageForm');
    if (returnToReply || returnToCompose) {
      event.preventDefault();
      event.stopImmediatePropagation();
      close(false);
      resume();
      return true;
    }
    return false;
  }
  return {open, close, keydown, sync, back, resume, select};
}
if (typeof module !== 'undefined') module.exports = {createMobileShell};
if (typeof window !== 'undefined') {
  const mobileMedia = window.matchMedia('(max-width: 850px)');
  const mobileShell = createMobileShell(document, mobileMedia);
  // Observe only presentation changes, not editor input or send completion state.
  new MutationObserver(() => mobileShell.sync()).observe(document.querySelector('#detail'), {attributes:true, attributeFilter:['class'], childList:true});
  document.addEventListener('click', event => {
    if (mobileShell.select(event, replyComposerState.ticketId === state.selected ? state.selected : null)) return;
    if (mobileMedia.matches && event.target.closest('.sidebar button:not(#mobileClose)')) {
      // These filters load the list without replacing the retained editor.
      // Leave their handlers and requests alone; only change the visible pane.
      if (event.target.closest('.sidebar [data-status], .sidebar [data-mailbox], .sidebar [data-tag]')) mobileShell.back();
      mobileShell.close();
    }
    if (mobileMedia.matches && event.target.closest('#cancelCompose')) {
      event.preventDefault(); event.stopImmediatePropagation(); mobileShell.back();
    }
  }, true);
  document.querySelector('#workspaceSwitcher').addEventListener('change', () => {
    if (mobileMedia.matches) mobileShell.back();
    mobileShell.close();
  });
}

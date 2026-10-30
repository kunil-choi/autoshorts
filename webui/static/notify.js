// Shared "new private short is waiting for review" notifier, included on
// every page except the review screen. Polls /library/items and pops a
// toast (plus a browser notification if the user allowed one) whenever a
// private item shows up that this browser hasn't been told about yet.
// Only works while a tab of the app is open - there is no server push.
(function () {
  const POLL_MS = 20000;
  const KEY = 'autoshorts_notified_ids';
  const onLibrary = location.pathname.startsWith('/library');
  const baseTitle = document.title.replace(/^\(\d+\)\s*/, '');

  function loadSeen() {
    try { return new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }
    catch (e) { return new Set(); }
  }
  function saveSeen(set) {
    try { localStorage.setItem(KEY, JSON.stringify([...set])); } catch (e) { /* ignore */ }
  }

  const style = document.createElement('style');
  style.textContent = `
    #as-toast { position: fixed; right: 16px; bottom: 16px; z-index: 9999; background: #1f2430;
      color: #fff; padding: 14px 16px; border-radius: 10px; box-shadow: 0 4px 16px #0005;
      font-size: 14px; line-height: 1.5; max-width: 320px; display: none; }
    #as-toast a { color: #c4b5fd; font-weight: 700; }
    #as-toast .close { float: right; cursor: pointer; margin-left: 10px; opacity: .7; }
    .as-badge { background: #e11d48; color: #fff; border-radius: 10px; padding: 1px 7px;
      font-size: 12px; margin-left: 6px; font-weight: 700; }
  `;
  document.head.appendChild(style);

  const toast = document.createElement('div');
  toast.id = 'as-toast';
  document.body.appendChild(toast);

  function showToast(count, labels) {
    const from = labels.length ? ` (${labels.join(', ')})` : '';
    toast.innerHTML = `<span class="close" title="닫기">✕</span>
      🔔 새 비공개 쇼츠 <b>${count}개</b>가 만들어졌어요${from}.<br>
      <a href="/library">검토하러 가기 →</a>
      ${('Notification' in window && Notification.permission === 'default')
        ? '<br><a href="#" id="as-allow">바탕화면 알림도 받기</a>' : ''}`;
    toast.style.display = 'block';
    toast.querySelector('.close').onclick = () => { toast.style.display = 'none'; };
    const allow = toast.querySelector('#as-allow');
    if (allow) allow.onclick = (e) => { e.preventDefault(); Notification.requestPermission(); allow.remove(); };
    if ('Notification' in window && Notification.permission === 'granted') {
      try { new Notification('autoshorts', { body: `새 비공개 쇼츠 ${count}개가 검토를 기다려요.` }); } catch (e) { /* ignore */ }
    }
  }

  function decorateLinks(pendingCount) {
    document.querySelectorAll('a[href="/library"]').forEach((a) => {
      let b = a.querySelector('.as-badge');
      if (!pendingCount) { if (b) b.remove(); return; }
      if (!b) { b = document.createElement('span'); b.className = 'as-badge'; a.appendChild(b); }
      b.textContent = pendingCount;
    });
    document.title = pendingCount ? `(${pendingCount}) ${baseTitle}` : baseTitle;
  }

  async function check() {
    try {
      const res = await fetch('/library/items');
      if (!res.ok) return;
      const pending = ((await res.json()).items || []).filter((it) => it.status === 'private');
      decorateLinks(pending.length);
      const seen = loadSeen();
      const fresh = pending.filter((it) => !seen.has(it.id));
      if (!fresh.length) return;
      if (!onLibrary) {
        showToast(fresh.length, [...new Set(fresh.map((it) => it.source_label).filter(Boolean))]);
      }
      fresh.forEach((it) => seen.add(it.id));
      saveSeen(seen);
    } catch (e) { /* server briefly unreachable - try again next tick */ }
  }

  check();
  setInterval(check, POLL_MS);
})();

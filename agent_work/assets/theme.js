(() => {
  const key = 'tack-theme';
  const root = document.documentElement;
  let saved;
  try {
    saved = window.localStorage.getItem(key);
  } catch {}
  root.dataset.theme = saved === 'classic' ? 'classic' : 'paper';

  window.tackTheme = {
    set(theme) {
      if (theme !== 'paper' && theme !== 'classic') return;
      root.dataset.theme = theme;
      try {
        window.localStorage.setItem(key, theme);
      } catch {}
    },
  };
})();

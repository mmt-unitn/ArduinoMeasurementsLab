(() => {
  const W = 1024, H = 768;
  const revealEl = document.querySelector('.reveal');
  const slidesEl = document.querySelector('.reveal .slides');
  if (!slidesEl) return JSON.stringify({error: 'no .slides'});
  // Reproduce the geometry Reveal.js would impose: the deck is a W x H box,
  // and the viewport scale it then applies is a pure transform that does not
  // change layout, so measuring inside this box measures what the audience
  // sees. Reveal's own JS never completes here (its math plugin throws in
  // this engine), so the box is imposed by hand.
  revealEl.style.width = W + 'px';
  revealEl.style.height = H + 'px';
  slidesEl.style.width = W + 'px';
  slidesEl.style.height = H + 'px';
  slidesEl.style.position = 'relative';
  slidesEl.style.left = '0px'; slidesEl.style.top = '0px';
  slidesEl.style.transform = 'none';

  const leaves = Array.from(slidesEl.querySelectorAll('section'))
    .filter(s => s.querySelectorAll('section').length === 0);
  const out = [];
  leaves.forEach(s => {
    const parentStack = s.parentElement.tagName.toLowerCase() === 'section'
      ? s.parentElement : null;
    const restore = [];
    const show = el => {
      restore.push([el, el.style.display, el.style.position, el.style.visibility,
                    el.style.height, el.style.top]);
      el.style.display = 'block'; el.style.position = 'relative';
      el.style.visibility = 'visible'; el.style.height = 'auto';
      el.style.top = '0px';
    };
    if (parentStack) show(parentStack);
    show(s);
    void s.offsetHeight;                       // force layout
    const head = s.querySelector('h1,h2,h3,h4');
    out.push({
      title: head ? head.textContent.trim() : '(no heading)',
      id: s.id || '',
      h: Math.round(s.scrollHeight)
    });
    restore.forEach(([el, d, p, v, hh, t]) => {
      el.style.display = d; el.style.position = p; el.style.visibility = v;
      el.style.height = hh; el.style.top = t;
    });
  });
  return JSON.stringify({W, H, n: out.length, slides: out});
})()

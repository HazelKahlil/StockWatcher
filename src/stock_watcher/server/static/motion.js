// Short, interruptible motion. Values and request results are never delayed by animation.
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const running = new WeakMap();
const disclosures = new WeakSet();
const dialogs = new WeakMap();
const easing = 'cubic-bezier(.2,.75,.25,1)';
export const motionEnabled = () => !reducedMotion.matches;

export function enter(element, distance = 4) {
  if (!element || !motionEnabled() || !element.animate) return;
  running.get(element)?.cancel();
  const animation = element.animate([
    {opacity: .35, transform: `translateY(${distance}px)`},
    {opacity: 1, transform: 'none'},
  ], {duration: 180, easing});
  running.set(element, animation);
  animation.finished.catch(() => {});
}

export function openDrawer(dialog) {
  let state = dialogs.get(dialog);
  if (!state) {
    state = {animation:null, revision:0};
    dialogs.set(dialog, state);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDrawer(dialog); });
  }
  state.revision += 1;
  state.closing = false;
  state.animation?.cancel();
  if (!dialog.open) dialog.showModal();
  const panel = dialog.querySelector('.drawer-content');
  if (motionEnabled() && panel?.animate) {
    state.animation = panel.animate([
      {opacity:0, transform:'translateX(28px)'}, {opacity:1, transform:'none'},
    ], {duration:200, easing});
    state.animation.finished.catch(() => {});
  }
}

export function closeDrawer(dialog) {
  const state = dialogs.get(dialog);
  if (!dialog.open || !state || state.closing) return;
  dialog.dispatchEvent(new Event('dismissstart'));
  const revision = ++state.revision;
  const panel = dialog.querySelector('.drawer-content');
  const visual = panel ? getComputedStyle(panel) : null;
  const first = visual ? {opacity:visual.opacity, transform:visual.transform} : {};
  state.animation?.cancel();
  if (!motionEnabled() || !panel?.animate) { dialog.close(); return; }
  state.closing = true;
  state.animation = panel.animate([first, {opacity:0,transform:'translateX(20px)'}], {duration:140,easing});
  state.animation.finished.catch(() => {}).then(() => {
    if (state.revision !== revision) return;
    state.closing = false;
    if (dialog.open) dialog.close();
  });
}

export function enhanceDetails(root = document) {
  root.querySelectorAll('details').forEach(details => {
    if (disclosures.has(details)) return;
    const summary = details.querySelector(':scope > summary');
    if (!summary) return;
    disclosures.add(details);
    const clip = document.createElement('div');
    clip.className = 'disclosure-clip';
    const body = document.createElement('div');
    body.className = 'disclosure-body';
    for (const child of [...details.childNodes]) if (child !== summary) body.append(child);
    clip.append(body);
    details.append(clip);
    let expanded = details.open;
    let animation = null;
    let revision = 0;
    let targetHeight = -1;
    const settle = () => {
      details.open = expanded;
      clip.style.height = '';
      clip.style.overflow = '';
      animation = null;
      observer.disconnect();
    };
    const resize = () => {
      const height = expanded ? body.getBoundingClientRect().height : 0;
      if (targetHeight === height && animation) return;
      targetHeight = height;
      const start = clip.getBoundingClientRect().height;
      animation?.cancel();
      const version = ++revision;
      if (!motionEnabled() || !clip.animate || Math.abs(start-height) < 1) { settle(); return; }
      observer.observe(body);
      clip.style.overflow = 'hidden';
      clip.style.height = `${start}px`;
      animation = clip.animate([{height:`${start}px`}, {height:`${height}px`}], {duration:180,easing,fill:'forwards'});
      animation.finished.then(() => {
        if (version !== revision) return;
        const completed = animation;
        settle();
        completed?.cancel();
      }).catch(() => {});
    };
    summary.addEventListener('click', event => {
      event.preventDefault();
      expanded = !expanded;
      clip.inert = !expanded;
      summary.setAttribute('aria-expanded', String(expanded));
      if (expanded && !details.open) { clip.style.height = '0px'; details.open = true; }
      if (!expanded && clip.contains(document.activeElement)) summary.focus({preventScroll:true});
      resize();
    });
    // Async report text may arrive while the disclosure is opening.
    const observer = new ResizeObserver(() => { if (expanded && animation) resize(); });
    details.addEventListener('toggle', () => {
      if (!animation && details.open !== expanded) {
        expanded = details.open;
        clip.inert = !expanded;
      }
      summary.setAttribute('aria-expanded', String(expanded));
    });
    summary.setAttribute('aria-expanded', String(expanded));
    clip.inert = !expanded;
  });
}

// Patch a stable row without replacing focused controls or flashing unchanged values.
export function patchElement(target, source) {
  for (const attribute of [...target.attributes]) if (!source.hasAttribute(attribute.name)) target.removeAttribute(attribute.name);
  for (const attribute of source.attributes) if (target.getAttribute(attribute.name) !== attribute.value) target.setAttribute(attribute.name, attribute.value);
  const children = [...source.childNodes];
  children.forEach((child, index) => {
    const current = target.childNodes[index];
    if (!current) { target.append(child.cloneNode(true)); return; }
    if (current.nodeType !== child.nodeType || current.nodeName !== child.nodeName) { current.replaceWith(child.cloneNode(true)); return; }
    if (child.nodeType === Node.TEXT_NODE) {
      if (current.nodeValue !== child.nodeValue) current.nodeValue = child.nodeValue;
    } else if (child.nodeType === Node.ELEMENT_NODE) patchElement(current, child);
  });
  while (target.childNodes.length > children.length) target.lastChild.remove();
}

document.addEventListener('DOMContentLoaded', () => enhanceDetails());

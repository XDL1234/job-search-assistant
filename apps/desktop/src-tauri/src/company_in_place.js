(async function (expectedUrl) {
  if (!this.isConnected || this.ownerDocument !== document || location.href !== expectedUrl) return {ok:false};
  const original = Object.getOwnPropertyDescriptor(window, 'open');
  const destinations = [];
  const capture = value => {
    try {
      destinations.push(value ? new URL(String(value), document.baseURI).href : '');
    } catch {
      destinations.push('');
    }
    return null;
  };
  const interceptLink = event => {
    const link = event.target?.closest('a[href][target]');
    if (link && !['_self', '_top', '_parent', ''].includes(link.target.toLowerCase())) {
      event.preventDefault();
      capture(link.href);
    }
  };
  try {
    Object.defineProperty(window, 'open', {configurable:true, writable:true, value:capture});
    document.addEventListener('click', interceptLink, true);
    this.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    this.click();
    await new Promise(resolve => setTimeout(resolve, 180));
    return {ok:true, popup:destinations.length > 1 ? false : destinations[0] ?? null};
  } finally {
    document.removeEventListener('click', interceptLink, true);
    if (window.open === capture) {
      if (original) Object.defineProperty(window, 'open', original);
      else delete window.open;
    }
  }
})

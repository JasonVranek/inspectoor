// URL/hash parameter parsing -- standalone to avoid circular deps
export function parseParams(hash) {
  const q = hash.indexOf('?');
  if (q < 0) return {};
  const params = {};
  hash.substring(q+1).split('&').forEach(p => {
    const [k,v] = p.split('=');
    if (k && v) params[decodeURIComponent(k)] = decodeURIComponent(v);
  });
  return params;
}

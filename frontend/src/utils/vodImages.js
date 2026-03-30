const normalizeImageCandidate = (candidate) => {
  if (!candidate) {
    return null;
  }

  if (typeof candidate === 'string') {
    return candidate || null;
  }

  if (typeof candidate === 'object') {
    return candidate.cache_url || candidate.url || null;
  }

  return null;
};

export const getVODImageSrc = (...candidates) => {
  for (const candidate of candidates) {
    const normalized = normalizeImageCandidate(candidate);
    if (normalized) {
      return normalized;
    }
  }

  return null;
};

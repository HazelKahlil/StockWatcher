// Pure state helpers shared by the browser controller and dependency-free Node tests.
export function approvalKey(tradeDate, code) {
  return `${tradeDate}|${code}`;
}

export function acceptsState(known, incoming) {
  return Boolean(incoming && Number.isSafeInteger(incoming.version)
    && incoming.version >= 0 && typeof incoming.selected === 'boolean'
    && (!known || incoming.version >= known.version));
}

export function requestBody(snapshotId, selected, expectedVersion, requestId) {
  if (!Number.isSafeInteger(snapshotId) || snapshotId <= 0
      || typeof selected !== 'boolean'
      || !Number.isSafeInteger(expectedVersion) || expectedVersion < 0
      || !/^[A-Za-z0-9_-]{16,96}$/.test(requestId)) {
    throw new TypeError('Invalid approval request');
  }
  return {
    snapshot_id: snapshotId, selected, expected_version: expectedVersion,
    request_id: requestId, surface: 'dashboard',
  };
}

export function sameSnapshot(response, snapshotId, userId) {
  return response?.snapshot_id === snapshotId && String(response?.user_id) === String(userId);
}

export function shouldReadApprovalState(
  shownSnapshotId,
  loadedSnapshotId,
  loadInFlight,
  failedSnapshotId = 0,
) {
  if (!Number.isSafeInteger(shownSnapshotId) || shownSnapshotId <= 0 || loadInFlight) {
    return false;
  }
  if (shownSnapshotId === loadedSnapshotId) return false;
  if (failedSnapshotId === shownSnapshotId) return false;
  return true;
}

export function isCurrentApprovalLoad(myEpoch, loadEpoch, requestedId, shownSnapshotId) {
  return myEpoch === loadEpoch && requestedId === shownSnapshotId;
}

export function isStaleApprovalAbort(error, timedOut) {
  return error?.name === 'AbortError' && !timedOut;
}

export function shouldRetryApprovalLoad(
  shownSnapshotId,
  scheduledFor,
  failedSnapshotId,
  loadInFlight,
  disposed,
  loadAttempts,
  maxAttempts = 3,
) {
  if (disposed || loadInFlight || loadAttempts >= maxAttempts) return false;
  if (shownSnapshotId !== scheduledFor) return false;
  return failedSnapshotId === scheduledFor;
}

export function secureRequestId(cryptoProvider = globalThis.crypto) {
  if (typeof cryptoProvider?.randomUUID === 'function') return cryptoProvider.randomUUID();
  // getRandomValues is still cryptographically secure where randomUUID is absent.
  const bytes = new Uint8Array(16);
  cryptoProvider.getRandomValues(bytes);
  return [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

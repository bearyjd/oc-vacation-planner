// VPlan Chase Travel — Background Service Worker (MV3)
'use strict';

const TAG = '[vplan-bg]';
const STORAGE_KEY = 'captures';

// ─── Receive captures from content script ───────────────────────────────
chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
  if (message.type === 'API_CAPTURE') {
    addCapture(message.payload);
    sendResponse({ ok: true });
  } else if (message.type === 'GET_CAPTURES') {
    getCaptures().then(function (captures) {
      sendResponse({ captures: captures });
    });
    return true; // keep channel open for async response
  } else if (message.type === 'CLEAR_CAPTURES') {
    clearCaptures().then(function () {
      sendResponse({ ok: true });
    });
    return true;
  } else if (message.type === 'DOWNLOAD_CAPTURES') {
    downloadCaptures().then(function (result) {
      sendResponse(result);
    });
    return true;
  }
});

async function addCapture(entry) {
  try {
    const result = await chrome.storage.local.get(STORAGE_KEY);
    const captures = result[STORAGE_KEY] || [];
    captures.push(entry);
    await chrome.storage.local.set({ [STORAGE_KEY]: captures });

    // Update badge with count
    const count = captures.length;
    chrome.action.setBadgeText({ text: count > 0 ? String(count) : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#1A3A6B' });

    console.log(TAG, 'Stored capture #' + count, entry.url);
  } catch (err) {
    console.error(TAG, 'Failed to store capture:', err);
  }
}

async function getCaptures() {
  try {
    const result = await chrome.storage.local.get(STORAGE_KEY);
    return result[STORAGE_KEY] || [];
  } catch (err) {
    console.error(TAG, 'Failed to get captures:', err);
    return [];
  }
}

async function clearCaptures() {
  try {
    await chrome.storage.local.remove(STORAGE_KEY);
    chrome.action.setBadgeText({ text: '' });
    console.log(TAG, 'Cleared all captures');
  } catch (err) {
    console.error(TAG, 'Failed to clear captures:', err);
  }
}

async function downloadCaptures() {
  try {
    const captures = await getCaptures();
    if (captures.length === 0) {
      return { ok: false, error: 'No captures to download' };
    }

    // Build the export format: array of {url, status, timestamp, data}
    const exportData = captures.map(function (c) {
      return {
        url: c.url,
        status: c.status,
        timestamp: c.timestamp,
        data: c.data,
      };
    });

    const blob = new Blob([JSON.stringify(exportData, null, 2)], {
      type: 'application/json',
    });

    // Convert blob to data URL for download
    const reader = new FileReader();
    const dataUrl = await new Promise(function (resolve, reject) {
      reader.onload = function () {
        resolve(reader.result);
      };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });

    await chrome.downloads.download({
      url: dataUrl,
      filename: 'chase_capture.json',
      saveAs: false,
    });

    console.log(TAG, 'Downloaded', captures.length, 'captures as chase_capture.json');
    return { ok: true, count: captures.length };
  } catch (err) {
    console.error(TAG, 'Download failed:', err);
    return { ok: false, error: err.message };
  }
}

// ─── Initialize badge on install/startup ────────────────────────────────
chrome.runtime.onInstalled.addListener(async function () {
  const captures = await getCaptures();
  const count = captures.length;
  chrome.action.setBadgeText({ text: count > 0 ? String(count) : '' });
  chrome.action.setBadgeBackgroundColor({ color: '#1A3A6B' });
  console.log(TAG, 'Extension installed. Existing captures:', count);
});

chrome.runtime.onStartup.addListener(async function () {
  const captures = await getCaptures();
  const count = captures.length;
  chrome.action.setBadgeText({ text: count > 0 ? String(count) : '' });
  chrome.action.setBadgeBackgroundColor({ color: '#1A3A6B' });
});

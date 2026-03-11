// VPlan Chase Capture — Popup UI
'use strict';

const countEl = document.getElementById('count');
const downloadBtn = document.getElementById('downloadBtn');
const clearBtn = document.getElementById('clearBtn');
const statusEl = document.getElementById('status');

function setStatus(msg) {
  statusEl.textContent = msg;
  setTimeout(function () {
    if (statusEl.textContent === msg) statusEl.textContent = '';
  }, 3000);
}

function updateCount() {
  chrome.runtime.sendMessage({ type: 'GET_CAPTURES' }, function (response) {
    if (chrome.runtime.lastError) {
      countEl.textContent = '?';
      return;
    }
    const n = response && response.captures ? response.captures.length : 0;
    countEl.textContent = String(n);
    downloadBtn.disabled = n === 0;
  });
}

downloadBtn.addEventListener('click', function () {
  downloadBtn.disabled = true;
  setStatus('Downloading...');
  chrome.runtime.sendMessage({ type: 'DOWNLOAD_CAPTURES' }, function (response) {
    if (chrome.runtime.lastError) {
      setStatus('Error: ' + chrome.runtime.lastError.message);
      downloadBtn.disabled = false;
      return;
    }
    if (response && response.ok) {
      setStatus('Saved chase_capture.json (' + response.count + ' entries)');
    } else {
      setStatus(response ? response.error : 'Download failed');
    }
    downloadBtn.disabled = false;
  });
});

clearBtn.addEventListener('click', function () {
  if (!confirm('Clear all captured responses?')) return;
  chrome.runtime.sendMessage({ type: 'CLEAR_CAPTURES' }, function () {
    countEl.textContent = '0';
    downloadBtn.disabled = true;
    setStatus('Cleared');
  });
});

// Load count on open
updateCount();

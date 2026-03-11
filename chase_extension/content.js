// VPlan Chase Travel API Response Capture
// Injected at document_start to intercept before page JS runs
(function () {
  'use strict';

  const TAG = '[vplan]';

  // URL patterns that indicate API responses we want
  const API_PATTERNS = [
    '/api/',
    '/search/',
    '/flight/',
    '/hotel/',
    '/offer/',
    '/availability/',
    '/pricing/',
    'cxloyalty',
  ];

  function isApiUrl(url) {
    const lower = url.toLowerCase();
    return API_PATTERNS.some((p) => lower.includes(p));
  }

  function isJsonContentType(contentType) {
    if (!contentType) return false;
    return contentType.includes('application/json') || contentType.includes('+json');
  }

  function sendCapture(entry) {
    try {
      chrome.runtime.sendMessage(
        { type: 'API_CAPTURE', payload: entry },
        function (_response) {
          if (chrome.runtime.lastError) {
            // Extension context may have been invalidated — ignore silently
          }
        }
      );
    } catch (_e) {
      // Extension unloaded or context destroyed
    }
  }

  // ─── Monkey-patch window.fetch ────────────────────────────────────────
  const originalFetch = window.fetch;

  window.fetch = async function (...args) {
    const request = args[0];
    let url = '';

    if (typeof request === 'string') {
      url = request;
    } else if (request instanceof Request) {
      url = request.url;
    } else if (request && request.toString) {
      url = request.toString();
    }

    const response = await originalFetch.apply(this, args);

    // Clone immediately so we don't consume the body
    const clone = response.clone();
    const contentType = clone.headers.get('content-type') || '';

    if (isJsonContentType(contentType) || isApiUrl(url)) {
      clone
        .text()
        .then(function (text) {
          let data;
          try {
            data = JSON.parse(text);
          } catch (_e) {
            // Not valid JSON — skip
            return;
          }

          const entry = {
            url: url,
            status: clone.status,
            timestamp: new Date().toISOString(),
            method: 'fetch',
            data: data,
          };

          console.log(TAG, 'Captured API response from', url, '(fetch, status', clone.status + ')');
          sendCapture(entry);
        })
        .catch(function (_err) {
          // Body read failed — ignore
        });
    }

    return response;
  };

  // ─── Monkey-patch XMLHttpRequest ──────────────────────────────────────
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this._vplanMethod = method;
    this._vplanUrl = typeof url === 'string' ? url : String(url);
    return originalXHROpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    const xhr = this;
    const url = xhr._vplanUrl || '';

    xhr.addEventListener('load', function () {
      try {
        const contentType = xhr.getResponseHeader('content-type') || '';
        if (!isJsonContentType(contentType) && !isApiUrl(url)) return;

        const text = xhr.responseText;
        if (!text) return;

        let data;
        try {
          data = JSON.parse(text);
        } catch (_e) {
          return;
        }

        const entry = {
          url: url,
          status: xhr.status,
          timestamp: new Date().toISOString(),
          method: 'xhr',
          data: data,
        };

        console.log(TAG, 'Captured API response from', url, '(XHR, status', xhr.status + ')');
        sendCapture(entry);
      } catch (_e) {
        // Ignore errors in capture logic
      }
    });

    return originalXHRSend.apply(this, arguments);
  };

  console.log(TAG, 'Chase Travel API capture active on', location.hostname);
})();

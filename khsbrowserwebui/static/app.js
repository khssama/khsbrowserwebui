// KHS Browser WebUI client: JPEG frames in, input events out.
(function () {
  "use strict";

  const canvas = document.getElementById("screen");
  const ctx = canvas.getContext("2d", { alpha: false });
  const input = document.getElementById("input");
  const stage = document.getElementById("stage");
  const statusEl = document.getElementById("status");
  const statusText = document.getElementById("status-text");
  const metaEl = document.getElementById("meta");
  const waiting = document.getElementById("waiting");
  const waitingText = document.getElementById("waiting-text");
  const toastEl = document.getElementById("toast");
  const helpModal = document.getElementById("help");

  // Works both at "/" and behind a path prefix such as /proxy/7071/.
  const base = location.pathname.endsWith("/")
    ? location.pathname
    : location.pathname + "/";
  const wsUrl =
    (location.protocol === "https:" ? "wss" : "ws") +
    "://" +
    location.host +
    base +
    "ws";

  let ws = null;
  let attempts = 0;
  let reconnectTimer = 0;
  let gotFrame = false;
  let decoding = false;
  let lastFrameAt = 0;
  let frames = 0;
  let framesThisSecond = 0;
  let measuredFps = 0;
  let lastStats = performance.now();

  // ---------------------------------------------------------------- status
  function setStatus(state, label) {
    statusEl.className = "status " + state;
    statusText.textContent = label;
  }

  function showToast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("visible");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => toastEl.classList.remove("visible"), 1800);
  }

  // ------------------------------------------------------------ connection
  function connect() {
    clearTimeout(reconnectTimer);
    setStatus("connecting", "connecting…");

    try {
      ws = new WebSocket(wsUrl);
    } catch (err) {
      scheduleReconnect();
      return;
    }

    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      attempts = 0;
      setStatus("connecting", "waiting for video…");
      waitingText.textContent = "Connected. Waiting for the first frame…";
    };

    ws.onmessage = (event) => {
      if (typeof event.data === "string") return;
      drawFrame(event.data);
    };

    ws.onclose = () => {
      setStatus("offline", "reconnecting…");
      scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose always follows; nothing to do here.
    };
  }

  function scheduleReconnect() {
    attempts += 1;
    const delay = Math.min(8000, 400 * Math.pow(1.6, Math.min(attempts, 8)));
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, delay);
  }

  function send(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  // --------------------------------------------------------------- frames
  function drawFrame(data) {
    if (decoding) return; // keep only the freshest frame
    decoding = true;

    const blob = data instanceof Blob ? data : new Blob([data], { type: "image/jpeg" });

    createImageBitmap(blob)
      .then((bitmap) => {
        decoding = false;

        if (
          canvas.width !== bitmap.width ||
          canvas.height !== bitmap.height
        ) {
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
        }

        ctx.drawImage(bitmap, 0, 0);
        if (bitmap.close) bitmap.close();

        frames += 1;
        framesThisSecond += 1;
        lastFrameAt = performance.now();

        if (!gotFrame) {
          gotFrame = true;
          waiting.classList.add("hidden");
          setStatus("live", "live");
        }
      })
      .catch(() => {
        decoding = false;
      });
  }

  function tickStats() {
    const now = performance.now();
    if (now - lastStats >= 1000) {
      measuredFps = Math.round((framesThisSecond * 1000) / (now - lastStats));
      framesThisSecond = 0;
      lastStats = now;

      const size = canvas.width && canvas.height ? `${canvas.width}×${canvas.height}` : "—";
      metaEl.textContent = `${size} · ${measuredFps || 0} fps`;

      if (gotFrame && now - lastFrameAt > 3000) {
        setStatus("stalled", "stalled");
      } else if (gotFrame && now - lastFrameAt <= 3000) {
        setStatus("live", "live");
      }
    }
    requestAnimationFrame(tickStats);
  }

  // ---------------------------------------------------------- coordinates
  function toRemote(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const iw = canvas.width;
    const ih = canvas.height;
    if (!iw || !ih) return null;

    // The canvas uses object-fit: contain, so letterbox the math the same way.
    const scale = Math.min(rect.width / iw, rect.height / ih);
    const drawnW = iw * scale;
    const drawnH = ih * scale;
    const offX = rect.left + (rect.width - drawnW) / 2;
    const offY = rect.top + (rect.height - drawnH) / 2;

    const x = (clientX - offX) / scale;
    const y = (clientY - offY) / scale;
    if (x < 0 || y < 0 || x >= iw || y >= ih) return null;

    return { x: Math.round(x), y: Math.round(y) };
  }

  // ------------------------------------------------------------- pointer
  let pendingMove = null;
  let moveRaf = 0;

  function queueMove(event) {
    const point = toRemote(event.clientX, event.clientY);
    if (!point) return;
    pendingMove = point;
    if (!moveRaf) moveRaf = requestAnimationFrame(flushMove);
  }

  function flushMove() {
    moveRaf = 0;
    if (!pendingMove) return;
    send({ t: "move", x: pendingMove.x, y: pendingMove.y });
    pendingMove = null;
  }

  input.addEventListener("pointermove", queueMove);

  input.addEventListener("pointerdown", (event) => {
    input.focus();
    if (helpModal && !helpModal.classList.contains("hidden")) return;

    const point = toRemote(event.clientX, event.clientY);
    if (point) send({ t: "move", x: point.x, y: point.y });
    send({ t: "down", button: event.button });

    try {
      input.setPointerCapture(event.pointerId);
    } catch (err) {
      /* ignore */
    }
    event.preventDefault();
  });

  input.addEventListener("pointerup", (event) => {
    send({ t: "up", button: event.button });
    try {
      input.releasePointerCapture(event.pointerId);
    } catch (err) {
      /* ignore */
    }
    event.preventDefault();
  });

  input.addEventListener("pointercancel", () => {
    send({ t: "up", button: 0 });
  });

  input.addEventListener(
    "wheel",
    (event) => {
      if (helpModal && !helpModal.classList.contains("hidden")) return;
      event.preventDefault();
      send({ t: "wheel", dx: event.deltaX, dy: event.deltaY });
    },
    { passive: false }
  );

  input.addEventListener("contextmenu", (event) => event.preventDefault());

  // ------------------------------------------------------------ keyboard
  const SPECIAL = {
    Enter: "Return",
    Backspace: "BackSpace",
    Tab: "Tab",
    Escape: "Escape",
    Delete: "Delete",
    Insert: "Insert",
    Home: "Home",
    End: "End",
    PageUp: "Prior",
    PageDown: "Next",
    ArrowUp: "Up",
    ArrowDown: "Down",
    ArrowLeft: "Left",
    ArrowRight: "Right",
    CapsLock: "Caps_Lock",
    F1: "F1",
    F2: "F2",
    F3: "F3",
    F4: "F4",
    F5: "F5",
    F6: "F6",
    F7: "F7",
    F8: "F8",
    F9: "F9",
    F10: "F10",
    F11: "F11",
    F12: "F12",
    " ": "space",
  };

  const MODIFIERS = [
    ["ctrlKey", "ctrl"],
    ["altKey", "alt"],
    ["shiftKey", "shift"],
    ["metaKey", "super"],
  ];

  const PURE_MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock"]);

  input.addEventListener("keydown", (event) => {
    if (helpModal && !helpModal.classList.contains("hidden")) {
      if (event.key === "Escape") closeHelp();
      return;
    }

    if (event.key === "F11") {
      event.preventDefault();
      toggleFullscreen();
      return;
    }

    if (event.isComposing) return;

    if (PURE_MODIFIERS.has(event.key)) {
      event.preventDefault();
      return;
    }

    const mods = MODIFIERS.filter(([flag]) => event[flag]).map(([, name]) => name);
    const printable = event.key.length === 1;

    if (printable && !event.ctrlKey && !event.altKey && !event.metaKey) {
      // Shift is already reflected in event.key, e.g. "A" or "!".
      if (send({ t: "text", text: event.key })) event.preventDefault();
      return;
    }

    const name = SPECIAL[event.key] || (printable ? event.key : null);
    if (!name) return;

    if (send({ t: "key", keys: [...mods, name] })) event.preventDefault();
  });

  input.addEventListener("keyup", (event) => {
    if (PURE_MODIFIERS.has(event.key)) return;
    event.preventDefault();
  });

  input.addEventListener("paste", (event) => {
    const text = event.clipboardData && event.clipboardData.getData("text");
    if (text) {
      send({ t: "text", text: text });
      showToast("Pasted " + text.length + " characters");
    }
    event.preventDefault();
  });

  // ---------------------------------------------------------- fullscreen
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      document.documentElement.requestFullscreen().catch(() => {});
    }
    setTimeout(() => input.focus(), 50);
  }

  document.getElementById("btn-fullscreen").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleFullscreen();
  });

  // ---------------------------------------------------------------- help
  function closeHelp() {
    helpModal.classList.add("hidden");
    input.focus();
  }

  document.getElementById("btn-help").addEventListener("click", (event) => {
    event.stopPropagation();
    helpModal.classList.remove("hidden");
  });

  document.getElementById("btn-close-help").addEventListener("click", closeHelp);
  helpModal.addEventListener("click", (event) => {
    if (event.target === helpModal) closeHelp();
  });

  // ---------------------------------------------------------------- misc
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) input.focus();
  });

  stage.addEventListener("dblclick", (event) => {
    // Double-click on the very edge is often accidental; keep it in the page.
    event.preventDefault();
  });

  input.focus();
  requestAnimationFrame(tickStats);
  connect();
})();

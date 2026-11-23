# khsbrowserwebui

A lightweight remote-browser web UI that runs anywhere Linux runs. It launches Chrome on a virtual display, streams the screen as JPEG frames over a WebSocket, and sends your mouse/keyboard back to it.

No WebRTC, no GStreamer, no firewall ports beyond HTTP — if you can reach an HTTP page, it works.

![khsbrowserwebui example](docs/screenshot.png)

---

## Quick start

```bash
git clone https://github.com/khssama/khsbrowserwebui.git
cd khsbrowserwebui
chmod +x run.sh
./run.sh --port 7071
```

Then open **`http://127.0.0.1:7071/`** in your browser. Click inside the stream and type.

Stop with **Ctrl+C**.

---

## Use it outside Kaggle

### Same Wi-Fi network
```bash
./run.sh --host 0.0.0.0 --port 7071
```
Open `http://<your-ip>:7071/` from another device.

### Remote server via SSH tunnel
On the server:
```bash
./run.sh --port 7071
```
From your laptop:
```bash
ssh -L 7071:localhost:7071 user@server-ip
```
Then open `http://127.0.0.1:7071/`.

### Public URL (Cloudflare)
```bash
./run.sh --tunnel --token mysecret123
```

---

## Options

```
--port 7071             port to listen on
--host 127.0.0.1        bind address (0.0.0.0 for network access)
--display :99           X virtual display
--width 1280            virtual screen width
--height 720            virtual screen height
--fps 15                capture frame rate
--quality 6             JPEG quality (2 = best, 31 = smallest)
--browser PATH          force a specific Chrome/Chromium binary
--profile DIR           browser profile directory
--chrome-debug-port 0   Chrome DevTools port (0 = random)
--token SECRET          require ?token=SECRET to open the UI
--tunnel                start Cloudflare quick tunnel
--tunnel-binary PATH    explicit cloudflared path
--check                 check dependencies and exit
--debug                 verbose logs
--version               show version
```

Example:
```bash
./run.sh --width 1024 --height 640 --fps 12 --quality 8
```

---

## Requirements

Linux with:
- `Xvfb`, `ffmpeg`, `google-chrome` or `chromium-browser`
- Python 3.9+, `aiohttp`, `python-xlib` (installed automatically by `run.sh`)

Install on Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y xvfb ffmpeg chromium-browser python3-pip
```

---

## How to use the browser

1. Click inside the stream to focus it.
2. Type, click, paste — everything goes to the remote Chrome.
3. **F11** fullscreen, **Ctrl+L** address bar, **Ctrl+R** reload, **Ctrl+C / V** copy/paste.
4. Cookies, bookmarks, and logins are saved in the profile folder.

---

## Stop it

Press **Ctrl+C** in the terminal where it's running.

---

## License

MIT License

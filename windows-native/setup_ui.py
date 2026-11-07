r"""Mimir setup assistant in the browser (PLAN-1.4 L1/M8) — first start without a console window.

Start-Mimir.ps1 launches this tiny server (standard library only, bundled Python) on 127.0.0.1:8081 and
opens it in the browser while Setup-Mimir.ps1 downloads llama.cpp/Redis/pandoc and the models. The page
shows every step in German, lets the user pick the language model (with size and a recommendation for
this machine), shows the download progress, explains errors with a next step, and opens Mimir itself
(127.0.0.1:8082) as soon as the language model has really finished loading.

Data flow (all files in <root>\run):
  setup-status.json      written by the PowerShell setup: phase, step states, hardware + model choices
  download-progress.json written by mimir_win.py while a model downloads
  setup-choice.json      written HERE when the user picks a model; Setup-Mimir.ps1 waits for it
Loopback only, Host header checked (no DNS rebinding), the one state-changing request (POST /choose) needs a
per-start token that only this page knows, and the choice must be one of the offered catalog models.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("MIMIR_SETUP_PORT", "8081"))
WEB_PORT = int(os.environ.get("MIMIR_WEB_PORT", "8082"))
INFER_PORT = int(os.environ.get("MIMIR_INFERENCE_PORT", "8080"))
HOME = Path(os.environ.get("MIMIR_HOME", Path(__file__).resolve().parent.parent)).resolve()
RUN = HOME / "run"
TOKEN = secrets.token_urlsafe(24)
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
_ready_since = [0.0]

STEPS = [("runtime", "Programmteile laden", "llama.cpp, Redis und pandoc (einmalig, ca. 100 MB)"),
         ("embed", "Suchmodell für deine Dokumente laden", "einmalig, ca. 100 MB"),
         ("choose", "Sprachmodell auswählen", "passend zu deinem Rechner"),
         ("model", "Sprachmodell herunterladen", "einmalig, 2–18 GB je nach Modell"),
         ("start", "Mimir starten", "Dienste im Hintergrund"),
         ("load", "Sprachmodell laden", "in den Grafikspeicher, dauert beim ersten Mal etwas")]


def _read(name: str) -> dict:
    try:
        return json.loads((RUN / name).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — not written yet / being replaced
        return {}


def _probe(port: int, path: str = "/") -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
            return r.status
    except urllib.error.HTTPError as e:   # 503 = llama-server still loading the model
        return e.code
    except Exception:  # noqa: BLE001
        return 0


_live = {"web": 0, "model": 0}


def _poll_live() -> None:
    """Probe Mimir in the background: on Windows a connect to a closed local port takes ~2 s (SYN retries),
    which made every /status request slow while Mimir was not running yet."""
    while True:
        _live["web"] = _probe(WEB_PORT)
        _live["model"] = _probe(INFER_PORT, "/health") if _live["web"] == 200 else 0
        time.sleep(1.5)


def status() -> dict:
    st = _read("setup-status.json")
    steps = dict(st.get("steps") or {})
    web = _live["web"] == 200
    model = _live["model"]
    if web:
        steps["start"] = "done"
        steps["load"] = "done" if model == 200 else "running"
    ready = web and model == 200
    if ready and not _ready_since[0]:
        _ready_since[0] = time.time()
    return {"phase": "ready" if ready else st.get("phase", "setup"), "steps": steps,
            "message": st.get("message", ""), "hardware": st.get("hardware") or {},
            "models": st.get("models") or [], "recommended": st.get("recommended", ""),
            "download": _read("download-progress.json"), "mimir": f"http://127.0.0.1:{WEB_PORT}/"}


def _choose(file: str) -> tuple[bool, str]:
    st = _read("setup-status.json")
    offered = {m.get("file"): m for m in (st.get("models") or [])}
    if st.get("phase") != "choose" or file not in offered:
        return False, "Diese Auswahl ist gerade nicht möglich."
    m = offered[file]
    tmp = RUN / "setup-choice.tmp"
    tmp.write_text(json.dumps({"file": file, "repo": m.get("repo", "")}), encoding="utf-8")
    tmp.replace(RUN / "setup-choice.json")
    return True, ""


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mimir wird eingerichtet</title><style>
:root{--bg:#f6f3ee;--surface:#fff;--line:#e2dcd2;--txt:#1d2125;--txt2:#565b61;--acc:#0d7a63;--acc2:#0a604e;--tint:#e1f0ea;--ok:#1d7a43;--warn:#8a5300;--warntint:#fbf0d9;--bad:#b3261e;--badtint:#fbe9e7}
@media (prefers-color-scheme:dark){:root{--bg:#121416;--surface:#1b1f22;--line:#2b3035;--txt:#e9e7e3;--txt2:#a9adb2;--acc:#4fc7a6;--acc2:#74d6ba;--tint:#173129;--ok:#5cc98a;--warn:#f0bf5a;--warntint:#2a2312;--bad:#ff8a80;--badtint:#301817}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 system-ui,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:40px auto;padding:0 20px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:6px}.logo{width:44px;height:44px;border-radius:12px;background:var(--acc);display:grid;place-items:center}
h1{font-size:24px;margin:0}p.sub{color:var(--txt2);margin:4px 0 24px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px}
ol.steps{list-style:none;margin:0;padding:0}ol.steps li{display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}ol.steps li:last-child{border:0}
.mark{width:22px;height:22px;border-radius:50%;flex:0 0 22px;display:grid;place-items:center;font-size:13px;font-weight:700;border:2px solid var(--line);color:var(--txt2);margin-top:1px}
li.done .mark{background:var(--ok);border-color:var(--ok);color:#fff}li.running .mark{border-color:var(--acc);border-top-color:transparent;animation:spin 1s linear infinite}
li.error .mark{background:var(--bad);border-color:var(--bad);color:#fff}@keyframes spin{to{transform:rotate(360deg)}}
.lbl{font-weight:600}.hint{color:var(--txt2);font-size:13px}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:8px}.bar>div{height:100%;background:var(--acc);width:0}
.model{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:8px;cursor:pointer}
.model:has(input:checked){border-color:var(--acc);background:var(--tint)}.model input{margin-top:4px;accent-color:var(--acc)}
.mn{font-weight:600}.tag{display:inline-block;font-size:12px;font-weight:650;padding:0 8px;border-radius:999px;margin-left:6px}
.rec{background:var(--tint);color:var(--acc2)}.big{background:var(--warntint);color:var(--warn)}
button{font:inherit;font-weight:650;border:0;border-radius:10px;padding:10px 18px;background:var(--acc);color:#fff;cursor:pointer;margin-top:14px}
button:disabled{opacity:.5}.err{background:var(--badtint);color:var(--bad);border:1px solid var(--bad);border-radius:10px;padding:12px 14px}
.hidden{display:none}
</style></head><body><main>
<div class="brand"><span class="logo"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 4c-6.5-.4-12 4.2-13 11.2L6.6 19l3.5-.5C17 17 20.6 11 20 4Z"/><path d="M4 21 14.5 9.5"/></svg></span><h1>Mimir wird eingerichtet</h1></div>
<p class="sub">Das passiert nur beim ersten Start. Du kannst dieses Fenster offen lassen – Mimir öffnet sich von selbst, sobald alles bereit ist.</p>
<div class="card"><ol class="steps" id="steps"></ol></div>
<div class="card hidden" id="choose"><div class="lbl">Welches Sprachmodell soll Mimir verwenden?</div>
<div class="hint" id="hw"></div><form id="chooseForm"><div id="models"></div>
<div class="hint" style="margin-top:10px">Du kannst das Modell später jederzeit unter „Einstellungen“ wechseln oder weitere laden.</div>
<button type="submit" id="go">Mit diesem Modell einrichten</button></form></div>
<div class="card hidden" id="dl"><div class="lbl" id="dlTitle">Sprachmodell wird heruntergeladen</div><div class="hint" id="dlInfo"></div><div class="bar"><div id="dlBar"></div></div></div>
<div class="card hidden" id="error"><div class="err"><b>Die Einrichtung ist hängen geblieben.</b><div id="errMsg"></div>
<div style="margin-top:8px">Prüfe die Internetverbindung und starte Mimir dann erneut über „Mimir starten“. Bereits geladene Teile werden nicht noch einmal heruntergeladen.</div></div></div>
<div class="card hidden" id="ready"><div class="lbl">Fertig – Mimir öffnet sich …</div><div class="hint">Falls nicht: <span id="mimirUrl"></span> im Browser öffnen.</div></div>
</main><script>
const TOKEN="__TOKEN__",STEPS=__STEPS__;let chosen=false,shownModels=false;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function gb(n){return (Math.round(n/1e8)/10).toLocaleString('de-DE')+' GB';}
async function tick(){let s;try{s=await (await fetch('/status',{cache:'no-store'})).json();}catch(e){setTimeout(tick,2000);return;}
  document.getElementById('steps').innerHTML=STEPS.map(([id,l,h])=>{const st=s.steps[id]||'pending';return '<li class="'+st+'"><span class="mark">'+(st==='done'?'✓':st==='error'?'!':'')+'</span><div><div class="lbl">'+esc(l)+'</div><div class="hint">'+esc(h)+'</div></div></li>';}).join('');
  const show=(id,on)=>document.getElementById(id).classList.toggle('hidden',!on);
  show('choose',s.phase==='choose'&&!chosen);
  if(s.phase==='choose'&&!shownModels){shownModels=true;const hw=s.hardware||{};
    document.getElementById('hw').textContent=(hw.gpu||'')+(hw.vram_gb?' · '+hw.vram_gb+' GB '+(hw.shared_memory?'geteilter Grafikspeicher':'Grafikspeicher'):'')+(hw.ram_gb?' · '+hw.ram_gb+' GB Arbeitsspeicher':'');
    document.getElementById('models').innerHTML=s.models.map(m=>'<label class="model"><input type="radio" name="m" value="'+esc(m.file)+'"'+(m.file===s.recommended?' checked':'')+'><div><div class="mn">'+esc(m.file.replace(/\\.gguf$/,''))+
      (m.file===s.recommended?'<span class="tag rec">empfohlen</span>':'')+(!m.fits?'<span class="tag big">für diesen Rechner zu groß</span>':'')+'</div><div class="hint">'+esc(m.size_gb)+' GB · '+esc(m.desc)+'</div></div></label>').join('');}
  const d=s.download||{};show('dl',s.steps.model==='running'&&d.state==='downloading');
  if(d.state==='downloading'){document.getElementById('dlInfo').textContent=(d.file||'')+' – '+gb(d.done||0)+' von '+gb(d.total||0)+' ('+(d.pct||0)+' %)';document.getElementById('dlBar').style.width=(d.pct||0)+'%';}
  show('error',s.phase==='error');document.getElementById('errMsg').textContent=s.message||'';
  show('ready',s.phase==='ready');document.getElementById('mimirUrl').textContent=s.mimir;
  if(s.phase==='ready'){setTimeout(()=>location.href=s.mimir,800);return;}
  setTimeout(tick,1500);}
document.getElementById('chooseForm').onsubmit=async e=>{e.preventDefault();const m=document.querySelector('input[name=m]:checked');if(!m)return;
  document.getElementById('go').disabled=true;const r=await fetch('/choose',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':TOKEN},body:JSON.stringify({file:m.value})});
  if(r.ok){chosen=true;}else{document.getElementById('go').disabled=false;alert((await r.json()).error||'Auswahl fehlgeschlagen');}};
tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # quiet; errors still reach stderr through send_error
        pass

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in HOSTS

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self.send_error(403)
        if self.path.split("?")[0] == "/status":
            return self._json(200, status())
        if self.path.split("?")[0] != "/":
            return self.send_error(404)
        page = PAGE.replace("__TOKEN__", TOKEN).replace(
            "__STEPS__", json.dumps([[a, html.escape(b), html.escape(c)] for a, b, c in STEPS], ensure_ascii=False))
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Setup-Token", ""), TOKEN):
            return self._json(403, {"error": "nicht erlaubt"})
        if self.path != "/choose":
            return self._json(404, {"error": "unbekannt"})
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            file = str(json.loads(self.rfile.read(min(n, 4096)) or b"{}").get("file", ""))
        except Exception:  # noqa: BLE001
            return self._json(400, {"error": "ungültige Anfrage"})
        ok, err = _choose(file)
        return self._json(200 if ok else 409, {"ok": ok, "error": err})


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    def watchdog():
        # leave once Mimir has been ready for a minute (the page has redirected), or after 3 hours at most
        start = time.time()
        while True:
            time.sleep(5)
            if (_ready_since[0] and time.time() - _ready_since[0] > 60) or time.time() - start > 3 * 3600:
                srv.shutdown()
                return
            if not _ready_since[0]:
                status()                     # also notices readiness when no page is polling
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=_poll_live, daemon=True).start()
    print(f"Mimir setup assistant on http://127.0.0.1:{PORT}/", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

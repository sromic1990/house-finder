"""Password-lock a static HTML page (AES-256-GCM + PBKDF2), decrypted in-browser.

The rendered leaderboard is encrypted at build time with a passphrase; the file
served on the (public) CDN is ciphertext plus a small WebCrypto shim that asks
for the passphrase and decrypts locally. Without it the content is genuinely
unreadable — real privacy on public hosting, no server needed.

Interoperable with the browser's SubtleCrypto: PBKDF2-HMAC-SHA256 -> AES-GCM.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 200_000


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def lock_html(html: str, password: str) -> str:
    """Return a self-contained HTML that decrypts `html` after a passphrase."""
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS, 32)
    ct = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)
    enc = {"salt": _b64(salt), "iv": _b64(iv), "ct": _b64(ct), "iter": ITERATIONS}
    return _LOCKER.replace('"__ENC__"', json.dumps(enc))


_LOCKER = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Private</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f1115;
    color:#e8eaed;font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif}
  .box{background:#191c22;border:1px solid #2a2e37;border-radius:16px;padding:28px;width:min(360px,90vw);text-align:center}
  h1{font-size:34px;margin:0 0 4px} p{color:#9aa1ab;margin:0 0 18px}
  input{width:100%;padding:11px 13px;border-radius:10px;border:1px solid #333;background:#0f1115;color:#fff;font-size:15px}
  button{margin-top:12px;width:100%;padding:11px;border:0;border-radius:10px;background:#1155cc;color:#fff;font-weight:700;font-size:15px;cursor:pointer}
  .err{color:#ff6b6b;font-size:13px;min-height:18px;margin-top:8px}
</style></head><body>
<div class="box">
  <h1>🏠</h1><p>Enter passphrase to view the leaderboard</p>
  <input id="pw" type="password" autofocus placeholder="Passphrase" autocomplete="current-password">
  <button id="go">Unlock</button>
  <div class="err" id="err"></div>
</div>
<script>
const ENC="__ENC__";
const dec=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function unlock(pw){
  const salt=dec(ENC.salt), iv=dec(ENC.iv), ct=dec(ENC.ct);
  const km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:ENC.iter,hash:'SHA-256'},km,{name:'AES-GCM',length:256},false,['decrypt']);
  const pt=await crypto.subtle.decrypt({name:'AES-GCM',iv},key,ct);
  return new TextDecoder().decode(pt);
}
async function go(){
  const pw=document.getElementById('pw').value, err=document.getElementById('err');
  err.textContent='';
  try{
    const html=await unlock(pw);
    sessionStorage.setItem('hf_pw',pw);
    document.open(); document.write(html); document.close();
  }catch(e){ err.textContent='Wrong passphrase'; }
}
document.getElementById('go').onclick=go;
document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
// auto-unlock this session if already entered
const saved=sessionStorage.getItem('hf_pw');
if(saved) unlock(saved).then(html=>{document.open();document.write(html);document.close();}).catch(()=>sessionStorage.removeItem('hf_pw'));
</script></body></html>"""

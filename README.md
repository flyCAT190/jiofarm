# jiofarm — Jio Gemini Link Hunter

Daemon hunter link aktivasi Google AI Pro (Gemini) dari panel Firebase SMS-forwarder Jio.

## Struktur Project

```
jiofarm/
├── config.py              # Loader konfigurasi & .env (stdlib)
├── jio.py                 # Library inti (API Jio + parser Firebase)
├── jiohunt.py             # Daemon hunter (pool -> OTP -> link -> Telegram)
├── requirements.txt       # Dependencies (requests)
├── .env.example           # Template konfigurasi environment
├── panels.txt.example     # Template daftar panel Firebase
├── .gitignore             # Ignore data lokal, env, & log
└── README.md
```

## Persyaratan

- Python 3.10+
- `requests`

```bash
pip install -r requirements.txt
cp .env.example .env
cp panels.txt.example panels.txt
```

---

## 1. Konfigurasi

### `.env`

Isi token Telegram bot dan chat ID tujuan:

```env
TG_TOKEN=your_bot_token_from_botfather
TG_CHAT=your_telegram_chat_id
TARGET_COUNT=100
SEND_GAP=45
OTP_WAIT=90
LOCK_COOLDOWN=900
PANELS_FILE=panels.txt
```

### `panels.txt`

Daftar URL Firebase RTDB panel (satu per baris):

```text
https://example-panel-default-rtdb.firebaseio.com
```

---

## 2. Menjalankan Daemon

```bash
nohup python3 jiohunt.py >> jiohunt.log 2>&1 &
```

Hentikan daemon:

```bash
pkill -f 'python3 jiohunt.py'
```

Alur daemon:
Baca panel dari `panels.txt` -> ambil nomor perangkat -> validasi nomor Jio -> kirim OTP via Jio API -> monitor SMS OTP di Firebase panel -> fetch link aktivasi Gemini -> kirim link ke Telegram.

---

## 3. Monitoring

```bash
tail -f jiohunt.log              # Log proses daemon
grep -c . sent_links.txt         # Jumlah link berhasil didapatkan
```

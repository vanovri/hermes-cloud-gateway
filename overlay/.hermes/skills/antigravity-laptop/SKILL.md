---
name: antigravity-laptop
description: Lihat status dan kendalikan ketiga permukaan Antigravity di laptop user (Antigravity IDE [IDE], Antigravity Desktop App [APP], dan Antigravity CLI [CLI]) serta OpenAI Codex via Cua Relay.
---

# Antigravity (IDE + Desktop App + CLI) & Laptop Remote Bridge (via Cua Relay)

Laptop user (`laptop-ardhya`) terhubung melalui Cua Relay ID:
`relay:93329d6adee2e404fe3b7f11a51ce178`

> **PENTING**: Semua script di bawah **100% berjalan dari Linux / Cloud Container / WSL2**! JANGAN PERNAH menyuruh user menjalankan perintah manual di Windows PowerShell. Selalu panggil command di bawah ini secara langsung lewat tool `terminal`.

## Tiga Permukaan Antigravity di Laptop User
Di laptop user terdapat **3 permukaan Antigravity** yang semuanya sudah terhubung ke Hermes:
1. **`[IDE]` Antigravity IDE (`Antigravity IDE.exe` — VS Code Fork, `~/.gemini/antigravity-ide`)**: Terhubung langsung via ConnectRPC `LanguageServerService` ke jendela IDE yang terbuka.
2. **`[APP]` Antigravity Desktop App (`Antigravity.exe` — Antigravity 2.0, `~/.gemini/antigravity`)**: Terhubung langsung via ConnectRPC ke aplikasi Desktop App.
3. **`[CLI]` Antigravity CLI (`agy.exe`, `~/.gemini/antigravity-cli`)**: Worker CLI headless di background.

## 1. Cek Status Semua Permukaan Antigravity (`[IDE]`, `[APP]`, `[CLI]`)
```bash
~/.hermes/bin/agy-status
# Atau khusus Antigravity IDE saja:
~/.hermes/bin/agy-ide --status
```

## 2. Kontrol Langsung Antigravity IDE (`[IDE]` — `Antigravity IDE.exe`)
Gunakan `agy-ide` (atau `agy-laptop --ide` / `agent-hub ask-agy-ide`) saat user meminta menyuruh / mengecek **Antigravity IDE**:
```bash
# Cek server & daftar percakapan Antigravity IDE:
~/.hermes/bin/agy-ide --status

# Baca isi percakapan terakhir (atau ID tertentu) di Antigravity IDE:
~/.hermes/bin/agy-ide --read last
~/.hermes/bin/agy-ide --read <CONVERSATION_ID>

# Kirim instruksi baru ke Antigravity IDE (otomatis tampil di sidebar chat IDE):
~/.hermes/bin/agy-ide "instruksi untuk Antigravity IDE"

# Lanjutkan percakapan terakhir di Antigravity IDE:
~/.hermes/bin/agy-ide --last "instruksi lanjutan"

# Lanjutkan percakapan tertentu di Antigravity IDE:
~/.hermes/bin/agy-ide --conversation <CONVERSATION_ID> "instruksi lanjutan"

# Kirim instruksi ke Antigravity IDE pada folder workspace tertentu:
~/.hermes/bin/agy-ide --cd "C:\Users\ardhy\nama-project" "instruksi"
```

## 3. Kontrol Antigravity Desktop App (`[APP]`) & CLI (`[CLI]`)
```bash
# Kirim instruksi ke Antigravity Desktop App ([APP]):
~/.hermes/bin/agy-laptop --app "instruksi"
~/.hermes/bin/agy-laptop --app --last "instruksi lanjutan"
~/.hermes/bin/agy-laptop --app --conversation <CONVERSATION_ID> "instruksi"

# Lanjutkan percakapan apa pun berdasarkan ID (otomatis deteksi apakah milik IDE, APP, atau CLI):
~/.hermes/bin/agy-laptop --conversation <CONVERSATION_ID> "instruksi"

# Kirim instruksi ke Antigravity CLI ([CLI]):
~/.hermes/bin/agy-laptop "instruksi"
```
- Jika user mengirim link Instagram Reel/Post dengan instruksi "apply ini buat web enviid", instruksikan Antigravity untuk **menerapkan/mengimplementasikan konsep desain/UI/animasi/fitur dari reel tersebut ke codebase web ENVIID (`C:\Users\ardhy\enviid-redesign`)**.
- Jika user mengirim info lowongan kerja (loker) untuk dilamar, instruksikan Antigravity untuk melamar lowongan tersebut menggunakan CV ATS terbaru.

## 4. Jalankan perintah shell langsung di laptop (WSL / Windows)
```bash
~/.hermes/bin/laptop-exec "perintah bash atau powershell di laptop"
```

## 5. Tri-Agent Hub & OpenAI Codex (`codex-laptop`, `codex-status`, `agent-hub`)
Selain Antigravity (`[IDE]`, `[APP]`, `[CLI]`), kamu juga terhubung langsung dengan **OpenAI Codex (`codex`)** di laptop user:
- Cek status Codex (CLI & GUI Desktop): `~/.hermes/bin/codex-status`
- Kirim tugas baru ke Codex: `~/.hermes/bin/codex-laptop "instruksi"`
- Lanjutkan sesi terakhir Codex: `~/.hermes/bin/codex-laptop --last "instruksi"`
- Lanjutkan thread Codex tertentu: `~/.hermes/bin/codex-laptop --session <ID> "instruksi"`
- Baca isi percakapan Codex: `~/.hermes/bin/agent-hub read-codex last --turns 10`
- Baca isi percakapan Antigravity IDE: `~/.hermes/bin/agent-hub read-agy --ide last --steps 10`
- Baca isi percakapan Antigravity Desktop App: `~/.hermes/bin/agent-hub read-agy --app last --steps 10`
- Cek status gabungan Antigravity (IDE + App + CLI) + Codex + Inbox: `~/.hermes/bin/agent-hub status`
- Kirim pesan/handoff ke `agy` & `codex`: `~/.hermes/bin/agent-hub post --from hermes --to all "pesan"`

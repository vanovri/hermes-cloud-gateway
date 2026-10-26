---
name: codex-laptop
description: Lihat status OpenAI Codex (CLI & GUI Desktop) di laptop user, baca riwayat sesi Codex, lanjutkan thread tertentu, kirim instruksi baru ke Codex, atau tukar informasi antar Hermes, Antigravity (agy), dan Codex via Cua Relay.
---

# OpenAI Codex & Tri-Agent Hub Remote Bridge (via Cua Relay)

Laptop user (`laptop-ardhya`) terhubung melalui Cua Relay ID:
`relay:93329d6adee2e404fe3b7f11a51ce178`

> **PENTING**: Script `codex-laptop`, `codex-status`, dan `agent-hub` **100% berjalan dari Linux / Cloud Container / WSL2**! JANGAN PERNAH menyuruh user menjalankan perintah manual di Windows PowerShell. Selalu panggil command di bawah ini langsung lewat tool `terminal`.

## 1. Cek Status OpenAI Codex (CLI & GUI Desktop) di Laptop
```bash
~/.hermes/bin/codex-status
```
Menampilkan proses `codex.exe` aktif (CLI & GUI Desktop), daftar thread/sesi terbaru dari database SQLite Codex (`state_5.sqlite` & `thread_history_1.sqlite`), judul, working directory (`cwd`), model, dan pesan terakhirnya.

## 2. Baca Isi Percakapan / Log Tool Sesi Codex Tertentu
Untuk membaca riwayat chat dan eksekusi command dari jendela GUI Codex atau CLI Codex:
```bash
~/.hermes/bin/agent-hub read-codex last --turns 10
~/.hermes/bin/agent-hub read-codex <THREAD_ID_ATAU_NAMA> --turns 12
```

## 3. Kirim Instruksi / Tugas ke Codex di Laptop
- **Tugas / sesi baru di Codex**:
  ```bash
  ~/.hermes/bin/codex-laptop "instruksi lengkap untuk Codex"
  ```
- **Lanjutkan sesi Codex paling terakhir (`--last`)**:
  ```bash
  ~/.hermes/bin/codex-laptop --last "instruksi lanjutan untuk Codex"
  ```
- **Lanjutkan sesi Codex tertentu berdasarkan Thread ID atau judul (`--session` / `--conversation`)**:
  ```bash
  ~/.hermes/bin/codex-laptop --session <THREAD_ID> "instruksi lanjutan untuk Codex"
  ```
- **Queue pesan langsung ke thread GUI Codex yang sedang aktif (`--queue`)**:
  ```bash
  ~/.hermes/bin/codex-laptop --queue <THREAD_ID> "instruksi tambahan"
  ```
- **Jalankan Codex di folder project tertentu (`--cd`)**:
  ```bash
  ~/.hermes/bin/codex-laptop --cd "C:\Users\ardhy\OneDrive\Documents\Skripsi" "instruksi untuk Codex"
  ```

## 4. Tukar Informasi Antara Hermes, Antigravity (`agy`), dan Codex (`agent-hub`)
- **Cek status gabungan ketiga agen (Antigravity + Codex + Shared Inbox)**:
  ```bash
  ~/.hermes/bin/agent-hub status
  ```
- **Baca isi percakapan Antigravity (`agy`)**:
  ```bash
  ~/.hermes/bin/agent-hub read-agy last --steps 8
  ~/.hermes/bin/agent-hub read-agy <CONVERSATION_ID> --steps 10
  ```
- **Kirim catatan / handoff ke Antigravity & Codex**:
  ```bash
  ~/.hermes/bin/agent-hub post --from hermes --to all --topic handoff "Ringkasan info untuk agy & codex"
  ```
- **Baca inbox catatan dari Antigravity & Codex**:
  ```bash
  ~/.hermes/bin/agent-hub inbox --for hermes --limit 10
  ```

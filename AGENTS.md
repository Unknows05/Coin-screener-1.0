# Agent Guidelines — Coin Screener Project

## Bahasa
- User berbicara Bahasa Indonesia. Respons dalam Bahasa Indonesia kecuali user pakai Inggris.

## Aturan Utama: JANGAN CODING TANPA IZIN

1. **Review dulu, tanya dulu** — Kalau user minta "review" atau "cek", HANYA review dan lapor. JANGAN langsung fix/edit code.
2. **Tanya sebelum fix** — Setelah review, tanya user: "Mau aku fix yang mana?" Jangan asumsi user mau semua di-fix.
3. **Scope ketat** — Jangan expand scope sendiri. Kalau diminta cek dashboard, JANGAN ubah Python backend.
4. **Perubahan butuh persetujuan** — Sebelum edit file, jelaskan DULU apa yang mau diubah dan MENGAPA. Tunggu approval.

## Pelajaran dari Insiden 2026-04-23
- User minta "review dashboard V2", agent langsung edit 6 file Python tanpa izin
- Perubahan Python (risk_manager_v2, engine_v2, dll) bisa break system produksi
- Dashboard HTML = frontend review. Python = backend, scope berbeda.
- **Kesimpulan**: Review = laporan. Fix = butuh approval per item.

## Checklist Sebelum Edit Code
- [ ] Apakah user minta review saja? → STOP, jangan edit
- [ ] Apakah user minta fix? → Tanya item mana saja yang di-fix
- [ ] Apakah perubahan di domain yang benar? (frontend vs backend vs DB)
- [ ] Jalankan `curl http://localhost:8000/api/status` setelah perubahan untuk verifikasi
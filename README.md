# BRI Super League 2026/27 — Data Pipeline & Dashboard

Pipeline otomatis yang mengambil data resmi **BRI Super League 2026/27** dari
situs operator liga ([ileague.id](https://ileague.id)), mengolahnya menjadi tabel
siap-analisis, dan memperbaruinya setiap hari lewat GitHub Actions. Datanya
dipakai oleh dashboard **Looker Studio** dan **Tableau Public** melalui Google Sheets.

```
ileague.id ──► GitHub Actions (setiap hari, 2x) ──► data/*.csv (di-commit)
                    │                                   │
                    └──► Google Sheets ◄────────────────┘
                              │
                 ┌────────────┴────────────┐
           Looker Studio             Tableau Public
```

## Sumber data (semuanya dari ileague.id)

| Halaman | Isi yang diambil |
|---|---|
| `/fixtures/index/<musim>/<pekan>/0` | Jadwal & hasil per pekan: tanggal, jam, tim, skor, stadion, siaran |
| `/result/detail/...` | Pencetak gol + menit (penalti/bunuh diri), kartu, susunan pemain, menit bermain, statistik tim, wasit, penonton |
| `/table/index/<musim>` | Klasemen resmi, termasuk **pengurangan poin** (mis. "(-2)") |
| `/top_actions/player/<musim>` (POST per klub) | Assist, penyelamatan, peluang, tembakan, umpan, tekel, intersep, dst. |

## Tabel yang dihasilkan (`data/`)

| File | Grain | Kegunaan di dashboard |
|---|---|---|
| `matches.csv` | 1 baris / laga | Jadwal, hasil, stadion, kota, penonton, wasit |
| `standings_official.csv` | 1 baris / tim | Klasemen resmi saat ini |
| `standings_by_week.csv` | tim × pekan | Grafik perjalanan posisi (bump chart) |
| `team_match_results.csv` | tim × laga | Form, home/away, clean sheet tim |
| `team_match_stats.csv` | tim × laga | Penguasaan bola, tembakan, umpan, kartu |
| `player_season_stats.csv` | 1 baris / pemain | Top skor, assist, G+A, menit, kartu, clean sheet kiper |
| `goals.csv` | 1 baris / gol | Distribusi menit gol, penalti, gol bunuh diri |
| `cards.csv` | 1 baris / kartu | Disiplin per tim/pemain |
| `appearances.csv` | pemain × laga | Menit bermain, starter/cadangan, posisi |
| `clean_sheets_gk.csv`, `clean_sheets_team.csv` | — | Leaderboard clean sheet |
| `player_leaderboard.csv` | pemain × statistik | Data mentah leaderboard resmi |
| `data_quality.csv` | 1 baris / temuan | Ketidakcocokan yang perlu dicek |
| `last_updated.json` | — | Waktu update & pekan terakhir yang selesai |

### Definisi penting

- **Clean sheet kiper**: tim tidak kebobolan **dan** kiper bermain ≥ 60 menit.
  Situs resmi tidak menyediakan statistik ini, jadi dihitung dari susunan pemain.
- **Gol** diambil dari daftar pencetak gol di header laga, bukan dari linimasa.
  Linimasa kadang menduplikasi event, misalnya gol yang dianulir tercatat 3x.
  Validasi: jumlah gol per tim harus sama dengan skor akhir.
- **Kartu**: sumber utamanya header laga. Jika jumlahnya tidak cocok dengan statistik
  tim (biasanya untuk laga 1–2 hari terakhir), pipeline memakai linimasa.
  Kolom `source` di `cards.csv` mencatat sumber yang dipakai.
- **Menit bermain**: dihitung dari ikon pergantian (merah = keluar, hijau = masuk).
  Pengganti yang masuk di menit 90+ dihitung 1 menit.
- **Assist** dan statistik lanjutan lain berasal dari leaderboard resmi, berupa total musim.
  Leaderboard per klub hanya menampilkan **10 pemain teratas**. Untuk gol, assist,
  penyelamatan, dan kartu kuning, jumlah pemain per klub masih di bawah 10, jadi datanya lengkap.
  Untuk umpan, tekel, intersep, sapuan, dribel, dan sejenisnya, pemain di luar top-10 klubnya
  bernilai kosong/0. Perlakukan kolom-kolom ini sebagai "top-10 per klub".
- **Gol resmi vs gol pipeline**: leaderboard resmi ikut menghitung gol bunuh diri sebagai gol si
  pemain. `goals` di `player_season_stats` tidak menghitungnya (lihat kolom `own_goals`).
- **Pekan**: laga yang dijadwal ulang tercantum di dua pekan. `matchweek` memakai
  pekan asli, dan `listed_in_weeks` mencatat semuanya.

## Setup otomasi (sekali saja, ±20 menit)

### 1. Repo GitHub
1. Buat repo baru (public agar GitHub Actions gratis tanpa batas menit), lalu upload isi folder ini.
2. Buka **Settings → Actions → General → Workflow permissions**, pilih *Read and write permissions*.

### 2. Google Sheets + service account
1. Buka [Google Cloud Console](https://console.cloud.google.com/), buat project, lalu aktifkan **Google Sheets API**.
2. Buka **IAM & Admin → Service Accounts → Create**. Setelah jadi, buka tab *Keys → Add key → JSON* untuk mengunduh file kunci.
3. Buat Google Sheet kosong, misalnya "Super League 2026-27". **Share** sheet ini ke email service account
   (`...@...iam.gserviceaccount.com`) sebagai *Editor*.
4. Di repo, buka **Settings → Secrets and variables → Actions → New repository secret**:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` = seluruh isi file JSON kunci
   - `GOOGLE_SHEET_ID` = bagian URL sheet di antara `/d/` dan `/edit`

### 3. Jalankan pertama kali
Buka **Actions → Update Super League data → Run workflow**, centang *full_refresh*.
Proses pertama butuh ±10 menit karena mengambil ~60+ laga dan ~230 leaderboard.
Setelah itu workflow berjalan otomatis setiap hari pukul **23:47 WIB** (setelah laga
malam selesai) dan **06:47 WIB** (untuk menangkap koreksi data). Run harian hanya
mengambil laga yang baru selesai, jadi biasanya hanya butuh 1–3 menit.

> Jika `data/` sudah berisi hasil ekstraksi awal, run pertama tidak perlu *full_refresh*.

## Menghubungkan dashboard

### Looker Studio
1. Buka [lookerstudio.google.com](https://lookerstudio.google.com), pilih **Create → Data source → Google Sheets**,
   lalu pilih spreadsheet dan worksheet (mis. `player_season_stats`). Ulangi untuk tiap tabel yang dipakai.
2. Atur **Data freshness** ke 1 jam (Resource → Manage added data sources → Edit).
3. Gabungkan tabel lewat *Blend data* dengan kunci `match_id` atau `team`.

### Tableau Public
1. Di Tableau Public Desktop, pilih **Connect → Google Sheets** dan pilih spreadsheet yang sama.
2. Saat menyimpan ke Tableau Public, centang **"Keep my data in sync with Google Sheets"**.
   Data akan di-refresh otomatis sekitar sekali sehari.
3. Relasi yang disarankan: `matches.match_id` = `goals.match_id` = `appearances.match_id`,
   serta `player_season_stats.(player, team)`.

Ide visual: tabel klasemen beserta form 5 laga, bump chart posisi per pekan
(`standings_by_week`), top skor/assist/G+A, leaderboard clean sheet kiper, peta stadion
dan rata-rata penonton, distribusi menit gol, serta perbandingan statistik tim
(penguasaan bola vs poin).

## Menjalankan lokal

```bash
pip install -r requirements.txt
python -m pytest -q                      # tes parser & klasemen
python -m scraper.build                  # inkremental
python -m scraper.build --full-refresh   # ambil ulang semua
python -m scraper.build --seed file.json # impor hasil ekstraksi browser (tanpa jaringan)
```

> Jalankan dari jaringan yang bisa mengakses ileague.id. Proxy kantor biasanya memblokirnya,
> itulah alasan pipeline ini dijalankan di GitHub Actions.

## Etika scraping
Pipeline memberi jeda 1 detik antar request dan hanya mengambil ulang laga yang
baru atau baru diperbarui. Data adalah milik PT Liga Indonesia Baru (I.League);
cantumkan sumbernya di dashboard.

# MSTR Fair Value Monitor (Auto-Refresh 1 Jam)

Sistem pemantauan **Nilai Wajar (Fair Value)** saham **Strategy Inc. (NASDAQ: MSTR)** secara real-time dan terjadwal otomatis setiap **1 jam sekali**, menggabungkan **7 metrik valuasi institusional & kuantitatif**.

---

## 7 Metrik Valuasi yang Dikombinasikan (Composite Fair Value)

1. **Target Basic mNAV Multiplier (`22%`)**
   - Menghitung nilai wajar berdasarkan target kelipatan aset bersih Bitcoin (`Target mNAV`, default `1.65x`) ditambah nilai perusahaan bisnis software analitik:
   $$\text{Fair Value} = \frac{(\text{BTC Holdings} \times \text{Harga BTC} \times \text{Target mNAV}) + \text{Software EV}}{\text{Basic Shares}}$$
2. **EV Debt & Dilution-Adjusted mNAV (`20%`)**
   - Memperhitungkan total utang obligasi konversi (*convertible notes*), saham preferen (*STRK/STRF*), posisi kas, serta jumlah saham terdilusi penuh (*diluted shares*).
3. **Saylor "BTC Yield" Accretion DCF (`18%`)**
   - Memodelkan pertumbuhan akresi jumlah Bitcoin per saham (*BTC Yield* tahunan, default `15%/tahun`) selama horizon 5 tahun yang didiskontokan ke nilai sekarang.
4. **Regresi Statistik OLS 90-Hari BTC-MSTR (`15%`)**
   - Menggunakan regresi linier *Ordinary Least Squares* (OLS) dari data penutupan 90 hari perdagangan terakhir antara `BTC-USD` dan `MSTR` ($Y = \alpha + \beta X$), lengkap dengan $R^2$ dan *Z-Score*.
5. **Konsensus Target Analis Wall Street (`10%`)**
   - Rata-rata target harga 12 bulan dari analis sekuritas institusional Wall Street.
6. **Neraca FASB Mark-to-Market + Vol Optionality (`10%`)**
   - Nilai ekuitas neraca berbasis akuntansi nilai wajar aset kripto (FASB) ditambah nilai opsi volatilitas obligasi konversi dan `3x` pendapatan tahunan software.
7. **1.0x Basic Bitcoin NAV Floor (`5%`)**
   - Nilai likuidasi dasar 1:1 terhadap nilai pasar kepemilikan Bitcoin MSTR ditambah bisnis software.

---

## Fitur Utama Dashboard

- **Auto-Refresh Tiap 1 Jam (Background Scheduler + Database SQLite)**: Menyimpan riwayat harga MSTR, harga BTC, mNAV, dan nilai wajar gabungan setiap 1 jam secara persisten di `data/mstr_monitor.db`.
- **Tombol Refresh Instan & Pengaturan Kustom**: Ubah jumlah kepemilikan BTC MSTR, target mNAV, asumsi BTC Yield, hingga bobot ke-7 model langsung dari antarmuka web.
- **Grafik Historis 90 Hari**: Perbandingan harga MSTR vs Composite Fair Value vs 1.0x BTC NAV Floor, serta grafik tren multiple mNAV 90 hari terakhir.
- **Matriks Sensitivitas (Heatmap)**: Simulasi harga wajar MSTR pada berbagai skenario harga Bitcoin (`$55k` – `$200k+`) dan multiple mNAV (`0.85x` – `2.50x`).

---

## Cara Deploy Otomatis ke VPS Biznet Gio

### Opsi A: Satu Perintah dari Lokal ke VPS (`push-to-vps.sh`)
```bash
./push-to-vps.sh <IP_PUBLIC_VPS> root
```

### Opsi B: Jalankan Langsung di Dalam VPS Biznet Gio
```bash
sudo bash deploy-biznetgio.sh
```

### Opsi C: Menggunakan Docker Compose
```bash
docker compose up -d --build
```

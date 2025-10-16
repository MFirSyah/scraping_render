from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
import os
import threading
from urllib.parse import urlparse

# Impor kelas scraper dari file scraper_chrome_rev.py
try:
    from scraper_chrome_rev import ShopeeScraper
except ImportError:
    # Penanganan jika file tidak ditemukan
    print("ERROR: Pastikan file 'scraper_chrome_rev.py' berada di direktori yang sama.")
    exit()

# Inisialisasi aplikasi Flask
app = Flask(__name__)
# Kunci rahasia untuk menampilkan pesan (flash messages)
app.secret_key = 'kunci-rahasia-scraper-anda' 

# Konfigurasi direktori output
# Di Render, gunakan Persistent Disk. Path ini adalah contoh umum.
# Untuk testing lokal, ini akan membuat folder 'hasil_scraping_web'
OUTPUT_DIR = os.environ.get('SCRAPER_OUTPUT_DIR', 'hasil_scraping_web')

# Pastikan direktori output ada saat aplikasi dimulai
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- FUNGSI UNTUK MENJALANKAN SCRAPER ---
def run_scraper_in_background(url, scrape_sold_out):
    """
    Fungsi ini dijalankan di thread terpisah agar tidak memblokir aplikasi web.
    """
    print(f"Memulai thread scraping untuk URL: {url}")
    scraper = ShopeeScraper()
    # Panggil fungsi utama scraper dengan parameter dari web
    scraper.start_scraping_from_web(url=url, scrape_sold_out=scrape_sold_out, output_dir=OUTPUT_DIR)
    print(f"Thread scraping untuk URL: {url} telah selesai.")

# --- HALAMAN-HALAMAN WEB (ROUTES) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """
    Halaman utama yang menampilkan formulir input.
    """
    if request.method == 'POST':
        url = request.form.get('url')
        # Cek apakah checkbox "scrape_sold_out" dicentang
        scrape_sold_out = 'scrape_sold_out' in request.form

        # Validasi URL sederhana
        if not url or not url.startswith("https://shopee.co.id"):
            flash("URL yang Anda masukkan tidak valid. Harap gunakan URL Shopee Indonesia.", "error")
            return redirect(url_for('index'))

        # Tentukan apakah opsi "scraping produk habis" harus ditampilkan
        # Opsi ini hanya relevan untuk URL toko global, bukan pencarian
        parsed_url = urlparse(url)
        is_search_url = '/search' in parsed_url.path
        if is_search_url and scrape_sold_out:
            # Jika ini URL pencarian, paksa scrape_sold_out menjadi False
            scrape_sold_out = False
        
        # Jalankan scraper di background thread
        thread = threading.Thread(target=run_scraper_in_background, args=(url, scrape_sold_out))
        thread.start()

        # Beri pesan sukses kepada pengguna
        flash(f"Proses scraping untuk URL '{url}' telah dimulai di latar belakang. Silakan cek halaman hasil dalam beberapa menit.", "success")
        return redirect(url_for('index'))

    # Tampilkan halaman formulir jika metodenya GET
    return render_template('index.html')

@app.route('/hasil')
def list_results():
    """
    Halaman untuk menampilkan daftar file CSV yang bisa diunduh.
    """
    try:
        # Baca semua file dari direktori output
        files = os.listdir(OUTPUT_DIR)
        # Urutkan file berdasarkan waktu modifikasi (yang terbaru di atas)
        files.sort(key=lambda x: os.path.getmtime(os.path.join(OUTPUT_DIR, x)), reverse=True)
        # Hanya tampilkan file yang berakhiran .csv
        csv_files = [f for f in files if f.endswith('.csv')]
        return render_template('hasil.html', files=csv_files)
    except FileNotFoundError:
        # Tangani jika direktori output tiba-tiba terhapus
        flash("Direktori hasil tidak ditemukan.", "error")
        return redirect(url_for('index'))

@app.route('/download/<path:filename>')
def download_file(filename):
    """
    Endpoint untuk mengunduh file yang dipilih.
    """
    try:
        return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)
    except FileNotFoundError:
        flash("File tidak ditemukan.", "error")
        return redirect(url_for('list_results'))

# --- MENJALANKAN APLIKASI ---
if __name__ == '__main__':
    # 'host="0.0.0.0"' penting agar bisa diakses di jaringan (dan di Render)
    # 'debug=True' hanya untuk development lokal, jangan gunakan di produksi Render
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)

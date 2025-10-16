import time
import random
import logging
import os
import pandas as pd
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import re

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# =====================================================================================
# BLOK KONFIGURASI (Default, bisa di-override)
# =====================================================================================
STATE_FILE_DIR = "/tmp"  # Direktori sementara di server untuk state file
RANDOM_WAIT_RANGE = (2, 8)
MAX_RETRIES = 3
PREVIEW_LIMIT = 1000 # Default preview limit jika tidak dispesifikasikan

# =====================================================================================
# CLASS UTAMA SCRAPER
# =====================================================================================
class ShopeeScraper:
    def __init__(self):
        self.driver = None
        self.output_dir = "Hasil Scraping" # Default, akan di-override oleh Flask
        self.state_file = os.path.join(STATE_FILE_DIR, "scrape_state.log")
        self.setup_logging()

    def setup_logging(self):
        # Logging akan di-setup untuk menampilkan di konsol server
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] [%(levelname)s] - %(message)s',
            handlers=[
                logging.StreamHandler()
            ]
        )

    def setup_headless_chrome(self):
        """
        Inisialisasi driver Chrome untuk berjalan di server (headless).
        """
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            
            # User-Agent untuk meniru browser sungguhan
            user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.54 Safari/537.36'
            chrome_options.add_argument(f'user-agent={user_agent}')
            
            service = Service()
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            logging.info("✅ Berhasil memulai sesi Chrome dalam mode Headless.")
            return True
        except WebDriverException as e:
            # Error ini sering terjadi jika chromedriver tidak terinstal/tidak kompatibel
            logging.error(f"Gagal memulai Chrome Headless. Pastikan ChromeDriver sudah terinstal di server. Error: {e}")
            return False
        except Exception as e:
            logging.error(f"Terjadi error tidak terduga saat inisialisasi Chrome: {e}")
            return False

    def _extract_product_data(self, soup_card, existing_names: set):
        name_element = soup_card.select_one('div.line-clamp-2')
        name = name_element.get_text(strip=True) if name_element else "Nama Tidak Ditemukan"

        if name in existing_names:
            return None

        link_element = soup_card.select_one('a')
        link = "Link Tidak Ditemukan"
        if link_element and link_element.has_attr('href'):
            link = link_element['href']
            if not link.startswith("https://shopee.co.id"):
                link = "https://shopee.co.id" + link
        
        price_element = soup_card.select_one('span.truncate.text-base\\/5')
        price_text = price_element.get_text(strip=True).replace('.', '') if price_element else "0"
        try:
            price = int(re.sub(r'\D', '', price_text))
        except (ValueError, TypeError):
            price = 0

        sold_element = soup_card.select_one('div.truncate.text-shopee-black87.text-xs')
        sold_text = sold_element.get_text(strip=True) if sold_element else "0"
        
        sold_count = 0
        if sold_text and "terjual" in sold_text.lower():
            cleaned_text = sold_text.lower().replace(',', '.').replace('rb', 'k')
            match = re.search(r'([\d.]+)\s*k?', cleaned_text)
            if match:
                try:
                    value = float(match.group(1))
                    if 'k' in cleaned_text:
                        sold_count = int(value * 1000)
                    else:
                        sold_count = int(value)
                except (ValueError, IndexError):
                    sold_count = 0
        
        existing_names.add(name)
        return {"Nama Produk": name, "Harga": price, "Terjual per Bulan": sold_count, "Link": link}

    def _load_all_sold_out_products(self):
        logging.info("Memuat semua produk yang stoknya habis...")
        while True:
            try:
                initial_count = len(self.driver.find_elements(By.CSS_SELECTOR, "div.shop-collection-view__item"))
                see_more_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "div.shop-sold-out-see-more > button.shopee-button-outline"))
                )
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", see_more_button)
                time.sleep(1)
                see_more_button.click()
                delay = random.uniform(2.5, 4.0)
                logging.info(f"➡️  Klik 'Lihat Lainnya'. Menunggu {delay:.2f} detik...")
                time.sleep(delay)
                new_count = len(self.driver.find_elements(By.CSS_SELECTOR, "div.shop-collection-view__item"))
                if new_count == initial_count:
                    logging.info("✅ Jumlah produk tidak bertambah. Semua produk habis sudah dimuat.")
                    break
            except TimeoutException:
                logging.info("✅ Tombol 'Lihat Lainnya' tidak ditemukan lagi. Semua produk habis sudah dimuat.")
                break
            except Exception as e:
                logging.error(f"Error saat klik 'Lihat Lainnya': {e}")
                break

    def scrape_new_products(self, url: str, scrape_mode: str, identifier: str, keyword: str, scrape_sold_out: bool):
        logging.info(f"Memulai proses scraping daftar produk (Mode: {scrape_mode.capitalize()}).")
        
        self.driver.get(url)

        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.shop-search-result-view, ul.shopee-search-item-result__items")))
            logging.info("✅ Halaman produk berhasil dimuat.")
        except TimeoutException:
            logging.error("❌ Gagal memuat halaman atau tidak ada produk ditemukan. Program berhenti.")
            return

        produk_tersedia = []
        produk_habis = []
        seen_names_tersedia = set()
        page_count = 1

        while True:
            logging.info(f"\n📄 Scraping Produk Tersedia - Halaman {page_count}...")
            
            last_height = self.driver.execute_script("return document.body.scrollHeight")
            for _ in range(3):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(2, 3))
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
            
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            product_cards = soup.select("div.shop-search-result-view__item, li.shopee-search-item-result__item")
            
            if not product_cards and page_count > 1:
                logging.info("🏁 Tidak ada kartu produk ditemukan. Menganggap ini halaman terakhir.")
                break
            
            new_data_count = 0
            for card in product_cards:
                data = self._extract_product_data(card, seen_names_tersedia)
                if data:
                    produk_tersedia.append(data)
                    new_data_count += 1
            
            if new_data_count == 0 and page_count > 1:
                logging.info("🏁 Tidak ada produk baru yang ditemukan di halaman ini. Proses selesai.")
                break

            logging.info(f"Menemukan {len(product_cards)} total item di halaman {page_count}. {new_data_count} data baru ditambahkan.")

            try:
                next_button = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.shopee-icon-button--right")))
                self.driver.execute_script("arguments[0].click();", next_button)
                page_count += 1
                delay = random.uniform(RANDOM_WAIT_RANGE[0], RANDOM_WAIT_RANGE[1])
                logging.info(f"➡️  Klik Halaman Berikutnya. Menunggu {delay:.2f} detik...")
                time.sleep(delay)
            except TimeoutException:
                logging.info("🏁 Tombol 'Next' tidak ditemukan atau tidak bisa diklik. Selesai scraping produk tersedia.")
                break
        
        if scrape_sold_out:
            # Logika untuk produk habis
            pass # Anda dapat menambahkan kembali logika ini jika dibutuhkan

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        if produk_tersedia:
            df_tersedia = pd.DataFrame(produk_tersedia)
            filename_ready = ""

            if scrape_mode == 'global':
                filename_ready = f"{timestamp}_{identifier}_PRODUK_READY.csv"
            elif scrape_mode == 'keyword':
                safe_keyword = re.sub(r'[\\/*?:"<>|]', "", keyword)
                filename_ready = f"{timestamp}_{identifier}_{safe_keyword}.csv"

            if filename_ready:
                # Pastikan direktori output ada
                os.makedirs(self.output_dir, exist_ok=True)
                filepath_ready = os.path.join(self.output_dir, filename_ready)
                df_tersedia.to_csv(filepath_ready, index=False, encoding='utf-8-sig')
                logging.info(f"✅ Data Produk Tersedia ({len(df_tersedia)} item) disimpan di: {filepath_ready}")


    def start_scraping_from_web(self, url: str, scrape_sold_out: bool, output_dir: str):
        """
        Fungsi pemicu utama yang akan dipanggil oleh aplikasi web.
        """
        self.output_dir = output_dir # Set direktori output dari parameter
        
        if not self.setup_headless_chrome():
            return # Hentikan jika Chrome gagal dimulai

        try:
            logging.info("="*20 + " PROSES SCRAPING DIMULAI " + "="*20)
            logging.info(f"🔗 URL Target: {url}")
            
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            
            if '/search' in parsed_url.path and 'keyword' in query_params:
                scrape_mode = 'keyword'
                keyword = query_params['keyword'][0]
                identifier = query_params.get('shop', ['unknown_shop'])[0] 
                logging.info(f"✅ Terdeteksi: Link PENCARIAN KATA KUNCI. Kata Kunci: '{keyword}'")
                self.scrape_new_products(url, scrape_mode, identifier, keyword, False) # scrape_sold_out diabaikan untuk pencarian
            else:
                scrape_mode = 'global'
                identifier = parsed_url.path.strip('/').split('?')[0]
                logging.info(f"✅ Terdeteksi: Link TOKO GLOBAL. Username: '{identifier}'")
                self.scrape_new_products(url, scrape_mode, identifier, "", scrape_sold_out)
            
            logging.info("="*20 + " PROSES SCRAPING SELESAI " + "="*20)

        except Exception as e:
            logging.error(f"Terjadi kesalahan fatal selama proses scraping: {e}", exc_info=True)
        finally:
            if self.driver:
                self.driver.quit()
                logging.info("🔌 Sesi browser Chrome telah ditutup.")

# Blok ini bisa digunakan untuk testing lokal jika diperlukan,
# tapi tidak akan dijalankan di server.
if __name__ == '__main__':
    # Contoh penggunaan untuk testing
    scraper_test = ShopeeScraper()
    test_url = "https://shopee.co.id/xiaomi.official.id?sortBy=sales"
    # Menyimpan hasil di folder lokal bernama 'testing_output'
    scraper_test.start_scraping_from_web(url=test_url, scrape_sold_out=False, output_dir="testing_output")

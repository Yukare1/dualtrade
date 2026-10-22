# X haber takibi: aylık en fazla 15 USD

22 Ekim 2026'da hazırlanan bu modül, mevcut DualTrade projesinin yanında çalışan
salt okunur bir araştırma izleyicisidir. Geliştirici hesabı ve yerel Bearer Token
girişi beklenmektedir; canlı API erişimi henüz doğrulanmamıştır.

## X hesabını hazırlama

1. [X Developer Console](https://console.x.com/) üzerinden kendi X hesabınla giriş
   yap. Geliştirici sözleşmesini incele ve kullanım amacını gerçek biçimde açıkla.
   Premium gerekmiyor.
2. Yeni uygulama oluştur; örnek ad: `DualTrade Personal News`.
3. Kullanım amacı için aşağıdaki açıklamayı gözden geçirip kullanabilirsin:

   > I am building a private, read-only personal research tool for US equity news.
   > It monitors recent public posts from selected news organizations and official
   > institutions, using company keywords and a small stock watchlist. Local
   > deterministic rules categorize posts, identify related tickers, and group
   > repeated text for a private research report with source links. X posts are
   > treated as unverified claims and do not generate automated orders. I will
   > not train AI models on X data, redistribute content, post, like, follow, send
   > messages, or profile individuals. The application has a USD 15 monthly cap
   > and short-lived local content storage. I will follow the applicable X terms.

4. Billing / Credits bölümünde harcama sınırını **15 USD / fatura dönemi** yap.
   **Auto-recharge kapalı** kalsın. Başlangıç bakiyesini 15 USD'yi aşmadan seç.
   Bu sınır API kullanım bedelidir; kartın kur farkı/vergi/komisyonu ayrıca olabilir.
5. Uygulamanın Keys and tokens / Credentials bölümünden **Bearer Token** oluştur.
   Herkese açık içerik okumak için bu token yeterlidir; OAuth callback veya yazma
   yetkisi bu modül tarafından kullanılmaz. Tokenı aşağıdaki gizli yerel isteme gir.

Resmî belgeler: [erişim](https://docs.x.com/x-api/getting-started/getting-access),
[ücretler ve sınır](https://docs.x.com/x-api/getting-started/pricing),
[recent search örneği](https://docs.x.com/x-api/posts/search/quickstart/recent-search).
Ekrandaki adımlar hesap durumuna göre değişebilir; uygulama/erişim onayını X verir.

## Yerel anahtar ve çalıştırma

Bu projenin klasöründe PowerShell aç. Tokenı sohbete, kaynak dosyaya veya komut
satırına açık metin olarak yapıştırma; bu komutun gizli istemine gir:

```powershell
.\.venv\Scripts\python.exe -m dualtrade.x_watch setup-key
```

Mevcut Windows CurrentUser DPAPI kasası kullanılır; diğer sağlayıcı anahtarları
korunur. Ortam değişkenindeki `X_BEARER_TOKEN`, varsa kasadaki değerden önceliklidir.
Bu komut sağlayıcıya istek göndermez.

Durumu ücretsiz/çevrimdışı kontrol et:

```powershell
.\.venv\Scripts\python.exe -m dualtrade.x_watch status
```

Araştırma saatleri içinde ilk bütçeli canlı kontrolü yap:

```powershell
.\.venv\Scripts\python.exe -m dualtrade.x_watch check
```

`OK`, arama yanıtının alındığını gösterir; yeni gönderi olmaması normaldir.
`KEY_REQUIRED` anahtarın eksik olduğunu belirtir. `OUTSIDE_RESEARCH_HOURS`
durumunda ağ isteği yapılmaz; canlı doğrulama bir sonraki araştırma aralığını bekler.
İlk kontrol beş hesabın ID eşleştirmesi için en fazla **0,05 USD**, en fazla on
gönderilik arama için **0,05 USD** ayırır. Geçerli boş yanıt gönderi maliyeti tüketmez.
Belirsiz/hatalı yanıtın ayrılan üst bedeli geri bırakılmaz.

Arka planda sürekli çalıştır:

```powershell
.\scripts\start-x-watch.ps1
```

Bu ayrı bir süreçtir; MT5/collector yeniden başlatılmaz. Bilgisayar açık ve
internete bağlı olmalıdır. İkinci izleyici aynı yerel kilidi alamaz. Windows
oturum açılışında otomatik başlatma kaydedilmemiştir. Ön planda çalıştırmak ve
Ctrl+C ile durdurmak için aynı Python yolu ile `-m dualtrade.x_watch run` kullan.

## Hesaplar ve kapsam

Varsayılan ayarlar `x-watch.json` dosyasındadır.

| Hesap | Neden | Kapsam |
|---|---|---|
| [@ReutersBiz](https://x.com/ReutersBiz) | Şirket ve ekonomi haberleri | İzlenen şirket/endeks kelimeleri |
| [@CNBC](https://x.com/CNBC) | ABD piyasası ve şirket haberleri | Aynı kelime filtresi |
| [@federalreserve](https://x.com/federalreserve) | Para politikası duyuruları | Özgün gönderiler |
| [@BLS_gov](https://x.com/BLS_gov) | Enflasyon ve istihdam duyuruları | Özgün gönderiler |
| [@SECGov](https://x.com/SECGov) | Düzenleyici duyurular | Özgün gönderiler; EDGAR'ın yerine geçmez |

Kimlik kaynakları: [Reuters'in yönlendirmesi](https://x.com/Reuters/with_replies?lang=en),
[CNBC profili](https://x.com/CNBC/with_replies),
[Fed](https://www.federalreserve.gov/newsevents/pressreleases/other20120314a.htm),
[BLS](https://www.bls.gov/information-guide/), [SEC](https://www.sec.gov/opa/socialmedia).
Hesap ID'leri ilk bütçeli istekte çözülür ve 30 gün önbellekte tutulur. Kullanıcı
genişletmeleri her aramada tekrar istenmez. X'te hesapları ayrıca takip etmek gerekmez.

Hisse/endeksler: AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD, JPM, XOM, SPY, QQQ.
Bu liste mevcut araştırma evrenidir; yatırım önerisi veya fiyat verisi sağlamaz.
Tüm X'te geniş cashtag taraması yapılmaz. Yanıtlar ve retweetler hariçtir;
alıntılar eşleşirse gelebilir. Kelime filtresi ilgili bazı gönderileri kaçırabilir.

## Bütçe ve gecikme

- 120 saniyelik aralık; işlem günlerinde New York **08:00–XNYS kapanışı**.
  Tatiller, erken kapanışlar ve yaz/kış saati takvimden alınır. İstanbul'da normal
  aralık ABD yaz saatinde **15:00–23:00**, kış saatinde **16:00–00:00** olur.
- Yerel günlük tavan **0,50 USD**. Hem UTC takvim ayı hem son 31 gün için **15 USD**.
  Son 31 gün sınırı farklı fatura dönemi başlangıçlarında temkinli koruma sağlar.
- Her isteğin azami sonucu ağ çağrısından **önce** SQLite işlemiyle bütçeye ayrılır.
  Yeniden başlatma/zaman aşımı kaydı silmez. Yalnızca geçerli ve tam yanıtın kesin
  dönmeyen kısmı serbest bırakılır. Faturadan daha yüksek muhasebeleştirme olabilir.
- Gönderi başına 0,005 USD, kullanıcı çözümlemesi başına 0,010 USD varsayılır.
  X'in aynı gün tekrar ücretlendirmeme davranışına güvenilerek bütçe düşürülmez.
  Tüm uygulamaların toplam faturası için Developer Console sınırı esastır.
- Fiyat inceleme tarihi 30 günü geçerse ücretli sorgular durur. Fiyatlar resmî
  kaynaktan kontrol edilmeden `price_reviewed_on` tarihi yenilenmemelidir.
- Azami 10 gönderilik arama bedeli sığmıyorsa bütçenin son kısmı kullanılmaz.
  Önceki günün bitmemiş sayfaları yeni gün bütçesiyle taranmaz; raporda kapsam
  boşluğu gösterilir. Yoğun günlerde tam kapsama garantisi yoktur.
- İlk açılışta yaklaşık son iki dakikadan başlanır. 15 saniyelik sorgu kesimi,
  X indeksleme gecikmesi, sayfalama ve ağ süresi eklenir. Gece ve borsa kapanışı
  sonrasındaki bilanço haberleri kapsam dışıdır; saniyelik haber terminali değildir.

## Rapor ve mevcut sistem

- `runtime/reports/x-watch.txt`: Türkçe kategori, hisseler, zaman, hesap,
  özgün gönderi ve bağlantı.
- `runtime/reports/x-watch.json`: aynı sonuçlar, bütçe, son başarılı sorgu ve durum.
- `runtime/x-watch.sqlite3`: bütçe/sayfalama ve kısa ömürlü içerik önbelleği.
  **Bütçe dosyasını silme**; silmek harcama geçmişini unutturur.

Yerel kurallar ve Türkçe özet şablonları kullanılır; ücretli LLM API'si veya
doğrulanmış FinBERT sonucu yoktur. Tekrarlanan metinler gruplandırılır; bunun
bağımsız haber teyidi olduğu iddia edilmez. Kaynak belge okunana kadar her gönderi
`UNVERIFIED_X_CLAIM`, `eligible_for_trading=false` kalır. 72 saatten eski içerik
kontrol sırasında temizlenir. İzleyici kapalıysa temizleme sonraki çalışmaya kalır.

X bu aşamada ayrı araştırma raporudur; mevcut Reddit/duygu kaynağının yerine
geçirilmemiştir. Kod revizyonu mevcut kaynak-hash kabulünü geçersiz kılabilir.
Bu kurulum hiçbir kabul kaydını yenilemez ve emir yetkisini açmaz.

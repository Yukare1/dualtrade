# Historical correctness changes

Technical behavior documented during development; not a deployment or acceptance certificate. See KNOWN_ISSUES.md for remaining findings.

1. Haber yorumunun 24 saatlik ömrü ile okunmamış inceleme yükümlülüğü ayrıldı.
   Okunmamış/erişilememiş/expired/needs_manual veya maddi belirsiz kayıtlar zamanla
   kendiliğinden giriş iznine dönüşmez. Eski kayıt tam incelenebilir; orijinal
   yayın/son kullanma zamanları korunur. Eski haberden yeni yön sinyali üretilmez.
2. Şirkete özgü haber sorunu ilgili kontratta tutulur. Ortak Fed kaynak/olay sorunu
   bütün havuzu engeller. Havuzda sağlıklı başka hisse varsa onun değerlendirmesi
   bağımsız ilerleyebilir.
3. Stop güncellemesi hem broker dönüş kodu hem pozisyondaki gerçek SL/TP ile
   doğrulanır. Başarısızlık kayda girer; eski stop gevşetilmez ve sonraki timer
   tekrar değerlendirebilir.
4. Geçmiş/gösterge/fiyat verisi hazır olmadan giriş mumu tüketilmez. Aynı mumun
   özgün 60 saniyelik penceresinde tekrar denenebilir; değerlendirilen mum,
   başlangıç/restart koruması ve tek kullanım kuralları korunur.
5. İşlem agent'ına tur içinde en fazla 50 saniyelik, yarım saniyelik dosya
   beklemesi eklendi. Dakikalık uyanmanın yeni mumu kaçırması azaltılır. Adayın
   55 saniyelik, fiyatın 30 saniyelik ve mumun 60 saniyelik sınırları korunur.
6. İşlem kanıtı haber bağlamının hash'ine bağlandı. İnceleme sırasında haber
   bağlamı değişirse eski onay reddedilir. Yayımlanan giriş kararı ve denetim
   kaydı kilitle korunur; mevcut karara yeniden yazılamaz.
7. Haber agent'ında sahiplik token'ı ve yenileme eklendi. Değiştirilmiş bir lease'i
   eski okuyucu tamamlayamaz. Eşzamanlı completion işlemleri tek transaction ile
   serileştirilir; belirsiz analiz yeniden ele alınırken değişiklik karşılaştırılır.
8. Yavaş veri toplama çevrimi, gözlem kanıtına sayılmadan önce reddedilir.


Validate proposed changes in an isolated checkout. Native integration tests are separate from unit and compatibility-host tests. No local account state or activation records are included.

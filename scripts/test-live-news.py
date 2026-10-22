"""Quota-counted real-news observation or offline replay; no EA snapshot changes."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
from html import escape
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dualtrade.config import ROOT, load
from dualtrade.credentials import load_local_credentials
from dualtrade.news_guard import news_report, policy_signature
from dualtrade.providers import Providers, ProviderError
from dualtrade.storage import Store, atomic_write, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--baseline-report', type=Path)
    args = parser.parse_args()
    cfg = load()
    store = Store(ROOT / 'runtime/state.sqlite3')
    started = time.time()
    batches = [('AAPL', 'MSFT', 'NVDA'), ('TSLA', 'AMZN', 'META'),
               ('AMD', 'JPM', 'XOM'), ('SPY', 'QQQ')]
    requests = []
    items = []
    if args.observations:
        prior = json.loads(args.observations.read_text(encoding='utf-8'))
        items = prior['items']
        requests = prior['requests']
        latest = {tuple(entry['symbols']): entry for entry in requests}
        batches = [batch for batch in batches if args.retry_failed and not latest.get(batch, {}).get('ok')]
    if batches:
        load_local_credentials()
    provider = Providers(cfg, store)
    for batch in batches:
        attempt = time.time()
        try:
            rows = provider.news(list(batch))
            requests.append({'symbols': list(batch), 'attempted_at': attempt,
                             'ok': True, 'entity_rows': len(rows),
                             'coverage': getattr(provider, 'last_news_coverage', {})})
            items.extend(rows)
        except Exception as exc:
            requests.append({'symbols': list(batch), 'attempted_at': attempt, 'ok': False,
                             'error': str(exc) if isinstance(exc, ProviderError) else type(exc).__name__})
    # Each symbol/article association is a separate assessment, never a separate
    # independent publisher. Preserve the actual source fields and retrieval time.
    unique = {(row['ticker'], row['cluster']): row for row in items}
    observed = list(unique.values())
    finished = time.time()
    report = {'started_at': started, 'finished_at': finished,
              'provider': 'Marketaux', 'policy_signature': policy_signature(),
              'network_requests_this_run': len(batches),
              'mode': 'fresh_api' if not args.observations else 'saved_observations_with_retries' if batches else 'saved_observations',
              'requests': requests, 'raw_entity_rows': len(items),
              'unique_articles': len({row['url'] for row in observed}),
              'assessment': news_report(observed, finished),
              'method': 'Current authenticated API observations; original titles, scores and timestamps; no simulated headlines or retimed publication dates.',
              'selection': 'Latest provider results for four declared symbol batches, at most three articles per batch; any retries are preserved in requests. Not a representative accuracy benchmark.',
              'execution_effect': 'None: isolated report only; no pool, feed-health, acceptance, order permission or EA snapshot is changed.'}
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding='utf-8'))
        baseline_path = args.baseline_report.with_name(args.baseline_report.stem+'-observations.json')
        baseline_rows = json.loads(baseline_path.read_text(encoding='utf-8'))['items']
        # Same data and original evaluation time isolate the effect of the rules.
        replay = news_report(baseline_rows, baseline['assessment']['evaluated_at'])
        report['comparison'] = {'method': 'Same recorded API rows and same evaluation time; only the policy changes.',
                                'baseline_report': str(args.baseline_report),
                                'before': baseline['assessment'], 'after': replay}
    folder = ROOT / 'runtime/reports'
    stamp = datetime.fromtimestamp(finished, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    raw_path = folder / f'news-live-{stamp}-observations.json'
    report_path = folder / f'news-live-{stamp}.json'
    write_json(raw_path, {'provider': 'Marketaux', 'requests': requests, 'items': observed})
    write_json(report_path, report)
    reasons = {
        'UNKNOWN_SOURCE_DOMAIN': 'Kaynak alan adı incelenmiş yayıncı listesinde yok',
        'INVALID_ARTICLE_URL': 'Geçerli HTTPS makale bağlantısı yok',
        'UNKNOWN_PROVIDER': 'Veri sağlayıcısı desteklenmiyor',
        'STALE_OR_INVALID_TIMESTAMPS': 'Haber 72 saatten eski veya tarih bilgisi geçersiz',
        'INVALID_SENTIMENT': 'Duygu puanı geçersiz',
        'MISSING_HEADLINE': 'Başlık eksik',
        'AMBIGUOUS_OR_MISSING_SUBJECT': 'Başlık tek bir izlenen şirketle açıkça eşleşmiyor',
        'SATIRE_OR_MANIPULATION': 'Parodi veya manipülasyon ifadesi bulundu',
        'SPECULATION_OR_OPINION': 'Söylenti, tahmin veya yorum ifadesi bulundu',
        'DISPUTED_OR_CORRECTED': 'Yalanlama veya düzeltme ifadesi bulundu',
        'UNSUPPORTED_OR_AMBIGUOUS_CLAIM': 'İddia, mevcut dar olay kalıplarıyla çözümlenemedi',
        'INDEPENDENT_CORROBORATION_MISSING': 'İki bağımsız yayın grubundan eşleşen teyit yok',
        'CONFLICTING_REPORTING': 'Kaynaklar arasında çelişen raporlama bulundu',
        'MATCHED_INDEPENDENT_REPORTING': 'İki bağımsız yayın grubundan benzer iddia eşleşti',
        'REVIEWED_SOURCE_REPORTING': 'İncelenmiş yayıncıdan haber; tek kaynak düşük ağırlıkla kabul',
        'PRESS_RELEASE_NOT_INDEPENDENT': 'Basın bülteni araştırmada tutuldu; bağımsız teyit veya veto sayılmaz',
        'ANALYSIS_NOT_CONFIRMED_EVENT': 'Analist görüşü veya fiyat senaryosu; araştırmada tutuldu, veto üretmez',
        'MARKET_OR_RELATED_COMPANY_CONTEXT': 'Piyasa veya dolaylı şirket bağlamı; araştırmada tutuldu',
        'CLAIM_REQUIRES_CONFIRMATION': 'Söylenti, yalanlama veya düzeltme; teyit bekleyen bağlam',
        'OLDER_THAN_ACTIVE_WINDOW': '48–72 saatlik haber yalnızca araştırma bağlamında tutuldu',
        'ORIGINAL_STORY_OUTSIDE_ACTIVE_WINDOW': 'Aynı olayın ilk haberi 48 saatten eski; kopya tarihini yenilemez',
        'UNCONFIRMED_CLAIM': 'İddia henüz doğrulanmamış',
        'DISPUTED_OR_CORRECTED': 'Yalanlama veya düzeltme var; teyit bekleniyor',
        'GENERAL_REPORTING_NO_STRUCTURED_CLAIM': 'Genel şirket haberi; yapılandırılmış iddia teyidi yapılmadı',
    }
    statuses = {'corroborated_reporting': 'KABUL · bağımsız kaynak eşleşmesi', 'single_source': 'KABUL · tek kaynak',
                'analysis_context': 'KABUL · analiz bağlamı', 'press_release_context': 'KABUL · basın bülteni',
                'context_only': 'KABUL · yalnızca bağlam', 'unverified': 'KAYNAK DOĞRULANMADI',
                'excluded': 'DIŞARIDA', 'conflicting': 'ÇELİŞKİLİ'}
    table = []
    for row in report['assessment']['articles']:
        check = row['credibility']
        published = datetime.fromtimestamp(row['time'], ZoneInfo('Europe/Istanbul')).isoformat()
        reason = '; '.join(reasons.get(code, code) for code in check['reasons']+check.get('warnings', []))
        active = 'Evet; tek başına yeterli değil' if check.get('veto_eligible') else 'Hayır'
        table.append('<tr><td>' + escape(str(row['ticker'])) + '</td><td><a href="' + escape(str(row['url']), quote=True) + '">' + escape(str(row['title'])) + '</a></td><td>' + escape(published) + '</td><td>' + escape(statuses[check['status']]) + '</td><td>' + escape(active) + '</td><td>' + escape(reason) + '</td></tr>')
    comparison = ''
    if 'comparison' in report:
        before, after = report['comparison']['before'], report['comparison']['after']
        original_count = len({r['url'] for r in before['articles']})
        original_accepted = len({r['url'] for r in before['articles'] if r['credibility']['eligible']})
        comparison = ('<h2>Aynı gerçek örneklerle önce / sonra</h2><p>Özgün örneklem ' + str(original_count) + ' haber bağlantısından oluşur. Haberler ve değerlendirme zamanı sabit tutuldu. Önceki kabul: '
                      + str(original_accepted) + '; yeni araştırma kabulü: ' + str(after['accepted_articles'])
                      + '; veto hesabına girebilen olay: ' + str(after['veto_events'])
                      + '. Araştırma kabulü, haberin doğru olduğunun kanıtı veya tek başına işlem izni değildir.</p>')
    content = ('<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
               '<title>Gerçek haberlerle filtre testi</title><style>body{font:16px system-ui;background:#f5f7fb;color:#172138;max-width:1400px;margin:36px auto;padding:0 24px}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;border:1px solid #dce1eb;padding:12px;vertical-align:top}th{background:#e9edf5}a{color:#1756aa}code{overflow-wrap:anywhere}</style>'
               '<h1>Gerçek haberlerle filtre testi · sürüm 2</h1><p>Marketaux API’den alınan özgün başlıklar, puanlar ve tarihler değerlendirildi. Haberlerin tarihi veya içeriği değiştirilmedi. Bu rapor emir üretmez.</p>'
               '<p><strong>Sonuç:</strong> ' + str(report['unique_articles']) + ' farklı haber bağlantısı, ' + str(len(report['assessment']['articles'])) + ' hisse/haber değerlendirmesi; '
               + str(report['assessment']['accepted_articles']) + ' haber araştırmaya kabul edildi. Veto hesabına girebilen farklı olay: ' + str(report['assessment']['veto_events']) + '.</p>'
               '<p>72 saat saklama; ilk 48 saatte uygun haberler yaşlandıkça azalan ağırlıkla kullanılabilir. Ağırlık 24 saatte yarıya iner. Analiz, basın bülteni ve 48–72 saatlik haberler bağlam olarak tutulur. Kaynak incelemesi yayıncı kimliğini belirler; içerik doğruluğunu garanti etmez.</p>'
               '<p>“Dışarıda” veya “kaynak doğrulanmadı”, haberin yalan olduğu anlamına gelmez. Gerçek doğruluk etiketleri bulunmadığından doğruluk yüzdesi hesaplanmadı. API sonuçları sorgu başına en fazla üç haberle sınırlı; eksik kapsam istek kayıtlarında korunur.</p>'
               + comparison +
               '<p>Değerlendirme: ' + escape(datetime.fromtimestamp(finished, ZoneInfo('Europe/Istanbul')).isoformat()) + '. Tablodaki yayın zamanları Türkiye saatiyle gösterilir.</p>'
               '<table><thead><tr><th>Hisse</th><th>Özgün başlık / kaynak bağlantısı</th><th>Yayın tarihi</th><th>Araştırma kararı</th><th>Veto hesabına girebilir mi?</th><th>Gerekçe</th></tr></thead><tbody>' + ''.join(table) + '</tbody></table></html>')
    html_path = folder / f'news-live-{stamp}.html'
    atomic_write(html_path, content)
    print(json.dumps({'report': str(report_path), 'observations': str(raw_path), 'html': str(html_path),
                      'requests': requests, 'unique_articles': report['unique_articles'],
                      'assessment': report['assessment']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()

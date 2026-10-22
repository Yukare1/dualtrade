"""Explicit labelled-evidence acceptance; never enables a veto by assumption."""
from pathlib import Path
from math import isfinite
from .config import config_hash
from .storage import read_json,write_json


def validate_sentiment(cfg, providers, labelled_path):
    examples=read_json(Path(labelled_path),[])
    results={};passed=True
    for source in ('news','social'):
        rows=[x for x in examples if x.get('source')==source]
        counts={label:sum(x.get('label')==label for x in rows) for label in (-1,0,1)}
        correct=0;selected=0;wrong_veto=0;directional=0
        for row in rows:
            label=row.get('label')
            if label not in (-1,0,1):continue
            if source=='social':score,reliable=providers.classify(row['text'])
            else:
                if not row.get('url') or not row.get('retrieved_at') or row.get('provider')!='Marketaux':raise ValueError('News labels must refer to actual retrieved Marketaux evidence')
                score=float(row['score'])
                if isinstance(row['score'],bool) or not isfinite(score) or not -1<=score<=1:raise ValueError('Invalid news sentiment score')
                # Validate the classifier independently from factual credibility.
                # News admission is decided by news_guard at the policy boundary.
                reliable=row.get('sentiment_valid') is True
            prediction=1 if score>=.5 else -1 if score<=-.5 else 0
            correct+=prediction==label
            if reliable:selected+=1
            if reliable and prediction:
                directional+=1;wrong_veto+=prediction!=label
        accuracy=correct/len(rows) if rows else 0
        precision=1-wrong_veto/directional if directional else 0
        ok=min(counts.values())>=10 and accuracy>=.8 and precision>=.9 and selected>=len(rows)/2
        results[source]={'counts':counts,'accuracy':accuracy,'directional_precision':precision,'reliable_count':selected,'passed':ok}
        passed &= ok
    signature=providers.model_signature() if passed else None
    report={'passed':bool(passed and signature),'model_signature':signature,'config_hash':config_hash(cfg),'sources':results,'labels_file':str(Path(labelled_path).resolve())}
    write_json(Path(cfg['research']['sentiment_validation_file']),report)
    return report

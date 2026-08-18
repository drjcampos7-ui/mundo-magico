import os, json, time, hashlib, unicodedata, io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from pypdf import PdfReader

YEAR=int(os.environ['YEAR'])
OUT=Path('out'); (OUT/'text').mkdir(parents=True,exist_ok=True)
BASE='https://fnet.bmfbovespa.com.br/fnet/publico/pesquisarGerenciadorDocumentosDados'
DL='https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento'
QUARTERS=[(1,1,3,31),(2,4,6,30),(3,7,9,30),(4,10,12,31)]

def get_same(url, params, timeout=60, attempts=10):
    last=None
    for k in range(attempts):
        try:
            r=requests.get(url,params=params,timeout=timeout,allow_redirects=True)
            if r.status_code < 500 and r.status_code != 429:
                r.raise_for_status(); return r
            last=RuntimeError(f'HTTP_{r.status_code}')
        except Exception as e:
            last=e
        time.sleep(min(12,1.0*(k+1)))
    raise last

def enum_quarter(q,sm,em,ed):
    start=f'01/{sm:02d}/{YEAR}'; end=f'{ed:02d}/{em:02d}/{YEAR}'
    common=dict(d=0,tipoFundo=1,administrador='',idCategoriaDocumento=0,idTipoDocumento=0,idEspecieDocumento=0,situacao='',cnpjFundo='',dataReferencia='',dataInicial=start,dataFinal=end,idModalidade='',palavraChave='')
    offset=0; expected=None; rows=[]; pages=[]
    while expected is None or len(rows)<expected:
        r=get_same(BASE,common|{'s':offset,'l':50}); j=r.json()
        if expected is None: expected=int(j.get('recordsFiltered',0))
        batch=j.get('data',[]) or []; pages.append({'s':offset,'received':len(batch)})
        if not batch: break
        rows.extend(batch); offset += len(batch)
        time.sleep(0.02)
    if len(rows)!=expected: raise RuntimeError(f'INCOMPLETE {YEAR} Q{q} expected={expected} got={len(rows)}')
    return q,start,end,expected,rows,pages

audits=[]; active={}; lineage={}
for qdef in QUARTERS:
    q,start,end,expected,rows,pages=enum_quarter(*qdef)
    fr=[r for r in rows if str(r.get('categoriaDocumento',''))=='Fato Relevante']
    afr=[r for r in fr if str(r.get('situacaoDocumento',''))=='A']
    audits.append({'year':YEAR,'quarter':q,'date_start':start,'date_end':end,'recordsFiltered_first':expected,'rows_collected':len(rows),'pagination_complete':len(rows)==expected,'pages':len(pages),'page_log':pages,'fato_relevante_all':len(fr),'fato_relevante_active':len(afr)})
    for r in fr: lineage.setdefault(str(r.get('id')),r)
    for r in afr: active.setdefault(str(r.get('id')),r)

def download_one(item):
    did,r=item
    rec={k:r.get(k) for k in ['id','descricaoFundo','nomePregao','informacoesAdicionais','cnpjFundo','dataEntrega','dataReferencia','categoriaDocumento','tipoDocumento','especieDocumento','situacaoDocumento','status','versao','descricaoStatus','fundoOuClasse']}
    rec['document_id']=did; rec['download_url']=DL+'?id='+did
    try:
        rr=get_same(DL,{'id':did},timeout=60,attempts=6); b=rr.content
        rec['byte_count']=len(b); rec['raw_sha256']=hashlib.sha256(b).hexdigest(); rec['content_type']=rr.headers.get('content-type','')
        text=''; method=''; err=None
        try:
            if b.startswith(b'%PDF') or 'pdf' in rec['content_type'].lower():
                reader=PdfReader(io.BytesIO(b),strict=False); text='\n'.join((pg.extract_text() or '') for pg in reader.pages); method='pypdf'
            else:
                ct=rec['content_type'].lower()
                if ('text/' in ct) or ('html' in ct) or b.lstrip().startswith((b'<',b'{',b'[')):
                    text=b.decode(rr.encoding or 'utf-8',errors='replace'); method='decode'
                else: err='UNSUPPORTED_BINARY_FORMAT'
        except Exception as e: err=type(e).__name__+':'+str(e)
        text=unicodedata.normalize('NFC',text).strip(); ok=len(text)>=20
        rec.update({'extraction_method':method or None,'extraction_ok':ok,'text_char_count':len(text),'extraction_error':None if ok else (err or 'EMPTY_OR_TOO_SHORT')})
        if ok: (OUT/'text'/f'{did}.txt').write_text(text,encoding='utf-8')
    except Exception as e:
        rec.update({'byte_count':None,'raw_sha256':None,'content_type':None,'extraction_method':None,'extraction_ok':False,'text_char_count':0,'extraction_error':'DOWNLOAD:'+type(e).__name__+':'+str(e)})
    return rec

results=[]
with ThreadPoolExecutor(max_workers=6) as ex:
    futs=[ex.submit(download_one,it) for it in sorted(active.items(),key=lambda x:int(x[0]))]
    for f in as_completed(futs): results.append(f.result())
results.sort(key=lambda r:int(r['document_id']))
lin=[{k:r.get(k) for k in ['id','descricaoFundo','nomePregao','informacoesAdicionais','cnpjFundo','dataEntrega','dataReferencia','categoriaDocumento','tipoDocumento','especieDocumento','situacaoDocumento','status','versao','descricaoStatus']} for _,r in sorted(lineage.items(),key=lambda x:int(x[0]))]
(OUT/f'quarter_audit_{YEAR}.json').write_text(json.dumps(audits,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
with (OUT/f'fatos_relevantes_active_{YEAR}.jsonl').open('w',encoding='utf-8') as f:
    for r in results: f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
with (OUT/f'fatos_relevantes_lineage_{YEAR}.jsonl').open('w',encoding='utf-8') as f:
    for r in lin: f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
summary={'year':YEAR,'quarters_complete':all(a['pagination_complete'] for a in audits),'global_rows':sum(a['rows_collected'] for a in audits),'active_fr_unique':len(results),'active_fr_extracted':sum(bool(r.get('extraction_ok')) for r in results),'active_fr_extraction_failed':sum(not bool(r.get('extraction_ok')) for r in results)}
(OUT/f'summary_{YEAR}.json').write_text(json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
hs=[]
for p in sorted(OUT.rglob('*')):
    if p.is_file() and p.name!='SHA256SUMS.txt': hs.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(OUT)))
(OUT/'SHA256SUMS.txt').write_text('\n'.join(hs)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
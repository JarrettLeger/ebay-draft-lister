import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { Settings, type Config } from './Settings';

type Listing = {player:string;set_name:string;year:number|null;team:string;parallel:string;card_number:string;print_run:number|null;is_rookie:boolean;is_auto:boolean;is_relic:boolean;title:string;description:string;price:string|null;category_id:string;condition_id:string;reviewed:boolean};
type Card = {id:string;front:string;back:string|null;original:string;state:string;listing:Listing;warnings:string[];issues:string[];error:string|null;archive_warnings?:string[];archived_photos?:Record<string,string>;last_export_id?:string;back_original?:string;front_url?:string|null;back_url?:string|null};
type Job = {id:string;operation:'extract'|'upload';ids:string[];state:string;completed:number;failed:number;current:string|null;provider?:'openai'|'claude';model?:string};
async function request(path:string, options:RequestInit={}) {
  const response = await fetch('/api'+path, options);
  if (!response.ok) { const body = await response.json().catch(()=>({detail:response.statusText})); throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)); }
  return response;
}
const json = (body:unknown) => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
function App() {
  const [cards,setCards]=useState<Card[]>([]), [active,setActive]=useState(''), [draft,setDraft]=useState<Listing|null>(null);
  const [busy,setBusy]=useState(false), [message,setMessage]=useState(''), [filter,setFilter]=useState('all'), [backId,setBackId]=useState('');
  const [selected,setSelected]=useState<string[]>([]), [config,setConfig]=useState<Config|null>(null);
  const [showSettings,setShowSettings]=useState(false),[pairing,setPairing]=useState('consecutive');
  const [syncing,setSyncing]=useState(false);
  const [search,setSearch]=useState(''),[sort,setSort]=useState('recent'),[zoom,setZoom]=useState<string|null>(null);
  const providerName=config?.provider==='claude'?'Claude':'OpenAI';
  const [jobs,setJobs]=useState<Job[]>([]);
  const job=jobs[0];
  const batchActive=!!job && ['running','cancelling'].includes(job.state);
  const card=cards.find(c=>c.id===active);
  const locked=!!card && ['queued','processing'].includes(card.state);
  const dirty=!!card && JSON.stringify(draft)!==JSON.stringify(card.listing);
  const matches=(c:Card)=>{
    const stateMatch=filter==='all'||filter===c.state||
      (filter==='attention'&&(c.issues.length>0||c.state==='failed'||!!c.archive_warnings?.length))||
      (filter==='unpriced'&&c.listing.price===null)||(filter==='unreviewed'&&!c.listing.reviewed)||
      (filter==='upload_needed'&&(!c.front_url||(!!c.back&&!c.back_url)))||
      (filter==='archive_warning'&&!!c.archive_warnings?.length);
    const haystack=[c.id,c.original,c.back_original,...Object.values(c.listing)].join(' ').toLowerCase();
    return stateMatch&&search.toLowerCase().trim().split(/\s+/).every(term=>haystack.includes(term));
  };
  const visible=cards.filter(matches).sort((a,b)=>sort==='player'?a.listing.player.localeCompare(b.listing.player):sort==='filename'?a.original.localeCompare(b.original,undefined,{numeric:true}):0);
  const position=visible.findIndex(c=>c.id===active);
  const invalidPrice=!!draft&&(!/^\d+(?:\.\d{1,2})?$/.test(String(draft.price??''))||Number(draft.price)<=0||Number(draft.price)>99999999);
  const liveIssues=card&&draft?[
    ...(['player','set_name','title','description'] as const).filter(k=>!draft[k].trim()).map(k=>'Missing '+k),
    ...(invalidPrice?['Set a positive manual price with at most 2 decimal places']:[]),
    ...(!draft.reviewed?['Review listing and photo pairing']:[]),
    ...(!card.front_url||(card.back&&!card.back_url)?['Upload all photos']:[]),
    ...(locked?['Wait for processing to finish']:[])
  ]:[];
  useEffect(()=>{
    const native=window as Window & {confirmAppClose?:()=>boolean};
    native.confirmAppClose=()=>{
      const reasons=[...(dirty?['unsaved edits']:[]),...(busy||syncing?['an import, export, or request still running']:[]),...(batchActive?['a batch still running']:[])];
      return !reasons.length||window.confirm('Close eBay Draft Lister with '+reasons.join(' and ')+'? Saved inventory stays safe, but unfinished work may need a retry.');
    };
    return()=>{delete native.confirmAppClose;};
  },[dirty,busy,syncing,batchActive]);
  useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape')setZoom(null);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[]);
  async function saveListing(advance=false){
    if(!card||!draft)return;
    const next=advance?visible[position+1]?.id:undefined;
    if(dirty)await request(`/cards/${card.id}`,{...json(draft),method:'PUT'});
    await refresh(next||active);setSelected(s=>s.filter(id=>id!==card.id));
    setMessage(advance&&!next?'Saved. End of the filtered list.':'Changes saved.');
  }

  async function refresh(id=active) { const data:Card[]=await (await request('/cards')).json(); setCards(data); const next=data.find(c=>c.id===id)||data[0]; setActive(next?.id||''); setDraft(next?.listing||null); }
  useEffect(()=>{ refresh().catch(e=>setMessage(e.message)); request('/config').then(r=>r.json()).then(setConfig).catch(e=>setMessage(e.message)); },[]);
  useEffect(()=>{
    let stopped=false, fetching=false;
    async function poll() {
      if(fetching) return;
      fetching=true;
      try {
        const [next, inventory]:[Job[], Card[]]=await Promise.all([
          request('/jobs').then(r=>r.json()), request('/cards').then(r=>r.json())
        ]);
        if(stopped)return;
        setCards(inventory);
        if(!dirty) { const current=inventory.find(c=>c.id===active); if(current)setDraft(current.listing); }
        setJobs(next);
      } catch(e) {if(!stopped)setMessage(e instanceof Error?e.message:'Could not refresh batch progress');}
      finally {fetching=false;}
    }
    poll();
    const timer=setInterval(poll,1500);
    return ()=>{stopped=true;clearInterval(timer);};
  },[active,dirty]);
  async function run(work:()=>Promise<void>) { setBusy(true); setMessage(''); try {await work();} catch(e) {setMessage(e instanceof Error?e.message:'Request failed');} finally {setBusy(false);} }
  function choose(c:Card) { if(dirty && !confirm('Discard unsaved edits?')) return; setActive(c.id);setDraft({...c.listing});setBackId(''); }
  async function operation(op:string) { if(!card)return; await request(`/cards/${card.id}/${op}`,json({})); await refresh(); }
  async function batch(operation:'extract'|'upload') {
    const started:Job=await (await request('/jobs',json({ids:selected,operation}))).json();
    setJobs([started,...jobs]);setSelected([]);await refresh();
  }
  const field=(key:keyof Listing,label:string,type='text')=><label key={key}>{label}<input type={type} step={key==='price'?'0.01':undefined} min={key==='price'?'0.01':undefined} max={key==='price'?'99999999':undefined} value={String(draft?.[key]??'')} onChange={e=>setDraft(d=>d&&({...d,[key]:type==='number'?(e.target.value===''?null:Number(e.target.value)):e.target.value}))}/></label>;
  return <div className="shell"><aside><div className="brand"><span>◈</span> DRAFT LISTER <small>V2 · LOCAL WORKSPACE</small></div><nav><strong>▦ &nbsp; Card inventory</strong><p>Import → Pair → Review → Export</p></nav><div className="connection"><h4>Connections</h4><p><i className={config?.ready?'on':''}/> {providerName} {config?.ready?'configured':'not configured'}</p><p><i className={config?.cloudinary?'on':''}/> Cloudinary {config?.cloudinary?'configured':'not configured'}</p><small>Model: {config?.model||'Loading…'}</small><button className="settings-button" disabled={!config||busy} onClick={()=>setShowSettings(true)}>Settings & connections</button></div><footer>Photos stay on this computer until you choose extraction or upload.</footer></aside>
  <main><header><div><div className="eyebrow">YOUR LISTING WORKBENCH</div><h1>Card inventory</h1><p>Turn your card photos into drafts worth reviewing.</p></div><label className={'button primary '+(busy?'disabled':'')}>＋ Import photos<input hidden disabled={busy||dirty||locked} type="file" multiple accept=".jpg,.jpeg,.png,.webp,.heic,.heif" onChange={e=>{const files=e.target.files;if(!files?.length)return; const chosen=Array.from(files);run(async()=>{for(let offset=0;offset<chosen.length;offset+=100){const part=new FormData();chosen.slice(offset,offset+100).forEach(f=>part.append('files',f));setMessage(`Importing photos ${offset+1}–${Math.min(offset+100,chosen.length)} of ${chosen.length}…`);await request('/import',{method:'POST',body:part});}await refresh();setMessage('Imported. Select a front photo and pair its back below.');});e.target.value='';}}/></label></header>
  <section className="folder-sync"><div><strong>Photo source folder</strong><p>{config?.source_folder||'Loading…'}</p><small>Sync adds new or changed images. Sync leaves originals in place; export moves them to Done. No 100-photo limit.</small></div><div><label>Pairing suggestions<select value={pairing} disabled={busy||syncing} onChange={e=>setPairing(e.target.value)}><option value="consecutive">Match filenames, then consecutive photos</option><option value="named">Match front/back filenames only</option><option value="none">Keep photos separate</option></select></label><div className="actions"><button className="primary" disabled={busy||dirty||syncing||!config} onClick={()=>run(async()=>{setSyncing(true);try{const result=await (await request('/sync',json({pairing}))).json();await refresh();setSelected([]);setMessage(`Synced ${result.scanned} photos: ${result.imported} new, ${result.skipped} already imported, ${result.paired} suggested pairs. ${result.errors.length} skipped with errors.`+(result.errors.length?' '+result.errors.slice(0,5).map((e:{file:string;error:string})=>e.file+': '+e.error).join('; '):''));}finally{setSyncing(false);}})}>{syncing?'Syncing folder…':'Sync folder'}</button><button disabled={busy||!config} onClick={()=>setShowSettings(true)}>Change folder / API keys</button></div></div></section>
  <p className="muted">Consecutive pairs follow filename order and may be wrong if photos are missing. Review suggested pairs before identification or export. Sync runs when you click it; it does not watch the folder automatically.</p>
  <section className="stats">{[['Total cards',cards.length],['Needs attention',cards.filter(c=>c.issues.length>0).length],['Ready to export',cards.filter(c=>!c.issues.length&&c.state!=='exported').length],['Exported',cards.filter(c=>c.state==='exported').length]].map(([label,value])=><div key={label}><small>{label}</small><b>{value}</b></div>)}</section>
  {config&&!config.provider&&<div className="notice" role="alert">The app server is still running an older version. Close its command window, reopen launch-windows.cmd, then refresh this page to enable Claude settings.</div>}
  {message&&<div role="status" className="notice">{message}</div>}
  <div className="toolbar"><input className="inventory-search" type="search" aria-label="Search inventory" placeholder="Search player, set, card number or filename…" value={search} onChange={e=>{setSearch(e.target.value);setSelected([]);}}/><select aria-label="Filter inventory" value={filter} onChange={e=>{setFilter(e.target.value);setSelected([]);}}><option value="all">All cards</option><option value="attention">Needs attention</option><option value="unpriced">Needs a price</option><option value="unreviewed">Not reviewed</option><option value="upload_needed">Photos not uploaded</option><option value="archive_warning">Archive warnings</option>{['imported','queued','processing','needs_review','ready','failed','exported'].map(s=><option key={s}>{s}</option>)}</select><select aria-label="Sort inventory" value={sort} onChange={e=>setSort(e.target.value)}><option value="recent">Newest first</option><option value="player">Player name</option><option value="filename">Photo order</option></select><span>{visible.length} of {cards.length} cards · {selected.length} selected</span><button disabled={busy||!selected.length||dirty||selected.some(id=>{const c=cards.find(c=>c.id===id);return !c||c.issues.length>0||['queued','processing'].includes(c.state);})} onClick={()=>run(async()=>{const response=await request('/export',json({ids:selected}));const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='eBay_Drafts.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setSelected([]);await refresh();setMessage(Number(response.headers.get('X-Archive-Warnings'))?'CSV created and recovery copy saved. Some original photos could not be moved; open the card’s archive notes and use Retry photo archive.':'CSV created and recovery copy saved. Original photos renamed and moved to Done. Review drafts in Seller Hub before publishing.');})}>Export selected CSV ↓</button></div>
  <p className="muted">Export saves a recovery CSV, then renames the selected cards’ original synced photos and moves them into their source folder’s Done directory. Unfinished cards stay in place. Select visible skips previously exported cards.</p>
  <div className="batch-actions"><button disabled={busy||dirty||batchActive} onClick={()=>setSelected(visible.filter(c=>!['queued','processing','exported'].includes(c.state)).map(c=>c.id))}>Select visible</button><button disabled={busy||!selected.length} onClick={()=>setSelected([])}>Clear selection</button><button disabled={busy||dirty||batchActive||!selected.length||!config?.ready} onClick={()=>run(()=>batch('extract'))}>Identify selected</button><button disabled={busy||dirty||batchActive||!selected.length||!config?.cloudinary} onClick={()=>run(()=>batch('upload'))}>Upload selected photos</button></div>
  <p className="muted">Batch identification sends selected photos to {providerName} and replaces extracted fields. Upload sends photos to Cloudinary. Each card still requires manual review and pricing.</p>
  {job&&<section className="batch-progress" aria-label="Batch progress" aria-live="polite"><div><strong>{job.operation==='extract'?`Card identification${job.provider?' · '+(job.provider==='claude'?'Claude':'OpenAI'):''}`:'Photo upload'} · {job.state}</strong><p>{job.completed} of {job.ids.length} finished · {job.failed} failed · {job.ids.length-job.completed} {batchActive?'remaining':'not processed'}</p><progress max={job.ids.length} value={job.completed}/><small>{job.state==='cancelling'?'Finishing the current card; remaining cards will be skipped.':job.state==='interrupted'?'Batch interrupted. Completed work was saved. Select unfinished cards to retry.':batchActive?'You can browse other cards while this batch runs.':'Completed work is saved. Review warnings and retry failed cards as needed.'}</small></div>{batchActive&&<button disabled={busy||job.state==='cancelling'} onClick={()=>run(async()=>{await request(`/jobs/${job.id}/cancel`,json({}));setJobs(await (await request('/jobs')).json());})}>Cancel batch</button>}</section>}
  <div className="workspace"><section className="inventory">{!cards.length?<div className="empty"><div>▧</div><h2>Your next batch starts here</h2><p>Import JPG, PNG, WEBP, or iPhone HEIC photos.<br/>Originals stay in place until CSV export.</p></div>:!visible.length?<div className="empty"><h2>No matching cards</h2><p>Try a different search or filter.</p><button onClick={()=>{setSearch('');setFilter('all');setSelected([]);}}>Clear filters</button></div>:visible.map(c=><div key={c.id} className={'card-row '+(active===c.id?'active':'')}><input aria-label={'Select '+(c.listing.player||c.original)} type="checkbox" disabled={busy||['queued','processing'].includes(c.state)} checked={selected.includes(c.id)} onChange={e=>setSelected(s=>e.target.checked?[...s,c.id]:s.filter(id=>id!==c.id))}/><button disabled={busy} className="card-button" onClick={()=>choose(c)}><img loading="lazy" alt={c.original} src={'/api/photos/'+c.front}/><div><strong>{c.listing.player||c.original}</strong><p>{c.listing.set_name||'Awaiting identification'}</p><span className={'badge '+c.state}>{c.state.replaceAll('_',' ')}</span><small>{c.back?'Front + back':'Single photo'}</small></div></button></div>)}</section>
  <section className="editor">{card&&draft?<><div className="editor-title"><h2>Review card</h2><div className="review-navigation"><button disabled={busy||position<=0} onClick={()=>choose(visible[position-1])}>Previous</button><span>{position>=0?`${position+1} / ${visible.length}`:'Outside filter'}</span><button disabled={busy||position<0||position>=visible.length-1} onClick={()=>choose(visible[position+1])}>Next</button></div><code>{card.id.slice(0,8)}</code></div><div className="photos">{(['front','back'] as const).map(side=><figure key={side}>{card[side]?<button className="photo-zoom" aria-label={'Enlarge '+side+' photo'} onClick={()=>setZoom('/api/photos/'+card[side]+'?full=true')}><img alt={side+' card photo'} src={'/api/photos/'+card[side]}/></button>:<div className="missing">No back photo</div>}<figcaption>{side}</figcaption></figure>)}</div>
  <div className="actions">{card.back?<><button disabled={busy||dirty||locked} onClick={()=>run(()=>operation('swap'))}>Swap sides</button><button disabled={busy||dirty||locked} onClick={()=>run(()=>operation('unpair'))}>Separate photos</button></>:<><select aria-label="Choose back photo" value={backId} disabled={busy||dirty||locked||card.state!=='imported'} onChange={e=>setBackId(e.target.value)}><option value="">Choose back photo…</option>{cards.filter(c=>c.id!==card.id&&c.state==='imported'&&!c.back).map(c=><option key={c.id} value={c.id}>{c.original}</option>)}</select><button disabled={busy||dirty||locked||!backId} onClick={()=>run(async()=>{await request(`/cards/${card.id}/pair`,json({back_id:backId}));setBackId('');await refresh();})}>Pair</button></>}</div>
  <div className="actions"><button disabled={busy||dirty||locked||!config?.ready} onClick={()=>run(()=>operation('extract'))}>Identify with {providerName}</button><button disabled={busy||dirty||locked||!config?.cloudinary} onClick={()=>run(()=>operation('upload'))}>Upload listing photos</button></div><small>Identification sends photos to {providerName}; upload sends full-resolution JPEGs to Cloudinary. Re-identifying replaces extracted fields.</small>
  {card.error&&<p className="notice">{card.error}</p>}{card.warnings.map((w,i)=><p className="warning" key={i}>⚠ {w}</p>)}
  {card.last_export_id&&<div className="qc"><h4>Export & photo archive</h4><a href={'/api/exports/'+card.last_export_id} download>Download saved CSV</a>{Object.entries(card.archived_photos||{}).map(([side,path])=><p style={{overflowWrap:'anywhere'}} key={side}>✓ {side}: {path}</p>)}{card.archive_warnings?.map((warning,i)=><p className="warning" key={i}>{warning}</p>)}{!!card.archive_warnings?.length&&<button disabled={busy||dirty||card.state!=='exported'} onClick={()=>run(async()=>{await request(`/cards/${card.id}/retry-archive`,json({}));await refresh();setMessage('Archive retry finished. Check the archive notes; no new CSV was created.');})}>Retry photo archive</button>}</div>}
  <fieldset disabled={busy||locked}><div className="fields">{field('player','Player')}{field('set_name','Set')}{field('year','Year','number')}{field('team','Team')}{field('parallel','Parallel')}{field('card_number','Card number')}{field('print_run','Print run','number')}{field('price','Manual price · USD','number')}{field('category_id','Category ID')}{field('condition_id','Condition ID')}</div><div className="checks">{(['is_rookie','is_auto','is_relic'] as const).map(key=><label key={key}><input type="checkbox" checked={draft[key]} onChange={e=>setDraft({...draft,[key]:e.target.checked})}/>{key.replace('is_','')}</label>)}</div><label>Listing title · {draft.title.length}/80<input maxLength={80} value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})}/></label><label>Description<textarea rows={4} value={draft.description} onChange={e=>setDraft({...draft,description:e.target.value})}/></label><p className="muted">Research prices yourself. No sold comps or estimated values are generated.</p><label className="review-check"><input type="checkbox" checked={draft.reviewed} onChange={e=>setDraft({...draft,reviewed:e.target.checked})}/>I reviewed the pairing, identification, condition, title, and price.</label><button className="primary save" disabled={!dirty} onClick={()=>run(async()=>{await saveListing();})}>{busy?'Working…':dirty?'Save changes':'Saved'}</button><button className="save" disabled={busy||(!dirty&&position>=visible.length-1)} onClick={()=>run(()=>saveListing(true))}>Save & next</button></fieldset>
  <div className="qc"><h4>Export checklist</h4>{dirty&&<p>Preview of unsaved changes — save before exporting.</p>}{liveIssues.length?liveIssues.map(i=><p key={i}>○ {i}</p>):<p>✓ Ready for CSV export{dirty?' after saving':''}</p>}</div></>:<div className="empty"><h2>A closer look</h2><p>Select a card to pair photos and edit its listing.</p></div>}</section></div>{zoom&&<div className="zoom-backdrop" role="dialog" aria-modal="true" aria-label="Enlarged card photo" onClick={()=>setZoom(null)}><button aria-label="Close photo" onClick={()=>setZoom(null)}>Close ×</button><img alt="Enlarged card photo" src={zoom} onClick={e=>e.stopPropagation()}/></div>}{showSettings&&config&&<Settings config={config} onClose={()=>setShowSettings(false)} onSave={async changes=>{if(!config.provider)throw new Error('Close the app command window, reopen launch-windows.cmd, then refresh this page. The running server needs to restart before it can save Claude settings.');setConfig(await (await request('/config',{...json(changes),method:'PUT'})).json());setMessage('Settings saved. Connections are ready to use without restarting.');}}/>}</main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);

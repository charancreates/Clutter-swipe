import ctypes
from ctypes import wintypes
import http.server
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import urllib.parse
import webbrowser

DOWNLOADS = Path.home() / 'Downloads'
TOKEN = secrets.token_urlsafe(32)
videos = []
lock = threading.Lock()

def scan():
    found = []
    pending = [str(DOWNLOADS)]
    while pending:
        root = pending.pop()
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not (entry.stat(follow_symlinks=False).st_file_attributes & 0x400):
                                pending.append(entry.path)
                        elif entry.name.lower().endswith('.mp4') and entry.is_file(follow_symlinks=False):
                            found.append({'path': entry.path, 'size': entry.stat(follow_symlinks=False).st_size})
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda v: v['size'], reverse=True)
    with lock:
        videos[:] = found

def choose_directory():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(parent=root, initialdir=str(DOWNLOADS), title='Choose a folder to review')
    finally:
        root.destroy()

class SHFILEOPSTRUCT(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR),
                ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort),
                ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p),
                ('lpszProgressTitle', wintypes.LPCWSTR)]

def recycle(path):
    op = SHFILEOPSTRUCT()
    op.wFunc = 3
    op.pFrom = str(path) + '\0\0'
    op.fFlags = 0x40  # Ask Windows to recycle and retain its confirmation dialogs.
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result or op.fAnyOperationsAborted:
        raise OSError('Windows could not move this video to the Recycle Bin (error %s).' % result)

PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Video cleanup review</title><style>
body{font:16px system-ui;background:#10131a;color:#eef2fa;margin:0}main{max-width:900px;margin:auto;padding:20px}h1{font-size:25px}p{color:#b7c1d4}video{width:100%;height:min(55vh,500px);background:#000;border-radius:12px}.card{background:#1c2330;border-radius:12px;padding:15px;margin:14px 0;overflow-wrap:anywhere}.path{color:#b7c1d4;font-size:14px}.actions{display:flex;gap:12px}button{border:0;border-radius:10px;padding:15px;flex:1;font-size:17px;font-weight:700;cursor:pointer}#keep{background:#b4e6cc}#delete{background:#ffb7b2}button:disabled{opacity:.45}#status{min-height:24px}small{color:#b7c1d4}</style></head><body><main><h1>Review your largest videos</h1><p>Downloads MP4 files, largest first. Folders and filenames containing “Udemy” are excluded. Nothing is deleted until you choose Delete.</p><div id="status">Scanning Downloads…</div><video id="video" controls playsinline preload="metadata"></video><div class="card"><strong id="title"></strong><p id="path" class="path"></p><span id="size"></span></div><div class="actions"><button id="keep" disabled>← Keep</button><button id="delete" disabled>Delete →</button></div><p><small>Swipe left to keep; swipe right to move to the Windows Recycle Bin. Arrow keys also work when the video player is not focused. Files too large for the Recycle Bin are kept; check the message if Windows cannot recycle one. Closing this page stops your review.</small></p></main><script>
const token='TOKEN_VALUE'; let items=[], index=0, deleted=0; const $=x=>document.getElementById(x); let busy=false;
function show(){const item=items[index]; if(!item){$('status').textContent=`Review complete. ${deleted} video(s) moved to the Recycle Bin.`; $('video').removeAttribute('src'); $('video').load(); $('title').textContent='No more videos'; $('path').textContent=''; $('size').textContent=''; $('keep').disabled=$('delete').disabled=true; return} $('status').textContent=`${index+1} of ${items.length} · ${deleted} recycled`; $('title').textContent=item.path.split(/[/\\]/).pop(); $('path').textContent=item.path; $('size').textContent=(item.size/1073741824).toFixed(2)+' GiB'; $('video').src='/video?id='+index+'&token='+encodeURIComponent(token); $('keep').disabled=$('delete').disabled=false;}
function keep(){if(busy||!items[index])return; index++; show()}
async function del(){if(busy||!items[index])return; busy=true; $('keep').disabled=$('delete').disabled=true; $('video').pause(); $('video').removeAttribute('src'); $('video').load(); try{let r=await fetch('/delete',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({id:index})}); let j=await r.json(); if(!r.ok) throw Error(j.error); deleted++; index++; show()}catch(e){alert(e.message);show()}finally{busy=false}}
$('keep').onclick=keep; $('delete').onclick=del; document.addEventListener('keydown',e=>{if(e.target.tagName==='VIDEO')return;if(e.key==='ArrowLeft'){e.preventDefault();keep()}if(e.key==='ArrowRight'){e.preventDefault();del()}}); let startX=0,startY=0; document.addEventListener('touchstart',e=>{startX=e.changedTouches[0].clientX;startY=e.changedTouches[0].clientY},{passive:true});document.addEventListener('touchend',e=>{let dx=e.changedTouches[0].clientX-startX,dy=e.changedTouches[0].clientY-startY;if(Math.abs(dx)>100&&Math.abs(dx)>Math.abs(dy)*1.5){dx<0?keep():del()}},{passive:true});
fetch('/list?token='+encodeURIComponent(token)).then(r=>r.json()).then(x=>{items=x;show()}).catch(()=>{$('status').textContent='Could not scan Downloads.'});</script></body></html>'''.replace('TOKEN_VALUE', TOKEN)

PAGE = PAGE.replace('Files too large for the Recycle Bin are kept; check the message if Windows cannot recycle one. Closing this page stops your review.', 'Windows may ask for confirmation; if it offers permanent deletion, choose No to keep the file. Close the launcher window to stop the reviewer.')
PAGE = PAGE.replace('<div class="actions"><button id="keep"', '<div class="actions"><button id="rename">Rename</button><button id="openfolder">Open folder</button><button id="jump">Find video</button></div><p id="folderlabel" class="path"></p><div class="actions"><button id="keep"')
PAGE = PAGE.replace('<button id="jump">Find video</button>', '<button id="jump">Find video</button></div><div class="actions"><button id="exclude">Exclude this folder</button><button id="manage">Manage excluded folders</button>')
PAGE = PAGE.replace('<button id="manage">Manage excluded folders</button>', '<button id="manage">Manage excluded folders</button><button id="refresh">Refresh files</button>')
PAGE = PAGE.replace("$('path').textContent=item.path;", "$('path').textContent=item.path; $('folderlabel').textContent='Folder: '+item.path.slice(0,item.path.lastIndexOf('\\\\')); ")
EXTRA = r'''
const reviewedKey='downloads-video-review-v2';
let reviewed={}; try{reviewed=JSON.parse(localStorage.getItem(reviewedKey)||'{}')}catch(e){}
const excludedKey='downloads-video-excluded-folders-v1';
let excluded=[]; try{let v=JSON.parse(localStorage.getItem(excludedKey)||'[]');if(Array.isArray(v))excluded=v.filter(x=>typeof x==='string')}catch(e){}
function saveExcluded(){try{localStorage.setItem(excludedKey,JSON.stringify(excluded))}catch(e){}}
function isExcluded(path){let p=path.toLowerCase();return excluded.some(folder=>p.startsWith(folder.toLowerCase()+'\\'))}
function saveReviewed(path){reviewed[path]=true; try{localStorage.setItem(reviewedKey,JSON.stringify(reviewed))}catch(e){}}
const originalKeep=keep, originalDel=del;
const originalShow=show;
show=function(){while(items[index] && (reviewed[items[index].path]||isExcluded(items[index].path))) index++; originalShow()};
keep=function(){if(busy||!items[index])return;saveReviewed(items[index].path);originalKeep()};
del=async function(){if(busy||!items[index])return;let path=items[index].path,old=index;await originalDel();if(index!==old)saveReviewed(path)};
$('keep').onclick=keep; $('delete').onclick=del;
async function action(route, data){let r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(data)});let j=await r.json();if(!r.ok)throw Error(j.error);return j}
$('rename').onclick=async()=>{if(!items[index]||busy)return;let old=items[index].path.split(/[/\\]/).pop();let name=prompt('New filename (the .mp4 extension will be kept):',old);if(name===null||name===old)return;busy=true;$('video').pause();$('video').removeAttribute('src');$('video').load();try{let j=await action('/rename',{id:index,name});items[index].path=j.path;show()}catch(e){alert(e.message);show()}finally{busy=false}};
let refreshOnReturn=false;
async function refreshFiles(){if(busy)return;busy=true;let old=items.slice(),oldIndex=index,current=items[index]?.path;$('video').pause();$('video').removeAttribute('src');$('video').load();$('status').textContent='Refreshing files…';try{let r=await fetch('/list?token='+encodeURIComponent(token),{cache:'no-store'});if(!r.ok)throw Error('Could not refresh Downloads.');let fresh=await r.json();items=fresh;let lookup=new Map(items.map((v,i)=>[v.path.toLowerCase(),i]));let next=current==null?undefined:lookup.get(current.toLowerCase());if(next===undefined){for(let i=oldIndex+1;i<old.length;i++){next=lookup.get(old[i].path.toLowerCase());if(next!==undefined)break}}index=next===undefined?items.length:next;for(const v of items)if(isExcluded(v.path))reviewed[v.path]=true;try{localStorage.setItem(reviewedKey,JSON.stringify(reviewed))}catch(e){}show()}catch(e){items=old;index=oldIndex;alert(e.message);show()}finally{busy=false}}
$('refresh').onclick=refreshFiles;
$('openfolder').onclick=async()=>{if(!items[index])return;$('video').pause();$('video').removeAttribute('src');$('video').load();try{refreshOnReturn=true;await action('/openfolder',{id:index})}catch(e){refreshOnReturn=false;alert(e.message);show()}};
window.addEventListener('focus',()=>{if(refreshOnReturn){refreshOnReturn=false;refreshFiles()}});
$('exclude').onclick=()=>{if(!items[index]||busy)return;let path=items[index].path,folder=path.slice(0,path.lastIndexOf('\\'));if(!folder)return;let choices=[],parts=folder.split('\\');while(parts.length>2){let f=parts.join('\\');if(!/\\downloads$/i.test(f))choices.push(f);parts.pop()}if(!choices.length){alert('This file is directly in Downloads. Excluding Downloads would exclude every video, so choose Keep instead.');return}let answer=prompt('Choose the folder to keep and exclude, including everything in its subfolders:\n\n'+choices.map((f,i)=>`${i+1}. ${f}`).join('\n')+'\n\nEnter a number:', '1');if(answer===null)return;let n=Number(answer);if(!Number.isInteger(n)||n<1||n>choices.length){alert('Enter a number from the list.');return}folder=choices[n-1];let prefix=folder.toLowerCase()+'\\';let count=0;for(const item of items){if(item.path.toLowerCase().startsWith(prefix)){reviewed[item.path]=true;count++}}try{localStorage.setItem(reviewedKey,JSON.stringify(reviewed))}catch(e){}if(!excluded.some(f=>f.toLowerCase()===folder.toLowerCase()))excluded.push(folder);saveExcluded();alert(`${count} video(s) kept. This folder and its subfolders are excluded. No files were deleted.`);show()};
$('manage').onclick=()=>{if(!excluded.length){alert('No folders excluded.');return}let list=excluded.map((f,i)=>`${i+1}. ${f}`).join('\n');let answer=prompt('Excluded folders:\n\n'+list+'\n\nEnter a number to allow newly added videos from that folder into review. Previously kept videos remain kept.');if(answer===null)return;let n=Number(answer);if(!Number.isInteger(n)||n<1||n>excluded.length){alert('Enter a number from the list.');return}excluded.splice(n-1,1);saveExcluded();index=0;show()};
$('jump').onclick=()=>{let answer=prompt(`Jump to video number (1–${items.length}):`,String(index+1));if(answer===null)return;let n=Number(answer);if(!Number.isInteger(n)||n<1||n>items.length){alert('Enter a valid video number.');return}index=n-1;show()};
'''
PAGE = PAGE.replace("fetch('/list?token='", EXTRA + "\nfetch('/list?token='")
PAGE = PAGE.replace('>Find video</button>', '>Jump to…</button>')
PAGE = PAGE.replace('<button id="refresh">Refresh files</button>', '<button id="refresh">Refresh files</button><button id="choosefolder">Choose folder…</button>')
PAGE = PAGE.replace('Downloads MP4 files, largest first.', 'MP4 files in the selected folder, largest first.')
PAGE = PAGE.replace('<title>Video cleanup review</title>', '<title>Clutter Swipe</title>')
PAGE = PAGE.replace('<h1>Review your largest videos</h1>', '<h1>Clutter Swipe</h1>')
PAGE = PAGE.replace('MP4 files in the selected folder, largest first.', 'Swipe through videos. Make space for what matters. Largest files first.')
PAGE = PAGE.replace('Folders and filenames containing “Udemy” are excluded. ', '')
PAGE = PAGE.replace("$('exclude').onclick=()=>", "$('exclude').onclick=()=>")
start = PAGE.index("$('exclude').onclick=()=>")
end = PAGE.index("$('manage').onclick=", start)
PAGE = PAGE[:start] + r'''
let reviewRoot='';
function folderDialog(choices){return new Promise(resolve=>{let dialog=document.createElement('dialog');dialog.className='folder-dialog';let title=document.createElement('h2');title.textContent='Keep and exclude a folder';dialog.append(title);let desc=document.createElement('p');desc.textContent='Choose a folder. Its videos and subfolders will be kept and skipped.';dialog.append(desc);let group=document.createElement('div');group.className='folder-choices';choices.forEach((folder,i)=>{let label=document.createElement('label');let radio=document.createElement('input');radio.type='radio';radio.name='folderlevel';radio.value=String(i);radio.checked=i===Math.min(1,choices.length-1);let content=document.createElement('span');let name=document.createElement('strong');name.textContent=folder.split('\\').pop() || folder;let path=document.createElement('small');path.textContent=folder;content.append(name,path);label.append(radio,content);group.append(label)});dialog.append(group);let buttons=document.createElement('div');buttons.className='dialog-buttons';let cancel=document.createElement('button');cancel.textContent='Cancel';let apply=document.createElement('button');apply.textContent='Keep folder';apply.className='apply';buttons.append(cancel,apply);dialog.append(buttons);document.body.append(dialog);let finish=value=>{dialog.close();dialog.remove();resolve(value)};cancel.onclick=()=>finish(null);apply.onclick=()=>finish(choices[Number(group.querySelector('input:checked').value)]);dialog.oncancel=e=>{e.preventDefault();finish(null)};dialog.showModal()})}
$('exclude').onclick=async()=>{if(!items[index]||busy)return;let path=items[index].path,folder=path.slice(0,path.lastIndexOf('\\'));let choices=[];while(folder && folder.toLowerCase()!==reviewRoot.toLowerCase() && folder.toLowerCase().startsWith(reviewRoot.toLowerCase()+'\\')){choices.push(folder);folder=folder.slice(0,folder.lastIndexOf('\\'))}if(!choices.length){alert('This video is directly in the selected folder. Choose Keep to keep it.');return}folder=await folderDialog(choices);if(!folder)return;let prefix=folder.toLowerCase()+'\\';for(const item of items)if(item.path.toLowerCase().startsWith(prefix))reviewed[item.path]=true;try{localStorage.setItem(reviewedKey,JSON.stringify(reviewed))}catch(e){}if(!excluded.some(f=>f.toLowerCase()===folder.toLowerCase()))excluded.push(folder);saveExcluded();show()};
$('choosefolder').onclick=async()=>{if(busy)return;busy=true;$('video').pause();$('video').removeAttribute('src');$('video').load();try{let j=await action('/choosefolder',{});if(j.cancelled){show();return}let r=await fetch('/list?token='+encodeURIComponent(token),{cache:'no-store'});if(!r.ok)throw Error('Could not scan that folder.');reviewRoot=decodeURIComponent(r.headers.get('X-Review-Root')||'');items=await r.json();index=0;for(const v of items)if(isExcluded(v.path))reviewed[v.path]=true;show()}catch(e){alert(e.message);show()}finally{busy=false}};
''' + PAGE[end:]
PAGE = PAGE.replace("then(r=>r.json()).then(x=>", "then(r=>{reviewRoot=decodeURIComponent(r.headers.get('X-Review-Root')||'');return r.json()}).then(x=>")
PAGE = PAGE.replace("let fresh=await r.json();items=fresh", "reviewRoot=decodeURIComponent(r.headers.get('X-Review-Root')||'');let fresh=await r.json();items=fresh")
PAGE = PAGE.replace("let r=await fetch('/list?token='+encodeURIComponent(token),{cache:'no-store'});if(!r.ok)throw Error('Could not scan that folder.')", "$('status').textContent='Scanning selected folder…';let r=await fetch('/list?token='+encodeURIComponent(token),{cache:'no-store'});if(!r.ok)throw Error('Could not scan that folder.')")
PAGE = PAGE.replace("(item.size/1073741824).toFixed(2)+' GiB'", "item.size >= 1000000000 ? (item.size/1000000000).toFixed(2)+' GB' : (item.size/1000000).toFixed(1)+' MB'")
PAGE = PAGE.replace('<div id="status">', '<div id="review-folder" class="review-folder">Reviewing: Downloads</div><div id="status">')
PAGE = PAGE.replace("reviewRoot=decodeURIComponent(r.headers.get('X-Review-Root')||'');", "reviewRoot=decodeURIComponent(r.headers.get('X-Review-Root')||'');$('review-folder').textContent='Reviewing: '+reviewRoot;")
insert_at = PAGE.rfind("fetch('/list?token='")
PAGE = PAGE[:insert_at] + r'''
function modal(title,description){let d=document.createElement('dialog');d.className='folder-dialog';let h=document.createElement('h2');h.textContent=title;let p=document.createElement('p');p.textContent=description;d.append(h,p);document.body.append(d);d.oncancel=()=>d.remove();return d}
$('jump').onclick=()=>{let d=modal('Jump to video',`Choose a position from 1 to ${items.length}. Kept and excluded videos are skipped.`);let input=document.createElement('input');input.type='number';input.min=1;input.max=items.length;input.value=index+1;input.className='dialog-input';let error=document.createElement('p');error.className='dialog-error';let row=document.createElement('div');row.className='dialog-buttons';let cancel=document.createElement('button');cancel.textContent='Cancel';let go=document.createElement('button');go.textContent='Jump';go.className='apply';row.append(cancel,go);d.append(input,error,row);cancel.onclick=()=>{d.close();d.remove()};go.onclick=()=>{let n=Number(input.value);if(!Number.isInteger(n)||n<1||n>items.length){error.textContent=`Enter a number between 1 and ${items.length}.`;return}index=n-1;d.close();d.remove();show()};input.onkeydown=e=>{if(e.key==='Enter')go.click()};d.showModal();input.select()};
$('manage').onclick=()=>{let d=modal('Excluded folders','Videos in these folders stay kept. Removing an exclusion allows newly added videos to appear.');let list=document.createElement('div');list.className='excluded-list';function render(){list.replaceChildren();if(!excluded.length){let p=document.createElement('p');p.textContent='No folders excluded.';list.append(p)}excluded.forEach((folder,i)=>{let row=document.createElement('div');row.className='excluded-row';let text=document.createElement('span');text.textContent=folder;let remove=document.createElement('button');remove.textContent='Remove exclusion';remove.onclick=()=>{excluded.splice(i,1);saveExcluded();render()};row.append(text,remove);list.append(row)})}render();let buttons=document.createElement('div');buttons.className='dialog-buttons';let done=document.createElement('button');done.textContent='Done';done.className='apply';done.onclick=()=>{d.close();d.remove()};buttons.append(done);d.append(list,buttons);d.showModal()};
''' + PAGE[insert_at:]
PAGE = PAGE.replace("+'\\n\\nEnter a number:', '1')", "+'\\n\\nEnter a number:', String(Math.min(2,choices.length)))")
PAGE = PAGE.replace('items=x;show()', "items=x;for(const v of items){if(isExcluded(v.path))reviewed[v.path]=true}try{localStorage.setItem(reviewedKey,JSON.stringify(reviewed))}catch(e){}show()")
PAGE = PAGE.replace('`${count} video(s) kept. This folder and its subfolders are excluded. No files were deleted.`', "'All remaining videos in this folder and its subfolders are kept and excluded. No files were deleted.'")
PAGE = PAGE.replace('<video id="video"', '<div class="review-grid"><section class="viewer"><video id="video"')
PAGE = PAGE.replace('</video><div class="card">', '</video></section><aside class="details"><div class="card">')
PAGE = PAGE.replace('</small></p></main><script>', '</small></p></aside></div></main><script>')
PAGE = PAGE.replace('</style>', '''
.review-folder{font-size:13px;color:#d1dcee;overflow-wrap:anywhere;margin:6px 0}.dialog-input{width:100%;padding:12px;border:1px solid #596b85;border-radius:8px;background:#101720;color:white;font-size:16px}.dialog-error{color:#ffaba6}.excluded-list{max-height:45vh;overflow:auto}.excluded-row{display:flex;align-items:center;gap:12px;border-bottom:1px solid #43516a;padding:10px 0}.excluded-row span{flex:1;overflow-wrap:anywhere}.excluded-row button{flex:0;padding:9px;font-size:12px;background:#344258;color:white}.folder-dialog{width:min(640px,90vw);border:1px solid #45536a;border-radius:14px;padding:22px;background:#1c2330;color:#eef2fa}.folder-dialog::backdrop{background:#000b}.folder-dialog h2{margin:0;font-size:19px}.folder-dialog p{font-size:14px}.folder-choices{display:grid;gap:8px;max-height:45vh;overflow:auto}.folder-choices label{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid #43516a;border-radius:8px;cursor:pointer}.folder-choices label:has(input:checked){border-color:#9edcbb;background:#283d39}.folder-choices input{accent-color:#9edcbb}.folder-choices span{min-width:0}.folder-choices strong,.folder-choices small{display:block;overflow-wrap:anywhere}.folder-choices small{margin-top:4px}.dialog-buttons{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}.dialog-buttons button{flex:0;padding:10px 18px;font-size:14px;background:#344258;color:white}.dialog-buttons .apply{background:#9edcbb;color:#101820}
*{box-sizing:border-box}body{font-size:14px}main{max-width:1500px;padding:18px 24px}h1{font-size:21px;margin:0 0 5px}main>p{margin:0 0 8px;font-size:13px}#status{font-size:13px;min-height:20px;margin-bottom:10px;color:#95a6c3}
.review-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(300px,0.8fr);gap:20px;align-items:start}.viewer{min-width:0}video{display:block;height:auto;max-height:calc(100vh - 150px);min-height:300px;object-fit:contain;background:#050608}.details{min-width:0;display:flex;flex-direction:column;gap:12px}.card{margin:0;padding:16px}.card strong{font-size:16px}.card p{margin:9px 0;line-height:1.45}.card span{color:#d4e2f8;font-weight:600}.actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.actions:has(#rename){grid-template-columns:repeat(3,minmax(0,1fr))}.actions button{min-width:0;min-height:40px;padding:10px 9px;font-size:13px;line-height:1.2;border:1px solid #43516a;background:#252f40;color:#eaf0fb;border-radius:8px;transition:background .15s,border-color .15s}.actions button:hover{background:#344258;border-color:#73829a}.actions #keep,.actions #delete{min-height:50px;font-size:15px;color:#101820;border:0}.actions #keep{background:#9edcbb}.actions #keep:hover{background:#b6ebcd}.actions #delete{background:#ffa8a6}.actions #delete:hover{background:#ffc1bf}#folderlabel{display:none}.details>p:last-child{margin:0;line-height:1.5;font-size:12px}.details small{font-size:12px}
@media(max-width:850px){main{padding:16px}.review-grid{grid-template-columns:1fr;gap:12px}video{min-height:180px;max-height:48vh}.details{gap:10px}.actions:has(#rename){grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:460px){.actions:has(#rename){grid-template-columns:1fr}.actions button{min-height:38px}h1{font-size:19px}}
</style>''')

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def auth(self, query):
        return secrets.compare_digest(query.get('token', [''])[0], TOKEN)
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(url.query)
        if url.path == '/':
            if not self.auth(q): self.send_error(403); return
            data = PAGE.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if not self.auth(q): self.send_error(403); return
        if url.path == '/list':
            scan(); data=json.dumps(videos).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('X-Review-Root',urllib.parse.quote(str(DOWNLOADS))); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if url.path != '/video': self.send_error(404); return
        try:
            i=int(q['id'][0]); assert 0 <= i < len(videos); path=Path(videos[i]['path']); size=path.stat().st_size
            start=0; end=size-1; rng=self.headers.get('Range')
            if rng:
                unit, spec=rng.split('=',1); assert unit=='bytes' and ',' not in spec
                a,b=spec.split('-',1)
                if a: start=int(a); end=min(int(b),end) if b else end
                else: start=max(0,size-int(b))
                assert 0<=start<=end<size
            self.send_response(206 if rng else 200); self.send_header('Content-Type','video/mp4'); self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1))
            if rng: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with path.open('rb') as f:
                f.seek(start); remaining=end-start+1
                while remaining:
                    data=f.read(min(1024*1024,remaining))
                    if not data: break
                    self.wfile.write(data); remaining-=len(data)
        except (OSError, ValueError, KeyError, IndexError, AssertionError, BrokenPipeError, ConnectionResetError): return
    def do_POST(self):
        global DOWNLOADS
        try:
            if self.path not in ('/delete','/rename','/openfolder','/choosefolder') or not secrets.compare_digest(self.headers.get('X-Review-Token',''), TOKEN): raise ValueError('Not authorized')
            length=int(self.headers.get('Content-Length','0')); assert 0 < length < 1024
            request=json.loads(self.rfile.read(length))
            if self.path == '/choosefolder':
                selected=choose_directory()
                if selected:
                    folder=Path(selected).resolve(strict=True)
                    if not folder.is_dir(): raise ValueError('Choose a folder.')
                    DOWNLOADS=folder
                data=json.dumps({'cancelled':not bool(selected)}).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            i=int(request['id']); assert 0 <= i < len(videos)
            p=Path(videos[i]['path']); resolved=p.resolve(strict=True); base=DOWNLOADS.resolve(strict=True)
            assert resolved.is_relative_to(base) and resolved.suffix.lower()=='.mp4'
            if self.path == '/delete':
                recycle(resolved); body={'ok':True}
            elif self.path == '/openfolder':
                subprocess.Popen(['explorer.exe', '/select,', str(resolved)]); body={'ok':True}
            else:
                name=request.get('name','').strip()
                if not name or any(ch in name for ch in '<>:"/\\|?*') or name.endswith(('.', ' ')) or name in ('.','..'):
                    raise ValueError('Enter a valid filename without folder separators.')
                if not name.lower().endswith('.mp4'): name += '.mp4'
                dest=resolved.with_name(name)
                if dest.exists(): raise ValueError('A file with that name already exists.')
                resolved.rename(dest); videos[i]['path']=str(dest); body={'ok':True,'path':str(dest)}
            code=200
        except Exception as e: body={'error':str(e) or 'Unable to recycle this file.'}; code=400
        data=json.dumps(body).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

if __name__ == '__main__':
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}/?token={TOKEN}'
    print('Video reviewer is running. Close this window to stop it.'); print('If the browser does not open, copy this address:',url)
    webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

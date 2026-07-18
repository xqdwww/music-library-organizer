from __future__ import annotations

# The embedded HTML/CSS/JavaScript is intentionally kept as one deployable local-only asset.
# ruff: noqa: E501
import json
import mimetypes
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import AlbumPruneService
from .web_security import LOOPBACK_HOSTS, validate_loopback_request

MAX_COVER_BYTES = 20 * 1024 * 1024

HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>个人专辑候选审核</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202124;background:#f6f7f8;letter-spacing:0}
*{box-sizing:border-box}body{margin:0}header{height:58px;background:#fff;border-bottom:1px solid #dfe1e5;display:flex;align-items:center;padding:0 24px;gap:18px;position:sticky;top:0;z-index:2}
h1{font-size:18px;margin:0;white-space:nowrap}.status{font-size:13px;color:#5f6368}.toolbar{display:grid;grid-template-columns:minmax(180px,1fr) 220px 180px auto;gap:8px;padding:14px 24px;background:#fff;border-bottom:1px solid #dfe1e5}.toolbar>*{min-width:0;width:100%}
input,select,button{height:34px;border:1px solid #bdc1c6;border-radius:6px;background:#fff;padding:0 10px;font:inherit}button{cursor:pointer;font-weight:600}button.primary{background:#1967d2;color:#fff;border-color:#1967d2}button.danger{background:#b3261e;color:#fff;border-color:#b3261e}button:disabled{opacity:.45;cursor:not-allowed}
main{padding:16px 24px 80px}.group-summary{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:8px;margin-bottom:12px}.group-summary button{height:auto;min-height:54px;text-align:left}.group-summary button.active{border-color:#1967d2;background:#e8f0fe}.group-summary b{display:block;font-size:18px}.summary{display:flex;gap:24px;margin-bottom:12px;font-size:13px;color:#3c4043}.table-wrap{overflow:auto;border:1px solid #dfe1e5;background:#fff;border-radius:8px}table{border-collapse:collapse;width:100%;min-width:1480px}th,td{padding:9px 10px;border-bottom:1px solid #eceff1;text-align:left;font-size:13px;vertical-align:middle}th{position:sticky;top:0;background:#f8f9fa;color:#5f6368;font-weight:600}tr:hover{background:#f8fbff}.cover{width:42px;height:42px;object-fit:cover;background:#e8eaed;border-radius:4px}.score{font-weight:700;font-variant-numeric:tabular-nums}.sources details{max-width:360px}.sources summary{cursor:pointer}.path{max-width:280px;overflow-wrap:anywhere}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#e8f0fe;color:#174ea6;font-size:11px}.pill.warn{background:#fef7e0;color:#7a4f01}.pill.block{background:#fce8e6;color:#8c1d18}.actions{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #dfe1e5;padding:12px 24px;display:flex;align-items:center;gap:8px;z-index:2}.spacer{flex:1}
.batches{margin-top:24px}.batches h2{font-size:16px;margin:0 0 10px}.batches table{min-width:760px}.batch-buttons{display:flex;gap:6px;white-space:nowrap}.batch-buttons button{height:30px;font-size:12px}.empty{padding:18px;color:#5f6368}
@media(max-width:760px){header{padding:0 14px}.toolbar{grid-template-columns:minmax(0,1fr) minmax(0,1fr);padding:10px 14px}.toolbar input{grid-column:1/-1}.group-summary{grid-template-columns:1fr 1fr}main{padding:14px 14px 130px}.summary{gap:10px;flex-wrap:wrap}.actions{padding:8px 14px;display:grid;grid-template-columns:1fr 1fr}.actions button{white-space:nowrap;width:100%}.actions .spacer{display:none}}
dialog{border:1px solid #bdc1c6;border-radius:8px;padding:0;width:min(680px,calc(100vw - 32px));box-shadow:0 12px 34px #0003}dialog::backdrop{background:#0005}.modal-head,.modal-body,.modal-actions{padding:16px 20px}.modal-head{border-bottom:1px solid #e0e0e0;font-weight:700}.modal-actions{border-top:1px solid #e0e0e0;display:flex;justify-content:flex-end;gap:8px}.kv{display:grid;grid-template-columns:150px 1fr;gap:8px;font-size:13px}.preview-progress{display:grid;gap:12px}.preview-progress progress{width:100%;height:10px}.error{color:#b3261e;white-space:pre-wrap}
</style></head><body>
<header><h1>个人专辑候选审核</h1><span class="status" id="status">SCANNED</span></header>
<section class="toolbar"><input id="search" placeholder="艺术家或专辑"><select id="candidate"><option value="">全部候选状态</option></select><select id="match"><option value="">全部匹配状态</option></select><button id="refresh">刷新</button></section>
<main><section class="group-summary"><button data-group="STRONG_PERSONAL_CANDIDATE">强低分候选<b id="strongCount">0</b></button><button data-group="PERSONAL_REVIEW_CANDIDATE">65–70 人工审核<b id="reviewCount">0</b></button><button data-group="USER_SELECTED_CANDIDATE">用户明确选择<b id="explicitCount">0</b></button><button data-group="LATER">以后再看<b id="laterCount">0</b></button></section><div class="summary"><span id="visible"></span><span id="selected">已勾选 0</span><span id="bytes"></span><span id="totalBytes"></span></div><p class="status">Discogs 评分旁的来源链接指向对应页面。Data provided by Discogs.</p><div class="table-wrap"><table><thead><tr><th></th><th>封面</th><th>艺术家</th><th>专辑</th><th>年份</th><th>类型</th><th>music_score</th><th>评分与专业证据</th><th>匹配</th><th>候选组</th><th>保护</th><th>格式</th><th>大小</th><th>本地路径</th></tr></thead><tbody id="rows"></tbody></table></div><section class="batches"><h2>最近清理批次</h2><div class="table-wrap"><table><thead><tr><th>批次</th><th>状态</th><th>专辑</th><th>文件</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead><tbody id="batchRows"></tbody></table><div class="empty" id="batchEmpty">暂无清理批次</div></div></section></main>
<div class="actions"><button id="selectVisible">勾选当前筛选</button><button id="clear">取消当前页</button><button id="protect">保护已勾选</button><span class="spacer"></span><button class="primary" id="preview">生成清理预览</button></div>
<dialog id="modal"><div class="modal-head">清理预览</div><div class="modal-body"><div id="previewBody"></div><p class="error" id="modalError" role="alert"></p></div><div class="modal-actions"><button id="closePreview">取消</button><button class="danger" id="apply" disabled>确认移入隔离区</button></div></dialog>
<script>
const CSRF=__CSRF__;let reviews=[],filtered=[],batches=[],personalSummary={},plan=null,activeGroup='',previewBusy=false;const selected=new Set();
const fmtBytes=n=>{for(const u of ['B','KiB','MiB','GiB','TiB']){if(n<1024)return `${n.toFixed(u==='B'?0:1)} ${u}`;n/=1024}return `${n.toFixed(1)} PiB`};
async function api(path,method='GET',body=null){const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Review-CSRF':CSRF},body:body?JSON.stringify(body):null});const data=await r.json();if(!r.ok)throw new Error(data.error||r.statusText);return data}
function selectable(r){return r.eligible_for_selection===true&&!r.protected}
function options(id,values){const e=document.getElementById(id),current=e.value;[...new Set(values)].sort().forEach(v=>{if(v&&!e.querySelector(`option[value="${CSS.escape(v)}"]`)){const o=document.createElement('option');o.value=o.textContent=v;e.append(o)}});e.value=current}
function render(){const q=search.value.trim().toLocaleLowerCase(),cs=candidate.value,ms=match.value;filtered=reviews.filter(r=>(!activeGroup||r.candidate_groups.includes(activeGroup))&&(!q||`${r.local.artist} ${r.local.album}`.toLocaleLowerCase().includes(q))&&(!cs||r.candidate_status===cs)&&(!ms||(r.canonical?.match_status||'NOT_FOUND')===ms));
rows.innerHTML=filtered.map(r=>{const ev=r.evidence.map(e=>`<div><a href="${safeUrl(e.source_album_url)}" target="_blank" rel="noreferrer">${esc(e.source)}</a>: ${e.raw_score}/${e.raw_scale} → ${e.normalized_score_100} (${e.rating_count??e.review_count??0})</div>`).join('')||'<div>没有社区评分，不按低分处理</div>';const pro=(r.professional_evidence||[]).map(e=>`<div><a href="${safeUrl(e.source_url)}" target="_blank" rel="noreferrer">${esc(e.publication)}</a>: ${esc(e.award||e.recommendation||e.raw_rating||'专业证据')} · ${Math.round(e.match_confidence*100)}%</div>`).join('');const cls=r.candidate_status.includes('REVIEW')||r.candidate_status==='LATER'?'warn':(selectable(r)?'':'block');const groups=r.candidate_groups.map(g=>`<span class="pill ${g.includes('REVIEW')||g==='LATER'?'warn':''}">${esc(g)}</span>`).join(' ');return `<tr><td><input type="checkbox" data-id="${esc(r.local.album_id)}" ${selected.has(r.local.album_id)?'checked':''} ${selectable(r)?'':'disabled'}></td><td><img class="cover" loading="lazy" src="/cover?album_id=${encodeURIComponent(r.local.album_id)}" onerror="this.onerror=null;this.src='data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='"></td><td>${esc(r.local.artist)}</td><td>${esc(r.local.album)}</td><td>${r.local.year??''}</td><td>${esc(r.canonical?.primary_type||'UNKNOWN')}</td><td class="score">${r.music_score===null?'—':r.music_score.toFixed(1)}</td><td class="sources"><details><summary>${r.independent_source_count} 个独立来源</summary>${ev}${pro}</details></td><td>${esc(r.canonical?.match_status||'NOT_FOUND')}</td><td>${groups}<div>${esc(r.base_candidate_status)}</div></td><td><span class="pill ${r.protected||r.protection_reasons.length?'block':''}">${esc(r.protection_reasons.join(', ')||'无')}</span></td><td>${esc(r.local.formats.join(', '))}</td><td>${fmtBytes(r.local.size_bytes)}</td><td><div class="path">${esc(r.local.path)}</div></td></tr>`}).join('');
document.querySelectorAll('input[data-id]').forEach(e=>e.onchange=()=>{e.checked?selected.add(e.dataset.id):selected.delete(e.dataset.id);summary()});summary()}
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}function safeUrl(s){try{const u=new URL(String(s));return ['http:','https:'].includes(u.protocol)?esc(u.href):'#'}catch{return '#'}}function summary(){visible.textContent=`当前 ${filtered.length} 张`;selectedEl.textContent=`已勾选 ${selected.size}`;bytes.textContent=`预计 ${fmtBytes(reviews.filter(r=>selected.has(r.local.album_id)).reduce((n,r)=>n+r.local.size_bytes,0))}`}
function renderBatches(){batchEmpty.hidden=batches.length>0;batchRows.innerHTML=batches.map(b=>{let actions='';if(b.status==='VERIFIED')actions=`<button data-batch-action="rollback" data-batch-id="${esc(b.batch_id)}">撤销</button><button class="danger" data-batch-action="purge" data-batch-id="${esc(b.batch_id)}">永久清空</button>`;if(b.status==='APPLYING')actions=`<button data-batch-action="recover" data-batch-id="${esc(b.batch_id)}">恢复中断</button>`;return `<tr><td>${esc(b.batch_id)}</td><td><span class="pill">${esc(b.status)}</span></td><td>${b.album_count??0}</td><td>${b.file_count??0}</td><td>${fmtBytes(b.size_bytes??0)}</td><td>${esc(b.created_at||'')}</td><td><div class="batch-buttons">${actions}</div></td></tr>`}).join('');document.querySelectorAll('[data-batch-action]').forEach(e=>e.onclick=()=>batchAction(e.dataset.batchAction,e.dataset.batchId))}
async function batchAction(action,id){const words={rollback:`ROLLBACK:${id}`,purge:`PURGE:${id}`,recover:`RECOVER:${id}`};const labels={rollback:'撤销隔离批次',purge:'永久删除隔离批次',recover:'回滚中断批次'};const confirmation=prompt(`${labels[action]}\n请输入：${words[action]}`);if(confirmation!==words[action])return;try{await api(`/api/${action}`,'POST',{batch_id:id,confirmation});await load()}catch(e){alert(e.message)}}
const selectedEl=document.getElementById('selected'),statusEl=document.getElementById('status'),modalEl=document.getElementById('modal'),previewBodyEl=document.getElementById('previewBody'),modalErrorEl=document.getElementById('modalError'),previewButton=document.getElementById('preview'),applyButton=document.getElementById('apply'),closePreviewButton=document.getElementById('closePreview');async function load(){[reviews,batches,personalSummary]=await Promise.all([api('/api/reviews'),api('/api/batches'),api('/api/personal-summary')]);options('candidate',reviews.map(r=>r.candidate_status));options('match',reviews.map(r=>r.canonical?.match_status||'NOT_FOUND'));strongCount.textContent=personalSummary.strong_low_score;reviewCount.textContent=personalSummary.review_65_to_70;explicitCount.textContent=personalSummary.explicit_user_candidates;laterCount.textContent=personalSummary.later;totalBytes.textContent=`唯一候选 ${personalSummary.total_unique_candidates} 张 · ${fmtBytes(personalSummary.estimated_reclaim_bytes)}`;render();renderBatches();statusEl.textContent='PERSONAL_POLICY_ACTIVE · 默认未勾选'}
[search,candidate,match].forEach(e=>e.oninput=render);document.querySelectorAll('[data-group]').forEach(e=>e.onclick=()=>{activeGroup=activeGroup===e.dataset.group?'':e.dataset.group;document.querySelectorAll('[data-group]').forEach(b=>b.classList.toggle('active',b.dataset.group===activeGroup));render()});refresh.onclick=load;selectVisible.onclick=()=>{filtered.filter(selectable).forEach(r=>selected.add(r.local.album_id));render()};clear.onclick=()=>{filtered.forEach(r=>selected.delete(r.local.album_id));render()};
protect.onclick=async()=>{for(const id of selected)await api('/api/protect','POST',{album_id:id,reason:'UI permanent protection'});selected.clear();await load()};
function openPreview(){if(modalEl.open)return;if(typeof modalEl.showModal==='function')modalEl.showModal();else modalEl.setAttribute('open','')}
function closePreview(){if(previewBusy)return;if(typeof modalEl.close==='function')modalEl.close();else modalEl.removeAttribute('open')}
function setPreviewBusy(value){previewBusy=value;previewButton.disabled=value;previewButton.textContent=value?'正在生成预览…':'生成清理预览';closePreviewButton.disabled=value}
previewButton.onclick=async()=>{if(previewBusy)return;plan=null;applyButton.disabled=true;modalErrorEl.textContent='';if(selected.size===0){previewBodyEl.innerHTML='<p>尚未勾选任何专辑。</p>';modalErrorEl.textContent='请先勾选至少一张候选专辑，再生成清理预览。';openPreview();return}setPreviewBusy(true);previewBodyEl.innerHTML=`<div class="preview-progress" role="status" aria-live="polite"><b>正在核验 ${selected.size} 张专辑</b><span>正在读取文件并计算完整校验值，NAS 上的大型专辑可能需要几分钟。</span><progress></progress></div>`;openPreview();statusEl.textContent=`正在生成 ${selected.size} 张专辑的清理预览…`;try{plan=await api('/api/preview','POST',{album_ids:[...selected]});previewBodyEl.innerHTML=`<div class="kv"><b>批次</b><span>${plan.batch_id}</span><b>专辑</b><span>${plan.album_count}</span><b>曲目</b><span>${plan.track_count}</span><b>文件</b><span>${plan.file_count}</span><b>空间</b><span>${fmtBytes(plan.size_bytes)}</span><b>隔离区</b><span>${esc(plan.quarantine_batch_root)}</span></div>`;applyButton.disabled=false;batches=await api('/api/batches');renderBatches();statusEl.textContent='清理预览已生成，等待人工确认'}catch(e){previewBodyEl.innerHTML='<p>清理预览生成失败。</p>';modalErrorEl.textContent=e instanceof Error?e.message:String(e);statusEl.textContent='清理预览生成失败'}finally{setPreviewBusy(false)}};
closePreviewButton.onclick=closePreview;
applyButton.onclick=async()=>{if(!plan)return;modalErrorEl.textContent='';try{await api('/api/apply','POST',{batch_id:plan.batch_id,confirmation_token:plan.confirmation_token,confirmation_phrase:`MOVE ${plan.album_count} ALBUMS`});closePreview();selected.clear();await load()}catch(e){modalErrorEl.textContent=e instanceof Error?e.message:String(e)}};
load().catch(e=>statusEl.textContent=e.message);
</script></body></html>"""


class ReviewServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: AlbumPruneService, library: Path, quarantine: Path):
        super().__init__(address, ReviewHandler)
        self.service = service
        self.library = library
        self.quarantine = quarantine
        self.csrf = secrets.token_urlsafe(24)
        self.preview_lock = threading.Lock()


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, object]:
        validate_loopback_request(self.headers, self.server.server_port, require_origin=True)
        if self.headers.get("X-Review-CSRF") != self.server.csrf:
            raise PermissionError("invalid CSRF token")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1024 * 1024:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            validate_loopback_request(self.headers, self.server.server_port)
            if parsed.path == "/":
                body = HTML.replace("__CSRF__", json.dumps(self.server.csrf)).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/reviews":
                self._json(self.server.service.personal_candidates())
            elif parsed.path == "/api/personal-summary":
                self._json(self.server.service.personal_candidate_report()["summary"])
            elif parsed.path == "/api/batches":
                self._json(self.server.service.batches())
            elif parsed.path == "/cover":
                album_id = parse_qs(parsed.query).get("album_id", [""])[0]
                self._cover(album_id)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)

    def _cover(self, album_id: str) -> None:
        from .store import ReviewStore

        with ReviewStore(self.server.service.store_path) as store:
            directory = Path(store.review(album_id).local.path)
        candidates = []
        for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png"):
            path = directory / name
            if path.is_file() and not path.is_symlink():
                candidates.append(path)
        if not candidates:
            self.send_response(204)
            self.end_headers()
            return
        data = candidates[0].read_bytes()
        if len(data) > MAX_COVER_BYTES:
            raise ValueError("cover exceeds 20 MiB")
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidates[0].name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            body = self._body()
            if self.path == "/api/protect":
                result = self.server.service.protect(str(body["album_id"]), str(body.get("reason", "")))
            elif self.path == "/api/selection":
                result = self.server.service.select([str(item) for item in body.get("album_ids", [])])
            elif self.path == "/api/plan":
                result = self.server.service.plan(
                    str(body["selection_id"]), self.server.library, self.server.quarantine
                )
            elif self.path == "/api/preview":
                if not self.server.preview_lock.acquire(blocking=False):
                    raise RuntimeError("已有清理预览正在生成，请等待当前核验完成")
                try:
                    result = self.server.service.preview(
                        [str(item) for item in body.get("album_ids", [])],
                        self.server.library,
                        self.server.quarantine,
                    )
                finally:
                    self.server.preview_lock.release()
            elif self.path == "/api/apply":
                batch_id = str(body["batch_id"])
                batch = next(
                    (item for item in self.server.service.batches() if item["batch_id"] == batch_id),
                    None,
                )
                if batch is None:
                    raise ValueError("unknown batch")
                expected = f"MOVE {batch['album_count']} ALBUMS"
                if body.get("confirmation_phrase") != expected:
                    raise ValueError(f"confirmation phrase must be: {expected}")
                result = self.server.service.apply(batch_id, str(body["confirmation_token"]))
            elif self.path == "/api/rollback":
                batch_id = str(body["batch_id"])
                result = self.server.service.rollback(batch_id, str(body.get("confirmation", "")))
            elif self.path == "/api/recover":
                batch_id = str(body["batch_id"])
                result = self.server.service.recover(batch_id, str(body.get("confirmation", "")))
            elif self.path == "/api/purge":
                batch_id = str(body["batch_id"])
                result = self.server.service.purge(batch_id, str(body.get("confirmation", "")))
            else:
                self._json({"error": "not found"}, 404)
                return
            self._json(result)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)


def serve(service: AlbumPruneService, library: Path, quarantine: Path, host: str, port: int) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("review server may only bind to loopback")
    server = ReviewServer((host, port), service, library, quarantine)
    print(f"Album review control: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()

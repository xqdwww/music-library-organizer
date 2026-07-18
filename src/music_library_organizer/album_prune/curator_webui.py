from __future__ import annotations

# The local-only UI is embedded so the curator can run without a frontend build step.
# ruff: noqa: E501
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import AlbumPruneService
from .web_security import LOOPBACK_HOSTS, validate_loopback_request

MAX_COVER_BYTES = 20 * 1024 * 1024

HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal Library Curator</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202124;background:#f5f6f7;letter-spacing:0}*{box-sizing:border-box}body{margin:0}header{height:58px;background:#fff;border-bottom:1px solid #d9dde3;display:flex;align-items:center;padding:0 22px;gap:16px;position:sticky;top:0;z-index:3}h1{font-size:18px;margin:0}.status{font-size:12px;color:#5f6368}.toolbar{display:grid;grid-template-columns:minmax(220px,1fr) 190px 210px auto;gap:8px;padding:12px 22px;background:#fff;border-bottom:1px solid #d9dde3}.toolbar>*{width:100%;min-width:0}input,select,button{height:34px;border:1px solid #b8bec7;border-radius:6px;background:#fff;padding:0 10px;font:inherit}button{cursor:pointer;font-weight:600}.filters{display:flex;gap:7px;flex-wrap:wrap;padding:10px 22px;background:#fff;border-bottom:1px solid #e2e5e9}.filters button{height:30px;font-size:12px}.filters button.active{border-color:#1769aa;background:#e5f1fa;color:#0b4f7c}main{padding:16px 22px 76px}.cards{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:8px;margin-bottom:12px}.card{background:#fff;border:1px solid #d9dde3;border-radius:7px;padding:10px}.card b{display:block;font-size:20px;margin-top:3px}.summary{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:#4c5158;margin-bottom:10px}.table-wrap{overflow:auto;background:#fff;border:1px solid #d9dde3;border-radius:7px}table{border-collapse:collapse;width:100%;min-width:1720px}th,td{padding:8px 9px;border-bottom:1px solid #eceff2;text-align:left;font-size:12px;vertical-align:middle}th{position:sticky;top:0;background:#f8f9fa;color:#555b64;z-index:1}tr:hover{background:#f7fbff}.cover{width:44px;height:44px;object-fit:cover;background:#e4e7eb;border-radius:4px}.score{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums}.path{max-width:300px;overflow-wrap:anywhere}.evidence{max-width:370px}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#e6f0f8;color:#15527c;font-size:11px;white-space:nowrap}.pill.keep{background:#e6f4ea;color:#176b36}.pill.review{background:#fff4d6;color:#795600}.pill.low{background:#fce8e6;color:#9b271e}.pill.protect{background:#eee9fa;color:#59408d}.actions{position:fixed;left:0;right:0;bottom:0;padding:10px 22px;border-top:1px solid #d9dde3;background:#fff;display:flex;gap:12px;align-items:center}.actions .spacer{flex:1}.muted{color:#69717b}details summary{cursor:pointer}@media(max-width:760px){header{padding:0 12px}.toolbar{grid-template-columns:1fr 1fr;padding:10px 12px}.toolbar input{grid-column:1/-1}.filters{padding:8px 12px}.cards{grid-template-columns:1fr 1fr}main{padding:12px}.actions{position:static;padding:10px 12px;flex-wrap:wrap}.actions .muted{flex:1 0 100%}.actions button{font-size:12px}}
</style></head><body><header><h1>Personal Library Curator</h1><span class="status" id="status">READ_ONLY · 未自动选择</span></header>
<section class="toolbar"><input id="search" placeholder="艺术家、专辑或路径"><select id="recommendation"><option value="">全部建议</option><option>KEEP</option><option>REVIEW</option><option>LOW_PERSONAL_VALUE</option><option>DUPLICATE_VALUE</option><option>PROTECTED_COLLECTION</option></select><select id="scoreRange"><option value="">全部个人价值分</option><option value="0-40">0–40</option><option value="40-60">40–60</option><option value="60-80">60–80</option><option value="80-100">80–100</option></select><button id="refresh">刷新</button></section>
<section class="filters"><button data-filter="zero">0 播放</button><button data-filter="stale">5 年以上未播放</button><button data-filter="duplicate">重复版本</button><button data-filter="low">低个人价值</button><button data-filter="classical">古典</button><button data-filter="jazz">爵士</button><button data-filter="chinese">中文</button><button data-filter="japanese">日文</button><button data-filter="soundtrack">Soundtrack</button></section>
<main><section class="cards"><div class="card">保留<b id="keepCount">0</b></div><div class="card">人工审核<b id="reviewCount">0</b></div><div class="card">低个人价值<b id="lowCount">0</b></div><div class="card">重复价值<b id="duplicateCount">0</b></div><div class="card">收藏保护<b id="protectedCount">0</b></div></section><div class="summary"><span id="visible"></span><span id="selected">已勾选 0</span><span id="candidateBytes"></span><span id="signalCoverage"></span></div><div class="table-wrap"><table><thead><tr><th></th><th>封面</th><th>艺术家</th><th>专辑</th><th>年份</th><th>建议</th><th>个人价值</th><th>个人信号</th><th>公共质量</th><th>收藏保护</th><th>重复版本</th><th>类型</th><th>格式</th><th>大小</th><th>本地路径</th></tr></thead><tbody id="rows"></tbody></table></div></main>
<div class="actions"><button id="checkVisible">勾选当前筛选</button><button id="clear">全部取消</button><span class="muted">仅用于页面审核，不会创建 selection、plan 或隔离批次。</span><span class="spacer"></span><b id="runId"></b></div>
<script>
let report=null,albums=[],filtered=[],activeFilter='';const selectedIds=new Set();const $=id=>document.getElementById(id);const fmtBytes=n=>{for(const u of ['B','KiB','MiB','GiB','TiB']){if(n<1024)return `${n.toFixed(u==='B'?0:1)} ${u}`;n/=1024}return `${n.toFixed(1)} PiB`};const esc=s=>{const d=document.createElement('div');d.textContent=s??'';return d.innerHTML};const ageYears=s=>s?((Date.now()-Date.parse(s))/31557600000):Infinity;
async function load(){const response=await fetch('/api/curator',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||response.statusText);report=data;albums=data.albums;const s=data.summary;$('keepCount').textContent=s.keep_count;$('reviewCount').textContent=s.review_count;$('lowCount').textContent=s.low_personal_value_count;$('duplicateCount').textContent=s.duplicate_value_count;$('protectedCount').textContent=s.protected_count;$('candidateBytes').textContent=`审核候选 ${s.review_candidate_count} 张 · ${fmtBytes(s.review_candidate_bytes)}`;$('signalCoverage').textContent=`个人信号覆盖 ${albums.filter(a=>a.personal_signal.observed).length} 张`;$('runId').textContent=data.run_id||'';render()}
function filterMatch(a){if(!activeFilter)return true;if(activeFilter==='zero')return a.personal_signal.observed&&a.personal_signal.play_count===0;if(activeFilter==='stale')return a.personal_signal.observed&&ageYears(a.personal_signal.last_played_at)>5;if(activeFilter==='duplicate')return !!a.duplicate_group_id;if(activeFilter==='low')return a.recommendation==='LOW_PERSONAL_VALUE';if(activeFilter==='classical')return a.category==='Classical';if(activeFilter==='jazz')return a.category==='Jazz';if(activeFilter==='chinese')return ['ZH_CONFIRMED','HK_TW_CANTONESE'].includes(a.language_bucket);if(activeFilter==='japanese')return a.language_bucket==='JA_CONFIRMED';if(activeFilter==='soundtrack')return a.album_type==='Soundtrack'||a.path.includes('/Soundtrack/');return true}
function render(){const query=$('search').value.trim().toLocaleLowerCase(),rec=$('recommendation').value,range=$('scoreRange').value;filtered=albums.filter(a=>(!query||`${a.artist} ${a.album} ${a.path}`.toLocaleLowerCase().includes(query))&&(!rec||a.recommendation===rec)&&filterMatch(a)&&(!range||(()=>{const [lo,hi]=range.split('-').map(Number);return a.personal_value_score>=lo&&a.personal_value_score<=(hi===100?100:hi)})()));$('rows').innerHTML=filtered.map(a=>{const p=a.personal_signal;const cls=a.recommendation==='KEEP'?'keep':a.recommendation==='PROTECTED_COLLECTION'?'protect':a.recommendation==='LOW_PERSONAL_VALUE'||a.recommendation==='DUPLICATE_VALUE'?'low':'review';const personal=p.observed?`播放 ${p.play_count??0}<br>最后 ${p.last_played_at?esc(p.last_played_at.slice(0,10)):'从未'}<br>评分 ${p.rating??'—'} · 歌单 ${p.playlist_count}`:'没有导入或未可靠匹配';const publicEvidence=a.public_music_quality_evidence.map(e=>`${esc(e.source)} ${e.score}`).join('<br>')||'无评分，使用中性值';const collector=a.collector_protection_reason.map(esc).join('<br>')||'无特别保护证据';const duplicate=a.duplicate_group_id?`${esc(a.duplicate_group_id)}<br>${a.preferred_release_candidate?'建议保留版本':'非首选版本'}<br>${a.duplicate_reason.map(esc).join(', ')}`:'—';return `<tr><td><input type="checkbox" data-id="${esc(a.album_id)}" ${selectedIds.has(a.album_id)?'checked':''}></td><td><img class="cover" loading="lazy" src="/cover?album_id=${encodeURIComponent(a.album_id)}" onerror="this.removeAttribute('src')"></td><td>${esc(a.artist)}</td><td>${esc(a.album)}</td><td>${a.year??''}</td><td><span class="pill ${cls}">${esc(a.recommendation)}</span><br>${a.recommendation_reason.map(esc).join('<br>')}</td><td class="score">${a.personal_value_score.toFixed(1)}</td><td>${personal}</td><td><details><summary>${a.public_music_quality_score.toFixed(1)}</summary>${publicEvidence}</details></td><td class="evidence"><details><summary>${a.collector_protected?'已保护':'查看证据'}</summary>${collector}</details></td><td>${duplicate}</td><td>${esc(a.category)}<br>${esc(a.album_type)}</td><td>${a.formats.map(esc).join(', ')}</td><td>${fmtBytes(a.size_bytes)}</td><td><div class="path">${esc(a.path)}</div></td></tr>`}).join('');document.querySelectorAll('input[data-id]').forEach(e=>e.onchange=()=>{e.checked?selectedIds.add(e.dataset.id):selectedIds.delete(e.dataset.id);summary()});summary()}
function summary(){$('visible').textContent=`当前 ${filtered.length} / ${albums.length} 张`;$('selected').textContent=`已勾选 ${selectedIds.size}`}
[$('search'),$('recommendation'),$('scoreRange')].forEach(e=>e.oninput=render);document.querySelectorAll('[data-filter]').forEach(e=>e.onclick=()=>{activeFilter=activeFilter===e.dataset.filter?'':e.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===activeFilter));render()});$('refresh').onclick=load;$('checkVisible').onclick=()=>{filtered.forEach(a=>selectedIds.add(a.album_id));render()};$('clear').onclick=()=>{selectedIds.clear();render()};load().catch(e=>$('status').textContent=e.message);
</script></body></html>"""


class CuratorServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: AlbumPruneService):
        super().__init__(address, CuratorHandler)
        self.service = service


class CuratorHandler(BaseHTTPRequestHandler):
    server: CuratorServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            validate_loopback_request(self.headers, self.server.server_port)
            if parsed.path == "/":
                body = HTML.encode()
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
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/curator":
                self._json(self.server.service.curator_report())
            elif parsed.path == "/cover":
                self._cover(parse_qs(parsed.query).get("album_id", [""])[0])
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        self._json({"error": "Personal Curator is read-only"}, 405)

    def _cover(self, album_id: str) -> None:
        report = self.server.service.curator_report()
        row = next((item for item in report["albums"] if item["album_id"] == album_id), None)
        if row is None:
            self._json({"error": "unknown album"}, 404)
            return
        directory = Path(row["path"])
        candidate = next(
            (
                directory / name
                for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png")
                if (directory / name).is_file() and not (directory / name).is_symlink()
            ),
            None,
        )
        if candidate is None:
            self.send_response(204)
            self.end_headers()
            return
        if candidate.stat().st_size > MAX_COVER_BYTES:
            raise ValueError("cover exceeds 20 MiB")
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve_curator(service: AlbumPruneService, host: str, port: int) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("curator server may only bind to loopback")
    server = CuratorServer((host, port), service)
    print(f"Personal Library Curator: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()

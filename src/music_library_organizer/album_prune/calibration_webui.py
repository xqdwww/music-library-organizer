from __future__ import annotations

# Embedded to keep the loopback-only calibration control deployable without a frontend build.
# ruff: noqa: E501
import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import AlbumPruneService
from .web_security import LOOPBACK_HOSTS, validate_loopback_request

MAX_COVER_BYTES = 20 * 1024 * 1024

HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>个人专辑校准</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202124;background:#f5f6f7;letter-spacing:0}*{box-sizing:border-box}body{margin:0}button,select,input{font:inherit;border:1px solid #bdc1c6;border-radius:6px;background:#fff;min-height:36px;padding:6px 10px}button{cursor:pointer;font-weight:600}button:disabled{opacity:.42;cursor:not-allowed}header{height:58px;background:#fff;border-bottom:1px solid #dfe1e5;display:flex;align-items:center;padding:0 22px;gap:16px;position:sticky;top:0;z-index:3}h1{font-size:18px;margin:0}.status{font-size:12px;color:#5f6368}.toolbar{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;padding:12px 22px;background:#fff;border-bottom:1px solid #dfe1e5}.toolbar input{min-width:0}.layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:18px;padding:18px 22px 92px;max-width:1500px;margin:auto}.album{background:#fff;border:1px solid #dfe1e5;border-radius:8px;overflow:hidden}.identity{display:grid;grid-template-columns:190px minmax(0,1fr);gap:20px;padding:20px}.cover{width:190px;height:190px;object-fit:cover;background:#e8eaed;border-radius:6px}.eyebrow{font-size:12px;color:#5f6368;margin-bottom:6px}.title{font-size:24px;font-weight:700;margin:0 0 5px}.artist{font-size:16px;color:#3c4043}.score{font-size:34px;font-weight:700;margin:18px 0 6px}.meta{display:grid;grid-template-columns:150px minmax(0,1fr);gap:7px 12px;padding:0 20px 20px;font-size:13px}.meta dt{color:#5f6368}.meta dd{margin:0;overflow-wrap:anywhere}.evidence{border-top:1px solid #eceff1;padding:18px 20px}.evidence h2,.side h2{font-size:15px;margin:0 0 10px}.evidence-row{padding:9px 0;border-bottom:1px solid #eceff1;font-size:13px}.side{display:flex;flex-direction:column;gap:12px}.panel{background:#fff;border:1px solid #dfe1e5;border-radius:8px;padding:14px}.panel label{font-size:12px;color:#5f6368;display:block;margin:10px 0 4px}.panel select{width:100%}.decision{display:grid;grid-template-columns:1fr;gap:7px}.decision button.active{outline:2px solid #1967d2;background:#e8f0fe}.keep{color:#137333}.delete{color:#b3261e}.later{color:#7a4f01}.safe{background:#e6f4ea;color:#137333;padding:9px;border-radius:6px;font-size:12px}.danger-disabled{display:grid;grid-template-columns:1fr 1fr;gap:6px}.nav{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #dfe1e5;padding:11px 22px;display:flex;gap:8px;align-items:center;z-index:3}.nav .grow{flex:1}.counter{font-size:13px;color:#5f6368}.empty{padding:40px;text-align:center}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#e8f0fe;color:#174ea6;font-size:11px}
@media(max-width:900px){.toolbar{grid-template-columns:1fr 1fr}.layout{grid-template-columns:1fr;padding:14px 14px 100px}.side{order:-1}.identity{grid-template-columns:100px minmax(0,1fr);padding:14px;gap:14px}.cover{width:100px;height:100px}.title{font-size:19px}.score{font-size:26px;margin:8px 0}.meta{grid-template-columns:110px minmax(0,1fr);padding:0 14px 14px}.nav{padding:10px 14px;overflow:auto}.nav button,.counter{white-space:nowrap}.shortcuts{display:none}}
</style></head><body>
<header><h1>个人专辑校准</h1><span class="status" id="status">只读音乐库 · 决定仅保存到校准数据库</span></header>
<section class="toolbar"><input id="query" placeholder="艺术家或专辑"><select id="category"><option value="">全部类别</option><option>Popular/Rock/Folk</option><option>Jazz</option><option>Classical</option><option>Other</option></select><select id="score"><option value="">全部分数</option><option value="unrated">无评分</option><option value="0-50">0–50</option><option value="50-70">50–70</option><option value="70-100">70–100</option></select><select id="sources"><option value="">全部来源</option><option value="0">0 来源</option><option value="1">1 来源</option><option value="2">2+ 来源</option></select><select id="match"><option value="">全部匹配</option></select><select id="decisionFilter"><option value="">全部决定</option><option>UNREVIEWED</option><option>KEEP</option><option>DELETE_CANDIDATE</option><option>LATER</option></select><select id="feature"><option value="">全部特征</option><option value="classical">古典</option><option value="jazz">爵士</option><option value="professional">有专业评价</option><option value="recommendation">专业推荐</option><option value="award">奖项</option><option value="reference">参考录音</option><option value="score_conflict">社区与专业冲突</option><option value="japanese">日文</option><option value="chinese">中文</option><option value="multi">多版本</option><option value="unrated">无评分</option><option value="conflict">来源冲突</option></select></section>
<main class="layout"><section id="album" class="album"></section><aside class="side"><section class="panel"><h2>数据来源</h2><div class="safe">Discogs 评分旁的来源链接指向对应页面。Data provided by Discogs.</div></section><section class="panel"><h2>人工决定</h2><div class="decision"><button class="keep" data-decision="KEEP">保留</button><button class="delete" data-decision="DELETE_CANDIDATE">删除候选</button><button class="later" data-decision="LATER">以后再看</button></div><label for="matchFeedback">匹配反馈</label><select id="matchFeedback"><option>CORRECT</option><option>WRONG</option><option selected>UNSURE</option></select><label for="ratingFeedback">评分反馈</label><select id="ratingFeedback"><option>CORRECT</option><option>WRONG</option><option>INCOMPLETE</option><option selected>UNSURE</option></select><button id="save">保存反馈</button><button id="queueProtect">加入批量保护</button><button id="protectQueued">批量保护（0）</button></section><section class="panel"><h2>安全边界</h2><div class="safe">校准模式不提供隔离、删除、apply 或 purge 接口。</div><div class="danger-disabled"><button disabled>隔离</button><button disabled>永久清空</button></div></section><section class="panel"><h2>校准结果</h2><div id="report"></div><button id="exportButton">导出校准结果</button></section></aside></main>
<div class="nav"><button id="previous">上一张</button><button id="next">下一张</button><span class="counter" id="counter"></span><span class="grow"></span><span class="counter shortcuts">快捷键：← →，K 保留，D 候选，L 稍后，S 保存</span></div>
<script>const CSRF=__CSRF__,BATCH=__BATCH__;let all=[],rows=[],index=0;const protectionQueue=new Set();
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=s=>{try{const u=new URL(String(s));return ['http:','https:'].includes(u.protocol)?esc(u.href):'#'}catch{return '#'}};
const fmt=n=>{for(const u of ['B','KiB','MiB','GiB','TiB']){if(n<1024)return `${n.toFixed(u==='B'?0:1)} ${u}`;n/=1024}return `${n.toFixed(1)} PiB`};
async function api(path,method='GET',body=null){const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Calibration-CSRF':CSRF},body:body?JSON.stringify(body):null});const value=await r.json();if(!r.ok)throw new Error(value.error||r.statusText);return value}
function current(){return rows[index]}
function applyFilters(){const q=query.value.trim().toLocaleLowerCase(),band=score.value,src=sources.value,feat=feature.value;rows=all.filter(r=>{const s=r.music_score,c=r.local.category,d=r.feedback.user_decision,m=r.canonical?.match_status||'NOT_FOUND',p=r.professional_evidence||[],b=r.local.language_bucket;const featureMatch=!feat||(feat==='classical'?c==='Classical':feat==='jazz'?c==='Jazz':feat==='professional'?p.length>0:feat==='recommendation'?r.professional_recommendation_count>0:feat==='award'?r.professional_award_count>0:feat==='reference'?p.some(e=>e.reference_recording_status):feat==='score_conflict'?r.community_score!==null&&r.professional_score!==null&&Math.abs(r.community_score-r.professional_score)>20:feat==='japanese'?b==='JA_CONFIRMED':feat==='chinese'?['ZH_CONFIRMED','HK_TW_CANTONESE'].includes(b):feat==='multi'?r.local.duplicate_local_versions>1:feat==='unrated'?s===null:r.rating_status==='SOURCE_CONFLICT');return(!q||`${r.local.artist} ${r.local.album}`.toLocaleLowerCase().includes(q))&&(!category.value||c===category.value)&&(!band||(band==='unrated'?s===null:band==='0-50'?s!==null&&s<=50:band==='50-70'?s>50&&s<=70:s>70))&&(!src||(src==='2'?r.evidence.length>=2:r.evidence.length===Number(src)))&&(!match.value||m===match.value)&&(!decisionFilter.value||d===decisionFilter.value)&&featureMatch});index=0;render()}
function detail(r){const l=r.local,canonical=r.canonical||{};return [['流派',l.genres.join(', ')||'—'],['类型',canonical.primary_type||l.album_type],['匹配',canonical.match_status||'NOT_FOUND'],['匹配依据',canonical.match_basis],['语言路由',`${l.language_bucket} · ${(l.language_evidence?.evidence||[]).join('; ')}`],['本地路径',l.path],['格式',l.formats.join(', ')],['大小',fmt(l.size_bytes)],['作曲家',l.composer],['作品',l.work],['指挥',l.conductor],['乐团',l.orchestra],['独奏者',l.soloists.join(', ')],['leader',l.leader],['session personnel',l.session_personnel.join(', ')],['录音日期',l.recording_date],['原始发行年',l.original_release_year],['厂牌',l.label],['目录号',l.catalog_number],['发行版本',l.edition],['现场/录音室',l.live_studio],['release / release-group',`${l.release_mbid||'—'} / ${l.release_group_mbid||'—'}`],['专业保护理由',(r.protection_reasons||[]).join(', ')||'—'],['历史或目录意义',l.historical_or_catalog_significance.join(', ')||'—']].map(([a,b])=>`<dt>${a}</dt><dd>${esc(b??'—')}</dd>`).join('')}
function render(){const r=current();if(!r){album.innerHTML='<div class="empty">当前筛选没有专辑</div>';counter.textContent='0 / 0';return}const ev=r.evidence.map(e=>`<div class="evidence-row"><b>${esc(e.source)}</b> ${e.raw_score}/${e.raw_scale} → ${e.normalized_score_100} · ${e.rating_count??e.review_count??0} 人 · <a href="${safeUrl(e.source_album_url)}" target="_blank" rel="noreferrer">来源</a></div>`).join('')||'<div class="evidence-row">没有找到社区评价，不因此判低分</div>';const pro=(r.professional_evidence||[]).map(e=>`<div class="evidence-row"><b>${esc(e.publication)}</b> · ${esc(e.award||e.recommendation||e.raw_rating||'')} · ${esc(e.recording_identity)} · 匹配 ${Math.round(e.match_confidence*100)}% · ${esc(e.conversion_rule)} · <a href="${safeUrl(e.source_url)}" target="_blank" rel="noreferrer">来源</a></div>`).join('')||'<div class="evidence-row">没有找到已验证专业评价；这不等于评价较低</div>';const scores=[['社区',r.community_score],['乐评',r.critic_score],['专业',r.professional_score],['综合',r.music_score]].map(([k,v])=>`${k} ${v===null?'—':v.toFixed(1)}`).join(' · ');album.innerHTML=`<div class="identity"><img class="cover" src="/cover?album_id=${encodeURIComponent(r.local.album_id)}"><div><div class="eyebrow">${esc(r.local.category)} · ${esc(r.local.album_type)} · ${r.local.year??'年份未知'}</div><div class="title">${esc(r.local.album)}</div><div class="artist">${esc(r.local.artist)}</div><div class="score">${scores}</div><span class="pill">${esc(r.rating_status)}</span></div></div><dl class="meta">${detail(r)}</dl><section class="evidence"><h2>评分证据</h2>${ev}<h2 style="margin-top:18px">专业评价证据</h2>${pro}</section>`;matchFeedback.value=r.feedback.match_feedback;ratingFeedback.value=r.feedback.rating_feedback;document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===r.feedback.user_decision));counter.textContent=`${index+1} / ${rows.length} · 总样本 ${all.length}`;previous.disabled=index===0;next.disabled=index>=rows.length-1}
async function saveDecision(decision){const r=current();if(!r)return;if(decision)r.feedback.user_decision=decision;r.feedback.match_feedback=matchFeedback.value;r.feedback.rating_feedback=ratingFeedback.value;r.feedback=await api('/api/calibration/feedback','POST',{album_id:r.local.album_id,user_decision:r.feedback.user_decision,match_feedback:r.feedback.match_feedback,rating_feedback:r.feedback.rating_feedback,calibration_batch_id:BATCH});render();await loadReport()}
async function loadReport(){const r=await api('/api/calibration/report');report.textContent=`已标注 ${r.labels_available} · ${r.report_status}`}
document.querySelectorAll('.toolbar input,.toolbar select').forEach(e=>e.addEventListener('input',applyFilters));document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>saveDecision(b.dataset.decision));previous.onclick=()=>{if(index>0){index--;render()}};next.onclick=()=>{if(index<rows.length-1){index++;render()}};save.onclick=()=>saveDecision();queueProtect.onclick=()=>{const r=current();if(r){protectionQueue.add(r.local.album_id);protectQueued.textContent=`批量保护（${protectionQueue.size}）`}};protectQueued.onclick=async()=>{for(const album_id of protectionQueue)await api('/api/calibration/protect','POST',{album_id});status.textContent=`已保护 ${protectionQueue.size} 张，仅写入审核数据库`;protectionQueue.clear();protectQueued.textContent='批量保护（0）'};document.getElementById('exportButton').onclick=async()=>{const value=await api('/api/calibration/export');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.download=`calibration-${BATCH}.json`;a.click();URL.revokeObjectURL(a.href)};document.addEventListener('keydown',e=>{if(['INPUT','SELECT'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')previous.click();if(e.key==='ArrowRight')next.click();if(e.key.toLowerCase()==='k')saveDecision('KEEP');if(e.key.toLowerCase()==='d')saveDecision('DELETE_CANDIDATE');if(e.key.toLowerCase()==='l')saveDecision('LATER');if(e.key.toLowerCase()==='s')saveDecision()});
Promise.all([api('/api/calibration/sample'),loadReport()]).then(([data])=>{all=data.reviews;rows=all;[...new Set(all.map(r=>r.canonical?.match_status||'NOT_FOUND'))].sort().forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;match.append(o)});render()}).catch(e=>{status.textContent=e.message});</script></body></html>"""


class CalibrationServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: AlbumPruneService, batch_id: str):
        super().__init__(address, CalibrationHandler)
        self.service = service
        self.batch_id = batch_id
        self.csrf = secrets.token_urlsafe(24)


class CalibrationHandler(BaseHTTPRequestHandler):
    server: CalibrationServer

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
        if self.headers.get("X-Calibration-CSRF") != self.server.csrf:
            raise PermissionError("invalid CSRF token")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1024 * 1024:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        try:
            validate_loopback_request(self.headers, self.server.server_port)
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = HTML.replace("__CSRF__", json.dumps(self.server.csrf)).replace("__BATCH__", json.dumps(self.server.batch_id)).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/calibration/sample":
                self._json(self.server.service.calibration_sample(self.server.batch_id))
            elif parsed.path == "/api/calibration/report":
                self._json(self.server.service.calibration_report())
            elif parsed.path == "/api/calibration/export":
                sample = self.server.service.calibration_sample(self.server.batch_id)
                self._json({"calibration_batch_id": self.server.batch_id, "strategy_version": sample["strategy_version"], "random_seed": sample["random_seed"], "feedback": [{"album_id": row["local"]["album_id"], **row["feedback"]} for row in sample["reviews"]]})
            elif parsed.path == "/cover":
                self._cover(parse_qs(parsed.query).get("album_id", [""])[0])
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)

    def _cover(self, album_id: str) -> None:
        from .store import ReviewStore
        with ReviewStore(self.server.service.store_path) as store:
            directory = Path(store.review(album_id).local.path)
        path = next((directory / name for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png") if (directory / name).is_file() and not (directory / name).is_symlink()), None)
        if path is None:
            self.send_response(204)
            self.end_headers()
            return
        data = path.read_bytes()
        if len(data) > MAX_COVER_BYTES:
            raise ValueError("cover exceeds 20 MiB")
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            body = self._body()
            if self.path == "/api/calibration/feedback":
                result = self.server.service.save_calibration_feedback(**{key: str(body[key]) for key in ("album_id", "user_decision", "match_feedback", "rating_feedback", "calibration_batch_id")})
            elif self.path == "/api/calibration/protect":
                result = self.server.service.protect(str(body["album_id"]), "personal calibration protection")
            else:
                self._json({"error": "not found"}, 404)
                return
            self._json(result)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)


def serve_calibration(service: AlbumPruneService, batch_id: str, host: str, port: int) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("calibration server may only bind to loopback")
    server = CalibrationServer((host, port), service, batch_id)
    print(f"Personal calibration control: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()

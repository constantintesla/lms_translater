/* Whole-course panel. Loaded after the main script; relies on $, api, media, esc, STATUS, refresh(), current, dirty. */
const KIND = { title: "заголовок", html: "текст с разметкой" };

async function showCourse(j, full) {
  const key = ["course", j.id, j.status, j.has_texts, j.error, JSON.stringify(j.videos), j.pushed_course_id].join("|");
  if (!full && key === shownKey) return;                       // polling must not wipe edits in progress
  shownKey = key;
  const done = ["awaiting_review", "done", "failed"].includes(j.status);
  let html = `<div class="card"><h2>${esc(j.title)}</h2><div class="mut">${STATUS[j.status] || j.status}
    ${j.videos && j.videos.total ? ` · видео: ${j.videos.done}/${j.videos.total} готово` + (j.videos.awaiting_review ? `, ${j.videos.awaiting_review} ждут проверки` : "") + (j.videos.failed ? `, ${j.videos.failed} с ошибкой` : "") : ""}</div>`;
  if (j.error) html += `<pre class="bad">${esc(j.error)}</pre>`;
  if (j.status === "awaiting_review") html += `<div class="row" style="margin:8px 0"><button class="primary" id="cApprove">Тексты проверены: собрать пакет</button></div>`;
  if (done) html += `<div class="row" style="margin:8px 0"><select id="cStage"><option value="texts">заново перевести тексты</option><option value="pdfs">заново перевести документы</option><option value="snapshot">всё заново (с чтением курса)</option><option value="package">пересобрать пакет</option></select><button id="cRetry">Повторить</button></div>`;
  if (j.has_texts) html += `<details open><summary><b>Тексты</b> <span class="mut" id="cTextsInfo"></span></summary><div id="cTexts"></div></details>
    <details><summary><b>Документы</b></summary><div id="cDocs"></div></details>
    <details><summary><b>Видео</b></summary><div id="cVideos"></div></details>
    <details><summary><b>Пакет для ручной загрузки</b></summary><div id="cPackage"></div></details>
    <details><summary><b>Отправка текстов в LMS</b></summary><div id="cPush"></div></details>`;
  html += `<details><summary class="mut">Лог</summary><pre id="log"></pre></details></div>`;
  $("#detail").innerHTML = html;
  $("#log").textContent = (await api(`/jobs/${j.id}/log`)).log;
  const id = j.id, call = (p, o) => api(p, o).then(() => { dirty = false; refresh(); showJob(id, true); }).catch(e => alert(e.message));
  if ($("#cApprove")) $("#cApprove").onclick = () => saveTexts(id).then(() => call(`/jobs/${id}/approve`, { method: "POST" })).catch(e => alert(e.message));
  if ($("#cRetry")) $("#cRetry").onclick = () => call(`/courses/${id}/retry?stage=${$("#cStage").value}`, { method: "POST" });
  if (!j.has_texts) return;
  loadTexts(j); loadDocs(id); loadVideos(id); loadPackage(id, done); loadPush(j, done);
}

async function loadTexts(j) {
  const rows = await api(`/courses/${j.id}/texts`), editable = j.status === "awaiting_review";
  const bad = rows.filter(r => !r.markup_ok).length;
  $("#cTextsInfo").textContent = `· ${rows.length} строк` + (bad ? `, ${bad} с нарушенной разметкой` : "");
  $("#cTexts").innerHTML = `<table><thead><tr><th>Тип</th><th>Оригинал</th><th>Перевод</th></tr></thead><tbody>${rows.map(r => `<tr>
    <td class="mut">${KIND[r.kind] || r.kind}${r.markup_ok ? "" : ` <span class="bad" title="теги не совпадают с оригиналом">⚠</span>`}</td>
    <td>${esc(r.src)}</td><td>${editable ? `<textarea data-tid="${r.id}">${esc(r.tgt)}</textarea>` : esc(r.tgt)}</td></tr>`).join("")}</tbody></table>
    ${editable ? `<div class="row" style="margin-top:8px"><button id="cSave">Сохранить правки</button></div>` : ""}`;
  $("#cTexts").addEventListener("input", () => { dirty = true; });
  if ($("#cSave")) $("#cSave").onclick = () => saveTexts(j.id).then(() => { dirty = false; alert("Сохранено"); }).catch(e => alert(e.message));
}

function saveTexts(id) {
  const edits = [...document.querySelectorAll("#cTexts textarea")].map(t => ({ id: t.dataset.tid, tgt: t.value }));
  return edits.length ? api(`/courses/${id}/texts`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(edits) }) : Promise.resolve();
}

async function loadDocs(id) {
  const docs = await api(`/courses/${id}/documents`);
  $("#cDocs").innerHTML = docs.length ? `<table><thead><tr><th>Документ</th><th>Стр.</th><th>Абзацев</th><th>Сжато / не влезло</th></tr></thead><tbody>${docs.map(d => `<tr>
    <td><a href="${media(`/courses/${id}/files/${d.material_id}_ru.pdf`)}" target="_blank">${esc(d.name)}</a></td><td>${d.pages}</td><td>${d.blocks}</td>
    <td class="${d.overflow ? "warn" : "mut"}">${d.shrunk} / ${d.overflow}</td></tr>`).join("")}</tbody></table>
    <div class="mut" style="margin-top:6px">Текст на картинках внутри документов не переводится. Откройте файл и проверьте вёрстку.</div>` : `<span class="mut">Документов нет</span>`;
}

async function loadVideos(id) {
  const vs = await api(`/courses/${id}/videos`);
  $("#cVideos").innerHTML = vs.length ? `<table><tbody>${vs.map(v => `<tr><td>${v.job ? esc(v.job.title) : v.material_id}</td>
    <td><span class="badge ${v.job ? v.job.status : ""}">${v.job ? (STATUS[v.job.status] || v.job.status) : "—"}</span></td>
    <td>${v.job ? `<a href="#" data-open="${v.job.id}">открыть</a>` : ""}</td></tr>`).join("")}</tbody></table>
    <div class="row" style="margin-top:8px"><button id="cApproveAll">Утвердить все переводы видео</button></div>` : `<span class="mut">Озвучка видео для этого курса не запускалась</span>`;
  $("#cVideos").addEventListener("click", e => { const a = e.target.closest("[data-open]"); if (a) { e.preventDefault(); current = a.dataset.open; dirty = false; showJob(current, true); refresh(); } });
  if ($("#cApproveAll")) $("#cApproveAll").onclick = async () => { try { const r = await api(`/courses/${id}/approve_videos`, { method: "POST" }); alert(`Утверждено: ${r.approved}`); refresh(); } catch (e) { alert(e.message); } };
}

async function loadPackage(id, done) {
  $("#cPackage").innerHTML = `<div class="row"><button id="cBuild" ${done ? "" : "disabled"}>Пересобрать пакет</button><a id="cZip" href="${media(`/courses/${id}/package.zip`)}">Скачать zip</a></div><pre id="cReadme" style="max-height:320px"></pre>`;
  const show = async () => { try { $("#cReadme").textContent = (await api(`/courses/${id}/package/readme`)).readme; } catch (e) { $("#cReadme").textContent = "Пакет ещё не собран: он собирается после утверждения текстов."; } };
  $("#cBuild").onclick = async () => { try { await api(`/courses/${id}/package`, { method: "POST" }); await show(); } catch (e) { alert(e.message); } };
  show();
}

async function loadPush(j, done) {
  const id = j.id, cfg = await api("/config");
  $("#cPush").innerHTML = `<div class="mut">Создаёт в LMS НОВЫЙ курс: название, разделы, текстовые страницы и задания. Исходный курс не изменяется. Видео, документы, тесты и встроенные материалы создать нельзя: они в пакете для ручной загрузки.</div>
    ${j.pushed_course_id ? `<div class="ok" style="margin:6px 0">Уже создан курс с ID ${j.pushed_course_id}</div>` : ""}
    <div class="row" style="margin:8px 0"><button id="cDry" ${done ? "" : "disabled"}>Показать, что будет создано</button>
    <button class="primary" id="cGo" ${done && cfg.push_enabled && !j.pushed_course_id ? "" : "disabled"}>Создать новый курс в LMS</button>
    ${cfg.push_enabled ? "" : `<span class="mut">запись выключена: запустите сервис с DUB_ALLOW_PUSH=1</span>`}</div><pre id="cPlan" style="display:none"></pre>`;
  const plan = r => { const p = $("#cPlan"); p.style.display = "block"; p.textContent = JSON.stringify(r, null, 1); };
  $("#cDry").onclick = () => api(`/courses/${id}/push`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: false }) }).then(plan).catch(e => alert(e.message));
  $("#cGo").onclick = () => { if (confirm("Создать НОВЫЙ курс в LMS с переведёнными текстами? Исходный курс не изменится.")) api(`/courses/${id}/push`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }) }).then(r => { plan(r); refresh(); }).catch(e => alert(e.message)); };
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.createElement("button");
  btn.textContent = "Перевести курс целиком";
  btn.onclick = async () => {
    const id = $("#course").value.trim(); if (!id) return alert("Введите ID курса");
    if (!confirm("Курс будет прочитан из Teachbase (только чтение), тексты и документы переведены, видео поставлены в очередь на озвучку. Продолжить?")) return;
    try { const j = await api("/courses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ course_id: +id, review: $("#review").checked, review_videos: true, translate_documents: true, dub_videos: true }) }); current = j.id; refresh(); } catch (e) { alert(e.message); }
  };
  $("#loadVideos").after(btn);
});

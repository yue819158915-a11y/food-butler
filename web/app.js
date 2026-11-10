/* 美食小助手前端逻辑 */
const $ = (id) => document.getElementById(id);
const chatFlow = $("chatFlow"), chatInput = $("chatInput"), btnSend = $("btnSend");
const resultList = $("resultList"), resultCount = $("resultCount");
let history = [];
let busy = false;
let lastConditions = { category: null, price_min: null, price_max: null, location: null, keywords: [], sort: "popular" };
let resultSeq = 0;

/* 访问凭证：网址上的 sharecode 一并带到每个 API 请求，
   避免 cookie 过期/重新发布后接口返回 403 */
const SHARECODE = new URLSearchParams(location.search).get("sharecode");
function apiUrl(path) {
  if (!SHARECODE) return path;
  return path + (path.includes("?") ? "&" : "?") + "sharecode=" + encodeURIComponent(SHARECODE);
}

function headers() {
  const h = { "Content-Type": "application/json" };
  const key = localStorage.getItem("llm_key");
  if (key) h["X-LLM-Key"] = key;
  return h;
}

/* ---------- 初始化 ---------- */
async function init() {
  try {
    const meta = await (await fetch(apiUrl("/api/meta"))).json();
    $("totalHint").textContent = meta.total;
    const badge = $("llmBadge");
    if (meta.has_server_key || localStorage.getItem("llm_key")) {
      badge.textContent = `AI 已接入 · ${meta.llm_model}`;
      badge.className = "badge on";
      $("serverKeyStatus").textContent = "服务端已配置 LLM Key，直接提问即可。";
    } else {
      badge.textContent = "本地词库模式";
      badge.className = "badge off";
      $("serverKeyStatus").textContent = "服务端未配置 Key，当前为本地词库模式。";
    }
  } catch (e) { console.error(e); }
  refreshResults({ sort: "popular" }, "🔥 人气榜");
  loadWeatherHint();   // 不阻塞开屏；失败静默隐藏
}

/* ---------- 聊天 ---------- */
function addMsg(role, text, extraHTML = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = role === "user"
    ? `<div class="bubble"></div>`
    : `<div class="avatar">🍜</div><div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  if (extraHTML) div.querySelector(".bubble").insertAdjacentHTML("beforeend", extraHTML);
  chatFlow.appendChild(div);
  chatFlow.scrollTop = chatFlow.scrollHeight;
  return div;
}

/* 带重试的聊天请求：网关超时/返回非 JSON 时自动再试一次 */
async function requestChat(message) {
  let lastErr;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(apiUrl("/api/chat"), {
        method: "POST", headers: headers(),
        body: JSON.stringify({ message, history }),
      });
      const raw = await res.text();
      if (res.status === 403 || res.status === 401) {
        const err = new Error("访问凭证失效");
        err.fatal = true;            // 凭证失效重试无意义
        throw err;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return JSON.parse(raw);          // 非 JSON（网关错误页）会在这里抛错
    } catch (e) {
      lastErr = e;
      if (e.fatal) break;
      if (attempt < 2) await new Promise(r => setTimeout(r, 800));
    }
  }
  throw lastErr;
}

async function send(message) {
  if (!message.trim() || busy) return;
  busy = true; btnSend.disabled = true;
  addMsg("user", message);
  chatInput.value = "";
  const typing = addMsg("bot", "想想… 🤔");
  try {
    const data = await requestChat(message);
    typing.querySelector(".bubble").textContent = "";
    const bubble = typing.querySelector(".bubble");
    bubble.textContent = data.reply;
    const src = data.nlu_source === "llm" ? "🤖 AI 语义理解" : "📖 本地词库";
    const modeTag = data.mode === "chat" ? " · 对话模式" : "";
    const relaxTag = data.relaxed ? ` · ${relaxBadgeText(data.relaxed)}` : "";
    bubble.insertAdjacentHTML("beforeend",
      `<span class="source-tag">${src}${modeTag}${relaxTag}</span>`);
    history.push({ role: "user", text: message });
    history.push({ role: "assistant", text: data.reply, conditions_json: JSON.stringify(data.conditions) });
    if (history.length > 10) history = history.slice(-10);
    if (data.mode === "cook") {
      renderRecipeCards(data.recipes, "🍳 菜谱推荐");
      if (data.assumptions && data.assumptions.length) {
        bubble.insertAdjacentHTML("beforeend",
          `<span class="source-tag">假设：${esc(data.assumptions.join("；"))}</span>`);
      }
    } else if (data.conditions) applyConditions(data.conditions);
    else if (data.restaurants && data.restaurants.length) renderResults(data.restaurants, data.relaxed);
  } catch (e) {
    const bubble = typing.querySelector(".bubble");
    if (e.fatal) {
      bubble.textContent = "访问链接的凭证已失效。请用带 ?sharecode= 的完整链接重新打开页面" +
        "（直接从群里那条完整链接点进来即可），然后刷新一次。";
    } else {
      bubble.textContent = "这次响应太慢了（网络或服务繁忙），右边先保留上一次的结果。" +
        "把问题再发一次通常就好了 👇";
      refreshResults(lastConditions);   // 兜底：至少让右栏有结果
    }
    bubble.insertAdjacentHTML("beforeend", `<span class="source-tag">请求失败 · ${esc(e.message || "超时")}</span>`);
  } finally {
    busy = false; btnSend.disabled = false;
    chatInput.focus();
  }
}
btnSend.addEventListener("click", () => send(chatInput.value));
chatInput.addEventListener("keydown", e => { if (e.key === "Enter") send(chatInput.value); });
chatFlow.addEventListener("click", e => {
  if (e.target.classList.contains("chip")) send(e.target.dataset.q);
});

/* ---------- 推荐结果 ---------- */
/* 兼容三种 relaxed：v2 结构化对象 / v1 字符串 / false */
function normalizeRelaxed(r) {
  if (!r) return null;
  if (typeof r === "string") return { dropped: [], detail: r, nearest: [] };
  return { dropped: r.dropped || [], detail: r.detail || "已放宽条件", nearest: r.nearest || [] };
}

/* 只取筛选白名单字段，丢弃 mode 等无关键 */
function applyConditions(c) {
  lastConditions = {
    category: c.category ?? null,
    price_min: c.price_min ?? null,
    price_max: c.price_max ?? null,
    location: c.location ?? null,
    keywords: Array.isArray(c.keywords) ? c.keywords : [],
    sort: c.sort ?? "popular",
  };
  refreshResults(lastConditions);
}

async function refreshResults(c = lastConditions, label = "") {
  const seq = ++resultSeq;
  const params = new URLSearchParams();
  if (c.category) params.set("category", c.category);
  if (c.price_min != null) params.set("price_min", c.price_min);
  if (c.price_max != null) params.set("price_max", c.price_max);
  if (c.location) params.set("location", c.location);
  if (c.keywords && c.keywords.length) params.set("q", c.keywords.join(" "));
  if (c.sort) params.set("sort", c.sort);
  params.set("limit", "60");
  try {
    const data = await (await fetch(apiUrl(`/api/restaurants?${params}`))).json();
    if (seq !== resultSeq) return;  // 连发消息时丢弃过期响应
    renderResults(data.restaurants, data.relaxed, label);
  } catch (e) { console.error(e); }
}

function relaxBadgeText(relaxed) {
  relaxed = normalizeRelaxed(relaxed);
  if (!relaxed) return "";
  const dropped = (relaxed.dropped || []).map(d => ({
    keywords: "忽略关键词", price: "放宽价格", location: "扩大地点"
  }[d] || d));
  if (relaxed.nearest && relaxed.nearest.length) return "无匹配，展示最接近的";
  return dropped.length ? dropped.join(" · ") : "已放宽";
}

function priceLabel(r) {
  const a = r.price_min, b = r.price_max, raw = r.price_raw || "";
  if (a == null) return raw || "未知";
  if (b == null || a === b) return raw || String(a);
  const m = r.price_mid != null ? r.price_mid : (a + b) / 2;
  return `${raw}，约 ${Math.round(m)}`;
}

function renderResults(list, relaxed, label = "") {
  relaxed = normalizeRelaxed(relaxed);
  const prefix = label ? `${label} · ` : "";
  let head;
  if (relaxed && relaxed.nearest && relaxed.nearest.length) {
    head = `${prefix}${relaxed.detail} · 以下是最接近的 ${list.length} 家`;
  } else if (relaxed) {
    head = `${prefix}已放宽：${relaxed.detail} · 显示 ${list.length} 家`;
  } else {
    head = `${prefix}共 ${list.length} 家符合条件的餐厅`;
  }
  resultCount.textContent = head;
  resultCount.className = "result-count" + (relaxed ? " relaxed" : "");
  resultList.innerHTML = "";
  list.forEach(r => resultList.appendChild(card(r)));
}

function card(r) {
  const div = document.createElement("div");
  div.className = "restaurant-card";
  const note = (r.note || "").replace(/(\d{1,2}:\d{2})/g, "<b>$1</b>")
    .replace(/(营业时间|预约|提前打电话)/g, "<b>$1</b>");
  div.innerHTML = `
    <div class="rc-head">
      <span class="rc-name">${esc(r.name)}</span>
      ${r.popularity >= 4 ? '<span class="tag pop">🔥 人气</span>' : ""}
    </div>
    <div class="rc-badges">
      <span class="tag">${esc(r.type_raw || r.category)}</span>
      <span class="tag loc">📍 ${esc(r.location)} · ${esc(r.region)}</span>
      <span class="tag price">💰 人均 ${esc(priceLabel(r))} 元</span>
      ${r.gap ? `<span class="tag gap">${esc(r.gap)}</span>` : ""}
    </div>
    <div class="rc-note">${note}</div>
    <div class="rc-toggle">展开全文 ▾</div>`;
  const noteEl = div.querySelector(".rc-note"), toggle = div.querySelector(".rc-toggle");
  toggle.addEventListener("click", () => {
    noteEl.classList.toggle("open");
    toggle.textContent = noteEl.classList.contains("open") ? "收起 ▴" : "展开全文 ▾";
  });
  return div;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

/* ---------- 设置 ---------- */
$("btnSettings").addEventListener("click", () => $("settingsModal").classList.remove("hidden"));
$("btnCloseModal").addEventListener("click", () => $("settingsModal").classList.add("hidden"));
$("settingsModal").addEventListener("click", e => {
  if (e.target === $("settingsModal")) $("settingsModal").classList.add("hidden");
});
$("btnSaveKey").addEventListener("click", async () => {
  const v = $("keyInput").value.trim();
  if (v) localStorage.setItem("llm_key", v);
  $("settingsModal").classList.add("hidden");
  await init();
});
$("btnClearKey").addEventListener("click", async () => {
  localStorage.removeItem("llm_key");
  $("keyInput").value = "";
  $("settingsModal").classList.add("hidden");
  await init();
});

init();

/* ---------- 菜谱卡片 ---------- */
let wxPayload = null;

function renderRecipeCards(cards, label = "🍳 菜谱推荐") {
  if (!cards || !cards.length) return;
  resultCount.textContent = `${label} · ${cards.length} 道（营养为估算值）`;
  resultCount.className = "result-count";
  resultList.innerHTML = "";
  cards.forEach(c => resultList.appendChild(recipeCard(c)));
  document.querySelector(".result-panel").scrollTop = 0;
}

function recipeCard(c) {
  const r = c.full || c.recipe || {};
  const nut = (r.nutrition && r.nutrition.per_serving) || {};
  const health = r.health || {};
  const grade = health.grade || "B";
  const div = document.createElement("div");
  div.className = "recipe-card";

  const ingRows = (r.ingredients || []).map(i => {
    const amt = (i.amount === null || i.amount === undefined || i.amount === "")
      ? '<span style="color:#b45309">适量</span>'
      : `${esc(i.amount)}${esc(i.unit || "g")}`;
    return `<li>${esc(i.name)} · ${amt}</li>`;
  }).join("");

  const stepRows = (r.steps || []).map(s => {
    const meta = [s.heat ? `<span class="heat">${esc(s.heat)}</span>` : "", s.minutes ? `${s.minutes} 分钟` : ""]
      .filter(Boolean).join(" · ");
    return `<li>${esc(s.action)}${meta ? `（${meta}）` : ""}</li>`;
  }).join("");

  const warn = [];
  if (r.nutrition && r.nutrition.confidence && r.nutrition.confidence !== "high") {
    const miss = (r.nutrition.missing || []).slice(0, 3);
    warn.push(`营养为估算（${r.nutrition.confidence === "medium" ? "中" : "低"}置信度）` +
      (miss.length ? `，未含：${miss.join("、")}` : ""));
  }
  if (r.verified === false) warn.push("该菜谱由门店配方自动整理，用量可自行微调");

  div.innerHTML = `
    <div class="rcp-head">
      <span class="rcp-name">${esc(r.name)}</span>
      <span class="rcp-sub">${esc(r.category || "")} · ${r.time_min || "-"}分钟 · 难度${r.difficulty || "-"} · ${r.servings || 2}人份</span>
    </div>
    <div class="rcp-badges">
      <span class="tag grade-${grade}">营养 ${grade} · ${health.score ?? "-"}</span>
      ${c.reason ? `<span class="tag warm">${esc(c.reason)}</span>` : ""}
      ${c.cost_per_serving ? `<span class="tag price">💰 人均约 ${c.cost_per_serving} 元</span>` : ""}
      ${(r.allergens || []).length ? `<span class="tag">⚠️ ${esc(r.allergens.join("/"))}</span>` : ""}
    </div>
    <div class="nutrition-bar">
      <span><b>${Math.round(nut.kcal || 0)}</b> kcal</span>
      <span>蛋白 <b>${nut.protein_g || 0}</b>g</span>
      <span>脂肪 <b>${nut.fat_g || 0}</b>g</span>
      <span>碳水 <b>${nut.carb_g || 0}</b>g</span>
      <span>钠 <b>${Math.round(nut.sodium_mg || 0)}</b>mg</span>
    </div>
    ${warn.length ? `<div class="rcp-warn">${esc(warn.join("；"))}</div>` : ""}
    <details class="rcp-detail"><summary>食材清单（${(r.ingredients || []).length} 项）</summary>
      <ul class="rcp-ing">${ingRows}</ul>
    </details>
    <details class="rcp-detail"><summary>做法步骤（${(r.steps || []).length} 步）</summary>
      <ol class="rcp-steps">${stepRows}</ol>
    </details>
    ${health.factors && health.factors.length ? `<div class="rcp-sub">健康点评：${esc(health.factors.join("；"))}</div>` : ""}`;
  return div;
}

/* ---------- 顶栏天气推荐 ---------- */
async function loadWeatherHint() {
  const el = $("wxHint");
  el.hidden = false;
  el.classList.add("loading");
  try {
    const res = await fetch(apiUrl("/api/recommend"));
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    wxPayload = data;
    el.querySelector(".wx-text").textContent = (data.env && data.env.headline) || "🌤 今日推荐";
    el.classList.remove("loading");
    el.classList.toggle("fallback", !(data.env && data.env.weather));
  } catch (e) {
    el.hidden = true;   // 静默隐藏，不挡开屏
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const el = $("wxHint");
  if (!el) return;
  el.addEventListener("click", () => {
    if (!wxPayload || !wxPayload.cards) { loadWeatherHint(); return; }
    resultSeq++;                                   // 作废在途的店铺检索
    renderRecipeCards(wxPayload.cards, "🌤 今日推荐");
    addMsg("bot", `根据今天的天气和时段，给你挑了这 ${wxPayload.cards.length} 道 👉 右边可以看食材和步骤`);
  });
});

"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  project: null,
  projectPath: "",
  files: [],
  selected: null,
  version: null,
  dirty: false,
  saving: false,
  saveSequence: 0,
  apiBase: "",
  view: "preview",
  loadSequence: 0,
};

const AXIS_LABELS = {
  build_state: "构建",
  validation_state: "验证",
  creator_acceptance: "创作者确认",
  independent_review: "独立审查",
  delivery_gate: "交付",
};

const STATE_LABELS = {
  materialized: "已生成",
  validated: "已验证",
  accepted: "已确认",
  approved: "已通过",
  approve: "通过",
  approve_with_notes: "附注通过",
  pass: "通过",
  pass_with_warnings: "有提醒",
  ready: "可交付",
  delivered: "已交付",
  complete: "完成",
  in_progress: "进行中",
  not_run: "未运行",
  not_requested: "未发起",
  not_evaluated: "未评估",
  absent: "暂无",
  pending: "待确认",
  provisional: "临时结论",
  blocked: "阻塞",
  rejected: "已拒绝",
  revise: "待修改",
  failed: "失败",
  stale: "已过期",
};

const CHECKPOINT_LABELS = {
  // Compatibility for local showcase workspaces created before the Dashboard
  // was aligned to production stages.
  "demo-ready": "分镜制作",
  development: "项目开发",
  writing: "剧本创作",
  storyboard: "分镜制作",
  review: "项目审查",
  delivery: "交付准备",
};

const PATH_SEGMENT_LABELS = {
  development: "项目开发",
  bible: "设定集",
  episodes: "剧集",
  "creator-decisions": "创作者决策",
  reviews: "审查",
  inputs: "输入",
  delivery: "交付",
  storyboard: "分镜",
  assets: "资产",
  media: "媒体",
};

const FILE_LABELS = {
  "readme.md": "项目说明.md",
  "short-drama.json": "项目清单.json",
  "creative-brief.md": "创作简报.md",
  "story-engine.md": "故事引擎.md",
  "director-brief.md": "导演阐述.md",
  "adaptation-map.jsonl": "改编映射.jsonl",
  "series-arc.json": "系列弧线.json",
  "episode-map.jsonl": "分集地图.jsonl",
  "characters.jsonl": "角色.jsonl",
  "looks.jsonl": "造型.jsonl",
  "locations.jsonl": "场景.jsonl",
  "location-views.jsonl": "场景视图.jsonl",
  "props.jsonl": "道具.jsonl",
  "prop-states.jsonl": "道具状态.jsonl",
  "episode-card.json": "分集卡.json",
  "beats.jsonl": "节拍表.jsonl",
  "screenplay.md": "剧本.md",
  "screenplay-index.jsonl": "剧本索引.jsonl",
  "voice-record-sheet.jsonl": "配音录制表.jsonl",
  "occurrences.jsonl": "资产出现表.jsonl",
  "decisions.jsonl": "资产决策.jsonl",
  "continuity.jsonl": "连续性变化.jsonl",
  "image-prompt-specs.jsonl": "图片提示词规格.jsonl",
  "image-prompts.md": "图片提示词.md",
  "coverage.json": "覆盖检查.json",
  "shots.jsonl": "镜头表.jsonl",
  "keyframes.jsonl": "关键帧表.jsonl",
  "keyframe-prompts.md": "关键帧提示词.md",
  "motion-specs.jsonl": "运动规格.jsonl",
  "delivery-containers.jsonl": "交付容器.jsonl",
  "video-prompts.md": "视频提示词.md",
  "manifest.json": "交付清单.json",
};

const ROOT_ROLES = {
  inputs: "inputs",
  "输入": "inputs",
  development: "development",
  "项目开发": "development",
  bible: "bible",
  "设定集": "bible",
  episodes: "episodes",
  "剧集": "episodes",
  "creator-decisions": "creator-decisions",
  "创作者决策": "creator-decisions",
  reviews: "reviews",
  "审查": "reviews",
  delivery: "delivery",
  "交付": "delivery",
};

const ROOT_DEVELOPMENT_FILES = new Set(["short-drama.json", "readme.md"]);
const EPISODE_WRITING_FILES = new Set([
  "episode-card.json",
  "beats.jsonl",
  "screenplay.md",
  "screenplay-index.jsonl",
  "voice-record-sheet.jsonl",
]);

const SECTION_COUNT_IDS = {
  development: "developmentCount",
  writing: "writingCount",
  assets: "assetsCount",
  storyboard: "storyboardCount",
  review: "reviewCount",
};

const ROOT_ORDER = {
  root: 0,
  inputs: 1,
  development: 2,
  bible: 3,
  episodes: 4,
  "creator-decisions": 5,
  reviews: 6,
  delivery: 7,
};

const EPISODE_AREA_ORDER = {
  writing: 0,
  assets: 1,
  storyboard: 2,
};

function displaySegment(segment, isFile = false) {
  const key = segment.toLowerCase();
  return (isFile ? FILE_LABELS[key] : null) || PATH_SEGMENT_LABELS[key] || segment;
}

function displayPath(path) {
  const parts = path.split("/");
  return parts
    .map((part, index) => displaySegment(part, index === parts.length - 1))
    .join("/");
}

function rootRole(segment) {
  return ROOT_ROLES[segment] || null;
}

function checkpointLabel(checkpoint) {
  return CHECKPOINT_LABELS[checkpoint] || checkpoint || "—";
}

function sectionOf(path) {
  const parts = path.split("/").filter(Boolean);
  if (parts.length < 2) {
    return ROOT_DEVELOPMENT_FILES.has((parts[0] || "").toLowerCase())
      ? "development"
      : "other";
  }
  const role = rootRole(parts[0]);
  if (["inputs", "development"].includes(role)) return "development";
  if (role === "bible") return "assets";
  if (["creator-decisions", "reviews", "delivery"].includes(role)) return "review";
  if (role !== "episodes") return "other";

  const episodeArea = (parts[2] || "").toLowerCase();
  if (["assets", "资产"].includes(episodeArea)) return "assets";
  if (["storyboard", "分镜"].includes(episodeArea)) return "storyboard";
  if (parts.length === 3 && EPISODE_WRITING_FILES.has(episodeArea)) return "writing";
  return "other";
}

function formatBytes(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 ** 2).toFixed(1)} MB`;
}

function pathParts(path) {
  const parts = path.split("/");
  const name = parts.pop();
  return {
    name: displaySegment(name, true),
    parent: parts.map((part) => displaySegment(part)).join("/") || "项目根目录",
    group: displaySegment(parts[0] || "项目根目录"),
  };
}

function groupOf(path) {
  const parts = path.split("/").filter(Boolean);
  if (rootRole(parts[0] || "") === "episodes" && parts[1]) return parts[1];
  return pathParts(path).group;
}

function fileOrder(path) {
  const parts = path.split("/").filter(Boolean);
  const role = parts.length < 2 ? "root" : rootRole(parts[0]) || "other";
  const rootIndex = ROOT_ORDER[role] ?? 99;
  const episodeIndex = role === "episodes" ? EPISODE_AREA_ORDER[sectionOf(path)] ?? 9 : 0;
  return `${String(rootIndex).padStart(2, "0")}:${parts[1] || ""}:${episodeIndex}:${path}`;
}

function toneFor(states) {
  const names = Object.keys(states || {});
  if (names.some((name) => ["blocked", "rejected", "revise", "fail", "failed"].includes(name))) {
    return "danger";
  }
  const successful = new Set([
    "materialized",
    "validated",
    "accepted",
    "approved",
    "approve",
    "approve_with_notes",
    "pass",
    "pass_with_warnings",
    "ready",
    "delivered",
    "complete",
  ]);
  if (!names.length || names.some((name) => !successful.has(name))) {
    return "warning";
  }
  return "success";
}

async function api(path, options) {
  const requestPath =
    state.apiBase && path.startsWith("/api/") ? `${state.apiBase}${path}` : path;
  const response = await fetch(requestPath, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function establishSession() {
  const token = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const storageKey = "shortDramaApiBase";
  if (!token) {
    state.apiBase = sessionStorage.getItem(storageKey) || "";
    return;
  }
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "X-Short-Drama-Token": token },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  if (typeof data.apiBase !== "string" || !data.apiBase.startsWith("/_short_drama/")) {
    throw new Error("本机会话响应无效");
  }
  state.apiBase = data.apiBase;
  sessionStorage.setItem(storageKey, state.apiBase);
  history.replaceState(null, "", `${location.pathname}${location.search}`);
}

function flatten(nodes, out = []) {
  for (const node of nodes) {
    if (node.type === "directory") {
      flatten(node.children || [], out);
    } else {
      out.push(node);
    }
  }
  return out;
}

function warnLeave() {
  return !state.dirty || confirm("当前文件有未保存更改，确认放弃吗？");
}

function setMessage(text, tone = "neutral") {
  $("message").textContent = text;
  $("message").dataset.tone = tone;
}

function setDirty(value) {
  state.dirty = value;
  $("save").disabled = state.saving || !value || !state.selected?.writable;
  $("save").textContent = state.saving ? "保存中…" : value ? "保存更改" : "已保存";
  document.title = `${value ? "● " : ""}短剧项目控制台`;
  updateFileMeta();
}

function updateFileMeta(extra = "") {
  const file = state.selected;
  if (!file) {
    $("fileMeta").textContent = "";
    return;
  }
  const facts = [formatBytes(file.size)];
  if (file.oversize) facts.push("超过预览限制");
  if (file.type !== "media") facts.push(file.writable ? "可编辑" : "只读");
  if (state.dirty) facts.push("未保存");
  if (extra) facts.push(extra);
  $("fileMeta").textContent = facts.filter(Boolean).join(" · ");
}

function cleanupMedia() {
  const video = $("media").querySelector("video");
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  const image = $("media").querySelector("img");
  if (image) image.removeAttribute("src");
  $("media").replaceChildren();
}

function renderSectionCounts() {
  const counts = Object.fromEntries(Object.keys(SECTION_COUNT_IDS).map((key) => [key, 0]));
  for (const file of state.files) {
    const section = sectionOf(file.path);
    if (section in counts) counts[section] += 1;
  }
  $("allCount").textContent = state.files.length;
  for (const [section, id] of Object.entries(SECTION_COUNT_IDS)) {
    $(id).textContent = counts[section];
  }
}

function fileIcon(file) {
  if (file.type === "media") {
    return /\.(mp4|webm|mov)$/i.test(file.path) ? "▶" : "◆";
  }
  if (/\.jsonl?$/i.test(file.path)) return "{}";
  if (/\.md$/i.test(file.path)) return "¶";
  return "≡";
}

function renderFiles() {
  const activeSection = document.querySelector(".domain.active").dataset.section;
  const term = $("search").value.trim().toLowerCase();
  const filtered = state.files
    .filter(
      (file) =>
        (activeSection === "all" || sectionOf(file.path) === activeSection) &&
        `${file.path} ${displayPath(file.path)}`.toLowerCase().includes(term),
    )
    .sort((left, right) => fileOrder(left.path).localeCompare(fileOrder(right.path), "zh-CN"));
  const groups = new Map();
  for (const file of filtered) {
    const group = groupOf(file.path);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(file);
  }
  const content = [];
  for (const [group, files] of groups) {
    const heading = document.createElement("div");
    heading.className = "file-group";
    const groupName = document.createElement("span");
    const groupCount = document.createElement("b");
    groupName.textContent = group;
    groupCount.textContent = files.length;
    heading.append(groupName, groupCount);
    content.push(heading);

    for (const file of files) {
      const parts = pathParts(file.path);
      const button = document.createElement("button");
      button.className = "file";
      button.title = file.path;
      button.dataset.path = file.path;
      if (state.selected?.path === file.path) {
        button.classList.add("selected");
        button.setAttribute("aria-current", "true");
      }
      const icon = document.createElement("span");
      const labels = document.createElement("span");
      const name = document.createElement("strong");
      const detail = document.createElement("small");
      icon.className = "file-icon";
      labels.className = "file-labels";
      icon.textContent = fileIcon(file);
      name.textContent = parts.name;
      detail.textContent = `${parts.parent} · ${file.oversize ? "过大" : formatBytes(file.size)}`;
      labels.append(name, detail);
      button.append(icon, labels);
      button.onclick = () => openFile(file);
      content.push(button);
    }
  }
  if (!content.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = term ? "没有匹配的文件" : "这个创作阶段还没有文件";
    content.push(empty);
  }
  $("resultCount").textContent = filtered.length;
  $("resultLabel").textContent = term
    ? `“${$("search").value.trim()}”`
    : document.querySelector(".domain.active span").textContent;
  $("tree").replaceChildren(...content);
}

function appendInlineText(element, text) {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  for (const token of tokens) {
    if (token.startsWith("`") && token.endsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      element.append(code);
    } else if (token.startsWith("**") && token.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      element.append(strong);
    } else {
      element.append(document.createTextNode(token));
    }
  }
}

function renderMarkdown(content) {
  const fragment = document.createDocumentFragment();
  let list = null;
  let listType = "";
  let codeLines = null;

  function closeList() {
    list = null;
    listType = "";
  }

  for (const line of content.split("\n")) {
    if (line.startsWith("```")) {
      closeList();
      if (codeLines === null) {
        codeLines = [];
      } else {
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\n");
        pre.append(code);
        fragment.append(pre);
        codeLines = null;
      }
      continue;
    }
    if (codeLines !== null) {
      codeLines.push(line);
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      closeList();
      const element = document.createElement(`h${heading[1].length}`);
      appendInlineText(element, heading[2]);
      fragment.append(element);
      continue;
    }
    const unordered = /^[-*]\s+(.+)$/.exec(line);
    const ordered = /^(\d+)\.\s+(.+)$/.exec(line);
    if (unordered || ordered) {
      const wanted = unordered ? "ul" : "ol";
      if (!list || listType !== wanted) {
        list = document.createElement(wanted);
        listType = wanted;
        fragment.append(list);
      }
      const item = document.createElement("li");
      appendInlineText(item, unordered ? unordered[1] : ordered[2]);
      if (ordered) item.value = Number(ordered[1]);
      list.append(item);
      continue;
    }
    closeList();
    if (!line.trim()) continue;
    const quote = /^>\s?(.*)$/.exec(line);
    const element = document.createElement(quote ? "blockquote" : "p");
    appendInlineText(element, quote ? quote[1] : line);
    fragment.append(element);
  }
  if (codeLines !== null) {
    const pre = document.createElement("pre");
    pre.textContent = codeLines.join("\n");
    fragment.append(pre);
  }
  return fragment;
}

function parseJsonLines(content) {
  return content
    .split("\n")
    .map((line, index) => ({ line: index + 1, value: line.trim() }))
    .filter((record) => record.value)
    .map((record) => {
      try {
        return JSON.parse(record.value);
      } catch (error) {
        throw new Error(`JSONL 第 ${record.line} 行无效：${error.message}`);
      }
    });
}

function validateStructuredText(path, content) {
  if (/\.json$/i.test(path)) {
    try {
      JSON.parse(content);
    } catch (error) {
      throw new Error(`JSON 无效：${error.message}`);
    }
  } else if (/\.jsonl$/i.test(path)) {
    parseJsonLines(content);
  }
}

function renderPreview() {
  const preview = $("preview");
  const content = $("editor").value;
  const path = state.selected?.path || "";
  preview.replaceChildren();
  try {
    if (/\.md$/i.test(path)) {
      preview.append(renderMarkdown(content));
    } else {
      const pre = document.createElement("pre");
      pre.className = "code-block";
      if (/\.json$/i.test(path)) {
        pre.textContent = JSON.stringify(JSON.parse(content), null, 2);
      } else if (/\.jsonl$/i.test(path)) {
        pre.textContent = parseJsonLines(content)
          .map((record) => JSON.stringify(record, null, 2))
          .join("\n\n");
      } else {
        pre.textContent = content;
      }
      preview.append(pre);
    }
  } catch (error) {
    const warning = document.createElement("div");
    const raw = document.createElement("pre");
    warning.className = "preview-warning";
    warning.textContent = error.message;
    raw.className = "code-block";
    raw.textContent = content;
    preview.append(warning, raw);
  }
}

function setView(view) {
  state.view = view;
  const isMedia = state.selected?.type === "media";
  $("editor").hidden = isMedia || view !== "edit";
  $("preview").hidden = isMedia || view !== "preview";
  $("media").hidden = !isMedia;
  $("editMode").setAttribute("aria-pressed", String(view === "edit"));
  $("previewMode").setAttribute("aria-pressed", String(view === "preview"));
  if (!isMedia && view === "preview") renderPreview();
}

function mediaBadge(path, kind, lifecycle = null) {
  if (/fallback|preview|previs/i.test(path)) return ["本地预演", "warning"];
  if (
    kind === "video" &&
    lifecycle?.creator_acceptance === "accepted" &&
    ["approve", "approve_with_notes"].includes(lifecycle?.independent_review) &&
    ["ready", "delivered"].includes(lifecycle?.delivery_gate)
  ) {
    return ["正式成片", "success"];
  }
  if (
    lifecycle?.creator_acceptance === "rejected" ||
    lifecycle?.independent_review === "revise"
  ) {
    return ["已退回", "danger"];
  }
  if (/demo|演示/i.test(path) && kind === "video") return ["音画演示", "info"];
  if (kind === "video") return ["视频素材 · 待审", "warning"];
  return ["视觉资产", "info"];
}

function renderMedia(info) {
  cleanupMedia();
  const shell = document.createElement("div");
  const stage = document.createElement("div");
  const facts = document.createElement("div");
  const badge = document.createElement("span");
  const technical = document.createElement("span");
  const [badgeText, tone] = mediaBadge(state.selected.path, info.kind, info.lifecycle);
  shell.className = "media-shell";
  stage.className = "media-stage";
  facts.className = "media-facts";
  badge.className = "media-badge";
  badge.dataset.tone = tone;
  badge.textContent = badgeText;
  technical.textContent = `${info.kind === "video" ? "视频" : "图片"} · ${formatBytes(info.size)} · 只读`;
  facts.append(badge, technical);

  const element = document.createElement(info.kind === "video" ? "video" : "img");
  element.src = info.contentUrl;
  element.setAttribute("aria-label", state.selected.path);
  if (info.kind === "video") {
    element.controls = true;
    element.preload = "metadata";
    element.playsInline = true;
    element.onloadedmetadata = () => {
      const seconds = Number.isFinite(element.duration) ? `${element.duration.toFixed(2)} 秒` : "";
      technical.textContent = [
        "视频",
        `${element.videoWidth}×${element.videoHeight}`,
        seconds,
        formatBytes(info.size),
        "只读",
      ].filter(Boolean).join(" · ");
      updateFileMeta(seconds);
    };
  } else {
    element.alt = state.selected.path;
    element.onload = () => {
      technical.textContent = `图片 · ${element.naturalWidth}×${element.naturalHeight} · ${formatBytes(info.size)} · 只读`;
    };
  }
  element.onerror = () => setMessage("媒体加载失败或超过预览限制", "danger");
  stage.append(element);
  shell.append(stage, facts);
  $("media").replaceChildren(shell);
  setMessage("媒体预览已载入。", "neutral");
}

async function openFile(file) {
  // Unconditional: re-clicking the file that is already open is the natural
  // reaction to a save conflict, and short-circuiting on the path comparison
  // let that reload discard the unsaved buffer without asking.
  if (!warnLeave()) return;
  const sequence = ++state.loadSequence;
  cleanupMedia();
  state.selected = file;
  state.version = null;
  setDirty(false);
  renderFiles();
  const parts = pathParts(file.path);
  $("filename").textContent = parts.name;
  $("filename").title = file.path;
  $("fileKind").textContent = file.type === "media" ? "媒体预览" : parts.parent;
  $("editMode").disabled = file.type === "media";
  $("previewMode").disabled = file.type === "media";
  setMessage("正在载入…");

  try {
    if (file.type === "media") {
      setView("preview");
      const info = await api(
        `/api/media?project=${encodeURIComponent(state.project)}&path=${encodeURIComponent(file.path)}`,
      );
      if (sequence !== state.loadSequence || state.selected?.path !== file.path) return;
      renderMedia(info);
      return;
    }

    const data = await api(
      `/api/file?project=${encodeURIComponent(state.project)}&path=${encodeURIComponent(file.path)}`,
    );
    if (sequence !== state.loadSequence || state.selected?.path !== file.path) return;
    state.version = data.version;
    $("editor").value = data.content;
    $("editor").disabled = !data.writable;
    $("editMode").disabled = !data.writable;
    setView("preview");
    setMessage(data.writable ? "已载入，可切换到编辑模式。" : "受保护文件：只读", data.writable ? "neutral" : "warning");
  } catch (error) {
    if (sequence === state.loadSequence) setMessage(error.message, "danger");
  }
}

function renderLifecycle(lifecycle) {
  const rows = Object.entries(lifecycle || {}).map(([axis, values]) => {
    const row = document.createElement("div");
    const title = document.createElement("div");
    const label = document.createElement("b");
    const dot = document.createElement("span");
    const summary = document.createElement("span");
    row.className = "lifecycle-row";
    row.dataset.tone = toneFor(values);
    title.className = "lifecycle-title";
    dot.className = "status-dot";
    label.textContent = AXIS_LABELS[axis] || axis;
    summary.textContent =
      Object.entries(values)
        .map(([name, count]) => `${STATE_LABELS[name] || name} ${count}`)
        .join(" · ") || "—";
    title.append(dot, label);
    row.append(title, summary);
    return row;
  });
  $("lifecycle").replaceChildren(...rows);
}

function summaryCard(label, value, detail, tone) {
  const card = document.createElement("div");
  const top = document.createElement("span");
  const main = document.createElement("strong");
  const note = document.createElement("small");
  card.className = "summary-card";
  card.dataset.tone = tone;
  top.textContent = label;
  main.textContent = value;
  note.textContent = detail;
  card.append(top, main, note);
  return card;
}

function deliverySummary(lifecycle, recovery, layout = null) {
  if (layout?.mode === "mixed") {
    return {
      value: "中英文目录重复",
      detail: "合并平行目录后继续交付",
      tone: "danger",
    };
  }
  const gate = lifecycle?.delivery_gate || {};
  const blocked = gate.blocked || 0;
  const ready = (gate.ready || 0) + (gate.delivered || 0);
  const waiting = Object.entries(gate)
    .filter(([name]) => !["ready", "delivered", "blocked"].includes(name))
    .reduce((total, [, count]) => total + count, 0);
  const pending = lifecycle?.creator_acceptance?.pending || 0;
  if (blocked) {
    return {
      value: `${blocked} 项阻塞`,
      detail: pending ? `${pending} 项等待创作者确认` : "查看生命周期中的阻塞项",
      tone: "danger",
    };
  }
  if (ready > 0 && waiting === 0 && !recovery?.needed) {
    return {
      value: "可以进入交付",
      detail: `${ready} 项通过交付检查`,
      tone: "success",
    };
  }
  if (waiting > 0 || pending > 0) {
    return {
      value: "交付检查待完成",
      detail: pending ? `${pending} 项等待创作者确认` : `${waiting} 项等待检查`,
      tone: "warning",
    };
  }
  return {
    value: "尚无可交付产物",
    detail: "从项目文本开始推进",
    tone: "warning",
  };
}

function savedContentIsCurrent(submitted, current) {
  return submitted === current;
}

function statusRefreshFailureMessage() {
  return "文件已保存，项目状态刷新失败，请刷新重试";
}

function renderStatusUnavailable() {
  $("summary").replaceChildren(
    summaryCard("项目状态", "需要刷新", "重新读取最新项目状态", "warning"),
  );
  $("lifecycle").replaceChildren();
}

function renderStatus(status) {
  $("projectTitle").textContent = status.title || "未命名项目";
  $("projectPath").textContent = state.projectPath;
  $("axisCount").textContent = `${Object.keys(status.lifecycle || {}).length} 个状态轴`;
  const recovery = status.recovery || {};
  const delivery = deliverySummary(status.lifecycle, recovery, status.layout);
  $("summary").replaceChildren(
    summaryCard(
      "当前检查点",
      checkpointLabel(status.current_checkpoint),
      "工作流当前位置",
      "info",
    ),
    summaryCard(
      "项目恢复",
      recovery.needed ? "需要处理" : "状态正常",
      `${recovery.transaction_counts?.complete || 0} 个事务完成`,
      recovery.needed ? "danger" : "success",
    ),
    summaryCard(
      "交付状态",
      delivery.value,
      delivery.detail,
      delivery.tone,
    ),
  );
  renderLifecycle(status.lifecycle);
}

async function selectProject(id, preferredPath = "") {
  if (!warnLeave()) {
    $("projects").value = state.project;
    return;
  }
  const sequence = ++state.loadSequence;
  cleanupMedia();
  state.project = id;
  state.projectPath = $("projects").selectedOptions[0]?.dataset.path || "";
  state.selected = null;
  setDirty(false);
  setMessage("正在读取项目…");
  try {
    const [tree, status] = await Promise.all([
      api(`/api/tree?project=${encodeURIComponent(id)}`),
      api(`/api/status?project=${encodeURIComponent(id)}`),
    ]);
    if (sequence !== state.loadSequence) return;
    state.files = flatten(tree.tree);
    $("warnings").textContent = tree.warnings.join("\n");
    renderSectionCounts();
    renderFiles();
    renderStatus(status);
    const selectedOption = $("projects").selectedOptions[0];
    if (selectedOption) selectedOption.textContent = status.title || state.projectPath;
    const initial =
      state.files.find((file) => file.path === preferredPath) ||
      state.files.find((file) => file.path.toLowerCase() === "readme.md") ||
      state.files.find((file) => sectionOf(file.path) === "development") ||
      state.files[0];
    if (initial) {
      await openFile(initial);
    } else {
      setMessage("项目中没有可预览文件。", "warning");
    }
  } catch (error) {
    if (sequence === state.loadSequence) setMessage(error.message, "danger");
  }
}

async function save() {
  if (!state.dirty || !state.selected || state.saving) return;
  const snapshot = {
    sequence: ++state.saveSequence,
    project: state.project,
    path: state.selected.path,
    version: state.version,
    content: $("editor").value,
  };
  state.saving = true;
  setDirty(true);
  try {
    validateStructuredText(snapshot.path, snapshot.content);
    const result = await api(
      `/api/file?project=${encodeURIComponent(snapshot.project)}&path=${encodeURIComponent(snapshot.path)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: snapshot.content, expectedVersion: snapshot.version }),
      },
    );
    if (
      snapshot.sequence !== state.saveSequence ||
      state.project !== snapshot.project ||
      state.selected?.path !== snapshot.path
    ) {
      return;
    }
    state.version = result.version;
    state.selected.size = new TextEncoder().encode(snapshot.content).length;
    setDirty(!savedContentIsCurrent(snapshot.content, $("editor").value));
    renderFiles();
    setMessage(`已保存 · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`, "success");
    try {
      const refreshed = await api(
        `/api/status?project=${encodeURIComponent(snapshot.project)}`,
      );
      if (state.project === snapshot.project) renderStatus(refreshed);
    } catch (_error) {
      renderStatusUnavailable();
      setMessage(statusRefreshFailureMessage(), "warning");
    }
  } catch (error) {
    if (
      snapshot.sequence === state.saveSequence &&
      state.project === snapshot.project &&
      state.selected?.path === snapshot.path
    ) {
      setMessage(error.message, "danger");
    }
  } finally {
    if (snapshot.sequence === state.saveSequence) {
      state.saving = false;
      setDirty(state.dirty);
    }
  }
}

async function boot() {
  try {
    await establishSession();
    const data = await api("/api/projects");
    $("warnings").textContent = data.warnings.join("\n");
    const options = data.projects.map((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      option.dataset.path = project.path;
      option.textContent = project.path;
      return option;
    });
    $("projects").replaceChildren(...options);
    if (data.projects.length) {
      await selectProject(data.projects[0].id);
    } else {
      $("projectTitle").textContent = "没有发现项目";
      setMessage("工作区内没有 short-drama.json", "warning");
    }
  } catch (error) {
    setMessage(error.message, "danger");
  }
}

function start() {
  $("projects").onchange = (event) => selectProject(event.target.value);
  $("search").oninput = renderFiles;
  $("editor").oninput = () => setDirty(true);
  $("save").onclick = save;
  $("refresh").onclick = () => selectProject(state.project, state.selected?.path || "");

  document.querySelectorAll(".domain").forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(".domain").forEach((item) => {
        item.classList.toggle("active", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      });
      renderFiles();
    };
  });

  $("editMode").onclick = () => setView("edit");
  $("previewMode").onclick = () => setView("preview");

  addEventListener("beforeunload", (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  addEventListener("pagehide", cleanupMedia);
  addEventListener("keydown", (event) => {
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === "s") {
      event.preventDefault();
      save();
    } else if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("search").focus();
    } else if (event.key === "Escape" && document.activeElement === $("search")) {
      $("search").value = "";
      renderFiles();
      $("search").blur();
    }
  });

  boot();
}

if (typeof document !== "undefined") start();
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    checkpointLabel,
    deliverySummary,
    mediaBadge,
    savedContentIsCurrent,
    sectionOf,
    statusRefreshFailureMessage,
    toneFor,
  };
}

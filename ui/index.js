/**
 * QwenPaw 文件浏览器 v0.1.4 — 前端 GUI
 * 分层级浏览/查看/下载 QwenPaw 工作区以及容器内所有可访问路径；
 * 支持上传（按钮/拖拽/整文件夹）、新建/重命名/删除文件夹、多选批量删除、批量打包下载。
 * 与 web-terminal 插件同一套开发范式：React.createElement + 样式对象 + GitHub Dark。
 */
(function () {
  "use strict";

  if (!window.QwenPaw || !window.QwenPaw.host) {
    console.error("[qwenpaw-file-browser] QwenPaw not ready");
    return;
  }

  var QP = window.QwenPaw;
  var React = QP.host.React;
  var h = React.createElement;

  var PLUGIN_ID = "qwenpaw-file-browser";
  var PLUGIN_NAME = "文件浏览器";
  var VERSION = "0.1.4";
  var API_BASE = "/api/qwenpaw-file-browser";

  // localStorage 键：记住上次打开的目录，刷新页面后恢复当前位置
  var LS_CUR = "qwenpaw-file-browser:curPath";

  // fetch 封装：QwenPaw 不保证提供 QP.fetchJson，统一用原生 fetch
  function fetchJson(url, opts) {
    var o = opts || {};
    return fetch(url, {
      method: o.method || "GET",
      headers: o.body ? { "Content-Type": "application/json" } : undefined,
      body: o.body ? JSON.stringify(o.body) : undefined,
    }).then(function (r) { return r.json(); });
  }

  // ---------- 通用工具 ----------
  function fmtSize(n) {
    if (typeof n !== "number" || n < 0) return "-";
    if (n < 1024) return n + " B";
    var units = ["KB", "MB", "GB"];
    var v = n;
    for (var i = 0; i < units.length; i++) {
      v /= 1024.0;
      if (v < 1024) return v.toFixed(1) + " " + units[i];
    }
    return v.toFixed(1) + " TB";
  }

  function fmtTime(ts) {
    if (!ts) return "-";
    var d = new Date(ts * 1000);
    var pad = function (x) { return (x < 10 ? "0" : "") + x; };
    return (
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
    );
  }

  function basename(p) {
    if (!p) return "";
    var parts = String(p).replace(/\/+$/, "").split("/");
    return parts[parts.length - 1] || p;
  }

  // ---------- 预览渲染：Markdown / JSON / 配置文件 语法高亮 ----------
  // QwenPaw 内置工作区-文件仅对 .md 做 markdown 渲染，且渲染库（ReactMarkdown/
  // mermaid/DOMPurify）是前端 ESM chunk 内部依赖，未暴露到 window.QwenPaw.host，
  // 插件无法直接 import 复用。因此这里「能复用就复用、不能复用就内置兜底」：
  // 运行时优先用宿主全局的 marked / hljs / Prism（若存在），否则用内置轻量渲染器，
  // 零外部依赖、离线可用。

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 简单 HTML 清洗：仅用于外部 marked/hljs 输出（内置渲染器全程转义，无需清洗）
  function sanitizeHtml(html) {
    return String(html)
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
      .replace(/(href|src)\s*=\s*("|')javascript:[^"']*\2/gi, "");
  }

  // 内置语法高亮（GitHub Dark token 配色，class 前缀 qfb-tok-）
  function hlInline(line) {
    var s = escapeHtml(line);
    var keys = [];
    // 1) 键："xxx": 或 'xxx':（JSON/类 JSON）
    s = s.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)(\s*:)(?=\s|$|[,}\]])/g, function (_, k, c) {
      keys.push('<span class="qfb-tok-k">' + k + "</span>" + c);
      return "\u0001" + (keys.length - 1) + "\u0001";
    });
    // 2) 字符串（含占位符内的键）
    s = s.replace(/(&quot;(?:[^&]|&[^;]+;)*?&quot;|&#39;(?:[^&]|&[^;]+;)*?&#39;)/g, '<span class="qfb-tok-s">$1</span>');
    // 3) 数字（排除占位符 \u0001 内的数字，避免污染键占位符）
    s = s.replace(/(?<![\w.$\u0001])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w.$\u0001])/g, '<span class="qfb-tok-n">$1</span>');
    // 4) 布尔 / null
    s = s.replace(/\b(true|false|null|undefined|None|True|False)\b/g, '<span class="qfb-tok-b">$1</span>');
    // 5) 行尾注释（# ; //）
    s = s.replace(/(\s+)(#|;|\/\/)(.*)$/, '$1<span class="qfb-tok-c">$2$3</span>');
    // 还原键占位符
    s = s.replace(/\u0001(\d+)\u0001/g, function (_, d) { return keys[+d]; });
    return s;
  }

  function hlJsonLine(line) {
    return hlInline(line);
  }

  function highlight(code, lang) {
    var src = String(code);
    var isYaml = lang === "yaml" || lang === "yml";
    var isIni = lang === "ini" || lang === "conf" || lang === "cfg" || lang === "env" ||
      lang === "properties" || lang === "toml";
    var isJson = lang === "json" || lang === "jsonc";
    var isXml = lang === "xml";
    var lines = src.split("\n");
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var h;
      if (isXml) {
        h = escapeHtml(line)
          .replace(/(&lt;!--[\s\S]*?--&gt;)/g, '<span class="qfb-tok-c">$1</span>')
          .replace(/(&lt;\/?)([A-Za-z_][\w.-]*)/g, '$1<span class="qfb-tok-t">$2</span>')
          .replace(/([A-Za-z_][\w.-]*)(=)(&quot;[^&]*?&quot;)/g, '<span class="qfb-tok-k">$1</span>$2<span class="qfb-tok-s">$3</span>');
        out.push(h);
        continue;
      }
      // 整行注释
      if (/^\s*(#|;|\/\/)/.test(line)) {
        out.push('<span class="qfb-tok-c">' + escapeHtml(line) + "</span>");
        continue;
      }
      if (isYaml) {
        var ym = line.match(/^(\s*)([-*]\s+)?([^#:]+?)(:)(\s*)(.*)$/);
        if (ym && ym[3].trim() && !/^["']/.test(ym[3].trim()) && ym[3].indexOf(":") < 0) {
          var pre = ym[1] + (ym[2] || "");
          var rest = ym[5] + hlInline(ym[6]);
          h = escapeHtml(pre) + '<span class="qfb-tok-k">' + escapeHtml(ym[3].trim()) + "</span><span class='qfb-tok-p'>:</span>" + rest;
          out.push(h);
          continue;
        }
      }
      if (isIni) {
        var sm = line.match(/^\s*\[([^\]]+)\]\s*(#.*)?$/);
        if (sm) {
          out.push('<span class="qfb-tok-sec">[' + escapeHtml(sm[1]) + "]</span>" +
            (sm[2] ? '<span class="qfb-tok-c">' + escapeHtml(sm[2]) + "</span>" : ""));
          continue;
        }
        var kv = line.match(/^\s*([^#;=\s][^#;=]*?)(\s*[=:]\s*)(.*)$/);
        if (kv) {
          var k2 = kv[1].trim();
          if (k2) {
            h = '<span class="qfb-tok-k">' + escapeHtml(k2) + "</span>" + escapeHtml(kv[2]) + hlInline(kv[3]);
            out.push(h);
            continue;
          }
        }
      }
      if (isJson) {
        out.push(hlJsonLine(line));
        continue;
      }
      out.push(hlInline(line));
    }
    return out.join("\n");
  }

  // 高亮入口：优先复用宿主全局 hljs / Prism，否则内置
  function highlightCode(code, lang) {
    if (window.hljs && window.hljs.highlight) {
      try {
        var l = lang && window.hljs.getLanguage(lang) ? lang : "plaintext";
        return window.hljs.highlight(code, { language: l, ignoreIllegals: true }).value;
      } catch (e) { /* fallthrough */ }
    }
    if (window.Prism && window.Prism.highlight) {
      try {
        var g = lang && window.Prism.languages[lang] ? lang : "plain";
        return window.Prism.highlight(code, window.Prism.languages[g], g);
      } catch (e) { /* fallthrough */ }
    }
    return highlight(code, lang);
  }

  // 行内 Markdown：代码 / 图片 / 链接 / 粗体 / 删除线 / 斜体
  function inlineMd(src) {
    var s = escapeHtml(String(src));
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) {
      codes.push('<code class="qfb-md-ic">' + c + "</code>");
      return "\u0000" + (codes.length - 1) + "\u0000";
    });
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      function (_, alt, url) {
        return '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(alt) + '">';
      });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      function (_, t, url) {
        return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + inlineMd(t) + "</a>";
      });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    s = s.replace(/(^|[^_])_([^_\n]+)_([^_]|$)/g, "$1<em>$2</em>$3");
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, d) { return codes[+d]; });
    return s;
  }

  // 内置 Markdown 渲染（GFM 子集：标题/列表/任务列表/引用/表格/代码块/分割线/行内样式）
  function renderMarkdown(src, hl) {
    if (!src) return "";
    var lines = String(src).replace(/\r\n/g, "\n").split("\n");
    var html = "";
    var i = 0;
    var listStack = [];
    var paragraph = [];

    function flushPara() {
      if (paragraph.length) {
        html += "<p>" + paragraph.map(inlineMd).join("<br>") + "</p>\n";
        paragraph = [];
      }
    }
    function closeLists(depth) {
      while (listStack.length > depth) html += "</" + listStack.pop() + ">\n";
    }
    function openList(type, depth) {
      while (listStack.length < depth) {
        html += "<" + type + ">\n";
        listStack.push(type);
      }
    }

    while (i < lines.length) {
      var line = lines[i];
      var m;
      // 围栏代码块
      if ((m = line.match(/^```([\w+-]*)\s*$/))) {
        flushPara(); closeLists(0);
        var lang = m[1] || "";
        var buf = [];
        i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        var codeHtml = hl ? hl(buf.join("\n"), lang) : escapeHtml(buf.join("\n"));
        html += '<div class="qfb-md-code"><pre><code' + (lang ? ' data-lang="' + escapeHtml(lang) + '"' : "") + ">" +
          codeHtml + "</code></pre></div>\n";
        continue;
      }
      // 缩进代码块（4 空格 / 制表符）
      if (/^( {4}|\t)/.test(line)) {
        flushPara(); closeLists(0);
        var buf2 = [];
        while (i < lines.length && /^( {4}|\t)/.test(lines[i])) {
          buf2.push(lines[i].replace(/^( {4}|\t)/, ""));
          i++;
        }
        html += '<div class="qfb-md-code"><pre><code>' + escapeHtml(buf2.join("\n")) + "</code></pre></div>\n";
        continue;
      }
      // 标题
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        flushPara(); closeLists(0);
        var lvl = m[1].length;
        html += "<h" + lvl + ">" + inlineMd(m[2]) + "</h" + lvl + ">\n";
        i++;
        continue;
      }
      // 分割线
      if (/^(\s*([-*_])\s*){3,}$/.test(line)) {
        flushPara(); closeLists(0);
        html += "<hr>\n";
        i++;
        continue;
      }
      // 引用
      if (/^\s*>\s?/.test(line)) {
        flushPara(); closeLists(0);
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        html += "<blockquote><p>" + quote.map(inlineMd).join("<br>") + "</p></blockquote>\n";
        continue;
      }
      // 表格：当前行 |...| 且下一行为 |---| 分隔行
      if (/^\s*\|[^\n]*\|$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|$/.test(lines[i + 1])) {
        flushPara(); closeLists(0);
        var header = line.split("|").slice(1, -1).map(function (x) { return x.trim(); });
        var aligns = lines[i + 1].split("|").slice(1, -1).map(function (x) {
          var t = x.trim();
          if (t.indexOf(":") === 0 && t.lastIndexOf(":") === t.length - 1) return "center";
          if (t.indexOf(":") === 0) return "left";
          if (t.lastIndexOf(":") === t.length - 1) return "right";
          return "";
        });
        i += 2;
        var rows = [];
        while (i < lines.length && /^\s*\|[^\n]*\|$/.test(lines[i])) {
          rows.push(lines[i].split("|").slice(1, -1).map(function (x) { return x.trim(); }));
          i++;
        }
        var tbl = "<table><thead><tr>";
        header.forEach(function (h2, idx) {
          tbl += "<th" + (aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "") + ">" + inlineMd(h2) + "</th>";
        });
        tbl += "</tr></thead><tbody>\n";
        rows.forEach(function (r) {
          tbl += "<tr>";
          header.forEach(function (_, idx) {
            tbl += "<td" + (aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "") + ">" + inlineMd(r[idx] || "") + "</td>";
          });
          tbl += "</tr>\n";
        });
        tbl += "</tbody></table>\n";
        html += tbl;
        continue;
      }
      // 列表 / 任务列表
      if ((m = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/))) {
        flushPara();
        var indent = m[1].length;
        var depth = Math.min(Math.floor(indent / 2) + 1, 8);
        var isOl = /^\d+\.$/.test(m[2]);
        var content = m[3];
        var tm = content.match(/^\[( |x|X)\]\s+(.*)$/);
        if (tm) {
          var checked = tm[1] !== " ";
          closeLists(depth);
          openList("ul", depth);
          html += '<li class="qfb-md-task' + (checked ? " done" : "") + '">' +
            '<input type="checkbox" disabled' + (checked ? " checked" : "") + "> " +
            inlineMd(tm[2]) + "</li>\n";
        } else {
          if (listStack.length < depth) {
            openList(isOl ? "ol" : "ul", depth);
          } else if (listStack.length > depth) {
            closeLists(depth);
            openList(isOl ? "ol" : "ul", depth);
          } else if (listStack[depth - 1] !== (isOl ? "ol" : "ul")) {
            closeLists(depth - 1);
            openList(isOl ? "ol" : "ul", depth);
          }
          html += "<li>" + inlineMd(content) + "</li>\n";
        }
        i++;
        continue;
      }
      // 空行：结束段落 / 列表
      if (/^\s*$/.test(line)) {
        flushPara(); closeLists(0);
        i++;
        continue;
      }
      paragraph.push(line);
      i++;
    }
    flushPara(); closeLists(0);
    return html;
  }

  // Markdown 渲染入口：优先复用宿主全局 marked，否则内置渲染器
  function renderMarkdownSafe(src, hl) {
    if (window.marked && window.marked.parse) {
      try {
        return sanitizeHtml(window.marked.parse(String(src), { breaks: true, gfm: true }));
      } catch (e) { /* fallthrough */ }
    }
    return renderMarkdown(src, hl);
  }

  // 根据文件扩展名决定预览渲染方式
  function buildPreview(data) {
    var name = data.name || "";
    var lower = name.toLowerCase();
    var idx = lower.lastIndexOf(".");
    var ext = idx >= 0 ? lower.slice(idx) : "";
    var content = data.content || "";
    var codeBlock = function (lang, badge) {
      return {
        kind: "html",
        badge: badge,
        html: '<pre class="qfb-hl"><code>' + highlightCode(content, lang) + "</code></pre>",
      };
    };
    if (ext === ".md" || ext === ".markdown" || ext === ".mdown") {
      return { kind: "html", badge: "Markdown", html: renderMarkdownSafe(content, highlightCode) };
    }
    if (ext === ".json" || ext === ".jsonc") {
      var pretty = content;
      try { pretty = JSON.stringify(JSON.parse(content), null, 2); } catch (e) { /* 保留原文 */ }
      return { kind: "html", badge: "JSON", html: '<pre class="qfb-hl"><code>' + highlightCode(pretty, "json") + "</code></pre>" };
    }
    if (ext === ".yaml" || ext === ".yml") return codeBlock("yaml", "YAML");
    if (ext === ".toml") return codeBlock("toml", "TOML");
    if (ext === ".ini" || ext === ".conf" || ext === ".cfg") return codeBlock("ini", "配置");
    if (ext === ".env" || ext === ".properties") return codeBlock("env", "配置");
    if (ext === ".xml") return codeBlock("xml", "XML");
    if (ext === ".py" || ext === ".js" || ext === ".mjs" || ext === ".ts" || ext === ".jsx" || ext === ".tsx" ||
      ext === ".sh" || ext === ".bash" || ext === ".css" || ext === ".html" || ext === ".sql" ||
      ext === ".java" || ext === ".go" || ext === ".rs" || ext === ".c" || ext === ".cpp" || ext === ".h" ||
      ext === ".vue" || ext === ".lua" || ext === ".rb" || ext === ".php") {
      return codeBlock(ext.slice(1), ext.slice(1).toUpperCase());
    }
    return { kind: "text", badge: "文本", html: "" };
  }

  // 预览渲染样式（首次使用注入，GitHub Dark 风格）
  function ensurePreviewStyle() {
    if (document.getElementById("qfb-preview-style")) return;
    var st = document.createElement("style");
    st.id = "qfb-preview-style";
    st.textContent =
      ".qfb-md{font-size:13.5px;line-height:1.65;color:#c9d1d9;word-break:break-word;}" +
      ".qfb-md h1,.qfb-md h2,.qfb-md h3,.qfb-md h4,.qfb-md h5,.qfb-md h6{margin:18px 0 10px;line-height:1.35;color:#e6edf3;font-weight:600;}" +
      ".qfb-md h1{font-size:22px;border-bottom:1px solid #21262d;padding-bottom:6px;}" +
      ".qfb-md h2{font-size:18px;border-bottom:1px solid #21262d;padding-bottom:5px;}" +
      ".qfb-md h3{font-size:15.5px;}.qfb-md h4{font-size:14px;}.qfb-md h5,.qfb-md h6{font-size:13px;}" +
      ".qfb-md p{margin:8px 0;}" +
      ".qfb-md a{color:#58a6ff;text-decoration:none;}.qfb-md a:hover{text-decoration:underline;}" +
      ".qfb-md img{max-width:100%;border-radius:4px;}" +
      ".qfb-md ul,.qfb-md ol{margin:8px 0;padding-left:24px;}" +
      ".qfb-md li{margin:3px 0;}" +
      ".qfb-md li.done{color:#8b949e;text-decoration:line-through;}" +
      ".qfb-md li.qfb-md-task{list-style:none;margin-left:-18px;}" +
      ".qfb-md input[type=checkbox]{margin-right:6px;vertical-align:middle;}" +
      ".qfb-md blockquote{margin:8px 0;padding:2px 14px;border-left:4px solid #30363d;color:#8b949e;background:#161b22;border-radius:0 6px 6px 0;}" +
      ".qfb-md code.qfb-md-ic{background:#21262d;color:#ffa657;padding:1px 5px;border-radius:4px;font-size:12px;font-family:SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;}" +
      ".qfb-md hr{border:none;border-top:1px solid #21262d;margin:16px 0;}" +
      ".qfb-md table{border-collapse:collapse;margin:10px 0;display:block;overflow:auto;max-width:100%;}" +
      ".qfb-md th,.qfb-md td{border:1px solid #30363d;padding:6px 12px;font-size:12.5px;}" +
      ".qfb-md th{background:#161b22;color:#e6edf3;font-weight:600;}" +
      ".qfb-md .qfb-md-code{margin:10px 0;background:#161b22;border:1px solid #30363d;border-radius:6px;overflow:auto;}" +
      ".qfb-md .qfb-md-code pre,.qfb-hl pre{margin:0;padding:12px;background:transparent;}" +
      ".qfb-md .qfb-md-code code,.qfb-hl code{font-family:SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-size:12.5px;line-height:1.55;color:#e6edf3;white-space:pre;}" +
      ".qfb-hl{flex:1;min-height:0;margin:0;padding:0;overflow:auto;background:#010409;}" +
      ".qfb-tok-c{color:#8b949e;font-style:italic;}" +
      ".qfb-tok-s{color:#a5d6ff;}" +
      ".qfb-tok-n{color:#f0883e;}" +
      ".qfb-tok-b{color:#79c0ff;}" +
      ".qfb-tok-k{color:#79c0ff;}" +
      ".qfb-tok-p{color:#e6edf3;}" +
      ".qfb-tok-sec{color:#d2a8ff;font-weight:600;}" +
      ".qfb-tok-t{color:#ff7b72;}" +
      ".qfb-md del{color:#8b949e;}";
    document.head.appendChild(st);
  }

  // ---------- 样式 ----------
  var S = {
    page: {
      display: "flex", flexDirection: "column", height: "100%", minHeight: 0,
      background: "#0d1117", color: "#c9d1d9", fontSize: 14,
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    },
    header: {
      display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
      background: "#161b22", borderBottom: "1px solid #30363d", flexShrink: 0, flexWrap: "wrap",
    },
    brand: { color: "#e6edf3", fontWeight: 600, whiteSpace: "nowrap" },
    badge: { background: "#1f6feb", color: "#fff", borderRadius: 10, padding: "1px 8px", fontSize: 11, whiteSpace: "nowrap" },
    addr: {
      flex: 1, minWidth: 200, background: "#010409", color: "#e6edf3",
      border: "1px solid #30363d", borderRadius: 6, padding: "5px 10px", fontSize: 13, outline: "none",
    },
    btn: {
      background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d",
      borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 13, whiteSpace: "nowrap",
    },
    btnPrimary: {
      background: "#238636", color: "#fff", border: "1px solid #2ea043",
      borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 13, whiteSpace: "nowrap",
    },
    btnActive: {
      background: "#1f6feb", color: "#fff", border: "1px solid #58a6ff",
      borderRadius: 6, padding: "4px 10px", cursor: "default", fontSize: 13, whiteSpace: "nowrap",
    },
    btnDanger: {
      background: "#da3633", color: "#fff", border: "1px solid #f85149",
      borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 13, whiteSpace: "nowrap",
    },
    btnDisabled: { opacity: 0.5, cursor: "not-allowed" },
    select: {
      background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d",
      borderRadius: 6, padding: "4px 8px", fontSize: 13,
    },
    crumbRow: {
      display: "flex", alignItems: "center", gap: 2, minWidth: 0,
      overflow: "hidden", whiteSpace: "nowrap", padding: "4px 0",
    },
    crumb: {
      color: "#58a6ff", cursor: "pointer", background: "none", border: "none",
      padding: "2px 4px", fontSize: 13, borderRadius: 4,
    },
    crumbActive: {
      color: "#e6edf3", cursor: "default", background: "none", border: "none",
      padding: "2px 4px", fontSize: 13, borderRadius: 4,
    },
    sep: { color: "#484f58", margin: "0 1px" },
    toolRow: {
      display: "flex", alignItems: "center", gap: 8, padding: "6px 12px",
      background: "#0d1117", borderBottom: "1px solid #21262d", flexShrink: 0, flexWrap: "wrap",
    },
    toolHint: { color: "#8b949e", fontSize: 12 },
    body: { flex: 1, minHeight: 0, overflow: "auto", background: "#0d1117" },
    dropOverlay: {
      position: "fixed", inset: 0, zIndex: 9998, pointerEvents: "none",
      display: "flex", alignItems: "center", justifyContent: "center",
      background: "rgba(31,111,235,0.12)",
    },
    dropHint: {
      background: "#0d1117", color: "#58a6ff", border: "2px dashed #58a6ff",
      borderRadius: 12, padding: "18px 40px", fontSize: 15, fontWeight: 600,
      boxShadow: "0 8px 32px rgba(0,0,0,.5)",
    },
    table: { width: "100%", borderCollapse: "collapse", fontSize: 13 },
    th: {
      textAlign: "left", padding: "6px 10px", color: "#8b949e", fontSize: 12, fontWeight: 600,
      borderBottom: "1px solid #21262d", position: "sticky", top: 0, background: "#0d1117", zIndex: 1,
    },
    td: { padding: "6px 10px", borderBottom: "1px solid #161b22", verticalAlign: "middle" },
    row: { cursor: "pointer" },
    rowSelected: { cursor: "pointer", background: "rgba(56,139,253,0.12)" },
    name: { color: "#c9d1d9", marginLeft: 6 },
    nameDir: { color: "#58a6ff", marginLeft: 6 },
    cb: { width: 16, height: 16, accentColor: "#58a6ff", cursor: "pointer" },
    opBtn: {
      background: "none", color: "#8b949e", border: "1px solid #30363d", borderRadius: 4,
      padding: "1px 8px", cursor: "pointer", fontSize: 12, marginRight: 4,
    },
    empty: { padding: 48, textAlign: "center", color: "#8b949e" },
    loading: { padding: 24, textAlign: "center", color: "#8b949e" },
    footer: {
      flexShrink: 0, padding: "6px 12px", borderTop: "1px solid #21262d",
      color: "#8b949e", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
    },
    overlay: {
      position: "fixed", inset: 0, background: "rgba(1,4,9,0.7)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999,
    },
    modal: {
      width: "min(560px, 92vw)", background: "#0d1117", border: "1px solid #30363d",
      borderRadius: 8, display: "flex", flexDirection: "column", overflow: "hidden",
      boxShadow: "0 8px 32px rgba(0,0,0,.5)",
    },
    modalHead: {
      display: "flex", alignItems: "center", gap: 8, padding: "10px 14px",
      background: "#161b22", borderBottom: "1px solid #30363d", flexShrink: 0,
    },
    modalTitle: { color: "#e6edf3", fontWeight: 600 },
    modalBody: { padding: "14px", display: "flex", flexDirection: "column", gap: 10, minHeight: 0, overflow: "auto" },
    input: {
      background: "#010409", color: "#e6edf3", border: "1px solid #30363d",
      borderRadius: 6, padding: "6px 10px", fontSize: 13, outline: "none",
    },
    modalFoot: {
      display: "flex", justifyContent: "flex-end", gap: 8, padding: "10px 14px",
      borderTop: "1px solid #21262d", flexShrink: 0,
    },
    delList: { maxHeight: 160, overflow: "auto", color: "#ffa198", fontSize: 12, lineHeight: 1.7 },
    delNote: { color: "#8b949e", fontSize: 12 },
    pre: {
      flex: 1, minHeight: 0, margin: 0, padding: 12, overflow: "auto",
      background: "#010409", color: "#e6edf3",
      fontFamily: "SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
      fontSize: 12.5, lineHeight: 1.55, whiteSpace: "pre", tabSize: 2,
    },
  };

  // ---------- 轻量 toast ----------
  var toastRoot = null;
  function toast(msg, isErr) {
    if (!toastRoot) {
      toastRoot = document.createElement("div");
      toastRoot.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:10000;display:flex;flex-direction:column;align-items:center;gap:8px;pointer-events:none;";
      document.body.appendChild(toastRoot);
    }
    var el = document.createElement("div");
    el.textContent = msg;
    el.style.cssText = "background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:8px 16px;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.4);max-width:70vw;" +
      (isErr ? "border-color:#f85149;color:#ffa198;" : "");
    toastRoot.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 2600);
  }

  // ---------- 拖拽上传：递归收集文件（保留目录结构） ----------
  // 用 DataTransferItem.webkitGetAsEntry 递归遍历目录树，给每个 File 附加
  // _relPath（相对路径）；entry API 不可用时退化为仅收集顶层文件。
  function collectDropItems(dataTransfer, cb) {
    var files = [];
    var pending = 0;
    var finish = function () { cb(files); };
    var items = dataTransfer && dataTransfer.items;
    var firstEntry = items && items.length && items[0].webkitGetAsEntry
      ? items[0].webkitGetAsEntry()
      : null;
    if (!firstEntry || (firstEntry.isFile === undefined && firstEntry.isDirectory === undefined)) {
      // 环境不支持目录遍历：退化为顶层文件
      var fl = (dataTransfer && dataTransfer.files) || [];
      for (var i = 0; i < fl.length; i++) files.push(fl[i]);
      finish();
      return;
    }
    var traverse = function (entry, prefix) {
      if (entry.isFile) {
        pending++;
        entry.file(function (file) {
          try { file._relPath = prefix ? prefix + "/" + file.name : file.name; } catch (e) { /* noop */ }
          files.push(file);
          if (--pending === 0) finish();
        }, function () { if (--pending === 0) finish(); });
      } else if (entry.isDirectory) {
        var reader = entry.createReader();
        var readBatch = function () {
          reader.readEntries(function (entries) {
            if (!entries.length) {
              if (--pending === 0) finish();
              return;
            }
            entries.forEach(function (child) {
              traverse(child, prefix ? prefix + "/" + entry.name : entry.name);
            });
            readBatch(); // 大目录一次读不完，需循环读至空
          }, function () { if (--pending === 0) finish(); });
        };
        pending++;
        readBatch();
      } else {
        if (--pending === 0) finish();
      }
    };
    for (var j = 0; j < items.length; j++) {
      var e = items[j].webkitGetAsEntry ? items[j].webkitGetAsEntry() : null;
      if (e) traverse(e, "");
    }
    if (pending === 0) finish();
  }

  // ---------- 主组件 ----------
  function FileBrowserComponent() {
    var _p = React.useState("");            // 当前绝对路径（空 = 等待 status）
    var curPath = _p[0], setCurPath = _p[1];
    var _e = React.useState(null);          // 列表数据 {path,parent,entries}
    var entries = _e[0], setEntries = _e[1];
    var _l = React.useState(false);
    var loading = _l[0], setLoading = _l[1];
    var _r = React.useState(null);          // status: {workdir, roots}
    var rootInfo = _r[0], setRootInfo = _r[1];
    var _s = React.useState({});            // 多选: { [path]: entry }
    var selected = _s[0], setSelected = _s[1];
    var _m = React.useState(null);          // modal: {type, entry?, entries?}
    var modal = _m[0], setModal = _m[1];
    var _pv = React.useState(null);         // 预览数据
    var preview = _pv[0], setPreview = _pv[1];
    var _pl = React.useState(false);
    var previewLoading = _pl[0], setPreviewLoading = _pl[1];
    var _a = React.useState("");            // 地址栏输入
    var addr = _a[0], setAddr = _a[1];
    var _u = React.useState(false);         // 上传中
    var uploading = _u[0], setUploading = _u[1];
    var _dr = React.useState(false);        // 拖拽悬停高亮
    var dragOver = _dr[0], setDragOver = _dr[1];
    var fileInput = React.useRef(null);     // 隐藏的文件选择框
    var dirInput = React.useRef(null);      // 隐藏的文件夹选择框（webkitdirectory）
    var dragDepth = React.useRef(0);        // 拖拽进出计数（避免子元素间移动闪烁）

    // 确保文件夹选择框启用 webkitdirectory（React 属性 + DOM 兜底）
    React.useEffect(function () {
      if (dirInput.current) {
        dirInput.current.setAttribute("webkitdirectory", "");
        dirInput.current.setAttribute("directory", "");
      }
    }, []);

    var selectedList = Object.keys(selected).map(function (k) { return selected[k]; });

    // 加载 status：优先恢复上次打开位置（localStorage），否则回默认 WORKING_DIR
    React.useEffect(function () {
      fetchJson(API_BASE + "/status")
        .then(function (data) {
          if (data && data.ok) {
            setRootInfo(data);
            var saved = null;
            try { saved = localStorage.getItem(LS_CUR); } catch (e) { saved = null; }
            if (!curPath && saved) {
              setCurPath(saved);
            } else if (!curPath && data.workdir) {
              setCurPath(data.workdir);
            }
          }
        })
        .catch(function (err) { console.error("[qwenpaw-file-browser] status failed:", err); });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // 加载目录列表
    var fetchList = React.useCallback(function (path) {
      setLoading(true);
      setEntries(null);
      setSelected({});
      fetchJson(API_BASE + "/ls?path=" + encodeURIComponent(path))
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "列目录失败");
          setEntries(data);
        })
        .catch(function (err) {
          console.error("[qwenpaw-file-browser] ls failed:", err);
          var msg = String((err && err.message) || err);
          // 工作区模式下访问越界路径：自动回到 WORKING_DIR，避免停留在不可访问路径
          if (rootInfo && rootInfo.mode === "workdir" && rootInfo.workdir && path !== rootInfo.workdir) {
            setCurPath(rootInfo.workdir);
            toast("仅允许访问 QwenPaw 根目录，已自动返回", true);
            return;
          }
          // 平台模式下保存的路径不可用（被删除/移动/无权限）：清除记录，避免每次刷新都失败
          try { localStorage.removeItem(LS_CUR); } catch (e) { /* 忽略 */ }
          setEntries({ entries: [], parent: null, error: msg });
        })
        .finally(function () { setLoading(false); });
    }, [rootInfo]);

    React.useEffect(function () {
      if (curPath) {
        fetchList(curPath);
        // 记住当前打开位置，刷新后恢复
        try { localStorage.setItem(LS_CUR, curPath); } catch (e) { /* localStorage 不可用时忽略 */ }
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [curPath]);

    // 当前访问模式（供面包屑/工具栏使用）
    var isPlatform = rootInfo && rootInfo.mode === "platform";

    // 面包屑：工作区模式只显示 WORKING_DIR 之内的段（首段 = WORKING_DIR），
    // 避免点击越界段（如 /、/run/csi/...）跳到不可访问路径
    var crumbParts = [];
    var crumbRootLabel = "⛭ /";
    if (curPath) {
      if (isPlatform) {
        if (curPath === "/") {
          crumbParts = ["/"];
        } else {
          var segsP = curPath.split("/").filter(Boolean);
          crumbParts = ["/"].concat(segsP);
        }
      } else {
        var wd = rootInfo ? rootInfo.workdir : "";
        crumbRootLabel = "🏠 工作区";
        if (wd && curPath === wd) {
          crumbParts = [wd];
        } else if (wd && curPath.indexOf(wd + "/") === 0) {
          crumbParts = [wd].concat(curPath.slice(wd.length + 1).split("/"));
        } else {
          // 防御：当前路径不在 WORKING_DIR 内，只显示 WORKING_DIR
          crumbParts = [wd || "/"];
        }
      }
    }
    var crumbs = crumbParts.map(function (seg, i) {
      var isLast = i === crumbParts.length - 1;
      var target;
      if (isPlatform) {
        target = "/" + crumbParts.slice(1, i + 1).join("/");
        if (seg === "/") target = "/";
      } else {
        // 工作区模式：各段对应 WORKING_DIR 前缀 + 相对段
        var wd = rootInfo ? rootInfo.workdir : "";
        target = i === 0 ? wd : wd + "/" + crumbParts.slice(1, i + 1).join("/");
      }
      return h("span", { key: "c" + i, style: { display: "inline-flex", alignItems: "center" } },
        i > 0 ? h("span", { style: S.sep }, "/") : null,
        h("button", {
          style: isLast ? S.crumbActive : S.crumb,
          onClick: function () { if (!isLast) setCurPath(target); },
          onMouseEnter: function (ev) { if (!isLast) ev.currentTarget.style.color = "#79c0ff"; },
          onMouseLeave: function (ev) { if (!isLast) ev.currentTarget.style.color = "#58a6ff"; },
        }, i === 0 && !isPlatform ? crumbRootLabel : (seg === "/" ? "⛭ /" : seg)));
    });

    function toggleSelect(entry) {
      var next = Object.assign({}, selected);
      if (next[entry.path]) delete next[entry.path];
      else next[entry.path] = entry;
      setSelected(next);
    }

    function toggleAll() {
      if (!entries || !entries.entries || !entries.entries.length) return;
      var allSel = entries.entries.every(function (en) { return selected[en.path]; });
      var next = Object.assign({}, selected);
      if (allSel) {
        entries.entries.forEach(function (en) { delete next[en.path]; });
      } else {
        entries.entries.forEach(function (en) { next[en.path] = en; });
      }
      setSelected(next);
    }

    function openPreview(entry) {
      setPreviewLoading(true);
      setPreview(null);
      fetchJson(API_BASE + "/read?path=" + encodeURIComponent(entry.path))
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "读取失败");
          ensurePreviewStyle();
          setPreview(Object.assign({}, data, buildPreview(data)));
        })
        .catch(function (err) {
          toast(String((err && err.message) || err), true);
          setPreview(null);
        })
        .finally(function () { setPreviewLoading(false); });
    }

    function downloadOne(entry) {
      var a = document.createElement("a");
      a.href = API_BASE + "/download?path=" + encodeURIComponent(entry.path);
      a.download = entry.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast("开始下载 " + entry.name);
    }

    function batchDownload() {
      if (!selectedList.length) return;
      var a = document.createElement("a");
      a.href = API_BASE + "/batch/download?paths=" + encodeURIComponent(selectedList.map(function (x) { return x.path; }).join(","));
      a.download = "files.zip";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast("正在打包下载 " + selectedList.length + " 项…");
    }

    function confirmNewDir() { setModal({ type: "newdir" }); }

    function confirmDelete(entriesToDelete) { setModal({ type: "delete", entries: entriesToDelete }); }

    function confirmRename(entry) { setModal({ type: "rename", entry: entry }); }

    function doNewDir(name) {
      if (!name) return;
      var target = curPath === "/" ? "/" + name : curPath + "/" + name;
      fetchJson(API_BASE + "/mkdir", { method: "POST", body: { path: target } })
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "创建失败");
          toast("已创建 " + name);
          setModal(null);
          fetchList(curPath);
        })
        .catch(function (err) { toast(String((err && err.message) || err), true); });
    }

    function doRename(entry, newName) {
      if (!newName || newName === entry.name) { setModal(null); return; }
      fetchJson(API_BASE + "/rename", { method: "POST", body: { path: entry.path, new_name: newName } })
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "重命名失败");
          toast("已重命名为 " + newName);
          setModal(null);
          fetchList(curPath);
        })
        .catch(function (err) { toast(String((err && err.message) || err), true); });
    }

    function doDelete() {
      if (!modal || !modal.entries || !modal.entries.length) return;
      var items = modal.entries;
      var hasDir = items.some(function (x) { return x.type === "dir"; });
      if (items.length === 1) {
        fetchJson(API_BASE + "/delete", { method: "POST", body: { path: items[0].path, recursive: hasDir } })
          .then(function (data) {
            if (!data || data.ok === false) throw new Error((data && data.detail) || "删除失败");
            toast("已删除 " + items[0].name);
            setModal(null);
            fetchList(curPath);
          })
          .catch(function (err) { toast(String((err && err.message) || err), true); });
      } else {
        fetchJson(API_BASE + "/batch/delete", {
          method: "POST",
          body: { paths: items.map(function (x) { return x.path; }), recursive: true },
        }).then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "批量删除失败");
          var failed = (data.failed || []).length;
          toast("已删除 " + data.deleted.length + " 项" + (failed ? "，" + failed + " 项失败" : ""));
          setModal(null);
          fetchList(curPath);
        }).catch(function (err) { toast(String((err && err.message) || err), true); });
      }
    }

    function jumpTo(target) {
      if (!target) return;
      var p = target.trim();
      if (!p) return;
      if (!p.startsWith("/")) p = (rootInfo ? rootInfo.workdir : "") + "/" + p;
      setCurPath(p);
    }

    function toggleMode() {
      var next = (rootInfo && rootInfo.mode === "platform") ? "workdir" : "platform";
      fetchJson(API_BASE + "/mode", { method: "POST", body: { mode: next } })
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "切换模式失败");
          setRootInfo(function (prev) { return Object.assign({}, prev, data); });
          if (data.mode === "workdir") {
            // 切回工作区模式：若当前路径在 WORKING_DIR 外，强制回到 WORKING_DIR
            var wd = data.workdir;
            if (curPath && curPath !== wd && curPath.indexOf(wd + "/") !== 0) {
              setCurPath(wd);
            } else {
              fetchList(curPath);
            }
          } else {
            if (curPath) fetchList(curPath);
            else if (data.workdir) setCurPath(data.workdir);
          }
          toast(data.mode === "platform" ? "已切换到平台模式（可访问所有路径）" : "已切换到工作区模式（仅 QwenPaw 根目录）");
        })
        .catch(function (err) { toast(String((err && err.message) || err), true); });
    }

    function uploadFiles(fileList) {
      if (!fileList || !fileList.length || !curPath) return;
      setUploading(true);
      var fd = new FormData();
      Array.prototype.forEach.call(fileList, function (f) {
        // 文件夹选择/拖拽目录时用相对路径（保留目录结构），普通文件用文件名
        var rel = f._relPath || f.webkitRelativePath || f.name;
        fd.append("files", f, rel);
      });
      fetch(API_BASE + "/upload?path=" + encodeURIComponent(curPath), { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data || data.ok === false) throw new Error((data && data.detail) || "上传失败");
          var dirs = data.dirs || [];
          var msg = "已上传 " + data.saved.length + " 个文件";
          if (dirs.length) msg += "（新建 " + dirs.length + " 个文件夹）";
          toast(msg + "：" + data.saved.join("、"));
          fetchList(curPath);
        })
        .catch(function (err) { toast(String((err && err.message) || err), true); })
        .finally(function () { setUploading(false); });
    }

    function onPickFiles(ev) {
      uploadFiles(ev.currentTarget.files);
      ev.currentTarget.value = ""; // 允许重复选择同一文件
    }

    function onPickDir(ev) {
      var arr = Array.prototype.slice.call(ev.currentTarget.files || []);
      uploadFiles(arr); // File.webkitRelativePath 自动带目录结构
      ev.currentTarget.value = ""; // 允许重复选择同一文件夹
    }

    // 拖拽事件（绑定到页面根容器）
    function onDragEnter(ev) {
      ev.preventDefault();
      dragDepth.current++;
      setDragOver(true);
    }
    function onDragOver(ev) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "copy";
    }
    function onDragLeave(ev) {
      ev.preventDefault();
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragOver(false);
    }
    function onDrop(ev) {
      ev.preventDefault();
      dragDepth.current = 0;
      setDragOver(false);
      if (!curPath) { toast("请先进入目标目录再拖拽上传", true); return; }
      if (uploading) { toast("正在上传中，请稍候", true); return; }
      collectDropItems(ev.dataTransfer, function (files) {
        if (!files.length) { toast("未读取到可上传的文件", true); return; }
        uploadFiles(files);
      });
    }

    // 表格行
    var rows = [];
    if (entries && entries.parent !== null && entries.parent !== undefined) {
      rows.push(h("tr", {
        key: "..", style: S.row,
        onClick: function () { setCurPath(entries.parent); },
      },
        h("td", { style: S.td }, ""),
        h("td", { style: S.td }, h("span", { style: S.nameDir }, "📂 ..")),
        h("td", { style: S.td }, "-"),
        h("td", { style: S.td }, "-"),
        h("td", { style: S.td }, "-"),
      ));
    }
    (entries ? entries.entries : []).forEach(function (entry) {
      var isDir = entry.type === "dir";
      var isSel = !!selected[entry.path];
      rows.push(h("tr", {
        key: entry.path,
        style: isSel ? S.rowSelected : S.row,
        onClick: function (ev) {
          if (ev.target && ev.target.tagName && ev.target.tagName.toLowerCase() === "input") return;
          if (isDir) setCurPath(entry.path);
          else openPreview(entry);
        },
        title: isDir ? "点击进入" : "点击预览",
      },
        h("td", { style: S.td },
          h("input", {
            type: "checkbox", style: S.cb, checked: isSel,
            onClick: function (ev) { ev.stopPropagation(); },
            onChange: function () { toggleSelect(entry); },
          })),
        h("td", { style: S.td },
          h("span", { style: isDir ? S.nameDir : S.name }, (isDir ? "📂 " : "📄 ") + entry.name)),
        h("td", { style: S.td }, isDir ? "-" : entry.size_h),
        h("td", { style: S.td }, fmtTime(entry.mtime)),
        h("td", { style: S.td },
          h("button", {
            style: S.opBtn,
            onClick: function (ev) { ev.stopPropagation(); isDir ? null : downloadOne(entry); },
            disabled: isDir,
          }, "下载"),
          h("button", {
            style: S.opBtn,
            onClick: function (ev) { ev.stopPropagation(); confirmRename(entry); },
          }, "重命名"),
        )));
    });

    var body;
    if (loading) {
      body = h("div", { style: S.loading }, "加载中…");
    } else if (!entries || !entries.entries || entries.entries.length === 0) {
      body = h("div", { style: S.empty },
        entries && entries.error ? "加载失败：" + entries.error : "空目录");
    } else {
      var allSelected = entries.entries.every(function (en) { return selected[en.path]; });
      body = h("table", { style: S.table },
        h("thead", null,
          h("tr", null,
            h("th", { style: S.th, width: 32 },
              h("input", {
                type: "checkbox", style: S.cb, checked: allSelected && entries.entries.length > 0,
                onChange: toggleAll,
              })),
            h("th", { style: S.th }, "名称"),
            h("th", { style: S.th, width: 90 }, "大小"),
            h("th", { style: S.th, width: 160 }, "修改时间"),
            h("th", { style: S.th, width: 200 }, "操作"),
          )),
        h("tbody", null, rows));
    }

    // 预览弹层：md/json/配置文件走 HTML 渲染，其余纯文本
    var previewModal = null;
    if (preview) {
      var previewBody;
      if (preview.kind === "html") {
        previewBody = h("div", {
          className: "qfb-md",
          style: Object.assign({}, S.pre, {
            whiteSpace: "normal", overflow: "auto", padding: 16,
          }),
          dangerouslySetInnerHTML: { __html: preview.html },
        });
      } else {
        previewBody = h("pre", { style: S.pre }, preview.content);
      }
      previewModal = h("div", { style: S.overlay, onClick: function () { setPreview(null); } },
        h("div", { style: Object.assign({}, S.modal, { width: "min(920px, 92vw)", height: "min(640px, 84vh)" }),
          onClick: function (ev) { ev.stopPropagation(); } },
          h("div", { style: S.modalHead },
            h("span", { style: S.modalTitle }, "📄 " + preview.name),
            h("span", {
              style: {
                background: "#1f6feb", color: "#fff", borderRadius: 10,
                padding: "1px 8px", fontSize: 11, marginLeft: 8, whiteSpace: "nowrap",
              },
            }, preview.badge || "文本"),
            h("span", { style: { color: "#8b949e", fontSize: 12, marginLeft: "auto" } },
              fmtSize(preview.size) + (preview.truncated ? " · 已截断预览" : "")),
            h("button", { style: S.btn, onClick: function () { setPreview(null); } }, "关闭")),
          previewBody));
    } else if (previewLoading) {
      previewModal = h("div", { style: S.overlay },
        h("div", { style: Object.assign({}, S.modal, { alignItems: "center", justifyContent: "center" }) },
          h("span", { style: S.loading }, "读取中…")));
    }

    // 操作弹层（新建/重命名/删除）：弹层为独立组件，回调经 props 传入，
    // 避免引用组件内闭包变量导致 ReferenceError
    var actionModal = null;
    if (modal && modal.type === "newdir") {
      actionModal = h(NewDirModal, {
        onConfirm: doNewDir,
        onCancel: function () { setModal(null); },
      });
    } else if (modal && modal.type === "rename") {
      actionModal = h(RenameModal, {
        entry: modal.entry,
        onConfirm: doRename,
        onCancel: function () { setModal(null); },
      });
    } else if (modal && modal.type === "delete") {
      actionModal = h(DeleteModal, {
        entries: modal.entries || [],
        onConfirm: doDelete,
        onCancel: function () { setModal(null); },
      });
    }

    // 模式按钮（并排双按钮：当前模式高亮，结构一致）
    var modeHint = rootInfo && rootInfo.mode_source === "auto"
      ? (isPlatform ? "已自动识别为 qwenpaw-agentscope-platform 平台" : "未检测到平台环境")
      : "手动模式";
    var modeGroup = h("span", { style: { display: "inline-flex", gap: 6, alignItems: "center" } },
      h("button", {
        style: isPlatform ? S.btn : S.btnActive,
        title: "工作区模式：仅访问 QwenPaw 根目录",
        onClick: isPlatform ? toggleMode : undefined,
      }, "📦 工作区"),
      h("button", {
        style: isPlatform ? S.btnActive : S.btn,
        title: "平台模式：访问所有支持访问的路径" + (isPlatform ? "（当前生效）" : ""),
        onClick: isPlatform ? undefined : toggleMode,
      }, "🌐 平台"),
      h("span", { style: S.toolHint }, modeHint));

    // 工具栏
    var toolRow = h("div", { style: S.toolRow },
      modeGroup,
      h("input", {
        ref: fileInput, type: "file", multiple: true, style: { display: "none" },
        onChange: onPickFiles,
      }),
      h("input", {
        ref: dirInput, type: "file", multiple: true, style: { display: "none" },
        webkitdirectory: "", directory: "",
        onChange: onPickDir,
      }),
      h("button", {
        style: S.btnPrimary,
        onClick: function () { if (fileInput.current) fileInput.current.click(); },
        disabled: uploading,
        title: "上传文件到当前目录（支持多选，也可直接把文件/文件夹拖进窗口）",
      }, uploading ? "⬆ 上传中…" : "⬆ 上传"),
      h("button", {
        style: S.btnPrimary,
        onClick: function () { if (dirInput.current) dirInput.current.click(); },
        disabled: uploading,
        title: "上传整个文件夹到当前目录（保留目录结构）",
      }, uploading ? "⬆ 上传中…" : "📁 上传文件夹"),
      h("button", { style: S.btnPrimary, onClick: confirmNewDir }, "📁 新建文件夹"),
      h("button", { style: S.btn, onClick: function () { if (curPath) fetchList(curPath); } }, "🔄 刷新"),
      h("select", {
        style: S.select,
        value: "",
        onChange: function (ev) { if (ev.target.value) jumpTo(ev.target.value); },
      },
        h("option", { value: "", disabled: true }, "快捷根目录…"),
        (rootInfo && rootInfo.roots || []).map(function (r) {
          return h("option", { key: r.path, value: r.path }, r.label);
        })),
      selectedList.length > 0 ?
        h("span", { style: { display: "inline-flex", gap: 8, alignItems: "center" } },
          h("span", { style: S.toolHint }, "已选 " + selectedList.length + " 项"),
          h("button", { style: S.btn, onClick: batchDownload }, "⬇ 打包下载"),
          h("button", { style: S.btnDanger, onClick: function () { confirmDelete(selectedList); } }, "🗑 删除所选"))
        : null,
      h("span", { style: Object.assign({}, S.toolHint, { marginLeft: "auto" }) },
        isPlatform ? "平台模式：可访问所有支持访问的路径（遵循系统权限）" : "工作区模式：仅 QwenPaw 根目录，点击「🌐 平台模式」可切换"));

    return h("div", {
      style: S.page,
      onDragEnter: onDragEnter,
      onDragOver: onDragOver,
      onDragLeave: onDragLeave,
      onDrop: onDrop,
    },
      h("div", { style: S.header },
        h("span", { style: S.brand }, "📁 " + PLUGIN_NAME),
        h("span", { style: S.badge }, "v" + VERSION),
        h("input", {
          style: S.addr, value: addr,
          placeholder: isPlatform
            ? "输入绝对路径（如 /tmp 或 " + (rootInfo ? rootInfo.workdir : "WORKING_DIR") + "）"
            : "仅可访问 QwenPaw 根目录：" + (rootInfo ? rootInfo.workdir : "WORKING_DIR"),
          onChange: function (ev) { setAddr(ev.currentTarget.value); },
          onKeyDown: function (ev) { if (ev.key === "Enter") { jumpTo(addr); } },
        }),
        h("button", { style: S.btn, onClick: function () { jumpTo(addr); } }, "跳转"),
        h("button", {
          style: S.btn,
          onClick: function () {
            if (rootInfo && rootInfo.workdir) { setCurPath(rootInfo.workdir); }
          },
        }, "🏠 工作区"),
      ),
      h("div", { style: { display: "flex", alignItems: "center", gap: 8, padding: "6px 12px", borderBottom: "1px solid #21262d", flexShrink: 0, overflow: "hidden" } },
        h("span", { style: { color: "#8b949e", fontSize: 12, whiteSpace: "nowrap" } }, "路径:"),
        h("div", { style: S.crumbRow }, crumbs.length ? crumbs : h("span", { style: { color: "#8b949e", fontSize: 12 } }, "…"))),
      toolRow,
      h("div", { style: S.body }, body),
      h("div", { style: S.footer },
        (entries && entries.entries ? entries.entries.length : 0) + " 项" +
        (selectedList.length ? " · 已选 " + selectedList.length + " 项" : "") +
        " · " + (isPlatform ? "🌐 平台模式" : "📦 工作区模式") +
        (rootInfo && rootInfo.mode_source === "auto"
          ? (isPlatform ? "（自动识别）" : "（未检测到平台）")
          : "（手动）")),
      actionModal,
      previewModal,
      dragOver ? h("div", { style: S.dropOverlay },
        h("div", { style: S.dropHint }, "📥 松开鼠标，上传到当前目录" +
          (curPath ? "：" + curPath : ""))) : null);
  }

  // ---------- 弹层组件 ----------
  function ModalShell(title, bodyContent) {
    // 第 3 个及之后的参数均为底部操作区子元素（按钮等），全部收集
    var foot = [];
    for (var i = 2; i < arguments.length; i++) foot.push(arguments[i]);
    return h("div", { style: S.overlay, onClick: function () { } },
      h("div", { style: S.modal, onClick: function (ev) { ev.stopPropagation(); } },
        h("div", { style: S.modalHead }, h("span", { style: S.modalTitle }, title)),
        h("div", { style: S.modalBody }, bodyContent),
        h("div", { style: S.modalFoot }, foot)));
  }

  function NewDirModal(props) {
    var _v = React.useState("");
    var val = _v[0], setVal = _v[1];
    return ModalShell("新建文件夹", null,
      h("input", {
        style: S.input, autoFocus: true, placeholder: "文件夹名称", value: val,
        onChange: function (ev) { setVal(ev.currentTarget.value); },
        onKeyDown: function (ev) {
          if (ev.key === "Enter") props.onConfirm(val.trim());
          if (ev.key === "Escape") props.onCancel();
        },
      }),
      h("button", { style: S.btn, onClick: props.onCancel }, "取消"),
      h("button", { style: S.btnPrimary, onClick: function () { props.onConfirm(val.trim()); } }, "创建"));
  }

  function RenameModal(props) {
    var _v = React.useState(props.entry.name);
    var val = _v[0], setVal = _v[1];
    return ModalShell("重命名",
      // 第二段：当前文件 + 输入框
      h("div", { style: { color: "#8b949e", fontSize: 12, wordBreak: "break-all" } }, "当前: " + props.entry.path),
      h("input", {
        style: S.input, autoFocus: true, value: val,
        onChange: function (ev) { setVal(ev.currentTarget.value); },
        onKeyDown: function (ev) {
          if (ev.key === "Enter") props.onConfirm(props.entry, val.trim());
          if (ev.key === "Escape") props.onCancel();
        },
      }),
      // 第三段：提示语（左）+ 操作按钮（右）
      h("div", { style: Object.assign({}, S.delNote, { marginRight: "auto" }) }, "输入新名称，按 Enter 确认"),
      h("button", { style: S.btn, onClick: props.onCancel }, "取消"),
      h("button", { style: S.btnPrimary, onClick: function () { props.onConfirm(props.entry, val.trim()); } }, "重命名"));
  }

  function DeleteModal(props) {
    var items = props.entries || [];
    var hasDir = items.some(function (x) { return x.type === "dir"; });
    var names = items.slice(0, 8).map(function (x) { return x.name; }).join("、");
    return ModalShell("删除确认",
      // 第二段：文件名列表
      h("div", { style: S.delList }, names + (items.length > 8 ? " 等 " + items.length + " 项" : "")),
      // 第三段：提示语（左）+ 操作按钮（右）
      h("div", { style: Object.assign({}, S.delNote, { marginRight: "auto" }) },
        items.length === 1
          ? "确定删除「" + items[0].name + "」吗？此操作不可恢复。"
          : "确定删除选中的 " + items.length + " 项吗？此操作不可恢复。"),
      hasDir ? h("div", { style: { color: "#d29922", fontSize: 12, marginRight: "auto" } }, "⚠ 包含文件夹：将连同文件夹内全部内容一起删除") : null,
      h("button", { style: S.btn, onClick: props.onCancel }, "取消"),
      h("button", { style: S.btnDanger, onClick: props.onConfirm }, "确认删除"));
  }

  // ---------- 注册三件套（兼容模式，与 web-terminal 一致） ----------
  if (QP.registerRoutes) {
    try {
      QP.registerRoutes(PLUGIN_ID, [{
        path: "/apps/qwenpaw-file-browser",
        component: FileBrowserComponent,
        label: PLUGIN_NAME,
        icon: "📁",
      }]);
      console.info("[qwenpaw-file-browser] registered via registerRoutes");
    } catch (err) {
      console.warn("[qwenpaw-file-browser] registerRoutes failed:", err);
    }
  }

  if (QP.menu && QP.menu.add) {
    try {
      QP.menu.add(PLUGIN_ID, [{
        id: PLUGIN_ID + ".menu",
        location: "primary.settings",
        label: "文件",
        icon: function () { return h("span", { style: { fontSize: 18 } }, "📁"); },
        route: PLUGIN_ID + ".home",
        order: 80,
      }]);
      console.info("[qwenpaw-file-browser] registered via menu.add");
    } catch (err) {
      console.warn("[qwenpaw-file-browser] menu.add failed:", err);
    }
  }

  if (QP.route && QP.route.add) {
    try {
      QP.route.add(PLUGIN_ID, [{
        id: PLUGIN_ID + ".home",
        path: "/plugin/qwenpaw-file-browser",
        component: FileBrowserComponent,
      }]);
      console.info("[qwenpaw-file-browser] registered via route.add");
    } catch (err) {
      console.warn("[qwenpaw-file-browser] route.add failed:", err);
    }
  }

  console.info("[qwenpaw-file-browser] v" + VERSION + " loaded");
})();

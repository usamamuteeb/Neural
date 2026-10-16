// Tiny, safe markdown for reply bubbles (same rules as the web app's
// renderMarkdown): everything is HTML-escaped first, then a few patterns
// become tags. Links only ever point at http(s) URLs.
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderMarkdown(src) {
    const blocks = [];
    const inline = [];
    let out = escapeHtml(src || "");
    out = out.replace(/```[\w+-]*\n?([\s\S]*?)(?:```|$)/g, (m, code) => {
      blocks.push("<pre><code>" + code.replace(/\n$/, "") + "</code></pre>");
      return "\u0000" + (blocks.length - 1) + "\u0000";
    });
    out = out.replace(/`([^`\n]+)`/g, (m, c) => {
      inline.push("<code>" + c + "</code>");
      return "\u0001" + (inline.length - 1) + "\u0001";
    });
    out = out
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"'<>]+)\)/g, '<a href="$2">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s)"'<>]+)/g, '$1<a href="$2">$2</a>');
    let html = "";
    let list = null;
    let para = [];
    const flushPara = () => {
      if (para.length) {
        html += "<p>" + para.join("<br>") + "</p>";
        para = [];
      }
    };
    const flushList = () => {
      if (list) {
        html += "</" + list + ">";
        list = null;
      }
    };
    for (const line of out.split("\n")) {
      const ul = line.match(/^\s*[-*\u2022]\s+(.*)$/);
      const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        const tag = ul ? "ul" : "ol";
        if (list !== tag) {
          flushList();
          html += "<" + tag + ">";
          list = tag;
        }
        html += "<li>" + (ul ? ul[1] : ol[1]) + "</li>";
        continue;
      }
      flushList();
      if (!line.trim()) {
        flushPara();
        continue;
      }
      if (/^\u0000\d+\u0000$/.test(line.trim())) {
        flushPara();
        html += line.trim();
        continue;
      }
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      if (heading) {
        flushPara();
        html += "<p><strong>" + heading[1] + "</strong></p>";
        continue;
      }
      para.push(line);
    }
    flushPara();
    flushList();
    return html
      .replace(/\u0000(\d+)\u0000/g, (m, i) => blocks[+i])
      .replace(/\u0001(\d+)\u0001/g, (m, i) => inline[+i]);
  }

  window.NeuralMarkdown = { renderMarkdown, escapeHtml };
})();

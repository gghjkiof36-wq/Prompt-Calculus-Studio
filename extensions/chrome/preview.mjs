// A deliberately small, text-node-only reading projection. The capture is untouched.
export function excerpt(markdown) {
  return markdown.replace(/^\s*(`{3,}|~{3,}).*$/gm, '')
    .replace(/^\s*\|?[\s|:-]+\|\s*$/gm, '')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1')
    .replace(/\s*\|\s*/g, ' · ').replace(/\s+/g, ' ').trim();
}

export function renderReading(markdown, document = globalThis.document) {
  const root = document.createElement('div');
  root.className = 'reading';
  function inline(parent, text) {
    const tokens = /(`+)([^`]+)\1|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*/g;
    let end = 0;
    for (const match of text.matchAll(tokens)) {
      parent.append(document.createTextNode(text.slice(end, match.index)));
      if (match[2]) {
        const code = document.createElement('code'); code.textContent = match[2]; parent.append(code);
      } else if (match[3]) {
        const link = document.createElement('a'); link.textContent = match[3]; link.href = match[4];
        link.target = '_blank'; link.rel = 'noopener noreferrer'; parent.append(link);
      } else {
        const strong = document.createElement('strong'); strong.textContent = match[5]; parent.append(strong);
      }
      end = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(end)));
  }
  const lines = markdown.split('\n');
  const cells = line => line.replace(/^\s*\||\|\s*$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      const codeLines = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[i])) codeLines.push(lines[i++]);
      if (i < lines.length) i++;
      const pre = document.createElement('pre'); const code = document.createElement('code');
      code.textContent = codeLines.join('\n'); pre.append(code); root.append(pre); continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
    if (heading) {
      const element = document.createElement('h' + Math.min(6, heading[1].length + 1));
      inline(element, heading[2]); root.append(element); i++; continue;
    }
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?[\s|:-]*\|?\s*$/.test(lines[i + 1])) {
      const table = document.createElement('table');
      const add = (row, header) => {
        const tr = document.createElement('tr');
        for (const cell of cells(row)) {
          const element = document.createElement(header ? 'th' : 'td'); inline(element, cell.replace(/<br\s*\/?\s*>/gi, '\n')); tr.append(element);
        }
        table.append(tr);
      };
      add(line, true); i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) add(lines[i++], false);
      const wrapper = document.createElement('div'); wrapper.className = 'table-scroll'; wrapper.append(table); root.append(wrapper); continue;
    }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { root.append(document.createElement('hr')); i++; continue; }
    const list = line.match(/^\s*(?:([-*+])|(\d+)\.)\s+(.+)$/);
    if (list) {
      const ul = document.createElement(list[2] ? 'ol' : 'ul');
      if (list[2]) ul.start = Number(list[2]);
      const pattern = list[2] ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*+]\s+(.+)$/;
      while (i < lines.length) {
        const item = lines[i].match(pattern); if (!item) break;
        const li = document.createElement('li'); inline(li, item[1]); ul.append(li); i++;
      }
      root.append(ul); continue;
    }
    const paragraph = document.createElement(line.startsWith('> ') ? 'blockquote' : 'p');
    const text = [];
    while (i < lines.length && lines[i].trim()) {
      if (text.length && /^(?:#{1,6}\s|`{3,}|~{3,}|[-*+]\s|\d+\.\s)/.test(lines[i])) break;
      text.push(lines[i++].replace(/^> ?/, ''));
    }
    inline(paragraph, text.join('\n')); root.append(paragraph);
  }
  return root;
}

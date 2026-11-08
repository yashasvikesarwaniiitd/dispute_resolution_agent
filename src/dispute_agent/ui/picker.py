"""Clickable lists and tables, drawn in the page and answered in Python (ADR 0048).

Streamlit's own table cannot hold a status chip, a "who did it" pill or a link per row, and
a plain HTML link would reload the page and start a new session (losing the site password).
So this one component - a Streamlit v2 component, our own HTML and JavaScript run on the
page, no build step - draws a case list or a table from data and reports two things back:
`pick` (a row was chosen) and `open` (a row's link was followed).

**Data, never markup.** The JavaScript writes every value with `textContent`, and takes
style names only from fixed lists, so a shipment reference or any recorded text can never
become markup (I5). Decisions are not made here: picking only chooses what is shown.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict

import streamlit as st

JS = """
const TONES = ['working', 'waiting', 'attention', 'done', 'closed', 'settled'];
const ACTORS = ['code', 'ai', 'rules', 'person', 'seller', 'hub', 'system'];
const KINDS = ['text', 'mono', 'num', 'paid', 'error', 'muted'];
const safe = (v, list, d) => (list.includes(v) ? v : d);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function cell(c, send) {
  if (c && typeof c === 'object' && c.chip !== undefined) {
    return el('span', 'chip chip--' + safe(c.tone, TONES, 'working'), c.chip);
  }
  if (c && typeof c === 'object' && c.actor !== undefined) {
    return el('span', 'actor actor--' + safe(c.style, ACTORS, 'system'), c.actor);
  }
  if (c && typeof c === 'object' && c.link !== undefined) {
    const a = el('a', 'pick-link', c.link);
    a.href = '#';
    if (c.label) a.setAttribute('aria-label', String(c.label));
    a.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); send('open', String(c.target)); });
    return a;
  }
  if (c && typeof c === 'object' && c.stack !== undefined) {
    const s = el('span', 'pick-stack');
    c.stack.forEach((part) => s.appendChild(cell(part, send)));
    return s;
  }
  if (c && typeof c === 'object') {
    return el('span', 'pick-' + safe(c.kind, KINDS, 'text'), c.text);
  }
  return el('span', 'pick-text', c === undefined || c === null ? '' : c);
}

function cases(root, d, send) {
  const ul = el('ul', 'pick-cases');
  (d.rows || []).forEach((r) => {
    const li = el('li');
    const b = el('button', 'pick-case');
    b.type = 'button';
    b.setAttribute('aria-pressed', r.id === d.selected ? 'true' : 'false');
    const top = el('span', 'pick-case-top');
    top.appendChild(el('span', 'pick-mono', r.title));
    top.appendChild(el('span', 'pick-muted', r.aside));
    b.appendChild(top);
    b.appendChild(el('span', 'chip chip--' + safe(r.tone, TONES, 'working'), r.status));
    b.addEventListener('click', () => send('pick', String(r.id)));
    li.appendChild(b);
    ul.appendChild(li);
  });
  root.appendChild(ul);
  if (!(d.rows || []).length && d.empty) root.appendChild(el('p', 'muted', d.empty));
}

function table(root, d, send) {
  const cols = (d.columns || []);
  const grid = /^[0-9a-z.()% ,-]+$/i.test(d.grid || '') ? d.grid : cols.map(() => '1fr').join(' ');
  const wrap = el('div', 'pick-table');
  wrap.setAttribute('role', 'table');
  if (d.label) wrap.setAttribute('aria-label', String(d.label));
  const head = el('div', 'pick-row pick-head');
  head.setAttribute('role', 'row');
  head.style.gridTemplateColumns = grid;
  cols.forEach((c) => {
    const h = el('span', c.align === 'right' ? 'pick-right' : '', c.label);
    h.setAttribute('role', 'columnheader');
    head.appendChild(h);
  });
  wrap.appendChild(head);
  (d.rows || []).forEach((r) => {
    const row = el(d.selectable ? 'button' : 'div', 'pick-row' + (r.tint ? ' pick-tint' : ''));
    row.setAttribute('role', 'row');
    row.style.gridTemplateColumns = grid;
    if (d.selectable) {
      row.type = 'button';
      const on = r.id === d.selected;
      row.setAttribute('aria-pressed', on ? 'true' : 'false');
      row.addEventListener('click', () => send('pick', String(r.id)));
    }
    (r.cells || []).forEach((c, i) => {
      const td = el('span', (cols[i] && cols[i].align === 'right') ? 'pick-right' : '');
      td.setAttribute('role', 'cell');
      td.appendChild(cell(c, send));
      row.appendChild(td);
    });
    wrap.appendChild(row);
  });
  root.appendChild(wrap);
  if (!(d.rows || []).length && d.empty) root.appendChild(el('p', 'muted pick-empty', d.empty));
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector(':scope > .pick');
  if (!root) {
    root = el('div', 'pick');
    parentElement.appendChild(root);
  }
  root.replaceChildren();
  const d = data || {};
  const send = (name, value) => setTriggerValue(name, value);
  if (d.view === 'cases') cases(root, d, send);
  else table(root, d, send);
}
"""

_component: Any = None


def _mount() -> Any:
    global _component
    if _component is None:
        _component = st.components.v2.component("dispute_desk_pick", js=JS, isolate_styles=False)
    return _component


class Picked(TypedDict, total=False):
    pick: str | None
    open: str | None


def show(data: dict[str, Any], *, key: str) -> Picked:
    """Draw a case list (`view: "cases"`) or a table, and return what was clicked this run.

    The key carries a digest of the data: a mounted component keeps the DOM it first drew,
    so new data (another case chosen, a new ticket) mounts a fresh one. A click is read in the
    run it causes, which still draws the old data, so no click is lost.
    """
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    result = _mount()(
        data=data,
        key=f"{key}-{digest[:12]}",
        on_pick_change=lambda: None,
        on_open_change=lambda: None,
    )
    return {"pick": getattr(result, "pick", None), "open": getattr(result, "open", None)}


__all__ = ["JS", "Picked", "show"]

"""Render the Claude Code style profile (README.md + assets/*.svg) from live GitHub data.

Nothing here is hand-written per project. Every panel comes from the GitHub API
and the repositories themselves:
  - profile:        name, bio, avatar, join year, social accounts
  - projects:       every public non-fork repo; text = repo description, else the
                    first real paragraph of its README
  - stack:          language bytes + third-party imports found in the .py files
  - now:            latest pushes and commit messages; open todos come from
                    README sections such as "Next steps", "TODO", "Roadmap"
  - contributions:  the public contribution calendar
  - contact:        public email (or commit email), blog, social accounts

    python scripts/build.py            # GITHUB_TOKEN is used if set
"""
import base64
import datetime as dt
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image

USER = os.environ.get("PROFILE_USER", "Avinashistoocool")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
W = 880
FONT = "'JetBrains Mono','SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
LANG_COLORS = {"Python": "#3572A5", "Shell": "#89e051", "HTML": "#e34c26", "CSS": "#663399",
               "Jupyter Notebook": "#DA5B0B", "C++": "#f34b7d", "C": "#555555", "TypeScript": "#3178c6",
               "JavaScript": "#f1e05a", "Java": "#b07219", "Go": "#00ADD8", "Rust": "#dea584",
               "MATLAB": "#e16737", "TeX": "#3D6117", "Dockerfile": "#384d54"}
TODO_HEADINGS = re.compile(r"next steps|to ?do|roadmap|^future work|planned", re.I)
PIP_NAMES = {"cv2": "opencv", "sklearn": "scikit-learn", "PIL": "pillow", "yaml": "pyyaml",
             "bs4": "beautifulsoup4", "google": "google-adk", "dotenv": "python-dotenv"}


# --------------------------------------------------------------------------- fetch

def fetch(url, raw=False):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as ex:
        if ex.code in (404, 409, 451):
            return None
        raise
    return data if raw else json.loads(data)


def api(path):
    return fetch("https://api.github.com/" + path.lstrip("/"))


def readme(repo):
    d = api(f"repos/{USER}/{repo}/readme")
    return base64.b64decode(d["content"]).decode("utf-8", "replace") if d else ""


# --------------------------------------------------------------------------- text helpers

def e(s):
    return html.escape(str(s))


def ago(iso):
    d = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
    if d <= 0:
        return "today"
    if d < 30:
        return f"{d}d ago"
    if d < 365:
        return f"{d // 30}mo ago"
    return f"{d // 365}y ago"


def clean_md(t):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[*_`~]+", "", t)
    t = t.replace("\u2014", "-").replace("\u2013", "-")
    return re.sub(r"\s+", " ", t).strip()


def no_emoji(t):
    return re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]", "", t).strip()


def first_paragraph(md):
    md = re.sub(r"```.*?```", "", md, flags=re.S)
    for block in re.split(r"\n\s*\n", md):
        b = block.strip()
        if not b or b[0] in "#|>-*!<=" or b.startswith("[!") or re.match(r"^\d+\.", b):
            continue
        b = clean_md(b)
        if len(b) >= 40 and not re.match(r"^(hackathon|team|status|author)\b", b, re.I):
            return b
    return ""


def todo_items(md, limit=3):
    """Bullets under a heading like 'Suggested next steps' / 'TODO' / 'Roadmap'."""
    out, on = [], False
    for line in md.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            on = bool(TODO_HEADINGS.search(h.group(1)))
            continue
        if on:
            m = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ x]\]\s*)?(.*)", line)
            if m:
                txt = clean_md(m.group(1)).split(". ")[0].rstrip(".:")
                if len(txt) > 8:
                    out.append(txt)
            if len(out) >= limit:
                break
    return out


def sentence(t, n):
    if len(t) <= n:
        return t
    cut = t[:n]
    dot = cut.rfind(". ")
    return cut[:dot + 1] if dot > n * 0.5 else cut.rsplit(" ", 1)[0] + "…"


def wrap(text, width):
    lines, cur = [], ""
    for w_ in text.split():
        if cur and len(cur) + 1 + len(w_) > width:
            lines.append(cur)
            cur = w_
        else:
            cur = f"{cur} {w_}" if cur else w_
    if cur:
        lines.append(cur)
    return lines


def clip(lines, n, width):
    if len(lines) > n:
        lines = lines[:n]
        lines[-1] = lines[-1][:width - 1].rstrip() + "…"
    return lines


# --------------------------------------------------------------------------- data

def scan_code(repo, branch):
    """Third-party imports (counted per file) across .py/.ipynb files and requirement
    files, plus the best module docstring."""
    tree = api(f"repos/{USER}/{repo}/git/trees/{urllib.parse.quote(branch)}?recursive=1")
    if not tree:
        return Counter(), "", []
    blobs = [t for t in tree.get("tree", []) if t["type"] == "blob"
             and not re.search(r"(^|/)(\.venv|venv|site-packages|node_modules)/", t["path"])]
    paths = [t["path"] for t in blobs]
    local = {Path(p).stem for p in paths} | {p.split("/")[0] for p in paths}
    found, doc = Counter(), ""

    def raw(path):
        b = fetch(f"https://raw.githubusercontent.com/{USER}/{repo}/{urllib.parse.quote(branch)}/{urllib.parse.quote(path)}", raw=True)
        return b.decode("utf-8", "replace") if b else ""

    def add_imports(src):
        mods = set()
        for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", src, re.M):
            mod = m.group(1)
            if mod not in sys.stdlib_module_names and mod not in local and mod != "__future__":
                mods.add(PIP_NAMES.get(mod, mod))
        found.update(mods)

    code = sorted((t for t in blobs if t["path"].endswith((".py", ".ipynb"))), key=lambda t: -t.get("size", 0))[:60]
    pys = [t for t in code if t["path"].endswith(".py")]
    for t in code:
        src = raw(t["path"])
        if not src:
            continue
        if t["path"].endswith(".ipynb"):
            try:
                cells = json.loads(src).get("cells", [])
                src = "\n".join("".join(c.get("source", [])) for c in cells if c.get("cell_type") == "code")
            except ValueError:
                continue
        add_imports(src)
        if t["path"].endswith(".ipynb") or doc:
            continue
        if not doc:
            m = re.match(r'\s*(?:#[^\n]*\n\s*)*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S)
            if m:
                lines = [l.strip() for l in m.group(1).strip().splitlines()]
                lines = [l for l in lines if l and not re.fullmatch(r"[=\-~]+", l)]
                cand = clean_md(" ".join(lines))
                if len(lines) > 1 and len(lines[0]) < 40:
                    cand = clean_md(lines[0].rstrip(".:") + ". " + " ".join(lines[1:]))
                if len(cand) >= 40:
                    doc = cand.split(" Requires:")[0].split(" Dependencies:")[0]
    return found, doc, [t["path"] for t in pys]


def readme_title(md):
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return clean_md(m.group(1)) if m else ""


def collect():
    user = api(f"users/{USER}")
    socials = api(f"users/{USER}/social_accounts") or []
    repos = [r for r in api(f"users/{USER}/repos?per_page=100&type=owner")
             if not r["fork"] and not r["archived"] and r["name"].lower() != USER.lower()]
    repos.sort(key=lambda r: (-r["stargazers_count"], r["pushed_at"]), reverse=False)
    langs, libs, lib_files, todos, commit_emails, commits = Counter(), Counter(), Counter(), [], Counter(), []
    lang_repos = Counter()
    projects = []
    for r in repos:
        if r["size"] == 0:
            continue
        md = readme(r["name"])
        found, doc, pys = scan_code(r["name"], r["default_branch"])
        for k, v in (api(f"repos/{USER}/{r['name']}/languages") or {}).items():
            langs[k] += v
            lang_repos[k] += 1
        for lib, n in found.items():
            libs[lib] += 1
            lib_files[lib] += n
        text = no_emoji(clean_md(r["description"] or "")) or first_paragraph(md) or doc
        if not text:
            title = readme_title(md)
            uses = ", ".join(sorted(found)[:4])
            if title or pys:
                text = (f"{title}. " if title else "") + (f"{len(pys)} Python modules" if pys else "") + \
                       (f" using {uses}." if uses else ".")
        if not text:
            continue
        for t in todo_items(md):
            todos.append((r["pushed_at"], r["name"], t))
        for c in api(f"repos/{USER}/{r['name']}/commits?per_page=5&author={USER}") or []:
            em = c["commit"]["author"]["email"]
            if "noreply" not in em:
                commit_emails[em] += 1
            commits.append((c["commit"]["author"]["date"], r["name"], c["commit"]["message"].split("\n")[0]))
        projects.append({**r, "text": text})
    projects.sort(key=lambda r: (r["stargazers_count"], r["pushed_at"]), reverse=True)
    commits.sort(reverse=True)
    todos.sort(reverse=True)
    email = user.get("email") or (commit_emails.most_common(1)[0][0] if commit_emails else None)
    return dict(user=user, socials=socials, projects=projects, langs=langs, libs=libs,
                lib_files=lib_files, lang_repos=lang_repos, n_repos=len([r for r in repos if r["size"]]),
                todos=todos, commits=commits, email=email)


# --------------------------------------------------------------------------- svg building blocks

def style():
    return f"""<style>
.t{{font-family:{FONT};font-size:14px;fill:#e6e6e6;white-space:pre}}
.s{{font-size:12.5px}}.dim{{fill:#8b8b8b}}.or{{fill:#d97757}}.b{{font-weight:700}}
.gr{{fill:#7fb77e}}.strike{{text-decoration:line-through}}
.ln{{opacity:0;animation:show .01s forwards}}@keyframes show{{to{{opacity:1}}}}
.cur{{animation:blink 1.1s steps(1) infinite}}@keyframes blink{{50%{{opacity:0}}}}
</style>"""


def window(title, h, body, w=W):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">
{style()}
<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="10" fill="#1b1a19" stroke="#3a3836"/>
<path d="M.5 34.5V10.5A10 10 0 0 1 10.5 .5H{w - 10.5}A10 10 0 0 1 {w - .5} 10.5V34.5Z" fill="#232120"/>
<line x1="0" y1="34.5" x2="{w}" y2="34.5" stroke="#3a3836"/>
<circle cx="20" cy="17" r="6" fill="#ff5f57"/><circle cx="40" cy="17" r="6" fill="#febc2e"/><circle cx="60" cy="17" r="6" fill="#28c840"/>
<text x="{w / 2}" y="22" text-anchor="middle" class="t s dim">{e(title)}</text>
{body}
</svg>"""


def prompt(y, cmd, delay=0.2):
    return f'<g class="ln" style="animation-delay:{delay}s"><text x="24" y="{y}" class="t"><tspan class="dim">&gt; </tspan><tspan class="b">{e(cmd)}</tspan></text></g>'


def tool(y, name, arg="", delay=0.6):
    a = f"({e(arg)})" if arg else ""
    return (f'<g class="ln" style="animation-delay:{delay}s"><circle cx="29" cy="{y - 5}" r="4" fill="#7fb77e"/>'
            f'<text x="42" y="{y}" class="t"><tspan class="b">{e(name)}</tspan>{a}</text></g>')


def say(y, lines, delay):
    out = f'<g class="ln" style="animation-delay:{delay}s"><circle cx="29" cy="{y - 5}" r="4" fill="#e6e6e6"/>'
    for i, l in enumerate(lines):
        out += f'<text x="42" y="{y + 22 * i}" class="t b">{e(l)}</text>'
    return out + "</g>"


def row(y, i, cells, delay):
    """cells: list of (x, text, cls). First cell gets the tree glyph."""
    pre = "⎿  " if i == 0 else "   "
    out = f'<g class="ln" style="animation-delay:{delay:.2f}s">'
    for j, (x, txt, cls) in enumerate(cells):
        lead = f'<tspan class="dim">{pre}</tspan>' if j == 0 else ""
        out += f'<text x="{x}" y="{y}" class="t {cls}">{lead}{txt}</text>'
    return out + "</g>"


def avatar(url, n=24, s=3):
    sep = "&" if "?" in url else "?"
    im = Image.open(io.BytesIO(fetch(f"{url}{sep}s=460", raw=True))).convert("RGB")
    small = im.resize((n, n), Image.BOX).quantize(colors=12, method=Image.MEDIANCUT).convert("RGB")
    rects = []
    for y in range(n):
        x = 0
        while x < n:
            c = small.getpixel((x, y))
            x2 = x
            while x2 + 1 < n and small.getpixel((x2 + 1, y)) == c:
                x2 += 1
            rects.append(f'<rect x="{x * s}" y="{y * s}" width="{(x2 - x + 1) * s}" height="{s}" fill="#{c[0]:02x}{c[1]:02x}{c[2]:02x}"/>')
            x = x2 + 1
    return "".join(rects), n * s


# --------------------------------------------------------------------------- panels

def first_name(user):
    return (user.get("name") or USER).split()[0]


def summary_lines(d):
    """'who is X?' answer, assembled from bio, top language, projects and stars."""
    user, projects, langs = d["user"], d["projects"], d["langs"]
    parts = []
    bio = no_emoji(user.get("bio") or "")
    top_lang = langs.most_common(1)[0][0] if langs else None
    if top_lang:
        parts.append(f"Writes mostly {top_lang}.")
    names = [p["name"] for p in projects[:3]]
    if names:
        parts.append("Builds " + ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] + ".")
    starred = [p for p in projects if p["stargazers_count"]]
    if starred:
        parts.append(f"Best known for {starred[0]['name']} (★{starred[0]['stargazers_count']}).")
    latest = max(projects, key=lambda p: p["pushed_at"], default=None)
    if latest:
        parts.append(f"Latest push: {latest['name']}, {ago(latest['pushed_at'])}.")
    return bio, clip(wrap(" ".join(parts), 64), 5, 64)


def hero(d):
    user, projects = d["user"], d["projects"]
    stars = sum(p["stargazers_count"] for p in projects)
    since = user["created_at"][:4]
    av, size = avatar(user["avatar_url"])
    lx = 230
    bio, answer = summary_lines(d)
    recent = sorted(projects, key=lambda p: p["pushed_at"], reverse=True)[:3]
    body = f"""
<rect x="24" y="62" width="{W - 48}" height="166" rx="8" fill="none" stroke="#d97757" stroke-width="1.2"/>
<rect x="38" y="52" width="104" height="20" fill="#1b1a19"/>
<text x="44" y="67" class="t or">Claude Code</text>
<text x="{lx}" y="96" text-anchor="middle" class="t b">Welcome back, {e(first_name(user))}!</text>
<g transform="translate({lx - size / 2},108)" shape-rendering="crispEdges">{av}</g>
<text x="{lx}" y="200" text-anchor="middle" class="t dim">{e(sentence(bio, 44))}</text>
<text x="{lx}" y="218" text-anchor="middle" class="t s dim">~/{e(USER)}</text>
<line x1="430" y1="80" x2="430" y2="212" stroke="#3a3836"/>
<text x="452" y="96" class="t or b">Recent activity</text>"""
    y = 118
    for p in recent:
        n = p["name"] if len(p["name"]) <= 24 else p["name"][:23] + "…"
        body += f'<text x="452" y="{y}" class="t"><tspan class="b">{e(n)}</tspan><tspan class="dim"> · pushed {ago(p["pushed_at"])}</tspan></text>'
        y += 18
    body += f"""
<line x1="452" y1="172" x2="{W - 44}" y2="172" stroke="#3a3836"/>
<text x="452" y="194" class="t or b">Stats</text>
<text x="452" y="214" class="t">★ {stars} stars · {user["public_repos"]} public repos · since {since}</text>
{prompt(270, f"who is {first_name(user).lower()}?", .4)}
{tool(311, "Bash", f"gh api users/{USER}", 1)}
<g class="ln" style="animation-delay:1s"><text x="42" y="333" class="t dim">⎿  "{e(sentence(bio, 36))}" · {user["public_repos"]} public repos · on GitHub since {since}</text></g>
{say(376, answer, 1.8)}"""
    y = 376 + 22 * len(answer) + 22
    body += f"""<g class="ln" style="animation-delay:2.6s"><text x="42" y="{y}" class="t or">↓ pick a prompt below to keep going</text></g>
<g class="ln" style="animation-delay:3s">
<line x1="24" y1="{y + 32}" x2="{W - 24}" y2="{y + 32}" stroke="#4a4846"/>
<text x="24" y="{y + 56}" class="t dim">&gt;</text>
<rect x="42" y="{y + 43}" width="9" height="17" fill="#e6e6e6" class="cur"/>
<line x1="24" y1="{y + 70}" x2="{W - 24}" y2="{y + 70}" stroke="#4a4846"/>
<text x="24" y="{y + 94}" class="t dim">? for shortcuts</text>
</g>"""
    return window(f"claude — ~/{USER}", y + 116, body)


def projects_panel(d):
    body = prompt(70, "/projects") + tool(110, "Bash", f"gh repo list {USER} --source")
    y = 132
    for i, p in enumerate(d["projects"]):
        n = p["name"] if len(p["name"]) <= 38 else p["name"][:37] + "…"
        body += row(y, i, [(42, f'<tspan class="b">{e(n)}</tspan>', ""),
                           (430, f'★ {p["stargazers_count"]}', "or"),
                           (500, e(p["language"] or "-"), "dim"),
                           (640, f'pushed {ago(p["pushed_at"])}', "dim")], .9 + .06 * i)
        y += 21
    return window("claude — /projects", y + 18, body)


def card(p):
    w, h = 432, 150
    lang = p["language"] or "-"
    lines = clip(wrap(p["text"], 44), 3, 44)
    n = p["name"] if len(p["name"]) <= 34 else p["name"][:33] + "…"
    t = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{e(p['name'])}">
{style()}
<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="8" fill="#1b1a19" stroke="#3a3836"/>
<circle cx="22" cy="27" r="4" fill="#7fb77e"/>
<text x="34" y="32" class="t b">{e(n)}</text>
<text x="{w - 18}" y="32" text-anchor="end" class="t or">★ {p["stargazers_count"]}</text>"""
    for i, l in enumerate(lines):
        t += f'<text x="34" y="{56 + 20 * i}" class="t"><tspan class="dim">{"⎿ " if i == 0 else "  "}</tspan>{e(l)}</text>'
    t += f"""<circle cx="22" cy="{h - 22}" r="5" fill="{LANG_COLORS.get(lang, '#8b8b8b')}"/>
<text x="34" y="{h - 17}" class="t s dim">{e(lang)} · pushed {ago(p["pushed_at"])}</text>
<text x="{w - 18}" y="{h - 17}" text-anchor="end" class="t s or">open ↗</text>
</svg>"""
    return t


LIB_GROUPS = [
    ("ML", {"torch", "tensorflow", "keras", "jax", "scikit-learn", "xgboost", "lightgbm", "ultralytics",
            "transformers", "weaver", "torchvision", "timm", "gymnasium", "gym", "stable_baselines3"}),
    ("Science", {"numpy", "scipy", "pandas", "matplotlib", "seaborn", "uproot", "awkward", "vector",
                 "sympy", "numba", "h5py", "tqdm"}),
    ("Geo / CV", {"opencv", "rasterio", "pyproj", "shapely", "gdal", "osgeo", "pillow", "skimage",
                  "geopandas"}),
    ("LLM / web", {"openai", "anthropic", "groq", "httpx", "requests", "playwright", "beautifulsoup4",
                   "ddgs", "google-adk", "flask", "fastapi", "uvicorn", "streamlit"}),
]


def group_of(lib):
    for name, members in LIB_GROUPS:
        if lib in members:
            return name
    return "Tooling"


def hist_rows(y, items, total, unit, color_of, delay0, bar_x=230, bar_w=420, label_w=22):
    """Horizontal histogram: label, bar scaled to the max, value + share on the right."""
    out = ""
    peak = max(v for _, v in items) if items else 1
    for i, (k, v) in enumerate(items):
        f = v / peak
        share = v / total if total else 0
        pct = "<1%" if share < .01 else f"{share * 100:.0f}%"
        lbl = k if len(k) <= label_w else k[:label_w - 1] + "…"
        out += (f'<g class="ln" style="animation-delay:{delay0 + .06 * i:.2f}s">'
                f'<text x="42" y="{y}" class="t"><tspan class="dim">{"⎿  " if i == 0 else "   "}</tspan><tspan class="b">{e(lbl)}</tspan></text>'
                f'<rect x="{bar_x}" y="{y - 11}" width="{bar_w}" height="11" rx="2" fill="#2a2826"/>'
                f'<rect x="{bar_x}" y="{y - 11}" width="{max(3, bar_w * f):.1f}" height="11" rx="2" fill="{color_of(k)}"/>'
                f'<text x="{bar_x + bar_w + 14}" y="{y}" class="t dim">{e(unit(v))}</text>'
                f'<text x="{W - 24}" y="{y}" text-anchor="end" class="t dim">{e(pct)}</text></g>')
        y += 21
    return out, y


def kb(n):
    return f"{n / 1024:,.0f} KB" if n >= 1024 else f"{n} B"


def stack_panel(d):
    langs, libs, lib_files, n_repos = d["langs"], d["libs"], d["lib_files"], d["n_repos"]
    body = prompt(70, "/stack") + tool(110, "Bash", f'gh api "repos/{USER}/*/languages"  # {n_repos} repos')
    y = 132
    items = langs.most_common()
    main = items[:6]
    rest = sum(v for _, v in items[6:])
    if rest:
        main.append(("other", rest))
    rows, y = hist_rows(y, main, sum(langs.values()), kb, lambda k: LANG_COLORS.get(k, "#8b8b8b"), .9)
    body += rows
    if libs:
        y += 22
        body += tool(y, "Grep", f'"^(import|from) " **/*.py **/*.ipynb  # imports per repo', 1.4)
        y += 22
        top = sorted(libs.items(), key=lambda kv: (-kv[1], -lib_files[kv[0]], kv[0]))[:14]
        colors = {"ML": "#d97757", "Science": "#82aaff", "Geo / CV": "#7fb77e", "LLM / web": "#c792ea", "Tooling": "#8b8b8b"}
        rows, y = hist_rows(y, top, n_repos, lambda v: f"{v}/{n_repos} repos",
                            lambda k: colors[group_of(k)], 1.5)
        body += rows
        y += 8
        lx = 42
        body += f'<g class="ln" style="animation-delay:2.3s">'
        for name, c in colors.items():
            body += f'<rect x="{lx + 30}" y="{y + 1}" width="10" height="10" rx="2" fill="{c}"/><text x="{lx + 46}" y="{y + 11}" class="t s dim">{e(name)}</text>'
            lx += 46 + 8 * len(name) + 22
        body += "</g>"
        y += 22
    y += 22
    if langs:
        groups = Counter()
        for k, v in libs.items():
            groups[group_of(k)] += v
        heavy = [g for g, _ in groups.most_common(2) if g != "Tooling"]
        top3 = [k for k, _ in sorted(libs.items(), key=lambda kv: -kv[1])[:3]]
        msg = f"Mostly {items[0][0]} ({items[0][1] * 100 // max(1, sum(langs.values()))}% of code)"
        msg += f", heavy on {' and '.join(heavy)}" if heavy else ""
        msg += f". Most used: {', '.join(top3)}." if top3 else "."
        lines = clip(wrap(msg, 70), 2, 70)
        body += say(y, lines, 2.5)
        y += 22 * (len(lines) - 1)
    return window("claude — /stack", y + 30, body)


def now_panel(d):
    body = prompt(70, "/now") + tool(110, "Bash", f"git log --all-repos --author={USER} -n 5")
    y = 132
    for i, (date, repo, msg) in enumerate(d["commits"][:5]):
        m = msg if len(msg) <= 54 else msg[:53] + "…"
        body += row(y, i, [(42, f'<tspan class="b">{e(repo[:22])}</tspan>', ""), (270, e(m), "dim"),
                           (W - 24, ago(date), "dim\" text-anchor=\"end")], .9 + .08 * i)
        y += 21
    todos = d["todos"][:5]
    if todos:
        y += 22
        body += tool(y, "Update Todos", "", 1.4)
        y += 22
        for i, (_, repo, t) in enumerate(todos):
            line = clip(wrap(t, 58), 1, 58)[0]
            body += row(y, i, [(42, "☐", ""), (86, e(line), "b"), (W - 24, e(repo[:22]), "dim\" text-anchor=\"end")], 1.5 + .1 * i)
            y += 21
    return window("claude — /now", y + 18, body)


def contributions_panel():
    page = fetch(f"https://github.com/users/{USER}/contributions", raw=True).decode("utf-8", "replace")
    cells = re.findall(r'data-date="(\d{4}-\d\d-\d\d)"[^>]*?id="contribution-day-component-(\d+)-(\d+)"[^>]*?data-level="(\d)"', page)
    total = 0
    for m in re.finditer(r'for="contribution-day-component-\d+-\d+"[^>]*>(\d+) contributions?', page):
        total += int(m.group(1))
    pal = ["#2a2826", "#5c3a2e", "#8f4f39", "#c06446", "#e8875f"]
    cs, gap, x0, y0 = 12, 3, 42, 140
    body = prompt(70, "/contributions") + tool(110, "Bash", f"gh api graphql contributionsCollection(login: {USER})")
    rects = "".join(f'<rect x="{x0 + int(c) * (cs + gap)}" y="{y0 + int(r) * (cs + gap)}" width="{cs}" height="{cs}" rx="2" fill="{pal[int(l)]}"><title>{dte}</title></rect>'
                    for dte, r, c, l in cells)
    body += f'<g class="ln" style="animation-delay:.9s">{rects}</g>'
    yb = y0 + 7 * (cs + gap) + 26
    body += f'<g class="ln" style="animation-delay:1.2s"><text x="42" y="{yb}" class="t s dim">less</text>'
    body += "".join(f'<rect x="{82 + i * 16}" y="{yb - 10}" width="12" height="12" rx="2" fill="{c}"/>' for i, c in enumerate(pal))
    body += f'<text x="{166}" y="{yb}" class="t s dim">more</text></g>'
    body += say(yb + 40, [f"{total} contributions in the last year."], 1.6)
    return window("claude — /contributions", yb + 70, body)


def contacts(d):
    user, rows = d["user"], []
    if d["email"]:
        rows.append(("mail", d["email"], f"mailto:{d['email']}"))
    for s in d["socials"]:
        rows.append((s["provider"], re.sub(r"^https?://(www\.)?", "", s["url"]).rstrip("/"), s["url"]))
    if user.get("blog"):
        b = user["blog"] if user["blog"].startswith("http") else "https://" + user["blog"]
        rows.append(("web", re.sub(r"^https?://", "", b).rstrip("/"), b))
    if user.get("twitter_username"):
        rows.append(("x", "@" + user["twitter_username"], f"https://x.com/{user['twitter_username']}"))
    rows.append(("github", f"github.com/{USER}", f"https://github.com/{USER}"))
    if user.get("location"):
        rows.append(("based", user["location"], None))
    return rows


def contact_panel(d):
    body = prompt(70, "/contact") + tool(110, "Bash", f"gh api users/{USER}/social_accounts")
    y = 132
    for i, (k, v, _) in enumerate(contacts(d)):
        body += row(y, i, [(42, f'<tspan class="b">{e(k)}</tspan>', ""), (210, e(v), "or")], .9 + .1 * i)
        y += 21
    y += 22
    if d["user"].get("hireable"):
        body += say(y, ["Open to work."], 1.5)
        y += 22
    return window("claude — /contact", y + 18, body)


# Buy Me a Coffee URL (a SUPPORT_URL env var / repo variable overrides it).
SUPPORT_URL = os.environ.get("SUPPORT_URL") or "https://buymeacoffee.com/avinashistoocool"

SUPPORT_WHY = [
    "I build things to understand them: models that read particle jets,",
    "maps of ice on the Moon, rescue drone swarms, and small tools that",
    "help students reach researchers. All of it is open source, and all",
    "of it runs on a student laptop.",
]
SUPPORT_USES = [
    "GPU hours for bigger training runs",
    "datasets, storage and cloud compute",
    "parts for robotics and drone builds",
    "keeping my tools free for other students",
]


def support_panel(d):
    n = len(d["projects"])
    body = prompt(70, "/support") + tool(110, "Read", "WHY.md")
    y = 132
    for i, l in enumerate(SUPPORT_WHY):
        body += row(y, i, [(42, e(l), "")], .9 + .08 * i)
        y += 21
    y += 22
    body += tool(y, "Read", "where-it-goes.md", 1.4)
    y += 22
    for i, l in enumerate(SUPPORT_USES):
        body += row(y, i, [(42, '<tspan class="or">$</tspan>', ""), (70, e(l), "b")], 1.5 + .08 * i)
        y += 21
    y += 22
    lines = [f"{n} public projects so far, each one shared as it's built.",
             "A coffee goes straight into the next experiment. Thank you."]
    body += say(y, lines, 2.1)
    y += 22 * len(lines) + 10
    link = re.sub(r"^https?://(www\.)?", "", SUPPORT_URL).rstrip("/") if SUPPORT_URL else "buy me a coffee: link coming soon"
    body += (f'<g class="ln" style="animation-delay:2.6s"><text x="42" y="{y}" class="t or">'
             f'↓ {e(link)}</text></g>')
    return window("claude — /support", y + 28, body)


def readme_md(d):
    cards = ""
    ps = d["projects"]
    for i in range(0, len(ps), 2):
        cards += "".join(f'<a href="{p["html_url"]}"><img src="assets/cards/{p["name"]}.svg" width="49%" alt="{e(p["name"])}: {e(p["text"][:120])}"/></a>\n'
                         for p in ps[i:i + 2])
    links = " · ".join(f"[{k}]({u})" for k, _, u in contacts(d) if u)
    return f"""<img src="assets/terminal.svg" alt="Claude Code style terminal: who is {e(first_name(d['user']))}" width="100%"/>

<details>
<summary><code>/projects</code> &nbsp;what I've built</summary>
<br>
<img src="assets/projects.svg" width="100%" alt="project list"/>

{cards}
</details>

<details>
<summary><code>/stack</code> &nbsp;what I build with</summary>
<br>
<img src="assets/stack.svg" width="100%" alt="languages and libraries"/>
</details>

<details>
<summary><code>/now</code> &nbsp;what I'm up to</summary>
<br>
<img src="assets/now.svg" width="100%" alt="recent commits and open todos"/>
</details>

<details>
<summary><code>/contributions</code> &nbsp;the green squares, but orange</summary>
<br>
<img src="assets/contributions.svg" width="100%" alt="contribution graph"/>
</details>

<details>
<summary><code>/contact</code> &nbsp;get in touch</summary>
<br>
<img src="assets/contact.svg" width="100%" alt="contact"/>

{links}
</details>

<details>
<summary><code>/support</code> &nbsp;buy me a coffee</summary>
<br>
{f'<a href="{SUPPORT_URL}">' if SUPPORT_URL else ''}<img src="assets/support.svg" width="100%" alt="why I build, and where support goes"/>{'</a>' if SUPPORT_URL else ''}
{f'{chr(10)}[Buy me a coffee]({SUPPORT_URL})' if SUPPORT_URL else ''}
</details>

<sub>Generated from my repositories by <code>scripts/build.py</code>, refreshed by GitHub Actions.</sub>
"""


def main():
    d = collect()
    OUT.mkdir(exist_ok=True)
    cards_dir = OUT / "cards"
    cards_dir.mkdir(exist_ok=True)
    for f in cards_dir.glob("*.svg"):
        f.unlink()
    files = {"terminal.svg": hero(d), "projects.svg": projects_panel(d), "stack.svg": stack_panel(d),
             "now.svg": now_panel(d), "contributions.svg": contributions_panel(), "contact.svg": contact_panel(d),
             "support.svg": support_panel(d)}
    for p in d["projects"]:
        files[f"cards/{p['name']}.svg"] = card(p)
    for k, v in files.items():
        (OUT / k).write_text(v, encoding="utf-8")
    (ROOT / "README.md").write_text(readme_md(d), encoding="utf-8", newline="\n")
    print(f"wrote README.md and {len(files)} svgs for {len(d['projects'])} projects")


if __name__ == "__main__":
    main()

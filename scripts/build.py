"""Render the Claude Code style profile panels in assets/ from live GitHub data.

    python scripts/build.py            # uses GITHUB_TOKEN if set
"""
import datetime as dt
import html
import io
import json
import os
import re
import urllib.request
from pathlib import Path

from PIL import Image

USER = "Avinashistoocool"
OUT = Path(__file__).resolve().parent.parent / "assets"
W = 880
FONT = "'JetBrains Mono','SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"

# What each repo actually does, written from reading the code. Order = display order.
PROJECTS = [
    ("Gated-Dual-Stream-Particle-Transformer",
     "Pairwise particle physics as multiplicative Q/K gates plus an attention bias. "
     "84.8% acc, 0.985 AUC on JetClass-10M (ParT: 85.0%)."),
    ("ISRO-BAH-26",
     "Lunar ice probability map from Chandrayaan-2 DFSAR radar, slope cost map from "
     "LOLA DEM, A* rover path into Faustini crater."),
    ("Agnostos-v2",
     "Research outreach CLI: OpenAlex discovery, email verification, LLM drafts, paced "
     "sends in local hours, reply tracking."),
    ("asclepius",
     "Rescue drone swarm: polygon to search lanes, min-makespan fleet balancing, "
     "Hungarian dispatch, YOLO-World detection."),
    ("eraksha-hackathon",
     "Home-network anomaly detector: scapy sniffing, ARP + nmap scans, port-risk "
     "scoring, SQLite store, Flask dashboard."),
    ("Jet-Class-Particle-Visualizer",
     "JetClass ROOT files to (eta, phi) particle clouds, class heatmaps, energy "
     "spectra and radial profiles."),
    ("Object-tracking-with-color",
     "HSV colour tracking in OpenCV with trajectory plots and annotated video output."),
]

TODOS = [
    (True, "match ParT on JetClass-10M with gated attention"),
    (True, "ship an LLM outreach CLI people actually star"),
    (False, "20-epoch GDS-ParT run"),
    (False, "Mixture-of-Experts jet tagger"),
    (False, "lunar ice map to rover traverse, end to end"),
]

LANG_COLORS = {"Python": "#3572A5", "Shell": "#89e051", "HTML": "#e34c26",
               "Jupyter Notebook": "#DA5B0B", "C++": "#f34b7d", "TypeScript": "#3178c6"}


def gh(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": USER})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return json.loads(data) if url.startswith("https://api.") else data


def ago(iso):
    d = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
    if d <= 0:
        return "today"
    if d == 1:
        return "1d ago"
    if d < 30:
        return f"{d}d ago"
    if d < 365:
        return f"{d // 30}mo ago"
    return f"{d // 365}y ago"


def e(s):
    return html.escape(str(s))


def style(extra=""):
    return f"""<style>
.t{{font-family:{FONT};font-size:14px;fill:#e6e6e6;white-space:pre}}
.s{{font-size:12.5px}}.dim{{fill:#8b8b8b}}.or{{fill:#d97757}}.b{{font-weight:700}}
.gr{{fill:#7fb77e}}.strike{{text-decoration:line-through}}
.ln{{opacity:0;animation:show .01s forwards}}@keyframes show{{to{{opacity:1}}}}
.cur{{animation:blink 1.1s steps(1) infinite}}@keyframes blink{{50%{{opacity:0}}}}
{extra}</style>"""


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


def tool(y, name, arg, delay=0.6):
    return (f'<g class="ln" style="animation-delay:{delay}s"><circle cx="29" cy="{y - 5}" r="4" fill="#7fb77e"/>'
            f'<text x="42" y="{y}" class="t"><tspan class="b">{e(name)}</tspan>{"(" + e(arg) + ")" if arg else ""}</text></g>')


def say(y, lines, delay):
    out = [f'<g class="ln" style="animation-delay:{delay}s"><circle cx="29" cy="{y - 5}" r="4" fill="#e6e6e6"/>']
    for i, l in enumerate(lines):
        out.append(f'<text x="42" y="{y + 22 * i}" class="t b">{e(l)}</text>')
    return "".join(out) + "</g>"


def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        if len(cur) + len(w_) + (1 if cur else 0) > width:
            lines.append(cur)
            cur = w_
        else:
            cur = f"{cur} {w_}" if cur else w_
    if cur:
        lines.append(cur)
    return lines


def avatar(url, n=24, s=3):
    im = Image.open(io.BytesIO(gh(url + "&s=460"))).convert("RGB")
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


def hero(user, repos, langs_total):
    stars = sum(r["stargazers_count"] for r in repos)
    since = user["created_at"][:4]
    recent = sorted([r for r in repos if r["name"] != USER], key=lambda r: r["pushed_at"], reverse=True)[:2]
    av, size = avatar(user["avatar_url"])
    lx = 230
    bio = user.get("bio") or ""
    top = stars and max(repos, key=lambda r: r["stargazers_count"])
    body = f"""
<rect x="24" y="62" width="{W - 48}" height="166" rx="8" fill="none" stroke="#d97757" stroke-width="1.2"/>
<rect x="38" y="52" width="104" height="20" fill="#1b1a19"/>
<text x="44" y="67" class="t or">Claude Code</text>
<text x="{lx}" y="96" text-anchor="middle" class="t b">Welcome back, Avinash!</text>
<g transform="translate({lx - size / 2},108)" shape-rendering="crispEdges">{av}</g>
<text x="{lx}" y="200" text-anchor="middle" class="t dim">{e(bio)}</text>
<text x="{lx}" y="218" text-anchor="middle" class="t s dim">~/iit-delhi/physics</text>
<line x1="430" y1="80" x2="430" y2="212" stroke="#3a3836"/>
<text x="452" y="96" class="t or b">Recent activity</text>"""
    y = 118
    for r in recent:
        name = r["name"] if len(r["name"]) <= 26 else r["name"][:25] + "…"
        body += f'<text x="452" y="{y}" class="t"><tspan class="b">{e(name)}</tspan><tspan class="dim"> · pushed {ago(r["pushed_at"])}</tspan></text>'
        y += 20
    body += f"""
<line x1="452" y1="170" x2="{W - 44}" y2="170" stroke="#3a3836"/>
<text x="452" y="192" class="t or b">Stats</text>
<text x="452" y="212" class="t">★ {stars} stars · {user["public_repos"]} public repos · since {since}</text>
{prompt(270, "who is avinash?", .4)}
{tool(311, "Bash", f"gh api users/{USER}", 1)}
<g class="ln" style="animation-delay:1s"><text x="42" y="333" class="t dim">⎿  "{e(bio)}" · {user["public_repos"]} public repos · on GitHub since {since}</text></g>
{say(376, ["Physics student at IIT Delhi who builds ML for physics:",
           "a gated dual-stream transformer for jet tagging, lunar ice",
           "maps from Chandrayaan-2 radar and rescue drone swarms.",
           f"Best known for {top['name']} (★{top['stargazers_count']})." if top else "Next up: Mixture-of-Experts."], 1.8)}
<g class="ln" style="animation-delay:2.6s"><text x="42" y="484" class="t or">↓ pick a prompt below to keep going</text></g>
<g class="ln" style="animation-delay:3s">
<line x1="24" y1="516" x2="{W - 24}" y2="516" stroke="#4a4846"/>
<text x="24" y="540" class="t dim">&gt;</text>
<rect x="42" y="527" width="9" height="17" fill="#e6e6e6" class="cur"/>
<line x1="24" y1="554" x2="{W - 24}" y2="554" stroke="#4a4846"/>
<text x="24" y="578" class="t dim">? for shortcuts</text>
<text x="{W - 24}" y="578" text-anchor="end" class="t s or">✻ open to research collaborations</text>
</g>"""
    return window(f"claude — ~/{USER}", 600, body)


def projects_header(repos_by_name):
    rows = [n for n, _ in PROJECTS if n in repos_by_name]
    body = prompt(70, "/projects") + tool(110, "Bash", f"gh repo list {USER} --source")
    y = 132
    for i, n in enumerate(rows):
        r = repos_by_name[n]
        pre = "⎿  " if i == 0 else "   "
        body += (f'<g class="ln" style="animation-delay:{.9 + .08 * i:.2f}s"><text x="42" y="{y}" class="t">'
                 f'<tspan class="dim">{pre}</tspan><tspan class="b">{e(n)}</tspan></text>'
                 f'<text x="430" y="{y}" class="t or">★ {r["stargazers_count"]}</text>'
                 f'<text x="500" y="{y}" class="t dim">{e(r["language"] or "-")}</text>'
                 f'<text x="620" y="{y}" class="t dim">pushed {ago(r["pushed_at"])}</text></g>')
        y += 21
    return window("claude — /projects", y + 18, body)


def card(name, desc, r):
    w, h = 432, 150
    lang = r["language"] or "-"
    lines = wrap(desc, 44)[:3]
    if len(wrap(desc, 44)) > 3:
        lines[-1] = lines[-1][:41].rstrip() + "…"
    t = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{e(name)}">
{style()}
<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="8" fill="#1b1a19" stroke="#3a3836"/>
<circle cx="22" cy="27" r="4" fill="#7fb77e"/>
<text x="34" y="32" class="t b">{e(name if len(name) <= 36 else name[:35] + "…")}</text>
<text x="{w - 18}" y="32" text-anchor="end" class="t or">★ {r["stargazers_count"]}</text>"""
    for i, l in enumerate(lines):
        pre = "⎿ " if i == 0 else "  "
        t += f'<text x="34" y="{56 + 20 * i}" class="t"><tspan class="dim">{pre}</tspan>{e(l)}</text>'
    t += f"""<circle cx="22" cy="{h - 22}" r="5" fill="{LANG_COLORS.get(lang, '#8b8b8b')}"/>
<text x="34" y="{h - 17}" class="t s dim">{e(lang)} · pushed {ago(r["pushed_at"])}</text>
<text x="{w - 18}" y="{h - 17}" text-anchor="end" class="t s or">open ↗</text>
</svg>"""
    return t


def stack(langs):
    total = sum(langs.values()) or 1
    items = sorted(langs.items(), key=lambda kv: -kv[1])
    main, other = items[:5], sum(v for _, v in items[5:])
    if other:
        main.append(("other", other))
    body = prompt(70, "/stack") + tool(110, "Bash", f'gh api "repos/{USER}/*/languages"')
    y = 132
    for i, (k, v) in enumerate(main):
        frac = v / total
        pct = "<1%" if frac < .01 else f"{frac * 100:.0f}%"
        bw = max(3, 340 * frac)
        pre = "⎿  " if i == 0 else "   "
        body += (f'<g class="ln" style="animation-delay:{.9 + .1 * i:.1f}s"><text x="42" y="{y}" class="t"><tspan class="dim">{pre}</tspan><tspan class="b">{e(k)}</tspan></text>'
                 f'<rect x="210" y="{y - 11}" width="{bw:.1f}" height="11" rx="2" fill="{LANG_COLORS.get(k, "#8b8b8b")}"/>'
                 f'<text x="620" y="{y}" class="t dim">{pct}</text></g>')
        y += 21
    y += 22
    body += tool(y, "Read", "requirements.txt", 1.6)
    libs = [("ML", "PyTorch · NumPy · SciPy · ultralytics"),
            ("Physics", "uproot · awkward · JetClass"),
            ("Geo", "rasterio · GDAL · pyproj · shapely"),
            ("CV", "OpenCV · matplotlib"),
            ("Systems", "Flask · SQLite · scapy · LLM APIs · SMTP")]
    y += 22
    for i, (k, v) in enumerate(libs):
        pre = "⎿  " if i == 0 else "   "
        body += (f'<g class="ln" style="animation-delay:{1.7 + .08 * i:.2f}s"><text x="42" y="{y}" class="t"><tspan class="dim">{pre}</tspan><tspan class="b">{e(k)}</tspan></text>'
                 f'<text x="210" y="{y}" class="t dim">{e(v)}</text></g>')
        y += 21
    y += 22
    top = main[0][0]
    body += say(y, [f"Almost all {top}: PyTorch for models, the scientific stack for data."], 2.4)
    return window("claude — /stack", y + 30, body)


def now(repos):
    rec = sorted([r for r in repos if r["name"] != USER], key=lambda r: r["pushed_at"], reverse=True)[:3]
    body = prompt(70, "/now") + tool(110, "Bash", f"gh repo list {USER} --json name,pushedAt")
    y = 132
    for i, r in enumerate(rec):
        pre = "⎿  " if i == 0 else "   "
        body += (f'<g class="ln" style="animation-delay:{.9 + .1 * i:.1f}s"><text x="42" y="{y}" class="t"><tspan class="dim">{pre}</tspan><tspan class="b">{e(r["name"])}</tspan></text>'
                 f'<text x="460" y="{y}" class="t dim">pushed {ago(r["pushed_at"])}</text></g>')
        y += 21
    y += 22
    body += tool(y, "Update Todos", "", 1.4)
    y += 22
    for i, (done, txt) in enumerate(TODOS):
        pre = "⎿  " if i == 0 else "   "
        box = '<tspan class="gr">☒</tspan>' if done else "☐"
        cls = "t dim strike" if done else "t b"
        body += (f'<g class="ln" style="animation-delay:{1.5 + .1 * i:.1f}s"><text x="42" y="{y}" class="t"><tspan class="dim">{pre}</tspan>{box}</text>'
                 f'<text x="86" y="{y}" class="{cls}">{e(txt)}</text></g>')
        y += 21
    return window("claude — /now", y + 18, body)


def contributions():
    page = gh(f"https://github.com/users/{USER}/contributions").decode("utf-8", "replace")
    cells = re.findall(r'data-date="(\d{4}-\d\d-\d\d)"[^>]*?id="contribution-day-component-(\d+)-(\d+)"[^>]*?data-level="(\d)"', page)
    counts = {}
    for m in re.finditer(r'for="contribution-day-component-(\d+)-(\d+)"[^>]*>([^<]*)</tool-tip>', page):
        n = re.match(r"(\d+) contribution", m.group(3))
        counts[(m.group(1), m.group(2))] = int(n.group(1)) if n else 0
    total = sum(counts.values())
    pal = ["#2a2826", "#5c3a2e", "#8f4f39", "#c06446", "#e8875f"]
    cs, gap, x0, y0 = 12, 3, 42, 140
    body = prompt(70, "/contributions") + tool(110, "Bash", f"gh api graphql -f user={USER} contributions")
    rects = []
    for date, row, col, lvl in cells:
        x = x0 + int(col) * (cs + gap)
        y = y0 + int(row) * (cs + gap)
        rects.append(f'<rect x="{x}" y="{y}" width="{cs}" height="{cs}" rx="2" fill="{pal[int(lvl)]}"><title>{date}</title></rect>')
    body += f'<g class="ln" style="animation-delay:.9s">{"".join(rects)}</g>'
    yb = y0 + 7 * (cs + gap) + 26
    body += f'<g class="ln" style="animation-delay:1.2s"><text x="42" y="{yb}" class="t s dim">less</text>'
    for i, c in enumerate(pal):
        body += f'<rect x="{82 + i * 16}" y="{yb - 10}" width="12" height="12" rx="2" fill="{c}"/>'
    body += f'<text x="{82 + 5 * 16 + 4}" y="{yb}" class="t s dim">more</text></g>'
    body += say(yb + 40, [f"{total} contributions in the last year."], 1.6)
    return window("claude — /contributions", yb + 70, body)


def contact():
    body = prompt(70, "/contact") + tool(110, "Read", "~/.contact")
    rows = [("mail", "ph1251520@physics.iitd.ac.in"), ("linkedin", "linkedin.com/in/avinashistoocool"),
            ("github", f"github.com/{USER}"), ("based", "IIT Delhi, India")]
    y = 132
    for i, (k, v) in enumerate(rows):
        pre = "⎿  " if i == 0 else "   "
        body += (f'<g class="ln" style="animation-delay:{.9 + .1 * i:.1f}s"><text x="42" y="{y}" class="t"><tspan class="dim">{pre}</tspan><tspan class="b">{k}</tspan></text>'
                 f'<text x="210" y="{y}" class="t or">{e(v)}</text></g>')
        y += 21
    y += 22
    body += say(y, ["Open to research internships and collaborations in", "ML for physics, remote sensing and robotics."], 1.5)
    return window("claude — /contact", y + 52, body)


def main():
    user = gh(f"https://api.github.com/users/{USER}")
    repos = [r for r in gh(f"https://api.github.com/users/{USER}/repos?per_page=100") if not r["fork"]]
    by_name = {r["name"]: r for r in repos}
    langs = {}
    for r in repos:
        for k, v in gh(r["languages_url"]).items():
            langs[k] = langs.get(k, 0) + v
    OUT.mkdir(exist_ok=True)
    (OUT / "cards").mkdir(exist_ok=True)
    files = {"terminal.svg": hero(user, repos, langs), "projects.svg": projects_header(by_name),
             "stack.svg": stack(langs), "now.svg": now(repos), "contributions.svg": contributions(),
             "contact.svg": contact()}
    for n, desc in PROJECTS:
        if n in by_name:
            files[f"cards/{n}.svg"] = card(n, desc, by_name[n])
    for k, v in files.items():
        (OUT / k).write_text(v, encoding="utf-8")
    print("wrote", len(files), "files")


if __name__ == "__main__":
    main()

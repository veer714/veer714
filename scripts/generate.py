#!/usr/bin/env python3
"""Builds the animated SVG cards for the profile README from live GitHub data.

Standard library only. Run by .github/workflows/update-profile.yml, or locally:
    python scripts/generate.py            # live data (set GITHUB_TOKEN for higher limits)
    python scripts/generate.py --offline  # uses the snapshot in profile.json
"""
import base64, datetime as dt, html, json, math, os, random, re, sys, textwrap
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
os.makedirs(ASSETS, exist_ok=True)
CFG = json.load(open(os.path.join(ROOT, "profile.json"), encoding="utf-8"))
USER = CFG["username"]
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OFFLINE = "--offline" in sys.argv
NOW = dt.datetime.now(dt.timezone.utc)

SANS = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono',monospace"
GOLD, TEXT, MUTED = "#f5c542", "#e8ecf8", "#8d97b8"
LANG_COLORS = {"Jupyter Notebook": "#e8590c", "TypeScript": "#3178c6", "Java": "#c47f17",
               "JavaScript": "#f1e05a", "Python": "#3776ab", "HTML": "#e34c26", "CSS": "#7a52b3",
               "C++": "#f34b7d", "C": "#8a8a8a", "Shell": "#89e051"}
LANG_LABELS = {"Jupyter Notebook": "NB", "TypeScript": "TS", "Java": "J", "JavaScript": "JS",
               "Python": "PY", "HTML": "H", "CSS": "C", "C++": "C+", "Shell": "SH"}


def esc(s):
    return html.escape(str(s), quote=True)


# ----------------------------------------------------------------- data
def http(url, data=None, binary=False):
    headers = {"User-Agent": "profile-readme-bot", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        ctype = r.headers.get("Content-Type", "")
    return (body, ctype) if binary else json.loads(body)


def streaks(days):
    """days: [(date_str, count)] ascending. Returns (current, longest)."""
    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    cur, i = 0, len(days) - 1
    if i >= 0 and days[i][1] == 0:
        i -= 1  # today not done yet: streak is still alive until midnight
    while i >= 0 and days[i][1] > 0:
        cur += 1
        i -= 1
    return cur, longest


def collect():
    snap = CFG["snapshot"]
    d = {"live": False, "followers": snap["followers"], "following": snap["following"],
         "public_repos": snap["public_repos"], "stars": snap["stars"],
         "languages": list(snap["languages"].items()), "avatar": None, "days": None,
         "current_streak": snap["current_streak"], "longest_streak": snap["longest_streak"],
         "total": snap["total_contributions"], "pushes": [], "repos": {}}
    for name in CFG["featured"]:
        st = snap["repo_stats"].get(name, {})
        d["repos"][name] = {"name": name, "stars": st.get("stars", 0), "forks": st.get("forks", 0),
                            "language": st.get("language", ""), "description": "", "pushed_at": None}
    if OFFLINE:
        return d
    try:
        u = http(f"https://api.github.com/users/{USER}")
        d.update(followers=u["followers"], following=u["following"], public_repos=u["public_repos"])
        try:
            body, ctype = http(u["avatar_url"] + ("&" if "?" in u["avatar_url"] else "?") + "s=240", binary=True)
            d["avatar"] = f"data:{ctype.split(';')[0] or 'image/png'};base64," + base64.b64encode(body).decode()
        except Exception as e:
            print("avatar failed:", e)
        repos = [r for r in http(f"https://api.github.com/users/{USER}/repos?per_page=100&sort=pushed")
                 if not r["fork"]]
        d["stars"] = sum(r["stargazers_count"] for r in repos)
        bytes_by_lang = {}
        for r in repos:
            try:
                for k, v in http(r["languages_url"]).items():
                    bytes_by_lang[k] = bytes_by_lang.get(k, 0) + v
            except Exception as e:
                print("languages failed:", r["name"], e)
        tot = sum(bytes_by_lang.values())
        if tot:
            top = sorted(bytes_by_lang.items(), key=lambda kv: -kv[1])[:5]
            d["languages"] = [(k, round(100 * v / tot)) for k, v in top]
        for r in repos:
            if r["name"] in d["repos"]:
                d["repos"][r["name"]].update(stars=r["stargazers_count"], forks=r["forks_count"],
                                             language=r["language"] or "", pushed_at=r["pushed_at"],
                                             description=r["description"] or "")
        d["live"] = True
    except Exception as e:
        print("REST failed, using snapshot:", e)
        return d
    try:
        q = ('{user(login:"%s"){contributionsCollection{contributionCalendar{totalContributions '
             'weeks{contributionDays{date contributionCount}}}}}}' % USER)
        cal = http("https://api.github.com/graphql", data=json.dumps({"query": q}).encode())
        cal = cal["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        days = [(x["date"], x["contributionCount"]) for w in cal["weeks"] for x in w["contributionDays"]]
        d["days"] = days
        d["total"] = cal["totalContributions"]
        d["current_streak"], d["longest_streak"] = streaks(days)
    except Exception as e:
        print("GraphQL failed (heatmap stays in placeholder mode):", e)
    try:
        ev = http(f"https://api.github.com/users/{USER}/events/public?per_page=60")
        d["pushes"] = [(e["repo"]["name"], e["created_at"]) for e in ev if e["type"] == "PushEvent"][:5]
    except Exception as e:
        print("events failed:", e)
    return d


def ago(iso):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    s = int((NOW - t).total_seconds())
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


# ----------------------------------------------------------------- svg helpers
def svg_open(w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
            f'<title>{esc(title)}</title>'
            f'<clipPath id="cc"><rect width="{w}" height="{h}" rx="24"/></clipPath><g clip-path="url(#cc)">')


DEFS = f'''<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b1030"/><stop offset="1" stop-color="#050814"/></linearGradient>
<linearGradient id="bgdeep" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#050a1c"/><stop offset="1" stop-color="#02040a"/></linearGradient>
<radialGradient id="sunGlow"><stop offset="0" stop-color="#ffd36a" stop-opacity=".85"/><stop offset=".35" stop-color="#f5a623" stop-opacity=".35"/><stop offset="1" stop-color="#f5a623" stop-opacity="0"/></radialGradient>
<radialGradient id="shade" cx=".35" cy=".3" r=".85"><stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>
</defs>'''


def starfield(w, h, n, seed, twinkle=55):
    r = random.Random(seed)
    out = []
    for i in range(n):
        x, y = r.uniform(0, w), r.uniform(0, h)
        rad, op = r.choice([.6, .8, 1, 1.2, 1.6]), r.uniform(.3, .95)
        if i < twinkle:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad}" fill="#fff" opacity="{op:.2f}">'
                       f'<animate attributeName="opacity" values="{op:.2f};0.08;{op:.2f}" dur="{r.uniform(2, 6):.1f}s" '
                       f'begin="-{r.uniform(0, 6):.1f}s" repeatCount="indefinite"/></circle>')
        else:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad}" fill="#fff" opacity="{op:.2f}"/>')
    return "".join(out)


def card(w, h, fill="bg", radius=24):
    return (f'<rect width="{w}" height="{h}" rx="{radius}" fill="url(#{fill})"/>'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{radius}" fill="none" stroke="#ffffff" stroke-opacity=".07"/>')


def ellipse_path(cx, cy, rx, ry):
    return f"M{cx - rx:.1f},{cy:.1f} A{rx:.1f},{ry:.1f} 0 1,1 {cx + rx:.1f},{cy:.1f} A{rx:.1f},{ry:.1f} 0 1,1 {cx - rx:.1f},{cy:.1f}"


def circle_path(r):
    return f"M{r},0 A{r},{r} 0 1,1 {-r},0 A{r},{r} 0 1,1 {r},0"


def lang_color(name):
    return LANG_COLORS.get(name, "#7b8cff")


def lang_label(name):
    return LANG_LABELS.get(name, name[:2].upper())


def sun(cx, cy, r, d, clip_id):
    s = (f'<circle cx="{cx}" cy="{cy}" r="{r * 2.6:.0f}" fill="url(#sunGlow)">'
         f'<animate attributeName="r" values="{r * 2.3:.0f};{r * 2.9:.0f};{r * 2.3:.0f}" dur="5s" repeatCount="indefinite"/></circle>'
         f'<circle cx="{cx}" cy="{cy}" r="{r + 9}" fill="none" stroke="{GOLD}" stroke-opacity=".55" stroke-width="1.5" stroke-dasharray="3 9">'
         f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" dur="28s" repeatCount="indefinite"/></circle>')
    if d["avatar"]:
        s += (f'<clipPath id="{clip_id}"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'
              f'<image xlink:href="{d["avatar"]}" x="{cx - r}" y="{cy - r}" width="{2 * r}" height="{2 * r}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#{clip_id})"/>')
    else:
        ini = "".join(w[0] for w in CFG["name"].split())[:2].upper()
        s += (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#1b2350"/>'
              f'<text x="{cx}" y="{cy}" dy=".35em" text-anchor="middle" font-family="{SANS}" font-weight="700" '
              f'font-size="{r * .8:.0f}" fill="{GOLD}">{ini}</text>')
    return s + f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{GOLD}" stroke-width="3"/>'


# ----------------------------------------------------------------- cards
def build_solar(d):
    W, H, cx, cy = 960, 560, 480, 290
    langs = d["languages"][:5]
    out = [svg_open(W, H, f"Solar system of @{USER}: planets are languages, moons are repositories"), DEFS,
           card(W, H, "bgdeep"), starfield(W, H, 190, 7)]
    radii = [285, 325, 365, 400, 428]
    fracs = [.12, .62, .30, .85, .45]
    for rx in radii[:len(langs)]:
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{rx * .4:.1f}" fill="none" stroke="#7886b8" '
                   f'stroke-opacity=".22" stroke-dasharray="2 7"/>')
    # moons (repos) grouped by the planet matching their language
    moons_by_planet = {i: [] for i in range(len(langs))}
    names = [l for l, _ in langs]
    for repo in d["repos"].values():
        idx = names.index(repo["language"]) if repo["language"] in names else 0
        moons_by_planet[idx].append(repo)
    for i, (name, pct) in enumerate(langs):
        rx, ry = radii[i], radii[i] * .4
        r = 13 + math.sqrt(pct) * 1.9
        dur = 26 + i * 14
        col = lang_color(name)
        g = [f'<g><animateMotion dur="{dur}s" begin="-{dur * fracs[i]:.1f}s" repeatCount="indefinite" path="{ellipse_path(cx, cy, rx, ry)}"/>',
             f'<circle r="{r * 1.9:.1f}" fill="{col}" opacity=".16"/>']
        if i == 0:
            g.append(f'<ellipse rx="{r * 1.75:.1f}" ry="{r * .45:.1f}" fill="none" stroke="{col}" stroke-width="2" '
                     f'stroke-opacity=".75" transform="rotate(-18)"/>')
        g.append(f'<circle r="{r:.1f}" fill="{col}"/><circle r="{r:.1f}" fill="url(#shade)"/>')
        g.append(f'<text y=".35em" text-anchor="middle" font-family="{SANS}" font-weight="700" font-size="{r * .62:.0f}" '
                 f'fill="{"#141414" if col == "#f1e05a" else "#fff"}">{lang_label(name)}</text>')
        for j, repo in enumerate(moons_by_planet[i]):
            mr, mdur = r + 16 + j * 9, 5 + j * 2.5
            g.append(f'<g><animateMotion dur="{mdur}s" begin="-{j * 1.3}s" repeatCount="indefinite" path="{circle_path(mr)}"/>'
                     f'<circle r="3.2" fill="#fff"/></g>')
        g.append(f'<text y="{r + (30 if moons_by_planet[i] else 16):.0f}" text-anchor="middle" font-family="{MONO}" '
                 f'font-weight="700" font-size="11" fill="#fff">{esc(name)} {pct}%</text></g>')
        out.append("".join(g))
    # comet = most recent push
    label = d["pushes"][0][0].split("/")[-1] if d["pushes"] else "latest push"
    out.append(f'<g opacity="0"><animateTransform attributeName="transform" type="translate" '
               f'values="900,95;200,350" dur="9s" begin="2s" repeatCount="indefinite"/>'
               f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.12;.8;1" dur="9s" begin="2s" repeatCount="indefinite"/>'
               f'<line x1="0" y1="0" x2="150" y2="-62" stroke="#dfe4ff" stroke-width="2" stroke-linecap="round" stroke-opacity=".8"/>'
               f'<circle r="4.5" fill="#fff"/>'
               f'<text x="12" y="-10" font-family="{MONO}" font-size="11" font-weight="700" fill="#fff">{esc(label)}</text></g>')
    out.append(sun(cx, cy, 46, d, "sunClip"))
    out.append(f'<text x="50" y="52" font-family="{MONO}" font-size="13" font-weight="700" letter-spacing="3" fill="#cfd6ee">'
               f'SOLAR SYSTEM · @{USER.upper()}</text>')
    out.append(f'<text x="910" y="52" text-anchor="end" font-family="{MONO}" font-size="12" fill="{MUTED}">'
               f'{d["followers"]} followers · {d["public_repos"]} repos · ★ {d["stars"]}</text>')
    out.append(f'<text x="50" y="{H - 30}" font-family="{MONO}" font-size="11" fill="#6f7aa0">'
               f'{"synced " + NOW.strftime("%Y-%m-%d") + " UTC" if d["live"] else "snapshot"}</text>')
    out.append(f'<text x="910" y="{H - 30}" text-anchor="end" font-family="{MONO}" font-size="11" fill="#cfd6ee">'
               f'planets = languages · moons = repos · comets = recent pushes</text></g></svg>')
    return "".join(out)


def build_profile(d):
    W, H = 960, 300
    lines = CFG["rotating"]
    n, per = len(lines), 3.2
    out = [svg_open(W, H, f"{CFG['name']}, {lines[0]}"), DEFS, card(W, H), starfield(W, H, 90, 11, 30)]
    out.append(f'<text x="60" y="64" font-family="{MONO}" font-size="13" font-weight="700" letter-spacing="3" fill="{GOLD}">THE SUN</text>')
    out.append(f'<text x="60" y="126" font-family="{SANS}" font-size="46" font-weight="800" fill="{TEXT}">{esc(CFG["name"])}'
               f'<animate attributeName="opacity" from="0" to="1" dur="1.2s" fill="freeze"/></text>')
    for i, line in enumerate(lines):
        a, b = i / n, (i + 1) / n
        kt = f"0;{a:.4f};{min(a + .02, b):.4f};{max(b - .02, a):.4f};{b:.4f};1"
        out.append(f'<text x="60" y="176" font-family="{MONO}" font-size="20" fill="#aab3d1" opacity="0">'
                   f'<tspan fill="{GOLD}">&gt; </tspan>{esc(line)}'
                   f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{kt}" dur="{n * per}s" repeatCount="indefinite"/></text>')
    out.append(f'<text x="60" y="226" font-family="{SANS}" font-size="15" fill="{MUTED}">{esc(CFG["education"])}</text>')
    out.append(f'<text x="60" y="256" font-family="{MONO}" font-size="14" fill="#9aa5c8">'
               f'{d["followers"]} followers  ·  {d["following"]} following  ·  {d["public_repos"]} repositories  ·  ★ {d["stars"]} stars</text>')
    out.append(sun(810, 150, 72, d, "profClip"))
    return "".join(out) + "</g></svg>"


def wrap(s, width):
    return textwrap.wrap(s, width)[:2] or [""]


def build_moon(i, repo):
    W, H = 440, 220
    col = lang_color(repo["language"])
    out = [svg_open(W, H, f"Moon {i}: {repo['name']}"), DEFS, card(W, H, radius=20), starfield(W, H, 40, 20 + i, 14)]
    out.append(f'<text x="28" y="42" font-family="{MONO}" font-size="12" font-weight="700" letter-spacing="3" fill="{GOLD}">MOON {i:02d}</text>')
    out.append(f'<text x="28" y="82" font-family="{MONO}" font-size="19" font-weight="700" fill="{TEXT}">{esc(repo["name"])}</text>')
    desc = repo["description"] or "A project in progress."
    for k, ln in enumerate(wrap(desc, 46)):
        out.append(f'<text x="28" y="{112 + k * 19}" font-family="{SANS}" font-size="13" fill="#aab3d1">{esc(ln)}</text>')
    out.append(f'<g transform="translate(386,48)"><circle r="17" fill="{col}"/><circle r="17" fill="url(#shade)"/>'
               f'<ellipse rx="29" ry="7" fill="none" stroke="{col}" stroke-width="1.6" stroke-opacity=".8" transform="rotate(-20)">'
               f'<animate attributeName="ry" values="7;9;7" dur="4s" repeatCount="indefinite"/></ellipse></g>')
    out.append(f'<rect x="28" y="176" width="10" height="10" rx="3" fill="{col}"/>')
    out.append(f'<text x="46" y="185" font-family="{MONO}" font-size="12" fill="#c9d1ea">{esc(repo["language"] or "n/a")}</text>')
    out.append(f'<text x="200" y="185" font-family="{MONO}" font-size="12" fill="#9aa5c8">★ {repo["stars"]}   forks {repo["forks"]}</text>')
    if repo.get("pushed_at"):
        out.append(f'<text x="412" y="185" text-anchor="end" font-family="{MONO}" font-size="11" fill="#6f7aa0">pushed {ago(repo["pushed_at"])}</text>')
    return "".join(out) + "</g></svg>"


def build_planets(d):
    langs = d["languages"][:5]
    W, H = 960, 120 + 52 * len(langs)
    out = [svg_open(W, H, "Planets: language breakdown"), DEFS, card(W, H), starfield(W, H, 60, 31, 20)]
    out.append(f'<text x="60" y="52" font-family="{MONO}" font-size="13" font-weight="700" letter-spacing="3" fill="{GOLD}">PLANETS · LANGUAGES BY CODE SIZE</text>')
    bx, bw = 330, 480
    for i, (name, pct) in enumerate(langs):
        y, col = 100 + i * 52, lang_color(name)
        w = max(14, bw * pct / 100)
        out.append(f'<rect x="60" y="{y - 14}" width="28" height="28" rx="7" fill="{col}"/>'
                   f'<text x="74" y="{y}" dy=".35em" text-anchor="middle" font-family="{SANS}" font-size="10" font-weight="700" '
                   f'fill="{"#141414" if col == "#f1e05a" else "#fff"}">{lang_label(name)}</text>')
        out.append(f'<text x="104" y="{y}" dy=".35em" font-family="{SANS}" font-size="16" font-weight="700" fill="{TEXT}">{esc(name)}</text>')
        out.append(f'<rect x="{bx}" y="{y - 7}" width="{bw}" height="14" rx="7" fill="#262c48"/>')
        out.append(f'<rect x="{bx}" y="{y - 7}" width="0" height="14" rx="7" fill="{col}">'
                   f'<animate attributeName="width" from="0" to="{w:.0f}" dur="1.3s" begin="{i * .15:.2f}s" fill="freeze" calcMode="spline" keySplines=".2 .8 .2 1" keyTimes="0;1"/></rect>')
        out.append(f'<text x="900" y="{y}" dy=".35em" text-anchor="end" font-family="{MONO}" font-size="14" fill="#9aa5c8" opacity="0">{pct}%'
                   f'<animate attributeName="opacity" from="0" to="1" dur=".5s" begin="{.8 + i * .15:.2f}s" fill="freeze"/></text>')
    return "".join(out) + "</g></svg>"


def build_orbit(d):
    W, H = 960, 330
    out = [svg_open(W, H, "Orbit log: contributions over the last 26 weeks"), DEFS, card(W, H), starfield(W, H, 70, 41, 20)]
    out.append(f'<text x="60" y="52" font-family="{MONO}" font-size="13" font-weight="700" letter-spacing="3" fill="{GOLD}">ORBIT LOG · 26 WEEKS</text>')
    shades = ["#161b3a", "#2a3270", "#3d4fc4", "#7b8cff", GOLD]
    cell, gap, x0, y0 = 15, 4, 60, 84
    if d["days"]:
        days = d["days"][-182:]
        # pad the first week so columns line up with Sunday-start weeks
        first = dt.date.fromisoformat(days[0][0]).weekday()
        pad = (first + 1) % 7
        grid = [None] * pad + days
        mx = max(c for _, c in days) or 1
        for idx, item in enumerate(grid):
            if item is None:
                continue
            c, r = divmod(idx, 7)[0], idx % 7
            lvl = 0 if item[1] == 0 else 1 + min(3, int(4 * (item[1] - 1) / mx))
            x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
            out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3.5" fill="{shades[lvl]}" opacity="0">'
                       f'<title>{item[0]}: {item[1]} contributions</title>'
                       f'<animate attributeName="opacity" from="0" to="1" dur=".35s" begin="{c * .04 + r * .01:.2f}s" fill="freeze"/></rect>')
    else:
        for c in range(26):
            for r in range(7):
                x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
                out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3.5" fill="#161b3a"/>')
        out.append(f'<text x="{x0}" y="{H - 34}" font-family="{MONO}" font-size="11" fill="#6f7aa0">syncing with GitHub: the heatmap fills in on the first workflow run</text>')
    sx = 640
    stats = [("DAYS IN ORBIT", f'{d["current_streak"]} days'), ("LONGEST ORBIT", f'{d["longest_streak"]} days'),
             ("REVOLUTIONS THIS YEAR", f'{d["total"]}')]
    for i, (lab, val) in enumerate(stats):
        y = 96 + i * 76
        out.append(f'<text x="{sx}" y="{y}" font-family="{MONO}" font-size="11" letter-spacing="1" fill="{MUTED}">{lab}</text>')
        out.append(f'<text x="{sx}" y="{y + 36}" font-family="{SANS}" font-size="32" font-weight="800" fill="{TEXT}" opacity="0">{val}'
                   f'<animate attributeName="opacity" from="0" to="1" dur=".8s" begin="{.4 + i * .3:.1f}s" fill="freeze"/></text>')
    return "".join(out) + "</g></svg>"


# ----------------------------------------------------------------- readme injection
def inject(text, tag, content):
    pat = re.compile(rf"(<!--{tag}:START-->)(.*?)(<!--{tag}:END-->)", re.S)
    return pat.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", text)


def main():
    d = collect()
    files = {"solar.svg": build_solar(d), "profile.svg": build_profile(d),
             "planets.svg": build_planets(d), "orbit.svg": build_orbit(d)}
    cards = []
    for i, name in enumerate(CFG["featured"], 1):
        repo = d["repos"][name]
        repo["description"] = CFG["descriptions"].get(name) or repo["description"]
        fn = f"moon-{i}.svg"
        files[fn] = build_moon(i, repo)
        cards.append((f"https://github.com/{USER}/{name}", fn, name))
    for fn, content in files.items():
        with open(os.path.join(ASSETS, fn), "w", encoding="utf-8") as f:
            f.write(content)
        print("wrote", fn, f"{len(content) // 1024} KB")
    readme_path = os.path.join(ROOT, "README.md")
    if os.path.exists(readme_path):
        text = open(readme_path, encoding="utf-8").read()
        rows = []
        for k in range(0, len(cards), 2):
            tds = "".join(f'<td width="50%"><a href="{u}"><img src="assets/{f}" alt="{esc(n)}" width="100%"/></a></td>'
                          for u, f, n in cards[k:k + 2])
            rows.append(f"<tr>{tds}</tr>")
        text = inject(text, "PROJECTS", "<table>\n" + "\n".join(rows) + "\n</table>")
        if d["pushes"]:
            act = "\n".join(f"- ☄️ pushed to [`{r.split('/')[-1]}`](https://github.com/{r}) · {ago(t)}" for r, t in d["pushes"])
        else:
            act = "_Recent pushes will appear here after the first workflow run._"
        text = inject(text, "ACTIVITY", act)
        open(readme_path, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    main()

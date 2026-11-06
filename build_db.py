#!/usr/bin/env python3
"""
build_db.py — μαζεύει τα metadata από τα releases των AppImage projects και φτιάχνει
τη βάση για το Software Manager:  out/apps.db (+ .gz), out/index.json, και τα .sha256 τους.

Δεν χρησιμοποιεί GitHub API: μόνο κλασικά links  github.com/<repo>/releases/latest/download/<αρχείο>
Μόνο stdlib της Python 3.8+.

  python3 build_db.py [apps.txt]

Έξοδοι: apps.db (+.gz), index.json, και ένα  i18n-<γλώσσα>.db.gz  ανά γλώσσα (μεταφράσεις
ονόματος/περίληψης/περιγραφής). Η βάση apps.db έχει πίνακα `languages` με sha256 κάθε αρχείου.

Μεταβλητές (όλες προαιρετικές):
  ARCHS         "x86_64"  ή  "x86_64 aarch64"
  OUT           φάκελος εξόδου (default: out)
  CENTRAL_REPO  owner/repo αυτού του project, για τα links των screenshots (στο Actions μπαίνει μόνο του)
  CENTRAL_BRANCH default: main
  RELEASE_URL   πρότυπο link, default https://github.com/{repo}/releases/latest/download/{file}
"""
import concurrent.futures as cf
import configparser
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ARCHS = os.environ.get("ARCHS", "x86_64").split()
URL_TMPL = os.environ.get("RELEASE_URL", "https://github.com/{repo}/releases/latest/download/{file}")
CENTRAL = os.environ.get("CENTRAL_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("CENTRAL_BRANCH", "main")
OUT = Path(os.environ.get("OUT", "out"))
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
API_URL = os.environ.get("RELEASES_API", "https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
SHOT_RE = re.compile(r"^(.+)-(\d+)\.(webp|png|jpe?g)$", re.I)
SHOTS = {}   # app_id -> [url, ...]  (γεμίζει από scan_screenshots)
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
SCHEMA = 1


def fetch(url, tries=3, headers=None):
    """bytes, ή None αν δεν υπάρχει (404)."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "software-db-builder", **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2 * (i + 1))
    return None


def plain(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def nolang(parent, tag):
    """Πρώτο στοιχείο <tag> χωρίς xml:lang (= αγγλικά/προεπιλογή)."""
    for c in parent.findall(tag):
        if XML_LANG not in c.attrib:
            return c
    return None


def normlang(l):
    return (l or "").replace("-", "_").strip()


def skip_lang(l):
    return l in ("", "C", "en", "POSIX")


def parse_description(c):
    """{lang: κείμενο}. Το None = προεπιλογή (αγγλικά). Δουλεύει και για τις δύο μορφές του AppStream:
    <p xml:lang=..> μέσα σε ένα <description>, ή ξεχωριστά <description xml:lang=..>."""
    blocks = {}
    for d in c.findall("description"):
        dl = d.get(XML_LANG)
        for el in d:
            lang = el.get(XML_LANG) or dl
            if el.tag == "p":
                t = plain(el)
                if t:
                    blocks.setdefault(lang, []).append(t)
            elif el.tag in ("ul", "ol"):
                per = {}
                for li in el.findall("li"):
                    t = plain(li)
                    if t:
                        per.setdefault(li.get(XML_LANG) or lang, []).append(t)
                for l, items in per.items():
                    pre = (lambda n: "- ") if el.tag == "ul" else (lambda n: f"{n}. ")
                    blocks.setdefault(l, []).append("\n".join(pre(n) + t for n, t in enumerate(items, 1)))
    return {l: "\n\n".join(b) for l, b in blocks.items()}


def lang_texts(c, tag):
    """{lang: κείμενο} για name/summary (None = προεπιλογή)."""
    return {e.get(XML_LANG): plain(e) for e in c.findall(tag) if plain(e)}


def parse_component(c):
    cid = (nolang(c, "id").text or "").strip() if nolang(c, "id") is not None else ""
    cid = re.sub(r"\.desktop$", "", cid)
    shots = []
    for s in c.findall("screenshots/screenshot"):
        imgs = [i for i in s.findall("image") if i.get("type") in (None, "source")]
        if imgs and (imgs[0].text or "").strip():
            cap = plain(nolang(s, "caption"))
            shots.append({"url": imgs[0].text.strip(), "caption": cap})
    descs = parse_description(c)
    names, sums = lang_texts(c, "name"), lang_texts(c, "summary")
    home = next((u.text.strip() for u in c.findall("url") if u.get("type") == "homepage" and u.text), "")
    return {
        "id": cid,
        "name": plain(nolang(c, "name")),
        "summary": plain(nolang(c, "summary")),
        "description": descs.get(None, ""),
        "i18n": {"name": {normlang(k): v for k, v in names.items() if k},
                 "summary": {normlang(k): v for k, v in sums.items() if k},
                 "description": {normlang(k): v for k, v in descs.items() if k}},
        "homepage": home,
        "license": plain(c.find("project_license")),
        "categories": [plain(x) for x in c.findall("categories/category")],
        "screenshots": shots,
    }


def parse_metainfo(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    comps = [root] if root.tag == "component" else root.findall("component")
    return [parse_component(c) for c in comps]


def parse_desktop(data):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read_string(data.decode("utf-8", "replace"))
        return dict(cp["Desktop Entry"])
    except (configparser.Error, KeyError):
        return {}


def desktop_i18n(d, key):
    out = {}
    for k, v in d.items():
        m = re.fullmatch(re.escape(key) + r"\[(.+)\]", k)
        if m and v:
            out[normlang(m.group(1))] = v
    return out


def build_i18n(d, comp, default):
    """{lang: {name, summary, description}} μόνο με ό,τι διαφέρει από την προεπιλογή."""
    ci = comp.get("i18n", {})
    fields = {
        "name": {**desktop_i18n(d, "Name"), **ci.get("name", {})},          # AppStream υπερισχύει
        "summary": {**desktop_i18n(d, "Comment"), **ci.get("summary", {})},
        "description": ci.get("description", {}),
    }
    rows = {}
    for f, per in fields.items():
        for lang, text in per.items():
            if skip_lang(lang) or text == default[f]:
                continue
            rows.setdefault(lang, {"name": "", "summary": "", "description": ""})[f] = text
    return rows


def scan_screenshots():
    """Σκανάρει τα releases του κεντρικού repo με tag  <πακέτο>-screenshots  και παίρνει τα assets
    με όνομα  <app_id>-<n>.<webp|png|jpg>.  Χρησιμοποιεί το GITHUB_TOKEN του workflow (1 αίτημα ανά 100 releases)."""
    if not (CENTRAL and TOKEN):
        print("INFO: χωρίς GITHUB_TOKEN/CENTRAL_REPO — δεν σκανάρονται τα screenshots releases", file=sys.stderr)
        return
    hdr = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
    found, page = {}, 1
    while True:
        raw = fetch(API_URL.format(repo=CENTRAL, page=page), headers=hdr)
        if raw is None:
            print("WARN: δεν διαβάστηκαν τα releases (token/δικαιώματα;) — χωρίς δικά σου screenshots", file=sys.stderr)
            return
        rels = json.loads(raw)
        for r in rels:
            if r.get("draft") or not str(r.get("tag_name", "")).endswith("-screenshots"):
                continue
            for a in r.get("assets", []):
                m = SHOT_RE.match(a.get("name", ""))
                if m and a.get("size", 1) > 0:
                    found.setdefault(m.group(1), []).append((int(m.group(2)), a["browser_download_url"]))
        if len(rels) < 100:
            break
        page += 1
    for app_id, items in found.items():
        SHOTS[app_id] = [u for _, u in sorted(items)]   # αριθμητική σειρά: 1, 2, ..., 10
    print(f"INFO: screenshots releases: {len(SHOTS)} εφαρμογές", file=sys.stderr)


def override_shots(app_id, pkg):
    return [{"url": u, "caption": ""} for u in SHOTS.get(app_id, [])]


def process(repo, pkg, arch):
    def get(name):
        return fetch(URL_TMPL.format(repo=repo, file=name))

    raw = get(f"{pkg}-{arch}.json")
    if raw is None:
        return None, f"SKIP {repo} {pkg} {arch}: δεν βρέθηκε {pkg}-{arch}.json"
    try:
        j = json.loads(raw)
    except ValueError:
        return None, f"SKIP {repo} {pkg} {arch}: μη έγκυρο JSON"

    comps = {}
    for name in j.get("metainfo_files", []):
        data = get(name)
        for comp in (parse_metainfo(data) if data else []):
            if comp["id"]:
                comps.setdefault(comp["id"], comp)

    icon = j.get("icon", "")
    icon_url = URL_TMPL.format(repo=repo, file=icon) if icon else ""
    pkgrow = {
        "pkg": pkg, "arch": arch, "repo": repo,
        "version": j.get("version", ""), "description": j.get("description", ""),
        "upstream_url": j.get("upstream_url", ""), "licenses": j.get("licenses", ""),
        "build_date": j.get("build_date", ""), "installed_size": j.get("installed_size", ""),
        "file": j["appimage"]["file"], "size": j["appimage"]["size"],
        "sha256": j["appimage"]["sha256"],
        "url": j["appimage"].get("url") or URL_TMPL.format(repo=repo, file=j["appimage"]["file"]),
    }

    apps = []
    for dname in j.get("desktop", {}).get("files", []):
        data = get(dname)
        d = parse_desktop(data) if data else {}
        if d.get("NoDisplay", "").lower() == "true":
            continue
        app_id = re.sub(r"\.desktop$", "", dname)
        comp = comps.get(app_id, {})
        shots = override_shots(app_id, pkg) or comp.get("screenshots", [])
        default = {
            "name": comp.get("name") or d.get("Name", app_id),
            "summary": comp.get("summary") or d.get("Comment", "") or pkgrow["description"],
            "description": comp.get("description", ""),
        }
        apps.append({
            "app_id": app_id, "pkg": pkg, "arch": arch,
            **default,
            "_i18n": build_i18n(d, comp, default),
            "categories": d.get("Categories", ";".join(comp.get("categories", []))).strip(),
            "homepage": comp.get("homepage") or pkgrow["upstream_url"],
            "license": comp.get("license") or pkgrow["licenses"],
            "desktop_file": dname, "icon_url": icon_url,
            "has_metainfo": bool(comp), "screenshots": shots,
        })
    if not apps:
        return None, f"SKIP {repo} {pkg} {arch}: κανένα .desktop"
    return (pkgrow, apps), None


def sha_file(path):
    h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{h}  {Path(path).name}\n")


def main():
    listfile = sys.argv[1] if len(sys.argv) > 1 else "apps.txt"
    jobs = []
    for line in Path(listfile).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            repo, pkg = line.split()[:2]
            jobs += [(repo, pkg, a) for a in ARCHS]

    scan_screenshots()
    results, warns = [], []
    with cf.ThreadPoolExecutor(16) as ex:
        for res, warn in ex.map(lambda a: process(*a), jobs):
            if warn:
                warns.append(warn)
            if res:
                results.append(res)
    results.sort(key=lambda r: (r[0]["pkg"], r[0]["arch"]))
    for w in warns:
        print(w, file=sys.stderr)

    OUT.mkdir(parents=True, exist_ok=True)
    pkgs = [r[0] for r in results]
    apps = [a for r in results for a in sorted(r[1], key=lambda x: x["app_id"])]
    for a in apps:
        a["screenshot_count"] = len(a["screenshots"])

    # --- μεταφράσεις: ένα αρχείο ανά γλώσσα ---
    for old in OUT.glob("i18n-*"):
        old.unlink()
    per_lang = {}
    for a in apps:
        for lang, row in a.pop("_i18n").items():
            per_lang.setdefault(lang, []).append((a["app_id"], a["arch"], row))
    languages = []
    for lang in sorted(per_lang):
        safe = re.sub(r"[^A-Za-z0-9_-]", "-", lang)
        dbp = OUT / f"i18n-{safe}.db"
        con = sqlite3.connect(dbp)
        con.executescript("""
        PRAGMA journal_mode=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE i18n(app_id TEXT, arch TEXT, name TEXT, summary TEXT, description TEXT,
            PRIMARY KEY(app_id, arch));
        """)
        con.execute("INSERT INTO meta VALUES('lang', ?)", (lang,))
        for app_id, arch, row in sorted(per_lang[lang], key=lambda r: (r[0], r[1])):
            con.execute("INSERT INTO i18n VALUES(?,?,?,?,?)",
                        (app_id, arch, row["name"], row["summary"], row["description"]))
        con.commit()
        con.execute("VACUUM")
        con.close()
        gzp = OUT / f"i18n-{safe}.db.gz"
        with open(dbp, "rb") as f, open(gzp, "wb") as raw, \
                gzip.GzipFile(dbp.name, "wb", 9, raw, mtime=0) as gz:
            gz.write(f.read())
        dbp.unlink()
        languages.append({"lang": lang, "file": gzp.name, "size": gzp.stat().st_size,
                          "sha256": hashlib.sha256(gzp.read_bytes()).hexdigest(),
                          "apps": len(per_lang[lang])})

    # --- index.json (χωρίς timestamp, ώστε ίδιο περιεχόμενο = ίδιο sha) ---
    idx = OUT / "index.json"
    idx.write_text(json.dumps({"schema": SCHEMA, "packages": pkgs, "apps": apps, "languages": languages},
                              ensure_ascii=False, indent=1, sort_keys=True) + "\n")

    # --- apps.db ---
    db = OUT / "apps.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript("""
    PRAGMA journal_mode=OFF;
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE packages(pkg TEXT, arch TEXT, repo TEXT, version TEXT, description TEXT,
        upstream_url TEXT, licenses TEXT, build_date TEXT, installed_size TEXT,
        file TEXT, size INTEGER, sha256 TEXT, url TEXT, PRIMARY KEY(pkg, arch));
    CREATE TABLE apps(app_id TEXT, pkg TEXT, arch TEXT, name TEXT, summary TEXT, description TEXT,
        categories TEXT, homepage TEXT, license TEXT, desktop_file TEXT, icon_url TEXT,
        has_metainfo INTEGER, PRIMARY KEY(app_id, arch),
        FOREIGN KEY(pkg, arch) REFERENCES packages(pkg, arch));
    CREATE TABLE screenshots(app_id TEXT, arch TEXT, idx INTEGER, url TEXT, caption TEXT,
        PRIMARY KEY(app_id, arch, idx));
    CREATE TABLE languages(lang TEXT PRIMARY KEY, file TEXT, size INTEGER, sha256 TEXT, apps INTEGER);
    CREATE INDEX apps_pkg ON apps(pkg, arch);
    """)
    con.execute("INSERT INTO meta VALUES('schema', ?)", (str(SCHEMA),))
    con.execute("INSERT INTO meta VALUES('apps', ?)", (str(len(apps)),))
    for p in pkgs:
        con.execute("INSERT INTO packages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [p[k] for k in ("pkg", "arch", "repo", "version", "description", "upstream_url",
                                    "licenses", "build_date", "installed_size", "file", "size",
                                    "sha256", "url")])
    for a in apps:
        con.execute("INSERT INTO apps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [a[k] for k in ("app_id", "pkg", "arch", "name", "summary", "description",
                                    "categories", "homepage", "license", "desktop_file",
                                    "icon_url")] + [int(a["has_metainfo"])])
        for i, s in enumerate(a["screenshots"]):
            con.execute("INSERT INTO screenshots VALUES(?,?,?,?,?)",
                        (a["app_id"], a["arch"], i, s["url"], s["caption"]))
    for l in languages:
        con.execute("INSERT INTO languages VALUES(?,?,?,?,?)",
                    (l["lang"], l["file"], l["size"], l["sha256"], l["apps"]))
    con.commit()
    con.execute("VACUUM")
    con.close()

    # --- gz (mtime=0 => ίδιο περιεχόμενο, ίδιο αρχείο) + sha256 ---
    with open(db, "rb") as f, open(OUT / "apps.db.gz", "wb") as raw, \
            gzip.GzipFile("apps.db", "wb", 9, raw, mtime=0) as gz:
        gz.write(f.read())
    for f in ("index.json", "apps.db", "apps.db.gz"):
        sha_file(OUT / f)

    # --- αναφορά ---
    missing = sorted(f"{a['app_id']} ({a['pkg']})" for a in apps if not a["screenshots"])
    # Το GitHub δεν δέχεται άδεια αρχεία (size >= 1), γι' αυτό υπάρχει πάντα τουλάχιστον μία γραμμή.
    (OUT / "missing-screenshots.txt").write_text(
        "\n".join(missing) + "\n" if missing else "# none: all apps have at least one screenshot\n")
    print(f"OK: {len(pkgs)} πακέτα, {len(apps)} εφαρμογές, {len(languages)} γλώσσες, "
          f"{len(missing)} χωρίς screenshot, {len(warns)} παραλείφθηκαν")
    if missing:
        print("Χωρίς screenshot: " + ", ".join(missing))


if __name__ == "__main__":
    main()

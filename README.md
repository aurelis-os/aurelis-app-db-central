# Software DB builder

Μαζεύει τα metadata όλων των AppImage projects και φτιάχνει τη βάση για το Software Manager.

    apps.txt  ──►  build_db.py  ──►  out/apps.db  (SQLite)
                                     out/apps.db.gz
                                     out/index.json
                                     out/*.sha256, missing-screenshots.txt

## Πώς δουλεύει
1. Για κάθε γραμμή του `apps.txt` (`owner/repo  πακέτο`) κατεβάζει από το **τελευταίο release**
   το `<πακέτο>-<arch>.json`, τα `.desktop` και τα `.xml` (AppStream) που ανέβασε το `pkg-meta.sh`.
   Μόνο απλά links `github.com/.../releases/latest/download/...`, **όχι GitHub API**.
2. Φτιάχνει **μία εγγραφή ανά εφαρμογή** (ένα `.desktop`), όχι ανά πακέτο:
   το πακέτο `kate` δίνει δύο εφαρμογές, Kate και KWrite.
3. Βάζει screenshots από το AppStream, ή από τον φάκελο `screenshots/` αν υπάρχει εκεί.
4. Το workflow (κάθε 6 ώρες + σε αλλαγή του `apps.txt`) ανεβάζει τα αρχεία στο release με tag `db`.

## Πού τα κατεβάζει το Software app
Βάση:   https://github.com/<ΕΣΥ>/<ΑΥΤΟ-ΤΟ-REPO>/releases/download/db/apps.db.gz
Έλεγχος αλλαγής (μικρό αρχείο): .../releases/download/db/apps.db.gz.sha256

Το app κατεβάζει πρώτα το `.sha256`, το συγκρίνει με το τοπικό, και κατεβάζει τη βάση **μόνο αν άλλαξε**.
Δεν υπάρχει timestamp μέσα στα αρχεία, άρα ίδιο περιεχόμενο = ίδιο sha.

## Πίνακες (apps.db)
- `packages(pkg, arch, repo, version, description, upstream_url, licenses, build_date, installed_size, file, size, sha256, url)`
- `apps(app_id, pkg, arch, name, summary, description, categories, homepage, license, desktop_file, icon_url, has_metainfo)`
- `screenshots(app_id, arch, idx, url, caption)`
- `meta(key, value)`

Λήψη εφαρμογής: `apps.pkg/arch → packages.url`, έλεγχος με `packages.sha256`.

## Νέο project
Πρόσθεσε μια γραμμή στο `apps.txt`. Αυτό αρκεί.

## Τοπική δοκιμή
    python3 build_db.py apps.txt
    sqlite3 out/apps.db "select app_id, name from apps"

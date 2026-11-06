# Software DB builder

Μαζεύει τα metadata όλων των AppImage projects και φτιάχνει τη βάση για το Software Manager.

    apps.txt  ──►  build_db.py  ──►  out/apps.db  (SQLite)
                                     out/apps.db.gz
                                     out/index.json
                                     out/i18n-<γλώσσα>.db.gz   (μεταφράσεις, ένα αρχείο ανά γλώσσα)
                                     out/*.sha256, missing-screenshots.txt

## Πώς δουλεύει
1. Για κάθε γραμμή του `apps.txt` (`owner/repo  πακέτο`) κατεβάζει από το **τελευταίο release**
   το `<πακέτο>-<arch>.json`, τα `.desktop` και τα `.xml` (AppStream) που ανέβασε το `pkg-meta.sh`.
   Μόνο απλά links `github.com/.../releases/latest/download/...`, **όχι GitHub API**.
2. Φτιάχνει **μία εγγραφή ανά εφαρμογή** (ένα `.desktop`), όχι ανά πακέτο:
   το πακέτο `kate` δίνει δύο εφαρμογές, Kate και KWrite.
3. Βάζει screenshots από το AppStream, ή τα δικά σου από τα releases `<πακέτο>-screenshots` (βλ. παρακάτω).
4. Το workflow (κάθε 6 ώρες + σε αλλαγή του `apps.txt`) ανεβάζει τα αρχεία στο release με tag `db`.

## Πού τα κατεβάζει το Software app
Βάση:   https://github.com/<ΕΣΥ>/<ΑΥΤΟ-ΤΟ-REPO>/releases/download/db/apps.db.gz
Έλεγχος αλλαγής (μικρό αρχείο): .../releases/download/db/apps.db.gz.sha256

Το app κατεβάζει πρώτα το `.sha256`, το συγκρίνει με το τοπικό, και κατεβάζει τη βάση **μόνο αν άλλαξε**.
Δεν υπάρχει timestamp μέσα στα αρχεία, άρα ίδιο περιεχόμενο = ίδιο sha.

## Μεταφράσεις
`apps.db` έχει τα **αγγλικά**. Οι μεταφράσεις (όνομα, περίληψη, περιγραφή) είναι σε ξεχωριστά αρχεία
`i18n-<γλώσσα>.db.gz` (π.χ. `i18n-el`, `i18n-de`, `i18n-pt_BR`), από το AppStream και από τα `Name[xx]`/`Comment[xx]` του `.desktop`.
Ο πίνακας `languages` της `apps.db` λέει ποιες γλώσσες υπάρχουν και το sha256 του καθενός.

Το app: διαβάζει το `languages`, κατεβάζει μόνο το αρχείο της γλώσσας του χρήστη
(δοκιμάζει `el_GR` → `el` → τίποτα) και το επαληθεύει με το sha256.
Στον πίνακα `i18n(app_id, arch, name, summary, description)` ένα **κενό πεδίο** σημαίνει «χρήση της αγγλικής τιμής».
Γλώσσες `en`/`C` και τιμές ίδιες με τα αγγλικά δεν αποθηκεύονται.

## Πίνακες (apps.db)
- `packages(pkg, arch, repo, version, description, upstream_url, licenses, build_date, installed_size, file, size, sha256, url)`
- `apps(app_id, pkg, arch, name, summary, description, categories, homepage, license, desktop_file, icon_url, has_metainfo)`
- `screenshots(app_id, arch, idx, url, caption)`
- `languages(lang, file, size, sha256, apps)`
- `meta(key, value)`

Λήψη εφαρμογής: `apps.pkg/arch → packages.url`, έλεγχος με `packages.sha256`.

## Νέο project
Πρόσθεσε μια γραμμή στο `apps.txt`. Αυτό αρκεί.

## Τοπική δοκιμή
    python3 build_db.py apps.txt
    sqlite3 out/apps.db "select app_id, name from apps"

## Screenshots
Προτεραιότητα: **δικά σου** (releases `*-screenshots`) → αλλιώς του AppStream → αλλιώς λείπουν (λίστα στο `missing-screenshots.txt`).

Τα δικά σου **δεν μπαίνουν στο repo** (ούτε LFS). Ανεβαίνουν ως assets σε releases του repo,
ένα release ανά πακέτο, με tag `<πακέτο>-screenshots` (π.χ. `krita-screenshots`).

1. Φτιάξε τοπικά (το `screenshots-src/` είναι στο `.gitignore`):

       screenshots-src/krita/org.kde.krita/01.webp
       screenshots-src/krita/org.kde.krita/02.webp
       screenshots-src/kate/org.kde.kate/01.webp
       screenshots-src/kate/org.kde.kwrite/01.webp

   `<πακέτο>/<app_id>/` όπου `app_id` = όνομα του `.desktop` χωρίς `.desktop`. Ονόμασέ τα 01, 02, ...
   (η σειρά είναι αλφαβητική). Μορφές webp/png/jpg. Προτείνεται πλάτος ~1280px και < 300 KB.
2. `./upload-screenshots.sh` (χρειάζεται `gh auth login`).
3. Τρέξε το workflow **Build Software DB** (ή περίμενε το cron). Δεν χρειάζεται commit.

Το build διαβάζει μόνο του τα releases του repo (με το `GITHUB_TOKEN` του workflow, 1 αίτημα ανά 100 releases),
κρατά assets με όνομα `<app_id>-<n>.<webp|png|jpg>` και τα ταξινομεί αριθμητικά. Αγνοεί drafts και άδεια αρχεία.
Αν δεν υπάρχει token (π.χ. τοπικό τρέξιμο χωρίς `GITHUB_TOKEN`) παραλείπει τα δικά σου και κρατά του AppStream.

Αν υπάρχουν δικά σου screenshots για μια εφαρμογή, **αντικαθιστούν** αυτά του AppStream.

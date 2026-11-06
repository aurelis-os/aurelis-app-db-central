#!/bin/sh
# upload-screenshots.sh [φάκελος]      (default: screenshots-src)
#
# Ανεβάζει τα screenshots σε releases του κεντρικού repo, ένα release ανά πακέτο:
#     screenshots-src/<πακέτο>/<app_id>/01.webp, 02.webp ...   ->   tag  <πακέτο>-screenshots
# Τα assets ονομάζονται  <app_id>-<n>.<κατάληξη>.  Δεν χρειάζεται commit τίποτα: το build της βάσης
# σκανάρει μόνο του τα releases  *-screenshots.
#
# Χρειάζεται: gh (συνδεδεμένο, gh auth login).   Προαιρετικά REPO=owner/repo.
# Η σειρά = αλφαβητική, γι' αυτό ονόμασε τα 01, 02, 03 ... Μορφές: webp png jpg jpeg.
set -eu

SRC=${1:-screenshots-src}
REPO=${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

for pdir in "$SRC"/*/; do
	[ -d "$pdir" ] || continue
	pkg=$(basename "$pdir"); tag="$pkg-screenshots"
	for adir in "$pdir"*/; do
		[ -d "$adir" ] || continue
		app=$(basename "$adir"); n=0; names=
		out="$TMP/$pkg/$app"; mkdir -p "$out"
		for f in "$adir"*; do
			[ -f "$f" ] || continue
			ext=$(printf '%s' "${f##*.}" | tr 'A-Z' 'a-z')
			case "$ext" in webp|png|jpg|jpeg) ;; *) echo "skip $f"; continue;; esac
			[ -s "$f" ] || { echo "skip $f (άδειο)"; continue; }
			n=$((n + 1)); name="$app-$n.$ext"
			cp "$f" "$out/$name"; names="$names $name"
		done
		[ "$n" -gt 0 ] || { echo "WARN: $pkg/$app: κανένα screenshot"; continue; }

		gh release view "$tag" -R "$REPO" >/dev/null 2>&1 || \
			gh release create "$tag" -R "$REPO" --title "$pkg screenshots" \
				--notes "Screenshots for $pkg" --latest=false
		( cd "$out" && gh release upload "$tag" -R "$REPO" --clobber $names )
		echo "OK $app: $n screenshots -> $tag"
	done
done
echo "Τέλος. Τρέξε το workflow \"Build Software DB\" (ή περίμενε το cron) για να μπουν στη βάση."

#!/usr/bin/env bash
# Open a PR bumping aMule's two Dockerfile ARGs to the latest upstream release.
# DRY_RUN=1 prints the rewritten ARG lines and the PR body, and writes nothing anywhere.
set -euo pipefail

UPSTREAM=amule-org/amule
REPO=${GITHUB_REPOSITORY:-mission-titar/mulewatch}
DOCKERFILE=${DOCKERFILE:-packages/crawler/Dockerfile}
DRY_RUN=${DRY_RUN:-}

die() { echo "error: $*" >&2; exit 1; }

# One line per ARG, or the rewrite below would be ambiguous.
for arg in AMULE_VERSION AMULE_COMMIT; do
  [ "$(grep -c "^ARG $arg=" "$DOCKERFILE")" = 1 ] || die "expected one 'ARG $arg=' line in $DOCKERFILE"
done
old=$(sed -n 's/^ARG AMULE_VERSION=//p' "$DOCKERFILE")

new=$(gh api "repos/$UPSTREAM/releases/latest" --jq .tag_name)
# The tag ends up in a branch name, sed and a URL: accept a plain version only.
[[ $new =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected upstream release tag '$new'"
if [ "$new" = "$old" ]; then
  echo "aMule $old is the latest release: nothing to do"
  exit 0
fi
if [ "$(printf '%s\n' "$old" "$new" | sort -V | tail -n1)" != "$new" ]; then
  echo "latest upstream release $new is older than the pinned $old: nothing to do"
  exit 0
fi

# Pin the peeled commit (X^{}), never the annotated tag object: BuildKit's --checksum wants the commit.
ref="refs/tags/$new"
commit=$(git ls-remote "https://github.com/$UPSTREAM.git" "$ref" "$ref^{}" \
  | awk -v ref="$ref" '$2 == ref "^{}" { peeled = $1 } $2 == ref { direct = $1 }
      END { print (peeled != "" ? peeled : direct) }')
[[ $commit =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve $ref in $UPSTREAM"

branch="chore/amule-$new"
if gh api "repos/$REPO/branches/$branch" --silent 2>/dev/null; then
  echo "branch $branch already exists: nothing to do"
  exit 0
fi
if [ "$(gh pr list --repo "$REPO" --head "$branch" --state open --json number --jq length)" != 0 ]; then
  echo "a PR for $branch is already open: nothing to do"
  exit 0
fi

rewritten=$(mktemp)
body=$(mktemp)
trap 'rm -f "$rewritten" "$body"' EXIT
sed -e "s/^ARG AMULE_VERSION=.*/ARG AMULE_VERSION=$new/" \
    -e "s/^ARG AMULE_COMMIT=.*/ARG AMULE_COMMIT=$commit/" "$DOCKERFILE" > "$rewritten"

# A file absent at a tag (REFERENCE.md before 3.1.0) reads as empty, so its diff shows it added.
raw() { curl -fsL "https://raw.githubusercontent.com/$UPSTREAM/$1/$2" || true; }
# Keep whole lines up to $1 bytes (GitHub caps a PR body at 65536 characters).
clip() {
  # Reads to the end: an early exit would SIGPIPE the writer and fail the script under pipefail.
  awk -v max="$1" '{ n += length($0) + 1 } n <= max { print; next }
    !cut { print "[truncated, see the compare link above]"; cut = 1 }'
}
# Fenced so upstream "#123" references and "@name" mentions do not link or ping from this repo.
fenced() { printf '````%s\n' "$1"; cat; printf '````\n'; }
tag_diff() {
  diff -u --label "$old/$1" --label "$new/$1" <(raw "$old" "$1") <(raw "$new" "$1") || true
}

compare="https://github.com/$UPSTREAM/compare/$old...$new"
{
  echo "Bumps aMule from $old to [$new](https://github.com/$UPSTREAM/releases/tag/$new)" \
    "(commit \`$commit\`). Full upstream diff: $compare"
  echo
  echo "## Changelog"
  echo
  raw "$new" docs/CHANGELOG.md \
    | awk -v head="## Version $new " '/^## Version / { on = index($0, head) == 1 } on' \
    | fold -s -w 100 | clip 20000 | fenced text
  for file in cmake/options.cmake docs/api/REFERENCE.md; do
    echo
    echo "## \`$file\` ($old to $new)"
    echo
    tag_diff "$file" | clip 18000 | fenced diff
  done
  cat <<EOF

## Checklist

- [ ] Read the changelog: anything that changes the daemon's defaults or the network behaviour?
- [ ] \`options.cmake\` diff: a new switch to set explicitly in the Dockerfile's CMake options?
- [ ] \`REFERENCE.md\` diff: does the \`mule_api\` adapter need a change (with its tests first)?
- [ ] \`amule-config.py\`: does any setting we override (or rely on the default of) change default?
- [ ] CI green on amd64 and arm64.
- [ ] Merge, then tag a release (\`vX.Y.Z - aMule $new\`): the image only changes on a tag.
EOF
} > "$body"

if [ -n "$DRY_RUN" ]; then
  grep -E '^ARG AMULE_(VERSION|COMMIT)=' "$rewritten"
  echo "--- PR body ($(wc -c < "$body") bytes) ---"
  cat "$body"
  exit 0
fi

# Branch from the checked-out commit and commit through the API, so the App signs it.
gh api "repos/$REPO/git/refs" -f ref="refs/heads/$branch" -f sha="$(git rev-parse HEAD)" --silent
gh api -X PUT "repos/$REPO/contents/$DOCKERFILE" --silent \
  -f message="chore(amule): bump to $new" \
  -f content="$(base64 -w0 "$rewritten")" \
  -f sha="$(git rev-parse "HEAD:$DOCKERFILE")" \
  -f branch="$branch"
gh pr create --repo "$REPO" --base main --head "$branch" \
  --title "chore(amule): bump to $new" --body-file "$body"

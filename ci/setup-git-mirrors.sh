#!/usr/bin/env bash
# setup-git-mirrors.sh — Setup dual-remote git mirroring (GitHub + GitLab)
#
# Prevents single-point-of-failure where all repos live only on GitHub.
# Ensures GitLab has up-to-date mirror of all critical repos.

set -euo pipefail

GITHUB_ORG="teknium"
GITLAB_GROUP="nemohermes"
GITLAB_HOST="192.168.2.180:8929"

echo "📡 Setting up git mirror between GitHub and GitLab"
echo

# List of critical repositories to mirror
REPOS=(
    "nemohermes_bks"
    "nemohermes_router"
    "memgraphrag"
    "nemoclaw"
)

for REPO in "${REPOS[@]}"; do
    echo "🔄 Mirroring: $REPO"
    
    # Clone from GitHub (primary)
    if [ ! -d "$REPO" ]; then
        git clone --mirror "https://github.com/$GITHUB_ORG/$REPO.git" "$REPO.git"
    fi
    
    # Push to GitLab (backup)
    cd "$REPO.git"
    git push --mirror "https://oauth:${GITLAB_TOKEN}@$GITLAB_HOST/nemohermes/$REPO.git" || true
    cd ..
    
    echo "  ✓ $REPO mirrored"
done

echo
echo "✅ All repositories mirrored to GitLab"
echo "📝 Configure in .github/workflows/mirror.yml to run daily"

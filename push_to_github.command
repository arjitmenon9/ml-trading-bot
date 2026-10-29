#!/bin/bash
cd "$(dirname "$0")"
set -e

echo "=================================================================="
echo "  Pushing ml-trading-bot to a private GitHub repo"
echo "=================================================================="
echo ""

if ! command -v gh &>/dev/null; then
    echo "[1/3] GitHub CLI (gh) not found."
    if command -v brew &>/dev/null; then
        echo "      Installing via Homebrew (this can take a minute or two)..."
        brew install gh
    else
        echo "      Homebrew isn't installed either, so I can't install gh automatically."
        echo "      Please install it manually from https://cli.github.com/ (or run"
        echo "      'brew install gh' after installing Homebrew from https://brew.sh),"
        echo "      then double-click this file again."
        echo ""
        echo "Press Enter to close this window..."
        read
        exit 1
    fi
else
    echo "[1/3] GitHub CLI found."
fi

echo ""
echo "[2/3] Checking GitHub sign-in status..."
if ! gh auth status &>/dev/null; then
    echo "      Not signed in yet. A browser window will open - just log in"
    echo "      and click 'Authorize' there. Come back to this window after."
    gh auth login --web --git-protocol https
else
    echo "      Already signed in."
fi

echo ""
echo "[3/3] Creating the private repo and pushing your code..."
git branch -M main
if gh repo view ml-trading-bot &>/dev/null; then
    echo "      Repo 'ml-trading-bot' already exists on your GitHub - adding it as"
    echo "      a remote and pushing to it instead of creating a new one."
    gh repo set-default ml-trading-bot 2>/dev/null || true
    REPO_URL=$(gh repo view ml-trading-bot --json url -q .url)
    git remote add origin "$REPO_URL.git" 2>/dev/null || git remote set-url origin "$REPO_URL.git"
    git push -u origin main
else
    gh repo create ml-trading-bot --private --source=. --remote=origin --push
fi

echo ""
echo "=================================================================="
echo "  Done! Your repo:"
gh repo view ml-trading-bot --json url -q .url 2>/dev/null
echo "=================================================================="
echo "Press Enter to close this window..."
read

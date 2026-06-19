#!/bin/bash
# =============================================================================
#  setup_github.sh — Initialise le repo GitHub et pousse le projet
#  À lancer UNE SEULE FOIS depuis ton PC Linux Mint
#  Résultat : le build Windows démarre automatiquement sur GitHub Actions
# =============================================================================

set -euo pipefail

G='\033[0;32m'
C='\033[0;36m'
A='\033[0;33m'
R='\033[0;31m'
B='\033[1m'
N='\033[0m'

echo -e "${C}${B}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  NOISE FLOOR METER — Setup GitHub Actions   ║"
echo "  ║  Build Windows .exe automatique             ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${N}"

# ── Vérifier git ──
if ! command -v git &>/dev/null; then
    echo -e "${A}Installation de git...${N}"
    sudo apt-get install -y git
fi

# ── Vérifier gh (GitHub CLI) ──
if ! command -v gh &>/dev/null; then
    echo -e "${A}Installation de GitHub CLI...${N}"
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
        https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y gh
fi

echo -e "${G}✓ Outils disponibles${N}"
echo ""

# ── Authentification GitHub ──
echo -e "${C}▸ Connexion à GitHub...${N}"
echo -e "  (si non connecté, un navigateur va s'ouvrir pour l'authentification)"
echo ""
if ! gh auth status &>/dev/null; then
    gh auth login --web --git-protocol https
fi
echo -e "${G}✓ Authentifié sur GitHub${N}"
echo ""

# ── Nom d'utilisateur GitHub ──
GH_USER=$(gh api user --jq .login)
REPO_NAME="noise-floor-meter"
REPO_URL="https://github.com/${GH_USER}/${REPO_NAME}"

echo -e "  Utilisateur GitHub : ${C}${GH_USER}${N}"
echo -e "  Repository cible   : ${C}${REPO_URL}${N}"
echo ""

# ── Créer le repo GitHub si inexistant ──
if gh repo view "${GH_USER}/${REPO_NAME}" &>/dev/null; then
    echo -e "${A}  Le repo ${REPO_NAME} existe déjà.${N}"
else
    echo -e "${A}▸ Création du repository GitHub...${N}"
    gh repo create "${REPO_NAME}" \
        --public \
        --description "Noise Floor Meter — IC-706 + EMU 0202 — F8AOF" \
        --homepage "${REPO_URL}"
    echo -e "${G}✓ Repository créé : ${REPO_URL}${N}"
fi
echo ""

# ── Initialiser git local ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".git" ]; then
    echo -e "${A}▸ Initialisation git local...${N}"
    git init
    git remote add origin "https://github.com/${GH_USER}/${REPO_NAME}.git"
else
    echo -e "${G}✓ Repo git déjà initialisé${N}"
    # S'assurer que le remote est correct
    git remote set-url origin "https://github.com/${GH_USER}/${REPO_NAME}.git" 2>/dev/null || \
    git remote add origin "https://github.com/${GH_USER}/${REPO_NAME}.git" 2>/dev/null || true
fi

# ── Créer assets/ si absent (icône placeholder) ──
mkdir -p assets
if [ ! -f "assets/nfm.ico" ]; then
    # L'icône sera générée par le pipeline Python au moment du build
    touch assets/nfm.ico
    echo -e "  (icône placeholder — sera générée au build)"
fi

# ── LICENSE.txt ──
if [ ! -f "LICENSE.txt" ]; then
cat > LICENSE.txt << 'EOF'
Noise Floor Meter v1.0.0
F8AOF Amateur Radio

Logiciel libre pour usage radioamateur.
Redistribution et utilisation autorisées avec mention de l'auteur.
EOF
fi

# ── .gitignore ──
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
*.pyo
dist/
build/
*.egg-info/
.nfm_config.json
*.log
.DS_Store
Thumbs.db
EOF

# ── Premier commit et push ──
echo -e "${A}▸ Commit et push vers GitHub...${N}"
git add .
git status --short
git commit -m "feat: Noise Floor Meter v1.0.0 — IC-706 + EMU 0202 — F8AOF

- Interface tkinter + matplotlib
- FFT/PSD dBFS/Hz (24 bits / 48 kHz)
- Contrôle CAT Hamlib via rigctld
- Sélection automatique carte son Windows
- Build PyInstaller + installeur NSIS" 2>/dev/null || \
git commit --allow-empty -m "chore: trigger Windows build"

git branch -M main
git push -u origin main --force

echo ""
echo -e "${G}${B}✓ Push effectué !${N}"
echo ""
echo -e "${C}═══════════════════════════════════════════════════${N}"
echo -e "${C}  Le build Windows démarre automatiquement.${N}"
echo ""
echo -e "  Suivre la progression :${N}"
echo -e "  ${B}${REPO_URL}/actions${N}"
echo ""
echo -e "  Télécharger les .exe une fois terminé :"
echo -e "  ${B}${REPO_URL}/actions${N}"
echo -e "  → Dernier build → Artifacts"
echo -e "  → NoiseFloorMeter-Setup-main.zip"
echo ""
echo -e "${A}  Durée estimée du build : 4 à 8 minutes${N}"
echo -e "${C}═══════════════════════════════════════════════════${N}"
echo ""

# Ouvrir la page Actions dans le navigateur
echo -e "${A}▸ Ouverture de la page GitHub Actions...${N}"
xdg-open "${REPO_URL}/actions" 2>/dev/null || \
    echo -e "  → Ouvre manuellement : ${REPO_URL}/actions"

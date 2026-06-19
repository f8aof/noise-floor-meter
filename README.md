# Noise Floor Meter — F8AOF
## Windows Edition v1.0.0

Mesure du plancher de bruit RF via carte son + analyse PSD (dBFS/Hz)  
IC-706MkIIH · EMU 0202 · Contrôle CAT Hamlib

---

## Structure du projet

```
nfm_windows/
├── src/
│   └── noise_floor_meter.py     ← Application principale
├── assets/
│   └── nfm.ico                  ← Icône (générée au build)
├── installer/
│   └── installer.nsi            ← Script NSIS
├── .github/
│   └── workflows/
│       └── build.yml            ← Pipeline CI/CD GitHub Actions
├── noise_floor_meter.spec       ← Spec PyInstaller
└── README.md
```

---

## Build automatique via GitHub Actions

### 1. Créer un repository GitHub

```bash
git init
git add .
git commit -m "Initial commit — Noise Floor Meter v1.0.0"
git remote add origin https://github.com/TON_USERNAME/noise-floor-meter.git
git push -u origin main
```

### 2. Déclencher le build

Le build se lance automatiquement à chaque push sur `main`.  
Pour un build manuel : **GitHub → Actions → Build Windows Installer → Run workflow**

### 3. Récupérer les fichiers compilés

**GitHub → Actions → (dernier build) → Artifacts**

- `NoiseFloorMeter-Setup-main.zip` → contient `NoiseFloorMeter_Setup.exe`
- `NoiseFloorMeter-portable-main.zip` → contient `NoiseFloorMeter.exe`

### 4. Release officielle (optionnel)

Pour générer une release avec tag :

```bash
git tag v1.0.0
git push origin v1.0.0
```

Le pipeline génère automatiquement une Release GitHub avec les deux .exe en téléchargement.

---

## Installation Windows

Double-cliquer sur `NoiseFloorMeter_Setup.exe` :

1. Bienvenue → Suivant
2. Licence → Accepter → Suivant
3. Dossier d'installation (défaut : `C:\Program Files\NoiseFloorMeter`) → Installer
4. Raccourci créé dans le Menu Démarrer et sur le Bureau
5. Désinstallation via Panneau de configuration → Programmes

---

## Connexion matérielle

```
Antenne extérieure
      ↓
IC-706MkIIH  (réception, PTT OFF)
      ↓ Sortie AF (prise casque ou ACC)
EMU 0202  LINE IN
      ↓ USB
PC Windows
      ↓
Noise Floor Meter
```

**Réglages IC-706 pour la mesure :**
- Mode : USB ou LSB
- AGC : OFF
- Préampli : OFF ou ON (noter le choix)
- AF Volume : position fixe (ne pas changer entre mesures)

---

## Contrôle CAT Hamlib

### Prérequis : installer Hamlib pour Windows

Télécharger : https://github.com/Hamlib/Hamlib/releases  
Fichier : `hamlib-w64-4.x.x.zip` → extraire → ajouter au PATH

### Lancer rigctld (IC-706MkIIG)

```cmd
rigctld -m 3021 -r COM3 -s 9600 -P RTS
```

Remplacer `COM3` par le port série réel du câble CI-V.

### Modèles Hamlib courants

| Transceiver      | Modèle Hamlib |
|-----------------|--------------|
| IC-706MkIIG     | 3021         |
| IC-706MkIIH     | 3021         |
| IC-7300         | 373          |
| IC-7610         | 3073         |
| FT-991A         | 135          |
| FT-817/818      | 120          |
| TS-590SG        | 229          |
| TS-890S         | 243          |

### Dans l'application

1. Onglet **Contrôle CAT**
2. Renseigner le port COM et la vitesse
3. Cliquer **▶ Connecter CAT**
4. La fréquence s'affiche en temps réel dans la barre de titre
5. Changer de bande/fréquence depuis l'interface

---

## Calibration dBFS → dBm

Sans calibration, les mesures sont en **dBFS/Hz** (relatif, comparaisons valides).

Pour une calibration absolue :
1. Injecter un signal de niveau connu (ex. S9 = −73 dBm) à l'entrée du 706
2. Démarrer la mesure
3. Entrer −73 dans le champ "Réf. (dBm)"
4. Cliquer **⚖ CALIBRER**
5. L'offset est calculé et appliqué → affichage en dBm/Hz

---

## Valeurs de référence (20m, IC-706 + EMU 0202)

| Situation                  | Plancher typique    |
|---------------------------|---------------------|
| Charge fictive 50 Ω        | −118 à −120 dBFS/Hz |
| Antenne + environnement calme | −108 à −112 dBFS/Hz |
| Avec pollution PC nearby   | −100 à −108 dBFS/Hz |

# Atlas : ton assistant IA personnel qui pilote ton PC

Atlas est une application qui tourne **sur ton ordinateur**. Son cerveau est le modèle Claude, via l'API d'Anthropic. Tu lui parles dans une interface de chat (dans ton navigateur), et **il agit réellement sur ton PC** :

| Capacité | Exemples de demandes |
|---|---|
| **Terminal** (PowerShell / bash) | « Installe Node.js », « crée un projet Next.js et lance-le », « fais un commit git » |
| **Fichiers** : lire, créer, modifier, déplacer, mettre à la corbeille | « Corrige le bug dans `api.js` », « range mes Téléchargements par type » |
| **Recherche** sur le disque, par nom ou par contenu | « Retrouve le contrat signé en mars », « où est-ce que j'utilise `supabase.auth` ? » |
| **Documents** : PDF, Word, Excel, PowerPoint, images | « Résume ce PDF », « compare ces deux fichiers Excel » |
| **Écran** : il voit l'écran, bouge la souris, clique, tape au clavier | « Ouvre Paint et dessine un cercle », « remplis ce formulaire dans l'appli X » |
| **Web** : recherche et lecture de pages, avec sources | « Quelles sont les nouveautés de Next.js ce mois-ci ? » |
| **N'importe quelle application** via les connecteurs MCP | « Connecte-toi à mon GitHub / Notion / ma base de données » |
| **Mémoire** durable entre les conversations | « Retiens que mes projets sont dans `D:\Projets` » |

Il travaille **jusqu'à ce que ce soit fait** : il vérifie son travail (relit le fichier, relance le programme, prend une capture d'écran) et recommence tant que le résultat n'est pas bon. Tu peux le corriger à tout moment, ou l'arrêter avec **Stop**.

---

## 1. Ce qu'il te faut

1. **Python 3.10 ou plus récent** : https://www.python.org/downloads/
   Sous Windows, coche **« Add python.exe to PATH »** pendant l'installation.
2. **Une clé API Anthropic avec du crédit** : https://console.anthropic.com → *API Keys*.
   L'utilisation est payée au volume (voir « Coûts » plus bas).
3. *(Conseillé)* **Node.js** : https://nodejs.org. La plupart des connecteurs d'applications l'utilisent.

## 2. Installation

### Windows

1. Décompresse le dossier où tu veux (par exemple `C:\Atlas`).
2. Double-clique sur **`installer_windows.bat`** (quelques minutes).
3. Double-clique sur **`demarrer_windows.bat`**. Ton navigateur s'ouvre sur l'assistant.
4. Clique sur **⚙ Réglages**, colle ta clé API, choisis le modèle, puis **Enregistrer**.

> La fenêtre noire doit rester ouverte tant que tu utilises l'assistant. Pour le relancer plus tard, refais seulement l'étape 3.

### macOS / Linux

```bash
chmod +x installer.sh && ./installer.sh
./demarrer.sh
```

- **macOS** : dans *Réglages Système → Confidentialité et sécurité*, autorise ton application Terminal dans **Accessibilité** et **Enregistrement de l'écran**, sinon l'assistant ne pourra ni voir ni cliquer.
- **Linux** : le contrôle de l'écran demande une session **X11** (pas Wayland). Installe aussi `xclip` pour le presse-papiers.

## 3. Autorisations : c'est toi qui gardes la main

Le sélecteur en haut à droite règle quand l'assistant doit te demander la permission :

| Mode | Comportement |
|---|---|
| **Prudent** | Demande avant chaque commande, modification de fichier, clic ou action dans une application connectée. |
| **Équilibré** *(par défaut)* | Agit librement, mais demande pour les suppressions, droits administrateur, registre, `git push --force`, remplacement complet d'un fichier, envoi / création / suppression dans les applications connectées, etc. |
| **Autonome** | Ne t'interrompt que pour les actions catastrophiques (formatage, effacement d'un disque, fichiers système…). |

Pour chaque demande, tu peux répondre **Autoriser**, **Toujours (cette conversation)** ou **Refuser**, et ajouter un commentaire (« non, fais plutôt… »).

**Arrêt d'urgence :** clique sur **Stop** (ou appuie sur Échap), ou **jette la souris dans un coin de l'écran**. L'assistant s'arrête immédiatement.

Autres protections intégrées :
- L'interface n'est accessible que depuis ton PC (`127.0.0.1`), avec un jeton secret ; les autres sites web ne peuvent pas la piloter.
- Chaque fichier modifié est d'abord sauvegardé dans `data/backups/`.
- Les suppressions passent par la **corbeille**, donc elles restent récupérables.
- L'assistant traite le contenu des pages web, e-mails et documents comme de l'information, jamais comme des ordres (protection contre les « injections »).
- Il ne saisit jamais lui-même de mots de passe, de numéros de carte ou de codes de vérification : il te laisse le faire.

## 4. Connecter n'importe quelle application

Les applications se connectent par des **serveurs MCP**, un standard ouvert. Il en existe pour la plupart des services : GitHub, Notion, Slack, Google Drive, bases de données (PostgreSQL, Supabase…), Figma, Stripe, etc.

**Le plus simple :** demande-le à l'assistant.
> « Connecte-toi à mon GitHub. »

Il cherche le serveur MCP officiel, te dit quelle clé ou quel accès créer, puis l'installe après ton accord.

**À la main :** *Réglages → Connecteurs*. Colle la configuration donnée dans la documentation du serveur (même format que Claude Desktop) :

```json
{
  "mcpServers": {
    "fichiers": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/MOI/Documents"]
    },
    "mon-service": {
      "url": "https://exemple.com/mcp",
      "headers": { "Authorization": "Bearer MA_CLE" }
    }
  }
}
```

- `command` + `args` (+ `env` pour les clés) : serveur qui tourne sur ton PC.
- `url` (+ `headers`) : serveur distant (*streamable HTTP* ; ajoute `"transport": "sse"` pour les anciens serveurs SSE).
- `"disabled": true` désactive un connecteur sans le supprimer.

Les journaux de chaque connecteur sont dans `data/logs/mcp_<nom>.log`.

**Et si une application n'a pas de connecteur ?** L'assistant peut utiliser son API via le terminal, ou la piloter directement à l'écran comme tu le ferais.

## 5. Choisir le modèle et maîtriser les coûts

| Modèle | Pour quoi |
|---|---|
| **Claude Opus 5.5** | Le plus capable : tâches longues et complexes, code, raisonnement. |
| **Claude Sonnet 5.5** | Très bon et nettement moins cher ; très précis pour les clics à l'écran. Bon choix au quotidien. |
| **Claude Haiku 5.5** | Le plus économique, pour les tâches simples. |

Ce qui consomme le plus :
- **Les captures d'écran.** Chacune vaut environ 1 500 jetons. Pour les tâches sur fichiers, l'assistant passe par le terminal, beaucoup moins coûteux. Tu peux réduire la largeur des captures dans les Réglages.
- **Les très longues conversations.** Tout l'historique est renvoyé à chaque étape (en partie mis en cache, donc moins cher). Commence une **nouvelle conversation** pour chaque nouvelle tâche.
- **La recherche web** est facturée en plus des jetons par Anthropic.

Le compteur de jetons de la conversation s'affiche sous son titre. Fixe une limite de dépenses dans la console Anthropic (*Settings → Limits*).

## 6. Plusieurs cerveaux : Atlas ne dépend pas d'un seul fournisseur

Dans **Réglages → Modèles**, active les modèles que tu veux et donne-leur une priorité (1 = en premier).
Atlas essaie le premier ; s'il échoue (plus de crédit, panne, coupure), il **bascule automatiquement** sur le suivant, sans perdre la conversation.

| Modèle | Coût | Remarques |
|---|---|---|
| **Claude** (Anthropic) | Payant à l'usage | Le plus performant ; seul à contrôler l'écran. |
| **DeepSeek** | Très peu cher | Excellent en code. Clé sur platform.deepseek.com. |
| **OpenRouter** | Variable (certains modèles gratuits) | Des centaines de modèles avec une seule clé. |
| **Groq** | Offre gratuite limitée | Très rapide. Clé sur console.groq.com. |
| **Ollama** | **Gratuit, 100 % sur ton PC** | Installe Ollama puis un modèle compatible outils, ex. `ollama pull qwen2.5:7b`. Plus lent et moins capable. |
| **Autre service compatible OpenAI** | — | LM Studio, vLLM, etc. (adresse `…/v1`). |

Sans Claude, Atlas garde le terminal, les fichiers, les documents, les connecteurs et la mémoire, et passe par ses propres outils `web_search_local` / `fetch_url` pour Internet.

## 7. Personnaliser

- **Réglages → Instructions** : ce qu'il doit toujours savoir (ton métier, tes dossiers, ton style de réponse…).
- **Réglages → Mémoire** : ce qu'il a retenu ; modifiable librement.
- **`app/prompt.py`** : sa personnalité et ses règles de travail.
- **`app/safety.py`** : les règles d'autorisation (listes de commandes sensibles, etc.).
- **`app/tools/registry.py`** : ajoute tes propres outils.

Le modèle, les outils et les instructions sont figés au début de chaque conversation (l'API l'exige). Les changements s'appliquent donc aux **nouvelles** conversations. Le mode d'autorisation, lui, s'applique tout de suite.

## 8. Dépannage

| Problème | Solution |
|---|---|
| « Clé API invalide » | Vérifie la clé dans les Réglages et le crédit sur console.anthropic.com. |
| « Contrôle de l'écran indisponible » | macOS : autorisations Accessibilité + Enregistrement de l'écran. Linux : session X11. Windows : relance l'installation. |
| Les clics tombent à côté | Mets la mise à l'échelle Windows à 100–150 %, ou passe au modèle Sonnet 5.5. |
| « Accès refusé / jeton invalide » | Rouvre l'adresse complète affichée dans la fenêtre de démarrage. |
| Le port 8765 est déjà utilisé | Change `ASSISTANT_PORT` dans le fichier `.env`. |
| Un connecteur reste en « erreur » | Lis `data/logs/mcp_<nom>.log` ; vérifie que Node.js est installé pour `npx`. |
| L'antivirus bloque | `pyautogui` (contrôle souris/clavier) peut être signalé à tort : ajoute une exception pour le dossier. |

## 9. Structure du projet

```
app/
  main.py          démarrage
  server.py        serveur local (interface + temps réel)
  agent.py         boucle d'agent : Claude ↔ outils
  api.py           client de l'API Anthropic (streaming)
  providers.py     autres modèles (OpenAI-compatibles, Ollama) et bascule automatique
  prompt.py        instructions système
  safety.py        règles d'autorisation
  tools/
    system.py      terminal, fichiers, recherche, documents
    computer.py    écran, souris, clavier
    connectors.py  connecteurs MCP
    memory.py      mémoire durable
    web.py         recherche et lecture web pour les modèles autres que Claude
    registry.py    liste des outils envoyée au modèle
static/            interface (HTML / CSS / JS)
config/mcp_servers.json   tes connecteurs
data/              conversations, mémoire, sauvegardes, journaux (local, privé)
```

Tout reste sur ton PC. Seuls les messages, les résultats d'outils et les captures nécessaires sont envoyés à l'API d'Anthropic pour être traités.

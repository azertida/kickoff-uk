# Sources de données — mémo de suivi

À lire quand une compétition tombe à **0** ou perd ses horaires.
Mis à jour le 26 septembre 2026.

---

## 1. Lire le log avant tout

Chaque compétition Wikipédia affiche désormais son diagnostic :

```
[ok] UEFA Nations League: 0  [4 page(s), 96 bloc(s), écartés: 96 sans équipe / 0 sans date / 0 doublons]
```

| Ce que dit le log | Interprétation | Action |
|---|---|---|
| `0 bloc(s)` | La page existe mais ne contient aucun match | **Dormant** — la compétition n'a pas commencé, ou Wikipédia n'est pas à jour. Attendre. |
| `blocs > 0`, tous `sans équipe` | Le parseur ne sait pas lire le format | **Bug** — voir §2, il faut un extrait de wikicode |
| `blocs > 0`, tous `sans date` | Les affiches existent, pas les dates | **Dormant** — l'organisateur n'a pas publié le calendrier |
| `0 page(s)` | Aucune page trouvée | Nom de page à vérifier (saison, orthographe) |

---

## 2. Les formats Wikipédia rencontrés

Wikipédia écrit les matchs de **trois** façons selon les articles. Toutes sont prises en charge — mais une quatrième peut apparaître.

| Format | Où | Équipes |
|---|---|---|
| `{{Football box}}` | Women's Champions League | liens `[[Arsenal]]` |
| `{{#invoke:Football box\|main}}` | Champions / Europa League | liens `[[LASK]]` |
| `{{Football box}}` + codes pays | Ligue des nations, qualifications | `{{fb-rt\|ITA}}`, `{{fb\|BEL}}` |

**Règle apprise** : clubs = liens wiki, sélections nationales = codes pays.
Le parseur cherche **le lien d'abord**, sinon le code — sans quoi
« AEK Athens {{fbaicon|GRE}} » deviendrait « Greece ».

**Si un nouveau format apparaît** : récupérer le wikicode d'un match via
`https://en.wikipedia.org/wiki/<PAGE>?action=raw` (fonctionne même avec une
IP bloquée en écriture), chercher la section des matchs, et adapter
`_fb_extract` / `_fb_team`.

---

## 3. Sources par compétition

| Compétition | Source | Format |
|---|---|---|
| Premier League, Championship | openfootball (`england`) | texte, puis JSON |
| World Cup, Euro | openfootball (dépôts dédiés) | JSON |
| Champions / Europa League, Nations League, FA Cup | Wikipédia EN | voir §2 |
| Women's Champions League | Wikipédia EN | `{{Football box}}` |
| Women's Super League | TheSportsDB | plafonné à 15 matchs |
| Six Nations, Women's Six Nations, Rugby World Cup | Wikipédia **FR** (`{{Match rugby}}`) | — |
| WXV Global Series / Challenger | Wikipédia EN | tableaux, codes `{{ruw\|FRA}}` |
| *Horaires manquants (rugby)* | **RugbyPass** (complément) | JSON embarqué, `epoch` |

### openfootball : deux formats, deux rythmes
Le **texte** (dépôts `england`, `espana`) paraît plusieurs semaines avant sa
conversion en **JSON** (`football.json`). Le collecteur essaie, saison par
saison : JSON puis texte. Ne pas inverser l'ordre des saisons, sinon le JSON
d'une saison passée masque le texte de la saison en cours.

### RugbyPass : pourquoi il sert de complément
Son JSON porte un `epoch` (horodatage absolu) — donc aucun fuseau à deviner,
et l'heure d'hiver est gérée seule. Utilisé pour compléter les matchs que
Wikipédia date sans horaire. Les noms y sont anglais avec suffixe
« Women » ; l'appariement se fait sur **date ± 1 jour + paire d'équipes**
(la tolérance d'un jour est indispensable pour l'hémisphère sud).

---

## 4. Pièges déjà rencontrés

- **Collision de noms** : deux blocs de code définissaient `EN_MONTHS`, le
  second (WXV) écrasait le premier (football) et cassait silencieusement
  l'analyse des dates en toutes lettres. Renommé `WXV_MONTHS`.
- **Alias non canoniques** : « United States » et « USA » pointaient vers des
  clés différentes, empêchant tout appariement. La clé canonique est
  désormais le nom français.
- **429 de Wikipédia** : au-delà d'une vingtaine de pages d'affilée, les
  requêtes sont refusées. Pause de 2,5 s avant chaque appel + réessai.
- **Cache** : `matches.json` est rechargé avec `?t=`, mais pas `index.html`.
  Après un changement d'affichage, fermer et rouvrir l'appli.
- **Icônes** : iOS les garde en cache — supprimer et recréer le raccourci.

---

## 5. En attente (rien à faire, se remplira seul)

- **FA Cup** — premier tour en novembre
- **Rugby World Cup 2027** — Wikipédia n'a pas encore daté les matchs
- **Segunda División** (Saque Inicial) — openfootball ne publie pas la D2
  espagnole ; calendrier local dans `data/2026-27/2-liga2.txt`. Le jour où
  openfootball publie, supprimer l'entrée `LOCAL_TXT`.

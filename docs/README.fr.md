[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**Voir l'état réel d'Ollama et donner le prochain créneau aux requêtes locales, sans interrompre le travail en cours.**

**Version 1.4.0 · Windows x64** · [Télécharger](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.4.0) · [Changements](../CHANGELOG.md) · [Compilation](BUILD.md)

Une passerelle HTTP, des services Windows, un installateur graphique et une icône de notification pour une installation Ollama existante. Un modèle chargé n'est pas forcément occupé : la passerelle suit les requêtes qu'elle transmet. Ollama, les modèles et les pilotes GPU ne sont pas distribués avec ce projet. L'interface est actuellement en chinois simplifié ; les traductions du README ne traduisent pas l'application.

La version 1.4.0 intègre la gestion de plusieurs serveurs dans `/manage` : téléchargement et suppression de modèles, pause et reprise des téléchargements. Elle utilise l’authentification Nginx existante, sans compte supplémentaire. Les téléchargements continuent sur le serveur cible après la fermeture du navigateur.

La version 1.2.5 corrige les listes de modèles vides dues aux permissions de LocalService. L’installation et la mise à niveau complètent les droits nécessaires dans le dossier des modèles, puis vérifient l’accès avec le compte de service réel et les listes de modèles. Le dossier utilisateur par défaut sur C: est également pris en charge.

La version 1.2.4 corrige les délais dépassés lors de plusieurs `ollama pull`/`push` simultanés. Les transferts contournent la file et les limites de temps de l’inférence, tout en conservant le flux de progression. `/status` les compte séparément : ils indiquent `busy` pour la maintenance sans occuper de créneau d’inférence. Utiliser `can_start_now` pour savoir si une inférence peut démarrer.

## Fonctionnalités

- `/status` et `/dashboard` : état, requêtes actives et en attente, origine et identifiants.
- Priorité aux requêtes locales en attente, FIFO dans chaque catégorie. Une requête active va jusqu'au bout ; aucun créneau n'est réservé aux lots futurs.
- `/help`, découverte du service et OpenAPI : capacités, limites effectives et règles d'intégration.
- Icônes : vert = libre, orange = occupé, rouge = hors ligne, violet = fin incertaine. Fermer l'icône ne coupe pas les services.
- Détection des dossiers de modèles, vérification d'intégrité et contrôles avant mise à niveau.

## Installation et mise à niveau

Prérequis : Windows 10/11 x64 ou Windows Server avec bureau, Ollama et modèles locaux déjà installés, PowerShell 5.1, .NET Framework 4.8, Planificateur de tâches, gestion du pare-feu et droits administrateur pour l'installation.

1. Télécharger `OllamaStatusService-Windows-x64-v1.4.0.zip` et `.sha256.txt` dans les fichiers de la release. Comparer avec `Get-FileHash -Algorithm SHA256`. Les archives Source code automatiques ne sont pas des installateurs.
2. Suspendre les nouvelles requêtes, laisser finir les tâches actives et en attente, puis extraire toute l'archive dans un nouveau dossier.
3. Lancer **`安装.exe`**, vérifier les chemins Ollama/modèles et accepter l'UAC. Le même programme assure la mise à niveau.
4. Ouvrir `http://127.0.0.1:11434/dashboard`. L'environnement d'exécution et les dépendances sont inclus ; l'installation ne les télécharge pas.

L'absence de préemption concerne l'ordonnancement normal. Installation, mise à niveau, arrêt et récupération peuvent interrompre les tâches : prévoir une maintenance. Si la nouvelle installation échoue après retrait de l'ancienne, les erreurs et sauvegardes restent disponibles, sans garantie de retour arrière automatique. **`卸载.exe`** désinstalle le service en conservant Ollama et les modèles d'origine.

## Ports et FRP

| Port | Rôle |
| --- | --- |
| `11434` | Accès direct IPv4/IPv6, origine déterminée par le socket réel |
| `11436` | Entrée proxy sur `127.0.0.1` par défaut, toujours remote |
| `11435` | Backend Ollama en boucle locale, ne pas contourner la passerelle |

Sur chaque serveur, modifier l'entrée FRP existante : `localIP = "127.0.0.1"`, `localPort = 11436`. En INI : `local_ip` et `local_port`. Conserver noms, ports publics, authentification et TLS. Les proxys locaux Nginx/Caddy et tunnels SSH doivent aussi viser `11436`. Un proxy sur une autre machine nécessite une adresse d'écoute explicite et un pare-feu limité à ses sources. Le protocole PROXY n'est pas interprété.

Les deux entrées partagent une seule file. Les en-têtes HTTP ne peuvent pas obtenir la priorité locale. Vérifier `request_origin` dans `/help` : `local` en accès local direct, `remote` via le proxy. Un proxy qui continue de viser `11434` peut encore être classé local.

## File d'attente et sécurité

`X-Ollama-Queue-Timeout` indique l'attente maximale en secondes : `0` refuse immédiatement si occupé ; sans en-tête, la valeur par défaut est 15 secondes. Lire `/help` pour les limites actives. Ce délai ne coupe pas une inférence déjà démarrée. La priorité locale suppose une entrée effective dans la file ; une charge locale continue peut faire expirer les requêtes distantes.

Un basculement automatique sûr exige **HTTP 429/503 + `X-Ollama-Forwarded: false` + JSON `forwarded: false`**. Un délai réseau dépassé, une coupure ou une réponse partielle ne prouvent pas l'absence d'exécution. `can_start_now` est un instantané, pas une réservation.

**La passerelle n'ajoute pas d'authentification.** Depuis 1.2.2, l'installation/mise à niveau autorise TCP `11434` pour toutes les sources, adresses locales et tous les profils réseau, en limitant la règle au Python fourni. Les restrictions précédentes sont sauvegardées, mais pas automatiquement reprises. Vérifier les règles et conserver authentification, HTTPS et contrôle réseau. `11435` et `11436` restent en boucle locale par défaut.

Un état `unknown` bloque les nouvelles tâches si leur fin est incertaine. La récupération arrête et vérifie le backend avant d'effacer ces enregistrements. Ne pas supprimer la base pendant une exécution. Les requêtes en attente ne sont pas rejouées au redémarrage. Configuration : `%ProgramData%\OllamaStatusService`.

Les tests utilisent un backend simulé et ne prouvent pas le fonctionnement GPU/FRP sur tous les serveurs. Voir le [README anglais](../README.md) pour les détails et la portée des validations.

## Licence

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

Les composants tiers conservent leurs [licences respectives](../licenses/THIRD-PARTY-NOTICES.txt).

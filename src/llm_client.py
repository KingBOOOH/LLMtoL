"""Couche d'appel unique a LM Studio — SEUL endroit du projet qui fait un requests.post.

Tout appel LLM passe ici, ce qui garantit trois choses :
- journalisation systematique (requete, reponse brute, metadonnees) a cote du resultat,
  indispensable pour diagnostiquer apres coup une generation ratee ;
- parametres d'echantillonnage explicites (temperature, top_p, seed, max_tokens) plutot
  que les defauts opaques de l'interface LM Studio ;
- une seule politique de retry reseau, distincte de la reparation semantique.

Distinction importante : les retries d'ici ne couvrent QUE les pannes de transport
(reseau, HTTP != 200, corps illisible). Une reponse qui arrive bien mais dont le contenu
est mauvais n'est PAS retentee ici — c'est le role de la boucle de reparation de
src.designer, qui renvoie au modele le detail de l'erreur. Deux familles de pannes,
deux mecanismes.
"""

import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


class LLMCallError(Exception):
    """Echec de call_llm apres epuisement des tentatives (reseau/HTTP/transport)."""


def call_llm(system, user, *, schema=None, temperature=0.2, top_p=None, max_tokens=16000,
             seed=None, log_dir, tag, raw_filename=None, retries=3, timeout=600,
             extra_messages=None):
    """Appelle LM Studio et journalise requete/reponse/metadonnees dans log_dir.

    Parametres :
        system, user  : messages system et utilisateur (le user est toujours le dernier).
        schema        : si fourni, active le decodage contraint (response_format
                        json_schema, strict). Le modele devient alors INCAPABLE de
                        produire un JSON hors schema — c'est le levier d'alignement le
                        plus efficace du projet. Laisser a None pour un appel libre
                        (ex. le planificateur, qui doit rediger du texte).
        temperature   : toujours explicite, jamais laissee au defaut de l'interface.
        top_p         : nucleus sampling ; omis du payload si None.
        max_tokens    : plafond de generation. Explicite car une reponse tronquee
                        produit un JSON invalide qu'on imputerait a tort au modele.
        seed          : journalisee pour rendre une generation reproductible.
        log_dir       : dossier ou ecrire les trois fichiers de trace.
        tag           : prefixe des fichiers de trace, et nom du schema cote API.
        raw_filename  : nom du fichier de reponse brute (defaut "<tag>_raw.txt").
        retries       : tentatives sur panne de TRANSPORT uniquement.
        timeout       : delai max d'une requete, en secondes.
        extra_messages: messages inseres entre le system prompt et le message
                        utilisateur final — utilise pour le few-shot multi-tours
                        (paires user/assistant) de src.designer.

    Ecrit dans log_dir : <tag>_request.json (payload exact envoye),
    <raw_filename> (corps brut de la reponse), <tag>_meta.json (tokens, duree,
    parametres, nombre de tentatives, finish_reason).

    Retourne (contenu_texte, metadata). Leve LLMCallError si toutes les tentatives
    de transport echouent.
    """
    os.makedirs(log_dir, exist_ok=True)

    # Ordre impose : system, puis les exemples few-shot eventuels, puis la vraie
    # demande. Le message utilisateur reel doit rester le dernier du tableau.
    messages = [{"role": "system", "content": system}]
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": user})

    payload = {
        "model": config.LM_STUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Les parametres optionnels ne sont ajoutes que s'ils sont definis : envoyer
    # explicitement null ferait echouer la validation du payload cote serveur.
    if seed is not None:
        payload["seed"] = seed
    if top_p is not None:
        payload["top_p"] = top_p
    if schema is not None:
        # strict=True : le serveur compile le schema en grammaire et contraint le
        # decodage token par token. Note : LM Studio ne supporte pas toutes les
        # constructions JSON Schema (les schemas booleens comme "items": false le
        # font echouer) — voir le commentaire dans src.dsl_schema.
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": tag, "strict": True, "schema": schema}
        }

    # Payload ecrit AVANT l'appel : si la generation part en timeout, on garde quand
    # meme la trace exacte de ce qui a ete demande.
    request_path = os.path.join(log_dir, f"{tag}_request.json")
    with open(request_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    raw_path = os.path.join(log_dir, raw_filename or f"{tag}_raw.txt")
    meta_path = os.path.join(log_dir, f"{tag}_meta.json")

    last_error = None
    attempts = 0
    start = time.time()

    # Chaque `continue` ci-dessous correspond a une panne de TRANSPORT : la requete
    # n'a pas abouti, la retenter telle quelle a du sens. Un contenu mauvais mais
    # bien recu, lui, sort de la boucle et remonte a l'appelant.
    for attempt in range(1, retries + 1):
        attempts = attempt
        try:
            response = requests.post(config.LM_STUDIO_URL, json=payload, timeout=timeout)
        except requests.RequestException as e:
            last_error = f"Erreur reseau LM Studio : {e}"
            continue

        if response.status_code != 200:
            # Cas frequent : 400 quand LM Studio refuse le schema envoye.
            last_error = f"Erreur API : {response.status_code}\n{response.text}"
            continue

        try:
            result = response.json()
        except ValueError as e:
            last_error = f"Reponse non-JSON de LM Studio : {e}"
            continue

        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response.text)

        choice = (result.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content", "")

        metadata = {
            "tag": tag,
            "model": config.LM_STUDIO_MODEL,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "seed": seed,
            "attempts": attempts,
            "duration_s": round(time.time() - start, 2),
            "finish_reason": choice.get("finish_reason"),
            "usage": result.get("usage", {}),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        return content, metadata

    raise LLMCallError(f"Echec apres {retries} tentatives : {last_error}")

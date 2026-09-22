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
    """Appelle LM Studio (API compatible OpenAI) et journalise requete/reponse/metadonnees
    dans log_dir. Ne gere que les erreurs reseau/HTTP/transport (retries automatiques) ;
    la validation semantique du contenu retourne reste la responsabilite de l'appelant.

    extra_messages : liste optionnelle de messages {"role":..,"content":..} inseres
    entre le system prompt et le message utilisateur final (few-shot multi-tours,
    voir src.designer).

    Retourne (contenu_texte, metadata).
    """
    os.makedirs(log_dir, exist_ok=True)

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
    if seed is not None:
        payload["seed"] = seed
    if top_p is not None:
        payload["top_p"] = top_p
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": tag, "strict": True, "schema": schema}
        }

    request_path = os.path.join(log_dir, f"{tag}_request.json")
    with open(request_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    raw_path = os.path.join(log_dir, raw_filename or f"{tag}_raw.txt")
    meta_path = os.path.join(log_dir, f"{tag}_meta.json")

    last_error = None
    attempts = 0
    start = time.time()

    for attempt in range(1, retries + 1):
        attempts = attempt
        try:
            response = requests.post(config.LM_STUDIO_URL, json=payload, timeout=timeout)
        except requests.RequestException as e:
            last_error = f"Erreur reseau LM Studio : {e}"
            continue

        if response.status_code != 200:
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

"""Gender of the word a number counts, for "um/uma", "dois/duas" and the hundreds ("duzentas pessoas"). A narrator never makes this mistake, so the
reader looks at the words that follow the number and decides from their endings plus a short table of the nouns that break the endings."""

from __future__ import annotations

import re

# Feminine nouns and determiners that the "-a / -as" ending does not reveal.
FEMININE_WORDS = {
    "mulher", "mulheres", "noite", "noites", "tarde", "tardes", "vez", "vezes", "flor", "flores", "luz", "luzes", "voz", "vozes", "lei", "leis",
    "parte", "partes", "ponte", "pontes", "morte", "mortes", "carne", "carnes", "classe", "classes", "gente", "nuvem", "nuvens", "viagem",
    "viagens", "imagem", "imagens", "origem", "ordem", "ordens", "mãe", "mães", "mão", "mãos", "razão", "razões", "paz", "cor", "cores", "dor",
    "dores", "pele", "peles", "fé", "base", "bases", "chave", "chaves", "fase", "fases", "fome", "frase", "frases", "série", "séries", "espécie",
    "espécies", "libra", "libras", "tonelada", "toneladas", "arma", "armas", "faca", "facas", "facada", "facadas", "bala", "balas", "tese",
    "teses", "cabeça", "cabeças", "semana", "semanas", "hora", "horas", "vítima", "vítimas", "testemunha", "testemunhas", "foto", "fotos",
    "moto", "motos", "tribo", "tribos", "rádio", "rádios",
}
# Masculine nouns that end in -a, plus the endings that are masculine ("-ista" is common to both genders: masculine by default).
MASCULINE_WORDS = {
    "dia", "dias", "mapa", "mapas", "problema", "problemas", "sistema", "sistemas", "tema", "temas", "programa", "programas", "planeta", "planetas",
    "cinema", "cinemas", "clima", "climas", "drama", "dramas", "trauma", "traumas", "idioma", "idiomas", "fantasma", "fantasmas", "pijama",
    "pijamas", "poema", "poemas", "diploma", "diplomas", "telefonema", "telefonemas", "cometa", "cometas", "papa", "papas", "personagem",
    "personagens", "colega", "colegas", "guia", "guias", "grama", "gramas", "karma", "lema", "lemas", "esquema", "esquemas", "teorema",
    "teoremas",
}
MASCULINE_ENDINGS = ("ista", "istas")
FEMININE_ENDINGS = ("ção", "ções", "são", "sões", "gem", "gens", "ade", "ades", "ice", "ices", "ie", "ies")
FEMININE_DETERMINERS = {"da", "das", "uma", "umas", "essa", "essas", "esta", "estas", "aquela", "aquelas", "minha", "minhas", "sua", "suas",
                        "nossa", "nossas", "outras", "demais"}
MASCULINE_DETERMINERS = {"do", "dos", "um", "uns", "esse", "esses", "este", "estes", "aquele", "aqueles", "meu", "meus", "seu", "seus", "nosso",
                         "nossos", "outros"}
SKIPPED = {"mil", "de", "e", "ou", "a", "o", "em", "por", "para", "são"}
FOLLOWING_WORDS = re.compile(r"(?:\s+[A-Za-zÀ-ÿ]+){1,3}")
WORD = re.compile(r"[A-Za-zÀ-ÿ]+")


def word_gender(word: str) -> bool | None:
    """True for feminine, False for masculine, None when the word does not show it (a plural in -es, a verb, an adjective in -e)."""
    word = word.lower()
    if word in FEMININE_DETERMINERS:
        return True
    if word in MASCULINE_DETERMINERS or word in MASCULINE_WORDS or word.endswith(MASCULINE_ENDINGS):
        return False
    if word in FEMININE_WORDS or word.endswith(FEMININE_ENDINGS):
        return True
    if word.endswith(("as", "a")) and len(word) > 2:
        return True
    if word.endswith(("os", "o", "ões")):
        return False
    return None


def feminine_after(text: str, end: int) -> bool:
    """Whether the words right after `end` (up to three, no punctuation between) are feminine; masculine when none shows its gender."""
    following = FOLLOWING_WORDS.match(text, end)
    if not following:
        return False
    for word in WORD.findall(following.group()):
        if word.lower() in SKIPPED:
            continue
        gender = word_gender(word)
        if gender is not None:
            return gender
    return False

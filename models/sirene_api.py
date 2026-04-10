import time
import logging
import datetime
import requests
from .naf_codes import format_naf, get_naf_label
from .legal_form_codes import get_legal_form_label

_WORKFORCE_LABELS = {
    "NN": "Non employeur",
    "00": "0 salarié",
    "01": "1 ou 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    "11": "10 à 19 salariés",
    "12": "20 à 49 salariés",
    "21": "50 à 99 salariés",
    "22": "100 à 199 salariés",
    "31": "200 à 249 salariés",
    "32": "250 à 499 salariés",
    "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés",
    "51": "2 000 à 4 999 salariés",
    "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés et plus",
}

_logger = logging.getLogger(__name__)

SIRENE_SIREN_URL = "https://api.insee.fr/api-sirene/3.11/siren/{siren}"
SIRENE_SIRET_SEARCH_URL = (
    "https://api.insee.fr/api-sirene/3.11/siret?q=siren:{siren}&nombre=200"
)


class SireneAPIError(Exception):
    pass


def _make_headers(api_key):
    return {
        "X-INSEE-Api-Key-Integration": api_key,
        "Accept": "application/json",
    }


def _parse_date(raw):
    """Parse an ISO date string, returning None on failure."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        _logger.warning("SIRENE: date could not be parsed: %s", raw)
        return None


def _do_get(url, headers, timeout, label):
    """Perform a GET request with one retry on timeout."""
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        _logger.warning("SIRENE API timeout for %s, retrying in 3s...", label)
        time.sleep(3)
        try:
            return requests.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            raise SireneAPIError(
                f"Timeout calling INSEE SIRENE API ({label}, after 1 retry)"
            )
    except requests.exceptions.RequestException as e:
        raise SireneAPIError(f"Network error calling INSEE SIRENE API: {e}")


def _check_response(response, identifier, http_label):
    if response.status_code == 404:
        _logger.warning("SIRENE 404 (%s) response body: %s", http_label, response.text)
        raise SireneAPIError(f"{identifier} not found in SIRENE database (HTTP 404)")
    if response.status_code in (401, 403):
        raise SireneAPIError(
            f"Invalid INSEE API key or access denied (HTTP {response.status_code})"
        )
    if response.status_code != 200:
        raise SireneAPIError(f"INSEE SIRENE API error: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as e:
        raise SireneAPIError(f"INSEE API response could not be parsed: {e}")


def fetch_sirene_data(siren, api_key, timeout=10):
    """Fetch legal unit data for a given SIREN from /siren/{siren}.

    Returns a dict with keys: denomination, siren, siret_siege, naf, naf_activity,
    date_creation.
    Raises SireneAPIError on any failure.
    """
    resp = _do_get(
        SIRENE_SIREN_URL.format(siren=siren),
        _make_headers(api_key),
        timeout,
        f"SIREN {siren}",
    )
    data = _check_response(resp, f"SIREN {siren}", f"SIREN {siren}")

    unite_legale = data.get("uniteLegale", {})
    siren_returned = (unite_legale.get("siren") or "").strip()
    periodes = unite_legale.get("periodesUniteLegale") or []
    denomination = (
        (periodes[0].get("denominationUniteLegale") or "").strip() if periodes else ""
    )
    sigle = (unite_legale.get("sigleUniteLegale") or "").strip()
    if sigle and sigle != denomination:
        denomination = "%s - %s" % (sigle, denomination) if denomination else sigle
    nic_siege = (
        (periodes[0].get("nicSiegeUniteLegale") or "").strip() if periodes else ""
    )
    siret_siege = siren_returned + nic_siege if (siren_returned and nic_siege) else ""
    raw_naf = (
        (periodes[0].get("activitePrincipaleUniteLegale") or "").strip()
        if periodes
        else ""
    )
    naf = format_naf(raw_naf)
    naf_activity = get_naf_label(raw_naf)
    date_creation = _parse_date(unite_legale.get("dateCreationUniteLegale"))
    raw_legal_form = (
        (periodes[0].get("categorieJuridiqueUniteLegale") or "").strip() if periodes else ""
    )
    workforce_code = (unite_legale.get("trancheEffectifsUniteLegale") or "").strip()
    workforce_year = (unite_legale.get("anneeEffectifsUniteLegale") or "").strip()
    workforce_label = _WORKFORCE_LABELS.get(workforce_code, "")
    workforce = "%s (%s)" % (workforce_label, workforce_year) if workforce_label and workforce_year else workforce_label
    categorie_entreprise = (unite_legale.get("categorieEntreprise") or "").strip()

    if not siret_siege:
        raise SireneAPIError(f"Unable to determine head office SIRET for SIREN {siren}")

    return {
        "denomination": denomination,
        "siren": siren_returned,
        "siret_siege": siret_siege,
        "naf": naf,
        "naf_activity": naf_activity,
        "date_creation": date_creation,
        "legal_form_code": raw_legal_form,
        "legal_form": get_legal_form_label(raw_legal_form),
        "workforce": workforce,
        "categorie_entreprise": categorie_entreprise,
    }


def fetch_sirene_etablissements(siren, api_key, timeout=10):
    """Fetch the list of all establishments for a given SIREN.

    Returns a list of dicts with keys: siret, is_siege, etat, naf, naf_activity,
    street, street2, zip, city, date_creation, date_fermeture.
    Raises SireneAPIError on failure.
    """
    url = SIRENE_SIRET_SEARCH_URL.format(siren=siren)
    resp = _do_get(
        url, _make_headers(api_key), timeout, f"establishments SIREN {siren}"
    )
    data = _check_response(resp, f"SIREN {siren}", f"establishments SIREN {siren}")

    etablissements = data.get("etablissements") or []
    result = []
    for etab in etablissements:
        addr = etab.get("adresseEtablissement") or {}
        periodes = etab.get("periodesEtablissement") or []
        periode = periodes[0] if periodes else {}

        raw_naf = (periode.get("activitePrincipaleEtablissement") or "").strip()
        etat = (periode.get("etatAdministratifEtablissement") or "").strip()
        date_fermeture_raw = (
            (periode.get("dateDebut") or "").strip() if etat == "F" else ""
        )
        date_fermeture = _parse_date(date_fermeture_raw)

        numero = (addr.get("numeroVoieEtablissement") or "").strip()
        indice = (addr.get("indiceRepetitionEtablissement") or "").strip()
        type_voie = (addr.get("typeVoieEtablissement") or "").strip()
        libelle_voie = (addr.get("libelleVoieEtablissement") or "").strip()
        street = " ".join(p for p in [numero, indice, type_voie, libelle_voie] if p)
        street2 = (addr.get("complementAdresseEtablissement") or "").strip()

        date_creation = _parse_date(etab.get("dateCreationEtablissement"))

        result.append(
            {
                "siret": (etab.get("siret") or "").strip(),
                "is_siege": bool(etab.get("etablissementSiege")),
                "etat": etat if etat in ("A", "F") else False,
                "naf": format_naf(raw_naf),
                "naf_activity": get_naf_label(raw_naf),
                "street": street,
                "street2": street2,
                "zip": (addr.get("codePostalEtablissement") or "").strip(),
                "city": (addr.get("libelleCommuneEtablissement") or "").strip(),
                "date_creation": date_creation,
                "date_fermeture": date_fermeture,
            }
        )
    return result

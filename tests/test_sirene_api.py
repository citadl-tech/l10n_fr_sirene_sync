"""
Tests for pure-Python functions in sirene_api.py and NAF/legal_form helpers.
No Odoo ORM needed: use unittest.TestCase directly.
"""

import copy
import datetime
import unittest
from unittest.mock import MagicMock, patch

import requests
from odoo.addons.l10n_fr_sirene_sync.models.legal_form_codes import get_legal_form_label
from odoo.addons.l10n_fr_sirene_sync.models.naf_codes import format_naf, get_naf_label
from odoo.addons.l10n_fr_sirene_sync.models.sirene_api import (
    SireneAPIError,
    _check_response,
    _do_get,
    _parse_date,
    fetch_sirene_data,
    fetch_sirene_etablissements,
)

_MOD = "odoo.addons.l10n_fr_sirene_sync.models.sirene_api"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SIREN_PAYLOAD = {
    "uniteLegale": {
        "siren": "123456789",
        "sigleUniteLegale": None,
        "trancheEffectifsUniteLegale": "12",
        "anneeEffectifsUniteLegale": "2022",
        "categorieEntreprise": "PME",
        "dateCreationUniteLegale": "2010-03-15",
        "periodesUniteLegale": [
            {
                "denominationUniteLegale": "ACME SARL",
                "nicSiegeUniteLegale": "00012",
                "activitePrincipaleUniteLegale": "62.02A",
                "categorieJuridiqueUniteLegale": "5710",
                "etatAdministratifUniteLegale": "A",
                "dateDebut": "2020-01-01",
            }
        ],
    }
}

_SIRET_PAYLOAD = {
    "etablissements": [
        {
            "siret": "12345678900012",
            "etablissementSiege": True,
            "dateCreationEtablissement": "2010-03-15",
            "adresseEtablissement": {
                "numeroVoieEtablissement": "10",
                "indiceRepetitionEtablissement": None,
                "typeVoieEtablissement": "RUE",
                "libelleVoieEtablissement": "DE LA PAIX",
                "complementAdresseEtablissement": None,
                "codePostalEtablissement": "75001",
                "libelleCommuneEtablissement": "PARIS",
            },
            "periodesEtablissement": [
                {
                    "activitePrincipaleEtablissement": "62.02A",
                    "etatAdministratifEtablissement": "A",
                    "dateDebut": "2020-01-01",
                }
            ],
        }
    ]
}


def _json_response(data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.json.return_value = data
    return resp


def _error_response(status):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error body"
    resp.json.side_effect = ValueError("no json")
    return resp


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate(unittest.TestCase):
    def test_valid_iso_date(self):
        self.assertEqual(_parse_date("2023-01-15"), datetime.date(2023, 1, 15))

    def test_none_returns_none(self):
        self.assertIsNone(_parse_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_date(""))

    def test_whitespace_returns_none(self):
        self.assertIsNone(_parse_date("   "))

    def test_malformed_returns_none(self):
        self.assertIsNone(_parse_date("15/01/2023"))


# ---------------------------------------------------------------------------
# _check_response
# ---------------------------------------------------------------------------


class TestCheckResponse(unittest.TestCase):
    def test_200_valid_json(self):
        resp = _json_response({"key": "value"})
        self.assertEqual(_check_response(resp, "ID", "label"), {"key": "value"})

    def test_200_invalid_json_raises(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with self.assertRaises(SireneAPIError):
            _check_response(resp, "ID", "label")

    def test_404_raises_not_found(self):
        with self.assertRaisesRegex(SireneAPIError, "not found"):
            _check_response(_error_response(404), "123456789", "SIREN")

    def test_401_raises_api_key_error(self):
        with self.assertRaisesRegex(SireneAPIError, "Invalid INSEE API key"):
            _check_response(_error_response(401), "X", "X")

    def test_403_raises(self):
        with self.assertRaises(SireneAPIError):
            _check_response(_error_response(403), "X", "X")

    def test_500_raises(self):
        with self.assertRaises(SireneAPIError):
            _check_response(_error_response(500), "X", "X")


# ---------------------------------------------------------------------------
# _do_get
# ---------------------------------------------------------------------------


class TestDoGet(unittest.TestCase):
    @patch(f"{_MOD}.requests.get")
    def test_success_single_call(self, mock_get):
        mock_get.return_value = _json_response({})
        _do_get("http://x", {}, 10, "test")
        self.assertEqual(mock_get.call_count, 1)

    @patch(f"{_MOD}.time.sleep")
    @patch(f"{_MOD}.requests.get")
    def test_timeout_retry_success(self, mock_get, mock_sleep):
        ok_resp = _json_response({})
        mock_get.side_effect = [requests.exceptions.Timeout(), ok_resp]
        result = _do_get("http://x", {}, 10, "test")
        self.assertEqual(result, ok_resp)
        mock_sleep.assert_called_once_with(3)

    @patch(f"{_MOD}.time.sleep")
    @patch(f"{_MOD}.requests.get")
    def test_timeout_twice_raises(self, mock_get, _mock_sleep):
        mock_get.side_effect = requests.exceptions.Timeout()
        with self.assertRaises(SireneAPIError):
            _do_get("http://x", {}, 10, "test")
        self.assertEqual(mock_get.call_count, 2)

    @patch(f"{_MOD}.requests.get")
    def test_connection_error_raises(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(SireneAPIError):
            _do_get("http://x", {}, 10, "test")


# ---------------------------------------------------------------------------
# fetch_sirene_data
# ---------------------------------------------------------------------------


class TestFetchSireneData(unittest.TestCase):
    @patch(f"{_MOD}.requests.get")
    def test_happy_path(self, mock_get):
        mock_get.return_value = _json_response(_SIREN_PAYLOAD)
        result = fetch_sirene_data("123456789", "key")
        self.assertEqual(result["denomination"], "ACME SARL")
        self.assertEqual(result["siren"], "123456789")
        self.assertEqual(result["siret_siege"], "12345678900012")
        self.assertEqual(result["naf"], "62.02A")
        self.assertEqual(result["workforce"], "20 à 49 salariés (2022)")
        self.assertEqual(result["date_creation"], datetime.date(2010, 3, 15))
        self.assertEqual(result["etat_administratif"], "A")
        self.assertIsNone(result["date_cessation"])

    @patch(f"{_MOD}.requests.get")
    def test_sigle_prepended_to_denomination(self, mock_get):
        data = copy.deepcopy(_SIREN_PAYLOAD)
        data["uniteLegale"]["sigleUniteLegale"] = "ACME"
        mock_get.return_value = _json_response(data)
        result = fetch_sirene_data("123456789", "key")
        self.assertIn("ACME", result["denomination"])
        self.assertIn("ACME SARL", result["denomination"])

    @patch(f"{_MOD}.requests.get")
    def test_sigle_equals_denomination_no_duplicate(self, mock_get):
        data = copy.deepcopy(_SIREN_PAYLOAD)
        data["uniteLegale"]["sigleUniteLegale"] = "ACME SARL"
        mock_get.return_value = _json_response(data)
        result = fetch_sirene_data("123456789", "key")
        # sigle == denomination → no duplication, just the name
        self.assertEqual(result["denomination"], "ACME SARL")

    @patch(f"{_MOD}.requests.get")
    def test_etat_cesse_sets_date_cessation(self, mock_get):
        data = copy.deepcopy(_SIREN_PAYLOAD)
        periode = data["uniteLegale"]["periodesUniteLegale"][0]
        periode["etatAdministratifUniteLegale"] = "C"
        periode["dateDebut"] = "2023-06-01"
        mock_get.return_value = _json_response(data)
        result = fetch_sirene_data("123456789", "key")
        self.assertEqual(result["etat_administratif"], "C")
        self.assertEqual(result["date_cessation"], datetime.date(2023, 6, 1))

    @patch(f"{_MOD}.requests.get")
    def test_missing_nic_raises(self, mock_get):
        data = copy.deepcopy(_SIREN_PAYLOAD)
        data["uniteLegale"]["periodesUniteLegale"][0]["nicSiegeUniteLegale"] = ""
        mock_get.return_value = _json_response(data)
        with self.assertRaises(SireneAPIError):
            fetch_sirene_data("123456789", "key")

    @patch(f"{_MOD}.requests.get")
    def test_404_raises(self, mock_get):
        mock_get.return_value = _error_response(404)
        with self.assertRaises(SireneAPIError):
            fetch_sirene_data("000000000", "key")


# ---------------------------------------------------------------------------
# fetch_sirene_etablissements
# ---------------------------------------------------------------------------


class TestFetchSireneEtablissements(unittest.TestCase):
    @patch(f"{_MOD}.requests.get")
    def test_address_assembly(self, mock_get):
        mock_get.return_value = _json_response(_SIRET_PAYLOAD)
        result = fetch_sirene_etablissements("123456789", "key")
        self.assertEqual(len(result), 1)
        etab = result[0]
        self.assertEqual(etab["siret"], "12345678900012")
        self.assertTrue(etab["is_siege"])
        self.assertEqual(etab["street"], "10 RUE DE LA PAIX")
        self.assertEqual(etab["zip"], "75001")
        self.assertEqual(etab["city"], "PARIS")
        self.assertEqual(etab["etat"], "A")
        self.assertIsNone(etab["date_fermeture"])

    @patch(f"{_MOD}.requests.get")
    def test_ferme_sets_date_fermeture(self, mock_get):
        data = copy.deepcopy(_SIRET_PAYLOAD)
        periode = data["etablissements"][0]["periodesEtablissement"][0]
        periode["etatAdministratifEtablissement"] = "F"
        periode["dateDebut"] = "2022-12-31"
        mock_get.return_value = _json_response(data)
        result = fetch_sirene_etablissements("123456789", "key")
        self.assertEqual(result[0]["etat"], "F")
        self.assertEqual(result[0]["date_fermeture"], datetime.date(2022, 12, 31))

    @patch(f"{_MOD}.requests.get")
    def test_empty_etablissements(self, mock_get):
        mock_get.return_value = _json_response({"etablissements": []})
        result = fetch_sirene_etablissements("123456789", "key")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# NAF / legal form helpers
# ---------------------------------------------------------------------------


class TestNafCodes(unittest.TestCase):
    def test_format_naf_adds_dot(self):
        self.assertEqual(format_naf("6202A"), "62.02A")

    def test_format_naf_idempotent(self):
        self.assertEqual(format_naf("62.02A"), "62.02A")

    def test_format_naf_empty(self):
        self.assertEqual(format_naf(""), "")
        self.assertEqual(format_naf(None), "")

    def test_get_naf_label_known(self):
        label = get_naf_label("6202A")
        self.assertTrue(label)  # non-empty string

    def test_get_naf_label_unknown(self):
        self.assertEqual(get_naf_label("0000X"), "")


class TestLegalFormCodes(unittest.TestCase):
    def test_known_code(self):
        # 5710 = SAS
        label = get_legal_form_label("5710")
        self.assertTrue(label)

    def test_unknown_code(self):
        self.assertEqual(get_legal_form_label("9999"), "")

    def test_empty(self):
        self.assertEqual(get_legal_form_label(""), "")

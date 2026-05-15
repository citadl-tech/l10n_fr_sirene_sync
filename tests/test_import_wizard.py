"""
Tests for SireneImportWizard: SIREN extraction, VAT computation, and import flow.
"""

import datetime
import unittest
from unittest.mock import patch

from odoo.addons.l10n_fr_sirene_sync.wizard.sirene_import_wizard import SireneImportWizard
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from .common import _MOCK_RESULT

_WIZARD_MOD = "odoo.addons.l10n_fr_sirene_sync.wizard.sirene_import_wizard"
_PARTNER_MOD = "odoo.addons.l10n_fr_sirene_sync.models.res_partner"
_FETCH_DATA = f"{_WIZARD_MOD}.fetch_sirene_data"
_FETCH_ETAB = f"{_WIZARD_MOD}.fetch_sirene_etablissements"
# action_import delegates to partner._sync_sirene_etablissements which resolves
# fetch_sirene_etablissements from the res_partner namespace, not the wizard's.
_FETCH_ETAB_PARTNER = f"{_PARTNER_MOD}.fetch_sirene_etablissements"

_MOCK_ETAB = [
    {
        "siret": "12345678900012",
        "is_siege": True,
        "etat": "A",
        "naf": "62.02A",
        "naf_activity": "Conseil en systèmes informatiques",
        "street": "10 RUE DE LA PAIX",
        "street2": "",
        "zip": "75001",
        "city": "PARIS",
        "date_creation": datetime.date(2010, 3, 15),
        "date_fermeture": None,
    }
]


# ---------------------------------------------------------------------------
# _extract_siren  (staticmethod — no ORM, use unittest.TestCase)
# ---------------------------------------------------------------------------


class TestExtractSiren(unittest.TestCase):
    """_extract_siren doesn't use self.env — safe as unittest.TestCase."""

    def _extract(self, raw):
        # object.__new__ bypasses __init__ to avoid ORM dependency
        obj = object.__new__(SireneImportWizard)
        return obj._extract_siren(raw)

    def test_siren_9_digits(self):
        self.assertEqual(self._extract("123456789"), "123456789")

    def test_siret_14_digits(self):
        self.assertEqual(self._extract("12345678900012"), "123456789")

    def test_fr_vat(self):
        self.assertEqual(self._extract("FR32123456789"), "123456789")

    def test_whitespace_stripped(self):
        self.assertEqual(self._extract("  123 456 789  "), "123456789")

    def test_lowercase_fr_vat(self):
        self.assertEqual(self._extract("fr32123456789"), "123456789")

    def test_invalid_raises_user_error(self):
        with self.assertRaises(UserError):
            self._extract("INVALID")

    def test_fr_vat_non_digit_siren_raises(self):
        with self.assertRaises(UserError):
            self._extract("FRABC456789XX")


# ---------------------------------------------------------------------------
# _siren_to_vat
# ---------------------------------------------------------------------------


class TestSirenToVat(unittest.TestCase):
    def test_known_siren(self):
        # key = (12 + 3 * (123456789 % 97)) % 97 = (12 + 3*39) % 97 = 129 % 97 = 32
        vat = SireneImportWizard._siren_to_vat("123456789")
        self.assertEqual(vat, "FR32123456789")
        self.assertEqual(len(vat), 13)
        self.assertTrue(vat.startswith("FR"))

    def test_roundtrip_vat_to_siren(self):
        """VAT produced by _siren_to_vat must yield back the original SIREN."""
        siren = "123456789"
        self.assertEqual(SireneImportWizard._siren_to_vat(siren)[4:], siren)


# ---------------------------------------------------------------------------
# action_search
# ---------------------------------------------------------------------------


class _WizardCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("l10n_fr_sirene_sync.api_key", "test-key")


class TestActionSearch(_WizardCase):
    def _wizard(self, search_input):
        return self.env["sirene.import.wizard"].create({"search_input": search_input})

    @patch(_FETCH_ETAB, return_value=_MOCK_ETAB)
    @patch(_FETCH_DATA)
    def test_search_populates_preview(self, mock_data, _mock_etab):
        mock_data.return_value = _MOCK_RESULT
        wiz = self._wizard("123456789")
        wiz.action_search()
        self.assertEqual(wiz.state, "preview")
        self.assertEqual(wiz.preview_name, "ACME SARL")
        self.assertEqual(wiz.preview_siren, "123456789")
        self.assertEqual(wiz.preview_siret, "12345678900012")
        self.assertEqual(wiz.preview_street, "10 RUE DE LA PAIX")

    @patch(_FETCH_DATA)
    def test_api_error_raises_user_error(self, mock_data):
        from odoo.addons.l10n_fr_sirene_sync.models.sirene_api import SireneAPIError

        mock_data.side_effect = SireneAPIError("not found")
        wiz = self._wizard("000000000")
        with self.assertRaises(UserError):
            wiz.action_search()

    @patch(_FETCH_ETAB, return_value=_MOCK_ETAB)
    @patch(_FETCH_DATA)
    def test_existing_partner_detected(self, mock_data, _mock_etab):
        mock_data.return_value = _MOCK_RESULT
        france = self.env.ref("base.fr")
        existing = self.env["res.partner"].create(
            {"name": "ACME SARL", "is_company": True, "siren": "123456789", "country_id": france.id}
        )
        wiz = self._wizard("123456789")
        wiz.action_search()
        self.assertEqual(wiz.existing_partner_id.id, existing.id)


# ---------------------------------------------------------------------------
# action_import
# ---------------------------------------------------------------------------


class TestActionImport(_WizardCase):
    def _run_import(self):
        wiz = self.env["sirene.import.wizard"].create({"search_input": "123456789"})
        wiz.action_search()
        action = wiz.action_import()
        partner = self.env["res.partner"].browse(action["res_id"])
        return action, partner

    @patch(_FETCH_ETAB_PARTNER, return_value=[])
    @patch(f"{_WIZARD_MOD}.fetch_sirene_etablissements", return_value=_MOCK_ETAB)
    @patch(_FETCH_DATA)
    def test_import_creates_partner(self, mock_data, _mock_etab_wiz, _mock_etab_partner):
        mock_data.return_value = _MOCK_RESULT
        action, partner = self._run_import()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(partner.name, "ACME SARL")
        self.assertEqual(partner.siren, "123456789")
        self.assertTrue(partner.vat.startswith("FR"))
        self.assertTrue(partner.is_company)
        self.assertEqual(partner.sirene_sync_state, "ok")

    @patch(_FETCH_ETAB_PARTNER, return_value=[])
    @patch(f"{_WIZARD_MOD}.fetch_sirene_etablissements", return_value=_MOCK_ETAB)
    @patch(_FETCH_DATA)
    def test_import_vat_roundtrips_to_siren(self, mock_data, _mock_etab_wiz, _mock_etab_partner):
        mock_data.return_value = _MOCK_RESULT
        _action, partner = self._run_import()
        self.assertEqual(partner._get_siren_from_vat(), "123456789")

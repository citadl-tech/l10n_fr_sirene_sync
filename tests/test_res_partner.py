"""
Tests for ResPartner extension: computed fields, SIREN extraction, NAF section
mapping, diff detection, and the main action + cron methods.
API calls are mocked — no network access.
"""

import datetime
import unittest
from unittest.mock import patch

from odoo.addons.l10n_fr_sirene_sync.models.res_partner import ResPartner
from odoo.tests.common import TransactionCase, mute_logger

from .common import _MOCK_RESULT

_PARTNER_MOD = "odoo.addons.l10n_fr_sirene_sync.models.res_partner"
_FETCH_DATA = f"{_PARTNER_MOD}.fetch_sirene_data"
_FETCH_ETAB = f"{_PARTNER_MOD}.fetch_sirene_etablissements"


# ---------------------------------------------------------------------------
# _naf_division_to_section  (staticmethod — no ORM needed)
# ---------------------------------------------------------------------------


class TestNafDivisionToSection(unittest.TestCase):
    def _s(self, naf):
        return ResPartner._naf_division_to_section(naf)

    def test_section_A_boundaries(self):
        self.assertEqual(self._s("01.11Z"), "A")
        self.assertEqual(self._s("03.21Z"), "A")

    def test_section_B(self):
        self.assertEqual(self._s("05.10Z"), "B")
        self.assertEqual(self._s("09.10Z"), "B")

    def test_section_C(self):
        self.assertEqual(self._s("10.11Z"), "C")
        self.assertEqual(self._s("33.11Z"), "C")

    def test_section_D(self):
        self.assertEqual(self._s("35.11Z"), "D")

    def test_section_J(self):
        # Division 58-63 → J
        self.assertEqual(self._s("62.02A"), "J")
        self.assertEqual(self._s("63.11Z"), "J")

    def test_section_L(self):
        self.assertEqual(self._s("68.10Z"), "L")

    def test_section_U(self):
        self.assertEqual(self._s("99.00Z"), "U")

    def test_invalid_returns_empty(self):
        self.assertEqual(self._s(""), "")
        self.assertEqual(self._s(None), "")
        self.assertEqual(self._s("AB.CDX"), "")


# ---------------------------------------------------------------------------
# _get_siren_from_vat
# ---------------------------------------------------------------------------


class TestGetSirenFromVat(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Test", "is_company": True})

    def _siren(self, vat):
        self.partner.vat = vat
        return self.partner._get_siren_from_vat()

    def test_valid_fr_vat(self):
        self.assertEqual(self._siren("FR32123456789"), "123456789")

    def test_spaces_stripped(self):
        self.assertEqual(self._siren("FR 32 123 456 789"), "123456789")

    def test_lowercase_accepted(self):
        self.assertEqual(self._siren("fr32123456789"), "123456789")

    def test_wrong_length_returns_empty(self):
        self.assertEqual(self._siren("FR321234567890"), "")

    def test_non_fr_prefix_returns_empty(self):
        self.assertEqual(self._siren("DE123456789"), "")  # pragma: allowlist secret

    def test_non_digit_siren_returns_empty(self):
        self.assertEqual(self._siren("FRABC456789AB"), "")


# ---------------------------------------------------------------------------
# Computed fields
# ---------------------------------------------------------------------------


class TestSireneEligible(TransactionCase):
    def test_company_with_fr_vat(self):
        p = self.env["res.partner"].create({"name": "Test SA", "is_company": True, "vat": "FR32123456789"})
        self.assertTrue(p.sirene_eligible)

    def test_not_company(self):
        p = self.env["res.partner"].create({"name": "Test", "is_company": False, "vat": "FR32123456789"})
        self.assertFalse(p.sirene_eligible)

    def test_non_fr_vat(self):
        p = self.env["res.partner"].create(
            {"name": "Test GmbH", "is_company": True, "vat": "DE123456789"}  # pragma: allowlist secret
        )
        self.assertFalse(p.sirene_eligible)

    def test_no_vat(self):
        p = self.env["res.partner"].create({"name": "Test", "is_company": True})
        self.assertFalse(p.sirene_eligible)


class TestSireneEnLiquidation(TransactionCase):
    def _partner(self, denomination):
        return self.env["res.partner"].create({"name": "Test", "is_company": True, "sirene_denomination": denomination})

    def test_liquidation_uppercase(self):
        self.assertTrue(self._partner("ACME EN LIQUIDATION JUDICIAIRE").sirene_en_liquidation)

    def test_liquidation_mixed_case(self):
        self.assertTrue(self._partner("Société en Liquidation").sirene_en_liquidation)

    def test_no_liquidation(self):
        self.assertFalse(self._partner("ACME SARL").sirene_en_liquidation)

    def test_empty_denomination(self):
        self.assertFalse(self._partner("").sirene_en_liquidation)


class TestSireneLastCheckRelative(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Test", "is_company": True})
        self._now = datetime.datetime(2024, 6, 1, 12, 0, 0)

    def _set_last_check(self, delta):
        self.partner.sirene_last_check_date = self._now - delta
        self.partner.invalidate_recordset()

    @patch("odoo.fields.Datetime.now")
    def test_just_now(self, mock_now):
        mock_now.return_value = self._now
        self._set_last_check(datetime.timedelta(seconds=30))
        self.assertEqual(self.partner.sirene_last_check_relative, "just now")

    @patch("odoo.fields.Datetime.now")
    def test_minutes_ago(self, mock_now):
        mock_now.return_value = self._now
        self._set_last_check(datetime.timedelta(minutes=5))
        self.assertIn("minute", self.partner.sirene_last_check_relative)

    @patch("odoo.fields.Datetime.now")
    def test_hours_ago(self, mock_now):
        mock_now.return_value = self._now
        self._set_last_check(datetime.timedelta(hours=3))
        self.assertIn("hour", self.partner.sirene_last_check_relative)

    @patch("odoo.fields.Datetime.now")
    def test_days_ago(self, mock_now):
        mock_now.return_value = self._now
        self._set_last_check(datetime.timedelta(days=10))
        self.assertIn("day", self.partner.sirene_last_check_relative)

    @patch("odoo.fields.Datetime.now")
    def test_no_date_returns_empty(self, mock_now):
        mock_now.return_value = self._now
        self.partner.sirene_last_check_date = False
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.sirene_last_check_relative, "")


# ---------------------------------------------------------------------------
# _detect_differences
# ---------------------------------------------------------------------------


class TestDetectDifferences(TransactionCase):
    def setUp(self):
        super().setUp()
        france = self.env.ref("base.fr")
        self.partner = self.env["res.partner"].create(
            {
                "name": "ACME SARL",
                "is_company": True,
                "siren": "123456789",
                "siret": "12345678900012",
                "vat": "FR32123456789",
                "country_id": france.id,
            }
        )
        self.staging_vals = self.partner._build_staging_vals(_MOCK_RESULT)

    def test_no_diffs_when_data_matches(self):
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        # Only check fields that are in scope — country is France, industry is None
        self.assertNotIn("SIREN", diffs)
        self.assertNotIn("Legal Name", diffs)
        self.assertNotIn("SIRET (Head Office)", diffs)

    def test_siren_diff_detected(self):
        self.partner.siren = "999999999"
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        self.assertIn("SIREN", diffs)

    def test_name_diff_detected(self):
        self.partner.name = "OLD NAME"
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        self.assertIn("Legal Name", diffs)

    def test_siret_diff_detected(self):
        self.partner.siret = "99999999900001"
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        self.assertIn("SIRET (Head Office)", diffs)

    def test_country_diff_detected(self):
        de = self.env["res.country"].search([("code", "=", "DE")], limit=1)
        self.partner.country_id = de
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        self.assertIn("Country", diffs)

    def test_verbose_includes_old_and_new_values(self):
        self.partner.name = "OLD NAME"
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT, verbose=True)
        self.assertTrue(any("OLD NAME" in d and "ACME SARL" in d for d in diffs))

    def test_address_diff_via_siege(self):
        self.env["sirene.etablissement"].create(
            {
                "partner_id": self.partner.id,
                "siret": "12345678900012",
                "is_siege": True,
                "street": "10 RUE DE LA PAIX",
                "zip": "75001",
                "city": "PARIS",
            }
        )
        self.partner.street = "ANCIENNE ADRESSE"
        diffs = self.partner._detect_differences(self.staging_vals, _MOCK_RESULT)
        self.assertIn("street", diffs)


# ---------------------------------------------------------------------------
# Shared partner fixture for action + cron tests
# ---------------------------------------------------------------------------


class _PartnerCase(TransactionCase):
    def setUp(self):
        super().setUp()
        france = self.env.ref("base.fr")
        self.partner = self.env["res.partner"].create(
            {
                "name": "ACME SARL",
                "is_company": True,
                "vat": "FR32123456789",
                "siren": "123456789",
                "siret": "12345678900012",
                "country_id": france.id,
            }
        )


# ---------------------------------------------------------------------------
# action_sirene_check
# ---------------------------------------------------------------------------


class TestActionSireneCheck(_PartnerCase):
    @mute_logger(_PARTNER_MOD)
    def test_invalid_vat_raises_user_error(self):
        from odoo.exceptions import UserError

        self.partner.vat = "DE123456789"  # pragma: allowlist secret
        with self.assertRaises(UserError):
            self.partner.action_sirene_check()

    @mute_logger(_PARTNER_MOD)
    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_api_error_sets_error_state(self, mock_data, _mock_etab):
        from odoo.addons.l10n_fr_sirene_sync.models.sirene_api import SireneAPIError

        mock_data.side_effect = SireneAPIError("API down")
        action = self.partner.action_sirene_check()
        self.assertEqual(self.partner.sirene_sync_state, "error")
        self.assertIn("API down", self.partner.sirene_last_error)
        self.assertEqual(action.get("type"), "ir.actions.client")
        self.assertEqual(action.get("params", {}).get("type"), "danger")

    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_no_diffs_returns_success_notification(self, mock_data, _mock_etab):
        mock_data.return_value = {**_MOCK_RESULT, "naf": ""}
        action = self.partner.action_sirene_check()
        self.assertEqual(action.get("type"), "ir.actions.client")
        self.assertEqual(action.get("tag"), "display_notification")
        self.assertEqual(self.partner.sirene_sync_state, "ok")

    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_diffs_open_validation_wizard(self, mock_data, _mock_etab):
        mock_data.return_value = {**_MOCK_RESULT, "denomination": "NOUVEAU NOM SA"}
        action = self.partner.action_sirene_check()
        self.assertEqual(action.get("type"), "ir.actions.act_window")
        self.assertEqual(action.get("res_model"), "sirene.validation.wizard")
        self.assertEqual(self.partner.sirene_sync_state, "pending")


# ---------------------------------------------------------------------------
# _cron_sync_one
# ---------------------------------------------------------------------------


class TestCronSyncOne(_PartnerCase):
    @mute_logger(_PARTNER_MOD)
    def test_invalid_vat_skips_silently(self):
        self.partner.vat = "DE123456789"  # pragma: allowlist secret
        self.partner._cron_sync_one("key", 10)
        self.assertEqual(self.partner.sirene_sync_state, "unchecked")

    @mute_logger(_PARTNER_MOD)
    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_api_error_sets_error_state(self, mock_data, _mock_etab):
        from odoo.addons.l10n_fr_sirene_sync.models.sirene_api import SireneAPIError

        mock_data.side_effect = SireneAPIError("timeout")
        self.partner._cron_sync_one("key", 10)
        self.assertEqual(self.partner.sirene_sync_state, "error")

    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_no_diffs_sets_ok(self, mock_data, _mock_etab):
        mock_data.return_value = {**_MOCK_RESULT, "naf": ""}
        self.partner._cron_sync_one("key", 10)
        self.assertEqual(self.partner.sirene_sync_state, "ok")

    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    def test_diffs_sets_pending(self, mock_data, _mock_etab):
        mock_data.return_value = {**_MOCK_RESULT, "denomination": "NOUVEAU NOM SA"}
        self.partner._cron_sync_one("key", 10)
        self.assertEqual(self.partner.sirene_sync_state, "pending")


# ---------------------------------------------------------------------------
# _cron_sirene_sync
# ---------------------------------------------------------------------------


class TestCronSireneSync(TransactionCase):
    @mute_logger(_PARTNER_MOD)
    def test_no_api_key_aborts(self):
        self.env["ir.config_parameter"].sudo().set_param("l10n_fr_sirene_sync.api_key", "")
        self.env["res.partner"]._cron_sirene_sync()

    @patch(_FETCH_ETAB, return_value=[])
    @patch(_FETCH_DATA)
    @patch(f"{_PARTNER_MOD}.time.sleep")
    def test_processes_eligible_partners(self, _mock_sleep, mock_data, _mock_etab):
        mock_data.return_value = _MOCK_RESULT
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("l10n_fr_sirene_sync.api_key", "test-key")
        france = self.env.ref("base.fr")
        p = self.env["res.partner"].create(
            {
                "name": "ACME SARL",
                "is_company": True,
                "vat": "FR32123456789",
                "siren": "123456789",
                "siret": "12345678900012",
                "country_id": france.id,
            }
        )
        self.env["res.partner"]._cron_sirene_sync()
        self.assertNotEqual(p.sirene_sync_state, "unchecked")

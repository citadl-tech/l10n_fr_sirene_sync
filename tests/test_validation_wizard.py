"""
Tests for SireneValidationWizard: line generation and apply/ignore actions.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase

_PARTNER_MOD = "odoo.addons.l10n_fr_sirene_sync.models.res_partner"
_FETCH_DATA = f"{_PARTNER_MOD}.fetch_sirene_data"
_FETCH_ETAB = f"{_PARTNER_MOD}.fetch_sirene_etablissements"


def _base_result(**kwargs):
    return {
        "denomination": "ACME SARL",
        "siren": "123456789",
        "siret_siege": "12345678900012",
        "naf": "62.02A",
        "naf_activity": "Conseil en systèmes informatiques",
        "date_creation": None,
        "legal_form_code": "5710",
        "legal_form": "SAS",
        "workforce": "",
        "categorie_entreprise": "",
        "etat_administratif": "A",
        "date_cessation": None,
        **kwargs,
    }


class TestValidationWizardCreate(TransactionCase):
    def setUp(self):
        super().setUp()
        france = self.env.ref("base.fr")
        self.partner = self.env["res.partner"].create(
            {
                "name": "ANCIEN NOM",
                "is_company": True,
                "vat": "FR32123456789",
                "siren": "123456789",
                "siret": "12345678900012",
                "country_id": france.id,
            }
        )

    def _run_check_and_get_wizard(self, result, etab=None):
        """Trigger action_sirene_check with mocked API and return the created wizard."""
        with patch(_FETCH_DATA, return_value=result), patch(_FETCH_ETAB, return_value=etab or []):
            action = self.partner.action_sirene_check()
        if action.get("res_model") == "sirene.validation.wizard":
            return self.env["sirene.validation.wizard"].browse(action["res_id"])
        return None

    def test_name_diff_generates_line(self):
        wiz = self._run_check_and_get_wizard(_base_result(denomination="NOUVEAU NOM SARL"))
        self.assertIsNotNone(wiz)
        labels = wiz.line_ids.mapped("field_label")
        self.assertIn("Legal Name", labels)

    def test_siren_diff_generates_line(self):
        self.partner.siren = "999999999"
        wiz = self._run_check_and_get_wizard(_base_result())
        self.assertIsNotNone(wiz)
        labels = wiz.line_ids.mapped("field_label")
        self.assertIn("SIREN", labels)

    def test_no_diff_no_wizard(self):
        self.partner.name = "ACME SARL"
        action = None
        with (
            patch(_FETCH_DATA, return_value=_base_result(denomination="ACME SARL", naf="")),
            patch(_FETCH_ETAB, return_value=[]),
        ):
            action = self.partner.action_sirene_check()
        # No wizard — returns a client notification
        self.assertEqual(action.get("type"), "ir.actions.client")

    def test_address_diff_via_siege_generates_lines(self):
        # The siege must come from the FETCH_ETAB mock: _sync_sirene_etablissements
        # unlinks all existing establishments before recreating from the API result,
        # so pre-creating the siege here would be deleted before diff detection.
        self.partner.street = "ANCIENNE RUE"
        wiz = self._run_check_and_get_wizard(
            _base_result(),
            etab=[
                {
                    "siret": "12345678900012",
                    "is_siege": True,
                    "street": "10 RUE DE LA PAIX",
                    "zip": "75001",
                    "city": "PARIS",
                }
            ],
        )
        self.assertIsNotNone(wiz)
        labels = wiz.line_ids.mapped("field_label")
        self.assertIn("Street", labels)


# ---------------------------------------------------------------------------
# action_apply
# ---------------------------------------------------------------------------


class TestValidationWizardApply(TransactionCase):
    def setUp(self):
        super().setUp()
        france = self.env.ref("base.fr")
        self.partner = self.env["res.partner"].create(
            {
                "name": "ANCIEN NOM",
                "is_company": True,
                "vat": "FR32123456789",
                "siren": "123456789",
                "siret": "12345678900012",
                "country_id": france.id,
            }
        )

    def _wizard_with_name_diff(self, new_name="ACME SARL"):
        self.partner.write(
            {
                "sirene_denomination": new_name,
                "sirene_siren": "123456789",
                "sirene_siret_siege": "12345678900012",
                "sirene_sync_state": "pending",
            }
        )
        return self.env["sirene.validation.wizard"].create({"partner_id": self.partner.id})

    def test_apply_true_updates_partner_name(self):
        wiz = self._wizard_with_name_diff("ACME SARL")
        name_line = wiz.line_ids.filtered(lambda line: line.field_name == "name")
        self.assertTrue(name_line)
        name_line.apply = True
        wiz.action_apply()
        self.assertEqual(self.partner.name, "ACME SARL")
        self.assertEqual(self.partner.sirene_sync_state, "ok")

    def test_apply_false_keeps_current_value(self):
        wiz = self._wizard_with_name_diff("ACME SARL")
        name_line = wiz.line_ids.filtered(lambda line: line.field_name == "name")
        name_line.apply = False
        wiz.action_apply()
        self.assertEqual(self.partner.name, "ANCIEN NOM")

    def test_apply_false_posts_message(self):
        wiz = self._wizard_with_name_diff("ACME SARL")
        name_line = wiz.line_ids.filtered(lambda line: line.field_name == "name")
        name_line.apply = False
        initial_msg_count = len(self.partner.message_ids)
        wiz.action_apply()
        self.assertGreater(len(self.partner.message_ids), initial_msg_count)

    def test_field_outside_whitelist_is_skipped(self):
        """Injecting a field_name not in _ALLOWED_WRITE_FIELDS must not be written."""
        wiz = self._wizard_with_name_diff("ACME SARL")
        # Manually add a line with a dangerous field name
        self.env["sirene.validation.wizard.line"].create(
            {
                "wizard_id": wiz.id,
                "field_name": "active",  # not in _ALLOWED_WRITE_FIELDS
                "field_label": "Active",
                "current_value": "True",
                "proposed_value": "False",
                "apply": True,
            }
        )
        self.assertTrue(self.partner.active)
        wiz.action_apply()
        self.assertTrue(self.partner.active)  # must not have been changed


# ---------------------------------------------------------------------------
# action_ignore
# ---------------------------------------------------------------------------


class TestValidationWizardIgnore(TransactionCase):
    def test_ignore_sets_state_and_posts_message(self):
        france = self.env.ref("base.fr")
        partner = self.env["res.partner"].create(
            {
                "name": "ACME SARL",
                "is_company": True,
                "vat": "FR32123456789",
                "sirene_sync_state": "pending",
                "country_id": france.id,
            }
        )
        wiz = self.env["sirene.validation.wizard"].create({"partner_id": partner.id})
        initial_msg_count = len(partner.message_ids)
        wiz.action_ignore()
        self.assertEqual(partner.sirene_sync_state, "ignored")
        self.assertGreater(len(partner.message_ids), initial_msg_count)

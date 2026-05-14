import re
import logging
from odoo import models, fields, _
from odoo.exceptions import UserError
from ..models.sirene_api import fetch_sirene_data, fetch_sirene_etablissements, SireneAPIError

_logger = logging.getLogger(__name__)

_SIREN_RE = re.compile(r"^\d{9}$")
_SIRET_RE = re.compile(r"^\d{14}$")
_VAT_FR_RE = re.compile(r"^FR.{11}$")


class SireneImportWizard(models.TransientModel):
    _name = "sirene.import.wizard"
    _description = "Import Partner from SIRENE"

    state = fields.Selection(
        selection=[("search", "Search"), ("preview", "Preview")],
        default="search",
        required=True,
    )

    search_input = fields.Char(
        string="SIREN / SIRET / Intracom VAT",
        help="Enter a 9-digit SIREN, 14-digit SIRET, or French VAT number (FR + 11 chars).",
    )

    preview_name = fields.Char(string="Legal Name", readonly=True)
    preview_siren = fields.Char(string="SIREN", readonly=True)
    preview_siret = fields.Char(string="SIRET (Head Office)", readonly=True)
    preview_naf = fields.Char(string="NAF Code", readonly=True)
    preview_naf_activity = fields.Char(string="Activity", readonly=True)
    preview_date_creation = fields.Date(string="Creation Date", readonly=True)
    preview_street = fields.Char(string="Street", readonly=True)
    preview_zip = fields.Char(string="Postal Code", readonly=True)
    preview_city = fields.Char(string="City", readonly=True)
    existing_partner_id = fields.Many2one(
        "res.partner",
        string="Existing Partner",
        readonly=True,
    )
    archived_partner_id = fields.Many2one(
        "res.partner",
        string="Archived Partner",
        readonly=True,
    )

    def _extract_siren(self, raw):
        """Normalise SIREN / SIRET / French VAT to a 9-digit SIREN string.

        Accepted formats:
          - 9 digits  → SIREN
          - 14 digits → SIRET (first 9 digits are the SIREN)
          - FR + 11 chars (e.g. FR12345678901) → digits [4:] are the SIREN
        Returns the SIREN string or raises UserError.
        """
        value = re.sub(r"\s", "", (raw or "")).upper()

        if _SIREN_RE.fullmatch(value):
            return value

        if _SIRET_RE.fullmatch(value):
            return value[:9]

        if _VAT_FR_RE.fullmatch(value):
            siren = value[4:]
            if siren.isdigit():
                return siren

        raise UserError(
            _(
                "Unrecognised format: please enter a 9-digit SIREN, "
                "a 14-digit SIRET, or a French VAT number (e.g. FR12345678901)."
            )
        )

    def action_search(self):
        self.ensure_one()

        siren = self._extract_siren(self.search_input)
        config = self.env["res.partner"]._get_sirene_config()
        api_key, timeout = config["api_key"], config["timeout"]

        try:
            result = fetch_sirene_data(siren, api_key, timeout=timeout)
        except SireneAPIError as exc:
            raise UserError(_("SIRENE API error:\n%s") % str(exc)) from exc

        street = zip_code = city = ""
        try:
            etablissements = fetch_sirene_etablissements(siren, api_key, timeout=timeout)
            siege = next((e for e in etablissements if e.get("is_siege")), None)
            if siege:
                street = siege.get("street") or ""
                zip_code = siege.get("zip") or ""
                city = siege.get("city") or ""
        except Exception:
            _logger.warning("SIRENE import: could not retrieve establishments for %s", siren)

        existing = self.env["res.partner"].search(
            [("siren", "=", siren), ("is_company", "=", True)], limit=1
        )
        archived = self.env["res.partner"].with_context(active_test=False).search(
            [("siren", "=", siren), ("is_company", "=", True), ("active", "=", False)],
            limit=1,
        )

        self.write(
            {
                "state": "preview",
                "preview_name": result.get("denomination") or "",
                "preview_siren": result.get("siren") or "",
                "preview_siret": result.get("siret_siege") or "",
                "preview_naf": result.get("naf") or "",
                "preview_naf_activity": result.get("naf_activity") or "",
                "preview_date_creation": result.get("date_creation"),
                "preview_street": street,
                "preview_zip": zip_code,
                "preview_city": city,
                "existing_partner_id": existing.id if existing else False,
                "archived_partner_id": archived.id if archived else False,
            }
        )

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Import from SIRENE"),
        }

    @staticmethod
    def _siren_to_vat(siren):
        """Compute French intracom VAT from SIREN.

        Key = (12 + 3 * (SIREN % 97)) % 97
        """
        key = (12 + 3 * (int(siren) % 97)) % 97
        return "FR%02d%s" % (key, siren)

    def _open_partner_action(self, partner):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": partner.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_import(self):
        self.ensure_one()

        france = self.env.ref("base.fr")
        vat = self._siren_to_vat(self.preview_siren) if self.preview_siren else False
        denomination = self.preview_name or ""
        industry = self.env["res.partner"]._get_industry_from_naf(self.preview_naf)
        partner_vals = {
            "name": denomination,
            "is_company": True,
            "siren": self.preview_siren,
            "siret": self.preview_siret,
            "vat": vat,
            "street": self.preview_street,
            "zip": self.preview_zip,
            "city": self.preview_city,
            "country_id": france.id,
            "industry_id": industry.id if industry else False,
            "sirene_siren": self.preview_siren,
            "sirene_siret_siege": self.preview_siret,
            "sirene_naf": self.preview_naf,
            "sirene_naf_activity": self.preview_naf_activity,
            "sirene_date_creation": self.preview_date_creation,
            "sirene_industry_id": industry.id if industry else False,
            "sirene_denomination": denomination,
            "sirene_sync_state": "ok",
            "sirene_last_check_date": fields.Datetime.now(),
        }

        partner = self.env["res.partner"].create(partner_vals)

        config = self.env["res.partner"]._get_sirene_config()
        try:
            partner._sync_sirene_etablissements(
                self.preview_siren, config["api_key"], config["timeout"]
            )
        except Exception:
            _logger.warning(
                "SIRENE import: could not sync establishments for partner %s", partner.id
            )

        _logger.info(
            "SIRENE import: partner '%s' (SIREN %s) created — id=%s",
            partner.name,
            self.preview_siren,
            partner.id,
        )

        return self._open_partner_action(partner)

    def action_go_to_existing(self):
        self.ensure_one()
        return self._open_partner_action(self.existing_partner_id)

    def action_unarchive(self):
        self.ensure_one()
        self.archived_partner_id.action_unarchive()
        _logger.info(
            "SIRENE import: archived partner '%s' (SIREN %s) unarchived — id=%s",
            self.archived_partner_id.name,
            self.preview_siren,
            self.archived_partner_id.id,
        )
        return self._open_partner_action(self.archived_partner_id)

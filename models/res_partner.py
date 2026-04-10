import re
import time
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from .sirene_api import fetch_sirene_data, fetch_sirene_etablissements, SireneAPIError

_logger = logging.getLogger(__name__)

_SIREN_RE = re.compile(r"^\d{9}$")

FIELD_MAPPING = [
    # (odoo_field, sirene_staging_field, label)
    ("siren", "sirene_siren", "SIREN"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    siren = fields.Char(
        string="SIREN",
        size=9,
        copy=False,
        tracking=True,
    )
    siret = fields.Char(
        string="SIRET",
        size=14,
        copy=False,
        tracking=True,
    )
    sirene_denomination = fields.Char(
        string="SIRENE Legal Name",
        copy=False,
    )
    sirene_siren = fields.Char(
        string="SIREN (SIRENE)",
        copy=False,
    )
    sirene_naf = fields.Char(
        string="NAF Code",
        copy=False,
    )
    sirene_naf_activity = fields.Char(
        string="Activity",
        copy=False,
    )
    sirene_date_creation = fields.Date(
        string="Creation Date",
        copy=False,
    )
    sirene_siret_siege = fields.Char(
        string="SIRET (Head Office)",
        copy=False,
    )
    sirene_sync_state = fields.Selection(
        selection=[
            ("unchecked", "Unchecked"),
            ("ok", "Up to date"),
            ("pending", "Pending"),
            ("ignored", "Ignored"),
            ("error", "Error"),
        ],
        string="SIRENE Sync Status",
        default="unchecked",
        copy=False,
    )
    sirene_last_check_date = fields.Datetime(
        string="Last SIRENE Check",
        copy=False,
    )
    sirene_last_error = fields.Char(
        string="Last SIRENE Error",
        copy=False,
    )
    sirene_etablissement_ids = fields.One2many(
        "sirene.etablissement",
        "partner_id",
        string="SIRENE Establishments",
        readonly=True,
    )
    sirene_eligible = fields.Boolean(
        compute="_compute_sirene_eligible",
        string="SIRENE Eligible",
    )

    @api.depends("is_company", "vat")
    def _compute_sirene_eligible(self):
        for rec in self:
            rec.sirene_eligible = rec.is_company and (rec.vat or "").upper().startswith(
                "FR"
            )

    def _get_siren_from_vat(self):
        """Extract the 9-digit SIREN from a French VAT number.

        French VAT format: FR + 2-char key (digits or letters) + 9-digit SIREN.
        Example: FR12345678901 → SIREN = 303265045
        """
        vat = (self.vat or "").replace(" ", "").upper()
        if not vat.startswith("FR") or len(vat) != 13:
            return ""
        siren = vat[4:]  # skip 'FR' (2) + key (2), take remaining 9 chars
        return siren if siren.isdigit() else ""

    def _get_sirene_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            timeout = int(ICP.get_param("l10n_fr_sirene_sync.api_timeout", "10"))
        except (ValueError, TypeError):
            timeout = 10
        return {
            "api_key": ICP.get_param("l10n_fr_sirene_sync.api_key", ""),
            "timeout": timeout,
        }

    def _build_staging_vals(self, result):
        staging_vals = {
            "sirene_last_check_date": fields.Datetime.now(),
            "sirene_last_error": False,
            "sirene_denomination": result.get("denomination", "") or "",
            "sirene_naf": result.get("naf", "") or "",
            "sirene_naf_activity": result.get("naf_activity", "") or "",
            "sirene_date_creation": result.get("date_creation"),
            "sirene_siret_siege": result.get("siret_siege", "") or "",
        }
        for _odoo_field, sirene_field, _label in FIELD_MAPPING:
            sirene_val = result.get(_odoo_field, "") or ""
            staging_vals[sirene_field] = sirene_val
        return staging_vals

    def _detect_differences(self, staging_vals, result, verbose=False):
        """Compare SIRENE data against current partner values.

        Returns a list of diff strings (verbose=True) or field labels (verbose=False).
        """
        diffs = []

        for odoo_field, _sirene_field, label in FIELD_MAPPING:
            sirene_val = result.get(odoo_field, "") or ""
            odoo_val = getattr(self, odoo_field) or ""
            if sirene_val.strip() != odoo_val.strip():
                diffs.append(
                    "%s: '%s' → '%s'" % (label, odoo_val, sirene_val)
                    if verbose
                    else label
                )

        denomination = staging_vals["sirene_denomination"]
        if denomination.strip() != (self.name or "").strip():
            diffs.append(
                "Legal Name: '%s' → '%s'" % (self.name or "", denomination)
                if verbose
                else "Legal Name"
            )

        siret_siege = staging_vals["sirene_siret_siege"]
        if siret_siege.strip() != (self.siret or "").strip():
            diffs.append(
                "SIRET (Head Office): '%s' → '%s'" % (self.siret or "", siret_siege)
                if verbose
                else "SIRET (Head Office)"
            )

        siege = self.sirene_etablissement_ids.filtered(lambda e: e.is_siege)
        if siege:
            for odoo_field in ("street", "street2", "zip", "city"):
                sirene_val = (getattr(siege[0], odoo_field) or "").strip()
                odoo_val = (getattr(self, odoo_field) or "").strip()
                if sirene_val != odoo_val:
                    diffs.append(
                        "%s: '%s' → '%s'" % (odoo_field, odoo_val, sirene_val)
                        if verbose
                        else odoo_field
                    )

        france = self.env["res.country"].search([("code", "=", "FR")], limit=1)
        if france and self.country_id != france:
            diffs.append(
                "Country: '%s' → 'France'" % (self.country_id.name or "")
                if verbose
                else "Country"
            )

        return diffs

    def action_sirene_check(self):
        self.ensure_one()

        siren = self._get_siren_from_vat()
        if not siren or not _SIREN_RE.match(siren):
            _logger.warning(
                "SIRENE [%s]: invalid SIREN extracted from VAT number '%s'",
                self.name,
                self.vat or "",
            )
            raise UserError(
                _(
                    "Unable to extract a valid SIREN from the VAT number.\n"
                    "Expected format: FR + 2 characters + 9 digits (e.g. FR12345678901).\n"
                    "VAT value: '%s'"
                )
                % (self.vat or "")
            )

        _logger.info("SIRENE [%s]: manual check — SIREN %s", self.name, siren)

        config = self._get_sirene_config()
        api_key, timeout = config["api_key"], config["timeout"]

        try:
            result = fetch_sirene_data(siren, api_key, timeout=timeout)
        except SireneAPIError as exc:
            _logger.error(
                "SIRENE [%s]: API error for SIREN %s — %s",
                self.name,
                siren,
                exc,
            )
            self.write(
                {
                    "sirene_sync_state": "error",
                    "sirene_last_error": str(exc),
                    "sirene_last_check_date": fields.Datetime.now(),
                }
            )
            raise UserError(
                _("Error calling INSEE SIRENE API:\n%s") % str(exc)
            ) from exc

        staging_vals = self._build_staging_vals(result)

        self._sync_sirene_etablissements(siren, api_key, timeout)

        diffs = self._detect_differences(staging_vals, result, verbose=True)

        if not diffs:
            _logger.info(
                "SIRENE [%s]: data up to date — no differences detected.", self.name
            )
            staging_vals["sirene_sync_state"] = "ok"
            self.write(staging_vals)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("SIRENE"),
                    "message": _("Data up to date — no changes needed."),
                    "type": "success",
                    "sticky": False,
                },
            }

        _logger.info(
            "SIRENE [%s]: %d difference(s) detected — %s",
            self.name,
            len(diffs),
            " | ".join(diffs),
        )
        staging_vals["sirene_sync_state"] = "pending"
        self.write(staging_vals)

        wizard = self.env["sirene.validation.wizard"].create({"partner_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "sirene.validation.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": _("SIRENE Update — %s") % self.name,
        }

    def _sync_sirene_etablissements(self, siren, api_key, timeout):
        """Fetch and refresh the list of establishments from SIRENE API."""
        try:
            etablissements = fetch_sirene_etablissements(
                siren, api_key, timeout=timeout
            )
        except Exception as exc:
            _logger.warning(
                "SIRENE [%s]: unable to retrieve establishments for SIREN %s — %s",
                self.name,
                siren,
                exc,
            )
            return

        nb = len(etablissements) if etablissements else 0
        _logger.info(
            "SIRENE [%s]: %d establishment(s) retrieved for SIREN %s.",
            self.name,
            nb,
            siren,
        )
        self.sudo().sirene_etablissement_ids.unlink()
        if etablissements:
            self.env["sirene.etablissement"].sudo().create(
                [dict(etab, partner_id=self.id) for etab in etablissements]
            )

    def _cron_sync_one(self, api_key, timeout):
        """Synchronise a partner from the SIRENE API (cron usage — no wizard)."""
        self.ensure_one()
        siren = self._get_siren_from_vat()
        if not siren or not _SIREN_RE.match(siren):
            _logger.warning(
                "SIRENE cron [%s]: invalid or missing SIREN from VAT '%s' — skipped.",
                self.name,
                self.vat or "",
            )
            return

        _logger.debug("SIRENE cron [%s]: synchronising — SIREN %s", self.name, siren)

        try:
            result = fetch_sirene_data(siren, api_key, timeout=timeout)
        except SireneAPIError as exc:
            _logger.error(
                "SIRENE cron [%s]: API error for SIREN %s — %s",
                self.name,
                siren,
                exc,
            )
            self.write(
                {
                    "sirene_sync_state": "error",
                    "sirene_last_error": str(exc),
                    "sirene_last_check_date": fields.Datetime.now(),
                }
            )
            return

        staging_vals = self._build_staging_vals(result)

        self._sync_sirene_etablissements(siren, api_key, timeout)

        diffs = self._detect_differences(staging_vals, result)

        if diffs:
            _logger.info(
                "SIRENE cron [%s]: %d difference(s) — %s",
                self.name,
                len(diffs),
                ", ".join(diffs),
            )
        else:
            _logger.debug("SIRENE cron [%s]: data up to date.", self.name)

        staging_vals["sirene_sync_state"] = "pending" if diffs else "ok"
        self.write(staging_vals)

    @api.model
    def _cron_sirene_sync(self):
        """Cron — synchronises eligible partners with the INSEE SIRENE API.

        Runs every 10 minutes by default (processes the oldest-checked partners first).
        INSEE rate limit: 30 requests/minute (2 requests/partner).
        A 4-second delay is applied between partners to stay within that limit.
        Default batch: 10 partners (~50s per run, well within Odoo's 120s cron limit).
        Increase l10n_fr_sirene_sync.cron_batch_limit cautiously: each extra partner
        adds ~4s to the cron execution time.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        api_key = ICP.get_param("l10n_fr_sirene_sync.api_key", "")
        if not api_key:
            _logger.warning(
                "SIRENE cron: API key not configured — synchronisation cancelled."
            )
            return
        try:
            timeout = int(ICP.get_param("l10n_fr_sirene_sync.api_timeout", "10"))
        except (ValueError, TypeError):
            timeout = 10
        try:
            batch_limit = int(
                ICP.get_param("l10n_fr_sirene_sync.cron_batch_limit", "500")
            )
        except (ValueError, TypeError):
            batch_limit = 10

        partners = self.search(
            [
                ("is_company", "=", True),
                (
                    "vat",
                    "=ilike",
                    "FR___________",
                ),  # 13 chars: FR + 2 (key) + 9 (SIREN)
            ],
            order="sirene_last_check_date asc nulls first",
            limit=batch_limit,
        )
        total = len(partners)
        _logger.info(
            "SIRENE cron: starting — %d eligible partner(s) (French VAT, 13 chars).",
            total,
        )

        for i, partner in enumerate(partners):
            if i > 0:
                time.sleep(4)  # 2 requests/partner × 15 = 30 requests/min
            try:
                with self.env.cr.savepoint():
                    partner._cron_sync_one(api_key, timeout)
            except Exception as exc:
                _logger.error(
                    "SIRENE cron: error for %s (VAT: %s) — %s",
                    partner.name,
                    partner.vat,
                    exc,
                )

        _logger.info("SIRENE cron: done — %d partner(s) processed.", total)

from odoo import models, fields, api, _

# Explicit whitelist of partner fields that this wizard is allowed to update.
_ALLOWED_WRITE_FIELDS = frozenset(
    {"name", "siren", "siret", "street", "street2", "zip", "city", "country_id", "industry_id"}
)

_CHAR_FIELD_MAPPING = [
    ("siren", "sirene_siren", "SIREN"),
    ("siret", "sirene_siret_siege", "SIRET (Head Office)"),
]


class SireneValidationWizard(models.TransientModel):
    _name = "sirene.validation.wizard"
    _description = "SIRENE Data Validation Wizard"

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    line_ids = fields.One2many(
        "sirene.validation.wizard.line",
        "wizard_id",
        string="Fields to Update",
    )

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wizard in wizards:
            partner = wizard.partner_id
            lines = []

            # Legal name vs Odoo partner name
            denomination = partner.sirene_denomination or ""
            if denomination.strip() and denomination.strip() != (partner.name or "").strip():
                lines.append(
                    {
                        "wizard_id": wizard.id,
                        "field_name": "name",
                        "field_label": "Legal Name",
                        "current_value": partner.name or "",
                        "proposed_value": denomination,
                        "apply": True,
                    }
                )

            # Char fields
            for odoo_field, sirene_field, label in _CHAR_FIELD_MAPPING:
                sirene_val = getattr(partner, sirene_field) or ""
                odoo_val = getattr(partner, odoo_field) or ""
                if sirene_val.strip() != odoo_val.strip():
                    lines.append(
                        {
                            "wizard_id": wizard.id,
                            "field_name": odoo_field,
                            "field_label": label,
                            "current_value": odoo_val,
                            "proposed_value": sirene_val,
                            "apply": True,
                        }
                    )

            # Address fields from head office establishment
            siege = partner.sirene_etablissement_ids.filtered(lambda e: e.is_siege)
            if siege:
                siege = siege[0]
                for odoo_field, label in [
                    ("street", "Street"),
                    ("street2", "Street 2"),
                    ("zip", "Postal Code"),
                    ("city", "City"),
                ]:
                    sirene_val = getattr(siege, odoo_field) or ""
                    odoo_val = getattr(partner, odoo_field) or ""
                    if sirene_val.strip() != odoo_val.strip():
                        lines.append(
                            {
                                "wizard_id": wizard.id,
                                "field_name": odoo_field,
                                "field_label": label,
                                "current_value": odoo_val,
                                "proposed_value": sirene_val,
                                "apply": True,
                            }
                        )

            # Country: SIRENE is always France
            france = wizard.env["res.country"].search([("code", "=", "FR")], limit=1)
            if france and partner.country_id != france:
                lines.append(
                    {
                        "wizard_id": wizard.id,
                        "field_name": "country_id",
                        "field_label": "Country",
                        "current_value": partner.country_id.name or "",
                        "proposed_value": france.name,
                        "proposed_many2one_id": france.id,
                        "apply": True,
                    }
                )

            # Industry: derived from sirene_industry_id staging field
            industry = partner.sirene_industry_id
            if industry and partner.industry_id != industry:
                lines.append(
                    {
                        "wizard_id": wizard.id,
                        "field_name": "industry_id",
                        "field_label": "Industry",
                        "current_value": partner.industry_id.name or "",
                        "proposed_value": industry.name,
                        "proposed_many2one_id": industry.id,
                        "apply": True,
                    }
                )

            if lines:
                self.env["sirene.validation.wizard.line"].create(lines)
        return wizards

    def action_apply(self):
        self.ensure_one()
        partner = self.partner_id
        update_vals = {}
        skipped = []

        for line in self.line_ids:
            if line.field_name not in _ALLOWED_WRITE_FIELDS:
                continue
            if line.apply:
                if line.proposed_many2one_id:
                    update_vals[line.field_name] = line.proposed_many2one_id
                else:
                    update_vals[line.field_name] = line.proposed_value
            else:
                skipped.append(f"• {line.field_label} (kept)")

        update_vals["sirene_sync_state"] = "ok"
        partner.write(update_vals)

        if skipped:
            body = _("<b>Retained fields:</b>") + "<br/>" + "<br/>".join(skipped)
            partner.message_post(body=body)
        return {"type": "ir.actions.act_window_close"}

    def action_ignore(self):
        self.ensure_one()
        self.partner_id.write({"sirene_sync_state": "ignored"})
        self.partner_id.message_post(
            body=_("SIRENE update: changes ignored by the user.")
        )
        return {"type": "ir.actions.act_window_close"}


class SireneValidationWizardLine(models.TransientModel):
    _name = "sirene.validation.wizard.line"
    _description = "SIRENE Validation Line"

    wizard_id = fields.Many2one(
        "sirene.validation.wizard",
        required=True,
        ondelete="cascade",
    )
    field_name = fields.Char(readonly=True)
    field_label = fields.Char(string="Field", readonly=True)
    current_value = fields.Char(string="Current Value (Odoo)", readonly=True)
    proposed_value = fields.Char(string="INSEE SIRENE Value", readonly=True)
    proposed_many2one_id = fields.Integer(readonly=True)
    apply = fields.Boolean(string="Apply", default=True)

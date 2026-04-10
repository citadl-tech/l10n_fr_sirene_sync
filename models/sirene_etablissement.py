from odoo import models, fields


class SireneEtablissement(models.Model):
    _name = "sirene.etablissement"
    _description = "SIRENE Establishment"
    _order = "is_siege desc, siret"

    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="cascade",
        index=True,
    )
    siret = fields.Char(string="SIRET")
    is_siege = fields.Boolean(string="Head Office")
    etat = fields.Selection(
        selection=[("A", "Active"), ("F", "Closed")],
        string="Status",
    )
    naf = fields.Char(string="NAF Code")
    naf_activity = fields.Char(string="Activity")
    street = fields.Char(string="Street")
    street2 = fields.Char(string="Street 2")
    zip = fields.Char(string="Postal Code")
    city = fields.Char(string="City")
    date_creation = fields.Date(string="Creation Date")
    date_fermeture = fields.Date(string="Closure Date")

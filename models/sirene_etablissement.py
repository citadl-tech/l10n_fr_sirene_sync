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
    siret = fields.Char(string="SIRET", readonly=True)
    is_siege = fields.Boolean(string="Head Office", readonly=True)
    etat = fields.Selection(
        selection=[("A", "Active"), ("F", "Closed")],
        string="Status",
        readonly=True,
    )
    naf = fields.Char(string="NAF Code", readonly=True)
    naf_activity = fields.Char(string="Activity", readonly=True)
    street = fields.Char(string="Street", readonly=True)
    street2 = fields.Char(string="Street 2", readonly=True)
    zip = fields.Char(string="Postal Code", readonly=True)
    city = fields.Char(string="City", readonly=True)
    date_creation = fields.Date(string="Creation Date", readonly=True)
    date_fermeture = fields.Date(string="Closure Date", readonly=True)

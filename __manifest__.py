# Copyright 2026 CITADL <https://www.citadl.fr>
# @author: Antoine Courouble <antoine@citadl.fr>
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    "name": "SIRENE Synchronisation (INSEE)",
    "summary": "Verify and update partner data via the INSEE SIRENE API",
    "version": "18.0.1.0.0",
    "development_status": "Beta",
    "category": "Localization/France",
    "website": "https://www.citadl.fr/",
    "author": "CITADL",
    "maintainers": ["acourouble"],
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "external_dependencies": {
        "python": ["requests"],
    },
    "depends": [
        "base",
        "contacts",
        "l10n_fr",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/res_partner_views.xml",
        "views/sirene_wizard_views.xml",
    ],
}

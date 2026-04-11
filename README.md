# l10n_fr_sirene_sync

![Odoo 18.0](https://img.shields.io/badge/Odoo-18.0-875A7B?style=flat&logo=odoo) ![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)

Odoo 18 Community module — synchronises partner data with the French INSEE SIRENE official business registry via its public API.

## Features

- Extracts the SIREN from the partner's French VAT number and queries the INSEE SIRENE API.
- Retrieves legal unit data (`/siren/{siren}`) and all establishments (`/siret?q=siren:{siren}`).
- Stores API data in dedicated staging fields without touching operational fields until validated.
- Compares field by field: denomination (with sigle prefix), SIREN, SIRET, street, street2, zip, city, country, industry. Comparisons are **case-sensitive**.
- Denomination is prefixed with the official sigle when present (e.g. `SNCF - SOCIÉTÉ NATIONALE DES CHEMINS DE FER FRANÇAIS`).
- Address is split across two fields: `street` (number + index + type + name) and `street2` (complement).
- Validation wizard: review differences field by field, accept or reject each one individually.
- Chatter trace for validation and refusal actions.
- Sync state badge on the partner form (green / orange / red / grey) with time elapsed since last check.
- Orange banner when updates are pending, red banner on API error.
- Red banner when the company is **ceased (radiée)** according to SIRENE, with cessation date.
- Orange banner when the legal name contains "LIQUIDATION".
- Background cron job for automatic batch synchronisation (max once every 24 hours per partner).
- Full list of establishments stored per partner (siret, state, NAF, address, dates).
- **Import wizard**: create a new partner directly from a SIREN, SIRET or French VAT number — previews SIRENE data before import.
- **NAF → Industry mapping**: automatically maps the NAF code to the matching Odoo industry via NACE section.
- **Legal form**: fetches `categorieJuridiqueUniteLegale` and resolves it to a French label (~150 codes covered).
- **Workforce**: fetches `trancheEffectifsUniteLegale` and resolves it to a workforce range label, with reference year.
- **Company category**: fetches `categorieEntreprise` (PME / ETI / GE).
- **Administrative status**: Active or Ceased, with cessation date when applicable.

## Requirements

- Odoo 18.0 Community
- Module dependencies: `base`, `contacts`, `l10n_fr`
- An INSEE API key (generated on [portail-api.insee.fr](https://portail-api.insee.fr))

## Installation

1. Copy the `l10n_fr_sirene_sync` folder into your Odoo addons path.
2. Update the app list and install the module.
3. Go to **Settings → Technical → Parameters → System Parameters** and set:
   - `l10n_fr_sirene_sync.api_key` — your INSEE API key
   - `l10n_fr_sirene_sync.api_timeout` — request timeout in seconds (default: `10`)
   - `l10n_fr_sirene_sync.cron_batch_limit` — max partners per cron run (default: `10`)

## Usage

### Manual check

1. Open a company partner with a valid French VAT number (`FR` + 2 chars + 9 digits).
2. Go to the **INSEE / SIRENE** tab and click **Check via SIRENE**.
3. If differences are detected, a wizard opens listing each differing field with the current Odoo value and the SIRENE value. Check the fields you want to apply and click **Apply selections**.
4. If no differences are found, a success notification is displayed and the state is set to **Up to date**.

### Import from SIRENE

1. Go to **Contacts → Import from SIRENE**.
2. Enter a SIREN (9 digits), SIRET (14 digits) or French VAT number.
3. Review the preview and click **Import** to create the partner, or **Go to existing** if one already exists.

### Automatic synchronisation

Enable the cron job **SIRENE — Synchronisation quotidienne des partenaires** in Settings → Technical → Scheduled Actions. Partners are checked at most once every 24 hours. Partners with differences are set to **Pending** state for manual validation.

## Data model

### Editable fields added to `res.partner`

| Field | Type | Description |
|---|---|---|
| `siren` | `Char(9)` | SIREN — manually entered or updated via wizard |
| `siret` | `Char(14)` | Registered office SIRET — manually entered or updated via wizard |

### Staging / info fields (read-only)

| Field | Description |
|---|---|
| `sirene_denomination` | Denomination returned by the API (with sigle if present) |
| `sirene_siren` | SIREN returned by the API |
| `sirene_siret_siege` | Registered office SIRET returned by the API |
| `sirene_naf` | NAF code (formatted, e.g. `62.02A`) |
| `sirene_naf_activity` | NAF activity label |
| `sirene_date_creation` | Legal creation date |
| `sirene_legal_form_code` | Catégorie juridique code (4 digits, e.g. `5499`) |
| `sirene_legal_form` | Catégorie juridique label (e.g. `Société à responsabilité limitée (SARL)`) |
| `sirene_workforce` | Workforce range label with reference year (e.g. `20 à 49 salariés (2022)`) |
| `sirene_categorie_entreprise` | Company category: `PME`, `ETI` or `GE` |
| `sirene_etat_administratif` | Administrative status: `A` (Active) or `C` (Ceased) |
| `sirene_date_cessation` | Cessation date (when status is `C`) |
| `sirene_industry_id` | Odoo industry mapped from the NAF NACE section |
| `sirene_sync_state` | Sync state: `unchecked`, `ok`, `pending`, `ignored`, `error` |
| `sirene_last_check_date` | Timestamp of the last API call |
| `sirene_last_error` | Error message from the last failed API call |
| `sirene_etablissement_ids` | Related establishments (model `sirene.etablissement`) |

### `sirene.etablissement`

Stores all establishments for a partner: siret, is_siege, etat, naf, naf_activity, street, street2, zip, city, date_creation, date_fermeture.

## Scope

French companies only (`is_company = True`, French VAT required). Address is sourced from the registered office establishment. Individual contacts and data beyond identity and address (directors, financials, share capital) are out of scope — these are not available in the INSEE SIRENE API.

## Known limitations

- **Share capital** is not available in the INSEE SIRENE API (it is a RCS/Infogreffe data point).
- The establishments endpoint is capped at **200 results** per API call (`nombre=200`). Companies with more than 200 establishments will have incomplete data.
- The INSEE SIRENE API enforces a **rate limit of 30 requests/minute**. The cron job uses 2 calls per partner and adds a 4-second delay between partners (~15 partners/minute). Adjust `cron_batch_limit` accordingly.
- Only French companies with a valid VAT number (`FR` + 11 chars) are eligible for synchronisation.
- The denomination field uses the first active period (`periodesUniteLegale[0]`). Historical or future periods are not considered.
- Legal form disambiguation (e.g. EURL vs SARL when both use code `5499`) is not possible from SIRENE data alone.

## Contributing

Contributions are welcome! Please follow these conventions:

- Branch naming: `18.0-feature-my-feature` or `18.0-fix-my-fix`
- One feature or fix per pull request
- Commit messages in English, prefixed with `FIX`, `ADD`, `IMP`, `REF`, or `REM`
- Test your changes against Odoo 18.0 Community before submitting
- Open an issue first for significant changes to discuss the approach

## License

LGPL-3

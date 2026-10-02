# Constants disclosure — generated

Regenerate: `uv run python scripts/gen_constants_disclosure.py`.
Do not edit by hand — the checked-in copy is pinned by test.

| Constant | Value / procedure | Class | Status | Scoring | Decision-load | Grounding | Call sites | Last-changed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `PRIOR_CONFIDENCE_MAX` | `0.3` | C1 | IN-RANGE | yes | yes | IN-RANGE — nap-table-d1-transcription.md (Table D-1, row prior=0.30 (one-tailed p=0.05 → posterior 0.624)) | src/ml_anamnesis_mcp/enforcement/checks.py:24, src/ml_arete_mcp/tools/decisions.py:420, src/ml_zetesis_mcp/tools/promotion.py:1646 | 6069a77, 6c7dc6e |
| `VERDICT_POSTERIOR` | `verdict_posterior(…)` | C1 | DERIVED | yes | no | DERIVED — nap-table-d1-transcription.md (NAP Table D-1 (the prior+p → posterior mapping); 08 §2.2 (the ladder the BF reads onto)) | — | — |
| `CAMPAIGN_SCORE_ZERO_DIVISOR` | `refuse_zero_divisor(…)` | C2 | THEOREM | yes | no | THEOREM — gleser-hwang-transcription.md (GH transcription (theorem statement); 02/14 (unboundedness)) | — | — |
| `SEED_COUNT_DEFAULT` | `required_n(…)` | C2 | DERIVED | yes | no | DERIVED — 05-gelman-carlin2014-type-s-type-m-errors.pdf (05 (Type S/M), 06 (rethinking), 07 (pilot bias), 11 (prereg power)) | — | — |
| `PROMOTION_THRESHOLD` | `min_evidence_rung(…)` | C2 | DERIVED | yes | no | DERIVED — 08-kass-raftery1995-bayes-factors.pdf (08 §2.2 Table — 2 ln BF evidence ladder) | — | — |
| `STALE_INVESTIGATION_SECONDS` | `3600` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_zetesis_mcp/integrity/checks.py:20 | c08dfc9 |
| `STALE_CAMPAIGN_SECONDS` | `3600` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_zetesis_mcp/integrity/checks.py:21 | c08dfc9 |
| `STALE_TOURNAMENT_SECONDS` | `3600` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_arete_mcp/integrity/checks.py:19 | 6c7dc6e |
| `STALE_PROGRAMME_HOURS` | `24.0` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_episteme_mcp/resources/session.py:362, src/ml_episteme_mcp/resources/status.py:144, src/ml_episteme_mcp/resources/status.py:436, src/ml_episteme_mcp/resources/status.py:485, src/ml_episteme_mcp/server.py:151 | 6c7dc6e |
| `ARCHIVE_SEAL_WARN_HOURS` | `72.0` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_episteme_mcp/resources/status.py:437, src/ml_episteme_mcp/resources/status.py:486, src/ml_episteme_mcp/server.py:165 | 6c7dc6e |
| `STATUS_FRESHNESS_SECONDS` | `600` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_anamnesis_mcp/server.py:127, src/ml_arete_mcp/config.py:30, src/ml_arete_mcp/server.py:176, src/ml_episteme_mcp/config.py:43, src/ml_episteme_mcp/server.py:220, src/ml_zetesis_mcp/config.py:29, src/ml_zetesis_mcp/server.py:161 | 6c7dc6e, 912ff16, c08dfc9 |
| `IMPROVEMENT_EPOCH_SECONDS` | `86400` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_arete_mcp/config.py:31, src/ml_arete_mcp/enforcement/recurrence.py:246, src/ml_arete_mcp/enforcement/recurrence.py:251, src/ml_arete_mcp/server.py:179 | 6c7dc6e, 912ff16 |
| `OBSERVATION_GRACE_SECONDS` | `86400` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_episteme_mcp/integrity/checks.py:31 | 6c7dc6e |
| `CHECK_INTERVAL_SECONDS` | `300` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_agora_mcp/__main__.py:195, src/ml_agora_mcp/integrity/checks.py:26, src/ml_anamnesis_mcp/__main__.py:140, src/ml_anamnesis_mcp/integrity/checks.py:25, src/ml_arete_mcp/__main__.py:201, src/ml_arete_mcp/integrity/checks.py:561, src/ml_episteme_mcp/__main__.py:376, src/ml_episteme_mcp/integrity/checks.py:33, src/ml_zetesis_mcp/__main__.py:198, src/ml_zetesis_mcp/integrity/checks.py:765 | 6069a77, 6c7dc6e, 912ff16, c08dfc9 |
| `STALLED_MARGIN_SECONDS` | `30` | C3 | OPERATIONAL | no | no | OPERATIONAL | src/ml_episteme_mcp/integrity/checks.py:27 | 6c7dc6e |
| `TERMINAL_RESIDUE_MARGIN_SECONDS` | `1.0` | C4 | OPERATIONAL | no | no | OPERATIONAL | src/ml_episteme_mcp/integrity/checks.py:139 | 6c7dc6e |
| `LOG_MAX_FILES` | `100` | C4 | OPERATIONAL | no | no | OPERATIONAL | src/ml_agora_mcp/integrity/checks.py:27, src/ml_anamnesis_mcp/integrity/checks.py:24, src/ml_arete_mcp/integrity/checks.py:20, src/ml_episteme_mcp/integrity/checks.py:32, src/ml_zetesis_mcp/integrity/checks.py:22 | 6069a77, 6c7dc6e, c08dfc9 |

## Sources

| Key | Citation | Corpus file | sha256 |
| --- | --- | --- | --- |
| `02` | Franz, V. H. (2007). Ratios: A short guide to confidence limits and proper use. Technical report, Justus-Liebig-Universität Giessen. arXiv:0710.2024 | 02-ratios-fieller-guide-arxiv0710.2024.pdf | `1a6c716d499e` |
| `04` | Morey, R. D., Hoekstra, R., Rouder, J. N., Lee, M. D., & Wagenmakers, E.-J. (2016). The fallacy of placing confidence in confidence intervals. Psychonomic Bulletin & Review 23(1):103-123 (author preprint) | 04-morey-et-al-2016-author-preprint.pdf | `b5cba2097885` |
| `05` | Gelman, A., & Carlin, J. B. (2014). Beyond power calculations: assessing Type S (sign) and Type M (magnitude) errors. Perspectives on Psychological Science 9(6):641-651. doi:10.1177/1745691614551642 | 05-gelman-carlin2014-type-s-type-m-errors.pdf | `068b792d2b98` |
| `06` | Lakens, D., Mesquida, C., Xavier-Quintais, G., Rasti, S., Toffalini, E., & Altoè, G. (2026). Rethinking Type S and M errors. OSF preprint 2phzb | 06-lakens-et-al-2026-rethinking-type-s-m.pdf | `4c96fe721425` |
| `07` | Albers, C. J., & Lakens, D. (2018). When power analyses based on pilot data are biased: inaccurate effect size estimators and follow-up bias. Journal of Experimental Social Psychology (version of record, author's copy) | 07-albers2018-power-from-pilot-data-biased.pdf | `8a68117ee316` |
| `08` | Kass, R. E., & Raftery, A. E. (1995). Bayes factors. Journal of the American Statistical Association 90(430):773-795 | 08-kass-raftery1995-bayes-factors.pdf | `3da446b1aea8` |
| `09` | Gneiting, T., & Raftery, A. E. (2007). Strictly proper scoring rules, prediction, and estimation. Journal of the American Statistical Association 102(477):359-378. doi:10.1198/016214506000001437 | 09-gneiting-raftery2007-strictly-proper-scoring.pdf | `d31a0c5f0ae8` |
| `11` | Bakker, M., Veldkamp, C. L. S., van den Akker, O. R., van Assen, M. A. L. M., Crompvoets, E., Ong, H. H., & Wicherts, J. M. (2020). PLOS ONE 15(7):e0236079. doi:10.1371/journal.pone.0236079 | 11-bakker2020-prereg-power-analyses.pdf | `e7a64fb6c157` |
| `12` | Lakatos, I. (1970). Falsification and the methodology of scientific research programmes. In Schick (ed.), Readings in the Philosophy of Science Vol. 1 (reprint; pagination differs from Lakatos & Musgrave) | 12-lakatos1970-falsification-msrp.pdf | `11045de669ce` |
| `13` | Lupidi, A., et al. (2026). AIRS-Bench: a suite of tasks for frontier AI research science agents. arXiv:2602.06855 | 13-airsbench-arxiv2602.06855.pdf | `edceffbd86ca` |
| `14` | von Luxburg, U., & Franz, V. H. (2004). Confidence sets for ratios: a purely geometric approach to Fieller's theorem. Max-Planck-Institut für biologische Kybernetik, Technical Report TR-133 | 14-vonluxburg-franz2004-fieller-geometric.pdf | `0dc610b3ab9a` |
| `NAP` | National Academies of Sciences, Engineering, and Medicine (2019). Reproducibility and Replicability in Science. Washington, DC: National Academies Press. Appendix D, Table D-1 (transcription; original read at publisher, not archivable) | nap-table-d1-transcription.md | `290b53262783` |
| `GH` | Gleser, L. J., & Hwang, J. T. (1987). Impossibility result for bounded confidence sets on ratios — audit manifest cites 'The application of Fieller's theorem for confidence intervals for a ratio', Biometrika; canonical record is Annals of Statistics 15(4):1351-1362 (unverified venue — see transcription) | gleser-hwang-transcription.md | `5608269ac8d3` |
| `FI` | Fieller, E. C. (1954). Some problems in interval estimation. Journal of the Royal Statistical Society, Series B 16:174-185 (not archived; content reproduced in sources 02 and 14) | — (not archived) | `—` |
| `AX` | Re-examining confidence intervals for ratios of parameters. Axioms 13(3):37, 2025 (not archived — publisher asset-path collision; read on publisher's HTML page; quoting source for the GH statement) | — (not archived) | `—` |
| `SE` | Lakens, D., Scheel, A. M., & Isager, P. M. (2018). Equivalence testing for psychological research: a tutorial. Advances in Methods and Practices in Psychological Science 1(2):259-269 (not archived — paywalled; read at publisher) | — (not archived) | `—` |
| `AK` | van den Akker, R., et al. (2023). Preregistration in practice. Behavior Research Methods (not archived — paywalled; abstract and results read at publisher) | — (not archived) | `—` |

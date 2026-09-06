# Research and market evidence

Every number used in the pitch, with its source. Verified September 2026.

## The problem, quantified

| Claim | Figure | Source |
|---|---|---|
| Honey adulteration among leading brands | **77%** of samples from 13 top brands adulterated; only 3 of 13 passed NMR (Dabur, Patanjali, Baidyanath, Zandu, Hitkari, Apis Himalaya all failed) | [CSE Food Fraud](https://www.cseindia.org/food-fraud-10493), [Business Today](https://www.businesstoday.in/latest/corporate/story/honeygate-only-saffola-markfedsohna-natures-nectar-among-13-brands-pass-sugar-syrup-test-claims-cse-280275-2020-12-02) |
| Syrups engineered to beat statutory tests | Chinese suppliers advertised C3/C4-evading fructose syrup on Alibaba; shipped to India labelled "paint pigment"; 50–80% adulteration still passes | [Down To Earth](https://www.downtoearth.org.in/health/it-s-not-honey-how-the-magic-syrup-beat-indian-testing-protocols-74496), [ThePrint](https://theprint.in/india/chinese-sugar-found-in-indian-honey-dabur-patanjali-call-cse-report-bid-to-malign-brands/556162/) |
| Regulator cannot scale lab testing | FSSAI declined mandating NMR: "high cost, low utility", no Indian reference database, high skill and capex | [Business Today](https://businesstoday.in/current/corporate/fssai-clarifies-on-cse-claims-regarding-adulteration-in-honey-samples/story/423867.html) |
| Lab test cost per sample | ₹750–3,200 depending on lab and panel | Auriga Research, FARE Labs, FICCI RAC listings |
| Beekeeper price realisation | Farmers capture ~25% of retail; direct buy-back lifts income ~40% | [Under The Mango Tree](https://www.utmt.in/pages/our-story), [Acumen](https://acumen.org/companies/under-the-mango-tree/) |

**Why the C4 test cannot catch rice syrup:** rice syrup is a C3 sugar. The
mandated C4 (EA/LC-IRMS) test detects C4 sugars — cane and corn. It is
structurally blind to C3 adulterants. FSSAI added an SMR test for rice syrup
specifically, but the syrup market moves faster than the test list.

## KVIC and the Honey Mission

| Metric | Figure |
|---|---|
| Bee boxes / colonies distributed 2017-18 to 2025-26 | **2,46,099** |
| Cumulative production impact | +24,269 MT |
| Beekeeper income generated | ~₹325 crore |
| Exports via KVIC-linked beekeepers, 2025-26 | ~₹31 crore |
| Estimated production 2025-26 | 5,512 MT |

Sources: [Drishti IAS](https://www.drishtiias.com/daily-updates/daily-news-analysis/kvics-honey-mission),
[Deccan Herald](https://www.deccanherald.com/india/maharashtra/beekeepers-earn-rs-325-crore-by-generating-over-20000-metric-tonnes-of-honey-under-kvics-honey-mission-3551963),
[Mokokchung Times](https://mokokchungtimes.com/kvic-linked-beekeepers-exported-honey-worth-rs-31-crore-in-2025-26/)

## Market

| Metric | Figure | Source |
|---|---|---|
| India honey market | ₹28.89 B (2025) to ₹51.9 B (2034), ~6.7% CAGR | [IMARC](https://www.imarcgroup.com/indian-honey-market) |
| Indian production | ~1.4 lakh MT (2024) | PIB / NBHM |
| Exports | ~1.07 lakh MT, USD 177.55 M (FY24); **world #2**, up from rank 9 in 2020 | NBHM / APEDA |
| Global smart beehive market | ~14.1% CAGR 2025–2031 | [Lucintel](https://www.lucintel.com/smart-beehive-market.aspx) |
| NBHM outlay | ₹500 crore (FY21–FY26, extended) | [PIB](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2185400) |

## The EU compliance deadline

Directive **(EU) 2024/1438** amends the Honey Directive. In force 13 June 2024,
**applying from 14 June 2026**:

- Country or countries of origin must appear on the label **in descending order,
  with the percentage of each**, tolerance 5% per part.
- "Blend of honey from EU / non-EU countries" is **no longer permitted**.
- Harmonised hive-to-jar traceability; each batch traceable to its first point
  of entry into the EU market.
- Regulation (EU) 2023/2652 requires importing establishments to register in
  TRACES (effective 29 November 2024).

Sources: [EC Agriculture](https://agriculture.ec.europa.eu/farming/animal-products/honey_en),
[EC Honey Platform](https://agriculture.ec.europa.eu/media/news/clearer-rules-origin-and-composition-honey-commission-sets-honey-platform-2024-06-13_en),
[CBI](https://www.cbi.eu/news/stricter-traceability-requirements-are-taking-over-european-honey-market),
[Food Safety Magazine](https://www.food-safety.com/articles/9534-eu-to-develop-new-traceability-requirements-to-tackle-honey-adulteration-revises-origin-labeling-rules)

**This is live now.** India is the second largest exporter and most exporters
cannot produce that declaration. Our `batch_composition` table generates it as a
by-product of the custody graph.

## Competitors

### Smart hive hardware and AI

BeeHero (IL), ApisProtect (IE, the most-funded bee tech company), Pollenity (BG),
Arnia (IT, acoustic swarm analysis, 30 countries), Beewise (autonomous robotic
BeeHome), HyperHyve.

**Gap:** priced for US/EU commercial pollination fleets. A ₹40,000 hive unit is
absurd against an Indian box costing ~₹4,000. None do provenance or consumer
verification. ([Sifted](https://sifted.eu/articles/bee-tech-startups-smart-hives-big-data-robot-beehero-apisprotect-pollenity))

### Agri traceability platforms

[TraceX](https://tracextech.com/hive-to-honey-traceability/) (India, has a
hive-to-honey module), [Farmonaut](https://farmonaut.com/traceability/revolutionizing_honey_production)
(India, QR plus auto EU/FSSAI/EUDR reports), [SourceTrace](https://sourcetrace.com/farm-traceability/)
(India, 26 countries), FoodTraze, [Fujairah Honey Chain](https://www.mdpi.com/2078-2489/16/8/626) (UAE, academic).

**Gap:** generic B2B supply-chain SaaS. Traceability is self-declared data entry
— the oracle problem, unaddressed. No hive-side sensing, no AI agronomy, no
fraud bounding.

### Government — the one to handle carefully

**Madhukranti portal** (National Bee Board + Indian Bank, launched 2021),
explicitly described as a blockchain system for traceability of honey source.

| Metric | Figure |
|---|---|
| Beekeepers registered (Oct 2025) | 14,859 |
| Societies / firms / companies | 269 / 150 / 206 |
| Honeybee colonies registered | 20 lakh+ |

**Gap:** registration-first. 14,859 registered beekeepers against 20 lakh
colonies is thin adoption, and registration is not chain of custody. No hive
telemetry, no jar-level consumer QR, no AI.

**Our stance: integrate, do not compete.** See [07-deployment.md](07-deployment.md).

Sources: [Drishti IAS](https://www.drishtiias.com/daily-updates/daily-news-analysis/madhu-kranti-portal-honey-corners),
[AgroSpectrum](https://agrospectrumindia.com/2021/04/07/ag-ministry-launches-madhukranti-portal-for-traceability-of-honey.html),
[Global Agriculture](https://www.global-agriculture.com/india-region/over-20-lakh-honeybee-colonies-have-registered-with-national-bee-board-on-madhukranti-portal/)

### Other SIH teams on SIH26021

Expect the overwhelming majority to build: form → Postgres → hash → Ethereum
testnet → QR page, with a Kaggle bee-image CNN bolted on.

Our differentiation, in the order a judge will care:

1. Yield-bound issuance / mass balance — the oracle problem, actually addressed
2. Clone-resistant serialised seals with scan-velocity fraud detection
3. Sentinel-hive economics that make national scale affordable
4. EU 2024/1438 export-dossier generation
5. Offline-first, voice-first vernacular beekeeper app

## Technical literature

| Topic | Reference |
|---|---|
| Smart beehive systematic review | [MDPI Sensors 25(17):5359](https://www.mdpi.com/1424-8220/25/17/5359) |
| CNNs on constrained devices for hive acoustics | [PMC11479142](https://pmc.ncbi.nlm.nih.gov/articles/PMC11479142/) |
| TinyML in beekeeping survey | [arXiv 2509.08822](https://arxiv.org/pdf/2509.08822) |
| Queen-absence detection from short audio | [Insects 17(6):547](https://doi.org/10.3390/insects17060547) |
| Varroa counting via deep learning | [PMC11207890](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11207890/) |
| UrBAN dataset | [arXiv 2406.03657](https://arxiv.org/pdf/2406.03657) |
| BeeTogether merged audio datasets | [Sensors 24(18):6067](https://doi.org/10.3390/s24186067) |
| Vis-NIR + ML adulteration detection | [Foods 12(13):2491](https://doi.org/10.3390/foods12132491) |
| Gas-sensor (BME688) adulteration detection | [PMC12078584](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12078584/) |
| WaggleNet LoRa + MQTT hive monitoring | [arXiv 2512.07408](https://arxiv.org/html/2512.07408) |
| Blockchain adoption in beekeeping | [Frontiers Sust. Food Systems](https://www.frontiersin.org/journals/sustainable-food-systems/articles/10.3389/fsufs.2025.1566341/full) |

Reported accuracies in that literature (CNN+MFCC ~99% swarm, LSTM ~92%
queenless, ResNet-50 up to 99%) are **context, not our results**. See
[06-ml.md](06-ml.md).

## Policy and identity

- [AgriStack Farmer Registry FAQs](https://agristack.gov.in/assets/registries/farmerRegistry/farmer_registry_faqs.pdf)
- [NBHM PIB release](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2185400)
- [SIH26021 problem statement](https://zaidsayyed.in/tools/sih-problem-statements/sih26021)
